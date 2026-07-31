"""Fail-closed recovery proofs for blocked AI-player iteration assessments.

Tier-1 recovery is deliberately separate from ordinary iteration samples.  A repair is
allowed to reopen physical execution only after a trusted, non-device regression runner
replays the failed window and an independent holdout partition.  The failed assessment
remains immutable; this module adds a later, signed proof that is bound to it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from statistics import fmean
from typing import Any, Literal, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import ArtifactRef, EvidenceRun, EvidenceStep, NormalizedAction, utc_now
from .contracts import EvidenceReferenceV1, PlayerIterationAssessmentV1


TIER1_REMEDIATION_GATE_ID = "tier1.perception-executor-regression.v1"
TIER1_REMEDIATION_POLICY_VERSION = "player-iteration.v1"
TIER1_MINIMUM_PARTITION_SAMPLES = 10
TIER1_MINIMUM_HOLDOUT_CASES = 2
TIER1_REMEDIATION_THRESHOLDS: dict[str, float | int] = {
    "maximum_policy_violation_count": 0,
    "maximum_invalid_target_execution_count": 0,
    "maximum_incomplete_evidence_count": 0,
    "minimum_expected_change_match_rate": 0.90,
    "minimum_telemetry_coverage_rate": 0.95,
    "minimum_skill_token_reduction_rate": 0.40,
    "minimum_skill_latency_reduction_rate": 0.30,
    "maximum_missing_skill_token_baseline_count": 0,
    "maximum_missing_skill_latency_baseline_count": 0,
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def tier1_remediation_policy_fingerprint() -> str:
    payload = {
        "gate_id": TIER1_REMEDIATION_GATE_ID,
        "policy_version": TIER1_REMEDIATION_POLICY_VERSION,
        "minimum_partition_samples": TIER1_MINIMUM_PARTITION_SAMPLES,
        "minimum_holdout_cases": TIER1_MINIMUM_HOLDOUT_CASES,
        "thresholds": TIER1_REMEDIATION_THRESHOLDS,
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def iteration_assessment_fingerprint(assessment: PlayerIterationAssessmentV1) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(assessment.model_dump(mode="json", by_alias=True))
    ).hexdigest()


class Tier1RemediationRegressionObservationV1(_StrictModel):
    """One raw fixed-runner observation; aggregate metrics are never caller supplied."""

    schema_id: Literal["game-observatory.ai-player.tier1-remediation-observation.v1"] = Field(
        default="game-observatory.ai-player.tier1-remediation-observation.v1",
        alias="schema",
    )
    policy_violation: bool = False
    invalid_target_execution: bool = False
    evidence_complete: bool = True
    expected_change_matched: bool
    token_telemetry_measured: bool
    latency_telemetry_measured: bool
    decision_mode: Literal["planner", "skill_replay", "recovery"] = "planner"
    model_input_tokens: int | None = Field(default=None, ge=0)
    baseline_model_input_tokens: int | None = Field(default=None, gt=0)
    decision_latency_ms: int | None = Field(default=None, ge=0)
    baseline_decision_latency_ms: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def preserve_measured_telemetry(self) -> "Tier1RemediationRegressionObservationV1":
        if self.token_telemetry_measured != (self.model_input_tokens is not None):
            raise ValueError("token measurement flag must match the raw token value")
        if self.latency_telemetry_measured != (self.decision_latency_ms is not None):
            raise ValueError("latency measurement flag must match the raw latency value")
        if self.decision_mode == "skill_replay":
            if self.baseline_model_input_tokens is None:
                raise ValueError("skill replay regression requires a token baseline")
            if self.baseline_decision_latency_ms is None:
                raise ValueError("skill replay regression requires a latency baseline")
        elif (
            self.baseline_model_input_tokens is not None
            or self.baseline_decision_latency_ms is not None
        ):
            raise ValueError("only skill replay regression may contain skill baselines")
        return self


class Tier1RemediationRegressionFixtureV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.tier1-remediation-fixture.v1"] = Field(
        default="game-observatory.ai-player.tier1-remediation-fixture.v1",
        alias="schema",
    )
    fixture_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    failed_assessment_id: str = Field(min_length=1)
    gate_id: Literal[TIER1_REMEDIATION_GATE_ID] = TIER1_REMEDIATION_GATE_ID
    suite_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    partition: Literal["failed_fixture", "holdout"]
    source_sample_ids: list[str] = Field(default_factory=list)
    observations: list[Tier1RemediationRegressionObservationV1] = Field(min_length=1)

    @model_validator(mode="after")
    def preserve_fixture_partition(self) -> "Tier1RemediationRegressionFixtureV1":
        if len(self.source_sample_ids) != len(set(self.source_sample_ids)):
            raise ValueError("fixture source samples must be unique")
        if self.partition == "failed_fixture" and not self.source_sample_ids:
            raise ValueError("failed fixture must bind original failed-window samples")
        if self.partition == "holdout" and self.source_sample_ids:
            raise ValueError("holdout fixture cannot bind failed-window samples")
        return self

    def content_sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json_bytes(self.model_dump(mode="json", by_alias=True))
        ).hexdigest()


class Tier1RemediationCaseMetricsV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.tier1-remediation-case-metrics.v1"] = Field(
        default="game-observatory.ai-player.tier1-remediation-case-metrics.v1",
        alias="schema",
    )
    sample_count: int = Field(ge=1)
    policy_violation_count: int = Field(ge=0)
    invalid_target_execution_count: int = Field(ge=0)
    incomplete_evidence_count: int = Field(ge=0)
    expected_change_match_rate: float = Field(ge=0, le=1)
    token_telemetry_coverage_rate: float = Field(ge=0, le=1)
    latency_telemetry_coverage_rate: float = Field(ge=0, le=1)
    skill_replay_count: int = Field(ge=0)
    missing_skill_token_baseline_count: int = Field(ge=0)
    missing_skill_latency_baseline_count: int = Field(ge=0)
    skill_token_reduction_rate: float | None = Field(default=None, ge=-10, le=1)
    skill_latency_reduction_rate: float | None = Field(default=None, ge=-10, le=1)

    @model_validator(mode="after")
    def require_skill_replay_measurements(self) -> "Tier1RemediationCaseMetricsV1":
        if self.skill_replay_count:
            if self.skill_token_reduction_rate is None:
                raise ValueError("skill replay remediation requires token reduction telemetry")
            if self.skill_latency_reduction_rate is None:
                raise ValueError("skill replay remediation requires latency reduction telemetry")
        elif (
            self.skill_token_reduction_rate is not None
            or self.skill_latency_reduction_rate is not None
        ):
            raise ValueError("skill reduction telemetry requires a skill replay")
        return self

    def hard_gate_passed(self) -> bool:
        thresholds = TIER1_REMEDIATION_THRESHOLDS
        if self.policy_violation_count > thresholds["maximum_policy_violation_count"]:
            return False
        if (
            self.invalid_target_execution_count
            > thresholds["maximum_invalid_target_execution_count"]
        ):
            return False
        if self.incomplete_evidence_count > thresholds["maximum_incomplete_evidence_count"]:
            return False
        if self.expected_change_match_rate < thresholds["minimum_expected_change_match_rate"]:
            return False
        if (
            self.token_telemetry_coverage_rate
            < thresholds["minimum_telemetry_coverage_rate"]
            or self.latency_telemetry_coverage_rate
            < thresholds["minimum_telemetry_coverage_rate"]
        ):
            return False
        if (
            self.missing_skill_token_baseline_count
            > thresholds["maximum_missing_skill_token_baseline_count"]
            or self.missing_skill_latency_baseline_count
            > thresholds["maximum_missing_skill_latency_baseline_count"]
        ):
            return False
        if self.skill_replay_count:
            if (
                self.skill_token_reduction_rate is None
                or self.skill_token_reduction_rate
                < thresholds["minimum_skill_token_reduction_rate"]
            ):
                return False
            if (
                self.skill_latency_reduction_rate is None
                or self.skill_latency_reduction_rate
                < thresholds["minimum_skill_latency_reduction_rate"]
            ):
                return False
        return True


class Tier1RemediationRegressionResultV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.tier1-remediation-result.v1"] = Field(
        default="game-observatory.ai-player.tier1-remediation-result.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    runner_version: Literal["tier1-remediation-runner.v1"] = "tier1-remediation-runner.v1"
    fixture_id: str = Field(min_length=1)
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics: Tier1RemediationCaseMetricsV1
    passed: bool
    generated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def preserve_machine_result(self) -> "Tier1RemediationRegressionResultV1":
        if self.passed != self.metrics.hard_gate_passed():
            raise ValueError("regression result must follow the fixed hard gate")
        return self


def run_tier1_remediation_fixture(
    fixture: Tier1RemediationRegressionFixtureV1,
    *,
    result_id: str,
    generated_at: str,
) -> Tier1RemediationRegressionResultV1:
    """Deterministically recompute tier-1 metrics from raw fixture observations."""

    observations = fixture.observations
    count = len(observations)
    skill_replays = [item for item in observations if item.decision_mode == "skill_replay"]
    token_reductions = [
        (item.baseline_model_input_tokens - item.model_input_tokens)
        / item.baseline_model_input_tokens
        for item in skill_replays
        if item.baseline_model_input_tokens is not None and item.model_input_tokens is not None
    ]
    latency_reductions = [
        (item.baseline_decision_latency_ms - item.decision_latency_ms)
        / item.baseline_decision_latency_ms
        for item in skill_replays
        if item.baseline_decision_latency_ms is not None and item.decision_latency_ms is not None
    ]
    metrics = Tier1RemediationCaseMetricsV1(
        sample_count=count,
        policy_violation_count=sum(item.policy_violation for item in observations),
        invalid_target_execution_count=sum(
            item.invalid_target_execution for item in observations
        ),
        incomplete_evidence_count=sum(not item.evidence_complete for item in observations),
        expected_change_match_rate=sum(item.expected_change_matched for item in observations)
        / count,
        token_telemetry_coverage_rate=sum(
            item.token_telemetry_measured for item in observations
        )
        / count,
        latency_telemetry_coverage_rate=sum(
            item.latency_telemetry_measured for item in observations
        )
        / count,
        skill_replay_count=len(skill_replays),
        missing_skill_token_baseline_count=sum(
            item.baseline_model_input_tokens is None for item in skill_replays
        ),
        missing_skill_latency_baseline_count=sum(
            item.baseline_decision_latency_ms is None for item in skill_replays
        ),
        skill_token_reduction_rate=fmean(token_reductions) if token_reductions else None,
        skill_latency_reduction_rate=(
            fmean(latency_reductions) if latency_reductions else None
        ),
    )
    return Tier1RemediationRegressionResultV1(
        id=result_id,
        fixture_id=fixture.fixture_id,
        fixture_sha256=fixture.content_sha256(),
        metrics=metrics,
        passed=metrics.hard_gate_passed(),
        generated_at=generated_at,
    )


class Tier1RemediationRegressionCaseV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.tier1-remediation-case.v1"] = Field(
        default="game-observatory.ai-player.tier1-remediation-case.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    partition: Literal["failed_fixture", "holdout"]
    fixture_id: str = Field(min_length=1)
    source_sample_ids: list[str] = Field(default_factory=list)
    fixture_artifact_id: str = Field(min_length=1)
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_artifact_id: str = Field(min_length=1)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_run_id: str = Field(min_length=1)
    evidence_step_id: str = Field(min_length=1)
    metrics: Tier1RemediationCaseMetricsV1
    passed: bool

    @field_validator("source_sample_ids")
    @classmethod
    def unique_source_samples(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("remediation source sample ids must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("remediation source sample ids must be unique")
        return value

    @model_validator(mode="after")
    def preserve_partition_and_result(self) -> "Tier1RemediationRegressionCaseV1":
        if self.partition == "failed_fixture" and not self.source_sample_ids:
            raise ValueError("a failed-fixture regression must cite original failed-window samples")
        if self.partition == "holdout" and self.source_sample_ids:
            raise ValueError("a holdout regression cannot reuse failed-window samples")
        if self.passed != self.metrics.hard_gate_passed():
            raise ValueError("remediation case result must follow the fixed tier-1 hard gate")
        return self

    def metrics_sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json_bytes(self.metrics.model_dump(mode="json"))
        ).hexdigest()


class Tier1RemediationAttestationV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.tier1-remediation-attestation.v1"] = Field(
        default="game-observatory.ai-player.tier1-remediation-attestation.v1",
        alias="schema",
    )
    verifier_id: str = Field(min_length=1)
    verifier_run_id: str = Field(min_length=1)
    gate_id: Literal[TIER1_REMEDIATION_GATE_ID] = TIER1_REMEDIATION_GATE_ID
    key_id: str = Field(min_length=1)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_base64: str = Field(min_length=1)


class Tier1RemediationVerificationV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.tier1-remediation-verification.v1"] = Field(
        default="game-observatory.ai-player.tier1-remediation-verification.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    failed_assessment_id: str = Field(min_length=1)
    failed_assessment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_id: Literal[TIER1_REMEDIATION_GATE_ID] = TIER1_REMEDIATION_GATE_ID
    policy_version: Literal[TIER1_REMEDIATION_POLICY_VERSION] = (
        TIER1_REMEDIATION_POLICY_VERSION
    )
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_id: str = Field(min_length=1)
    verifier_session_id: str = Field(min_length=1)
    verification_evidence_run_id: str = Field(min_length=1)
    verification_evidence_step_id: str = Field(min_length=1)
    regression_cases: list[Tier1RemediationRegressionCaseV1] = Field(min_length=3)
    decision: Literal["allow_physical_execution", "remain_blocked"]
    verified_at: str = Field(default_factory=utc_now)
    attestation: Tier1RemediationAttestationV1 | None = None

    @model_validator(mode="after")
    def preserve_fail_closed_decision(self) -> "Tier1RemediationVerificationV1":
        if any(reference.environment_id != self.environment_id for reference in self.evidence_refs):
            raise ValueError("remediation evidence must stay inside its environment")
        case_ids = [item.id for item in self.regression_cases]
        fixture_ids = [item.fixture_id for item in self.regression_cases]
        evidence_pairs = [
            (item.evidence_run_id, item.evidence_step_id) for item in self.regression_cases
        ]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("remediation regression case ids must be unique")
        if len(fixture_ids) != len(set(fixture_ids)):
            raise ValueError("failed fixtures and holdouts must be disjoint")
        if len(evidence_pairs) != len(set(evidence_pairs)):
            raise ValueError("remediation regression cases cannot reuse evidence")
        failed_cases = [item for item in self.regression_cases if item.partition == "failed_fixture"]
        holdouts = [item for item in self.regression_cases if item.partition == "holdout"]
        if not failed_cases:
            raise ValueError("tier-1 remediation requires a failed-fixture partition")
        if len(holdouts) < TIER1_MINIMUM_HOLDOUT_CASES:
            raise ValueError("tier-1 remediation requires at least two independent holdout cases")
        partition_sizes = (
            sum(item.metrics.sample_count for item in failed_cases),
            sum(item.metrics.sample_count for item in holdouts),
        )
        if any(size < TIER1_MINIMUM_PARTITION_SAMPLES for size in partition_sizes):
            raise ValueError("each remediation partition requires at least ten regression samples")
        passed = all(item.passed for item in self.regression_cases)
        expected_decision = "allow_physical_execution" if passed else "remain_blocked"
        if self.decision != expected_decision:
            raise ValueError("remediation decision must follow every hard-gate case result")
        if self.attestation is not None:
            if self.attestation.verifier_id != self.verifier_id:
                raise ValueError("remediation verifier identity must match its attestation")
            if self.attestation.gate_id != self.gate_id:
                raise ValueError("remediation gate must match its attestation")
        return self

    def attestation_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude={"attestation"})

    def payload_sha256(self) -> str:
        return hashlib.sha256(_canonical_json_bytes(self.attestation_payload())).hexdigest()


def stable_tier1_remediation_verification_id(
    *,
    environment_id: str,
    failed_assessment_id: str,
    regression_cases: Sequence[Tier1RemediationRegressionCaseV1],
    verifier_id: str,
    verification_evidence_step_id: str,
) -> str:
    payload = [
        environment_id,
        failed_assessment_id,
        TIER1_REMEDIATION_GATE_ID,
        [item.model_dump(mode="json") for item in regression_cases],
        verifier_id,
        verification_evidence_step_id,
    ]
    return f"tier1.remediation.{hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()[:24]}"


class Tier1RemediationVerifierPublicKeyV1(_StrictModel):
    verifier_id: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    gate_id: Literal[TIER1_REMEDIATION_GATE_ID] = TIER1_REMEDIATION_GATE_ID
    public_key_base64: str = Field(min_length=1)
    status: Literal["active", "revoked"] = "active"


def tier1_remediation_signing_bytes(verification: Tier1RemediationVerificationV1) -> bytes:
    return _canonical_json_bytes(verification.attestation_payload())


class Tier1RemediationSigner:
    """Private-key holder for the isolated regression-verifier process."""

    def __init__(
        self,
        *,
        verifier_id: str,
        verifier_run_id: str,
        key_id: str,
        private_key: Ed25519PrivateKey,
    ) -> None:
        self.verifier_id = verifier_id
        self.verifier_run_id = verifier_run_id
        self.key_id = key_id
        self._private_key = private_key

    @classmethod
    def generate(
        cls,
        *,
        verifier_id: str,
        verifier_run_id: str,
        key_id: str,
    ) -> "Tier1RemediationSigner":
        return cls(
            verifier_id=verifier_id,
            verifier_run_id=verifier_run_id,
            key_id=key_id,
            private_key=Ed25519PrivateKey.generate(),
        )

    def public_identity(self) -> Tier1RemediationVerifierPublicKeyV1:
        raw = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return Tier1RemediationVerifierPublicKeyV1(
            verifier_id=self.verifier_id,
            key_id=self.key_id,
            public_key_base64=base64.b64encode(raw).decode("ascii"),
        )

    def sign(
        self,
        verification: Tier1RemediationVerificationV1,
    ) -> Tier1RemediationVerificationV1:
        if verification.verifier_id != self.verifier_id:
            raise ValueError("remediation verifier identity does not match the signer")
        if verification.attestation is not None:
            raise ValueError("remediation verification is already attested")
        payload = tier1_remediation_signing_bytes(verification)
        attestation = Tier1RemediationAttestationV1(
            verifier_id=self.verifier_id,
            verifier_run_id=self.verifier_run_id,
            key_id=self.key_id,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            signature_base64=base64.b64encode(self._private_key.sign(payload)).decode("ascii"),
        )
        return verification.model_copy(update={"attestation": attestation})


class Tier1RemediationVerifierTrustStore:
    ENV_NAME = "OMNICOMPANY_AI_PLAYER_REMEDIATION_VERIFIER_KEYS_JSON"

    def __init__(
        self,
        identities: Iterable[Tier1RemediationVerifierPublicKeyV1] = (),
    ) -> None:
        self._identities: dict[str, Tier1RemediationVerifierPublicKeyV1] = {}
        for identity in identities:
            if identity.key_id in self._identities:
                raise ValueError(f"duplicate remediation verifier key id: {identity.key_id}")
            self._identities[identity.key_id] = identity

    @classmethod
    def from_environment(cls) -> "Tier1RemediationVerifierTrustStore":
        encoded = os.environ.get(cls.ENV_NAME)
        if not encoded:
            return cls()
        values = json.loads(encoded)
        if not isinstance(values, list):
            raise ValueError(f"{cls.ENV_NAME} must contain a JSON list")
        return cls(Tier1RemediationVerifierPublicKeyV1.model_validate(item) for item in values)

    def verify(self, verification: Tier1RemediationVerificationV1) -> None:
        attestation = verification.attestation
        if attestation is None:
            raise ValueError("remediation verification requires a trusted attestation")
        identity = self._identities.get(attestation.key_id)
        if identity is None or identity.status != "active":
            raise ValueError("remediation verifier key is not an active trust root")
        if identity.verifier_id != verification.verifier_id:
            raise ValueError("remediation verifier identity is not bound to the trusted key")
        if identity.gate_id != verification.gate_id:
            raise ValueError("remediation verifier key is not authorized for this gate")
        payload = tier1_remediation_signing_bytes(verification)
        if attestation.payload_sha256 != hashlib.sha256(payload).hexdigest():
            raise ValueError("remediation attestation payload hash does not match")
        try:
            signature = base64.b64decode(attestation.signature_base64, validate=True)
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(identity.public_key_base64, validate=True)
            )
            public_key.verify(signature, payload)
        except (InvalidSignature, ValueError) as error:
            raise ValueError("remediation verifier signature is invalid") from error


class Tier1RemediationWorkflow:
    """Runnable fixed gate: persist fixtures/results, sign the review, then append it."""

    def __init__(self, player_store: Any, signer: Tier1RemediationSigner) -> None:
        self.player_store = player_store
        self.observatory_store = player_store.observatory_store
        self.signer = signer

    def _save_artifact_once(
        self,
        *,
        artifact_id: str,
        payload: bytes,
        environment_id: str,
        captured_at: str,
        role: str,
    ) -> ArtifactRef:
        sha256 = hashlib.sha256(payload).hexdigest()
        existing = self.observatory_store.get_artifact(artifact_id)
        if existing is not None:
            if existing.sha256 != sha256 or Path(existing.path).read_bytes() != payload:
                raise ValueError(f"remediation artifact id contains different bytes: {artifact_id}")
            return existing
        destination = (
            Path(self.observatory_store.artifact_root)
            / "ai-player-remediation"
            / f"{artifact_id}.json"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        artifact = ArtifactRef(
            id=artifact_id,
            kind="runtime_state",
            path=str(destination),
            sha256=sha256,
            captured_at=captured_at,
            media_type="application/json",
            metadata={
                "environment_id": environment_id,
                "gate_id": TIER1_REMEDIATION_GATE_ID,
                "remediation_role": role,
                "immutable": True,
            },
        )
        self.observatory_store.save_artifact(artifact)
        return artifact

    def _save_evidence_once(self, run: EvidenceRun, step: EvidenceStep) -> None:
        existing_run = self.observatory_store.get_evidence_run(run.id)
        existing_step = self.observatory_store.get_evidence_step(step.id)
        if existing_run is not None and existing_run != run:
            raise ValueError(f"remediation evidence run id contains different content: {run.id}")
        if existing_step is not None and existing_step != step:
            raise ValueError(f"remediation evidence step id contains different content: {step.id}")
        if existing_run is None:
            self.observatory_store.save_evidence_run(run)
        if existing_step is None:
            self.observatory_store.save_evidence_step(step)

    def verify_and_append(
        self,
        *,
        environment_id: str,
        failed_assessment_id: str,
        fixtures: Sequence[Tier1RemediationRegressionFixtureV1],
        verifier_session_id: str,
        verified_at: str | None = None,
    ) -> Tier1RemediationVerificationV1:
        """Run both partitions and create the only durable tier-1 resume proof."""

        from .session_control import (
            AIPlayerSessionControl,
            CreateAIPlayerSessionCommand,
        )

        assessment = self.player_store.get_iteration_assessment(
            environment_id,
            failed_assessment_id,
        )
        if assessment is None:
            raise ValueError("tier-1 remediation assessment does not exist")
        if not fixtures:
            raise ValueError("tier-1 remediation requires regression fixtures")
        if any(
            fixture.environment_id != environment_id
            or fixture.failed_assessment_id != failed_assessment_id
            for fixture in fixtures
        ):
            raise ValueError("remediation fixture targets another environment or assessment")
        if len({fixture.suite_manifest_sha256 for fixture in fixtures}) != 1:
            raise ValueError("failed fixtures and holdouts must come from one sealed suite manifest")
        timestamp = verified_at or utc_now()
        sessions = AIPlayerSessionControl(self.player_store)
        verifier_session = sessions.get_session(environment_id, verifier_session_id)
        if verifier_session is None:
            sessions.create_session(
                CreateAIPlayerSessionCommand(
                    command_id=f"{verifier_session_id}.create",
                    requested_environment_id=environment_id,
                    objective="Run isolated tier-1 remediation regression verification",
                    action_budget=0,
                    token_budget=0,
                    time_budget_seconds=600,
                    actor=self.signer.verifier_id,
                    reason="Bind the independent non-device remediation verifier",
                    session_id=verifier_session_id,
                    last_evidence_refs=assessment.evidence_refs,
                )
            )

        planned: list[tuple[
            Tier1RemediationRegressionFixtureV1,
            Tier1RemediationRegressionResultV1,
            ArtifactRef,
            ArtifactRef,
            Tier1RemediationRegressionCaseV1,
        ]] = []
        for fixture in fixtures:
            fixture_payload = _canonical_json_bytes(
                fixture.model_dump(mode="json", by_alias=True)
            )
            fixture_digest = hashlib.sha256(fixture_payload).hexdigest()
            fixture_artifact = self._save_artifact_once(
                artifact_id=f"remediation.fixture.{fixture_digest[:24]}",
                payload=fixture_payload,
                environment_id=environment_id,
                captured_at=timestamp,
                role="regression_fixture",
            )
            result_id = f"remediation.result.{fixture_digest[:24]}"
            result = run_tier1_remediation_fixture(
                fixture,
                result_id=result_id,
                generated_at=timestamp,
            )
            result_payload = _canonical_json_bytes(
                result.model_dump(mode="json", by_alias=True)
            )
            result_artifact = self._save_artifact_once(
                artifact_id=result_id,
                payload=result_payload,
                environment_id=environment_id,
                captured_at=timestamp,
                role="regression_result",
            )
            case_hash = hashlib.sha256(
                _canonical_json_bytes(
                    [fixture.fixture_id, fixture_artifact.id, result_artifact.id]
                )
            ).hexdigest()[:24]
            case = Tier1RemediationRegressionCaseV1(
                id=f"remediation.case.{case_hash}",
                partition=fixture.partition,
                fixture_id=fixture.fixture_id,
                source_sample_ids=fixture.source_sample_ids,
                fixture_artifact_id=fixture_artifact.id,
                fixture_sha256=fixture_artifact.sha256,
                result_artifact_id=result_artifact.id,
                result_sha256=result_artifact.sha256,
                evidence_run_id=f"remediation.case-run.{case_hash}",
                evidence_step_id=f"remediation.case-step.{case_hash}",
                metrics=result.metrics,
                passed=result.passed,
            )
            planned.append((fixture, result, fixture_artifact, result_artifact, case))

        cases = [item[4] for item in planned]
        verifier_hash = hashlib.sha256(
            _canonical_json_bytes(
                [environment_id, failed_assessment_id, [case.id for case in cases], self.signer.verifier_id]
            )
        ).hexdigest()[:24]
        verification_run_id = self.signer.verifier_run_id
        verification_step_id = f"remediation.verifier-step.{verifier_hash}"
        verification_id = stable_tier1_remediation_verification_id(
            environment_id=environment_id,
            failed_assessment_id=failed_assessment_id,
            regression_cases=cases,
            verifier_id=self.signer.verifier_id,
            verification_evidence_step_id=verification_step_id,
        )

        environment = self.player_store.get_environment(environment_id)
        if environment is None:
            raise ValueError("tier-1 remediation environment does not exist")
        for _fixture, _result, fixture_artifact, result_artifact, case in planned:
            terminal_status = "passed" if case.passed else "failed"
            case_run = EvidenceRun(
                id=case.evidence_run_id,
                target_id=environment.device_scope_id,
                adapter="ai-player-tier1-remediation-regression-v1",
                status=terminal_status,
                game_id=environment.game_id,
                build_scope_id=environment.build_scope_id,
                scope_id=environment_id,
                viewport_width=environment.viewport_width,
                viewport_height=environment.viewport_height,
                orientation=(
                    "landscape"
                    if environment.viewport_width > environment.viewport_height
                    else "portrait"
                    if environment.viewport_width < environment.viewport_height
                    else "square"
                ),
                environment={"environment_id": environment_id, "non_device": True},
                started_at=timestamp,
                ended_at=timestamp,
                step_ids=[case.evidence_step_id],
                artifact_ids=[fixture_artifact.id, result_artifact.id],
            )
            case_step = EvidenceStep(
                id=case.evidence_step_id,
                evidence_run_id=case.evidence_run_id,
                step_index=1,
                status=terminal_status,
                started_at=timestamp,
                ended_at=timestamp,
                action=NormalizedAction(type="wait", seconds=0),
                viewport_width=environment.viewport_width,
                viewport_height=environment.viewport_height,
                artifact_ids=[fixture_artifact.id, result_artifact.id],
                metadata={
                    "tier1_remediation_case": {
                        "schema": "game-observatory.ai-player.tier1-remediation-case-evidence.v1",
                        "environment_id": environment_id,
                        "verification_id": verification_id,
                        "failed_assessment_id": failed_assessment_id,
                        "gate_id": TIER1_REMEDIATION_GATE_ID,
                        "case_id": case.id,
                        "partition": case.partition,
                        "fixture_id": case.fixture_id,
                        "metrics_sha256": case.metrics_sha256(),
                        "policy_sha256": tier1_remediation_policy_fingerprint(),
                    }
                },
            )
            self._save_evidence_once(case_run, case_step)

        all_artifact_ids = [
            artifact.id
            for _fixture, _result, fixture_artifact, result_artifact, _case in planned
            for artifact in (fixture_artifact, result_artifact)
        ]
        all_case_run_ids = [case.evidence_run_id for case in cases]
        all_case_step_ids = [case.evidence_step_id for case in cases]
        evidence_reference = EvidenceReferenceV1(
            environment_id=environment_id,
            artifact_ids=all_artifact_ids,
            evidence_run_ids=[*all_case_run_ids, verification_run_id],
            evidence_step_ids=[*all_case_step_ids, verification_step_id],
            note="Sealed machine regression fixtures, results, and independent verifier proof.",
        )
        unsigned = Tier1RemediationVerificationV1(
            id=verification_id,
            environment_id=environment_id,
            evidence_refs=[evidence_reference],
            failed_assessment_id=failed_assessment_id,
            failed_assessment_sha256=iteration_assessment_fingerprint(assessment),
            policy_sha256=tier1_remediation_policy_fingerprint(),
            verifier_id=self.signer.verifier_id,
            verifier_session_id=verifier_session_id,
            verification_evidence_run_id=verification_run_id,
            verification_evidence_step_id=verification_step_id,
            regression_cases=cases,
            decision=(
                "allow_physical_execution"
                if all(case.passed for case in cases)
                else "remain_blocked"
            ),
            verified_at=timestamp,
        )
        verifier_run = EvidenceRun(
            id=verification_run_id,
            target_id=environment.device_scope_id,
            adapter="ai-player-tier1-remediation-verifier-v1",
            status="passed",
            game_id=environment.game_id,
            build_scope_id=environment.build_scope_id,
            scope_id=environment_id,
            viewport_width=environment.viewport_width,
            viewport_height=environment.viewport_height,
            orientation=(
                "landscape"
                if environment.viewport_width > environment.viewport_height
                else "portrait"
                if environment.viewport_width < environment.viewport_height
                else "square"
            ),
            environment={"environment_id": environment_id, "non_device": True},
            started_at=timestamp,
            ended_at=timestamp,
            step_ids=[verification_step_id],
        )
        verifier_step = EvidenceStep(
            id=verification_step_id,
            evidence_run_id=verification_run_id,
            step_index=1,
            status="passed",
            started_at=timestamp,
            ended_at=timestamp,
            action=NormalizedAction(type="wait", seconds=0),
            viewport_width=environment.viewport_width,
            viewport_height=environment.viewport_height,
            metadata={
                "tier1_remediation_verification": {
                    "schema": "game-observatory.ai-player.tier1-remediation-verifier-evidence.v1",
                    "environment_id": environment_id,
                    "verification_id": verification_id,
                    "failed_assessment_id": failed_assessment_id,
                    "gate_id": TIER1_REMEDIATION_GATE_ID,
                    "verifier_id": self.signer.verifier_id,
                    "payload_sha256": unsigned.payload_sha256(),
                    "decision": unsigned.decision,
                }
            },
        )
        self._save_evidence_once(verifier_run, verifier_step)
        signed = self.signer.sign(unsigned)
        existing = self.player_store.get_tier1_remediation_verification(
            environment_id,
            verification_id,
        )
        if existing is not None:
            if existing != signed:
                raise ValueError("remediation verification id contains different signed content")
            self.player_store.validate_tier1_remediation_verification(existing)
            return existing
        return self.player_store.append_tier1_remediation_verification(signed)


class RemediationStoreProtocol(Protocol):
    def list_tier1_remediation_verifications(
        self,
        environment_id: str,
        *,
        failed_assessment_id: str | None = None,
        limit: int = 20,
    ) -> list[Tier1RemediationVerificationV1]: ...

    def validate_tier1_remediation_verification(
        self,
        verification: Tier1RemediationVerificationV1,
    ) -> None: ...


class IterationRemediationGateV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.iteration-remediation-gate.v1"] = Field(
        default="game-observatory.ai-player.iteration-remediation-gate.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    assessment_id: str | None = Field(default=None, min_length=1)
    directive: str = Field(min_length=1)
    status: Literal["open", "blocked", "remediated", "unsupported_fail_closed"]
    remediated_tier: int | None = Field(default=None, ge=1, le=4)
    verification_id: str | None = Field(default=None, min_length=1)
    handler: str = Field(min_length=1)
    required_work_item: str | None = Field(default=None, min_length=1)
    reason: str = Field(min_length=1)


def resolve_iteration_remediation_gate(
    player_store: RemediationStoreProtocol,
    *,
    environment_id: str,
    assessment: PlayerIterationAssessmentV1 | None,
) -> IterationRemediationGateV1:
    if assessment is None or assessment.directive == "continue":
        return IterationRemediationGateV1(
            environment_id=environment_id,
            assessment_id=assessment.id if assessment else None,
            directive=assessment.directive if assessment else "continue",
            status="open",
            handler="continue_physical_execution",
            reason="No unresolved iteration directive blocks physical execution.",
        )
    if assessment.directive == "pause_physical_and_repair_perception_executor":
        tier1 = assessment.tiers[0]
        metrics = tier1.metrics
        thresholds = tier1.thresholds
        safety_checks_pass = all(
            (
                metrics.get("sample_count", 0)
                >= thresholds.get("minimum_sample_count", 1),
                metrics.get("executed_count", 0) > 0,
                metrics.get("policy_violation_count", 0)
                <= thresholds.get("maximum_policy_violation_count", 0),
                metrics.get("invalid_target_execution_count", 0)
                <= thresholds.get("maximum_invalid_target_execution_count", 0),
                metrics.get("incomplete_evidence_count", 0)
                <= thresholds.get("maximum_incomplete_evidence_count", 0),
                metrics.get("expected_change_match_rate", 0.0)
                >= thresholds.get("minimum_expected_change_match_rate", 1.0),
                metrics.get("expected_change_measurement_coverage_rate", 0.0)
                >= thresholds.get(
                    "minimum_expected_change_measurement_coverage_rate",
                    1.0,
                ),
            )
        )
        minimum_telemetry_coverage = thresholds.get(
            "minimum_telemetry_coverage_rate",
            1.0,
        )
        telemetry_gap = any(
            (
                metrics.get("token_telemetry_coverage_rate", 0.0)
                < minimum_telemetry_coverage,
                metrics.get("latency_telemetry_coverage_rate", 0.0)
                < minimum_telemetry_coverage,
            )
        )
        no_skill_efficiency_failure = all(
            (
                metrics.get("skill_replay_count", 0) == 0,
                metrics.get("missing_skill_token_baseline_count", 0) == 0,
                metrics.get("missing_skill_latency_baseline_count", 0) == 0,
                metrics.get("skill_token_reduction_rate") is None,
                metrics.get("skill_latency_reduction_rate") is None,
            )
        )
        if safety_checks_pass and telemetry_gap and no_skill_efficiency_failure:
            return IterationRemediationGateV1(
                environment_id=environment_id,
                assessment_id=assessment.id,
                directive=assessment.directive,
                status="open",
                remediated_tier=1,
                handler="continue_instrumented_execution_and_reassess_after_10_actions",
                required_work_item="quality_remediation.tier1.telemetry_coverage",
                reason=(
                    "Physical execution remains safe because policy, target, evidence, and "
                    "expected-change checks passed. Continue with repaired telemetry and "
                    "reassess after the next ten actions."
                ),
            )
        verifications = player_store.list_tier1_remediation_verifications(
            environment_id,
            failed_assessment_id=assessment.id,
        )
        for verification in verifications:
            try:
                player_store.validate_tier1_remediation_verification(verification)
            except ValueError:
                continue
            if verification.decision == "allow_physical_execution":
                return IterationRemediationGateV1(
                    environment_id=environment_id,
                    assessment_id=assessment.id,
                    directive=assessment.directive,
                    status="remediated",
                    remediated_tier=1,
                    verification_id=verification.id,
                    handler="tier1_signed_regression_verification",
                    reason=(
                        "The immutable failed assessment is superseded for execution gating by "
                        "a valid failed-fixture and holdout regression verification."
                    ),
                )
        return IterationRemediationGateV1(
            environment_id=environment_id,
            assessment_id=assessment.id,
            directive=assessment.directive,
            status="blocked",
            remediated_tier=1,
            handler="tier1_signed_regression_verification",
            required_work_item="quality_remediation.tier1.perception_executor",
            reason="Tier-1 remains blocked until a valid signed regression verification passes.",
        )
    handlers = {
        "shadow_only": (
            "collect_shadow_evidence",
            "quality_remediation.shadow_evidence",
        ),
        "revise_planner_and_task_policy": (
            "tier2_planner_policy_remediation_not_implemented",
            "quality_remediation.tier2.planner_policy",
        ),
        "refresh_guides_and_reprioritize_objectives": (
            "tier3_guide_objective_remediation_not_implemented",
            "quality_remediation.tier3.guide_objectives",
        ),
        "expand_discovery_frontier": (
            "tier4_frontier_remediation_not_implemented",
            "quality_remediation.tier4.discovery_frontier",
        ),
    }
    handler, work_item = handlers[assessment.directive]
    return IterationRemediationGateV1(
        environment_id=environment_id,
        assessment_id=assessment.id,
        directive=assessment.directive,
        status="unsupported_fail_closed",
        handler=handler,
        required_work_item=work_item,
        reason="This directive has no verified remediation handler and therefore stays blocked.",
    )


__all__ = [
    "IterationRemediationGateV1",
    "TIER1_MINIMUM_HOLDOUT_CASES",
    "TIER1_MINIMUM_PARTITION_SAMPLES",
    "TIER1_REMEDIATION_GATE_ID",
    "TIER1_REMEDIATION_POLICY_VERSION",
    "TIER1_REMEDIATION_THRESHOLDS",
    "Tier1RemediationAttestationV1",
    "Tier1RemediationCaseMetricsV1",
    "Tier1RemediationRegressionCaseV1",
    "Tier1RemediationSigner",
    "Tier1RemediationVerificationV1",
    "Tier1RemediationVerifierPublicKeyV1",
    "Tier1RemediationVerifierTrustStore",
    "iteration_assessment_fingerprint",
    "resolve_iteration_remediation_gate",
    "stable_tier1_remediation_verification_id",
    "tier1_remediation_policy_fingerprint",
    "tier1_remediation_signing_bytes",
]
