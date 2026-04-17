#!/usr/bin/env python3
"""
Diagnostic script — run this BEFORE starting the service to:
  1. Verify the Elasticsearch connection
  2. Discover which observation_id maps to which LimeSurvey survey
  3. Validate the ELASTIC_*_OBS_ID env vars in .env.local

Usage:
    python check_elastic.py

Or with env vars overridden:
    ELASTIC_HOST=localhost ELASTIC_PASSWORD=secret python check_elastic.py
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv(".env.local")

from elasticsearch import Elasticsearch

ELASTIC_HOST = os.environ.get("ELASTIC_HOST", "localhost")
ELASTIC_PORT = int(os.environ.get("ELASTIC_PORT", 9200))
ELASTIC_USERNAME = os.environ.get("ELASTIC_USERNAME", "elastic")
ELASTIC_PASSWORD = os.environ.get("ELASTIC_PASSWORD", "")
ELASTIC_SCHEME = os.environ.get("ELASTIC_SCHEME", "http")
ELASTIC_STUDY_ID = os.environ.get("ELASTIC_STUDY_ID", "1")
INDEX = f"study_{ELASTIC_STUDY_ID}"

CONFIGURED_IDS = {
    "ELASTIC_BASELINE_OBS_ID":          os.environ.get("ELASTIC_BASELINE_OBS_ID", ""),
    "ELASTIC_DAILY_CHECKIN_OBS_ID":     os.environ.get("ELASTIC_DAILY_CHECKIN_OBS_ID", ""),
    "ELASTIC_PA_SCHEDULE_OBS_ID":       os.environ.get("ELASTIC_PA_SCHEDULE_OBS_ID", ""),
    "ELASTIC_EVENING_FOLLOWUP_OBS_ID":  os.environ.get("ELASTIC_EVENING_FOLLOWUP_OBS_ID", ""),
    "ELASTIC_MESSAGE_EVAL_OBS_ID":      os.environ.get("ELASTIC_MESSAGE_EVAL_OBS_ID", ""),
}


def connect():
    kwargs = {}
    if ELASTIC_USERNAME and ELASTIC_PASSWORD:
        kwargs["basic_auth"] = (ELASTIC_USERNAME, ELASTIC_PASSWORD)
    return Elasticsearch(f"{ELASTIC_SCHEME}://{ELASTIC_HOST}:{ELASTIC_PORT}", **kwargs)


def main():
    print("=" * 70)
    print(f"  Elasticsearch Diagnostic  —  {ELASTIC_SCHEME}://{ELASTIC_HOST}:{ELASTIC_PORT}")
    print("=" * 70)

    # 1. Connection check
    es = connect()
    try:
        info = es.info()
        print(f"\n✓  Connected  (ES version: {info['version']['number']})")
    except Exception as e:
        print(f"\n✗  Cannot connect to Elasticsearch: {e}")
        print("    Check ELASTIC_HOST / ELASTIC_PORT / ELASTIC_PASSWORD in .env.local")
        sys.exit(1)

    # 2. Index check
    print(f"\n  Index: {INDEX}  (study_id={ELASTIC_STUDY_ID})")
    if not es.indices.exists(index=INDEX):
        print(f"  ✗  Index '{INDEX}' does not exist.")
        print("     → Check ELASTIC_STUDY_ID, or the study manager hasn't stored any data yet.")
        sys.exit(1)

    count = es.count(index=INDEX)["count"]
    print(f"  ✓  Index exists with {count} document(s)")

    # 3. Discover lime-survey observations
    print("\n  Discovering lime-survey-observation documents by observation_id …\n")
    result = es.search(
        index=INDEX,
        query={"term": {"observation_type.keyword": "lime-survey-observation"}},
        aggs={
            "by_obs": {
                "terms": {"field": "observation_id.keyword", "size": 20},
                "aggs": {
                    "sample": {
                        "top_hits": {
                            "size": 1,
                            "_source": {
                                "excludes": ["data_bigfive*", "data_G05Q40*", "data_G07Q33*"]
                            },
                        }
                    }
                },
            }
        },
        size=0,
    )

    buckets = result.get("aggregations", {}).get("by_obs", {}).get("buckets", [])
    if not buckets:
        print("  ✗  No lime-survey-observation documents found.")
        print("     → Make sure participants have submitted at least one LimeSurvey survey.")
        sys.exit(1)

    configured_values = set(CONFIGURED_IDS.values()) - {""}

    for bucket in buckets:
        obs_id = bucket["key"]
        count = bucket["doc_count"]
        hit = bucket["sample"]["hits"]["hits"]
        src = hit[0]["_source"] if hit else {}

        data_fields = sorted([k[5:] for k in src if k.startswith("data_")])  # strip data_
        sample_participant = src.get("participant_id", "—")
        sample_time = src.get("effective_time_frame", "—")

        marker = "✓ configured" if obs_id in configured_values else "← set in .env.local"

        print(f"  observation_id: \"{obs_id}\"  ({count} doc(s))  {marker}")
        print(f"    participant:  {sample_participant}")
        print(f"    time:         {sample_time}")
        print(f"    data fields:  {', '.join(data_fields[:15])}{'…' if len(data_fields) > 15 else ''}")
        print()

    # 4. Show which env vars to set
    print("-" * 70)
    print("  Identify each observation by its data fields:")
    print("    Baseline survey    → has fields like: bigfive[SQ001], age, gender, occupation")
    print("    Daily check-in     → has fields like: Q00[SQ001], Q00[SQ002], G01Q02")
    print("    PA schedule        → has fields like: msgdays[SQ001], msgdays[SQ001comment]")
    print("    Evening follow-up  → has fields like: Q00 (exercised Y/N), G00Q02")
    print("    Message eval       → custom evaluation questions")
    print()
    print("  Set in .env.local:")
    for var, val in CONFIGURED_IDS.items():
        status = f"= \"{val}\"" if val else "= \"?\"  ← FILL IN"
        print(f"    {var} {status}")
    print()
    print("  Then restart the worker and beat containers:")
    print("    docker-compose restart worker beat")
    print("=" * 70)


if __name__ == "__main__":
    main()
