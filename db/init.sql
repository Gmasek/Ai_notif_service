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
    was_personalized     BOOLEAN DEFAULT FALSE,
    group_id             INTEGER,
    send_status          TEXT    DEFAULT 'pending',  -- pending / sent / failed
    generated_at         TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    sent_at              TIMESTAMP WITH TIME ZONE
);
