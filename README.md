# AI Notification Prototype

Generates personalized physical-activity motivational notifications for MORE study
participants. Participant profiles, daily check-ins and survey responses come from
**LimeSurvey**; messages are generated with **Claude** and delivered through the MORE
studymanager backend. 

This application has to be deployed next to an instance of MORE platform, and share a network.
https://github.com/MORE-Platform

There is **no HTTP API** — the whole system is a set of scheduled Celery tasks.

## How it works

```
LimeSurvey ──► profiles / check-ins / survey responses
                    │
                    ▼
        ┌───────────────────────────┐
        │  Celery beat + worker      │
        │  (scheduled tasks)         │
        └───────────────────────────┘
                    │
        generate with Claude ──► send via studymanager ──► trigger feedback survey
```

Daily cycle for a participant:

1. **`periodic_task`** — scans the LimeSurvey baseline survey and upserts `patients`
   (Big Five, TPB, demographics, notification schedule). Participants are assigned an
   A/B group (`group_id = participant_id % 3`).
2. **`get_momentary_assessment_task`** — pulls today's check-in into `daily_checkins`;
   if there is none and the participant is inside their notification window, triggers the
   momentary-assessment survey via the MORE Gateway.
3. **`send_notifications_task`** — for participants with a check-in who haven't been
   notified today: generates the message with Claude, sends it via the studymanager
   internal endpoint, writes `notification_logs`, marks `notif_in_24h`, and triggers the
   message-evaluation survey. Generated text is persisted to `generated_notifications`
   first, so a failed send is retried without re-calling the LLM.
4. **`trigger_evening_followup_task`** / **`fetch_evening_followup_task`** — the evening
   "did you exercise?" survey, stored in `evening_followup_responses`.
5. **`fetch_message_eval_task`** — fetches message-evaluation responses from LimeSurvey
   and logs them. *(Currently fetch-only — the responses are returned by the task but not
   persisted to the database.)*
6. **`reset_notification_flags`** — clears daily state (`daily_checkins`,
   `generated_notifications`, `notif_in_24h`).

Weekly, **`trigger_schedule_update_survey`** and **`update_schedule_fields_task`** refresh
each participant's `time_to_notif` notification windows from the PA-schedule survey.

## Message generation

Prompts are assembled in [`app/pipelines.py`](app/pipelines.py) from two parts:

- **Context** — the check-in numerics (affective valence, energetic arousal, stress, locus
  of control, motivation, barriers) plus the planned activity and open reflection.
- **Personality** (conditional) — the participant's Big Five scores rendered as a full
  spectrum of adjective descriptors, injected with instructions to adapt tone, framing and
  emotional register *invisibly*.

**Personalization is the A/B variable.** For each notification a number 1–10 is drawn and
compared against a per-group threshold, so personality injection happens with a different
probability per group:

| `group_id` | Chance the message is personalized |
|------------|-----------------------------------|
| 1 | 30% |
| 2 | 60% |
| 0 | 90% |

Whether personality was actually used is recorded on each row
(`generated_notifications.was_personalized`, `notification_logs.big5_used`) so outcomes can
be compared across groups.

Generation runs in parallel across participants (`LLM_GENERATION_THREADS` worker threads)
with exponential-backoff retry on rate limits.

## Schedule

Celery beat runs in **UTC** (application logic offsets by `SERVER_TZ_OFFSET`, +2h).

| Task | Schedule |
|------|----------|
| `periodic_task` | every 150 s |
| `get_momentary_assessment_task` | every 150 s |
| `send_notifications_task` | every 150 s |
| `trigger_evening_followup_task` | 17:30 daily |
| `fetch_evening_followup_task` | 21:15 daily |
| `fetch_message_eval_task` | 01:00 daily |
| `reset_notification_flags` | 02:00 daily |
| `trigger_schedule_update_survey` | Sunday 07:00 |
| `update_schedule_fields_task` | Sunday 21:59 |

> Note: several task docstrings say "every 15 minutes"; the configured interval is
> actually 150 seconds.

## Database

Schema lives in [`db/init.sql`](db/init.sql) (applied automatically on a fresh Postgres
volume — there is no migration framework, so schema changes to a live DB need manual DDL).

| Table | Purpose |
|-------|---------|
| `patients` | Participant profile (Big Five, TPB, demographics, schedule) + notification state |
| `daily_checkins` | Today's check-in (cleared at daily reset) |
| `generated_notifications` | Today's generated text + send status, so retries don't re-call the LLM (cleared at reset) |
| `notification_logs` | Every sent notification, with group and whether Big Five was used |
| `evening_followup_responses` | "Did you exercise?" survey responses |

## Setup

### 1. Configure environment

```bash
cp .env.example .env.local
# fill in ANTHROPIC_API_KEY, LimeSurvey credentials/survey IDs, MORE Gateway tokens
```

### 2. Start the services

```bash
docker compose up --build -d
```

Services: **worker** (Celery worker), **beat** (Celery scheduler), **db** (Postgres 16).

Two local-run caveats:

- The Celery broker is `redis://redis:6379/0`, but **no `redis` service is defined in
  `docker-compose.yml`** — it is expected on the external network. Without it the worker
  logs connection errors and eventually exits. Add a `redis` service (or run one on the
  network) for a fully standalone local stack.
- `docker-compose.yml` joins the external network `traefik_default`. If it doesn't exist
  locally: `docker network create traefik_default`.

### 3. Reset the database

```bash
docker compose down -v && docker compose up --build -d
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | — | Claude API key (required) |
| `LLM_GENERATION_THREADS` | `4` | Parallel generation worker threads |
| `LLM_MAX_RETRIES` | `3` | Rate-limit retry attempts |
| `LLM_RETRY_BASE_DELAY` | `5.0` | Backoff base (seconds) |
| `DATABASE_URL` | — | Postgres connection string |
| `MORE_GATEWAY_BASE_URL` | — | MORE Gateway base URL for survey triggers |
| `MOMENTARY_ASSESMENT_TOKEN` | — | Triggers the daily check-in survey |
| `NOTIFACTION_FEEDBACK_TOKEN` | — | Triggers the message-evaluation survey |
| `PA_SCHEDULE_UPDATE_TOKEN` | — | Triggers the weekly PA-schedule survey |
| `EVENING_FOLLOW_UP_TOKEN` | — | Triggers the evening follow-up survey |
| `MORE_STUDYMANAGER_BASE_URL` | `http://host.docker.internal:8080` | Notification delivery endpoint |
| `MORE_STUDY_ID` | - | Study ID used when sending notifications |
| `INTERNAL_API_KEY` | — | API key for the studymanager internal endpoint |
| `LIME_REMOTE_URL` | — | LimeSurvey RemoteControl API URL |
| `LIME_ADMIN_USER` / `LIME_ADMIN_PWD` | — | LimeSurvey credentials |
| `LIME_*_SURVEY_ID` | — | Baseline, daily check-in, PA schedule, evening follow-up and message-eval survey IDs |

The generation model is currently hard-coded to `claude-opus-4-7` in
[`app/pipelines.py`](app/pipelines.py).

## Running tasks manually

Tasks can be invoked directly inside the worker container, which is the quickest way to
exercise a step without waiting for the scheduler:

```bash
docker compose exec worker python -c \
  "from app.tasks import periodic_task; print(periodic_task())"

docker compose exec worker python -c \
  "from app.tasks import send_notifications_task; print(send_notifications_task())"
```

## Logs

```bash
docker compose logs -f worker
docker compose logs -f beat
```

Generation logs the full system prompt and user message per participant at INFO level,
which is the fastest way to see exactly what was sent to the model.
