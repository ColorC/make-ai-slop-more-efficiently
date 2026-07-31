CREATE TABLE IF NOT EXISTS ai_player_tier1_remediation_verifications (
    environment_id TEXT NOT NULL,
    id TEXT NOT NULL,
    failed_assessment_id TEXT NOT NULL,
    gate_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    verifier_id TEXT NOT NULL,
    verifier_session_id TEXT NOT NULL,
    verification_evidence_run_id TEXT NOT NULL,
    verification_evidence_step_id TEXT NOT NULL,
    body_json TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    PRIMARY KEY(environment_id, id),
    FOREIGN KEY(environment_id) REFERENCES ai_player_environments(id) ON DELETE RESTRICT,
    FOREIGN KEY(environment_id, failed_assessment_id)
        REFERENCES ai_player_iteration_assessments(environment_id, id) ON DELETE RESTRICT,
    UNIQUE(environment_id, verification_evidence_run_id, verification_evidence_step_id)
);

CREATE INDEX IF NOT EXISTS idx_ai_player_tier1_remediation_assessment
ON ai_player_tier1_remediation_verifications(
    environment_id, failed_assessment_id, verified_at, id
);

CREATE TABLE IF NOT EXISTS ai_player_tier1_remediation_cases (
    environment_id TEXT NOT NULL,
    verification_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    partition TEXT NOT NULL,
    fixture_id TEXT NOT NULL,
    evidence_run_id TEXT NOT NULL,
    evidence_step_id TEXT NOT NULL,
    PRIMARY KEY(environment_id, verification_id, case_id),
    FOREIGN KEY(environment_id, verification_id)
        REFERENCES ai_player_tier1_remediation_verifications(environment_id, id)
        ON DELETE RESTRICT,
    UNIQUE(environment_id, evidence_run_id, evidence_step_id)
);

CREATE TRIGGER IF NOT EXISTS ai_player_tier1_remediation_verifications_no_update
BEFORE UPDATE ON ai_player_tier1_remediation_verifications
BEGIN
    SELECT RAISE(ABORT, 'tier-1 remediation verifications are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_tier1_remediation_verifications_no_delete
BEFORE DELETE ON ai_player_tier1_remediation_verifications
BEGIN
    SELECT RAISE(ABORT, 'tier-1 remediation verifications are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_tier1_remediation_cases_no_update
BEFORE UPDATE ON ai_player_tier1_remediation_cases
BEGIN
    SELECT RAISE(ABORT, 'tier-1 remediation cases are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_tier1_remediation_cases_no_delete
BEFORE DELETE ON ai_player_tier1_remediation_cases
BEGIN
    SELECT RAISE(ABORT, 'tier-1 remediation cases are append-only');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_iteration_assessments_no_update
BEFORE UPDATE ON ai_player_iteration_assessments
BEGIN
    SELECT RAISE(ABORT, 'iteration assessments are immutable');
END;

CREATE TRIGGER IF NOT EXISTS ai_player_iteration_assessments_no_delete
BEFORE DELETE ON ai_player_iteration_assessments
BEGIN
    SELECT RAISE(ABORT, 'iteration assessments are immutable');
END;
