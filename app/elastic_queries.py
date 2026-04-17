"""
Elasticsearch query helpers for the AI notification prototype.

Data stored by the MORE Data Gateway / Study Manager has these conventions:
  - Index:             study_{ELASTIC_STUDY_ID}
  - participant_id:    "participant_{more_participant_id}"   e.g. "participant_3"
  - observation_type:  "lime-survey-observation" for ALL LimeSurvey surveys
  - observation_id:    the study-manager observation ID as a STRING e.g. "1", "2"
  - Data fields:       stored with "data_" prefix
                       e.g. LimeSurvey field "Q00[SQ001]" → "data_Q00[SQ001]"

How to find your observation IDs:
  GET http://localhost:8000/elastic_info          (diagnostic endpoint)
  — or run:  python check_elastic.py

Then set these env vars to match your study's observation IDs:
  ELASTIC_BASELINE_OBS_ID           baseline personality survey (Big Five, TPB, demographics)
  ELASTIC_DAILY_CHECKIN_OBS_ID      daily momentary assessment (mood, energy, stress …)
  ELASTIC_PA_SCHEDULE_OBS_ID        PA-schedule / notification-window survey
  ELASTIC_EVENING_FOLLOWUP_OBS_ID   evening exercise follow-up survey
  ELASTIC_MESSAGE_EVAL_OBS_ID       notification message evaluation survey
"""
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from .elastic_client import get_elastic_client, get_index_name

logger = logging.getLogger(__name__)

# All LimeSurvey surveys share this observation_type — do NOT change this.
LIME_OBS_TYPE = "lime-survey-observation"

# Observation IDs from the study manager (BIGINT stored as STRING in ES).
# Find yours via:  python check_elastic.py   or   GET /elastic_info
BASELINE_OBS_ID = os.environ.get("ELASTIC_BASELINE_OBS_ID", "")
DAILY_CHECKIN_OBS_ID = os.environ.get("ELASTIC_DAILY_CHECKIN_OBS_ID", "")
PA_SCHEDULE_OBS_ID = os.environ.get("ELASTIC_PA_SCHEDULE_OBS_ID", "")
EVENING_FOLLOWUP_OBS_ID = os.environ.get("ELASTIC_EVENING_FOLLOWUP_OBS_ID", "")
MESSAGE_EVAL_OBS_ID = os.environ.get("ELASTIC_MESSAGE_EVAL_OBS_ID", "")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pid(more_participant_id: int) -> str:
    return f"participant_{more_participant_id}"


def _strip_data_prefix(source: dict) -> dict:
    """Remove the 'data_' prefix from Elasticsearch field names to recover LimeSurvey keys."""
    return {k[5:]: v for k, v in source.items() if k.startswith("data_")}


def _latest_doc(observation_id: str, more_participant_id: int) -> Optional[dict]:
    """
    Return the most recent ES document for a given observation_id and participant.

    All LimeSurvey surveys share observation_type='lime-survey-observation'.
    The observation_id (e.g. "1", "2") identifies which survey.
    """
    if not observation_id:
        logger.warning("_latest_doc: observation_id is not configured — skipping query")
        return None

    es = get_elastic_client()
    try:
        result = es.search(
            index=get_index_name(),
            query={
                "bool": {
                    "must": [
                        {"term": {"observation_type.keyword": LIME_OBS_TYPE}},
                        {"term": {"observation_id.keyword": observation_id}},
                        {"term": {"participant_id.keyword": _pid(more_participant_id)}},
                    ]
                }
            },
            sort=[{"effective_time_frame": {"order": "desc"}}],
            size=1,
        )
        hits = result["hits"]["hits"]
        return hits[0]["_source"] if hits else None
    except Exception as e:
        logger.error(
            "ES _latest_doc failed [obs_id=%s, pid=%d]: %s",
            observation_id, more_participant_id, e,
        )
        return None


# ---------------------------------------------------------------------------
# Public query functions
# ---------------------------------------------------------------------------

def get_all_baseline_participant_ids() -> list:
    """
    Return a list of more_participant_ids (int) for every participant
    who has at least one completed baseline survey in Elasticsearch.
    """
    if not BASELINE_OBS_ID:
        logger.warning("get_all_baseline_participant_ids: ELASTIC_BASELINE_OBS_ID not set")
        return []

    es = get_elastic_client()
    try:
        result = es.search(
            index=get_index_name(),
            query={
                "bool": {
                    "must": [
                        {"term": {"observation_type.keyword": LIME_OBS_TYPE}},
                        {"term": {"observation_id.keyword": str(BASELINE_OBS_ID)}},
                    ]
                }
            },
            _source=["participant_id"],
            size=1000,
        )
    except Exception as e:
        logger.error("get_all_baseline_participant_ids failed: %s", e)
        return []

    ids = []
    for hit in result["hits"]["hits"]:
        pid_str = hit["_source"].get("participant_id", "")
        if pid_str.startswith("participant_"):
            try:
                ids.append(int(pid_str[12:]))
            except ValueError:
                pass
    return ids

def get_patient_profile(more_participant_id: int) -> Optional[dict]:
    """
    Fetch and parse the latest baseline survey for a participant from Elasticsearch.

    Requires: ELASTIC_BASELINE_OBS_ID
    Returns: {name, age, gender, job_type, big5, tpb, hobbies} or None.
    """
    from .lime_parser import parse_bigfive, parse_g05q40_tpb, parse_breq3, parse_hobbies

    doc = _latest_doc(BASELINE_OBS_ID, more_participant_id)
    if doc is None:
        return None

    lime_data = _strip_data_prefix(doc)

    name = lime_data.get("G01Q36") or _pid(more_participant_id)

    age = None
    if lime_data.get("age"):
        try:
            age = int(lime_data["age"])
        except (ValueError, TypeError):
            pass

    gender = lime_data.get("gender")
    job_type = lime_data.get("occupation")
    if job_type == "-oth-":
        job_type = lime_data.get("occupation[other]") or "other"

    tpb = {**(parse_g05q40_tpb(lime_data) or {}), **(parse_breq3(lime_data) or {})}

    return {
        "name": name,
        "age": age,
        "gender": gender,
        "job_type": job_type,
        "big5": parse_bigfive(lime_data),
        "tpb": tpb or None,
        "hobbies": parse_hobbies(lime_data),
    }


def get_notification_schedule(more_participant_id: int) -> Optional[dict]:
    """
    Fetch and parse the latest PA-schedule survey for a participant from Elasticsearch.

    Requires: ELASTIC_PA_SCHEDULE_OBS_ID
    Returns: {"monday": {"start": "08:00", "end": "18:00"}, ...} or None.
    """
    from .lime_parser import parse_time_to_notif

    doc = _latest_doc(PA_SCHEDULE_OBS_ID, more_participant_id)
    if doc is None:
        return None

    lime_data = _strip_data_prefix(doc)
    return parse_time_to_notif(lime_data)


def get_todays_checkin(more_participant_id: int) -> Optional[dict]:
    """
    Fetch and parse today's daily check-in for a participant from Elasticsearch.

    Requires: ELASTIC_DAILY_CHECKIN_OBS_ID
    Returns a context dict or None if no check-in found today.
    """
    if not DAILY_CHECKIN_OBS_ID:
        logger.warning("get_todays_checkin: ELASTIC_DAILY_CHECKIN_OBS_ID not set")
        return None

    es = get_elastic_client()
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_end = today_start + timedelta(days=1)

    try:
        result = es.search(
            index=get_index_name(),
            query={
                "bool": {
                    "must": [
                        {"term": {"observation_type.keyword": LIME_OBS_TYPE}},
                        {"term": {"observation_id.keyword": DAILY_CHECKIN_OBS_ID}},
                        {"term": {"participant_id.keyword": _pid(more_participant_id)}},
                        {"range": {"effective_time_frame": {
                            "gte": today_start.isoformat(),
                            "lt": today_end.isoformat(),
                        }}},
                    ]
                }
            },
            sort=[{"effective_time_frame": {"order": "desc"}}],
            size=1,
        )
        hits = result["hits"]["hits"]
        if not hits:
            return None
    except Exception as e:
        logger.error(
            "get_todays_checkin failed for participant %d: %s", more_participant_id, e
        )
        return None

    lime_data = _strip_data_prefix(hits[0]["_source"])

    def _int(val):
        try:
            return int(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    return {
        "mood_valence": _int(lime_data.get("Q00[SQ001]")),
        "energetic_arousal": _int(lime_data.get("Q00[SQ002]")),
        "locus_of_control": _int(lime_data.get("Q00[SQ003]")),
        "motivation_pa": _int(lime_data.get("Q00[SQ004]")),
        "barrier_pa": _int(lime_data.get("Q00[SQ005]")),
        "stress": _int(lime_data.get("Q00[SQ006]")),
        "events_today": lime_data.get("G01Q02"),
        "pa_scheduled_today": lime_data.get("G01Q03"),
        "day": datetime.now().strftime("%A"),
        "time": datetime.now().strftime("%H:%M"),
    }


def get_recent_evening_followups(since_hours: int = 24) -> list:
    """Fetch evening follow-up responses from the last N hours. Requires ELASTIC_EVENING_FOLLOWUP_OBS_ID."""
    if not EVENING_FOLLOWUP_OBS_ID:
        logger.warning("get_recent_evening_followups: ELASTIC_EVENING_FOLLOWUP_OBS_ID not set")
        return []

    es = get_elastic_client()
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    try:
        result = es.search(
            index=get_index_name(),
            query={
                "bool": {
                    "must": [
                        {"term": {"observation_type.keyword": LIME_OBS_TYPE}},
                        {"term": {"observation_id.keyword": EVENING_FOLLOWUP_OBS_ID}},
                        {"range": {"effective_time_frame": {"gte": since.isoformat()}}},
                    ]
                }
            },
            sort=[{"effective_time_frame": {"order": "desc"}}],
            size=100,
        )
    except Exception as e:
        logger.error("get_recent_evening_followups failed: %s", e)
        return []

    records = []
    for hit in result["hits"]["hits"]:
        src = hit["_source"]
        d = _strip_data_prefix(src)
        records.append({
            "participant_id": src.get("participant_id"),
            "effective_time_frame": src.get("effective_time_frame"),
            "exercised": d.get("Q00") == "Y",
            "activity": d.get("G00Q02"),
            "duration": d.get("G00Q03"),
            "when": d.get("G00Q08"),
            "other_activity": d.get("G00Q05") == "Y",
            "other_activity_desc": d.get("G00Q06"),
            "other_duration": d.get("G00Q07"),
        })
    return records


def get_recent_message_evals(since_hours: int = 24) -> list:
    """Fetch message evaluation responses from the last N hours. Requires ELASTIC_MESSAGE_EVAL_OBS_ID."""
    if not MESSAGE_EVAL_OBS_ID:
        logger.warning("get_recent_message_evals: ELASTIC_MESSAGE_EVAL_OBS_ID not set")
        return []

    es = get_elastic_client()
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    try:
        result = es.search(
            index=get_index_name(),
            query={
                "bool": {
                    "must": [
                        {"term": {"observation_type.keyword": LIME_OBS_TYPE}},
                        {"term": {"observation_id.keyword": MESSAGE_EVAL_OBS_ID}},
                        {"range": {"effective_time_frame": {"gte": since.isoformat()}}},
                    ]
                }
            },
            sort=[{"effective_time_frame": {"order": "desc"}}],
            size=100,
        )
    except Exception as e:
        logger.error("get_recent_message_evals failed: %s", e)
        return []

    return [
        {
            "participant_id": hit["_source"].get("participant_id"),
            "effective_time_frame": hit["_source"].get("effective_time_frame"),
            "data": _strip_data_prefix(hit["_source"]),
        }
        for hit in result["hits"]["hits"]
    ]


# ---------------------------------------------------------------------------
# Discovery helper — used by /elastic_info and check_elastic.py
# ---------------------------------------------------------------------------

def discover_observations() -> dict:
    """
    Return a diagnostic summary of the study index.

    First tries to find documents filtered by observation_type='lime-survey-observation'.
    If none are found, falls back to a raw sample of all documents in the index so the
    caller can see what observation_type values actually exist.
    """
    es = get_elastic_client()
    index = get_index_name()

    def _summarise(buckets):
        summary = []
        for bucket in buckets:
            obs_id = bucket["key"]
            count = bucket["doc_count"]
            hit = bucket["sample"]["hits"]["hits"]
            src = hit[0]["_source"] if hit else {}
            data_fields = sorted(k for k in src if k.startswith("data_"))
            summary.append({
                "observation_id": obs_id,
                "document_count": count,
                "sample_participant": src.get("participant_id"),
                "sample_observation_type": src.get("observation_type"),
                "sample_time": src.get("effective_time_frame"),
                "data_fields": data_fields[:20],
            })
        return summary

    agg_def = {
        "by_observation": {
            "terms": {"field": "observation_id.keyword", "size": 20},
            "aggs": {
                "sample": {"top_hits": {"size": 1, "_source": {"excludes": ["data_bigfive*"]}}}
            },
        }
    }

    try:
        # Try filtered query first
        result = es.search(
            index=index,
            query={"term": {"observation_type.keyword": LIME_OBS_TYPE}},
            aggs=agg_def,
            size=0,
        )
        buckets = result.get("aggregations", {}).get("by_observation", {}).get("buckets", [])

        if buckets:
            return {"filter": f"observation_type={LIME_OBS_TYPE}", "observations": _summarise(buckets)}

        # Nothing matched — show raw sample to reveal actual field values
        raw = es.search(
            index=index,
            query={"match_all": {}},
            aggs={
                "by_obs_type": {
                    "terms": {"field": "observation_type.keyword", "size": 20},
                },
                **agg_def,
            },
            size=3,
        )
        all_buckets = raw.get("aggregations", {}).get("by_observation", {}).get("buckets", [])
        obs_types = [b["key"] for b in raw.get("aggregations", {}).get("by_obs_type", {}).get("buckets", [])]
        sample_docs = [
            {k: v for k, v in h["_source"].items() if not k.startswith("data_")}
            for h in raw["hits"]["hits"]
        ]
        return {
            "warning": f"No documents found with observation_type='{LIME_OBS_TYPE}'. Raw index sample below.",
            "actual_observation_types": obs_types,
            "observations": _summarise(all_buckets),
            "raw_sample_docs": sample_docs,
        }
    except Exception as e:
        logger.error("discover_observations failed: %s", e)
        return {"error": str(e)}
