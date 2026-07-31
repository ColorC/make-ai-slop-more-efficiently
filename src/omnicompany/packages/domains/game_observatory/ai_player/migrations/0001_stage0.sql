CREATE TABLE IF NOT EXISTS ai_player_schema_version(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    version INTEGER NOT NULL CHECK(version >= 0),
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_player_environments(
    id TEXT PRIMARY KEY,
    game_id TEXT NOT NULL,
    identity_hash TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_player_environments_identity
    ON ai_player_environments(identity_hash);
CREATE INDEX IF NOT EXISTS idx_ai_player_environments_game
    ON ai_player_environments(game_id, id);

CREATE TABLE IF NOT EXISTS ai_player_memory_records(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    status TEXT NOT NULL,
    supersedes_id TEXT,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_memory_subject
    ON ai_player_memory_records(environment_id, subject_id, kind, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_player_memory_supersedes
    ON ai_player_memory_records(environment_id, supersedes_id);

CREATE TABLE IF NOT EXISTS ai_player_semantic_states(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    semantic_fingerprint TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id, version)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_states_fingerprint
    ON ai_player_semantic_states(environment_id, semantic_fingerprint, status);

CREATE TABLE IF NOT EXISTS ai_player_transition_edges(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    from_state_id TEXT NOT NULL,
    to_state_id TEXT,
    outcome TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id, version)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_edges_from_state
    ON ai_player_transition_edges(environment_id, from_state_id, outcome);
CREATE INDEX IF NOT EXISTS idx_ai_player_edges_to_state
    ON ai_player_transition_edges(environment_id, to_state_id);

CREATE TABLE IF NOT EXISTS ai_player_frontier_tasks(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_tasks_status
    ON ai_player_frontier_tasks(environment_id, status, created_at);

CREATE TABLE IF NOT EXISTS ai_player_skill_versions(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    level TEXT NOT NULL,
    status TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id),
    UNIQUE(environment_id, skill_id, version)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_skills_status
    ON ai_player_skill_versions(environment_id, skill_id, status, version DESC);

CREATE TABLE IF NOT EXISTS ai_player_session_capsules(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id),
    UNIQUE(environment_id, session_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_capsules_latest
    ON ai_player_session_capsules(environment_id, session_id, sequence DESC);

CREATE TABLE IF NOT EXISTS ai_player_entity_evidence(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_version TEXT NOT NULL,
    reference_kind TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    reference_json TEXT NOT NULL,
    PRIMARY KEY(
        environment_id,
        entity_type,
        entity_id,
        entity_version,
        reference_kind,
        reference_id
    )
);
CREATE INDEX IF NOT EXISTS idx_ai_player_evidence_reference
    ON ai_player_entity_evidence(reference_kind, reference_id);

CREATE TABLE IF NOT EXISTS ai_player_baseline_runs(
    environment_id TEXT NOT NULL REFERENCES ai_player_environments(id),
    baseline_id TEXT NOT NULL,
    fixture_hash TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, baseline_id, fixture_hash, code_hash, config_hash)
);
CREATE INDEX IF NOT EXISTS idx_ai_player_baselines_latest
    ON ai_player_baseline_runs(environment_id, baseline_id, created_at DESC);
