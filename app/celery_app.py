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
        "schedule": 30.0,
    },
    # Every midnight: reset daily notification flags
    "reset-notifications-at-midnight": {
        "task": "app.tasks.reset_notification_flags",
        "schedule": crontab(hour=0, minute=0),
    },
    # Every 5 minutes: generate and send notifications for completed check-ins
    "check-daily-survey-every-5min": {
        "task": "app.tasks.check_daily_survey_task",
        "schedule": 30.0,
    },
    # Nightly: collect evening follow-up responses from Elasticsearch
    "fetch-evening-followup-nightly": {
        "task": "app.tasks.fetch_evening_followup_task",
        "schedule": crontab(hour=19, minute=30),
    },
    # Daily: collect message evaluation responses from Elasticsearch
    "fetch-message-eval-daily": {
        "task": "app.tasks.fetch_message_eval_task",
        "schedule": crontab(hour=1, minute=0),
    },
}
