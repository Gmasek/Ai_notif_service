"""
Self-contained cron for the schedule guard.

Runs two jobs (times in UTC by default — the main app's celery beat runs
update_schedule_fields_task at 21:59 UTC on Sunday):

  BACKUP_CRON   before the updater   (default: Sunday 21:50 UTC)
  RESTORE_CRON  after the updater    (default: Sunday 22:15 UTC)
"""

import os
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from guard import backup_schedules, validate_and_restore

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("schedule_guard.scheduler")

TZ = os.environ.get("TZ", "UTC")
BACKUP_CRON = os.environ.get("BACKUP_CRON", "50 21 * * 0")   # Sun 21:50 UTC
RESTORE_CRON = os.environ.get("RESTORE_CRON", "15 22 * * 0")  # Sun 22:15 UTC


def main():
    sched = BlockingScheduler(timezone=TZ)
    sched.add_job(
        backup_schedules,
        CronTrigger.from_crontab(BACKUP_CRON, timezone=TZ),
        id="backup", name="backup_schedules", misfire_grace_time=3600,
    )
    sched.add_job(
        validate_and_restore,
        CronTrigger.from_crontab(RESTORE_CRON, timezone=TZ),
        id="restore", name="validate_and_restore", misfire_grace_time=3600,
    )
    log.info(
        "schedule-guard started (tz=%s). backup='%s' restore='%s'",
        TZ, BACKUP_CRON, RESTORE_CRON,
    )
    for job in sched.get_jobs():
        log.info("  job '%s' next run: %s", job.id, job.next_run_time)
    sched.start()


if __name__ == "__main__":
    main()
