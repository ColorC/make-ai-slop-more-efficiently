CREATE TABLE IF NOT EXISTS ai_player_soft_signal_reviews (
    environment_id TEXT NOT NULL,
    id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    reviewer_role TEXT NOT NULL,
    reviewer_session_id TEXT,
    review_evidence_run_id TEXT NOT NULL,
    review_evidence_step_id TEXT NOT NULL,
    responds_to_request_id TEXT,
    minimum_score INTEGER NOT NULL,
    body_json TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id),
    FOREIGN KEY(environment_id) REFERENCES ai_player_environments(id) ON DELETE RESTRICT,
    UNIQUE(environment_id, review_evidence_run_id, review_evidence_step_id),
    UNIQUE(environment_id, responds_to_request_id)
);

CREATE INDEX IF NOT EXISTS idx_ai_player_soft_signal_reviews_window
ON ai_player_soft_signal_reviews(environment_id, reviewed_at, id);

CREATE TABLE IF NOT EXISTS ai_player_soft_signal_review_subjects (
    environment_id TEXT NOT NULL,
    review_id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    PRIMARY KEY(environment_id, review_id, sample_id),
    FOREIGN KEY(environment_id, review_id)
        REFERENCES ai_player_soft_signal_reviews(environment_id, id) ON DELETE RESTRICT,
    UNIQUE(environment_id, reviewer_id, sample_id)
);

CREATE TABLE IF NOT EXISTS ai_player_soft_signal_review_requests (
    environment_id TEXT NOT NULL,
    id TEXT NOT NULL,
    trigger_review_id TEXT NOT NULL,
    execution_mode TEXT NOT NULL CHECK(execution_mode = 'non_device_review'),
    device_action_budget INTEGER NOT NULL CHECK(device_action_budget = 0),
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id),
    FOREIGN KEY(environment_id) REFERENCES ai_player_environments(id) ON DELETE RESTRICT,
    FOREIGN KEY(environment_id, trigger_review_id)
        REFERENCES ai_player_soft_signal_reviews(environment_id, id) ON DELETE RESTRICT,
    UNIQUE(environment_id, trigger_review_id)
);

CREATE INDEX IF NOT EXISTS idx_ai_player_soft_signal_review_requests_open
ON ai_player_soft_signal_review_requests(environment_id, created_at, id);

CREATE TRIGGER IF NOT EXISTS ai_player_soft_signal_reviews_no_update
BEFORE UPDATE ON ai_player_soft_signal_reviews
BEGIN
    SELECT RAISE(ABORT, 'soft-signal reviews are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_soft_signal_reviews_no_delete
BEFORE DELETE ON ai_player_soft_signal_reviews
BEGIN
    SELECT RAISE(ABORT, 'soft-signal reviews are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_soft_signal_review_requests_no_update
BEFORE UPDATE ON ai_player_soft_signal_review_requests
BEGIN
    SELECT RAISE(ABORT, 'soft-signal review requests are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_soft_signal_review_subjects_no_update
BEFORE UPDATE ON ai_player_soft_signal_review_subjects
BEGIN
    SELECT RAISE(ABORT, 'soft-signal review subjects are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_soft_signal_review_subjects_no_delete
BEFORE DELETE ON ai_player_soft_signal_review_subjects
BEGIN
    SELECT RAISE(ABORT, 'soft-signal review subjects are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_soft_signal_review_requests_no_delete
BEFORE DELETE ON ai_player_soft_signal_review_requests
BEGIN
    SELECT RAISE(ABORT, 'soft-signal review requests are append-only');
END;
