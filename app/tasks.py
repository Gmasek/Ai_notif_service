from .celery_app import celery_app
from .pipelines import (
    generateNotif_gpt,
    generate_notifications_for_patients,
    send_notifications_via_firebase,
)
from .db import SessionLocal
from .models import Patient
from celery import shared_task


@celery_app.task(name="app.tasks.ask_question")
def ask_question():
    # Call the Haystack pipeline
    result = generateNotif_gpt()
    return result


@celery_app.task(name="app.tasks.reset_notification_flags")
def reset_notification_flags():
    """Task that runs every midnight to reset the notif_in_24h flag for all patients."""
    session = SessionLocal()
    try:
        # Reset the flag for all patients
        updated_count = (
            session.query(Patient)
            .filter(Patient.notif_in_24h == True)
            .update({Patient.notif_in_24h: False})
        )
        session.commit()

        return {
            "status": "success",
            "message": f"Reset notification flags for {updated_count} patients",
            "updated_count": updated_count,
        }
    except Exception as e:
        session.rollback()
        return {
            "status": "error",
            "message": f"Failed to reset notification flags: {str(e)}",
        }
    finally:
        session.close()


def is_time_in_window(current_time: str, start_time: str, end_time: str) -> bool:
    """
    Check if current_time is within the time window.
    Handles midnight crossing (e.g., 23:00 to 01:00).

    Args:
        current_time: Current time in HH:MM format
        start_time: Window start time in HH:MM format
        end_time: Window end time in HH:MM format

    Returns:
        True if current_time is within the window
    """
    if not start_time or not end_time:
        return False

    if start_time <= end_time:
        # Normal case: e.g., 08:00 to 18:00
        return start_time <= current_time <= end_time
    else:
        # Midnight crossing: e.g., 23:00 to 01:00
        return current_time >= start_time or current_time <= end_time


@celery_app.task(name="app.tasks.periodic_task")
def periodic_task():
    """Task that runs every 15 minutes to check for patients needing notifications."""
    from datetime import datetime

    # Get current day and time
    now = datetime.now()
    current_day = now.strftime("%A").lower()  # e.g., "monday"
    current_time = now.strftime("%H:%M")  # e.g., "14:30"

    print(f"Current day: {current_day}, Current time: {current_time}")

    session = SessionLocal()
    try:
        # Get all patients who haven't received a notification in the last 24 hours
        all_patients = (
            session.query(Patient).filter(Patient.notif_in_24h == False).all()
        )

        print(f"Found {len(all_patients)} patients with notif_in_24h=False")

        matching_patients = []
        for patient in all_patients:
            print(
                f"Checking patient {patient.name}, time_to_notif: {patient.time_to_notif}"
            )
            if patient.time_to_notif:
                # time_to_notif format: {"monday": {"start": "08:00", "end": "16:00"}, ...}
                day_config = patient.time_to_notif.get(current_day)
                print(f"  Day config for {current_day}: {day_config}")
                if day_config:
                    start_time = day_config.get("start")
                    end_time = day_config.get("end")
                    # Check if current time is within the time window
                    is_match = is_time_in_window(current_time, start_time, end_time)
                    print(
                        f"  Time window: {start_time} - {end_time}, Current time: {current_time}, Match: {is_match}"
                    )
                    if is_match:
                        matching_patients.append(
                            {
                                "id": str(patient.id),
                                "name": patient.name,
                                "time_to_notif": patient.time_to_notif,
                                "firebase_token": patient.firebase_token,
                                "big5": patient.big5,
                                "hobbies": patient.hobbies,
                                "tpb": patient.tpb,
                                "group_id": patient.group_id,
                                "age": patient.age,
                                "gender": patient.gender,
                                "job_type": patient.job_type,
                                "notif_in_24h": patient.notif_in_24h,
                                "created_at": (
                                    patient.created_at.isoformat()
                                    if patient.created_at
                                    else None
                                ),
                            }
                        )
        # TODO put in randomisation with hard cutoff before the end time
        # Generate notifications for matching patients
        notif = generate_notifications_for_patients(matching_patients)
        print(notif)

        # Send notifications via Firebase and track which ones succeeded
        successfully_notified = []
        if notif:
            firebase_result = send_notifications_via_firebase(notif)
            print(f"Firebase sending result: {firebase_result}")

            # Only mark patients as notified if Firebase send succeeded
            if firebase_result.get("status") == "complete" and firebase_result.get("results"):
                for i, result in enumerate(firebase_result["results"]):
                    if isinstance(result, dict) and result.get("status") == "success":
                        successfully_notified.append(notif[i]["patient_id"])

        # Mark only successfully notified patients
        for patient_id in successfully_notified:
            set_notification_sent(patient_id)

        return {
            "current_day": current_day,
            "current_time": current_time,
            "matching_patients_count": len(matching_patients),
            "successfully_notified_count": len(successfully_notified),
            "matching_patients": matching_patients,
        }
    finally:
        session.close()


from pydantic import BaseModel, Field, conint
from typing import List, Optional, Dict
from uuid import UUID
from datetime import datetime


class PatientCreate(BaseModel):
    name: str = Field(..., example="Alice")

    time_to_notif: Optional[Dict[str, Dict[str, str]]]  # e.g., {"Monday": "08:00"}
    firebase_token: Optional[str]

    big5: Optional[
        Dict[str, float]
    ]  # e.g., {"openness": 0.8, "conscientiousness": 0.7}
    hobbies: Optional[List[str]]
    tpb: Optional[Dict[str, float]]

    group_id: Optional[int]

    age: Optional[conint(ge=0)]  # ensures age >= 0
    gender: Optional[str]
    job_type: Optional[str]

    notif_in_24h: Optional[bool] = False


@shared_task
def data_fill_task(data):
    return data_fill(data=data)


def data_fill(data: dict):
    # Validate and parse input using Pydantic
    patient_input = PatientCreate(**data)

    # Create the ORM Patient object
    db_patient = Patient(
        name=patient_input.name,
        time_to_notif=patient_input.time_to_notif,
        firebase_token=patient_input.firebase_token,
        big5=patient_input.big5,
        hobbies=patient_input.hobbies,
        tpb=patient_input.tpb,
        group_id=patient_input.group_id,
        age=patient_input.age,
        gender=patient_input.gender,
        job_type=patient_input.job_type,
        notif_in_24h=patient_input.notif_in_24h,
    )

    session = SessionLocal()
    try:
        # Add and commit to DB
        session.add(db_patient)
        session.commit()
        session.refresh(db_patient)

        # Extract data into a dict before closing session
        result = {
            "id": str(db_patient.id),  # convert UUID to string
            "name": db_patient.name,
            "time_to_notif": db_patient.time_to_notif,
            "firebase_token": db_patient.firebase_token,
            "big5": db_patient.big5,
            "hobbies": db_patient.hobbies,
            "tpb": db_patient.tpb,
            "group_id": db_patient.group_id,
            "age": db_patient.age,
            "gender": db_patient.gender,
            "job_type": db_patient.job_type,
            "notif_in_24h": db_patient.notif_in_24h,
            "created_at": (
                db_patient.created_at.isoformat() if db_patient.created_at else None
            ),
        }
        return result
    finally:
        session.close()


def set_notification_sent(patient_id: str):
    """
    Set the notif_in_24h flag to True for a patient.

    Args:
        patient_id: UUID string of the patient

    Returns:
        True if successful, False otherwise
    """
    session = SessionLocal()
    try:
        patient = session.query(Patient).filter(Patient.id == patient_id).first()
        if patient:
            patient.notif_in_24h = True
            session.commit()
            return True
        return False
    finally:
        session.close()


@shared_task
def send_firebase_notification_task(
    patient_id: str, title: str, body: str, data: dict = None
):
    """
    Send a Firebase notification to a specific patient.

    Args:
        patient_id: UUID string of the patient
        title: Notification title
        body: Notification body text
        data: Optional dictionary of custom data

    Returns:
        Dict with status and message
    """
    session = SessionLocal()
    try:
        patient = session.query(Patient).filter(Patient.id == patient_id).first()
        if not patient:
            return {
                "status": "error",
                "message": f"Patient with id {patient_id} not found",
            }

        if not patient.firebase_token:
            return {
                "status": "error",
                "message": f"Patient {patient.name} has no Firebase token",
            }

        # Send notification
        from .firebase_messaging import send_push_notification

        result = send_push_notification(
            token=patient.firebase_token, title=title, body=body, data=data or {}
        )

        return {
            **result,
            "patient_id": str(patient.id),
            "patient_name": patient.name,
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to send notification: {str(e)}",
        }
    finally:
        session.close()


@shared_task
def update_firebase_token_task(patient_id: str, firebase_token: str):
    """
    Update the Firebase token for a patient.

    Args:
        patient_id: UUID string of the patient
        firebase_token: New Firebase token to set

    Returns:
        Dict with status and message
    """
    return update_firebase_token(patient_id, firebase_token)


def update_firebase_token(patient_id: str, firebase_token: str):
    """
    Update the Firebase token for a patient.

    Args:
        patient_id: UUID string of the patient
        firebase_token: New Firebase token to set

    Returns:
        Dict with status, message, and updated patient info
    """
    session = SessionLocal()
    try:
        patient = session.query(Patient).filter(Patient.id == patient_id).first()
        if not patient:
            return {
                "status": "error",
                "message": f"Patient with id {patient_id} not found",
            }

        # Update the Firebase token
        patient.firebase_token = firebase_token
        session.commit()
        session.refresh(patient)

        return {
            "status": "success",
            "message": f"Firebase token updated for patient {patient.name}",
            "patient_id": str(patient.id),
            "patient_name": patient.name,
            "firebase_token": patient.firebase_token,
        }
    except Exception as e:
        session.rollback()
        return {
            "status": "error",
            "message": f"Failed to update Firebase token: {str(e)}",
        }
    finally:
        session.close()
