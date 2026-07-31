CREATE TABLE IF NOT EXISTS ai_player_sessions(
    id TEXT PRIMARY KEY,
    requested_environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    objective TEXT NOT NULL,
    state TEXT NOT NULL CHECK(
        state IN ('created', 'running', 'paused', 'safe_stopped', 'completed')
    ),
    version INTEGER NOT NULL CHECK(version >= 1),
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_player_sessions_environment
    ON ai_player_sessions(environment_id, updated_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_ai_player_sessions_state
    ON ai_player_sessions(environment_id, state, updated_at DESC, id);

CREATE TABLE IF NOT EXISTS ai_player_session_lifecycle_events(
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES ai_player_sessions(id),
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    event_type TEXT NOT NULL CHECK(
        event_type IN (
            'created', 'started', 'paused', 'resumed', 'safe_stopped',
            'completed', 'checkpointed'
        )
    ),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    command_id TEXT NOT NULL UNIQUE,
    request_sha256 TEXT NOT NULL,
    previous_version INTEGER NOT NULL CHECK(previous_version >= 0),
    new_version INTEGER NOT NULL CHECK(new_version >= 1),
    body_json TEXT NOT NULL,
    result_session_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, new_version)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_session_events_session
    ON ai_player_session_lifecycle_events(session_id, new_version, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_player_session_events_environment
    ON ai_player_session_lifecycle_events(environment_id, created_at, id);

CREATE TRIGGER IF NOT EXISTS ai_player_session_events_no_update
BEFORE UPDATE ON ai_player_session_lifecycle_events
BEGIN
    SELECT RAISE(ABORT, 'AI-player session lifecycle events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_session_events_no_delete
BEFORE DELETE ON ai_player_session_lifecycle_events
BEGIN
    SELECT RAISE(ABORT, 'AI-player session lifecycle events are append-only');
END;
