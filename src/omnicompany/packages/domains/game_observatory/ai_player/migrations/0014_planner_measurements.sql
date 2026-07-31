CREATE TABLE IF NOT EXISTS ai_player_planner_measurement_receipts(
    id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL,
    command_id TEXT NOT NULL UNIQUE,
    producer_identity TEXT NOT NULL,
    invocation_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL UNIQUE,
    artifact_sha256 TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(producer_identity, invocation_id),
    FOREIGN KEY(environment_id) REFERENCES ai_player_environments(id),
    FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);

CREATE TRIGGER IF NOT EXISTS ai_player_planner_receipts_no_update
BEFORE UPDATE ON ai_player_planner_measurement_receipts
BEGIN
    SELECT RAISE(ABORT, 'planner measurement receipts are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_planner_receipts_no_delete
BEFORE DELETE ON ai_player_planner_measurement_receipts
BEGIN
    SELECT RAISE(ABORT, 'planner measurement receipts are append-only');
END;
