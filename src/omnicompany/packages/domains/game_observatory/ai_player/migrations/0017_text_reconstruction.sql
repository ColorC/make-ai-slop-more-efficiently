DROP TRIGGER IF EXISTS ai_player_text_corrections_no_update;
DROP TRIGGER IF EXISTS ai_player_text_corrections_no_delete;

ALTER TABLE ai_player_text_corrections
RENAME TO ai_player_text_corrections_v16;

CREATE TABLE ai_player_text_corrections (
    id TEXT PRIMARY KEY,
    source_table TEXT NOT NULL,
    record_key_json TEXT NOT NULL,
    source_column TEXT NOT NULL,
    field_path TEXT NOT NULL,
    original_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(
        status IN ('recovered', 'reconstructed', 'unrecoverable')
    ),
    projected_text TEXT NOT NULL,
    diagnosis TEXT NOT NULL,
    created_by TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

INSERT INTO ai_player_text_corrections(
    id, source_table, record_key_json, source_column, field_path,
    original_sha256, status, projected_text, diagnosis, created_by,
    body_json, created_at
)
SELECT
    id, source_table, record_key_json, source_column, field_path,
    original_sha256, status, projected_text, diagnosis, created_by,
    body_json, created_at
FROM ai_player_text_corrections_v16;

DROP TABLE ai_player_text_corrections_v16;

CREATE INDEX idx_ai_player_text_corrections_source
ON ai_player_text_corrections(
    source_table,
    record_key_json,
    source_column,
    field_path,
    created_at,
    id
);

CREATE INDEX idx_ai_player_text_corrections_hash
ON ai_player_text_corrections(original_sha256, created_at, id);

CREATE TRIGGER ai_player_text_corrections_no_update
BEFORE UPDATE ON ai_player_text_corrections
BEGIN
    SELECT RAISE(ABORT, 'AI-player text corrections are append-only');
END;

CREATE TRIGGER ai_player_text_corrections_no_delete
BEFORE DELETE ON ai_player_text_corrections
BEGIN
    SELECT RAISE(ABORT, 'AI-player text corrections are append-only');
END;
