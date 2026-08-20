import os
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

SERVER_TZ_OFFSET = timedelta(hours=2)

from .celery_app import celery_app
from .db import SessionLocal
from .models import (
    Patient, NotificationLog, DailyCheckin, GeneratedNotification, NotificationExample,
)

logger = logging.getLogger(__name__)

PAST_NOTIF_CONTEXT_N = int(os.environ.get("PAST_NOTIF_CONTEXT_N", "3"))

MORE_GATEWAY_BASE_URL = os.environ.get(
    "MORE_GATEWAY_BASE_URL", "https://6be5-88-116-37-249.ngrok-free.app"
)
MOMENTARY_ASSESMENT_TOKEN = os.environ.get(
    "MOMENTARY_ASSESMENT_TOKEN",
    "Mi0xLTE=.NzQ3ODQwYjItZWY0NC00MWEzLTg5YWYtZjhjODMzM2NlNDc3",
)
NOTIFICATION_FEEDBACK_TOKEN = os.environ.get(
    "NOTIFACTION_FEEDBACK_TOKEN",
    "Mi0yLTE=.YjJkZjhjMGYtOWU5NC00NjBiLTlkNmYtNWY4NGUwNzM4NTY5",
)
PA_SCHEDULE_UPDATE_TOKEN = os.environ.get("PA_SCHEDULE_UPDATE_TOKEN", "")
EVENING_FOLLOW_UP_TOKEN = os.environ.get("EVENING_FOLLOW_UP_TOKEN", "")
MORE_STUDYMANAGER_BASE_URL = os.environ.get(
    "MORE_STUDYMANAGER_BASE_URL", "http://host.docker.internal:8080"
)
MORE_STUDY_ID = int(os.environ.get("MORE_STUDY_ID", "4"))
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "internal-secret-key")
LOCAL_TZ_NAME = os.environ.get("LOCAL_TZ_NAME", "Europe/Vienna")
LATE_FOLLOWUP_CUTOFF = os.environ.get("LATE_FOLLOWUP_CUTOFF", "19:30")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _select_pipeline(session, more_participant_id: int) -> int:
    """Pick a generation pipeline for this participant, weighted toward an even 1/3
    split using their own history. Counts past NotificationLog.pipeline values and
    favours the least-used pipeline (weight proportional to how far it is behind the
    most-used one), while staying randomised."""
    from .pipelines import ALL_PIPELINES

    counts = {p: 0 for p in ALL_PIPELINES}
    rows = (
        session.query(NotificationLog.pipeline)
        .filter(
            NotificationLog.more_participant_id == more_participant_id,
            NotificationLog.pipeline != None,  # noqa: E711
        )
        .all()
    )
    for (p,) in rows:
        if p in counts:
            counts[p] += 1

    max_count = max(counts.values())
    # weight ∝ (max - count + 1): under-used pipelines get more weight, ties stay equal.
    weights = [max_count - counts[p] + 1 for p in ALL_PIPELINES]
    chosen = random.choices(list(ALL_PIPELINES), weights=weights, k=1)[0]
    logger.info(
        "_select_pipeline: participant %d counts=%s weights=%s chosen=%d",
        more_participant_id, counts, weights, chosen,
    )
    return chosen


def _snapshot_notification_example(session, pd: dict, notif: dict) -> None:
    """Write (or refresh) today's NotificationExample row for a participant, capturing the
    perishable check-in context + generated notification. Idempotent per (participant, day)."""
    more_pid = pd["more_participant_id"]
    today = (datetime.now(timezone.utc).replace(tzinfo=None) + SERVER_TZ_OFFSET).date()
    ctx = pd.get("context_data") or {}

    existing = (
        session.query(NotificationExample)
        .filter(
            NotificationExample.more_participant_id == more_pid,
            NotificationExample.notif_date == today,
        )
        .first()
    )
    if existing:
        return  # one row per participant per day; already snapshotted

    session.add(NotificationExample(
        more_participant_id=more_pid,
        notif_date=today,
        big5=pd.get("big5"),
        pipeline=notif.get("pipeline"),
        notification_text=notif["notification_text"],
        mood_valence=ctx.get("mood_valence"),
        energetic_arousal=ctx.get("energetic_arousal"),
        locus_of_control=ctx.get("locus_of_control"),
        stress=ctx.get("stress"),
        motivation_pa=ctx.get("motivation_pa"),
        barrier_pa=ctx.get("barrier_pa"),
        plans_pa_today=ctx.get("plans_pa_today"),
        pa_scheduled_today=ctx.get("pa_scheduled_today"),
        pa_change_reason=ctx.get("pa_change_reason"),
        events_today=ctx.get("events_today"),
    ))


def is_time_in_window(current_time: str, start_time: str, end_time: str) -> bool:
    """Return True if current_time falls within [start_time, end_time], handles midnight crossing."""
    if not start_time or not end_time:
        return False
    # Normalize to zero-padded HH:MM for correct string comparison ("9:30" → "09:30")
    start_time = start_time.zfill(5)
    end_time = end_time.zfill(5)
    if start_time <= end_time:
        return start_time <= current_time <= end_time
    # midnight-crossing window e.g. 23:00 → 01:00
    return current_time >= start_time or current_time <= end_time


def _send_notification_via_backend(
    participant_id: int, title: str, message: str
) -> dict:
    """POST to the studymanager internal endpoint to send a notification via FCM and log it in the notification center."""
    import requests as _requests

    url = f"{MORE_STUDYMANAGER_BASE_URL}/api/v1/internal/notifications"
    try:
        resp = _requests.post(
            url,
            json={
                "studyId": MORE_STUDY_ID,
                "participantId": participant_id,
                "title": title,
                "message": message,
            },
            headers={"Content-Type": "application/json", "X-Api-Key": INTERNAL_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        return {"status": "success", "http_status": resp.status_code}
    except Exception as exc:
        logger.error(
            "_send_notification_via_backend failed for participant %d: %s",
            participant_id,
            exc,
        )
        return {"status": "error", "message": str(exc)}


def _trigger_more_assessment(participant_ids: list, more_token: str) -> dict:
    """POST to the MORE Gateway to trigger an in-app assessment for the given participant IDs."""
    import requests as _requests

    url = f"{MORE_GATEWAY_BASE_URL}/api/v1/trigger/external"
    try:
        resp = _requests.post(
            url,
            json={"participantIds": participant_ids},
            headers={
                "Content-Type": "application/json",
                "More-Api-Token": more_token,
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(
            "_trigger_more_assessment: triggered %d participant(s), status=%s",
            len(participant_ids),
            resp.status_code,
        )
        return {"status": "success", "http_status": resp.status_code}
    except Exception as exc:
        logger.error("_trigger_more_assessment failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def _mark_evening_followup_triggered(session, participant_ids: list) -> None:
    """Stamp evening_followup_triggered_at so the follow-up is not re-sent tonight.
    Cleared by reset_notification_flags at the daily reset."""
    session.query(Patient).filter(
        Patient.more_participant_id.in_(participant_ids)
    ).update(
        {Patient.evening_followup_triggered_at: datetime.now(timezone.utc)},
        synchronize_session=False,
    )
    session.commit()


# ---------------------------------------------------------------------------
# Scheduled tasks
# ---------------------------------------------------------------------------


@celery_app.task(name="app.tasks.reset_notification_flags")
def reset_notification_flags():
    """Runs every midnight — resets notif_in_24h and daily_survey_triggered_at for all patients."""
    session = SessionLocal()
    try:
        updated_count = (
            session.query(Patient)
            .update(
                {
                    Patient.notif_in_24h: False,
                    Patient.daily_survey_triggered_at: None,
                    Patient.evening_followup_triggered_at: None,
                }
            )
        )
        session.query(DailyCheckin).delete()
        session.query(GeneratedNotification).delete()
        session.commit()
        return {"status": "success", "updated_count": updated_count}
    except Exception as e:
        session.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        session.close()


@celery_app.task(name="app.tasks.periodic_task")
def periodic_task():
    """
    Runs every 15 minutes.

    Scans LimeSurvey directly for all participants who have completed the
    baseline survey (bypasses Elasticsearch, which is unreliable due to the
    endUrl callback bug). For each one:
      - Creates a Patient row if it doesn't exist yet, populating the full
        profile (Big Five, TPB, demographics, hobbies, notification schedule)
        from LimeSurvey so Postgres is the single source of truth.
      - Backfills profile fields for existing rows that are missing them.
    """
    from .lime_fetcher import (
        get_all_baseline_profiles_from_lime,
        get_all_schedules_from_lime,
    )

    profiles = get_all_baseline_profiles_from_lime()
    schedules = get_all_schedules_from_lime()
    participant_ids = list(profiles.keys())
    logger.info(
        "periodic_task: %d participant(s) with baseline found in LimeSurvey",
        len(participant_ids),
    )

    session = SessionLocal()
    upserted = 0
    try:
        for pid in participant_ids:
            profile = profiles.get(pid)
            schedule = schedules.get(pid)
            patient = (
                session.query(Patient)
                .filter(Patient.more_participant_id == pid)
                .first()
            )
            if not patient:
                session.add(
                    Patient(
                        more_participant_id=pid,
                        name=(profile or {}).get("name") or f"participant_{pid}",
                        group_id=pid % 3,
                        big5=(profile or {}).get("big5"),
                        hobbies=(profile or {}).get("hobbies"),
                        tpb=(profile or {}).get("tpb"),
                        age=(profile or {}).get("age"),
                        gender=(profile or {}).get("gender"),
                        job_type=(profile or {}).get("job_type"),
                        time_to_notif=schedule or (profile or {}).get("time_to_notif"),
                    )
                )
                upserted += 1
            elif patient.big5 is None:
                # Backfill profile for rows created before LimeSurvey data was available
                if profile:
                    patient.name = profile.get("name") or patient.name
                    patient.big5 = profile.get("big5")
                    patient.hobbies = profile.get("hobbies")
                    patient.tpb = profile.get("tpb")
                    patient.age = profile.get("age")
                    patient.gender = profile.get("gender")
                    patient.job_type = profile.get("job_type")
                fallback_schedule = schedule or (profile or {}).get("time_to_notif")
                if fallback_schedule and patient.time_to_notif is None:
                    patient.time_to_notif = fallback_schedule

        session.commit()
        logger.info("periodic_task: %d new participant(s) added to DB", upserted)
        return {
            "status": "success",
            "found": len(participant_ids),
            "upserted": upserted,
        }
    except Exception as e:
        session.rollback()
        logger.error("periodic_task: DB upsert failed: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        session.close()


@celery_app.task(name="app.tasks.get_momentary_assessment_task")
def get_momentary_assessment_task():
    """
    Runs every 5 minutes.

    Fetches today's check-ins from LimeSurvey and:
      - Upserts check-in data to DailyCheckin for participants who responded.
      - For participants without check-in data: triggers the momentary assessment
        survey if inside their notification window and not yet triggered today.
    """
    from .lime_fetcher import get_all_todays_checkins_from_lime

    local_now = datetime.now(timezone.utc).replace(tzinfo=None) + SERVER_TZ_OFFSET
    current_time = local_now.strftime("%H:%M")
    current_day = local_now.strftime("%A").lower()

    todays_checkins = get_all_todays_checkins_from_lime()
    logger.info(
        "get_momentary_assessment_task: %d participant(s) with check-in data today",
        len(todays_checkins),
    )

    session = SessionLocal()
    try:
        pending = (
            session.query(Patient)
            .filter(
                Patient.notif_in_24h == False,
                Patient.more_participant_id != None,
            )
            .all()
        )

        if not pending:
            return {"status": "success", "triggered": 0}

        triggered = 0
        for patient in pending:
            context_data = todays_checkins.get(patient.more_participant_id)

            if context_data is not None:
                # Upsert check-in to local DB so send_notifications_task can read it.
                existing = (
                    session.query(DailyCheckin)
                    .filter(DailyCheckin.more_participant_id == patient.more_participant_id)
                    .first()
                )
                if existing:
                    existing.checkin_data = context_data
                else:
                    session.add(DailyCheckin(
                        more_participant_id=patient.more_participant_id,
                        checkin_data=context_data,
                    ))
            else:
                # No check-in yet — gate on time window before triggering survey.
                if patient.time_to_notif:
                    window = patient.time_to_notif.get(current_day, {})
                    if not is_time_in_window(
                        current_time, window.get("start"), window.get("end")
                    ):
                        logger.info(
                            "get_momentary_assessment_task: participant %d outside window on %s at %s",
                            patient.more_participant_id,
                            current_day,
                            current_time,
                        )
                        continue

                if patient.daily_survey_triggered_at is None:
                    trigger_result = _trigger_more_assessment(
                        [patient.more_participant_id], MOMENTARY_ASSESMENT_TOKEN
                    )
                    if trigger_result.get("status") == "success":
                        patient.daily_survey_triggered_at = datetime.now(timezone.utc).replace(tzinfo=None)
                        triggered += 1
                        logger.info(
                            "get_momentary_assessment_task: triggered check-in for participant %d",
                            patient.more_participant_id,
                        )
                    else:
                        logger.warning(
                            "get_momentary_assessment_task: failed to trigger check-in for participant %d: %s",
                            patient.more_participant_id,
                            trigger_result.get("message"),
                        )
                else:
                    logger.info(
                        "get_momentary_assessment_task: no check-in yet for participant %d",
                        patient.more_participant_id,
                    )

        session.commit()
        return {"status": "success", "triggered": triggered}

    except Exception as e:
        session.rollback()
        logger.error("get_momentary_assessment_task failed: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        session.close()


@celery_app.task(name="app.tasks.send_notifications_task")
def send_notifications_task():
    """
    Runs every 5 minutes.

    For participants with check-in data in DailyCheckin and notif_in_24h=False:
      - Reuses stored GeneratedNotification if available (avoids re-calling LLM on retry).
      - Generates a personalised notification via LLM for new participants.
      - Sends via studymanager, logs, triggers message-evaluation, marks notif_in_24h=True.
    """
    from .pipelines import generate_notifications_for_patients

    session = SessionLocal()
    try:
        rows = (
            session.query(DailyCheckin, Patient)
            .join(Patient, DailyCheckin.more_participant_id == Patient.more_participant_id)
            .filter(Patient.notif_in_24h == False)
            .all()
        )

        if not rows:
            return {"status": "success", "notified": 0}

        patient_dicts = []
        for checkin, patient in rows:
            past_logs = (
                session.query(NotificationLog)
                .filter(NotificationLog.more_participant_id == patient.more_participant_id)
                .order_by(NotificationLog.sent_at.desc())
                .limit(PAST_NOTIF_CONTEXT_N)
                .all()
            )
            entry = {
                "id": str(patient.id),
                "more_participant_id": patient.more_participant_id,
                "group_id": patient.group_id,
                "pipeline": _select_pipeline(session, patient.more_participant_id),
                "name": patient.name or f"participant_{patient.more_participant_id}",
                "big5": patient.big5,
                "hobbies": patient.hobbies,
                "tpb": patient.tpb,
                "age": patient.age,
                "gender": patient.gender,
                "job_type": patient.job_type,
                "context_data": checkin.checkin_data,
                "past_notifications": [
                    {"text": log.notification_text, "feedback_raw": log.feedback_raw}
                    for log in past_logs
                ],
            }
            logger.info(
                "send_notifications_task: patient_dict for participant %d: %s",
                patient.more_participant_id,
                entry,
            )
            patient_dicts.append(entry)

        # Split: already generated vs needing LLM call.
        ready_notifications = []
        patients_needing_generation = []
        for pd_entry in patient_dicts:
            stored = (
                session.query(GeneratedNotification)
                .filter(GeneratedNotification.more_participant_id == pd_entry["more_participant_id"])
                .first()
            )
            if stored:
                logger.info(
                    "send_notifications_task: reusing stored notification for participant %d (status=%s)",
                    pd_entry["more_participant_id"],
                    stored.send_status,
                )
                ready_notifications.append({
                    "patient_id": pd_entry["id"],
                    "notification_text": stored.notification_text,
                    "group_id": stored.group_id,
                    "pipeline": stored.pipeline,
                })
            else:
                patients_needing_generation.append(pd_entry)

        if patients_needing_generation:
            new_notifications = generate_notifications_for_patients(
                patients=patients_needing_generation,
                include_big5=True,
            )
            for notif in new_notifications:
                pd_match = next(
                    (p for p in patients_needing_generation if p["id"] == notif["patient_id"]), None
                )
                if pd_match:
                    session.add(GeneratedNotification(
                        more_participant_id=pd_match["more_participant_id"],
                        notification_text=notif["notification_text"],
                        group_id=notif.get("group_id"),
                        pipeline=notif.get("pipeline"),
                        send_status="pending",
                    ))
            session.commit()
            ready_notifications.extend(new_notifications)

        notified = 0
        for notif in ready_notifications:
            patient_id = notif["patient_id"]
            pd = next((p for p in patient_dicts if p["id"] == patient_id), None)
            more_pid = pd["more_participant_id"] if pd else None

            if more_pid is None:
                logger.warning("No more_participant_id for patient %s, skipping", patient_id)
                continue

            send_result = _send_notification_via_backend(
                participant_id=more_pid,
                title="Time to Move!",
                message=notif["notification_text"],
            )

            stored_notif = (
                session.query(GeneratedNotification)
                .filter(GeneratedNotification.more_participant_id == more_pid)
                .first()
            )

            if send_result.get("status") != "success":
                logger.warning(
                    "Backend notification send failed for participant %d: %s",
                    more_pid,
                    send_result.get("message"),
                )
                if stored_notif:
                    stored_notif.send_status = "failed"
                session.commit()
                continue

            if stored_notif:
                stored_notif.send_status = "sent"
                stored_notif.sent_at = datetime.now(timezone.utc).replace(tzinfo=None)

            session.add(NotificationLog(
                more_participant_id=more_pid,
                notification_text=notif["notification_text"],
                big5_used=bool(pd["big5"] if pd else None),
                group_id=notif.get("group_id"),
                pipeline=notif.get("pipeline"),
            ))

            # Snapshot the example row now, while the perishable check-in context is still
            # available (DailyCheckin is wiped at the 02:00 reset). Feedback grade + execution
            # outcome are filled in later by enrich_notification_examples_task.
            if pd:
                _snapshot_notification_example(session, pd, notif)

            patient_obj = session.query(Patient).filter(Patient.id == patient_id).first()
            if patient_obj:
                patient_obj.notif_in_24h = True
                if patient_obj.more_participant_id:
                    eval_result = _trigger_more_assessment(
                        [patient_obj.more_participant_id], NOTIFICATION_FEEDBACK_TOKEN
                    )
                    if eval_result.get("status") != "success":
                        logger.warning(
                            "MORE eval trigger failed for participant %d: %s",
                            patient_obj.more_participant_id,
                            eval_result.get("message"),
                        )

            notified += 1
            logger.info("Notification sent via backend & logged for participant %d", more_pid)

        session.commit()
        return {"status": "success", "notified": notified}

    except Exception as e:
        session.rollback()
        logger.error("send_notifications_task failed: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        session.close()


@celery_app.task(name="app.tasks.trigger_evening_followup_task")
def trigger_evening_followup_task():
    """
    Runs nightly at 22:00.
    Triggers the evening follow-up survey via the MORE Gateway for all participants
    who received a notification today (notif_in_24h=True).
    """
    session = SessionLocal()
    try:
        participant_ids = [
            p.more_participant_id
            for p in session.query(Patient)
            .filter(Patient.notif_in_24h == True, Patient.more_participant_id != None)
            .all()
        ]

        if not participant_ids:
            logger.info("trigger_evening_followup_task: no notified participants today")
            return {"status": "success", "triggered": 0}

        result = _trigger_more_assessment(participant_ids, EVENING_FOLLOW_UP_TOKEN)
        if result.get("status") == "success":
            _mark_evening_followup_triggered(session, participant_ids)
        logger.info(
            "trigger_evening_followup_task: triggered %d participant(s)",
            len(participant_ids),
        )
        return {"status": result.get("status"), "triggered": len(participant_ids)}
    finally:
        session.close()


@celery_app.task(name="app.tasks.fetch_evening_followup_task")
def fetch_evening_followup_task():
    """
    Runs nightly at 19:30.
    Fetches today's evening follow-up survey responses from LimeSurvey and persists them.
    """
    from .lime_fetcher import get_recent_evening_followups_from_lime
    from .models import EveningFollowupResponse

    try:
        records = get_recent_evening_followups_from_lime(since_hours=24)
        logger.info("fetch_evening_followup_task: %d responses", len(records))
    except Exception as e:
        logger.error("fetch_evening_followup_task failed: %s", e)
        return {"status": "error", "message": str(e)}

    session = SessionLocal()
    try:
        for r in records:
            pid_str = r.get("participant_id") or ""
            pid = (
                int(pid_str.replace("participant_", ""))
                if pid_str.startswith("participant_")
                else None
            )
            submitdate = None
            if r.get("submitdate"):
                try:
                    submitdate = datetime.strptime(
                        r["submitdate"], "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            session.add(
                EveningFollowupResponse(
                    more_participant_id=pid,
                    submitdate=submitdate,
                    exercised=r.get("exercised"),
                    activity=r.get("activity"),
                    duration=r.get("duration"),
                    when_exercised=r.get("when"),
                    other_activity=r.get("other_activity"),
                    other_activity_desc=r.get("other_activity_desc"),
                    other_duration=r.get("other_duration"),
                )
            )
        session.commit()
        logger.info("fetch_evening_followup_task: saved %d record(s)", len(records))
    except Exception as e:
        session.rollback()
        logger.error("fetch_evening_followup_task: db write failed: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        session.close()

    return {"status": "success", "count": len(records)}


@celery_app.task(name="app.tasks.fetch_message_eval_task")
def fetch_message_eval_task():
    """
    Runs daily every morning.
    Fetches notification message evaluation responses from LimeSurvey and writes
    the raw survey data to the most recent unmatched NotificationLog entry for
    each participant so it can be used as context in future generation.
    """
    from .lime_fetcher import get_recent_message_evals_from_lime

    try:
        records = get_recent_message_evals_from_lime(since_hours=48)
        logger.info("fetch_message_eval_task: %d responses", len(records))
    except Exception as e:
        logger.error("fetch_message_eval_task failed: %s", e)
        return {"status": "error", "message": str(e)}

    session = SessionLocal()
    matched = 0
    try:
        for record in records:
            pid_str = record.get("participant_id") or ""
            pid = (
                int(pid_str.replace("participant_", ""))
                if pid_str.startswith("participant_")
                else None
            )
            if pid is None or not record.get("data"):
                continue

            log_entry = (
                session.query(NotificationLog)
                .filter(
                    NotificationLog.more_participant_id == pid,
                    NotificationLog.feedback_raw == None,  # noqa: E711
                )
                .order_by(NotificationLog.sent_at.desc())
                .first()
            )
            if log_entry:
                log_entry.feedback_raw = record["data"]
                matched += 1
                logger.info(
                    "fetch_message_eval_task: matched feedback for participant %d (log id=%s)",
                    pid,
                    log_entry.id,
                )
            else:
                logger.info(
                    "fetch_message_eval_task: no unmatched notification log for participant %d",
                    pid,
                )

        session.commit()
        logger.info("fetch_message_eval_task: %d feedback(s) persisted", matched)
        return {"status": "success", "fetched": len(records), "matched": matched}
    except Exception as e:
        session.rollback()
        logger.error("fetch_message_eval_task: db write failed: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        session.close()


@celery_app.task(name="app.tasks.enrich_notification_examples_task")
def enrich_notification_examples_task():
    """
    Runs nightly (~01:30, after message-eval feedback is fetched).

    Fills in the enrichment fields on notification_examples rows snapshotted at send time:
      - feedback grade/score from the matching NotificationLog.feedback_raw
      - executed/activity from the matching evening follow-up response
    Re-processes recent rows so late-arriving feedback backfills without duplication.
    Only reads persistent tables, so it is independent of the 02:00 daily reset.
    """
    from sqlalchemy import func
    from .pipelines import _classify_feedback, _feedback_score
    from .models import EveningFollowupResponse

    lookback_start = (
        datetime.now(timezone.utc).replace(tzinfo=None) + SERVER_TZ_OFFSET
    ).date() - timedelta(days=4)

    session = SessionLocal()
    enriched = 0
    try:
        rows = (
            session.query(NotificationExample)
            .filter(
                NotificationExample.notif_date >= lookback_start,
                (NotificationExample.enriched_at == None)  # noqa: E711
                | (NotificationExample.feedback_grade == None),  # noqa: E711
            )
            .all()
        )

        for ex in rows:
            log_entry = (
                session.query(NotificationLog)
                .filter(
                    NotificationLog.more_participant_id == ex.more_participant_id,
                    func.date(NotificationLog.sent_at) == ex.notif_date,
                    NotificationLog.feedback_raw != None,  # noqa: E711
                )
                .order_by(NotificationLog.sent_at.desc())
                .first()
            )
            if log_entry:
                ex.feedback_raw = log_entry.feedback_raw
                ex.feedback_grade = _classify_feedback(log_entry.feedback_raw)
                ex.feedback_score = _feedback_score(log_entry.feedback_raw)

            followup = (
                session.query(EveningFollowupResponse)
                .filter(
                    EveningFollowupResponse.more_participant_id == ex.more_participant_id,
                    func.date(EveningFollowupResponse.submitdate) == ex.notif_date,
                )
                .order_by(EveningFollowupResponse.submitdate.desc())
                .first()
            )
            if followup:
                ex.executed = followup.exercised
                ex.execution_activity = followup.activity

            ex.enriched_at = datetime.now(timezone.utc).replace(tzinfo=None)
            enriched += 1

        session.commit()
        logger.info("enrich_notification_examples_task: %d example(s) enriched", enriched)
        return {"status": "success", "enriched": enriched}
    except Exception as e:
        session.rollback()
        logger.error("enrich_notification_examples_task failed: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        session.close()


@celery_app.task(name="app.tasks.trigger_schedule_update_survey")
def trigger_schedule_update_survey():
    """
    Runs every Sunday morning at 09:00.
    Triggers the PA schedule update survey via the MORE Gateway for all active participants.
    """
    session = SessionLocal()
    try:
        participant_ids = [
            p.more_participant_id
            for p in session.query(Patient)
            .filter(Patient.more_participant_id != None)
            .all()
        ]
    finally:
        session.close()

    if not participant_ids:
        logger.info("trigger_schedule_update_survey: no participants found")
        return {"status": "success", "triggered": 0}

    result = _trigger_more_assessment(participant_ids, PA_SCHEDULE_UPDATE_TOKEN)
    logger.info(
        "trigger_schedule_update_survey: triggered %d participant(s)",
        len(participant_ids),
    )
    return {"status": result.get("status"), "triggered": len(participant_ids)}


@celery_app.task(name="app.tasks.update_schedule_fields_task")
def update_schedule_fields_task():
    """
    Runs every Sunday at 23:59.
    Fetches the latest PA schedules from LimeSurvey and updates time_to_notif
    for all matching participants in the patients table.
    """
    from .lime_fetcher import get_all_schedules_from_lime

    schedules = get_all_schedules_from_lime()
    logger.info(
        "update_schedule_fields_task: %d schedule(s) fetched from LimeSurvey",
        len(schedules),
    )

    session = SessionLocal()
    updated = 0
    try:
        for pid, schedule in schedules.items():
            if not schedule:
                continue
            patient = (
                session.query(Patient)
                .filter(Patient.more_participant_id == pid)
                .first()
            )
            if patient:
                patient.time_to_notif = schedule
                updated += 1
        session.commit()
        logger.info("update_schedule_fields_task: %d patient(s) updated", updated)
        return {"status": "success", "updated": updated}
    except Exception as e:
        session.rollback()
        logger.error("update_schedule_fields_task failed: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        session.close()


@celery_app.task(name="app.tasks.trigger_late_evening_followup_task")
def trigger_late_evening_followup_task():
    """
    Runs every 20 min from 20:00, after the regular evening follow-up.
    Participants whose notification window ends after LATE_FOLLOWUP_CUTOFF can be
    notified *after* trigger_evening_followup_task has already run, so their survey
    would ask about a nudge they had not received yet. This re-triggers the follow-up
    for those participants — but only if a notification was actually generated for
    them today past the cutoff, and only once per day (evening_followup_triggered_at,
    cleared by reset_notification_flags).
    """
    from sqlalchemy import text

    session = SessionLocal()
    try:
        rows = session.execute(
            text(
                """
                WITH today AS (
                    SELECT lower(to_char(now() AT TIME ZONE :tz, 'FMDay')) AS weekday,
                           (now() AT TIME ZONE :tz)::date AS local_date
                )
                SELECT DISTINCT gn.more_participant_id
                FROM generated_notifications gn
                JOIN patients p ON p.more_participant_id = gn.more_participant_id
                CROSS JOIN today t
                WHERE p.evening_followup_triggered_at IS NULL
                  AND jsonb_typeof(p.time_to_notif) = 'object'
                  AND (p.time_to_notif -> t.weekday ->> 'end') IS NOT NULL
                  AND (p.time_to_notif -> t.weekday ->> 'end')::time > CAST(:cutoff AS time)
                  AND (gn.generated_at AT TIME ZONE :tz)::date = t.local_date
                  AND (gn.generated_at AT TIME ZONE :tz)::time > CAST(:cutoff AS time)
                  AND gn.more_participant_id IS NOT NULL
                """
            ),
            {"tz": LOCAL_TZ_NAME, "cutoff": LATE_FOLLOWUP_CUTOFF},
        ).all()
        participant_ids = [r[0] for r in rows]

        if not participant_ids:
            logger.info(
                "trigger_late_evening_followup_task: nobody pending past %s",
                LATE_FOLLOWUP_CUTOFF,
            )
            return {"status": "success", "triggered": 0}

        result = _trigger_more_assessment(participant_ids, EVENING_FOLLOW_UP_TOKEN)
        if result.get("status") != "success":
            # Leave the flag unset so the next tick retries.
            logger.error(
                "trigger_late_evening_followup_task: trigger failed for %s, will retry",
                participant_ids,
            )
            return {"status": "error", "message": result.get("message")}

        _mark_evening_followup_triggered(session, participant_ids)
        logger.info(
            "trigger_late_evening_followup_task: triggered %d participant(s) past %s: %s",
            len(participant_ids),
            LATE_FOLLOWUP_CUTOFF,
            participant_ids,
        )
        return {"status": "success", "triggered": len(participant_ids)}
    except Exception as e:
        session.rollback()
        logger.error("trigger_late_evening_followup_task failed: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        session.close()
