# Implementation

This chapter describes the system as it was actually built, across two iterations. The
first iteration established the end-to-end pipeline — fetch context, generate a message,
deliver it, collect feedback — with personalization treated as a probabilistic A/B
variable. The second iteration kept that infrastructure unchanged and replaced the single
generation step with three competing generation strategies, an LLM validation stage, and a
cross-user example store that lets messages learn from feedback across participants.

The service is a small, self-contained Python application. It exposes **no HTTP API**: the
entire system is a set of scheduled Celery tasks that poll data sources, call the model, and
push results back into the MORE platform.

## 1. Deployment topology

The service is designed to be deployed *next to* an existing MORE platform instance and to
share its Docker network, so that all inter-service traffic stays on an internal network
rather than crossing the public internet. This was a deliberate design choice: it removes a
class of latency and authentication problems, and lets the service reach the MORE
studymanager, the MORE Gateway, and the LimeSurvey RemoteControl endpoint as internal hosts.

`docker-compose.yml` defines three services built from a single image:

| Service | Command | Role |
|---------|---------|------|
| `worker` | `celery -A app.celery_app worker` | Executes tasks (data fetch, generation, delivery) |
| `beat`   | `celery -A app.celery_app beat`   | Scheduler — enqueues tasks on their cron/interval |
| `db`     | `postgres:16`                     | Local state and the example store |

The image itself is minimal (`python:3.11-slim`, `pip install -r requirements.txt`, copy
`app/`). Both `worker` and `beat` join two networks: the compose-internal `default` and an
**external** network `traefik_default` (aliased `more_platform`), which is where the MORE
platform and its Postgres/LimeSurvey containers live. The Celery broker and result backend
are Redis (`redis://redis:6379/0`), which is expected to already exist on that shared
network rather than being defined in this compose file — a consequence of co-locating with
MORE rather than running standalone.

Health checks are defined for all three services: `pg_isready` for Postgres, a Celery
`inspect ping` for the worker, and a PID-file liveness check for beat. The worker and beat
both wait on `db: service_healthy` before starting.

## 2. Technology stack

The service stack is intentionally small and boring, chosen for operational simplicity
rather than novelty:

- **Celery + Redis** — the whole application is periodic work: poll LimeSurvey, generate,
  deliver, collect. A task queue with a beat scheduler models this directly, gives each step
  independent ret/failure isolation, and lets generation fan out across worker threads. No
  web framework is needed because nothing calls *into* the service.
- **PostgreSQL 16 (SQLAlchemy ORM)** — holds participant profiles, mutable daily state, the
  sent-notification log, and (iteration 2) the example store. The schema is applied once from
  `db/init.sql` on a fresh volume; there is no migration framework, so schema changes to a
  live database require manual DDL. This is acceptable for a fixed-duration study.
- **Haystack (`AnthropicChatGenerator`)** — used as a thin, uniform wrapper around Claude so
  the generation step has a stable interface. Iteration 2 additionally uses the raw
  `anthropic` SDK directly where Haystack's abstraction gets in the way — specifically for
  the tool-use (agentic) loop and the validator call.
- **Claude (Anthropic)** — Opus for generation, and (iteration 2) Haiku 4.5 as a cheap
  validator. The model IDs are environment-configurable.

The MORE platform side of the stack (Spring Boot studymanager, Vue frontend, Keycloak,
Elasticsearch, Firebase Cloud Messaging for delivery) is unchanged and treated as an
external dependency reached over the shared network.

## 3. Data flow and the LimeSurvey workaround

By design, participant context for MORE studies is stored in Elasticsearch, populated by an
`endUrl` callback that fires when a participant completes a survey. In practice this callback
pipeline was unreliable during the study (the ES documents came back empty), so the service
**bypasses Elasticsearch entirely** and reads survey data directly from LimeSurvey through
its RemoteControl JSON-RPC API (`app/lime_fetcher.py`).

The fetcher authenticates with an admin session key (`get_session_key`), then for each
survey performs two calls: `list_participants` to build a token → MORE-participant-ID map,
and `export_responses` to pull the completed responses. The link between the two is a MORE
convention: **the MORE participant ID is stored as the LimeSurvey token's `firstname`
field**, set by the studymanager when a participant is activated. Responses are exported with
short question codes (e.g. `Q00[SQ001]`) and parsed by `app/lime_parser.py` into typed
profile/check-in dictionaries. The session key is released after each batch.

Five surveys are consumed, each with its own configurable survey ID:

| Survey | Purpose |
|--------|---------|
| Baseline | Big Five, TPB, demographics, hobbies, initial notification schedule |
| Daily check-in (momentary assessment) | Today's feeling-state numerics + planned activity |
| PA schedule | Weekly notification-window update |
| Evening follow-up | "Did you exercise?" outcome |
| Message evaluation | Feedback on the delivered notification |

Because polling LimeSurvey on every task would be slow and fragile, the important data is
**copied into the service's own Postgres** as it arrives (`daily_checkins`, profile fields on
`patients`, etc.), so downstream steps read from the local database. This gives lower latency
and a stable, self-owned source of truth for the generation pipeline, while LimeSurvey
remains the permanent system of record.

## 4. Triggering observations from an external service

Before this work, MORE could only trigger observations internally, on its own schedulers —
there was no way for an external service to say "ask participant X question Y now." Adding
that capability was a prerequisite for the study, because notifications and their feedback
surveys have to fire at participant-specific times driven by logic that lives outside MORE.

The mechanism implemented is a **notification-based observation trigger**. The service POSTs
to a single MORE Gateway endpoint:

```http
POST {MORE_GATEWAY_BASE_URL}/api/v1/trigger/external
Headers:
  Content-Type: application/json
  More-Api-Token: <observation token>
Body:
  { "participantIds": [<ids>] }
```

The key design decision is the **token-per-observation** model. Each observation type
(momentary assessment, message-evaluation feedback, PA-schedule update, evening follow-up)
has its own API token; the request body carries only the participant IDs to trigger. One
token therefore maps to exactly one observation, and can be reused across participants and
across time without any participant being coupled to another. In the code these are four
separate environment variables (`MOMENTARY_ASSESMENT_TOKEN`, `NOTIFACTION_FEEDBACK_TOKEN`,
`PA_SCHEDULE_UPDATE_TOKEN`, `EVENING_FOLLOW_UP_TOKEN`) passed to a single helper
`_trigger_more_assessment(participant_ids, token)`.

This gave per-participant, per-observation timing control — each participant can be asked a
given questionnaire within their own preferred time window — using observations that are
momentary check-in questionnaires whose responses are easy to handle. Individually editable
observation times for arbitrary participants would be the natural next step but were out of
scope.

Notification **delivery** is separate from observation triggering. Generated messages are
sent through a studymanager internal endpoint, which handles the actual Firebase push and
logs the message in the participant's notification center:

```http
POST {MORE_STUDYMANAGER_BASE_URL}/api/v1/internal/notifications
Headers:
  X-Api-Key: <internal key>
Body:
  { "studyId": ..., "participantId": ..., "title": ..., "message": ... }
```

## 5. The scheduled task pipeline

All behaviour is driven by the Celery beat schedule in `app/celery_app.py`. Beat runs in
**UTC**; application logic offsets to local study time via a fixed `SERVER_TZ_OFFSET` of
+2 hours. A participant's day flows through the following tasks.

**Fast loop (every 150 s):**

1. `periodic_task` — scans the baseline survey and upserts a `patients` row for every
   participant who has completed it, populating the full profile (Big Five, TPB, demographics,
   hobbies, schedule) and assigning a group as `group_id = participant_id % 3`. Existing rows
   with missing profiles are backfilled.
2. `get_momentary_assessment_task` — pulls today's check-ins from LimeSurvey into
   `daily_checkins`. For participants who have *not* checked in yet, it checks whether the
   current local time falls inside that participant's notification window for the current day
   (`is_time_in_window`, which handles midnight-crossing windows) and, if so and not already
   triggered today, fires the momentary-assessment observation. `daily_survey_triggered_at`
   guards against re-triggering.
3. `send_notifications_task` — the core step (detailed in §7–8). For every participant with a
   check-in and `notif_in_24h = False`, it generates a message, delivers it, logs it, and
   triggers the message-evaluation survey.

**Daily (cron):**

- `trigger_evening_followup_task` (17:30) → triggers the "did you exercise?" survey for
  everyone notified that day.
- `fetch_evening_followup_task` (21:15) → pulls those responses into
  `evening_followup_responses`.
- `fetch_message_eval_task` (01:00) → pulls message-evaluation responses.
- `enrich_notification_examples_task` (01:30, iteration 2 only) → grades feedback and records
  outcomes onto the example store (§8).
- `reset_notification_flags` (02:00) → clears the day's perishable state: `daily_checkins`,
  `generated_notifications`, and the `notif_in_24h` / `daily_survey_triggered_at` flags.

**Weekly (Sunday):**

- `trigger_schedule_update_survey` (07:00) and `update_schedule_fields_task` (21:59) refresh
  each participant's `time_to_notif` windows from the PA-schedule survey.

The ordering matters: check-in must be captured before generation; the message-eval survey is
triggered at send time but only *fetched* after midnight, and — in iteration 2 — the example
store is only enriched at 01:30, after feedback has been fetched but before the 02:00 reset
wipes the day's context. The example snapshot itself is taken *at send time* precisely because
`daily_checkins` will not survive the reset.

## 6. Database schema and state model

The schema separates **perishable daily state** from **permanent records**.

- `patients` — one row per participant: profile (`big5`, `tpb`, demographics, `hobbies`),
  `time_to_notif` (a `{day: {start, end}}` JSON schedule), `group_id`, and the daily flags
  `notif_in_24h` and `daily_survey_triggered_at`. This is the single source of truth for the
  pipeline once populated from LimeSurvey.
- `daily_checkins` — one row per participant, today's check-in numerics as JSON. Cleared at
  the 02:00 reset.
- `generated_notifications` — today's generated text and its send status
  (`pending`/`sent`/`failed`). Written *before* the delivery attempt, so a delivery failure is
  retried on the next task tick **without re-calling the LLM**. Cleared at reset.
- `notification_logs` — the permanent record of every sent notification, with `group_id`,
  whether Big Five was used (iteration 1) / which `pipeline` produced it (iteration 2), and
  `feedback_raw` (iteration 2) once the message-eval response arrives.
- `evening_followup_responses` — permanent record of exercise-outcome responses.
- `notification_examples` (iteration 2) — the denormalized example store (§8).

Idempotency is enforced with uniqueness (`daily_checkins.more_participant_id`,
`generated_notifications.more_participant_id`, and `notification_examples`'
`(participant, date)` constraint) and with the `notif_in_24h` flag, so re-runs of the 150 s
tasks cannot double-send.

## 7. Message generation — iteration 1

Prompts are assembled in `app/pipelines.py` from two parts:

- A **context** block (`_CONTEXT_TEMPLATE`) rendering the check-in numerics — affective
  valence, energetic arousal, stress, locus of control, motivation, barriers — plus the
  planned activity and an open reflection, each on a labelled 0–100 scale.
- A conditional **personality** block. The participant's Big Five scores are converted to a
  descriptive adjective string: each trait score is bucketed to one of nine levels
  (`_score_to_level`), and each level maps to a full spectrum of facet adjectives with an
  intensity prefix (`extremely` / `very` / `a bit` / `neither`). Note that MORE stores
  *emotional stability*, so neuroticism is reconstructed as `60 − emotional_stability`. The
  adjectives are injected together with the raw scores and a trait reference (`_TRAIT_INFO`)
  under a "highest priority" instruction to adapt tone, framing and emotional register
  **invisibly** — the model is explicitly told never to mention the personality or that it is
  adapting.

**Personalization is the A/B variable.** For each notification a number 1–10 is drawn and
compared to a per-group threshold, so personality injection happens with a different
probability per group:

| `group_id` | P(personalized) |
|------------|-----------------|
| 1 | 30% |
| 2 | 60% |
| 0 | 90% |

Whether personality was actually used is recorded per row (`notification_logs.big5_used`) so
outcomes can be compared across groups. Generation runs in parallel across participants using
a `ThreadPoolExecutor` (`LLM_GENERATION_THREADS`, default 4), and each call has exponential
backoff with `Retry-After` support on Anthropic rate-limit errors (`LLM_MAX_RETRIES`,
`LLM_RETRY_BASE_DELAY`).

## 8. Message generation — iteration 2

Iteration 2 leaves the infrastructure of §3–6 untouched and restructures only the generation
step. Personality injection is now **always on**; the experimental variable becomes *which
generation strategy* produced the message.

### 8.1 Three pipelines

Every notification is produced by one of three pipelines, each implemented as a builder that
returns a zero-argument `generate_fn`:

| # | Pipeline | Prior evidence it uses |
|---|----------|------------------------|
| 0 | `basic_context` | Check-in context + Big Five only. No examples. Equivalent to the iteration-1 generator with personalization forced on. |
| 1 | `rag` | One deterministic read of the example store: good/bad messages from users similar in **Big Five + feeling-state**, injected into the prompt. |
| 2 | `agentic` | A Claude **tool-use loop** — the model itself calls `find_similar_users` and `get_user_examples` against the same store and drives retrieval. |

**Pipeline selection** is per participant and history-balanced (`_select_pipeline`): it counts
that participant's past `notification_logs.pipeline` values and samples with weights
`∝ (max_count − count + 1)`, favouring the least-used pipeline while staying randomised, so
each participant converges toward a 1/3 split over the study.

The **agentic** pipeline uses the raw Anthropic SDK with a tool loop capped at
`AGENTIC_MAX_TOOL_ITERS` (default 4) iterations. Two tools are exposed:
`find_similar_users(limit)` and `get_user_examples(participant_ids, grade?)`, both backed by
the same distance functions used by the RAG pipeline. The model is instructed to gather what
it needs and then reply with only the final message; tool-result blocks are fed back in as
`tool_result` content until the model stops calling tools or the cap is hit.

### 8.2 Validation with a cheaper model

All three pipelines terminate in the same tail: `_generate_with_validation` runs the
`generate_fn` up to `VALIDATOR_MAX_RETRIES + 1` times (3 by default) and returns the first
candidate that passes a **Claude Haiku 4.5** validator. The validator is a separate, cheap
call (`_validate_notification`) that returns a strict JSON `{valid, reason}` verdict, marking
a message invalid if it is off-topic, harmful, wrongly formatted (bullet/numbered lists), or
outside a 20–150 word band. This is a deliberate cost optimisation: validation does not need
an expensive model, so generation uses Opus while the guardrail uses Haiku. If the validator
call fails, the message is treated as valid (fail-open) so a validator outage never blocks
delivery.

### 8.3 The example store

The learning substrate is `notification_examples`, a denormalized, one-row-per-(participant,
day) table that captures the full numerical story of each sent notification:

> how the user felt (check-in numerics) → what was generated → how they graded it → whether
> they actually exercised.

It is filled in two stages so that nothing perishable is lost:

1. **Snapshot at send** (`_snapshot_notification_example`) — context numerics, `big5`,
   message, and pipeline are written when the notification is sent, *before* the 02:00 reset
   wipes `daily_checkins`. The `(participant, date)` uniqueness makes this idempotent.
2. **Nightly enrich** (`enrich_notification_examples_task`, 01:30) — fills in
   `feedback_grade`, `feedback_score`, `executed`, and `execution_activity` by matching the
   participant and date against `notification_logs.feedback_raw` and
   `evening_followup_responses`. It re-processes a 4-day lookback window, so late-arriving
   feedback backfills without duplication, and it reads only permanent tables, so it is
   independent of the daily reset.

**Feedback grade** is a 1–3 scale derived from a configurable eval field: `1 = good`
(score ≥ `FEEDBACK_GOOD_MIN`), `3 = bad` (≤ `FEEDBACK_BAD_MAX`), `2 = neutral` in between,
`NULL` when no feedback has arrived yet.

**Retrieval similarity** is a weighted sum of Big Five distance (Euclidean over the five
traits, normalised by 60) and feeling-state distance (Euclidean over the six 0–100 numerics,
normalised by 100), tunable via `BIG5_WEIGHT` / `CONTEXT_WEIGHT`. The RAG pipeline ranks all
graded rows by this distance and injects the top `RAG_EXAMPLES_PER_BUCKET` "good" examples as
patterns to emulate and the top "bad" ones as patterns to avoid, skipping neutral. Because
this is cross-user, a new participant benefits from feedback given by everyone similar to
them, not only from their own history.

## 9. Reliability and cost

Several concrete measures address the requirement to stay reliable under bursty, potentially
high LLM usage:

- **Generation before delivery.** Text is persisted to `generated_notifications` before the
  send attempt, so a failed delivery is retried by the next 150 s tick without paying for
  another generation.
- **Rate-limit backoff.** Every model call (Haystack, raw SDK, and the agentic loop) retries
  on `RateLimitError` with `Retry-After`-aware exponential backoff and jitter.
- **Bounded parallelism.** Generation fans out over a fixed thread pool
  (`LLM_GENERATION_THREADS`) rather than firing one request per participant simultaneously.
- **Cheap validation.** Haiku validates Opus output, and the validator fails open so it can
  never become an availability bottleneck.
- **Idempotent daily state.** Uniqueness constraints plus `notif_in_24h` prevent
  double-sends across overlapping task runs.

## 10. Configuration and operations

Everything environment-specific is supplied via `.env.local`: the Anthropic API key and model
IDs (`GENERATION_MODEL`, `VALIDATOR_MODEL`), the MORE Gateway base URL and the four
observation tokens, the studymanager delivery URL / study ID / internal key, and the
LimeSurvey RC URL, credentials and five survey IDs. Iteration 2 adds the generation-strategy
knobs (`SIMILAR_PARTICIPANTS_N`, `RAG_EXAMPLES_PER_BUCKET`, `AGENTIC_MAX_TOOL_ITERS`,
similarity weights, feedback thresholds).

Operationally the service is run with `docker compose up --build -d`; a full reset is
`docker compose down -v && docker compose up --build -d` (which reapplies `init.sql` on the
fresh volume). Individual tasks can be invoked directly inside the worker container for
testing, and generation logs the full system prompt, user message, and — in iteration 2 —
the injected examples per participant at INFO level, which is the fastest way to inspect
exactly what each pipeline fed the model.
