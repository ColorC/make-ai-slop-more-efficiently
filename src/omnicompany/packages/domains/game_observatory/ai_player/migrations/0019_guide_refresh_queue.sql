CREATE TABLE IF NOT EXISTS ai_player_guide_refresh_requests(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    id TEXT NOT NULL PRIMARY KEY,
    task_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status = 'queued'),
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_player_guide_refresh_requests_pending
    ON ai_player_guide_refresh_requests(environment_id, created_at, id);

CREATE TABLE IF NOT EXISTS ai_player_guide_refresh_receipts(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    id TEXT NOT NULL PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE
        REFERENCES ai_player_guide_refresh_requests(id),
    status TEXT NOT NULL CHECK(
        status IN ('completed', 'offline', 'source_unavailable', 'failed')
    ),
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_player_guide_refresh_receipts_status
    ON ai_player_guide_refresh_receipts(environment_id, status, created_at, id);
