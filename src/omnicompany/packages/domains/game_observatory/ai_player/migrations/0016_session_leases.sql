DROP TRIGGER IF EXISTS ai_player_session_events_no_update;
DROP TRIGGER IF EXISTS ai_player_session_events_no_delete;

ALTER TABLE ai_player_session_lifecycle_events
RENAME TO ai_player_session_lifecycle_events_v15;

CREATE TABLE ai_player_session_lifecycle_events(
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES ai_player_sessions(id),
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    event_type TEXT NOT NULL CHECK(
        event_type IN (
            'created', 'started', 'paused', 'resumed', 'safe_stopped',
            'completed', 'checkpointed', 'heartbeat', 'stale_reconciled'
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

INSERT INTO ai_player_session_lifecycle_events(
    id, session_id, environment_id, event_type, actor, reason,
    command_id, request_sha256, previous_version, new_version,
    body_json, result_session_json, created_at
)
SELECT
    id, session_id, environment_id, event_type, actor, reason,
    command_id, request_sha256, previous_version, new_version,
    body_json, result_session_json, created_at
FROM ai_player_session_lifecycle_events_v15;

DROP TABLE ai_player_session_lifecycle_events_v15;

CREATE INDEX idx_ai_player_session_events_session
    ON ai_player_session_lifecycle_events(session_id, new_version, created_at);
CREATE INDEX idx_ai_player_session_events_environment
    ON ai_player_session_lifecycle_events(environment_id, created_at, id);

CREATE TRIGGER ai_player_session_events_no_update
BEFORE UPDATE ON ai_player_session_lifecycle_events
BEGIN
    SELECT RAISE(ABORT, 'AI-player session lifecycle events are append-only');
END;

CREATE TRIGGER ai_player_session_events_no_delete
BEFORE DELETE ON ai_player_session_lifecycle_events
BEGIN
    SELECT RAISE(ABORT, 'AI-player session lifecycle events are append-only');
END;

CREATE TABLE IF NOT EXISTS ai_player_session_leases(
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES ai_player_sessions(id),
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    holder TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'released', 'expired')),
    acquired_at TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    body_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_player_session_leases_active_environment
    ON ai_player_session_leases(environment_id) WHERE status='active';
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_player_session_leases_active_session
    ON ai_player_session_leases(session_id) WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_ai_player_session_leases_expiry
    ON ai_player_session_leases(status, expires_at, environment_id, session_id);
