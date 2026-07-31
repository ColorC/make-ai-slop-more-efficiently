CREATE TABLE IF NOT EXISTS ai_player_operations(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('candidate', 'verified', 'quarantined', 'retired')),
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id),
    UNIQUE(environment_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_operations_status
    ON ai_player_operations(environment_id, status, updated_at, id);

CREATE TABLE IF NOT EXISTS ai_player_operation_aliases(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    alias_kind TEXT NOT NULL,
    alias_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, alias_kind, alias_id, source_version),
    FOREIGN KEY(environment_id, operation_id)
        REFERENCES ai_player_operations(environment_id, id)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_operation_alias_target
    ON ai_player_operation_aliases(environment_id, operation_id, alias_kind, alias_id);

CREATE TABLE IF NOT EXISTS ai_player_runtime_telemetry_events(
    id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    session_id TEXT,
    operation_id TEXT,
    event_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    duration_ms REAL NOT NULL CHECK(duration_ms >= 0),
    input_tokens INTEGER NOT NULL CHECK(input_tokens >= 0),
    output_tokens INTEGER NOT NULL CHECK(output_tokens >= 0),
    action_count INTEGER NOT NULL CHECK(action_count >= 0),
    previous_event_sha256 TEXT,
    event_sha256 TEXT NOT NULL UNIQUE,
    body_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_player_runtime_telemetry_session
    ON ai_player_runtime_telemetry_events(environment_id, session_id, occurred_at, id);
CREATE INDEX IF NOT EXISTS idx_ai_player_runtime_telemetry_operation
    ON ai_player_runtime_telemetry_events(environment_id, operation_id, occurred_at, id);

CREATE TRIGGER IF NOT EXISTS ai_player_runtime_telemetry_no_update
BEFORE UPDATE ON ai_player_runtime_telemetry_events
BEGIN
    SELECT RAISE(ABORT, 'runtime telemetry is append-only');
END;
CREATE TRIGGER IF NOT EXISTS ai_player_runtime_telemetry_no_delete
BEFORE DELETE ON ai_player_runtime_telemetry_events
BEGIN
    SELECT RAISE(ABORT, 'runtime telemetry is append-only');
END;

CREATE TABLE IF NOT EXISTS ai_player_operation_executions(
    id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    operation_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    started_event_id TEXT NOT NULL REFERENCES ai_player_runtime_telemetry_events(id),
    completed_event_id TEXT NOT NULL REFERENCES ai_player_runtime_telemetry_events(id),
    evidence_step_id TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('success', 'no_effect', 'failed', 'interrupted')),
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(environment_id, operation_id)
        REFERENCES ai_player_operations(environment_id, id)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_operation_executions_operation
    ON ai_player_operation_executions(environment_id, operation_id, created_at, id);

CREATE TRIGGER IF NOT EXISTS ai_player_operation_executions_no_update
BEFORE UPDATE ON ai_player_operation_executions
BEGIN
    SELECT RAISE(ABORT, 'operation executions are append-only');
END;
CREATE TRIGGER IF NOT EXISTS ai_player_operation_executions_no_delete
BEFORE DELETE ON ai_player_operation_executions
BEGIN
    SELECT RAISE(ABORT, 'operation executions are append-only');
END;

CREATE TABLE IF NOT EXISTS ai_player_canonical_state_registry(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    semantic_fingerprint TEXT NOT NULL,
    canonical_state_id TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, semantic_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_canonical_state_id
    ON ai_player_canonical_state_registry(environment_id, canonical_state_id);

CREATE TABLE IF NOT EXISTS ai_player_canonical_state_aliases(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    state_id TEXT NOT NULL,
    canonical_state_id TEXT NOT NULL,
    semantic_fingerprint TEXT NOT NULL,
    reason TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, state_id)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_canonical_state_alias_target
    ON ai_player_canonical_state_aliases(environment_id, canonical_state_id, state_id);
