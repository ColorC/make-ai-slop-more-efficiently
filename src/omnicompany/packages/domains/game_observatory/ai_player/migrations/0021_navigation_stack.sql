CREATE TABLE IF NOT EXISTS ai_player_navigation_stacks(
    environment_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1),
    current_state_id TEXT NOT NULL,
    body_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, session_id)
);

CREATE TABLE IF NOT EXISTS ai_player_navigation_stack_events(
    environment_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1),
    operation TEXT NOT NULL CHECK(operation IN ('push', 'pop')),
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, session_id, version)
);

CREATE TRIGGER IF NOT EXISTS ai_player_navigation_stack_events_no_update
BEFORE UPDATE ON ai_player_navigation_stack_events
BEGIN
    SELECT RAISE(ABORT, 'navigation stack events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_navigation_stack_events_no_delete
BEFORE DELETE ON ai_player_navigation_stack_events
BEGIN
    SELECT RAISE(ABORT, 'navigation stack events are append-only');
END;