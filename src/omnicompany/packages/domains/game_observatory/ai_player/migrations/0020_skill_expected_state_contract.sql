CREATE TABLE IF NOT EXISTS ai_player_skill_contract_migrations(
    migration_id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL,
    skill_version_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    original_body_json TEXT NOT NULL,
    original_body_sha256 TEXT NOT NULL,
    original_content_sha256 TEXT NOT NULL,
    original_status TEXT NOT NULL,
    migrated_body_sha256 TEXT NOT NULL,
    migrated_content_sha256 TEXT NOT NULL,
    migrated_status TEXT NOT NULL CHECK(migrated_status = 'invalidated'),
    reason_code TEXT NOT NULL,
    migrated_at TEXT NOT NULL,
    UNIQUE(environment_id, skill_version_id)
);

CREATE TRIGGER IF NOT EXISTS ai_player_skill_contract_migrations_no_update
BEFORE UPDATE ON ai_player_skill_contract_migrations
BEGIN
    SELECT RAISE(ABORT, 'skill contract migration provenance is append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_skill_contract_migrations_no_delete
BEFORE DELETE ON ai_player_skill_contract_migrations
BEGIN
    SELECT RAISE(ABORT, 'skill contract migration provenance is append-only');
END;
