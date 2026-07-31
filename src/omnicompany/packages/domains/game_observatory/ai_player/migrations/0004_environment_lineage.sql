CREATE TABLE IF NOT EXISTS ai_player_environment_promotions(
    id TEXT PRIMARY KEY,
    parent_environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    child_environment_id TEXT NOT NULL UNIQUE REFERENCES ai_player_environments(id),
    body_json TEXT NOT NULL,
    promoted_at TEXT NOT NULL,
    CHECK(parent_environment_id <> child_environment_id)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_environment_promotions_parent
    ON ai_player_environment_promotions(parent_environment_id, promoted_at, id);

CREATE TABLE IF NOT EXISTS ai_player_environment_lineage(
    parent_environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    child_environment_id TEXT NOT NULL UNIQUE REFERENCES ai_player_environments(id),
    promotion_id TEXT NOT NULL UNIQUE REFERENCES ai_player_environment_promotions(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY(parent_environment_id, child_environment_id),
    CHECK(parent_environment_id <> child_environment_id)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_environment_lineage_parent
    ON ai_player_environment_lineage(parent_environment_id, child_environment_id);

CREATE TABLE IF NOT EXISTS ai_player_evidence_origins(
    reference_kind TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    origin_environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY(reference_kind, reference_id)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_evidence_origins_environment
    ON ai_player_evidence_origins(origin_environment_id, reference_kind, reference_id);

INSERT OR IGNORE INTO ai_player_evidence_origins(
    reference_kind, reference_id, origin_environment_id, created_at
)
SELECT reference_kind, reference_id, MIN(environment_id), CURRENT_TIMESTAMP
FROM ai_player_entity_evidence
GROUP BY reference_kind, reference_id;
