CREATE TABLE IF NOT EXISTS ai_player_state_adjudications(
    environment_id TEXT NOT NULL,
    id TEXT NOT NULL,
    packet_sha256 TEXT NOT NULL,
    seed_sha256 TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    reviewer_session_id TEXT NOT NULL,
    subject_session_ids_json TEXT NOT NULL,
    state_version_ids_json TEXT NOT NULL,
    assignment_version_ids_json TEXT NOT NULL,
    body_json TEXT NOT NULL,
    result_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id),
    FOREIGN KEY(environment_id) REFERENCES ai_player_environments(id)
);

CREATE INDEX IF NOT EXISTS idx_ai_player_state_adjudications_reviewer
    ON ai_player_state_adjudications(environment_id, reviewer_id, created_at);
