CREATE TABLE IF NOT EXISTS ai_player_gameplay_candidates (
    environment_id TEXT NOT NULL,
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    game_id TEXT NOT NULL,
    status TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id, version),
    FOREIGN KEY(environment_id) REFERENCES ai_player_environments(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ai_player_gameplay_candidates_status
ON ai_player_gameplay_candidates(environment_id, status, created_at);

CREATE TABLE IF NOT EXISTS ai_player_account_policies (
    environment_id TEXT NOT NULL,
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id, version),
    FOREIGN KEY(environment_id) REFERENCES ai_player_environments(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ai_player_account_policies_latest
ON ai_player_account_policies(environment_id, version DESC);

CREATE TABLE IF NOT EXISTS ai_player_speech_intents (
    environment_id TEXT NOT NULL,
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    policy_id TEXT NOT NULL,
    triggering_task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id, version),
    FOREIGN KEY(environment_id) REFERENCES ai_player_environments(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ai_player_speech_intents_status
ON ai_player_speech_intents(environment_id, status, created_at);

CREATE TABLE IF NOT EXISTS ai_player_speech_events (
    environment_id TEXT NOT NULL,
    id TEXT NOT NULL,
    speech_intent_id TEXT NOT NULL,
    speech_intent_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id),
    FOREIGN KEY(environment_id) REFERENCES ai_player_environments(id) ON DELETE RESTRICT,
    FOREIGN KEY(environment_id, speech_intent_id, speech_intent_version)
        REFERENCES ai_player_speech_intents(environment_id, id, version) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ai_player_speech_events_intent
ON ai_player_speech_events(environment_id, speech_intent_id, created_at);