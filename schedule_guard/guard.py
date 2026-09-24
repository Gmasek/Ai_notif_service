"""
Schedule safety net for the MORE notification pipeline.

Standalone from the main app: it only reads/writes ``patients.time_to_notif`` in
the shared Postgres and keeps its own JSON backup on a mounted volume, so it
requires no schema changes and can be deployed / redeployed on its own.

Flow (see scheduler.py for the cron wiring):
  1. backup_schedules()      — run BEFORE the weekly schedule updater.
  2. (main app's update_schedule_fields_task runs, pulling from LimeSurvey)
  3. validate_and_restore()  — run AFTER. Any schedule that is now null or has
     fewer than MIN_SCHEDULE_DAYS valid days is rolled back to the backup.
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone

import psycopg2

log = logging.getLogger("schedule_guard.guard")

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@db:5432/app_db"
)
BACKUP_FILE = os.environ.get("BACKUP_FILE", "/data/schedule_backup.json")
MIN_SCHEDULE_DAYS = int(os.environ.get("MIN_SCHEDULE_DAYS", "4"))


def _dsn(url: str) -> str:
    """Accept SQLAlchemy-style URLs (postgresql+psycopg2://...) as well as plain ones."""
    return url.replace("+psycopg2", "")


def _connect():
    return psycopg2.connect(_dsn(DATABASE_URL))


def _safe_target(url: str) -> str:
    """The host:port/db of DATABASE_URL with any credentials stripped, for logging."""
    return _dsn(url).split("@")[-1]


def ping() -> bool:
    """Confirm the sidecar can reach Postgres. `SELECT 1` only — does not touch
    the patients table, so it works even before the main app has created it."""
    target = _safe_target(DATABASE_URL)
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
    except Exception as exc:
        log.error("ping: could NOT reach Postgres at %s: %s", target, exc)
        return False
    log.info("ping: reached Postgres at %s", target)
    return True


# --------------------------------------------------------------------------- #
# Health check
# --------------------------------------------------------------------------- #
def count_scheduled_days(schedule) -> int:
    """Number of days in a time_to_notif dict that have a non-empty start AND end."""
    if not isinstance(schedule, dict):
        return 0
    n = 0
    for _day, window in schedule.items():
        if isinstance(window, dict) and window.get("start") and window.get("end"):
            n += 1
    return n


def is_healthy(schedule, min_days: int = MIN_SCHEDULE_DAYS) -> bool:
    return count_scheduled_days(schedule) >= min_days


# --------------------------------------------------------------------------- #
# Backup  (run before the updater)
# --------------------------------------------------------------------------- #
def backup_schedules() -> dict:
    """Snapshot every participant's current time_to_notif to BACKUP_FILE (atomically)."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT more_participant_id, time_to_notif FROM patients "
                "WHERE more_participant_id IS NOT NULL"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    snapshot = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        # keys are strings because JSON object keys must be strings
        "schedules": {str(pid): sched for pid, sched in rows},
    }
    os.makedirs(os.path.dirname(BACKUP_FILE) or ".", exist_ok=True)
    tmp = BACKUP_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snapshot, f, indent=2)
    os.replace(tmp, BACKUP_FILE)  # atomic replace so a crash never leaves a half-written file

    healthy = sum(1 for _pid, s in rows if is_healthy(s))
    log.info(
        "backup_schedules: saved %d schedule(s) to %s (%d healthy, %d already unhealthy)",
        len(rows), BACKUP_FILE, healthy, len(rows) - healthy,
    )
    return {"backed_up": len(rows), "healthy": healthy}


# --------------------------------------------------------------------------- #
# Validate + restore  (run after the updater)
# --------------------------------------------------------------------------- #
def validate_and_restore() -> dict:
    """Restore any schedule that is now null / has < MIN_SCHEDULE_DAYS days from the backup."""
    if not os.path.exists(BACKUP_FILE):
        log.error(
            "validate_and_restore: no backup at %s — nothing to restore from. "
            "Did backup_schedules run first?", BACKUP_FILE,
        )
        return {"status": "error", "reason": "no_backup"}

    with open(BACKUP_FILE) as f:
        payload = json.load(f)
    backup = payload.get("schedules", {})
    log.info(
        "validate_and_restore: loaded backup from %s (created_at=%s, %d entries)",
        BACKUP_FILE, payload.get("created_at"), len(backup),
    )

    conn = _connect()
    checked = restored = skipped_no_backup = skipped_bad_backup = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT more_participant_id, time_to_notif FROM patients "
                "WHERE more_participant_id IS NOT NULL"
            )
            rows = cur.fetchall()

            for pid, current in rows:
                checked += 1
                if is_healthy(current):
                    continue

                if str(pid) not in backup:
                    log.warning(
                        "participant %s: unhealthy schedule (%d day(s)) and no backup entry — "
                        "left for manual review (current=%s)",
                        pid, count_scheduled_days(current), current,
                    )
                    skipped_no_backup += 1
                    continue

                saved = backup[str(pid)]
                if not is_healthy(saved):
                    log.warning(
                        "participant %s: unhealthy after update AND backup is also unhealthy "
                        "(current=%d day(s), backup=%d day(s)) — left for manual review",
                        pid, count_scheduled_days(current), count_scheduled_days(saved),
                    )
                    skipped_bad_backup += 1
                    continue

                cur.execute(
                    "UPDATE patients SET time_to_notif = %s::jsonb WHERE more_participant_id = %s",
                    (json.dumps(saved), pid),
                )
                restored += 1
                log.warning(
                    "participant %s: RESTORED schedule from backup "
                    "(bad current=%d day(s) -> backup=%d day(s))",
                    pid, count_scheduled_days(current), count_scheduled_days(saved),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("validate_and_restore failed; transaction rolled back")
        raise
    finally:
        conn.close()

    log.info(
        "validate_and_restore: checked=%d restored=%d skipped_no_backup=%d skipped_bad_backup=%d",
        checked, restored, skipped_no_backup, skipped_bad_backup,
    )
    return {
        "checked": checked,
        "restored": restored,
        "skipped_no_backup": skipped_no_backup,
        "skipped_bad_backup": skipped_bad_backup,
    }


# --------------------------------------------------------------------------- #
# CLI — for manual runs / testing:  python guard.py [ping|backup|restore|check]
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "ping":
        sys.exit(0 if ping() else 1)
    elif cmd == "backup":
        print(backup_schedules())
    elif cmd == "restore":
        print(validate_and_restore())
    elif cmd == "check":
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT more_participant_id, time_to_notif FROM patients "
                "WHERE more_participant_id IS NOT NULL ORDER BY more_participant_id"
            )
            for pid, sched in cur.fetchall():
                days = count_scheduled_days(sched)
                print(f"participant {pid}: {days} day(s) "
                      f"{'OK' if is_healthy(sched) else 'UNHEALTHY'}")
        conn.close()
    else:
        print("usage: guard.py [ping|backup|restore|check]")
        sys.exit(1)
