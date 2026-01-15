CREATE TABLE patiens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    name TEXT NOT NULL,

    time_to_notif JSONB,          -- mapping days/times for notifications
    firebase_token TEXT,

    big5 JSONB,                   -- Big Five personality data
    hobbies TEXT[],               -- list of hobbies
    tpb JSONB,                    -- Theory of Planned Behavior data

    group_id INTEGER,

    age INTEGER CHECK (age >= 0),
    gender TEXT,                  -- e.g. 'male', 'female', 'other'
    job_type TEXT,                -- user-provided string

    notif_in_24h BOOLEAN DEFAULT FALSE,  -- notification sent in last 24 hours

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);