-- Participant notification state.
-- Profile data (Big Five, TPB, demographics, hobbies, notification schedule)
-- is stored in Elasticsearch and fetched on-demand; only mutable state lives here.
CREATE TABLE patients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    more_participant_id INTEGER UNIQUE,          -- MORE study-manager participant ID
    name                TEXT    NOT NULL DEFAULT 'unknown',
    group_id            INTEGER,                 -- A/B-test group: 0, 1, or 2

    -- Profile populated from LimeSurvey baseline survey by periodic_task
    big5                JSONB,
    hobbies             TEXT[],
    tpb                 JSONB,
    time_to_notif       JSONB,                   -- {day: {start, end}} — drives notification windows
    age                 INTEGER,
    gender              TEXT,
    job_type            TEXT,

    -- LimeSurvey participant identifiers
    lime_response_id    INTEGER,
    lime_token          TEXT,

    notif_in_24h                BOOLEAN   DEFAULT FALSE,    -- notification already sent today
    daily_survey_triggered_at   TIMESTAMP WITH TIME ZONE,   -- when daily check-in was last triggered

    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Evening follow-up survey responses.
CREATE TABLE evening_followup_responses (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    more_participant_id  INTEGER,
    submitdate           TIMESTAMP WITH TIME ZONE,
    exercised            BOOLEAN,
    activity             TEXT,
    duration             TEXT,
    when_exercised       TEXT,
    other_activity       BOOLEAN,
    other_activity_desc  TEXT,
    other_duration       TEXT,
    fetched_at           TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Sent notifications log.
CREATE TABLE notification_logs (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    more_participant_id  INTEGER,
    notification_text    TEXT    NOT NULL,
    big5_used            BOOLEAN DEFAULT FALSE,
    group_id             INTEGER,
    pipeline             INTEGER,   -- generation pipeline: 0=basic_context, 1=rag, 2=agentic
    sent_at              TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    feedback_raw         JSONB      -- full survey response from message-eval (NULL = no response yet)
);

-- Today's check-in data cached from LimeSurvey. One row per participant, cleared at daily reset.
CREATE TABLE daily_checkins (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    more_participant_id  INTEGER UNIQUE NOT NULL,
    checkin_data         JSONB   NOT NULL,
    fetched_at           TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- LLM-generated notification for today. One row per participant, cleared at daily reset.
-- Persisted before send so retries reuse the text instead of re-calling the LLM.
CREATE TABLE generated_notifications (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    more_participant_id  INTEGER UNIQUE NOT NULL,
    notification_text    TEXT    NOT NULL,
    group_id             INTEGER,
    pipeline             INTEGER,   -- generation pipeline: 0=basic_context, 1=rag, 2=agentic
    send_status          TEXT    DEFAULT 'pending',  -- pending / sent / failed
    generated_at         TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    sent_at              TIMESTAMP WITH TIME ZONE
);

-- Denormalized example store: one row per (participant, day) carrying the full numerical
-- story of a sent notification. Context is snapshotted at send time (before the daily reset
-- wipes daily_checkins); feedback grade + execution outcome are filled in by the nightly
-- enrich task. Accumulates across days (NOT cleared at reset); read by the rag/agentic
-- pipelines at generation time.
CREATE TABLE IF NOT EXISTS notification_examples (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    more_participant_id  INTEGER NOT NULL,
    notif_date           DATE    NOT NULL,

    big5                 JSONB,
    pipeline             INTEGER,   -- 0=basic_context, 1=rag, 2=agentic
    notification_text    TEXT    NOT NULL,

    -- Feeling snapshot (from the day's check-in)
    mood_valence         INTEGER,
    energetic_arousal    INTEGER,
    locus_of_control     INTEGER,
    stress               INTEGER,
    motivation_pa        INTEGER,
    barrier_pa           INTEGER,
    plans_pa_today       TEXT,
    pa_scheduled_today   TEXT,
    pa_change_reason     TEXT,
    events_today         TEXT,

    -- Enrichment (nightly)
    feedback_raw         JSONB,
    feedback_grade       INTEGER,   -- 1=good, 2=neutral, 3=bad, NULL=no feedback yet
    feedback_score       NUMERIC,
    executed             BOOLEAN,   -- from evening follow-up survey (did_you_exercise)
    execution_activity   TEXT,
    enriched_at          TIMESTAMP WITH TIME ZONE,

    created_at           TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_example_participant_date UNIQUE (more_participant_id, notif_date)
);
