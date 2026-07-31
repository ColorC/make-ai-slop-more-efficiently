CREATE TABLE IF NOT EXISTS ai_player_text_corrections (
    id TEXT PRIMARY KEY,
    source_table TEXT NOT NULL,
    record_key_json TEXT NOT NULL,
    source_column TEXT NOT NULL,
    field_path TEXT NOT NULL,
    original_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('recovered', 'unrecoverable')),
    projected_text TEXT NOT NULL,
    diagnosis TEXT NOT NULL,
    created_by TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_player_text_corrections_source
ON ai_player_text_corrections(
    source_table,
    record_key_json,
    source_column,
    field_path,
    created_at,
    id
);

CREATE INDEX IF NOT EXISTS idx_ai_player_text_corrections_hash
ON ai_player_text_corrections(original_sha256, created_at, id);

CREATE TRIGGER IF NOT EXISTS ai_player_text_corrections_no_update
BEFORE UPDATE ON ai_player_text_corrections
BEGIN
    SELECT RAISE(ABORT, 'AI-player text corrections are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_text_corrections_no_delete
BEFORE DELETE ON ai_player_text_corrections
BEGIN
    SELECT RAISE(ABORT, 'AI-player text corrections are append-only');
END;
