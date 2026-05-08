from celery import Celery

celery_app = Celery(
    "app", broker="redis://redis:6379/0", backend="redis://redis:6379/0"
)
celery_app.autodiscover_tasks(["app"])

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

# Celery Beat schedule
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    # Every 15 minutes: check notification windows and trigger daily check-in
    "run-every-15-minutes": {
        "task": "app.tasks.periodic_task",
        "schedule": 150.0,
    },
    # Every midnight: reset daily notification flags
    "reset-notifications-at-midnight": {
        "task": "app.tasks.reset_notification_flags",
        "schedule": crontab(hour=22, minute=0),
    },
    # Every 5 minutes: generate and send notifications for completed check-ins
    "check-daily-survey-every-5min": {
        "task": "app.tasks.check_daily_survey_task",
        "schedule": 150.0,
    },
    # Nightly: trigger evening follow-up survey for participants notified today
    "trigger-evening-followup-nightly": {
        "task": "app.tasks.trigger_evening_followup_task",
        "schedule": crontab(hour=17, minute=30),
    },
    # Nightly: collect evening follow-up responses from LimeSurvey
    "fetch-evening-followup-nightly": {
        "task": "app.tasks.fetch_evening_followup_task",
        "schedule": crontab(hour=21, minute=15),
    },
    # Daily: collect message evaluation responses from
    "fetch-message-eval-daily": {
        "task": "app.tasks.fetch_message_eval_task",
        "schedule": crontab(hour=1, minute=0),
    },
    # Every Sunday morning: trigger PA schedule update survey for all participants
    "trigger-schedule-update-survey-sunday-morning": {
        "task": "app.tasks.trigger_schedule_update_survey",
        "schedule": crontab(hour=7, minute=0, day_of_week=0),
    },
    # Every Sunday night: update time_to_notif fields from LimeSurvey responses
    "update-schedule-fields-sunday-night": {
        "task": "app.tasks.update_schedule_fields_task",
        "schedule": crontab(hour=21, minute=59, day_of_week=0),
    },
}
