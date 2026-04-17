"""
Elasticsearch client for the AI notification prototype.

Connects to the same Elasticsearch instance used by the MORE Data Gateway.
The study data lives in the index study_{ELASTIC_STUDY_ID}.
"""
import os
from elasticsearch import Elasticsearch

ELASTIC_HOST = os.environ.get("ELASTIC_HOST", "localhost")
ELASTIC_PORT = int(os.environ.get("ELASTIC_PORT", 9200))
ELASTIC_USERNAME = os.environ.get("ELASTIC_USERNAME", "elastic")
ELASTIC_PASSWORD = os.environ.get("ELASTIC_PASSWORD", "")
ELASTIC_SCHEME = os.environ.get("ELASTIC_SCHEME", "http")
ELASTIC_STUDY_ID = os.environ.get("ELASTIC_STUDY_ID", "1")


def get_elastic_client() -> Elasticsearch:
    kwargs = {}
    if ELASTIC_USERNAME and ELASTIC_PASSWORD:
        kwargs["basic_auth"] = (ELASTIC_USERNAME, ELASTIC_PASSWORD)
    return Elasticsearch(
        f"{ELASTIC_SCHEME}://{ELASTIC_HOST}:{ELASTIC_PORT}",
        **kwargs,
    )


def get_index_name() -> str:
    return f"study_{ELASTIC_STUDY_ID}"
