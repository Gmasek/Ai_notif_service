from haystack_integrations.components.generators.anthropic import AnthropicChatGenerator
from haystack.dataclasses import ChatMessage
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic as _anthropic
import json
import os
import random
import logging
import time

load_dotenv()

LLM_GENERATION_THREADS = int(os.environ.get("LLM_GENERATION_THREADS", "4"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "3"))
LLM_RETRY_BASE_DELAY = float(os.environ.get("LLM_RETRY_BASE_DELAY", "5.0"))
PAST_NOTIF_CONTEXT_N = int(os.environ.get("PAST_NOTIF_CONTEXT_N", "3"))
VALIDATOR_MODEL = os.environ.get("VALIDATOR_MODEL", "claude-haiku-4-5")
VALIDATOR_MAX_RETRIES = int(os.environ.get("VALIDATOR_MAX_RETRIES", "2"))

# Generation pipelines (stored as ints on notification_logs / generated_notifications).
PIPELINE_BASIC_CONTEXT = 0
PIPELINE_RAG = 1
PIPELINE_AGENTIC = 2
ALL_PIPELINES = (PIPELINE_BASIC_CONTEXT, PIPELINE_RAG, PIPELINE_AGENTIC)
PIPELINE_NAMES = {
    PIPELINE_BASIC_CONTEXT: "basic_context",
    PIPELINE_RAG: "rag",
    PIPELINE_AGENTIC: "agentic",
}

# Feedback grading config. Feedback is graded 1=good / 2=neutral / 3=bad (NULL=no feedback).
# The exact eval-survey rating field is still being confirmed — keep it env-configurable.
FEEDBACK_RATING_FIELD = os.environ.get("FEEDBACK_RATING_FIELD", "Q00[SQ004]")
FEEDBACK_GOOD_MIN = float(os.environ.get("FEEDBACK_GOOD_MIN", "4"))  # >= → grade 1 (good)
FEEDBACK_BAD_MAX = float(os.environ.get("FEEDBACK_BAD_MAX", "2"))    # <= → grade 3 (bad)
GRADE_GOOD = 1
GRADE_NEUTRAL = 2
GRADE_BAD = 3

# Cross-user retrieval config.
SIMILAR_PARTICIPANTS_N = int(os.environ.get("SIMILAR_PARTICIPANTS_N", "5"))
RAG_EXAMPLES_PER_BUCKET = int(os.environ.get("RAG_EXAMPLES_PER_BUCKET", "3"))
AGENTIC_MAX_TOOL_ITERS = int(os.environ.get("AGENTIC_MAX_TOOL_ITERS", "4"))
GENERATION_MODEL = os.environ.get("GENERATION_MODEL", "claude-opus-4-7")

# Combined-similarity weights (Big Five distance vs feeling-state distance).
BIG5_WEIGHT = float(os.environ.get("BIG5_WEIGHT", "1.0"))
CONTEXT_WEIGHT = float(os.environ.get("CONTEXT_WEIGHT", "1.0"))

# Feeling-state numeric keys (0–100 scales) used for context-similarity matching.
_CONTEXT_KEYS = (
    "mood_valence",
    "energetic_arousal",
    "locus_of_control",
    "stress",
    "motivation_pa",
    "barrier_pa",
)

# Big Five trait keys as stored on Patient.big5 (emotional_stability is the inverse of neuroticism).
_BIG5_KEYS = (
    "openness",
    "extraversion",
    "agreeableness",
    "conscientiousness",
    "emotional_stability",
)

logger = logging.getLogger(__name__)


anthropic_client = AnthropicChatGenerator(model="claude-opus-4-7")
_anthropic_raw = _anthropic.Anthropic()


# ── Big Five adjective tables (ported from Prompt_Comparison.py) ──────────────
_TRAIT_INFO = {
    "extraversion": {
        "label": "Extraversion",
        "definition": "a tendency to seek stimulation in the company of others",
        "high": "extroverted, excitement seeking, attention seeking, "
        "outgoing, warm, seeking adventure, enthusiastic in groups, "
        "often energized by social situations, happy to be the "
        "center of attention",
        "low": "socially withdrawn, detached coldness, quiet, reserved, "
        "prefers solitary or small-group settings, fatigued by too "
        "much social interaction, reflective",
    },
    "agreeableness": {
        "label": "Agreeableness",
        "definition": "a tendency to be compassionate and cooperative "
        "towards others",
        "high": "submissiveness, selflessness, gullibility, helpful, "
        "trusting/forgiving, empathetic, supportive, team-oriented, "
        "straightforward, altruistic, compliant, modest, sympathetic",
        "low": "deceitfulness, manipulativeness, callousness, critical, "
        "uncooperative, suspicious, competitive, skeptical, "
        "demanding, insulting, stubborn, show-offs, unsympathetic, "
        "less caring",
    },
    "conscientiousness": {
        "label": "Conscientiousness",
        "definition": "a tendency that a person acts in an organized or "
        "spontaneous way",
        "high": "perfectionism, workaholism, hardworking, dependable, "
        "organized, reliable, persistent, good at planning, "
        "competent, dutiful, achievement-striving, "
        "self-disciplined, considerate",
        "low": "distractibility, irresponsibility, rashness, impulsive, "
        "careless, disorganized, easily distracted, less structured, "
        "incompetent, procrastinator, undisciplined",
    },
    "neuroticism": {
        "label": "Neuroticism",
        "definition": "the extent to which a person's emotion is sensitive "
        "to the environment",
        "high": "depressivity, emotional lability, shamefulness, anxious, "
        "unhappy, prone to negative emotions, prone to worry, mood "
        "swings, very stressed, may experience anxiety more "
        "frequently, hostile (irritable), self-conscious (shy), "
        "vulnerable, experiencing dramatic shifts in mood",
        "low": "fearlessness, shamelessness, calm, even-tempered, secure, "
        "generally calm, secure, and resilient when facing "
        "challenges, laid back, emotionally stable, confident, "
        "rarely sad or depressed",
    },
    "openness": {
        "label": "Openness",
        "definition": "the extent to which a person is open to experience a "
        "variety of activities",
        "high": "magical thinking, eccentricity, curious, wide range of "
        "interests, independent, enjoys variety, embraces change, "
        "likely to engage in creative or unconventional pursuits, "
        "imaginative, open to trying new things",
        "low": "inflexible, close-minded, practical, conventional, prefers "
        "routine, prefers routine and tradition, values practicality "
        "over novelty, predictable, not very imaginative, "
        "uncomfortable with change, strict with routine, traditional",
    },
}

ADJECTIVE_MARKERS: dict[str, list[tuple[str, str]]] = {
    "extraversion": [
        ("unfriendly", "friendly"),  # E1 Friendliness
        ("introverted", "extraverted"),  # E2 Gregariousness
        ("silent", "talkative"),  # E2 Gregariousness
        ("timid", "bold"),  # E3 Assertiveness
        ("unassertive", "assertive"),  # E3 Assertiveness
        ("inactive", "active"),  # E4 Activity Level
        ("unenergetic", "energetic"),  # E5 Excitement-Seeking
        ("unadventurous", "adventurous"),  # E5 Excitement-Seeking
        ("gloomy", "cheerful"),  # E6 Cheerfulness
    ],
    "agreeableness": [
        ("distrustful", "trustful"),  # A1 Trust
        ("immoral", "moral"),  # A2 Morality
        ("dishonest", "honest"),  # A2 Morality
        ("unkind", "kind"),  # A3 Altruism
        ("stingy", "generous"),  # A3 Altruism
        ("unaltruistic", "altruistic"),  # A3 Altruism
        ("uncooperative", "cooperative"),  # A4 Cooperation
        ("self-important", "humble"),  # A5 Modesty
        ("unsympathetic", "sympathetic"),  # A6 Sympathy
        ("selfish", "unselfish"),  # AGR domain
        ("disagreeable", "agreeable"),  # AGR domain
    ],
    "conscientiousness": [
        ("unsure", "self-efficacious"),  # C1 Self-Efficacy
        ("messy", "orderly"),  # C2 Orderliness
        ("irresponsible", "responsible"),  # C3 Dutifulness
        ("lazy", "hardworking"),  # C4 Achievement-Striving
        ("undisciplined", "self-disciplined"),  # C5 Self-Discipline
        ("impractical", "practical"),  # C6 Cautiousness
        ("extravagant", "thrifty"),  # C6 Cautiousness
        ("disorganized", "organized"),  # CON domain
        ("negligent", "conscientious"),  # CON domain
        ("careless", "thorough"),  # CON domain
    ],
    "neuroticism": [
        ("relaxed", "tense"),  # N1 Anxiety
        ("at ease", "nervous"),  # N1 Anxiety
        ("easygoing", "anxious"),  # N1 Anxiety
        ("calm", "angry"),  # N2 Anger
        ("patient", "irritable"),  # N2 Anger
        ("happy", "depressed"),  # N3 Depression
        ("unselfconscious", "self-conscious"),  # N4 Self-Consciousness
        ("level-headed", "impulsive"),  # N5 Immoderation
        ("contented", "discontented"),  # N6 Vulnerability
        ("emotionally stable", "emotionally unstable"),  # N6 Vulnerability
    ],
    "openness": [
        ("unimaginative", "imaginative"),  # O1 Imagination
        ("uncreative", "creative"),  # O2 Artistic Interests
        ("artistically unappreciative", "artistically appreciative"),  # O2
        ("unaesthetic", "aesthetic"),  # O2
        ("unreflective", "reflective"),  # O3 Emotionality
        ("emotionally closed", "emotionally aware"),  # O3
        ("uninquisitive", "curious"),  # O4 Adventurousness
        ("predictable", "spontaneous"),  # O4
        ("unintelligent", "intelligent"),  # O5 Intellect
        ("unanalytical", "analytical"),  # O5
        ("unsophisticated", "sophisticated"),  # O5
        ("socially conservative", "socially progressive"),  # O6 Liberalism
    ],
}

LEVEL_PREFIXES = {
    1: "extremely",
    2: "very",
    3: "",
    4: "a bit",
    5: "neither",
    6: "a bit",
    7: "",
    8: "very",
    9: "extremely",
}


def _score_to_level(score: int) -> int:
    if score <= 14:
        return 1
    elif score <= 18:
        return 2
    elif score <= 22:
        return 3
    elif score <= 26:
        return 4
    elif score <= 33:
        return 5
    elif score <= 37:
        return 6
    elif score <= 41:
        return 7
    elif score <= 45:
        return 8
    else:
        return 9


def _level_to_adjectives(level: int, markers: list[tuple[str, str]]) -> list[str]:
    results = []
    for low_adj, high_adj in markers:
        if level == 5:
            results.append(f"neither {low_adj} nor {high_adj}")
        elif level < 5:
            p = LEVEL_PREFIXES[level]
            results.append(f"{p} {low_adj}".strip())
        else:
            p = LEVEL_PREFIXES[level]
            results.append(f"{p} {high_adj}".strip())
    return results


def _scores_to_description(scores: dict[str, int]) -> str:
    all_adj: list[str] = []
    for trait in [
        "extraversion",
        "agreeableness",
        "conscientiousness",
        "neuroticism",
        "openness",
    ]:
        level = _score_to_level(scores[trait])
        all_adj.extend(_level_to_adjectives(level, ADJECTIVE_MARKERS[trait]))
    return ", ".join(all_adj)


# ── Prompt templates ───────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are an intelligent healthcare motivational agent. Your task is to process each "
    "incoming user message (and any provided personality features) and write a motivational "
    "message for physical activity. The user input is based on the user's current contextual "
    "situation. Given the user's input:\n"
    "1. Think about the contextual information of the user.\n"
    "2. If Big Five traits are mentioned in the input, think how you can adapt tone and "
    "phrasing to match those traits; otherwise choose an appropriate tone based on context.\n"
    "Always adapt content to the specific user input. Do not use bullet points or numbered lists."
)

_PERSONALITY_TEMPLATE = (
    'The user you are writing this message for has the following personality profile: "{description}"\n\n'
    "Adapt your communication style — tone, word choice, framing, energy level, structure, and "
    "emotional register — to match the personality profile above. The adjectives describe the FULL "
    "SPECTRUM of the user's personality — use all of them to inform how you communicate, not just "
    "the most salient ones.\n\n"
    "CRITICAL: Do NOT mention the user's personality traits, this description, or that you are "
    "adapting your style in any way."
)

_PERSONALITY_TEMPLATE_2 = (
    "\n\n=== PERSONALITY ADAPTATION (HIGHEST PRIORITY) ===\n\n"
    "You are speaking to a specific user whose personality is described "
    "below in three complementary forms: adjectives, numeric scores, "
    "and a trait reference. Integrate ALL three when shaping how you "
    "communicate.\n\n"
    "{description}"
    f"{json.dumps(_TRAIT_INFO, indent=2)}\n\n"
    "Adapt your communication style — tone, word choice, framing, "
    "energy level, structure, and emotional register — to match this "
    "profile. You absolutely must take into account the COMBINATION of traits, not just "
    "one in isolation: a user high on Conscientiousness AND high on "
    "Neuroticism communicates differently than one high on "
    "Conscientiousness alone.\n\n"
    "This adaptation has SUPERIOR PRIORITY over all other style "
    "guidance in this prompt. When in doubt, favour the personality "
    "profile.\n\n"
    "CRITICAL: Do NOT mention the user's personality, scores, this "
    "description, or that you are adapting. The adaptation must be "
    "invisible."
)

_CONTEXT_TEMPLATE = (
    "Here is the user's current context:\n\n"
    "- Affective Valence (0 = very unwell, 100 = very well): {valence}\n"
    "- Energetic Arousal (0 = no energy, 100 = full of energy): {arousal}\n"
    "- Stress (0 = not stressed, 100 = extremely stressed): {stress}\n"
    "- Locus of Control (0 = no control, 100 = full control): {locus}\n"
    "- Motivation for Physical Activity (0 = none, 100 = very high): {motivation}\n"
    "- Barriers for Physical Activity (0 = many barriers, 100 = no barriers): {barriers}\n"
    '- Planned activity for today: "{plan}"\n'
    '- Open reflection: "{reflection}"\n\n'
    "Generate a single motivational message for this user based on their current situation. "
    "The message should acknowledge their state and encourage them toward their planned activity."
    )

_PAST_NOTIFICATIONS_TEMPLATE = (
    "\n\nPrevious motivational messages sent to this user with their survey feedback "
    "(where feedback is available, use it to understand what they responded well or poorly to "
    "and adapt your approach accordingly; always avoid repeating similar phrasing or themes):\n{entries}"
)

_VALIDATOR_PROMPT = (
    "You are a quality-control assistant for a healthcare motivational app. "
    "A message has been generated to motivate a user toward physical activity. "
    "Assess it and reply with ONLY a JSON object — no other text — with two keys:\n"
    '  "valid": true or false\n'
    '  "reason": one sentence explanation\n\n'
    "Mark it INVALID if it:\n"
    "- Is not a motivational message for physical activity\n"
    "- Contains harmful, inappropriate, or offensive content\n"
    "- Is too short (under 20 words) or absurdly long (over 150 words)\n"
    "- Uses bullet points or numbered lists\n\n"
    "Otherwise mark it VALID."
)


def _validate_notification(text: str) -> tuple[bool, str]:
    try:
        response = _anthropic_raw.messages.create(
            model=VALIDATOR_MODEL,
            max_tokens=128,
            system=_VALIDATOR_PROMPT,
            messages=[{"role": "user", "content": f'Candidate message:\n"{text}"'}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = json.loads(raw)
        return bool(data.get("valid", True)), data.get("reason", "")
    except Exception as e:
        logger.warning("Validator call failed (%s); treating notification as valid", e)
        return True, "validator unavailable"


# ── Feedback grading & example-store retrieval (rag / agentic pipelines) ────────

_FEEDBACK_EXAMPLES_TEMPLATE = (
    "\n\n=== EXAMPLES FROM SIMILAR USERS ===\n\n"
    "Below are past motivational messages sent to users with a similar personality "
    "profile and a similar feeling-state, with how they were received and whether the "
    "user actually exercised afterwards. Use them to guide your writing.\n\n"
    "GOOD — these landed well; emulate their tone, framing, and approach:\n{good}\n\n"
    "BAD — these landed poorly; avoid these patterns, phrasing, and themes:\n{bad}\n\n"
    "Do not copy any example verbatim — write a fresh message for the current user's context."
)


def _classify_feedback(feedback_raw: dict | None) -> int | None:
    """Grade a notification's survey feedback on the 1–3 scale: 1=good, 2=neutral, 3=bad.

    Reads the configurable rating field (FEEDBACK_RATING_FIELD): >= FEEDBACK_GOOD_MIN → 1,
    <= FEEDBACK_BAD_MAX → 3, in-between → 2. Returns None when feedback is missing or the
    field is absent/unparseable, so such rows carry no grade.
    """
    if not isinstance(feedback_raw, dict):
        return None
    value = feedback_raw.get(FEEDBACK_RATING_FIELD)
    if value is None or value == "":
        return None
    try:
        score = float(value)
    except (ValueError, TypeError):
        return None
    if score >= FEEDBACK_GOOD_MIN:
        return GRADE_GOOD
    if score <= FEEDBACK_BAD_MAX:
        return GRADE_BAD
    return GRADE_NEUTRAL


def _feedback_score(feedback_raw: dict | None) -> float | None:
    """Extract the raw numeric rating from feedback (for storage), or None."""
    if not isinstance(feedback_raw, dict):
        return None
    value = feedback_raw.get(FEEDBACK_RATING_FIELD)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _big5_distance(a: dict, b: dict) -> float | None:
    """Euclidean distance between two Big Five profiles. None if either is incomplete."""
    total = 0.0
    for key in _BIG5_KEYS:
        va, vb = a.get(key), b.get(key)
        if va is None or vb is None:
            return None
        total += (float(va) - float(vb)) ** 2
    return total ** 0.5


def _context_distance(a: dict, b: dict) -> float:
    """Euclidean distance over the feeling-state numerics, each normalized to 0–1 (÷100),
    averaged over the keys present in both. Returns 0.0 when nothing overlaps."""
    total = 0.0
    n = 0
    for key in _CONTEXT_KEYS:
        va, vb = a.get(key), b.get(key)
        if va is None or vb is None:
            continue
        total += ((float(va) - float(vb)) / 100.0) ** 2
        n += 1
    if n == 0:
        return 0.0
    return (total / n) ** 0.5


def _combined_distance(big5_a: dict, big5_b: dict, ctx_a: dict, ctx_b: dict) -> float:
    """Weighted Big Five + feeling-state distance used to rank example rows."""
    b5 = _big5_distance(big5_a or {}, big5_b or {})
    b5_term = (b5 / 60.0) if b5 is not None else 0.0
    ctx_term = _context_distance(ctx_a or {}, ctx_b or {})
    return BIG5_WEIGHT * b5_term + CONTEXT_WEIGHT * ctx_term


def _example_to_record(ex) -> dict:
    """Project a NotificationExample row into a lightweight dict for prompts/tools."""
    return {
        "more_participant_id": ex.more_participant_id,
        "notification_text": ex.notification_text,
        "feedback_grade": ex.feedback_grade,
        "executed": ex.executed,
        "feeling": {k: getattr(ex, k) for k in _CONTEXT_KEYS},
        "big5": ex.big5,
    }


def _retrieve_examples(
    session, big5: dict, context: dict, k_per_grade: int = RAG_EXAMPLES_PER_BUCKET,
    exclude_pid: int | None = None,
) -> dict[str, list[dict]]:
    """Single read of the example store: load graded rows, rank by combined Big Five +
    feeling-state distance to the current user, and bucket into good (grade 1) / bad
    (grade 3). Neutral (grade 2) is skipped."""
    from .models import NotificationExample

    rows = (
        session.query(NotificationExample)
        .filter(NotificationExample.feedback_grade != None)  # noqa: E711
        .all()
    )
    scored = []
    for ex in rows:
        if exclude_pid is not None and ex.more_participant_id == exclude_pid:
            continue
        dist = _combined_distance(big5, ex.big5 or {}, context, _example_to_record(ex)["feeling"])
        scored.append((dist, ex))
    scored.sort(key=lambda t: t[0])

    buckets: dict[str, list[dict]] = {"good": [], "bad": []}
    for _, ex in scored:
        label = "good" if ex.feedback_grade == GRADE_GOOD else (
            "bad" if ex.feedback_grade == GRADE_BAD else None
        )
        if label and len(buckets[label]) < k_per_grade:
            buckets[label].append(_example_to_record(ex))
        if len(buckets["good"]) >= k_per_grade and len(buckets["bad"]) >= k_per_grade:
            break
    return buckets


def _find_similar_participants(
    session, big5: dict, context: dict | None, limit: int, exclude_pid: int | None = None
) -> list[dict]:
    """Return the `limit` participants whose Big Five (+ optional feeling-state) profile is
    closest to the current user, as dicts with id + personality + latest feeling snapshot.
    Used by the agentic find_similar_users tool."""
    from .models import Patient

    if not big5:
        return []
    rows = (
        session.query(Patient.more_participant_id, Patient.big5)
        .filter(Patient.big5 != None, Patient.more_participant_id != None)  # noqa: E711
        .all()
    )
    scored = []
    for pid, other_big5 in rows:
        if pid == exclude_pid:
            continue
        dist = _combined_distance(big5, other_big5 or {}, context or {}, {})
        scored.append((dist, pid, other_big5))
    scored.sort(key=lambda t: t[0])
    return [
        {"more_participant_id": pid, "big5": b5}
        for _, pid, b5 in scored[:limit]
    ]


def _get_examples_for_participants(
    session, participant_ids: list[int], grade: int | None = None
) -> list[dict]:
    """Enriched example rows for the given donors, optionally filtered by feedback grade
    (1=good / 3=bad). Used by the agentic get_user_examples tool."""
    from .models import NotificationExample

    if not participant_ids:
        return []
    q = session.query(NotificationExample).filter(
        NotificationExample.more_participant_id.in_(participant_ids),
        NotificationExample.feedback_grade != None,  # noqa: E711
    )
    if grade is not None:
        q = q.filter(NotificationExample.feedback_grade == grade)
    return [_example_to_record(ex) for ex in q.order_by(NotificationExample.notif_date.desc()).all()]


def _format_example_record(rec: dict) -> str:
    f = rec.get("feeling") or {}
    feeling = ", ".join(
        f"{k}={f[k]}" for k in _CONTEXT_KEYS if f.get(k) is not None
    ) or "n/a"
    executed = rec.get("executed")
    exec_str = "exercised" if executed else ("did not exercise" if executed is False else "unknown")
    return f'- Felt: {feeling}; outcome: {exec_str}\n  Message: "{rec.get("notification_text", "")}"'


def _format_examples(buckets: dict[str, list[dict]]) -> str:
    """Render good/bad enriched example records into the examples prompt block.
    Returns '' when there is no material to inject."""
    good, bad = buckets.get("good") or [], buckets.get("bad") or []
    if not good and not bad:
        return ""
    good_str = "\n".join(_format_example_record(r) for r in good) if good else "- (none available)"
    bad_str = "\n".join(_format_example_record(r) for r in bad) if bad else "- (none available)"
    return _FEEDBACK_EXAMPLES_TEMPLATE.format(good=good_str, bad=bad_str)


def build_contextual_information(context_data: dict) -> str:
    """Build contextual information string from context data, skipping unavailable fields."""
    missing = [
        k
        for k in (
            "day",
            "time",
            "user status",
            "weather",
            "activity",
            "location",
            "position of body",
            "last interaction",
            "mood_valence",
            "energetic_arousal",
            "affect_calmness",
            "stress",
            "locus_of_control",
            "close_locations",
            "calendar entries",
            "motivation_pa",
            "barrier_pa",
            "plans_pa_today",
            "pa_scheduled_today",
            "pa_change_reason",
            "pa_performed_today",
            "next_day_text",
            "pa_scheduled_tomorrow",
        )
        if not context_data.get(k)
    ]
    if missing:
        logger.warning("build_contextual_information: missing/empty keys: %s", missing)
    logger.info(
        "build_contextual_information raw keys present: %s", list(context_data.keys())
    )

    parts = []

    day = context_data.get("day")
    time = context_data.get("time")
    if day or time:
        parts.append(f"Today is {day} at {time}.")

    mood = context_data.get("mood_valence")
    energy = context_data.get("energetic_arousal")
    calm = context_data.get("affect_calmness")
    stress = context_data.get("stress")
    locus = context_data.get("locus_of_control")
    mood_parts = []
    if mood is not None:
        mood_parts.append(f"{mood}% well")
    if energy is not None:
        mood_parts.append(f"{energy}% energetic")
    if calm is not None:
        mood_parts.append(f"{calm}% calm")
    if stress is not None:
        mood_parts.append(f"{stress}% stressed")
    if locus is not None:
        mood_parts.append(f"{locus}% in control")
    if mood_parts:
        parts.append(f"Feeling: {', '.join(mood_parts)}.")

    motivation = context_data.get("motivation_pa")
    barrier = context_data.get("barrier_pa")
    if motivation is not None:
        parts.append(f"Motivation to exercise: {motivation}%.")
    if barrier is not None:
        parts.append(f"Circumstances favouring exercise: {barrier}%.")

    plans_pa = context_data.get("plans_pa_today")
    pa_today = context_data.get("pa_scheduled_today")
    pa_change_reason = context_data.get("pa_change_reason")
    if plans_pa == "Y" and pa_today:
        parts.append(f"Planned physical activity today: {pa_today}.")
    elif plans_pa == "N":
        if pa_change_reason:
            parts.append(
                f"No physical activity planned today. What changed: {pa_change_reason}."
            )
        else:
            parts.append("No physical activity planned today.")
    elif pa_today:
        # Fallback for legacy responses without G01Q04
        parts.append(f"Planned physical activity today: {pa_today}.")

    pa_performed = context_data.get("pa_performed_today")
    if pa_performed:
        parts.append(f"Physical activity completed today: {pa_performed}.")

    events = context_data.get("events_today")
    if events:
        parts.append(f"Events today: {events}.")

    return " ".join(parts)


def build_big5_information(big5: dict) -> str:
    """Build Big Five personality traits information string from patient's big5 dict."""
    if not big5:
        return ""
    return (
        f"Big Five personality: Openness={big5.get('openness')}, "
        f"Extraversion={big5.get('extraversion')}, "
        f"Agreeableness={big5.get('agreeableness')}, "
        f"Conscientiousness={big5.get('conscientiousness')}, "
        f"Emotional stability={big5.get('emotional_stability')}."
    )


def prompt_builder(
    patient: dict,
    personalized: bool,
    context_data: dict = None,
    include_big5: bool = False,
    past_notifications: list[dict] | None = None,
) -> dict:
    """
    Build a prompt dict {"system": ..., "user": ...} for the LLM.

    When personalized=True and big5 scores are available, the system prompt
    includes a personality injection block built from Big Five adjective
    descriptors (ported from Prompt_Comparison.py).  The user message is
    always structured via _CONTEXT_TEMPLATE.

    Returns:
        {"system": str, "user": str}
    """
    pid = patient.get("more_participant_id")

    logger.info(
        "prompt_builder inputs — participant=%s, personalized=%s, include_big5=%s",
        pid,
        personalized,
        include_big5,
    )
    logger.info(
        "prompt_builder patient fields:\n%s",
        "\n".join(f"  {k}: {v}" for k, v in patient.items() if k != "context_data"),
    )
    logger.info("prompt_builder context_data:\n%s", context_data)

    # ── System prompt ─────────────────────────────────────────────────────────
    system_parts = [_SYSTEM_PROMPT]

    if personalized:
        big5 = patient.get("big5") or {}
        emotional_stability = big5.get("emotional_stability")
        scores = {
            "extraversion": big5.get("extraversion"),
            "agreeableness": big5.get("agreeableness"),
            "conscientiousness": big5.get("conscientiousness"),
            # emotional_stability is stored instead of neuroticism; invert so
            # that high stability → low N (calm adjectives)
            "neuroticism": (
                (60 - emotional_stability) if emotional_stability is not None else None
            ),
            "openness": big5.get("openness"),
        }
        logger.info(
            "prompt_builder [participant=%s] big5 raw: %s | mapped trait scores: %s",
            pid,
            big5,
            scores,
        )
        if all(v is not None for v in scores.values()):
            desc = _scores_to_description(scores)
            if include_big5:
                desc = desc + "\n\nRaw Big Five scores: " + build_big5_information(big5)
            logger.info(
                "prompt_builder [participant=%s] personality adjective description:\n  %s",
                pid,
                desc,
            )
            system_parts.append(_PERSONALITY_TEMPLATE_2.replace("{description}", desc))
        else:
            logger.warning(
                "prompt_builder [participant=%s] incomplete big5 scores — "
                "skipping personality injection (scores=%s)",
                pid,
                scores,
            )
    else:
        logger.info(
            "prompt_builder [participant=%s] personalized=False — no personality injection",
            pid,
        )

    full_system = "\n\n".join(system_parts)

    # ── User message ──────────────────────────────────────────────────────────
    cd = context_data or {}
    plans_pa = cd.get("plans_pa_today")
    pa_today = cd.get("pa_scheduled_today")
    pa_change_reason = cd.get("pa_change_reason")
    if plans_pa == "N":
        plan_text = (
            f"no PA planned today — {pa_change_reason}"
            if pa_change_reason
            else "no PA planned today"
        )
    else:
        plan_text = pa_today or "not specified"
    user_message = _CONTEXT_TEMPLATE.format(
        valence=cd.get("mood_valence", "N/A"),
        arousal=cd.get("energetic_arousal", "N/A"),
        stress=cd.get("stress", "N/A"),
        locus=cd.get("locus_of_control", "N/A"),
        motivation=cd.get("motivation_pa", "N/A"),
        barriers=cd.get("barrier_pa", "N/A"),
        plan=plan_text,
        reflection=cd.get("events_today") or "(none provided)",
    )

    if past_notifications:
        lines = []
        for i, item in enumerate(past_notifications):
            text = item.get("text", "") if isinstance(item, dict) else item
            feedback_raw = item.get("feedback_raw") if isinstance(item, dict) else None
            if feedback_raw:
                feedback_str = json.dumps(feedback_raw, ensure_ascii=False)
                lines.append(f"{i + 1}. Message: {text}\n   User feedback: {feedback_str}")
            else:
                lines.append(f"{i + 1}. Message: {text}\n   User feedback: (none yet — avoid repeating)")
        user_message += _PAST_NOTIFICATIONS_TEMPLATE.format(entries="\n".join(lines))

    logger.info(
        "prompt_builder [participant=%s] FULL SYSTEM PROMPT:\n%s",
        pid,
        full_system,
    )
    logger.info(
        "prompt_builder [participant=%s] FULL USER MESSAGE:\n%s",
        pid,
        user_message,
    )

    return {"system": full_system, "user": user_message}


# ── Low-level LLM call helpers (shared rate-limit retry) ───────────────────────

def _retry_delay(e, attempt: int) -> float:
    try:
        return float(e.response.headers.get("retry-after") or 0) or (
            LLM_RETRY_BASE_DELAY * (2 ** attempt) + random.random()
        )
    except Exception:
        return LLM_RETRY_BASE_DELAY * (2 ** attempt) + random.random()


def _run_haystack(messages, pid) -> str | None:
    """One generation via the Haystack Anthropic client with rate-limit retry."""
    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            response = anthropic_client.run(messages=messages)
            candidate = response["replies"][-1].text
            logger.info("LLM response for participant %s: %s", pid, candidate)
            return candidate
        except _anthropic.RateLimitError as e:
            if attempt >= LLM_MAX_RETRIES:
                logger.error(
                    "Rate limit: giving up after %d attempt(s) for participant %s",
                    LLM_MAX_RETRIES + 1, pid,
                )
                return None
            delay = _retry_delay(e, attempt)
            logger.warning(
                "Rate limit hit for participant %s (attempt %d/%d), retrying in %.1fs",
                pid, attempt + 1, LLM_MAX_RETRIES + 1, delay,
            )
            time.sleep(delay)
        except Exception as e:
            logger.error("Anthropic API error for participant %s: %s", pid, str(e))
            return None
    return None


def _raw_messages_create(pid=None, **kwargs):
    """Raw Anthropic messages.create with rate-limit retry (used by the agentic loop)."""
    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            return _anthropic_raw.messages.create(**kwargs)
        except _anthropic.RateLimitError as e:
            if attempt >= LLM_MAX_RETRIES:
                logger.error(
                    "Rate limit: giving up after %d attempt(s) for participant %s",
                    LLM_MAX_RETRIES + 1, pid,
                )
                return None
            delay = _retry_delay(e, attempt)
            logger.warning(
                "Rate limit hit for participant %s (attempt %d/%d), retrying in %.1fs",
                pid, attempt + 1, LLM_MAX_RETRIES + 1, delay,
            )
            time.sleep(delay)
        except Exception as e:
            logger.error("Anthropic API error for participant %s: %s", pid, str(e))
            return None
    return None


def _log_prompt(pid, system: str, user: str) -> None:
    logger.info(
        "FULL PROMPT for participant=%s\n--- SYSTEM ---\n%s\n--- USER ---\n%s",
        pid, system, user,
    )


def _generate_with_validation(generate_fn, pid) -> str | None:
    """Run generate_fn up to VALIDATOR_MAX_RETRIES+1 times (3 by default), returning
    the first candidate that passes the Haiku validator."""
    for val_attempt in range(VALIDATOR_MAX_RETRIES + 1):
        candidate = generate_fn()
        if not candidate:
            break
        is_valid, reason = _validate_notification(candidate)
        if is_valid:
            return candidate
        logger.warning(
            "Validator rejected notification (attempt %d/%d) for participant %s: %s",
            val_attempt + 1, VALIDATOR_MAX_RETRIES + 1, pid, reason,
        )
    return None


# ── Pipeline builders — each returns a zero-arg generate_fn callable ────────────
# Personalization (Big Five injection) is always on for every pipeline.

def _pipeline_basic_context(patient: dict, include_big5: bool):
    """Context + Big Five personality only; no examples."""
    pid = patient.get("more_participant_id")
    prompt_parts = prompt_builder(
        patient=patient,
        personalized=True,
        context_data=patient["context_data"],
        include_big5=include_big5,
        past_notifications=None,
    )
    messages = [
        ChatMessage.from_system(prompt_parts["system"]),
        ChatMessage.from_user(prompt_parts["user"]),
    ]
    _log_prompt(pid, prompt_parts["system"], prompt_parts["user"])
    return lambda: _run_haystack(messages, pid)


def _pipeline_rag(patient: dict, include_big5: bool):
    """Deterministic retrieval: one read of the example store, injecting good/bad
    examples from users similar in Big Five + feeling-state."""
    pid = patient.get("more_participant_id")
    context = patient.get("context_data") or {}
    from .db import SessionLocal

    session = SessionLocal()
    try:
        buckets = _retrieve_examples(
            session, patient.get("big5") or {}, context,
            RAG_EXAMPLES_PER_BUCKET, exclude_pid=pid,
        )
    finally:
        session.close()

    examples_block = _format_examples(buckets)
    logger.info(
        "rag pipeline participant=%s good=%d bad=%d",
        pid, len(buckets["good"]), len(buckets["bad"]),
    )
    prompt_parts = prompt_builder(
        patient=patient,
        personalized=True,
        context_data=context,
        include_big5=include_big5,
        past_notifications=None,
    )
    system = prompt_parts["system"]
    user = prompt_parts["user"] + examples_block
    messages = [ChatMessage.from_system(system), ChatMessage.from_user(user)]
    _log_prompt(pid, system, user)
    return lambda: _run_haystack(messages, pid)


_AGENTIC_TOOLS = [
    {
        "name": "find_similar_users",
        "description": (
            "Find users whose Big Five personality (and feeling-state) is most similar to "
            "the current user. Returns participant IDs and their personality profile; pass "
            "the IDs to get_user_examples."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many similar users to return (default 5).",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_user_examples",
        "description": (
            "Fetch past notifications sent to the given users, each with the user's "
            "feeling-state at the time, how they graded it, and whether they exercised "
            "afterwards. Optionally filter by grade (1=good, 3=bad)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "participant_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "grade": {
                    "type": "integer",
                    "description": "Optional grade filter: 1 for good examples, 3 for bad.",
                },
            },
            "required": ["participant_ids"],
        },
    },
]

_AGENTIC_SYSTEM_SUFFIX = (
    "\n\n=== RETRIEVAL TOOLS ===\n"
    "You have tools to learn from how similar users responded to past messages. "
    "Before writing, call find_similar_users to identify users with a similar "
    "personality and feeling-state, then get_user_examples to see which messages they "
    "graded good (1) or bad (3) and whether they ended up exercising. Emulate what "
    "worked and steer clear of what did not. Once you have gathered what you need, reply "
    "with ONLY the final motivational message — no tool talk, no preamble, no explanation."
)


def _execute_agentic_tool(name: str, tool_input: dict, big5: dict, context: dict, pid) -> dict:
    from .db import SessionLocal

    session = SessionLocal()
    try:
        if name == "find_similar_users":
            limit = int(tool_input.get("limit") or SIMILAR_PARTICIPANTS_N)
            return {
                "users": _find_similar_participants(
                    session, big5, context, limit, exclude_pid=pid
                )
            }
        if name == "get_user_examples":
            pids = [int(p) for p in (tool_input.get("participant_ids") or [])]
            grade = tool_input.get("grade")
            grade = int(grade) if grade is not None else None
            return {"examples": _get_examples_for_participants(session, pids, grade)}
        return {"error": f"unknown tool {name}"}
    except Exception as e:
        logger.error("agentic tool %s failed for participant %s: %s", name, pid, e)
        return {"error": str(e)}
    finally:
        session.close()


def _run_agentic_loop(system: str, user: str, big5: dict, context: dict, pid) -> str | None:
    messages = [{"role": "user", "content": user}]
    for _ in range(AGENTIC_MAX_TOOL_ITERS):
        resp = _raw_messages_create(
            pid=pid,
            model=GENERATION_MODEL,
            max_tokens=1024,
            system=system,
            tools=_AGENTIC_TOOLS,
            messages=messages,
        )
        if resp is None:
            return None
        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    result = _execute_agentic_tool(block.name, block.input, big5, context, pid)
                    logger.info(
                        "agentic tool call participant=%s tool=%s input=%s -> %s",
                        pid, block.name, block.input, result,
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    })
            messages.append({"role": "user", "content": tool_results})
            continue
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        ).strip()
        logger.info("agentic LLM response for participant %s: %s", pid, text)
        return text or None
    logger.warning("agentic loop hit max tool iterations for participant %s", pid)
    return None


def _pipeline_agentic(patient: dict, include_big5: bool):
    """Tool-use loop: Opus fetches similar users' graded examples from the store itself."""
    pid = patient.get("more_participant_id")
    big5 = patient.get("big5") or {}
    context = patient.get("context_data") or {}
    prompt_parts = prompt_builder(
        patient=patient,
        personalized=True,
        context_data=context,
        include_big5=include_big5,
        past_notifications=None,
    )
    system = prompt_parts["system"] + _AGENTIC_SYSTEM_SUFFIX
    user = prompt_parts["user"]
    _log_prompt(pid, system, user)
    return lambda: _run_agentic_loop(system, user, big5, context, pid)


_PIPELINE_BUILDERS = {
    PIPELINE_BASIC_CONTEXT: _pipeline_basic_context,
    PIPELINE_RAG: _pipeline_rag,
    PIPELINE_AGENTIC: _pipeline_agentic,
}


def _generate_for_single_patient(patient: dict, include_big5: bool) -> dict | None:
    pid = patient.get("more_participant_id")
    pipeline = patient.get("pipeline")
    if pipeline not in _PIPELINE_BUILDERS:
        pipeline = PIPELINE_BASIC_CONTEXT
    logger.info(
        "calling Anthropic for participant=%s group=%s pipeline=%s model=%s",
        pid, patient.get("group_id"), PIPELINE_NAMES.get(pipeline), GENERATION_MODEL,
    )

    generate_fn = _PIPELINE_BUILDERS[pipeline](patient, include_big5)
    notification_text = _generate_with_validation(generate_fn, pid)

    if notification_text:
        logger.info("Generated notification for %s: %s", patient.get("name"), notification_text)
        return {
            "patient_id": patient.get("id"),
            "patient_name": patient.get("name"),
            "firebase_token": patient.get("firebase_token"),
            "notification_text": notification_text,
            "group_id": patient.get("group_id"),
            "pipeline": pipeline,
        }
    logger.warning("Skipping notification for %s due to generation failure", patient.get("name"))
    return None


def generate_notifications_for_patients(patients: list, include_big5: bool = False):
    """
    Generate personalized notifications for a list of patients.
    LLM calls run in parallel using LLM_GENERATION_THREADS worker threads.
    """
    notifications = []
    with ThreadPoolExecutor(max_workers=LLM_GENERATION_THREADS) as executor:
        futures = {
            executor.submit(_generate_for_single_patient, patient, include_big5): patient
            for patient in patients
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                notifications.append(result)
    return notifications
