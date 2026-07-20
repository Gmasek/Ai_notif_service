# AI Notification Prototype

Generates personalized physical-activity motivational notifications for MORE study
participants. Participant profiles, daily check-ins and survey feedback come from
**LimeSurvey**; messages are generated with **Claude** and delivered through the MORE
studymanager backend.

There is **no HTTP API** — the whole system is a set of scheduled Celery tasks.

## How it works

```
LimeSurvey ──► profiles / check-ins / feedback
                    │
                    ▼
        ┌───────────────────────────┐
        │  Celery beat + worker      │
        │  (scheduled tasks)         │
        └───────────────────────────┘
                    │
      generate (Claude) ──► validate (Haiku) ──► send via studymanager
                    │
                    ▼
        notification_examples  ◄── nightly enrichment (grade + did they exercise)
                    │
                    └──► retrieval material for the next generation
```

Daily cycle for a participant:

1. **`periodic_task`** — scans the LimeSurvey baseline survey and upserts `patients`
   (Big Five, TPB, demographics, notification schedule).
2. **`get_momentary_assessment_task`** — pulls today's check-in into `daily_checkins`;
   if there is none and the participant is inside their notification window, triggers the
   momentary-assessment survey via the MORE Gateway.
3. **`send_notifications_task`** — for participants with a check-in who haven't been
   notified today: picks a generation pipeline, generates + validates the message, sends it
   via the studymanager internal endpoint, writes `notification_logs`, snapshots
   `notification_examples`, and triggers the message-evaluation survey.
4. **`fetch_message_eval_task`** — pulls message-eval responses into
   `notification_logs.feedback_raw`.
5. **`trigger_evening_followup_task`** / **`fetch_evening_followup_task`** — the evening
   "did you exercise?" survey, stored in `evening_followup_responses`.
6. **`enrich_notification_examples_task`** — grades the feedback and records the execution
   outcome onto `notification_examples` (see *Example store* below).
7. **`reset_notification_flags`** — clears daily state (`daily_checkins`,
   `generated_notifications`, `notif_in_24h`).

## Generation pipelines

Every notification is produced by one of three pipelines. Personalization (Big Five
injection) is **always on**; the pipelines differ only in what prior evidence they use.

| # | Pipeline | What it uses |
|---|----------|--------------|
| 0 | `basic_context` | Check-in context + Big Five personality only. No examples. |
| 1 | `rag` | One read of the example store: good/bad examples from users similar in **Big Five + feeling-state**, injected into the prompt. |
| 2 | `agentic` | A Claude **tool-use loop** — the model calls `find_similar_users` and `get_user_examples` against the same store and drives retrieval itself. |

**Selection** is per participant and history-balanced: `_select_pipeline` counts that
participant's past `notification_logs.pipeline` values and picks with weights favouring the
least-used pipeline, so each participant converges to roughly 1/3 of each over time.

**All three** end with the same tail: a **Claude Haiku 4.5 validator** with up to 3
generation attempts, taking the first candidate that passes.

## Example store

`notification_examples` is a denormalized, one-row-per-(participant, day) table holding the
full numerical story of each sent notification:

> how the user felt (check-in numerics) → what was generated → how they graded it →
> whether they actually exercised

It is populated in two stages so nothing perishable is lost:

- **Snapshot at send** — context numerics + `big5` + message + pipeline are written when the
  notification is sent, *before* the 02:00 reset wipes `daily_checkins`.
- **Nightly enrich** — `enrich_notification_examples_task` fills in `feedback_grade`,
  `feedback_score`, `executed` and `execution_activity` by matching the participant + date
  against `notification_logs` and `evening_followup_responses`. It re-processes recent rows,
  so late-arriving feedback backfills without duplication.

**Feedback grade** is a 1–3 scale derived from the configured eval field:
`1 = good` (score ≥ `FEEDBACK_GOOD_MIN`), `3 = bad` (≤ `FEEDBACK_BAD_MAX`), `2 = neutral`
in between, `NULL` when no feedback has arrived. Retrieval surfaces grade 1 as
"emulate these" and grade 3 as "avoid these"; neutral is skipped.

**Similarity** for retrieval is a weighted sum of Big Five distance and feeling-state
distance (valence, arousal, locus of control, stress, motivation, barriers), tunable via
`BIG5_WEIGHT` / `CONTEXT_WEIGHT`.

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
| `enrich_notification_examples_task` | 01:30 daily |
| `reset_notification_flags` | 02:00 daily |
| `trigger_schedule_update_survey` | Sunday 07:00 |
| `update_schedule_fields_task` | Sunday 21:59 |

## Database

Schema lives in [`db/init.sql`](db/init.sql) (applied automatically on a fresh Postgres
volume — there is no migration framework, so schema changes to a live DB need manual DDL).

| Table | Purpose |
|-------|---------|
| `patients` | Participant profile + notification state |
| `daily_checkins` | Today's check-in (cleared at daily reset) |
| `generated_notifications` | Today's generated text, so retries don't re-call the LLM (cleared at reset) |
| `notification_logs` | Every sent notification + raw message-eval feedback |
| `evening_followup_responses` | "Did you exercise?" survey responses |
| `notification_examples` | Denormalized graded example store (accumulates; **not** reset) |

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
| `GENERATION_MODEL` | `claude-opus-4-7` | Model used for generation |
| `VALIDATOR_MODEL` | `claude-haiku-4-5` | Model used to validate candidates |
| `VALIDATOR_MAX_RETRIES` | `2` | Extra validation attempts (3 total) |
| `LLM_GENERATION_THREADS` | `4` | Parallel generation workers |
| `LLM_MAX_RETRIES` | `3` | Rate-limit retry attempts |
| `LLM_RETRY_BASE_DELAY` | `5.0` | Backoff base (seconds) |
| `FEEDBACK_RATING_FIELD` | `Q00[SQ004]` | Eval-survey field holding the rating |
| `FEEDBACK_GOOD_MIN` | `4` | Score ≥ this → grade 1 (good) |
| `FEEDBACK_BAD_MAX` | `2` | Score ≤ this → grade 3 (bad) |
| `SIMILAR_PARTICIPANTS_N` | `5` | Donors considered for retrieval |
| `RAG_EXAMPLES_PER_BUCKET` | `3` | Good/bad examples injected per generation |
| `AGENTIC_MAX_TOOL_ITERS` | `4` | Tool-loop iteration cap |
| `BIG5_WEIGHT` / `CONTEXT_WEIGHT` | `1.0` / `1.0` | Similarity weighting |
| `DATABASE_URL` | — | Postgres connection string |
| `MORE_GATEWAY_BASE_URL`, `*_TOKEN` | — | MORE Gateway survey triggers |
| `MORE_STUDYMANAGER_BASE_URL`, `MORE_STUDY_ID`, `INTERNAL_API_KEY` | — | Notification delivery |
| `LIME_*` | — | LimeSurvey RC API URL, credentials, survey IDs |

> `FEEDBACK_RATING_FIELD` is a placeholder until the message-eval survey schema is
> confirmed; set it to the real field code before relying on grading.

## Local testing

Seed synthetic participants with check-ins to exercise generation without live LimeSurvey
data:

```bash
docker compose cp seed_stress_test.py worker:/app/seed_stress_test.py
docker compose exec worker python /app/seed_stress_test.py --count 10
docker compose exec worker python -c \
  "from app.tasks import send_notifications_task; print(send_notifications_task())"
```

Note the image only copies `app/`, so standalone scripts must be `docker compose cp`'d in.

## Logs

```bash
docker compose logs -f worker
docker compose logs -f beat
```

Generation logs the full system prompt, user message and injected examples per participant
at INFO level, which is the fastest way to see exactly what a pipeline fed the model.
