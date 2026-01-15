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
    "run-every-15-minutes": {
        "task": "app.tasks.periodic_task",
        "schedule": 100.0,  # 15 minutes in seconds
    },
    "reset-notifications-at-midnight": {
        "task": "app.tasks.reset_notification_flags",
        "schedule": crontab(hour=0, minute=0),  # Every day at midnight
    },
}
