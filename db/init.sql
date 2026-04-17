-- Participant notification state.
-- Profile data (Big Five, TPB, demographics, hobbies, notification schedule)
-- is stored in Elasticsearch and fetched on-demand; only mutable state lives here.
CREATE TABLE patients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    more_participant_id INTEGER UNIQUE,          -- MORE study-manager participant ID
    group_id            INTEGER,                 -- A/B-test group: 0, 1, or 2

    notif_in_24h                BOOLEAN   DEFAULT FALSE,    -- notification already sent today
    daily_survey_triggered_at   TIMESTAMP WITH TIME ZONE,   -- when daily check-in was last triggered

    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Sent notifications log.
CREATE TABLE notification_logs (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    more_participant_id  INTEGER,
    notification_text    TEXT    NOT NULL,
    big5_used            BOOLEAN DEFAULT FALSE,
    group_id             INTEGER,
    sent_at              TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
