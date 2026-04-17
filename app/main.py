import os
from fastapi import FastAPI, Body, Depends
from .tasks import (
    ask_question,
    data_fill_task,
)
from celery.result import AsyncResult
from .celery_app import celery_app
from .db import get_db
from .models import Patient
from sqlalchemy.orm import Session
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Notification Prototype", redirect_slashes=False)


def _patient_dict(p: Patient) -> dict:
    return {
        "id": str(p.id),
        "more_participant_id": p.more_participant_id,
        "name": p.name,
        "group_id": p.group_id,
        "big5": p.big5,
        "hobbies": p.hobbies,
        "tpb": p.tpb,
        "time_to_notif": p.time_to_notif,
        "age": p.age,
        "gender": p.gender,
        "job_type": p.job_type,
        "notif_in_24h": p.notif_in_24h,
        "daily_survey_triggered_at": (
            p.daily_survey_triggered_at.isoformat() if p.daily_survey_triggered_at else None
        ),
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@app.get("/ask")
async def ask():
    task = ask_question.delay()
    return {"task_id": task.id, "status": "submitted"}


@app.post("/db_fill")
async def db_fill(data_in: dict = Body(...)):
    """
    Create or update a participant state record in PostgreSQL.

    Body: {"more_participant_id": 1, "firebase_token": "...", "group_id": 0}
    Profile data (Big Five, demographics, etc.) is read from Elasticsearch.
    """
    data = data_in
    if isinstance(data_in, str):
        try:
            data = json.loads(data_in)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON string: {e}")

    if not isinstance(data, dict):
        raise TypeError("Input must be a dict or JSON string representing a dict")

    task = data_fill_task.delay(data)
    return {"task_id": task.id, "status": "submitted"}


@app.get("/result/{task_id}")
async def get_result(task_id: str):
    result = celery_app.AsyncResult(task_id)
    if result.ready():
        return {"status": "done", "result": result.result}
    return {"status": "pending"}



@app.get("/get_db_ids")
def get_db_ids(db: Session = Depends(get_db)) -> list[str]:
    patients = db.query(Patient.id).all()
    return [str(p.id) for p in patients]


@app.get("/patients")
def get_patients(db: Session = Depends(get_db)) -> list[dict]:
    """Returns all registered participants with their full profile and notification state."""
    patients = db.query(Patient).all()
    return [_patient_dict(p) for p in patients]


@app.get("/lime_info")
def lime_info() -> dict:
    """
    Diagnostic endpoint. Shows participant counts fetched directly from LimeSurvey
    for each configured survey.
    """
    from .lime_fetcher import (
        get_all_baseline_profiles_from_lime,
        get_all_schedules_from_lime,
        get_all_todays_checkins_from_lime,
        LIME_BASELINE_SURVEY_ID,
        LIME_DAILY_CHECKIN_SURVEY_ID,
        LIME_PA_SCHEDULE_SURVEY_ID,
        LIME_EVENING_FOLLOWUP_SURVEY_ID,
        LIME_MESSAGE_EVAL_SURVEY_ID,
    )

    profiles = get_all_baseline_profiles_from_lime()
    schedules = get_all_schedules_from_lime()
    checkins = get_all_todays_checkins_from_lime()
    return {
        "survey_ids": {
            "baseline": LIME_BASELINE_SURVEY_ID,
            "daily_checkin": LIME_DAILY_CHECKIN_SURVEY_ID,
            "pa_schedule": LIME_PA_SCHEDULE_SURVEY_ID,
            "evening_followup": LIME_EVENING_FOLLOWUP_SURVEY_ID,
            "message_eval": LIME_MESSAGE_EVAL_SURVEY_ID,
        },
        "baseline_profiles": len(profiles),
        "pa_schedules": len(schedules),
        "todays_checkins": len(checkins),
        "participant_ids": sorted(profiles.keys()),
    }


@app.get("/patients/{patient_id}")
def get_patient(patient_id: str, db: Session = Depends(get_db)) -> dict:
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        return {"status": "error", "message": f"Patient {patient_id} not found"}
    return _patient_dict(patient)
