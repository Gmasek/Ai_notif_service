"""
Parser for LimeSurvey response data to Patient model format.
"""
import re
import random
from typing import Optional


# Map msgdays SQ codes to day names
DAY_MAP = {
    "SQ001": "monday",
    "SQ002": "tuesday",
    "SQ003": "wednesday",
    "SQ004": "thursday",
    "SQ005": "friday",
    "SQ006": "saturday",
    "SQ007": "sunday",
}

# Map patype SQ codes to activity names
ACTIVITY_MAP = {
    "SQ001": "walking",
    "SQ002": "running",
    "SQ003": "cycling",
    "SQ004": "swimming",
    "SQ005": "gym",
    "SQ006": "yoga",
    "SQ007": "pilates",
    "SQ008": "dancing",
    "SQ009": "hiking",
    "SQ010": "tennis",
    "SQ011": "football",
    "SQ012": "basketball",
    "SQ013": "volleyball",
    "SQ014": "badminton",
    "SQ015": "golf",
    "SQ016": "martial_arts",
    "SQ017": "climbing",
    "SQ018": "skating",
    "SQ019": "skiing",
    "SQ020": "rowing",
    "SQ021": "boxing",
    "SQ022": "crossfit",
    "SQ023": "aerobics",
}


def parse_time_range(time_str: str) -> Optional[dict]:
    """
    Parse a time range string like "12:30-16:30" into {"start": "12:30", "end": "16:30"}.
    Returns None if parsing fails.
    """
    if not time_str:
        return None

    match = re.match(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", time_str.strip())
    if match:
        return {"start": match.group(1), "end": match.group(2)}
    return None


# IPIP 50-item Big Five scoring
# Based on https://ipip.ori.org/new_ipip-50-item-scale.htm
# Positive-keyed items are added, reverse-keyed items are reversed (6 - score)
BIG5_SCORING = {
    "extraversion": {
        "positive": [1, 11, 21, 31, 41],
        "reverse": [6, 16, 26, 36, 46],
    },
    "agreeableness": {
        "positive": [7, 17, 27, 37, 42, 47],
        "reverse": [2, 12, 22, 32],
    },
    "conscientiousness": {
        "positive": [3, 13, 23, 33, 43, 48],
        "reverse": [8, 18, 28, 38],
    },
    "emotional_stability": {
        "positive": [9, 19],
        "reverse": [4, 14, 24, 29, 34, 39, 44, 49],
    },
    "openness": {
        "positive": [5, 15, 25, 35, 40, 45, 50],
        "reverse": [10, 20, 30],
    },
}


def parse_bigfive(lime_data: dict) -> dict:
    """
    Calculate Big Five personality trait scores from LimeSurvey data.
    Uses IPIP 50-item scale scoring:
    - https://ipip.ori.org/new_ipip-50-item-scale.htm
    - https://ipip.ori.org/newScoringInstructions.htm

    Keys in lime_data are like bigfive[SQ001] through bigfive[SQ050].
    Response scale: 1-5 (1=Very Inaccurate, 5=Very Accurate)
    Reverse-scored items: score = 6 - raw_score

    Returns dict with summed trait scores (per IPIP instructions).
    Each trait has 10 items, so scores range from 10-50.
    """
    # Extract raw scores from lime_data
    raw_scores = {}
    for key, value in lime_data.items():
        if key.startswith("bigfive[") and key.endswith("]"):
            sq_code = key[8:-1]  # Extract SQ001, SQ002, etc.
            if value:
                try:
                    # Extract item number from SQ001 -> 1
                    item_num = int(sq_code[2:])
                    raw_scores[item_num] = int(value)
                except ValueError:
                    pass

    if not raw_scores:
        return None

    big5 = {}
    for trait, scoring in BIG5_SCORING.items():
        total = 0

        # Add positive-keyed items
        for item in scoring["positive"]:
            if item in raw_scores:
                total += raw_scores[item]

        # Add reverse-scored items (6 - score)
        for item in scoring["reverse"]:
            if item in raw_scores:
                total += (6 - raw_scores[item])

        big5[trait] = total

    return big5 if big5 else None


def parse_breq3(lime_data: dict) -> dict:
    """
    Parse BREQ-3 affective attitude items from the baseline survey.

    The survey stores these under G05Q36[SQ002–SQ006] as a semantic
    differential scale (AO01–AO07 → 1–7):
      SQ002: Unenjoyable–Very Enjoyable
      SQ003: Bad–Good
      SQ004: Useless–Useful
      SQ005: Boring–Fun
      SQ006: Harmful–Beneficial

    Keys are stored with a "breq3_" prefix to avoid collision with the
    G05Q40 TPB items when both are merged into the tpb field.
    """
    result = {}
    for key, value in lime_data.items():
        if key.startswith("G05Q36[") and key.endswith("]"):
            sq_code = key[7:-1]  # e.g. "SQ002"
            if value:
                result[f"breq3_{sq_code}"] = _AO_MAP.get(value, value)
    return result if result else None


# Answer-option codes used in array questions (AO01–AO07 → 1–7)
_AO_MAP = {f"AO{i:02d}": i for i in range(1, 8)}


def parse_g05q40_tpb(lime_data: dict) -> dict:
    """
    Parse the G05Q40 TPB scale from the baseline survey.

    Covers intention, attitude, perceived behavioural control, subjective norms,
    implementation intentions, goal setting, and action control items (SQ001–SQ017).
    Answer codes AO01–AO07 are converted to integers 1–7.
    """
    tpb = {}
    for key, value in lime_data.items():
        if key.startswith("G05Q40[") and key.endswith("]"):
            sq_code = key[7:-1]  # e.g. "SQ001"
            if value:
                tpb[sq_code] = _AO_MAP.get(value, value)
    return tpb if tpb else None


def parse_time_to_notif(lime_data: dict) -> dict:
    """
    Parse msgdays fields into time_to_notif format.
    Input: msgdays[SQ001]='Y', msgdays[SQ001comment]='12:30-16:30'
    Output: {"monday": {"start": "12:30", "end": "16:30"}}
    """
    time_to_notif = {}

    for sq_code, day_name in DAY_MAP.items():
        # Check if this day is selected
        day_key = f"msgdays[{sq_code}]"
        comment_key = f"msgdays[{sq_code}comment]"

        if lime_data.get(day_key) == "Y":
            time_range = lime_data.get(comment_key, "")
            parsed = parse_time_range(time_range)
            if parsed:
                time_to_notif[day_name] = parsed

    return time_to_notif if time_to_notif else None


def parse_hobbies(lime_data: dict, rating_threshold: int = 4) -> list:
    """
    Parse activity/hobby fields into a list of activity names.

    Supports two survey formats:
    - Checkbox format: patype[SQ001] == "Y"
    - Rating scale format: G07Q33[SQ001] with AO01-AO07 values; activities rated
      >= rating_threshold (default 4) are included.
    """
    hobbies = []

    for sq_code, activity_name in ACTIVITY_MAP.items():
        # Checkbox format (patype)
        if lime_data.get(f"patype[{sq_code}]") == "Y":
            hobbies.append(activity_name)
            continue

        # Rating scale format (G07Q33)
        raw = lime_data.get(f"G07Q33[{sq_code}]")
        if raw:
            score = _AO_MAP.get(raw)
            if score is not None and score >= rating_threshold:
                hobbies.append(activity_name)

    # Add custom "other" activity if specified
    other_activity = lime_data.get("patype[other]") or lime_data.get("tapaother")
    if other_activity and other_activity.strip():
        hobbies.append(other_activity.strip())

    return hobbies if hobbies else None


def parse_limesurvey_to_patient(
    lime_responses: list,
    firebase_token: str,
    participant_id: str,
) -> dict:
    """
    Transform LimeSurvey response data into Patient model format.

    Args:
        lime_responses: List of LimeSurvey response dicts (usually contains one entry)
        firebase_token: Firebase push notification token
        participant_id: Participant identifier (used as name fallback)

    Returns:
        Dict matching PatientCreate schema
    """
    if not lime_responses:
        raise ValueError("No LimeSurvey responses provided")

    # Take the first (and usually only) response
    lime_data = lime_responses[0]

    # Prefer the in-survey nickname (G01Q36) over the token-based participant_id
    name = lime_data.get("G01Q36") or str(participant_id)

    # Parse age
    age = None
    if lime_data.get("age"):
        try:
            age = int(lime_data["age"])
        except ValueError:
            pass

    # Parse gender
    gender = lime_data.get("gender")

    # Parse job_type from occupation
    job_type = lime_data.get("occupation")
    if job_type == "-oth-":
        job_type = lime_data.get("occupation[other]") or "other"

    # TPB: merge G05Q40 items with BREQ-3 affective attitude items (G05Q36)
    tpb = {**(parse_g05q40_tpb(lime_data) or {}), **(parse_breq3(lime_data) or {})}

    # Build the patient dict
    patient_data = {
        "name": name,
        "firebase_token": firebase_token,
        "age": age,
        "gender": gender,
        "job_type": job_type,
        "big5": parse_bigfive(lime_data),
        "tpb": tpb,
        "hobbies": parse_hobbies(lime_data),
        "group_id": random.randint(0, 2),
        "time_to_notif": parse_time_to_notif(lime_data),
        "notif_in_24h": False,
    }

    return patient_data
