CREATE TABLE IF NOT EXISTS ai_player_state_observations(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    id TEXT NOT NULL,
    feature_hash TEXT NOT NULL,
    body_json TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_observations_feature
    ON ai_player_state_observations(environment_id, feature_hash, captured_at);

CREATE TABLE IF NOT EXISTS ai_player_state_assignments(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    state_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    method TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id),
    UNIQUE(environment_id, observation_id, version)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_assignments_observation
    ON ai_player_state_assignments(environment_id, observation_id, version);
CREATE INDEX IF NOT EXISTS idx_ai_player_assignments_state
    ON ai_player_state_assignments(environment_id, state_id, status, created_at);