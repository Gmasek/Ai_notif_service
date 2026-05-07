"""
LimeSurvey Remote Control API client — direct data access, bypassing Elasticsearch.

Used when the endUrl callback pipeline is unavailable (known bug) so ES documents
are empty. Connects to LimeSurvey at LIME_REMOTE_URL (default:
http://host.docker.internal:8081/admin/remotecontrol) from inside Docker.

Participant identity: LimeSurvey stores the MORE participant ID as the token's
'firstname' field (set by studymanager when activating participants).
"""

import base64
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

LIME_REMOTE_URL = os.environ.get(
    "LIME_REMOTE_URL", "http://host.docker.internal:8081/admin/remotecontrol"
)
LIME_ADMIN_USER = os.environ.get("LIME_ADMIN_USER", "admin")
LIME_ADMIN_PWD = os.environ.get("LIME_ADMIN_PWD", "admin")

LIME_BASELINE_SURVEY_ID = int(os.environ.get("LIME_BASELINE_SURVEY_ID", "589661"))
LIME_DAILY_CHECKIN_SURVEY_ID = int(
    os.environ.get("LIME_DAILY_CHECKIN_SURVEY_ID", "474444")
)
LIME_PA_SCHEDULE_SURVEY_ID = int(os.environ.get("LIME_PA_SCHEDULE_SURVEY_ID", "537526"))
LIME_EVENING_FOLLOWUP_SURVEY_ID = int(
    os.environ.get("LIME_EVENING_FOLLOWUP_SURVEY_ID", "844354")
)
LIME_MESSAGE_EVAL_SURVEY_ID = int(
    os.environ.get("LIME_MESSAGE_EVAL_SURVEY_ID", "159126")
)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _get_session_key() -> str:
    resp = requests.post(
        LIME_REMOTE_URL,
        json={
            "method": "get_session_key",
            "params": [LIME_ADMIN_USER, LIME_ADMIN_PWD],
            "id": 1,
        },
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json().get("result", "")
    if "Invalid" in str(result):
        raise RuntimeError(f"LimeSurvey auth failed: {result}")
    return result


def _release_session_key(sk: str) -> None:
    try:
        requests.post(
            LIME_REMOTE_URL,
            json={"method": "release_session_key", "params": [sk], "id": 1},
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
    except Exception:
        pass


def _export_responses(sk: str, survey_id: int) -> list:
    """
    Export all complete responses with short-code field names (e.g. Q00[SQ001]).
    Returns list of response dicts; empty list on error or no data.
    """
    resp = requests.post(
        LIME_REMOTE_URL,
        json={
            "method": "export_responses",
            "params": [
                sk,
                survey_id,
                "json",  # sDocumentType
                None,  # sLanguageCode — default language
                "complete",  # sCompletionStatus
                "code",  # sHeadingType — short codes matching lime_parser expectations
                "short",  # sResponseType
            ],
            "id": 1,
        },
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    raw = resp.json().get("result")
    if not raw or isinstance(raw, dict):
        logger.info(
            "_export_responses: no data for survey %d (result=%s)", survey_id, raw
        )
        return []
    try:
        decoded = base64.b64decode(raw)
        return json.loads(decoded).get("responses", [])
    except Exception as e:
        logger.error("_export_responses decode error for survey %d: %s", survey_id, e)
        return []


def _list_participants(sk: str, survey_id: int) -> list:
    """Return list of {token, firstname} dicts for all participants of a survey."""
    resp = requests.post(
        LIME_REMOTE_URL,
        json={
            "method": "list_participants",
            "params": [
                sk,
                survey_id,
                0,  # iStart
                1000,  # iLimit
                False,  # bUnused — False = all participants, True = only unused
                ["token", "firstname", "completed"],  # aAttributes
            ],
            "id": 1,
        },
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json().get("result", [])
    if isinstance(result, dict):
        logger.warning("_list_participants error for survey %d: %s", survey_id, result)
        return []
    return result


def _build_token_to_pid(sk: str, survey_id: int) -> dict:
    """Return {token_string: more_participant_id_int} from the survey's participant table.

    LimeSurvey RC API nests firstname/lastname under 'participant_info', e.g.:
      {"token": "abc", "participant_info": {"firstname": "3", "lastname": "3"}}
    """
    mapping = {}
    for p in _list_participants(sk, survey_id):
        token = p.get("token")
        firstname = p.get("participant_info", {}).get("firstname")
        if token and firstname:
            try:
                mapping[token] = int(firstname)
            except (ValueError, TypeError):
                pass
    return mapping


# ---------------------------------------------------------------------------
# Profile parsing helpers (same logic as elastic_queries.get_patient_profile)
# ---------------------------------------------------------------------------


def _parse_profile(lime_data: dict, pid: int) -> dict:
    from .lime_parser import (
        parse_bigfive,
        parse_g05q40_tpb,
        parse_breq3,
        parse_time_to_notif,
    )

    name = lime_data.get("G01Q36") or f"participant_{pid}"
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
        "time_to_notif": parse_time_to_notif(lime_data),
    }


def _parse_checkin(lime_data: dict) -> dict:
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


# ---------------------------------------------------------------------------
# Public bulk functions — fetch all participants in one session
# ---------------------------------------------------------------------------


def get_all_baseline_profiles_from_lime() -> dict:
    """
    Return {more_participant_id: profile_dict} for every participant with a
    completed baseline survey.  One session key + two API calls.
    """
    sk = _get_session_key()
    try:
        t2pid = _build_token_to_pid(sk, LIME_BASELINE_SURVEY_ID)
        responses = _export_responses(sk, LIME_BASELINE_SURVEY_ID)
    finally:
        _release_session_key(sk)

    profiles = {}
    for r in responses:
        pid = t2pid.get(r.get("token"))
        if pid is None:
            continue
        # Later responses overwrite earlier ones → keeps the most recent submission
        profiles[pid] = _parse_profile(r, pid)

    logger.info("get_all_baseline_profiles_from_lime: %d profile(s)", len(profiles))
    return profiles


def get_all_schedules_from_lime() -> dict:
    """
    Return {more_participant_id: schedule_dict} for every participant with a
    completed PA-schedule survey.  One session key + two API calls.
    """
    from .lime_parser import parse_time_to_notif

    sk = _get_session_key()
    try:
        t2pid = _build_token_to_pid(sk, LIME_PA_SCHEDULE_SURVEY_ID)
        responses = _export_responses(sk, LIME_PA_SCHEDULE_SURVEY_ID)
    finally:
        _release_session_key(sk)

    schedules = {}
    for r in responses:
        pid = t2pid.get(r.get("token"))
        if pid is None:
            continue
        schedule = parse_time_to_notif(r)
        if schedule:
            schedules[pid] = schedule

    logger.info("get_all_schedules_from_lime: %d schedule(s)", len(schedules))
    return schedules


def get_all_todays_checkins_from_lime() -> dict:
    """
    Return {more_participant_id: context_dict} for every participant who has
    a completed check-in survey response submitted today.  One session key + two API calls.

    Date filtering uses 'datestamp' (preferred) or 'submitdate' as fallback.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    sk = _get_session_key()
    try:
        t2pid = _build_token_to_pid(sk, LIME_DAILY_CHECKIN_SURVEY_ID)
        responses = _export_responses(sk, LIME_DAILY_CHECKIN_SURVEY_ID)
    finally:
        _release_session_key(sk)

    checkins = {}
    for r in responses:
        if not str(r.get("submitdate") or "").startswith(today):
            continue
        pid = t2pid.get(r.get("token"))
        if pid is None:
            continue
        checkins[pid] = _parse_checkin(r)

    logger.info(
        "get_all_todays_checkins_from_lime: %d check-in(s) found", len(checkins)
    )
    return checkins


# ---------------------------------------------------------------------------
# Per-participant helpers — convenience wrappers around bulk functions
# ---------------------------------------------------------------------------


def get_patient_profile_from_lime(more_participant_id: int) -> Optional[dict]:
    """
    Single-participant baseline profile.
    Replacement for elastic_queries.get_patient_profile().
    """
    return get_all_baseline_profiles_from_lime().get(more_participant_id)


def get_notification_schedule_from_lime(more_participant_id: int) -> Optional[dict]:
    """
    Single-participant PA schedule.
    Replacement for elastic_queries.get_notification_schedule().
    """
    return get_all_schedules_from_lime().get(more_participant_id)


def get_todays_checkin_from_lime(more_participant_id: int) -> Optional[dict]:
    """
    Single-participant today's check-in.
    Replacement for elastic_queries.get_todays_checkin().
    """
    return get_all_todays_checkins_from_lime().get(more_participant_id)


def get_recent_evening_followups_from_lime(since_hours: int = 24) -> list:
    """
    Fetch evening follow-up survey responses submitted in the last N hours.
    Replacement for elastic_queries.get_recent_evening_followups().
    """
    since = datetime.now() - timedelta(hours=since_hours)
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")

    sk = _get_session_key()
    try:
        t2pid = _build_token_to_pid(sk, LIME_EVENING_FOLLOWUP_SURVEY_ID)
        responses = _export_responses(sk, LIME_EVENING_FOLLOWUP_SURVEY_ID)
    finally:
        _release_session_key(sk)

    records = []
    for r in responses:
        if str(r.get("submitdate", "")) < since_str:
            continue
        pid = t2pid.get(r.get("token"))
        records.append(
            {
                "participant_id": f"participant_{pid}" if pid else None,
                "submitdate": r.get("submitdate"),
                "exercised": r.get("Q00") == "Y",
                "activity": r.get("G00Q02"),
                "duration": r.get("G00Q03"),
                "when": r.get("G00Q08"),
                "other_activity": r.get("G00Q05") == "Y",
                "other_activity_desc": r.get("G00Q06"),
                "other_duration": r.get("G00Q07"),
            }
        )
    logger.info(
        "get_recent_evening_followups_from_lime: %d response(s) in last %dh",
        len(records),
        since_hours,
    )
    return records


def get_recent_message_evals_from_lime(since_hours: int = 24) -> list:
    """
    Fetch notification message evaluation responses submitted in the last N hours.
    Replacement for elastic_queries.get_recent_message_evals().
    """
    since = datetime.now() - timedelta(hours=since_hours)
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")

    sk = _get_session_key()
    try:
        t2pid = _build_token_to_pid(sk, LIME_MESSAGE_EVAL_SURVEY_ID)
        responses = _export_responses(sk, LIME_MESSAGE_EVAL_SURVEY_ID)
    finally:
        _release_session_key(sk)

    records = []
    for r in responses:
        if str(r.get("submitdate", "")) < since_str:
            continue
        pid = t2pid.get(r.get("token"))
        # Return all question fields (exclude LimeSurvey metadata fields)
        meta = {
            "id",
            "submitdate",
            "lastpage",
            "startlanguage",
            "seed",
            "token",
            "startdate",
            "datestamp",
        }
        data = {k: v for k, v in r.items() if k not in meta}
        records.append(
            {
                "participant_id": f"participant_{pid}" if pid else None,
                "submitdate": r.get("submitdate"),
                "data": data,
            }
        )
    logger.info(
        "get_recent_message_evals_from_lime: %d response(s) in last %dh",
        len(records),
        since_hours,
    )
    return records
