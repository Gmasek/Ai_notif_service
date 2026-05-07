from haystack.components.generators import OpenAIGenerator
from dotenv import load_dotenv
import asyncio
import random
import logging

load_dotenv()

logger = logging.getLogger(__name__)


openai_client = OpenAIGenerator(model="gpt-4o-mini")


# ── Big Five adjective tables (ported from Prompt_Comparison.py) ──────────────

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
    "Please have the length of the motivational message reflect the length of user input of planned activity today and open reflection"
    "Keep it kind of short if user is replying under 10-15 words"
)


### Simple llm generation with prompt using template
### Can do Async later, but i dont think its necessary
## No context
def generateNotif_gpt():
    response = openai_client.run(prompt=_SYSTEM_PROMPT)
    print(response["replies"][-1])
    return response["replies"][-1]


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
            "pa_scheduled_today",
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

    pa_today = context_data.get("pa_scheduled_today")
    if pa_today:
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
            logger.info(
                "prompt_builder [participant=%s] personality adjective description:\n  %s",
                pid,
                desc,
            )
            system_parts.append(_PERSONALITY_TEMPLATE.replace("{description}", desc))
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
    user_message = _CONTEXT_TEMPLATE.format(
        valence=cd.get("mood_valence", "N/A"),
        arousal=cd.get("energetic_arousal", "N/A"),
        stress=cd.get("stress", "N/A"),
        locus=cd.get("locus_of_control", "N/A"),
        motivation=cd.get("motivation_pa", "N/A"),
        barriers=cd.get("barrier_pa", "N/A"),
        plan=cd.get("pa_scheduled_today") or "not specified",
        reflection=cd.get("events_today") or "(none provided)",
    )

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


def generate_notifications_for_patients(
    patients: list, context_data: dict = None, include_big5: bool = False
):
    """
    Generate personalized notifications for a list of patients.

    Args:
        patients: List of patient dictionaries with their full information
        context_data: Optional dict with contextual information (day, time, weather, activity, etc.)
        include_big5: Whether to include Big Five personality traits in the prompt

    Returns:
        List of generated notifications with patient info
    """
    notifications = []

    for patient in patients:
        # Extract relevant patient context (excluding id, firebase_token, notif_in_24h, time_to_notif, created_at)
        probability = random.randint(1, 10)
        was_personalized = False
        if patient.get("group_id") == 1:
            was_personalized = probability < 4
        elif patient.get("group_id") == 2:
            was_personalized = probability < 7
        else:
            was_personalized = probability < 10
        prompt_parts = prompt_builder(
            patient=patient,
            personalized=was_personalized,
            context_data=patient["context_data"],
            include_big5=include_big5,
        )
        # Generate notification using OpenAI
        logger.info(
            "calling OpenAI for participant=%s group=%s personalized=%s model=gpt-4o-mini",
            patient.get("more_participant_id"),
            patient.get("group_id"),
            was_personalized,
        )
        try:
            response = openai_client.run(
                prompt=prompt_parts["user"],
                system_prompt=prompt_parts["system"],
            )
            notification_text = response["replies"][-1]
            logger.info(
                "LLM response for participant %s: %s",
                patient.get("more_participant_id"),
                notification_text,
            )
        except Exception as e:
            logger.error(
                "OpenAI API error for participant %s: %s",
                patient.get("more_participant_id"),
                str(e),
            )
            notification_text = None

        if notification_text:
            notifications.append(
                {
                    "patient_id": patient.get("id"),
                    "patient_name": patient.get("name"),
                    "firebase_token": patient.get("firebase_token"),
                    "notification_text": notification_text,
                    "was_personalized": was_personalized,
                    "group_id": patient.get("group_id"),
                }
            )
            print(
                f"Generated notification for {patient.get('name')}: {notification_text}"
            )
        else:
            print(
                f"Skipping notification for {patient.get('name')} due to generation failure"
            )

    return notifications


async def send_notifications_via_firebase_async(notifications: list):
    """
    Send generated notifications via Firebase Cloud Messaging asynchronously.

    Args:
        notifications: List of notification dicts from generate_notifications_for_patients

    Returns:
        Dict with sending results
    """
    from .firebase_messaging import send_batch_notifications_async

    # Convert notification format to Firebase format
    firebase_notifications = []
    for notif in notifications:
        if notif.get("firebase_token"):
            firebase_notifications.append(
                {
                    "token": notif["firebase_token"],
                    "title": "Time to Move!",
                    "body": notif["notification_text"],
                    "data": {
                        "patient_id": str(notif["patient_id"]),
                        "patient_name": str(notif["patient_name"]),
                        "was_personalized": str(notif.get("was_personalized", False)),
                        "group_id": str(notif.get("group_id", "")),
                    },
                }
            )

    if not firebase_notifications:
        return {
            "status": "skipped",
            "message": "No valid Firebase tokens found",
            "success_count": 0,
            "failure_count": 0,
        }

    # Send all notifications
    result = await send_batch_notifications_async(firebase_notifications)
    return result


def send_notifications_via_firebase(notifications: list):
    """
    Synchronous wrapper for sending notifications via Firebase.

    Args:
        notifications: List of notification dicts from generate_notifications_for_patients

    Returns:
        Dict with sending results
    """
    # Use existing event loop if available, otherwise create new one
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # If already in an async context, create a new loop in a thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                asyncio.run, send_notifications_via_firebase_async(notifications)
            )
            return future.result()
    else:
        return asyncio.run(send_notifications_via_firebase_async(notifications))
