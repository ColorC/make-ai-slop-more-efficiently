CREATE TABLE IF NOT EXISTS ai_player_skill_runs(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    id TEXT NOT NULL,
    skill_version_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    independent_reset_id TEXT NOT NULL,
    visual_variant_id TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_skill_runs_version
    ON ai_player_skill_runs(environment_id, skill_version_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_ai_player_skill_runs_outcome
    ON ai_player_skill_runs(environment_id, outcome, created_at);

CREATE TABLE IF NOT EXISTS ai_player_skill_validations(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    id TEXT NOT NULL,
    skill_version_id TEXT NOT NULL,
    status TEXT NOT NULL,
    total_run_count INTEGER NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_skill_validations_version
    ON ai_player_skill_validations(environment_id, skill_version_id, created_at, id);