CREATE TABLE IF NOT EXISTS ai_player_sanguo_daily_continuity_days(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    continuity_run_id TEXT NOT NULL,
    natural_day TEXT NOT NULL,
    day_index INTEGER NOT NULL CHECK(day_index BETWEEN 1 AND 7),
    state TEXT NOT NULL CHECK(state IN ('in_progress', 'interrupted', 'sealed')),
    version INTEGER NOT NULL CHECK(version >= 1),
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sealed_at TEXT,
    PRIMARY KEY(environment_id, continuity_run_id, natural_day),
    UNIQUE(environment_id, natural_day),
    UNIQUE(environment_id, continuity_run_id, day_index)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_sanguo_daily_continuity_run
    ON ai_player_sanguo_daily_continuity_days(
        environment_id, continuity_run_id, day_index, natural_day
    );

CREATE TABLE IF NOT EXISTS ai_player_sanguo_daily_continuity_events(
    id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL,
    continuity_run_id TEXT NOT NULL,
    natural_day TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(
        event_type IN ('duty_recorded', 'interrupted', 'resumed', 'sealed')
    ),
    command_id TEXT NOT NULL UNIQUE,
    request_sha256 TEXT NOT NULL,
    previous_version INTEGER NOT NULL CHECK(previous_version >= 0),
    new_version INTEGER NOT NULL CHECK(new_version >= 1),
    body_json TEXT NOT NULL,
    result_day_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(environment_id, continuity_run_id, natural_day)
        REFERENCES ai_player_sanguo_daily_continuity_days(
            environment_id, continuity_run_id, natural_day
        ),
    UNIQUE(environment_id, continuity_run_id, natural_day, new_version)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_sanguo_daily_continuity_events_day
    ON ai_player_sanguo_daily_continuity_events(
        environment_id, continuity_run_id, natural_day, new_version
    );

CREATE TRIGGER IF NOT EXISTS ai_player_sanguo_daily_events_no_update
BEFORE UPDATE ON ai_player_sanguo_daily_continuity_events
BEGIN
    SELECT RAISE(ABORT, 'AI-player Sanguo daily continuity events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_sanguo_daily_events_no_delete
BEFORE DELETE ON ai_player_sanguo_daily_continuity_events
BEGIN
    SELECT RAISE(ABORT, 'AI-player Sanguo daily continuity events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_sanguo_daily_days_no_delete
BEFORE DELETE ON ai_player_sanguo_daily_continuity_days
BEGIN
    SELECT RAISE(ABORT, 'AI-player Sanguo daily continuity days cannot be deleted');
END;
