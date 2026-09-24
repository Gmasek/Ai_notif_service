# schedule-guard

A small, **independently deployed** safety net for the weekly notification
schedule update. It protects against the failure mode where the schedule updater
writes a `null` or incomplete `time_to_notif` (as happened to participant 54),
which then lets the notification pipeline fire outside any window.

It does **not** modify the main app or its database schema. It only:

1. **Backs up** every participant's current `time_to_notif` to its own JSON file
   (on a Docker volume) *before* the updater runs.
2. Lets the main app's `update_schedule_fields_task` run as usual.
3. **Validates & restores** *after*: any participant whose schedule is now `null`
   or has fewer than `MIN_SCHEDULE_DAYS` (default 4) valid days is rolled back to
   the backed-up value.

If both the new schedule **and** the backup are unhealthy, it does **not**
overwrite — it logs a warning for manual review (restoring a broken schedule
would not help).

## What "healthy" means

`time_to_notif` looks like:

```json
{
  "monday":    {"start": "10:00", "end": "14:00"},
  "tuesday":   {"start": "10:00", "end": "14:00"},
  "wednesday": {"start": "10:00", "end": "14:00"},
  "thursday":  {"start": "10:00", "end": "14:00"}
}
```

A day counts only if it has a non-empty `start` **and** `end`. Healthy = at
least `MIN_SCHEDULE_DAYS` such days.

## Deploy

```bash
cd schedule_guard
cp .env.example .env
# edit .env: DATABASE_URL, DB_NETWORK, cron times, MIN_SCHEDULE_DAYS

# find the network the main Postgres is on and set DB_NETWORK to it:
docker network ls

docker compose up -d --build
docker compose logs -f
```

The container schedules itself; nothing else to wire up. Because it's separate,
you can rebuild/redeploy it without touching the main stack.

### Timing

Cron times are UTC by default to match celery beat, which runs
`update_schedule_fields_task` at **21:59 UTC Sunday**. Defaults:

- `BACKUP_CRON=50 21 * * 0` → backup Sunday 21:50 UTC (before the updater)
- `RESTORE_CRON=15 22 * * 0` → restore Sunday 22:15 UTC (after the updater)

Give the updater enough margin; widen the gap if it can run long.

## Manual runs / testing

```bash
# check the sidecar can reach Postgres on the server (SELECT 1 only, no tables
# needed). Exits 0 on success, 1 on failure — handy in a deploy smoke test.
docker compose exec schedule-guard python guard.py ping

# inspect current schedule health for all participants
docker compose exec schedule-guard python guard.py check

# force a backup now
docker compose exec schedule-guard python guard.py backup

# force validate + restore now (uses the latest backup file)
docker compose exec schedule-guard python guard.py restore
```

## Notes

- Backup is one snapshot per run, overwritten each week (atomic write), stored on
  the `schedule_guard_data` volume — it survives container rebuilds.
- Connects with `DATABASE_URL`; SQLAlchemy-style URLs
  (`postgresql+psycopg2://...`) are accepted too.
- Read/writes only `patients.time_to_notif`. No schema changes required.
