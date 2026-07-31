CREATE TABLE IF NOT EXISTS ai_player_account_metric_definitions (
    environment_id TEXT NOT NULL,
    id TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id),
    FOREIGN KEY(environment_id) REFERENCES ai_player_environments(id) ON DELETE RESTRICT,
    UNIQUE(environment_id, metric_key)
);

CREATE TABLE IF NOT EXISTS ai_player_account_metric_derivations (
    environment_id TEXT NOT NULL,
    id TEXT NOT NULL,
    definition_id TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    before_observation_id TEXT NOT NULL,
    after_observation_id TEXT NOT NULL,
    before_evidence_step_id TEXT NOT NULL,
    after_evidence_step_id TEXT NOT NULL,
    delta_fingerprint TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id),
    FOREIGN KEY(environment_id) REFERENCES ai_player_environments(id) ON DELETE RESTRICT,
    FOREIGN KEY(environment_id, definition_id)
        REFERENCES ai_player_account_metric_definitions(environment_id, id) ON DELETE RESTRICT,
    UNIQUE(environment_id, delta_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_ai_player_account_metric_derivations_window
ON ai_player_account_metric_derivations(environment_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_ai_player_account_metric_derivations_key
ON ai_player_account_metric_derivations(environment_id, metric_key, created_at, id);

CREATE TRIGGER IF NOT EXISTS ai_player_account_metric_definitions_no_update
BEFORE UPDATE ON ai_player_account_metric_definitions
BEGIN
    SELECT RAISE(ABORT, 'account metric definitions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_account_metric_definitions_no_delete
BEFORE DELETE ON ai_player_account_metric_definitions
BEGIN
    SELECT RAISE(ABORT, 'account metric definitions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_account_metric_derivations_no_update
BEFORE UPDATE ON ai_player_account_metric_derivations
BEGIN
    SELECT RAISE(ABORT, 'account metric derivations are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_account_metric_derivations_no_delete
BEFORE DELETE ON ai_player_account_metric_derivations
BEGIN
    SELECT RAISE(ABORT, 'account metric derivations are append-only');
END;
