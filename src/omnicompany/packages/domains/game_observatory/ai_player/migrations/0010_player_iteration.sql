CREATE TABLE IF NOT EXISTS ai_player_action_quality_samples (
    environment_id TEXT NOT NULL,
    id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    task_id TEXT,
    command_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    execution_disposition TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id),
    FOREIGN KEY(environment_id) REFERENCES ai_player_environments(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ai_player_action_quality_window
ON ai_player_action_quality_samples(environment_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_ai_player_action_quality_session
ON ai_player_action_quality_samples(environment_id, session_id, created_at);

CREATE TABLE IF NOT EXISTS ai_player_iteration_assessments (
    environment_id TEXT NOT NULL,
    id TEXT NOT NULL,
    window_kind TEXT NOT NULL,
    overall_status TEXT NOT NULL,
    directive TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id),
    FOREIGN KEY(environment_id) REFERENCES ai_player_environments(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ai_player_iteration_assessment_latest
ON ai_player_iteration_assessments(environment_id, created_at, id);