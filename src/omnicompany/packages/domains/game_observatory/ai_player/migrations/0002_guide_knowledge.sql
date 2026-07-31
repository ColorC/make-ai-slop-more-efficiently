CREATE TABLE IF NOT EXISTS ai_player_guide_knowledge(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    url TEXT NOT NULL,
    season TEXT,
    server_stage TEXT,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id, version)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_guides_applicability
    ON ai_player_guide_knowledge(environment_id, status, season, server_stage, created_at);