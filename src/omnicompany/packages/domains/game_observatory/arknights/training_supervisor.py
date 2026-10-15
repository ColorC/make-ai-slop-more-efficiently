# [OMNI] origin=codex created_by=codex intent="gate Arknights training with hypothesis-result evaluation and canonical AI-player evidence"
"""Evidence-bound supervision for tournament training attempts.

The supervisor is a control-plane adapter.  It never observes the device and
never issues input.  Domain attempt records live in one append-only hash chain;
actions, planner measurements, tasks and iteration assessments remain owned by
the canonical :mod:`ai_player` store.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from ..ai_player.contracts import (
    ActionQualitySampleV1,
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    FrontierTaskV1,
    SessionCapsuleV1,
)
from ..ai_player.iteration_monitor import PlayerIterationMonitor
from ..ai_player.planner_measurement import PlannerMeasurementReceiptV1
from ..ai_player.remediation import resolve_iteration_remediation_gate
from ..ai_player.session_control import (
    AIPlayerSessionControl,
    CreateAIPlayerSessionCommand,
)
from ..ai_player.store import AIPlayerStore
from ..models import (
    ArtifactRef,
    EvidenceRun,
    EvidenceStability,
    EvidenceStep,
    NormalizedAction,
    RunResult,
    utc_now,
)
from .training_evidence import CERTIFICATE_SCHEMA
from .training_planner_measurement import (
    COLLECTION_SCHEMA,
    ReceiptLinkV1,
    TrainingPlannerMeasurementCollectionV1,
)


LEDGER_SCHEMA = "omnicompany.arknights.training-supervision-ledger.v1"
HYPOTHESIS_SCHEMA = "omnicompany.arknights.training-hypothesis.v1"
RESULT_SCHEMA = "omnicompany.arknights.training-attempt-result.v1"
BATCH_SCHEMA = "omnicompany.arknights.training-supervision-batch.v1"
GATE_SCHEMA = "omnicompany.arknights.training-supervision-gate.v1"
ENVIRONMENT_RECEIPT_SCHEMA = "omnicompany.arknights.training-environment-receipt.v1"
ACTION_OBSERVATION_SCHEMA = "omnicompany.arknights.training-semantic-action.v1"
CANCELLATION_SCHEMA = "omnicompany.arknights.training-attempt-cancellation.v1"
ABORT_SCHEMA = "omnicompany.arknights.training-attempt-abort.v1"
INTERRUPTED_METRICS_SCHEMA = (
    "omnicompany.arknights.interrupted-attempt-metrics-unavailable.v1"
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class TrainingIdentityV1(_StrictModel):
    run_id: str = Field(min_length=1)
    slot_id: str = Field(min_length=1)
    theme_id: str = Field(min_length=1)


class TrainingEnvironmentReceiptV1(_StrictModel):
    schema_id: Literal["omnicompany.arknights.training-environment-receipt.v1"] = Field(
        default=ENVIRONMENT_RECEIPT_SCHEMA,
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    game_id: Literal["arknights"] = "arknights"
    build_scope_id: str = Field(min_length=1)
    account_scope_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    device_scope_id: str = Field(min_length=1)
    locale: str = Field(min_length=1)
    viewport_width: int = Field(gt=0)
    viewport_height: int = Field(gt=0)
    source: str = Field(min_length=1)
    captured_at: str = Field(min_length=1)


class TrainingRemediationCheckV1(_StrictModel):
    """One explicit positive or negative mechanical regression check."""

    id: StrictStr = Field(min_length=1)
    polarity: Literal["positive", "negative"]
    name: StrictStr = Field(min_length=1)
    expected: StrictStr = Field(min_length=1)
    observed: StrictStr = Field(min_length=1)
    evidence_channel: Literal["stdout", "stderr", "structured_output"]
    evidence_excerpt: StrictStr = Field(min_length=1)
    passed: bool


class TrainingRemediationVerificationV1(_StrictModel):
    """Remote, non-device remediation proof for a domain training gate."""

    schema_id: Literal[
        "omnicompany.arknights.training-remediation-verification.v1"
    ] = Field(
        default="omnicompany.arknights.training-remediation-verification.v1",
        alias="schema",
    )
    id: StrictStr = Field(min_length=1)
    source_attempt_id: StrictStr = Field(min_length=1)
    environment_id: StrictStr = Field(min_length=1)
    required_work_item: StrictStr = Field(min_length=1)
    remote_node: StrictStr = Field(min_length=1)
    remote_task_id: StrictStr = Field(min_length=1)
    controller_task_receipt_path: StrictStr = Field(min_length=1)
    controller_task_receipt_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    controller_task_receipt_artifact_id: StrictStr = Field(min_length=1)
    regression_command: list[StrictStr] = Field(min_length=1)
    exit_code: int
    status: Literal["passed", "failed"]
    duration_seconds: float = Field(ge=0)
    output_artifact_path: StrictStr = Field(min_length=1)
    output_artifact_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    output_artifact_id: StrictStr = Field(min_length=1)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    checks: list[TrainingRemediationCheckV1] = Field(min_length=2)
    passed: bool
    verified_at: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def derive_measured_pass(self) -> "TrainingRemediationVerificationV1":
        ids = [item.id for item in self.checks]
        if len(ids) != len(set(ids)):
            raise ValueError("remediation checks must be unique")
        if {item.polarity for item in self.checks} != {"positive", "negative"}:
            raise ValueError("remediation requires explicit positive and negative checks")
        measured_pass = self.exit_code == 0 and all(item.passed for item in self.checks)
        if self.passed != measured_pass:
            raise ValueError("remediation passed must derive from exit code and all checks")
        expected_status = "passed" if measured_pass else "failed"
        if self.status != expected_status:
            raise ValueError("remediation status must derive from measured checks")
        try:
            verified_at = datetime.fromisoformat(self.verified_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("remediation verified_at must be ISO-8601") from error
        if verified_at.tzinfo is None or verified_at.utcoffset() is None:
            raise ValueError("remediation verified_at must include a timezone")
        if verified_at > datetime.now(timezone.utc):
            raise ValueError("remediation verification cannot be in the future")
        if any(reference.environment_id != self.environment_id for reference in self.evidence_refs):
            raise ValueError("remediation evidence must stay inside its environment")
        if self.output_artifact_id == self.controller_task_receipt_artifact_id:
            raise ValueError("remediation output and controller receipt must be distinct artifacts")
        if not any(
            self.controller_task_receipt_artifact_id in reference.artifact_ids
            for reference in self.evidence_refs
        ):
            raise ValueError("controller task receipt artifact must be in remediation evidence")
        if not any(
            self.output_artifact_id in reference.artifact_ids
            for reference in self.evidence_refs
        ):
            raise ValueError("remediation output artifact must be in remediation evidence")
        return self


class ChangedVariableV1(_StrictModel):
    """Exactly one controlled difference from the parent attempt."""

    name: str = Field(min_length=1, max_length=120)
    before: str | None = Field(default=None, max_length=1000)
    after: str = Field(min_length=1, max_length=1000)


class TrainingHypothesisV1(_StrictModel):
    schema_id: Literal["omnicompany.arknights.training-hypothesis.v1"] = Field(
        default=HYPOTHESIS_SCHEMA,
        alias="schema",
    )
    attempt_id: str = Field(min_length=1)
    parent_attempt_id: str | None = Field(default=None, min_length=1)
    identity: TrainingIdentityV1
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    direction: str = Field(min_length=8, max_length=4000)
    changed_variable: ChangedVariableV1
    expected_outcome: str = Field(min_length=8, max_length=4000)
    falsification_criterion: str = Field(min_length=8, max_length=4000)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    client_version: str = Field(min_length=1)
    score_table: str = Field(min_length=1)
    action_budget: int = Field(ge=1)
    token_budget: int | None = Field(default=None, ge=0)
    time_budget_seconds: float = Field(gt=0)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def keep_evidence_in_environment(self) -> "TrainingHypothesisV1":
        if any(ref.environment_id != self.environment_id for ref in self.evidence_refs):
            raise ValueError("hypothesis evidence must remain inside its AI-player environment")
        if self.parent_attempt_id == self.attempt_id:
            raise ValueError("an attempt cannot be its own parent")
        return self


class TrainingUsageV1(_StrictModel):
    measurement_status: Literal["measured", "partial", "unavailable"]
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_creation_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def preserve_missing_usage(self) -> "TrainingUsageV1":
        values = (
            self.input_tokens,
            self.cached_input_tokens,
            self.cache_creation_input_tokens,
            self.output_tokens,
            self.reasoning_tokens,
            self.cost,
        )
        if self.measurement_status == "unavailable" and any(value is not None for value in values):
            raise ValueError("unavailable usage cannot contain invented zero or measured values")
        if self.measurement_status == "measured" and any(value is None for value in values[:-1]):
            raise ValueError("measured token usage requires every token component")
        if self.measurement_status == "partial" and not any(value is not None for value in values):
            raise ValueError("partial usage requires at least one measured component")
        return self


class MissingAttemptManifestV1(_StrictModel):
    """One worker invocation whose canonical completion manifest was never written."""

    invocation_id: str = Field(min_length=1)
    expected_manifest_path: str = Field(min_length=1)
    invocation_artifact_path: str = Field(min_length=1)
    invocation_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=8, max_length=1000)


class InterruptedAttemptMetricsV1(_StrictModel):
    """Evidence for preserving unknown cost/usage after an interrupted attempt."""

    schema_id: Literal[
        "omnicompany.arknights.interrupted-attempt-metrics-unavailable.v1"
    ] = Field(default=INTERRUPTED_METRICS_SCHEMA, alias="schema")
    attempt_id: str = Field(min_length=1)
    identity: TrainingIdentityV1
    reason: str = Field(min_length=8, max_length=2000)
    runner_log_path: str = Field(min_length=1)
    runner_log_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    missing_attempt_manifests: list[MissingAttemptManifestV1] = Field(min_length=1)

    @model_validator(mode="after")
    def keep_gaps_unique(self) -> "InterruptedAttemptMetricsV1":
        gaps = self.missing_attempt_manifests
        for field in (
            "invocation_id",
            "expected_manifest_path",
            "invocation_artifact_path",
        ):
            values = [getattr(item, field) for item in gaps]
            if len(values) != len(set(values)):
                raise ValueError("interrupted attempt manifest gaps must be unique")
        return self


class TrainingSemanticActionV1(_StrictModel):
    """One exact, screenshot-free semantic action receipt from a training journal."""

    schema_id: Literal["omnicompany.arknights.training-semantic-action.v1"] = Field(
        default=ACTION_OBSERVATION_SCHEMA,
        alias="schema",
    )
    id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    identity: TrainingIdentityV1
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    source_artifact_id: str = Field(min_length=1)
    journal_path: str = Field(min_length=1)
    journal_bytes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    journal_seq: int = Field(ge=1)
    journal_row_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    timestamp: str = Field(min_length=1)
    semantic_action: str = Field(min_length=1)
    verified_success: bool
    exploration_id: str = Field(min_length=1)
    intent_id: str = Field(min_length=1)
    evidence_receipt: str = Field(min_length=1)
    evidence_binding: dict[str, Any]
    effect_proof: dict[str, Any]
    safety_channel: dict[str, Any]
    facility_duration_ms: float = Field(ge=0)
    safety_available: bool
    safety_fresh: bool
    operator: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_one_verified_effect_binding(self) -> "TrainingSemanticActionV1":
        binding = (self.exploration_id, self.intent_id, self.evidence_receipt)
        for payload in (
            self.evidence_binding,
            self.effect_proof,
            self.safety_channel,
        ):
            if tuple(
                str(payload.get(key) or "").strip()
                for key in ("exploration_id", "intent_id", "evidence_receipt")
            ) != binding:
                raise ValueError("semantic action proof crosses its canonical binding")
        monotonic = [
            self.safety_channel.get(key)
            for key in (
                "sample_requested_monotonic_ns",
                "sample_received_monotonic_ns",
                "receipt_created_monotonic_ns",
            )
        ]
        try:
            safety_at = datetime.fromisoformat(
                str(self.safety_channel.get("captured_at") or "").replace(
                    "Z", "+00:00"
                )
            )
            action_at = datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("semantic action safety timestamp is invalid") from exc
        if safety_at.tzinfo is None or action_at.tzinfo is None:
            raise ValueError("semantic action safety timestamp must include a timezone")
        expected = self.effect_proof.get("expected")
        actual = self.effect_proof.get("actual")
        predicate = (
            str(expected.get("predicate") or "").strip()
            if isinstance(expected, Mapping)
            else ""
        )
        semantic_effect_proved = False
        if isinstance(actual, Mapping) and actual.get("satisfied") is True:
            if predicate == "semantic-surface-changed":
                before = str(actual.get("before_semantic_sha256") or "")
                after = str(actual.get("after_semantic_sha256") or "")
                semantic_effect_proved = bool(
                    re.fullmatch(r"[0-9a-f]{64}", before)
                    and re.fullmatch(r"[0-9a-f]{64}", after)
                    and before != after
                )
            elif predicate == "any-semantic-marker":
                required = set(expected.get("required_markers") or ())
                observed = set(actual.get("observed_markers") or ())
                semantic_effect_proved = bool(required and required & observed)
            elif predicate == "active-run-surface":
                semantic_effect_proved = actual.get("active_run_surface") is True
            elif predicate == "route-map-visible":
                semantic_effect_proved = actual.get("route_map_visible") is True
        if (
            not self.verified_success
            or not self.safety_available
            or not self.safety_fresh
            or self.safety_channel.get("input_sent") is not True
            or not str(self.effect_proof.get("kind") or "").strip()
            or not isinstance(expected, Mapping)
            or not isinstance(actual, Mapping)
            or expected.get("kind") != self.effect_proof.get("kind")
            or "kind" in actual
            or not semantic_effect_proved
            or not str(self.safety_channel.get("source") or "").strip()
            or not str(self.safety_channel.get("captured_at") or "").strip()
            or len(monotonic) != 3
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in monotonic
            )
            or monotonic != sorted(monotonic)
            or safety_at > action_at
            or (action_at - safety_at).total_seconds() > 120
        ):
            raise ValueError("semantic action lacks verified effect/safety evidence")
        return self


class TrainingAttemptResultV1(_StrictModel):
    schema_id: Literal["omnicompany.arknights.training-attempt-result.v1"] = Field(
        default=RESULT_SCHEMA,
        alias="schema",
    )
    attempt_id: str = Field(min_length=1)
    hypothesis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity: TrainingIdentityV1
    classification: Literal["technical_void", "unresolved", "natural_loss", "clear"]
    failure_axis: Literal[
        "none",
        "facility",
        "perception",
        "planner",
        "policy",
        "execution",
        "combat",
        "evidence",
        "unknown",
    ]
    observed_outcome: str = Field(min_length=1, max_length=8000)
    hypothesis_verdict: Literal["supported", "falsified", "inconclusive"]
    decision: Literal["continue", "revise", "retire", "repair"]
    decision_reason: str = Field(min_length=8, max_length=4000)
    next_direction: str | None = Field(default=None, min_length=8, max_length=4000)
    reached_floor: int | None = Field(default=None, ge=0)
    wall_time_seconds: float | None = Field(default=None, ge=0)
    active_time_seconds: float | None = Field(default=None, ge=0)
    external_wait_seconds: float | None = Field(default=None, ge=0)
    model_roundtrips: int | None = Field(ge=0)
    usage: TrainingUsageV1 | None
    interrupted_metrics: InterruptedAttemptMetricsV1 | None = None
    certificate_path: str = Field(min_length=1)
    certificate_bytes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_sample_ids: list[StrictStr] = Field(default_factory=list)
    semantic_action_ids: list[StrictStr] = Field(default_factory=list)
    planner_collection_artifact_id: StrictStr | None = Field(default=None, min_length=1)
    planner_collection_path: StrictStr | None = Field(default=None, min_length=1)
    planner_collection_sha256: StrictStr | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    planner_receipt_links: list[ReceiptLinkV1] = Field(default_factory=list)
    planner_receipt_ids: list[StrictStr] = Field(default_factory=list)
    iteration_assessment_id: StrictStr | None = Field(default=None, min_length=1)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    finished_at: str = Field(default_factory=utc_now)

    @field_validator("action_sample_ids", "semantic_action_ids", "planner_receipt_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("result evidence ids must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("result ids must not contain duplicates")
        return value

    @model_validator(mode="after")
    def preserve_classification_semantics(self) -> "TrainingAttemptResultV1":
        timings = (
            self.wall_time_seconds,
            self.active_time_seconds,
            self.external_wait_seconds,
        )
        missing_timings = [value is None for value in timings]
        if any(missing_timings) and not all(missing_timings):
            raise ValueError("attempt timing metrics must be wholly measured or unavailable")
        if all(missing_timings):
            if (
                self.classification != "technical_void"
                or self.model_roundtrips is not None
                or self.usage is not None
                or self.interrupted_metrics is None
            ):
                raise ValueError(
                    "only a technical void with interrupted evidence may preserve unavailable metrics"
                )
        elif self.interrupted_metrics is not None:
            raise ValueError("measured metrics cannot carry an interrupted-unavailable receipt")
        elif self.usage is None:
            raise ValueError("nullable usage requires an interrupted-unavailable receipt")
        elif (
            self.active_time_seconds is not None
            and self.external_wait_seconds is not None
            and self.wall_time_seconds is not None
            and self.active_time_seconds + self.external_wait_seconds
            > self.wall_time_seconds + 1
        ):
            raise ValueError("active plus external wait time cannot exceed wall time")
        if self.classification in {"natural_loss", "clear"} and (
            any(missing_timings)
            or self.model_roundtrips is None
            or self.usage is None
            or self.usage.measurement_status != "measured"
        ):
            raise ValueError("nontechnical results require measured metrics")
        if self.classification == "technical_void" and (
            self.failure_axis != "facility" or self.decision != "repair"
        ):
            raise ValueError("technical voids require facility attribution and repair")
        if self.classification == "unresolved" and self.hypothesis_verdict != "inconclusive":
            raise ValueError("unresolved evidence cannot decide a hypothesis")
        if self.decision in {"continue", "revise"} and not self.next_direction:
            raise ValueError("continuation requires an explicit next direction")
        if self.classification in {"natural_loss", "clear"} and not self.action_sample_ids:
            raise ValueError(
                "natural_loss and clear require canonical action-quality sample ids"
            )
        planner_fields = (
            self.planner_collection_artifact_id,
            self.planner_collection_path,
            self.planner_collection_sha256,
        )
        if any(planner_fields) and not all(planner_fields):
            raise ValueError("planner collection artifact id, path, and hash are indivisible")
        linked_receipt_ids = [item.receipt_id for item in self.planner_receipt_links]
        if self.planner_receipt_ids != linked_receipt_ids:
            raise ValueError("planner receipt ids must exactly match their artifact links")
        if len({item.invocation_id for item in self.planner_receipt_links}) != len(
            self.planner_receipt_links
        ) or len({item.artifact_id for item in self.planner_receipt_links}) != len(
            self.planner_receipt_links
        ):
            raise ValueError("planner receipt links must be one-to-one")
        return self


def validate_interrupted_metrics_evidence(
    metrics: InterruptedAttemptMetricsV1,
    *,
    attempt_id: str,
    identity: TrainingIdentityV1,
) -> None:
    """Recheck the exact files that make interrupted metrics unknowable."""

    if metrics.attempt_id != attempt_id or metrics.identity != identity:
        raise ValueError("interrupted metrics receipt crosses its training attempt")
    runner = Path(metrics.runner_log_path).resolve()
    if (
        runner.name != "runner.scheduled.log"
        or not runner.is_file()
        or hashlib.sha256(runner.read_bytes()).hexdigest()
        != metrics.runner_log_sha256
    ):
        raise ValueError("interrupted metrics runner log is missing or hash-invalid")
    try:
        runner_rows = [
            json.loads(line)
            for line in runner.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("interrupted metrics runner log is not valid JSONL") from error
    invocation_count = sum(
        isinstance(row, Mapping)
        and isinstance(row.get("worker_attempt"), int)
        and not isinstance(row.get("worker_attempt"), bool)
        for row in runner_rows
    )
    if invocation_count < len(metrics.missing_attempt_manifests):
        raise ValueError("interrupted metrics gaps exceed runner invocation evidence")
    manifest_name = (
        "training-planner-attempt-"
        + hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()[:16]
        + ".json"
    )
    for gap in metrics.missing_attempt_manifests:
        expected = Path(gap.expected_manifest_path).resolve()
        invocation = Path(gap.invocation_artifact_path).resolve()
        if (
            expected.name != manifest_name
            or expected.parent != invocation.parent
            or invocation.parent.name != gap.invocation_id
            or invocation.name != "request.json"
        ):
            raise ValueError("interrupted metrics manifest gap crosses its worker invocation")
        if expected.exists():
            raise ValueError("interrupted metrics claims a manifest that exists")
        if (
            not invocation.is_file()
            or hashlib.sha256(invocation.read_bytes()).hexdigest()
            != gap.invocation_artifact_sha256
        ):
            raise ValueError(
                "interrupted metrics invocation artifact is missing or hash-invalid"
            )


class TrainingAttemptCancellationV1(_StrictModel):
    """A pre-device-I/O control-plane terminal for a registered hypothesis."""

    schema_id: Literal[
        "omnicompany.arknights.training-attempt-cancellation.v1"
    ] = Field(default=CANCELLATION_SCHEMA, alias="schema")
    attempt_id: StrictStr = Field(min_length=1)
    hypothesis_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    identity: TrainingIdentityV1
    environment_id: StrictStr = Field(min_length=1)
    reason: StrictStr = Field(min_length=8, max_length=4000)
    next_direction: StrictStr = Field(min_length=8, max_length=4000)
    control_artifact_id: StrictStr = Field(min_length=1)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    created_at: StrictStr = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_control_plane_binding(self) -> "TrainingAttemptCancellationV1":
        if any(reference.environment_id != self.environment_id for reference in self.evidence_refs):
            raise ValueError("cancellation evidence must remain inside its environment")
        if not any(
            self.control_artifact_id in reference.artifact_ids
            for reference in self.evidence_refs
        ):
            raise ValueError("cancellation control artifact must be in evidence references")
        try:
            created_at = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("cancellation created_at must be ISO-8601") from error
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("cancellation created_at must include a timezone")
        if created_at > datetime.now(timezone.utc):
            raise ValueError("cancellation cannot be in the future")
        return self


class TrainingAttemptAbortV1(_StrictModel):
    """A trusted technical terminal after physical/control I/O has begun."""

    schema_id: Literal["omnicompany.arknights.training-attempt-abort.v1"] = Field(
        default=ABORT_SCHEMA,
        alias="schema",
    )
    attempt_id: StrictStr = Field(min_length=1)
    hypothesis_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    identity: TrainingIdentityV1
    environment_id: StrictStr = Field(min_length=1)
    classification: Literal["technical_void"] = "technical_void"
    physical_io_started: Literal[True] = True
    reason: StrictStr = Field(min_length=8, max_length=4000)
    next_direction: StrictStr = Field(min_length=8, max_length=4000)
    recovery_artifact_id: StrictStr = Field(min_length=1)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    created_at: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def bind_recovery_artifact(self) -> "TrainingAttemptAbortV1":
        if any(reference.environment_id != self.environment_id for reference in self.evidence_refs):
            raise ValueError("post-I/O abort evidence crosses its environment")
        if not any(
            self.recovery_artifact_id in reference.artifact_ids
            for reference in self.evidence_refs
        ):
            raise ValueError("post-I/O abort recovery artifact is not referenced")
        try:
            created_at = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("post-I/O abort created_at must be ISO-8601") from error
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("post-I/O abort created_at must include a timezone")
        if created_at > datetime.now(timezone.utc):
            raise ValueError("post-I/O abort cannot be in the future")
        return self


class TrainingSupervisionBatchV1(_StrictModel):
    schema_id: Literal["omnicompany.arknights.training-supervision-batch.v1"] = Field(
        default=BATCH_SCHEMA,
        alias="schema",
    )
    batch_id: str = Field(min_length=1)
    attempt_ids: list[str] = Field(min_length=3, max_length=3)
    technical_void_count: int = Field(ge=0, le=3)
    nontechnical_count: int = Field(ge=0, le=3)
    clear_count: int = Field(ge=0, le=3)
    first_battle_zero_leak_count: int = Field(ge=0, le=3)
    median_reached_floor: float | None = Field(default=None, ge=0)
    supported_hypotheses: int = Field(ge=0, le=3)
    falsified_hypotheses: int = Field(ge=0, le=3)
    recommendation: Literal["continue_direction", "revise_direction", "repair_facility"]
    reason: str = Field(min_length=8)
    created_at: str = Field(default_factory=utc_now)


class TrainingSupervisionGateV1(_StrictModel):
    schema_id: Literal["omnicompany.arknights.training-supervision-gate.v1"] = Field(
        default=GATE_SCHEMA,
        alias="schema",
    )
    status: Literal["open", "blocked"]
    source_attempt_id: str | None = Field(default=None, min_length=1)
    iteration_assessment_id: str | None = Field(default=None, min_length=1)
    reason: str = Field(min_length=8)
    required_work_item: str | None = Field(default=None, min_length=1)
    evidence_refs: list[EvidenceReferenceV1] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def bind_gate_evidence(self) -> "TrainingSupervisionGateV1":
        environments = {reference.environment_id for reference in self.evidence_refs}
        if len(environments) > 1:
            raise ValueError("a supervision gate cannot mix evidence environments")
        return self


_PAYLOAD_MODELS: dict[str, type[_StrictModel]] = {
    "hypothesis": TrainingHypothesisV1,
    "action_observation": TrainingSemanticActionV1,
    "result": TrainingAttemptResultV1,
    "batch": TrainingSupervisionBatchV1,
    "gate": TrainingSupervisionGateV1,
    "cancellation": TrainingAttemptCancellationV1,
    "abort": TrainingAttemptAbortV1,
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json", by_alias=True))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def training_hypothesis_sha256(value: TrainingHypothesisV1) -> str:
    """Public stable binding used by terminal result producers."""

    return _hash(value)


def training_attempt_result_sha256(value: TrainingAttemptResultV1) -> str:
    """Return the stable hash used when a later hypothesis cites a terminal result."""

    return _hash(value)


def ensure_training_environment(
    player_store: AIPlayerStore,
    receipt: TrainingEnvironmentReceiptV1,
    *,
    receipt_path: Path | str,
) -> EnvironmentScopeV1:
    """Create one immutable canonical environment from an exact structured receipt."""

    path = Path(receipt_path).resolve()
    if not path.is_file():
        raise ValueError("training environment receipt file is missing")
    persisted = TrainingEnvironmentReceiptV1.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    if persisted != receipt:
        raise ValueError("training environment receipt differs from persisted bytes")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    artifact_id = f"artifact.arknights.training-environment.{digest[:24]}"
    artifact = player_store.observatory_store.get_artifact(artifact_id)
    if artifact is None:
        player_store.observatory_store.save_artifact(
            ArtifactRef(
                id=artifact_id,
                kind="runtime_state",
                path=str(path),
                sha256=digest,
                captured_at=receipt.captured_at,
                media_type="application/json",
                metadata={"environment_id": receipt.environment_id},
            )
        )
    reference = EvidenceReferenceV1(
        environment_id=receipt.environment_id,
        artifact_ids=[artifact_id],
    )
    environment = EnvironmentScopeV1(
        id=receipt.environment_id,
        game_id=receipt.game_id,
        build_scope_id=receipt.build_scope_id,
        account_scope_id=receipt.account_scope_id,
        channel=receipt.channel,
        device_scope_id=receipt.device_scope_id,
        locale=receipt.locale,
        viewport_width=receipt.viewport_width,
        viewport_height=receipt.viewport_height,
        identity_hash=_hash(receipt),
        evidence_refs=[reference],
    )
    existing = player_store.get_environment(environment.id)
    if existing is not None:
        if existing.model_dump(exclude={"created_at"}) != environment.model_dump(
            exclude={"created_at"}
        ):
            raise ValueError("training environment id already has different immutable evidence")
        return existing
    return player_store.put_environment(environment)


def ensure_training_session_capsule(
    player_store: AIPlayerStore,
    hypothesis: TrainingHypothesisV1,
) -> SessionCapsuleV1:
    """Make the supervised attempt's canonical session identity durable and idempotent."""

    capsule = player_store.get_latest_session_capsule(
        hypothesis.environment_id,
        session_id=hypothesis.session_id,
    )
    if capsule is not None:
        if hypothesis.task_id not in capsule.active_task_ids:
            raise ValueError("existing training session capsule belongs to another task")
    else:
        capsule = player_store.append_session_capsule(
            SessionCapsuleV1(
                id=(
                    "capsule."
                    + hashlib.sha256(
                        _canonical([hypothesis.attempt_id, hypothesis.session_id])
                    ).hexdigest()[:24]
                ),
                environment_id=hypothesis.environment_id,
                session_id=hypothesis.session_id,
                sequence=1,
                active_task_ids=[hypothesis.task_id],
                remaining_action_budget=hypothesis.action_budget,
                remaining_token_budget=hypothesis.token_budget,
                remaining_time_seconds=hypothesis.time_budget_seconds,
                stop_reason="supervised training attempt registered before execution",
                evidence_refs=hypothesis.evidence_refs,
            )
        )
    sessions = AIPlayerSessionControl(player_store)
    session = sessions.get_session(hypothesis.environment_id, hypothesis.session_id)
    if session is None:
        session = sessions.create_session(
            CreateAIPlayerSessionCommand(
                command_id=f"{hypothesis.attempt_id}:create-training-session",
                requested_environment_id=hypothesis.environment_id,
                objective=hypothesis.expected_outcome,
                action_budget=hypothesis.action_budget,
                token_budget=hypothesis.token_budget,
                time_budget_seconds=hypothesis.time_budget_seconds,
                active_task_ids=[hypothesis.task_id],
                last_capsule_id=capsule.id,
                last_evidence_refs=hypothesis.evidence_refs,
                actor="arknights-training-supervisor",
                reason="Bind the supervised attempt before canonical action recording.",
                session_id=hypothesis.session_id,
            )
        )
    elif (
        session.environment_id != hypothesis.environment_id
        or hypothesis.task_id not in session.active_task_ids
        or session.last_capsule_id != capsule.id
    ):
        raise ValueError("existing canonical training session has a different binding")
    return capsule


def training_supervision_readiness(
    ledger: "TrainingSupervisionLedger",
    certificate_paths: list[Path | str],
) -> dict[str, Any]:
    """Project a fail-closed, non-starting readiness view from exact evidence.

    The tournament readiness compiler calls this after validating certificates.
    It does not infer missing token usage, does not adjudicate gameplay, and never
    starts a match.  A qualifying certificate is supervised only when its exact
    resolved path and current bytes hash are bound by a terminal result.
    """

    normalized_paths = [Path(path).resolve() for path in certificate_paths]

    def certificate_path_key(path: Path | str) -> str:
        """Return one Windows filesystem identity for ordinary and long paths."""

        value = str(Path(path).resolve())
        if os.name == "nt":
            if value.startswith("\\\\?\\UNC\\"):
                value = "\\\\" + value[8:]
            elif value.startswith("\\\\?\\"):
                value = value[4:]
        return os.path.normcase(value)
    if not ledger.path.is_file():
        return {
            "schema": "omnicompany.arknights.training-supervision-readiness.v1",
            "ready": False,
            "starts_match": False,
            "reason": "training supervision ledger is missing",
            "hypothesis_count": 0,
            "result_count": 0,
            "cancellation_count": 0,
            "abort_count": 0,
            "batch_count": 0,
            "semantic_action_count": 0,
            "unsupervised_certificates": [str(path) for path in normalized_paths],
            "planner_evidence_faults": [],
            "interrupted_metric_faults": [],
            "planner_coverage": {
                "count_available": 0,
                "count_unavailable": 0,
                "receipt_count": 0,
                "receipt_eligible_invocations": 0,
            },
            "usage": {"measured": 0, "partial": 0, "unavailable": 0},
            "cost": {"wall_time_seconds": 0.0, "active_time_seconds": 0.0},
        }

    hypotheses = ledger.payloads("hypothesis")
    action_observations = ledger.payloads("action_observation")
    results = ledger.payloads("result")
    cancellations = ledger.payloads("cancellation")
    aborts = ledger.payloads("abort")
    batches = ledger.payloads("batch")
    gates = ledger.payloads("gate")
    terminal_attempts = {
        item.attempt_id for item in (*results, *cancellations, *aborts)
    }
    dangling = [
        item.attempt_id
        for item in hypotheses
        if item.attempt_id not in terminal_attempts
    ]
    bound: dict[str, TrainingAttemptResultV1] = {}
    changed: list[str] = []
    planner_evidence_faults: list[str] = []
    interrupted_metric_faults: list[str] = []
    for item in results:
        path = Path(item.certificate_path).resolve()
        if not path.is_file():
            changed.append(str(path))
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != item.certificate_bytes_sha256:
            changed.append(str(path))
            continue
        bound[certificate_path_key(path)] = item
        if item.interrupted_metrics is not None:
            try:
                validate_interrupted_metrics_evidence(
                    item.interrupted_metrics,
                    attempt_id=item.attempt_id,
                    identity=item.identity,
                )
            except ValueError as error:
                interrupted_metric_faults.append(f"{item.attempt_id}: {error}")
        if item.classification in {"natural_loss", "clear"}:
            planner_path = (
                Path(item.planner_collection_path).resolve()
                if item.planner_collection_path
                else None
            )
            try:
                if (
                    item.planner_collection_artifact_id is None
                    or planner_path is None
                    or item.planner_collection_sha256 is None
                    or not planner_path.is_file()
                    or hashlib.sha256(planner_path.read_bytes()).hexdigest()
                    != item.planner_collection_sha256
                ):
                    raise ValueError("missing or changed planner collection")
                collection = TrainingPlannerMeasurementCollectionV1.model_validate_json(
                    planner_path.read_bytes()
                )
                if (
                    collection.attempt_id != item.attempt_id
                    or collection.count_status != "measured"
                    or collection.actual_invocation_count != item.model_roundtrips
                    or collection.receipt_links != item.planner_receipt_links
                    or collection.usage_coverage.receipt_count
                    != len(item.planner_receipt_links)
                ):
                    raise ValueError("planner collection/result binding differs")
                usage_values = collection.observed_usage_sums.model_dump(mode="json")
                if any(value is not None for value in usage_values.values()):
                    if any(
                        getattr(item.usage, name) != value
                        for name, value in usage_values.items()
                    ):
                        raise ValueError("planner collection/result usage differs")
                elif item.usage.measurement_status != "unavailable":
                    raise ValueError("planner unavailable usage status differs")
            except ValueError as error:
                planner_evidence_faults.append(f"{item.attempt_id}: {error}")
    unsupervised = [
        str(path)
        for path in normalized_paths
        if certificate_path_key(path) not in bound
    ]
    latest_gate_open = bool(gates and gates[-1].status == "open")
    usage_counts = {status: 0 for status in ("measured", "partial", "unavailable")}
    for item in results:
        usage_counts[
            item.usage.measurement_status if item.usage is not None else "unavailable"
        ] += 1
    ready = bool(
        hypotheses
        and results
        and batches
        and not dangling
        and latest_gate_open
        and not unsupervised
        and not changed
        and not planner_evidence_faults
        and not interrupted_metric_faults
    )
    reasons: list[str] = []
    if not hypotheses:
        reasons.append("no pre-run hypothesis is recorded")
    if not results:
        reasons.append("no certificate-bound result is recorded")
    if not batches:
        reasons.append("no fixed three-attempt supervision batch is recorded")
    if dangling:
        reasons.append("a hypothesis lacks a terminal result, cancellation, or abort")
    if not latest_gate_open:
        reasons.append("the latest supervision gate is not open")
    if unsupervised:
        reasons.append("one or more readiness certificates are not supervised")
    if changed:
        reasons.append("one or more supervised certificate files changed")
    if planner_evidence_faults:
        reasons.append("one or more nontechnical planner collections are invalid")
    if interrupted_metric_faults:
        reasons.append("one or more interrupted metric receipts are invalid")
    measured_roundtrips = [item.model_roundtrips for item in results]

    def complete_sum(values: list[float | None]) -> float | None:
        return sum(value for value in values if value is not None) if all(
            value is not None for value in values
        ) else None

    return {
        "schema": "omnicompany.arknights.training-supervision-readiness.v1",
        "ready": ready,
        "starts_match": False,
        "reason": "all supervision gates passed" if ready else "; ".join(reasons),
        "hypothesis_count": len(hypotheses),
        "result_count": len(results),
        "cancellation_count": len(cancellations),
        "abort_count": len(aborts),
        "batch_count": len(batches),
        "semantic_action_count": len(action_observations),
        "latest_gate": gates[-1].model_dump(mode="json", by_alias=True) if gates else None,
        "dangling_attempt_ids": dangling,
        "unsupervised_certificates": unsupervised,
        "changed_certificates": changed,
        "planner_evidence_faults": planner_evidence_faults,
        "interrupted_metric_faults": interrupted_metric_faults,
        "planner_coverage": {
            "count_available": sum(item.model_roundtrips is not None for item in results),
            "count_unavailable": sum(item.model_roundtrips is None for item in results),
            "receipt_count": sum(len(item.planner_receipt_links) for item in results),
            "receipt_eligible_invocations": sum(
                item.model_roundtrips or 0 for item in results
            ),
        },
        "usage": usage_counts,
        "cost": {
            "wall_time_seconds": complete_sum(
                [item.wall_time_seconds for item in results]
            ),
            "active_time_seconds": complete_sum(
                [item.active_time_seconds for item in results]
            ),
            "external_wait_seconds": complete_sum(
                [item.external_wait_seconds for item in results]
            ),
            "model_roundtrips": (
                sum(value for value in measured_roundtrips if value is not None)
                if all(value is not None for value in measured_roundtrips)
                else None
            ),
        },
    }


def require_registered_training_attempt(
    ledger: "TrainingSupervisionLedger",
    *,
    attempt_id: str,
    identity: TrainingIdentityV1,
    provider: str,
    model: str,
) -> TrainingHypothesisV1:
    """Fail before any game I/O unless this exact run has a pre-registered hypothesis."""

    hypotheses = ledger.payloads("hypothesis")
    if not hypotheses or hypotheses[-1].attempt_id != attempt_id:
        raise RuntimeError("the requested training attempt is not the latest registered hypothesis")
    hypothesis = hypotheses[-1]
    if hypothesis.identity != identity:
        raise RuntimeError(
            "the registered training identity does not match this harness run: "
            f"registered={hypothesis.identity.model_dump(mode='json')!r}, "
            f"received={identity.model_dump(mode='json')!r}"
        )
    if hypothesis.provider != provider or hypothesis.model != model:
        raise RuntimeError("the registered provider/model does not match this harness run")
    terminal_attempts = {
        item.attempt_id
        for item in (
            *ledger.payloads("result"),
            *ledger.payloads("cancellation"),
            *ledger.payloads("abort"),
        )
    }
    if attempt_id in terminal_attempts:
        raise RuntimeError("the registered training attempt is already terminal")
    gates = ledger.payloads("gate")
    if gates and gates[-1].status != "open":
        raise RuntimeError(f"the training supervision gate is blocked: {gates[-1].reason}")
    return hypothesis


class TrainingSupervisionLedger:
    """Append-only domain projection; canonical player entities stay in AIPlayerStore."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        previous = ""
        event_ids: set[str] = set()
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                row = json.loads(line)
                kind = row.get("kind") if isinstance(row, dict) else None
                model = _PAYLOAD_MODELS.get(str(kind))
                if (
                    not isinstance(row, dict)
                    or row.get("schema") != LEDGER_SCHEMA
                    or row.get("seq") != line_number
                    or row.get("prev_hash") != previous
                    or not isinstance(row.get("event_id"), str)
                    or not isinstance(row.get("timestamp"), str)
                    or model is None
                ):
                    raise ValueError("row envelope is invalid")
                if row["event_id"] in event_ids:
                    raise ValueError("event id is duplicated")
                expected = _hash({key: value for key, value in row.items() if key != "hash"})
                if row.get("hash") != expected:
                    raise ValueError("hash chain is invalid")
                model.model_validate(row.get("payload"))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid supervision ledger row {line_number}: {exc}") from exc
            rows.append(row)
            event_ids.add(row["event_id"])
            previous = row["hash"]
        return rows

    def append(
        self,
        kind: Literal[
            "hypothesis",
            "action_observation",
            "result",
            "batch",
            "gate",
            "cancellation",
        ],
        event_id: str,
        payload: _StrictModel,
        *,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        model = _PAYLOAD_MODELS[kind]
        normalized = model.model_validate(payload).model_dump(mode="json", by_alias=True)
        rows = self.rows()
        existing = next((row for row in rows if row["event_id"] == event_id), None)
        if existing is not None:
            if existing["kind"] == kind and existing["payload"] == normalized:
                return existing
            raise ValueError("supervision event id already contains different content")
        row = {
            "schema": LEDGER_SCHEMA,
            "seq": len(rows) + 1,
            "event_id": event_id,
            "timestamp": timestamp or utc_now(),
            "kind": kind,
            "payload": normalized,
            "prev_hash": rows[-1]["hash"] if rows else "",
        }
        row["hash"] = _hash(row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return row

    def payloads(self, kind: str) -> list[_StrictModel]:
        model = _PAYLOAD_MODELS[kind]
        return [model.model_validate(row["payload"]) for row in self.rows() if row["kind"] == kind]


def _training_usage_from_worker(
    worker_usage: TrainingUsageV1 | Mapping[str, Any] | Literal["unavailable"] | None,
) -> TrainingUsageV1:
    """Normalize only explicit worker telemetry; never manufacture missing counters."""

    if worker_usage is None or worker_usage == "unavailable":
        return TrainingUsageV1(measurement_status="unavailable")
    if isinstance(worker_usage, TrainingUsageV1):
        return worker_usage
    if not isinstance(worker_usage, Mapping):
        raise ValueError("worker usage must be a mapping, TrainingUsageV1, or unavailable")

    aliases = {
        "input": "input_tokens",
        "output": "output_tokens",
        "cache_read": "cached_input_tokens",
        "cache_write": "cache_creation_input_tokens",
        "reasoning": "reasoning_tokens",
        "cache_read_input_tokens": "cached_input_tokens",
        "cache_write_input_tokens": "cache_creation_input_tokens",
        "reasoning_output_tokens": "reasoning_tokens",
    }
    accepted = {
        "measurement_status",
        "input_tokens",
        "cached_input_tokens",
        "cache_creation_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cost",
        *aliases,
    }
    unknown = sorted(str(key) for key in worker_usage if key not in accepted)
    if unknown:
        raise ValueError(f"worker usage contains unsupported fields: {', '.join(unknown)}")

    normalized: dict[str, Any] = {}
    for key, value in worker_usage.items():
        target = aliases.get(str(key), str(key))
        if target in normalized:
            raise ValueError(f"worker usage reports {target} more than once")
        normalized[target] = value
    token_fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_creation_input_tokens",
        "output_tokens",
        "reasoning_tokens",
    )
    if "measurement_status" not in normalized:
        measured = [normalized.get(field) is not None for field in token_fields]
        any_value = any(measured) or normalized.get("cost") is not None
        normalized["measurement_status"] = (
            "measured" if all(measured) else "partial" if any_value else "unavailable"
        )
    return TrainingUsageV1.model_validate(normalized)


def build_training_attempt_result(
    ledger: TrainingSupervisionLedger,
    hypothesis: TrainingHypothesisV1,
    *,
    certificate_path: Path | str,
    wall_time_seconds: float | None,
    active_time_seconds: float | None,
    external_wait_seconds: float | None,
    model_roundtrips: int | None,
    action_sample_ids: list[str] | None = None,
    semantic_action_ids: list[str] | None = None,
    planner_collection_artifact_id: str | None = None,
    planner_collection_path: Path | str | None = None,
    planner_collection_sha256: str | None = None,
    planner_receipt_links: list[ReceiptLinkV1] | None = None,
    planner_receipt_ids: list[str] | None = None,
    iteration_assessment_id: str | None = None,
    worker_usage: TrainingUsageV1 | Mapping[str, Any] | Literal["unavailable"] | None = None,
    interrupted_metrics: InterruptedAttemptMetricsV1 | None = None,
    evaluator_verdict: Literal["supported", "falsified", "inconclusive"] | None = None,
    evaluator_decision: Literal["continue", "revise", "retire", "repair"] | None = None,
    evaluator_next_direction: str | None = None,
) -> TrainingAttemptResultV1:
    """Build one terminal result from registered identity and persisted evidence.

    The function is deliberately read-only.  It binds the exact certificate
    bytes and accepts gameplay adjudication only as explicit evaluator input.
    """

    registered = require_registered_training_attempt(
        ledger,
        attempt_id=hypothesis.attempt_id,
        identity=hypothesis.identity,
        provider=hypothesis.provider,
        model=hypothesis.model,
    )
    if registered != hypothesis:
        raise ValueError("hypothesis differs from the exact registered payload")

    path = Path(certificate_path).resolve()
    if not path.is_file():
        raise ValueError("persisted training certificate is missing")
    raw = path.read_bytes()
    try:
        certificate = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("persisted training certificate is not valid UTF-8 JSON") from exc
    if not isinstance(certificate, dict):
        raise ValueError("persisted training certificate must be one JSON object")
    if certificate.get("schema") != CERTIFICATE_SCHEMA:
        raise ValueError("persisted training certificate schema is invalid")
    if certificate.get("identity") != hypothesis.identity.model_dump(mode="json"):
        raise ValueError("certificate identity differs from the registered hypothesis")
    certificate_hash = certificate.get("hash")
    expected_hash = _hash({key: value for key, value in certificate.items() if key != "hash"})
    if (
        not isinstance(certificate_hash, str)
        or len(certificate_hash) != 64
        or any(character not in "0123456789abcdef" for character in certificate_hash)
        or certificate_hash != expected_hash
    ):
        raise ValueError("persisted training certificate hash is invalid")

    classification = certificate.get("classification")
    if classification not in {"technical_void", "unresolved", "natural_loss", "clear"}:
        raise ValueError("persisted training certificate classification is invalid")
    if "final_floor" not in certificate:
        raise ValueError("persisted training certificate final_floor is missing")
    final_floor = certificate["final_floor"]
    if final_floor is not None and (
        isinstance(final_floor, bool) or not isinstance(final_floor, int) or final_floor < 0
    ):
        raise ValueError("persisted training certificate final_floor is invalid")

    if classification == "technical_void":
        fixed = ("inconclusive", "repair", None)
        failure_axis = "facility"
        observed_outcome = "Persisted certificate classifies this attempt as technical_void."
        decision_reason = "Technical evidence requires facility repair before another attempt."
    elif classification == "unresolved":
        fixed = ("inconclusive", "repair", None)
        failure_axis = "evidence"
        observed_outcome = "Persisted certificate classifies this attempt as unresolved."
        decision_reason = "Incomplete evidence requires repair before gameplay adjudication."
    else:
        if (
            evaluator_verdict is None
            or evaluator_decision is None
            or evaluator_next_direction is None
        ):
            raise ValueError(
                "nontechnical results require explicit evaluator verdict, decision, and next direction"
            )
        fixed = (evaluator_verdict, evaluator_decision, evaluator_next_direction)
        failure_axis = "none" if classification == "clear" else "combat"
        observed_outcome = f"Persisted certificate classifies this attempt as {classification}."
        decision_reason = "Explicit evaluator adjudication is bound to the persisted certificate."

    if classification in {"technical_void", "unresolved"}:
        supplied = (evaluator_verdict, evaluator_decision, evaluator_next_direction)
        if any(value is not None for value in supplied) and supplied != fixed:
            raise ValueError(f"{classification} evaluator inputs conflict with fixed semantics")

    if interrupted_metrics is not None:
        if classification != "technical_void":
            raise ValueError("interrupted unavailable metrics require a technical void")
        validate_interrupted_metrics_evidence(
            interrupted_metrics,
            attempt_id=hypothesis.attempt_id,
            identity=hypothesis.identity,
        )

    verdict, decision, next_direction = fixed
    action_sample_ids = list(action_sample_ids or [])
    semantic_action_ids = list(semantic_action_ids or [])
    planner_receipt_ids = list(planner_receipt_ids or [])
    planner_receipt_links = list(planner_receipt_links or [])
    if classification in {"natural_loss", "clear"} and not action_sample_ids:
        raise ValueError(
            "natural_loss and clear require canonical action-quality sample ids"
        )
    return TrainingAttemptResultV1(
        attempt_id=hypothesis.attempt_id,
        hypothesis_sha256=training_hypothesis_sha256(hypothesis),
        identity=hypothesis.identity,
        classification=classification,
        failure_axis=failure_axis,
        observed_outcome=observed_outcome,
        hypothesis_verdict=verdict,
        decision=decision,
        decision_reason=decision_reason,
        next_direction=next_direction,
        reached_floor=final_floor,
        wall_time_seconds=wall_time_seconds,
        active_time_seconds=active_time_seconds,
        external_wait_seconds=external_wait_seconds,
        model_roundtrips=model_roundtrips,
        usage=(
            None
            if interrupted_metrics is not None
            else _training_usage_from_worker(worker_usage)
        ),
        interrupted_metrics=interrupted_metrics,
        certificate_path=str(path),
        certificate_bytes_sha256=hashlib.sha256(raw).hexdigest(),
        action_sample_ids=action_sample_ids,
        semantic_action_ids=semantic_action_ids,
        planner_collection_artifact_id=planner_collection_artifact_id,
        planner_collection_path=(
            str(Path(planner_collection_path).resolve())
            if planner_collection_path is not None
            else None
        ),
        planner_collection_sha256=planner_collection_sha256,
        planner_receipt_links=planner_receipt_links,
        planner_receipt_ids=planner_receipt_ids,
        iteration_assessment_id=iteration_assessment_id,
        evidence_refs=list(hypothesis.evidence_refs),
    )


class ArknightsTrainingSupervisor:
    """Bind tournament attempts to canonical tasks, samples, measurements and gates."""

    def __init__(self, player_store: AIPlayerStore, ledger: TrainingSupervisionLedger) -> None:
        self.player_store = player_store
        self.ledger = ledger
        self.monitor = PlayerIterationMonitor(player_store)

    def _latest_iteration_gate(self, environment_id: str) -> Any:
        assessments = self.player_store.list_iteration_assessments(environment_id, limit=1)
        return resolve_iteration_remediation_gate(
            self.player_store,
            environment_id=environment_id,
            assessment=assessments[0] if assessments else None,
        )

    @staticmethod
    def _evidence_key(reference: EvidenceReferenceV1) -> bytes:
        return _canonical(reference)

    @staticmethod
    def _reference_ids(reference: EvidenceReferenceV1) -> set[str]:
        return {
            value
            for field in (
                "artifact_ids",
                "evidence_run_ids",
                "evidence_step_ids",
                "trace_run_ids",
                "source_ids",
            )
            for value in getattr(reference, field)
        }

    def _terminal_payloads(
        self,
    ) -> list[
        TrainingAttemptResultV1
        | TrainingAttemptCancellationV1
        | TrainingAttemptAbortV1
    ]:
        """Return all terminal records in append order, not model order."""

        records: list[
            TrainingAttemptResultV1
            | TrainingAttemptCancellationV1
            | TrainingAttemptAbortV1
        ] = []
        for row in self.ledger.rows():
            if row["kind"] == "result":
                records.append(TrainingAttemptResultV1.model_validate(row["payload"]))
            elif row["kind"] == "cancellation":
                records.append(
                    TrainingAttemptCancellationV1.model_validate(row["payload"])
                )
            elif row["kind"] == "abort":
                records.append(TrainingAttemptAbortV1.model_validate(row["payload"]))
        return records

    @staticmethod
    def _parse_time(value: Any, label: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise ValueError(f"{label} must be ISO-8601") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{label} must include a timezone")
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _epoch_ms(cls, value: Any, label: str) -> datetime:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{label} must be numeric milliseconds")
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as error:
            raise ValueError(f"{label} is outside the supported range") from error

    def _controller_receipt(
        self,
        verification: TrainingRemediationVerificationV1,
        *,
        cutoff: datetime,
    ) -> tuple[dict[str, Any], datetime, datetime, dict[str, Any]]:
        path = Path(verification.controller_task_receipt_path).resolve()
        if not path.is_file():
            raise ValueError("controller task receipt artifact is missing")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != verification.controller_task_receipt_sha256:
            raise ValueError("controller task receipt artifact hash changed")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("controller task receipt is not valid UTF-8 JSON") from error
        if not isinstance(payload, Mapping):
            raise ValueError("controller task receipt must be one JSON object")
        receipt = payload.get("receipt")
        if not isinstance(receipt, Mapping):
            raise ValueError("controller task receipt lacks the signed node receipt")
        receipt = dict(receipt)
        task_payload = payload.get("payload")
        task_payload = task_payload if isinstance(task_payload, Mapping) else payload

        if task_payload is not payload:
            for identity_field in ("task_id", "node_id"):
                outer_value = payload.get(identity_field)
                nested_value = task_payload.get(identity_field)
                if (
                    outer_value is not None
                    and nested_value is not None
                    and outer_value != nested_value
                ):
                    raise ValueError("controller task identity fields disagree")

        def task_value(name: str) -> Any:
            return payload.get(name) if payload.get(name) is not None else task_payload.get(name)

        task_id = str(task_value("task_id") or "")
        node_id = str(task_value("node_id") or "")
        if not task_id or not node_id:
            raise ValueError("controller task receipt lacks task/node identity")
        if receipt.get("task_id") != task_id or receipt.get("node_id") != node_id:
            raise ValueError("controller task and signed receipt identities differ")

        receipt_signature = payload.get("receipt_signature")
        registered_node_public_key = payload.get(
            "node_public_key", payload.get("registered_node_public_key")
        )
        receipt_node_public_key = payload.get("receipt_node_public_key")
        if receipt_node_public_key is None:
            receipt_node_public_key = registered_node_public_key
        receipt_body_sha256 = payload.get("receipt_signed_body_sha256")
        if receipt_body_sha256 is None:
            receipt_body_sha256 = payload.get("body_sha256")
        receipt_algorithm = payload.get("receipt_signature_algorithm")
        if receipt_algorithm is None:
            receipt_algorithm = payload.get("algorithm")
        receipt_verified_at_ms = payload.get("receipt_verified_at_ms")
        if receipt_verified_at_ms is None:
            receipt_verified_at_ms = payload.get("verified_at_ms", payload.get("verified_at"))
        receipt_replayable = payload.get("receipt_signature_replayable")
        if receipt_replayable is None:
            receipt_replayable = payload.get("replayable")
        if registered_node_public_key is not None and (
            receipt_node_public_key != registered_node_public_key
        ):
            raise ValueError("controller task receipt node public key differs from registered key")
        if not isinstance(receipt_node_public_key, str) or not receipt_node_public_key:
            raise ValueError("controller task receipt lacks the node public key")
        if not isinstance(receipt_signature, str) or not receipt_signature:
            raise ValueError("controller task receipt lacks the node signature")
        if receipt_algorithm != "Ed25519":
            raise ValueError("controller task receipt signature algorithm is unavailable")
        if receipt_replayable is not True:
            raise ValueError("controller task receipt is not replayable")
        if "output_compaction" in receipt:
            raise ValueError("controller task receipt is compacted")
        if not isinstance(receipt_body_sha256, str) or len(receipt_body_sha256) != 64:
            raise ValueError("controller task receipt body hash is unavailable")
        if any(character not in "0123456789abcdef" for character in receipt_body_sha256):
            raise ValueError("controller task receipt body hash is invalid")
        if hashlib.sha256(_canonical(receipt)).hexdigest() != receipt_body_sha256:
            raise ValueError("controller task receipt body hash mismatch")
        try:
            public_key = base64.b64decode(receipt_node_public_key, validate=True)
            signature = base64.b64decode(receipt_signature, validate=True)
            if len(public_key) != 32 or len(signature) != 64:
                raise ValueError("controller task receipt signature key length is invalid")
            Ed25519PublicKey.from_public_bytes(public_key).verify(
                signature,
                _canonical(receipt),
            )
        except (ValueError, InvalidSignature, binascii.Error) as error:
            raise ValueError("controller task receipt Ed25519 signature is invalid") from error
        if isinstance(receipt_verified_at_ms, bool) or not isinstance(receipt_verified_at_ms, (int, float)):
            raise ValueError("controller task receipt verification time is unavailable")
        verified_at = self._epoch_ms(receipt_verified_at_ms, "controller receipt verified_at_ms")

        required = ("task_id", "node_id", "grant_id", "policy_hash", "program_id", "argv")
        if any(not str(task_value(key) or "").strip() for key in required[:5]):
            raise ValueError("controller task receipt lacks task/grant/policy/program identity")
        argv = task_value("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            raise ValueError("controller task receipt lacks the original argv")
        policy_hash = str(task_value("policy_hash") or "").lower()
        if len(policy_hash) != 64 or any(char not in "0123456789abcdef" for char in policy_hash):
            raise ValueError("controller task receipt policy hash is invalid")
        if task_id != verification.remote_task_id:
            raise ValueError("controller task receipt task id differs from verification")
        if node_id != verification.remote_node:
            raise ValueError("controller task receipt node differs from verification")
        claimed = self._epoch_ms(task_value("claimed_at_ms"), "controller claimed_at_ms")
        completed = self._epoch_ms(task_value("completed_at_ms"), "controller completed_at_ms")
        if completed <= claimed:
            raise ValueError("controller task receipt completed_at precedes claimed_at")
        if claimed <= cutoff or completed <= cutoff:
            raise ValueError("controller task receipt predates the blocked attempt")
        if verified_at < cutoff or verified_at > completed:
            raise ValueError("controller task receipt verification time is outside the task window")
        status = str(task_value("status") or receipt.get("status") or "").strip()
        if status not in {"completed", "failed", "rejected", "revoked"}:
            raise ValueError("controller task receipt status is not terminal")
        nested_status = str(receipt.get("status") or "").strip()
        if nested_status and nested_status != status:
            raise ValueError("controller task and signed receipt statuses differ")
        exit_code = receipt.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise ValueError("controller task receipt exit code is missing")
        duration_ms = receipt.get("duration_ms")
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)) or duration_ms < 0:
            raise ValueError("controller task receipt duration is missing")
        if float(duration_ms) > (completed - claimed).total_seconds() * 1000 + 1000:
            raise ValueError("controller task receipt duration exceeds claimed window")
        if not isinstance(receipt.get("stdout"), str) or not isinstance(receipt.get("stderr"), str):
            raise ValueError("controller task receipt must preserve stdout and stderr")
        normalized = dict(payload)
        normalized.update(
            {
                "task_id": task_value("task_id"),
                "node_id": task_value("node_id"),
                "grant_id": task_value("grant_id"),
                "policy_hash": task_value("policy_hash"),
                "program_id": task_value("program_id"),
                "argv": argv,
                "status": status,
            }
        )
        return normalized, claimed, completed, dict(receipt)

    def _validate_remediation_artifacts(
        self,
        verification: TrainingRemediationVerificationV1,
        *,
        source_attempt_id: str,
        cutoff: datetime,
    ) -> tuple[dict[str, Any], datetime, datetime, dict[str, Any]]:
        artifacts: dict[str, Any] = {}
        for reference in verification.evidence_refs:
            for artifact_id in reference.artifact_ids:
                artifact = self.player_store.observatory_store.get_artifact(artifact_id)
                if artifact is None:
                    raise ValueError(f"remediation evidence artifact is missing: {artifact_id}")
                captured_at = getattr(artifact, "captured_at", None)
                if not captured_at or self._parse_time(captured_at, f"artifact {artifact_id} captured_at") <= cutoff:
                    raise ValueError("remediation evidence was captured before the blocked attempt ended")
                artifacts[artifact_id] = artifact
        controller_artifact = artifacts.get(verification.controller_task_receipt_artifact_id)
        output_artifact = artifacts.get(verification.output_artifact_id)
        if controller_artifact is None or output_artifact is None:
            raise ValueError("remediation evidence must include controller and output artifacts")
        for artifact, expected_path, expected_sha, label in (
            (
                controller_artifact,
                verification.controller_task_receipt_path,
                verification.controller_task_receipt_sha256,
                "controller task receipt",
            ),
            (
                output_artifact,
                verification.output_artifact_path,
                verification.output_artifact_sha256,
                "remediation output",
            ),
        ):
            actual_path = Path(str(artifact.path)).resolve()
            if actual_path != Path(expected_path).resolve() or str(artifact.sha256) != expected_sha:
                raise ValueError(f"{label} artifact reference does not match persisted bytes")
            if not actual_path.is_file():
                raise ValueError(f"{label} artifact bytes are missing")
            if hashlib.sha256(actual_path.read_bytes()).hexdigest() != expected_sha:
                raise ValueError(f"{label} artifact hash changed")
        metadata = getattr(output_artifact, "metadata", {})
        if not isinstance(metadata, Mapping) or (
            metadata.get("source_attempt_id") != source_attempt_id
            or metadata.get("required_work_item") != verification.required_work_item
            or metadata.get("remote_task_id") != verification.remote_task_id
        ):
            raise ValueError("remediation output artifact metadata is not source-bound")
        return self._controller_receipt(verification, cutoff=cutoff)

    def _planner_collection(
        self,
        hypothesis: TrainingHypothesisV1,
        result: TrainingAttemptResultV1,
    ) -> TrainingPlannerMeasurementCollectionV1 | None:
        artifact_id = result.planner_collection_artifact_id
        if artifact_id is None:
            return None
        artifact = self.player_store.observatory_store.get_artifact(artifact_id)
        if artifact is None or artifact.kind != "trace":
            raise ValueError("planner collection artifact is missing or has the wrong kind")
        path = Path(artifact.path).resolve()
        if (
            result.planner_collection_path is None
            or result.planner_collection_sha256 is None
            or path != Path(result.planner_collection_path).resolve()
            or artifact.sha256 != result.planner_collection_sha256
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256
            or artifact.metadata.get("schema") != COLLECTION_SCHEMA
            or artifact.metadata.get("attempt_id") != hypothesis.attempt_id
            or artifact.metadata.get("environment_id") != hypothesis.environment_id
            or artifact.metadata.get("actual_invocation_count")
            != result.model_roundtrips
            or artifact.metadata.get("count_status")
            != ("measured" if result.model_roundtrips is not None else "unavailable")
        ):
            raise ValueError("planner collection artifact bytes are missing or hash-invalid")
        try:
            collection = TrainingPlannerMeasurementCollectionV1.model_validate_json(
                path.read_bytes()
            )
        except ValueError as error:
            raise ValueError("planner collection artifact is not valid canonical JSON") from error
        if (
            collection.schema_id != COLLECTION_SCHEMA
            or collection.attempt_id != hypothesis.attempt_id
            or collection.environment_id != hypothesis.environment_id
            or collection.ai_player_session_id != hypothesis.session_id
            or collection.task_id != hypothesis.task_id
            or collection.provider != hypothesis.provider
            or collection.model != hypothesis.model
            or collection.actual_invocation_count != result.model_roundtrips
            or collection.receipt_links != result.planner_receipt_links
            or collection.usage_coverage.receipt_count
            != len(result.planner_receipt_links)
        ):
            raise ValueError("planner collection crosses or contradicts the supervised attempt")
        usage_values = collection.observed_usage_sums.model_dump(mode="json")
        token_fields = (
            "input_tokens",
            "cached_input_tokens",
            "cache_creation_input_tokens",
            "output_tokens",
            "reasoning_tokens",
        )
        if collection.count_status != "measured" or not any(
            value is not None for value in usage_values.values()
        ):
            expected_usage = TrainingUsageV1(measurement_status="unavailable")
        else:
            expected_usage = TrainingUsageV1(
                measurement_status=(
                    "measured"
                    if all(
                        collection.usage_coverage.measured_counts.get(field)
                        == collection.actual_invocation_count
                        for field in token_fields
                    )
                    else "partial"
                ),
                **usage_values,
            )
        if result.usage != expected_usage:
            raise ValueError("result usage differs from its planner collection coverage")
        if not any(
            artifact_id in reference.artifact_ids for reference in result.evidence_refs
        ):
            raise ValueError("planner collection artifact is absent from result evidence")
        return collection

    def _planner_receipt(
        self,
        hypothesis: TrainingHypothesisV1,
        link: ReceiptLinkV1,
    ) -> Any | None:
        """Resolve a receipt through its canonical artifact and recheck scope."""

        getter = getattr(
            self.player_store,
            "get_planner_measurement_receipt_by_artifact",
            None,
        )
        if getter is None:
            return None
        receipt = getter(link.artifact_id)
        if receipt is None:
            return None
        if (
            receipt.id != link.receipt_id
            or receipt.invocation_id != link.invocation_id
            or receipt.environment_id != hypothesis.environment_id
            or receipt.session_id != hypothesis.session_id
            or receipt.task_id != hypothesis.task_id
            or receipt.provider != hypothesis.provider
            or receipt.model != hypothesis.model
        ):
            raise ValueError("planner receipt crosses or contradicts its supervised attempt")
        return receipt

    def assert_can_start(self, environment_id: str) -> TrainingSupervisionGateV1:
        gates = self.ledger.payloads("gate")
        domain_gate = gates[-1] if gates else TrainingSupervisionGateV1(
            status="open",
            reason="No unresolved Arknights training attempt blocks the first supervised run.",
        )
        if domain_gate.status != "open":
            raise RuntimeError(
                f"training supervision is blocked: {domain_gate.reason}; "
                f"required={domain_gate.required_work_item or 'unspecified'}"
            )
        player_gate = self._latest_iteration_gate(environment_id)
        if player_gate.status not in {"open", "remediated"}:
            raise RuntimeError(
                f"AI-player remediation gate is {player_gate.status}: {player_gate.reason}"
            )
        hypotheses = self.ledger.payloads("hypothesis")
        terminals = self._terminal_payloads()
        terminal_attempts = {item.attempt_id for item in terminals}
        if hypotheses and hypotheses[-1].attempt_id not in terminal_attempts:
            raise RuntimeError("a prior supervised attempt has no terminal result or cancellation")
        return domain_gate

    def begin_attempt(self, hypothesis: TrainingHypothesisV1) -> TrainingHypothesisV1:
        self.assert_can_start(hypothesis.environment_id)
        self.player_store.resolve_evidence_references(hypothesis.evidence_refs)
        prior = {item.attempt_id: item for item in self.ledger.payloads("hypothesis")}
        if hypothesis.attempt_id in prior:
            if prior[hypothesis.attempt_id] != hypothesis:
                raise ValueError("attempt id already contains a different hypothesis")
            return prior[hypothesis.attempt_id]
        if prior:
            if not hypothesis.parent_attempt_id or hypothesis.parent_attempt_id not in prior:
                raise ValueError("a later attempt must name an existing parent attempt")
            terminals = self._terminal_payloads()
            if not terminals:
                raise ValueError("a child hypothesis requires a terminal result or cancellation")
            terminal = terminals[-1]
            if terminal.attempt_id != hypothesis.parent_attempt_id:
                raise ValueError("hypothesis parent must be the latest terminal result")
            gates = self.ledger.payloads("gate")
            latest_gate = gates[-1] if gates else None
            gate_rows = [row for row in self.ledger.rows() if row["kind"] == "gate"]
            latest_gate_event_id = gate_rows[-1]["event_id"] if gate_rows else None
            if terminal.next_direction:
                if hypothesis.direction != terminal.next_direction:
                    raise ValueError(
                        "hypothesis direction must exactly equal the latest terminal result next_direction"
                    )
            elif (
                isinstance(terminal, TrainingAttemptResultV1)
                and terminal.classification == "technical_void"
            ):
                parent = prior[terminal.attempt_id]
                if (
                    latest_gate is None
                    or latest_gate.status != "open"
                    or latest_gate.source_attempt_id != terminal.attempt_id
                    or not latest_gate.evidence_refs
                    or not str(latest_gate_event_id or "").startswith(
                        f"{terminal.attempt_id}:remediation:"
                    )
                ):
                    raise ValueError(
                        "technical void child requires its exact open remediation gate"
                    )
                if hypothesis.direction != parent.direction:
                    raise ValueError(
                        "technical void child must exactly inherit the parent hypothesis direction"
                    )
            else:
                raise ValueError(
                    "a null terminal next_direction cannot authorize a child hypothesis"
                )
            if latest_gate is not None and latest_gate.status == "open" and latest_gate.evidence_refs:
                required = {
                    self._evidence_key(item) for item in latest_gate.evidence_refs
                }
                supplied = {self._evidence_key(item) for item in hypothesis.evidence_refs}
                if not required.issubset(supplied):
                    raise ValueError(
                        "hypothesis must bind evidence from the latest open remediation gate"
                    )
        elif hypothesis.parent_attempt_id is not None:
            raise ValueError("the first supervised attempt cannot name a parent")
        if self.player_store.get_task(hypothesis.environment_id, hypothesis.task_id) is None:
            self.player_store.enqueue_task(
                FrontierTaskV1(
                    id=hypothesis.task_id,
                    environment_id=hypothesis.environment_id,
                    evidence_refs=hypothesis.evidence_refs,
                    title=f"训练假设：{hypothesis.changed_variable.name}",
                    source="gameplay_candidate",
                    reason=(
                        f"方向：{hypothesis.direction}\n预期：{hypothesis.expected_outcome}\n"
                        f"证伪：{hypothesis.falsification_criterion}"
                    ),
                    action_budget=hypothesis.action_budget,
                    token_budget=hypothesis.token_budget,
                    time_budget_seconds=hypothesis.time_budget_seconds,
                )
            )
        self.ledger.append("hypothesis", f"{hypothesis.attempt_id}:hypothesis", hypothesis)
        return hypothesis

    def record_action(
        self,
        attempt_id: str,
        sample: ActionQualitySampleV1,
    ) -> tuple[ActionQualitySampleV1, Any | None]:
        hypothesis = self._open_hypothesis(attempt_id)
        if (
            sample.environment_id != hypothesis.environment_id
            or sample.session_id != hypothesis.session_id
            or sample.task_id != hypothesis.task_id
        ):
            raise ValueError("action sample is not bound to the open training attempt")
        return self.monitor.record(sample)

    def ingest_semantic_action_journal(
        self,
        attempt_id: str,
        *,
        journal_path: Path | str,
        certificate: Mapping[str, Any] | None = None,
    ) -> list[TrainingSemanticActionV1]:
        """Project exact hash-chained semantic actions without inventing pixel evidence."""

        from .training_evidence import EvidenceJournal

        hypothesis = self._open_hypothesis(attempt_id)
        path = Path(journal_path).resolve()
        if not path.is_file():
            raise ValueError("semantic action journal is missing")
        if certificate is not None and Path(
            str(certificate.get("journal_path", ""))
        ).resolve() != path:
            raise ValueError("certificate does not bind the exact semantic action journal")
        if certificate is not None and certificate.get(
            "identity"
        ) != hypothesis.identity.model_dump(mode="json"):
            raise ValueError("action journal certificate crosses the supervised identity")
        journal = EvidenceJournal(path, hypothesis.identity.model_dump(mode="json"))
        rows = journal.rows()
        if not rows or (
            certificate is not None
            and certificate.get("journal_hash") != rows[-1]["hash"]
        ):
            raise ValueError("certificate does not bind the terminal action journal hash")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        artifact_id = f"artifact.arknights.training-actions.{digest[:24]}"
        artifact = ArtifactRef(
            id=artifact_id,
            kind="runtime_state",
            path=str(path),
            sha256=digest,
            captured_at=rows[-1]["timestamp"],
            media_type="application/x-ndjson",
            metadata={
                "schema": "omnicompany.arknights.training-semantic-action-journal.v1",
                "environment_id": hypothesis.environment_id,
                "attempt_id": hypothesis.attempt_id,
                "terminal_row_hash": rows[-1]["hash"],
            },
        )
        existing_artifact = self.player_store.observatory_store.get_artifact(artifact_id)
        if existing_artifact is None:
            self.player_store.observatory_store.save_artifact(artifact)
        elif existing_artifact != artifact:
            raise ValueError("semantic action journal artifact id contains different bytes")

        observations: list[TrainingSemanticActionV1] = []
        for row in rows:
            if row["kind"] != "action":
                continue
            payload = row["payload"]
            observation_id = (
                "training-action."
                + hashlib.sha256(
                    _canonical([hypothesis.attempt_id, row["hash"]])
                ).hexdigest()[:24]
            )
            observation = TrainingSemanticActionV1(
                id=observation_id,
                attempt_id=hypothesis.attempt_id,
                identity=hypothesis.identity,
                environment_id=hypothesis.environment_id,
                session_id=hypothesis.session_id,
                task_id=hypothesis.task_id,
                source_artifact_id=artifact_id,
                journal_path=str(path),
                journal_bytes_sha256=digest,
                journal_seq=row["seq"],
                journal_row_hash=row["hash"],
                timestamp=row["timestamp"],
                semantic_action=payload["semantic_action"],
                verified_success=payload["verified_success"],
                exploration_id=payload["exploration_id"],
                intent_id=payload["intent_id"],
                evidence_receipt=payload["evidence_receipt"],
                evidence_binding=payload["evidence_binding"],
                effect_proof=payload["effect_proof"],
                safety_channel=payload["safety_channel"],
                facility_duration_ms=payload["facility_duration_ms"],
                safety_available=payload["safety_channel"]["available"],
                safety_fresh=payload["safety_channel"]["fresh"],
                operator=payload.get("operator"),
            )
            self.ledger.append(
                "action_observation",
                f"{hypothesis.attempt_id}:action:{row['seq']}",
                observation,
                timestamp=row["timestamp"],
            )
            observations.append(observation)
        return observations

    def record_canonical_semantic_actions(
        self,
        attempt_id: str,
        observations: list[TrainingSemanticActionV1],
    ) -> list[ActionQualitySampleV1]:
        """Persist one canonical terminal bundle and sample per executed action."""

        hypothesis = self._open_hypothesis(attempt_id)
        observatory = self.player_store.observatory_store
        samples: list[ActionQualitySampleV1] = []
        from ..ai_player.contracts import EvidenceReferenceV1
        from ..ai_player.semantic_action_quality import (
            produce_semantic_action_quality_sample,
        )

        environment = self.player_store.get_environment(hypothesis.environment_id)
        if environment is None:
            raise ValueError("semantic action environment is missing")
        target_id = environment.device_scope_id

        for observation in observations:
            self._validate_semantic_action(hypothesis, observation)
            digest = hashlib.sha256(
                _canonical([attempt_id, observation.id, observation.intent_id])
            ).hexdigest()[:24]
            evidence_run_id = f"evidence-run.arknights.semantic.{digest}"
            evidence_step_id = f"evidence-step.arknights.semantic.{digest}"
            action_run_id = f"action-run.arknights.semantic.{digest}"
            command_id = f"semantic-command.arknights.{digest}"
            trace_payloads = {
                "before": {
                    "schema": "game-observatory.arknights.semantic-trace.v1",
                    "role": "before",
                    "command_id": command_id,
                    "attempt_id": attempt_id,
                    "identity": observation.identity.model_dump(mode="json"),
                    "exploration_id": observation.exploration_id,
                    "intent_id": observation.intent_id,
                    "evidence_receipt": observation.evidence_receipt,
                    "evidence_binding": observation.evidence_binding,
                    "safety_channel": observation.safety_channel,
                },
                "after": {
                    "schema": "game-observatory.arknights.semantic-trace.v1",
                    "role": "after",
                    "command_id": command_id,
                    "attempt_id": attempt_id,
                    "identity": observation.identity.model_dump(mode="json"),
                    "exploration_id": observation.exploration_id,
                    "intent_id": observation.intent_id,
                    "evidence_receipt": observation.evidence_receipt,
                    "effect_proof": observation.effect_proof,
                    "facility_duration_ms": observation.facility_duration_ms,
                },
            }
            artifacts: list[ArtifactRef] = []
            for role, payload in trace_payloads.items():
                encoded = _canonical(payload)
                artifact_digest = hashlib.sha256(encoded).hexdigest()
                artifact_id = f"artifact.arknights.semantic-{role}.{artifact_digest[:24]}"
                path = observatory.artifact_root / f"{artifact_id}.json"
                if path.exists():
                    if path.read_bytes() != encoded:
                        raise ValueError("semantic trace artifact path contains different bytes")
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
                    temporary.write_bytes(encoded)
                    os.replace(temporary, path)
                artifact = ArtifactRef(
                    id=artifact_id,
                    kind="runtime_state",
                    path=str(path),
                    sha256=artifact_digest,
                    captured_at=str(observation.safety_channel["captured_at"]),
                    run_id=evidence_run_id,
                    media_type="application/json",
                    metadata={
                        "environment_id": hypothesis.environment_id,
                        "attempt_id": attempt_id,
                        "session_id": hypothesis.session_id,
                        "task_id": hypothesis.task_id,
                        "role": role,
                    },
                )
                existing = observatory.get_artifact(artifact.id)
                if existing is None:
                    observatory.save_artifact(artifact)
                elif existing != artifact:
                    raise ValueError("semantic trace artifact id contains different content")
                artifacts.append(artifact)
            reference = EvidenceReferenceV1(
                environment_id=hypothesis.environment_id,
                artifact_ids=[artifact.id for artifact in artifacts],
                evidence_run_ids=[evidence_run_id],
                evidence_step_ids=[evidence_step_id],
                trace_run_ids=[action_run_id],
                note=f"Arknights semantic facility action {observation.id}",
            )
            action = NormalizedAction(
                type="semantic", semantic_command=observation.semantic_action
            )
            action_run = RunResult(
                id=action_run_id,
                adapter="arknights-facility-semantic",
                target_id=target_id,
                task_id=hypothesis.task_id,
                status="passed",
                started_at=str(observation.safety_channel["captured_at"]),
                ended_at=observation.timestamp,
                artifact_ids=[artifact.id for artifact in artifacts],
            )
            step = EvidenceStep(
                id=evidence_step_id,
                evidence_run_id=evidence_run_id,
                step_index=1,
                status="passed",
                started_at=action_run.started_at,
                ended_at=action_run.ended_at,
                before_ui_tree_id=artifacts[0].id,
                after_ui_tree_id=artifacts[1].id,
                action=action,
                action_run_id=action_run.id,
                action_started_at=action_run.started_at,
                action_ended_at=action_run.ended_at,
                target_name=observation.semantic_action,
                viewport_width=1280,
                viewport_height=720,
                artifact_ids=[artifact.id for artifact in artifacts],
                stability=EvidenceStability(
                    profile="static-consecutive",
                    required_consecutive=1,
                    observed_consecutive=1,
                    settled=True,
                ),
                metadata={
                    "capture_profile": "semantic_trace",
                    "canonical_semantic_execution": {
                        "schema": "game-observatory.ai-player.semantic-execution.v1",
                        "environment_id": hypothesis.environment_id,
                        "session_id": hypothesis.session_id,
                        "task_id": hypothesis.task_id,
                        "command_id": command_id,
                        "attempt_id": attempt_id,
                        "observation_id": observation.id,
                        "semantic_command": observation.semantic_action,
                        "exploration_id": observation.exploration_id,
                        "intent_id": observation.intent_id,
                        "evidence_receipt": observation.evidence_receipt,
                        "effect_kind": str(observation.effect_proof["kind"]),
                        "facility_duration_ms": observation.facility_duration_ms,
                    },
                },
            )
            evidence_run = EvidenceRun(
                id=evidence_run_id,
                target_id=target_id,
                adapter="arknights-facility-semantic",
                status="passed",
                game_id="arknights",
                scope_id=hypothesis.environment_id,
                viewport_width=1280,
                viewport_height=720,
                orientation="landscape",
                environment={
                    "environment_id": hypothesis.environment_id,
                    "attempt_id": attempt_id,
                    "ai_player_session_id": hypothesis.session_id,
                    "task_id": hypothesis.task_id,
                    "exploration_id": observation.exploration_id,
                },
                started_at=action_run.started_at,
                ended_at=action_run.ended_at,
                step_ids=[step.id],
                artifact_ids=[artifact.id for artifact in artifacts],
                action_run_ids=[action_run.id],
            )
            for existing, value, saver, label in (
                (observatory.get_run(action_run.id), action_run, observatory.save_run, "run"),
                (
                    observatory.get_evidence_run(evidence_run.id),
                    evidence_run,
                    observatory.save_evidence_run,
                    "evidence run",
                ),
                (
                    observatory.get_evidence_step(step.id),
                    step,
                    observatory.save_evidence_step,
                    "evidence step",
                ),
            ):
                if existing is None:
                    saver(value)
                elif existing != value:
                    raise ValueError(
                        f"canonical semantic {label} id contains different content"
                    )
            sample = produce_semantic_action_quality_sample(
                environment_id=hypothesis.environment_id,
                session_id=hypothesis.session_id,
                task_id=hypothesis.task_id,
                command_id=command_id,
                action_run_id=action_run.id,
                evidence_step_id=step.id,
                effect_proof=observation.effect_proof,
                evidence_refs=[reference],
                created_at=str(step.ended_at),
            )
            stored, _assessment = self.record_action(attempt_id, sample)
            samples.append(stored)
        return samples

    def _validate_semantic_action(
        self,
        hypothesis: TrainingHypothesisV1,
        observation: TrainingSemanticActionV1,
    ) -> None:
        from .training_evidence import EvidenceJournal

        if (
            observation.attempt_id != hypothesis.attempt_id
            or observation.identity != hypothesis.identity
            or observation.environment_id != hypothesis.environment_id
            or observation.session_id != hypothesis.session_id
            or observation.task_id != hypothesis.task_id
        ):
            raise ValueError("semantic action observation crosses its supervised attempt")
        path = Path(observation.journal_path).resolve()
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != (
            observation.journal_bytes_sha256
        ):
            raise ValueError("semantic action journal bytes changed")
        artifact = self.player_store.observatory_store.get_artifact(
            observation.source_artifact_id
        )
        if (
            artifact is None
            or artifact.sha256 != observation.journal_bytes_sha256
            or Path(artifact.path).resolve() != path
        ):
            raise ValueError("semantic action observation lacks its canonical artifact")
        rows = EvidenceJournal(
            path, hypothesis.identity.model_dump(mode="json")
        ).rows()
        if observation.journal_seq > len(rows):
            raise ValueError("semantic action journal row is missing")
        row = rows[observation.journal_seq - 1]
        payload = row["payload"]
        expected = (
            row["kind"],
            row["hash"],
            row["timestamp"],
            payload.get("semantic_action"),
            payload.get("verified_success"),
            payload.get("exploration_id"),
            payload.get("intent_id"),
            payload.get("evidence_receipt"),
            payload.get("evidence_binding"),
            payload.get("effect_proof"),
            payload.get("safety_channel"),
            payload.get("facility_duration_ms"),
            payload.get("safety_channel", {}).get("available"),
            payload.get("safety_channel", {}).get("fresh"),
            payload.get("operator"),
        )
        actual = (
            "action",
            observation.journal_row_hash,
            observation.timestamp,
            observation.semantic_action,
            observation.verified_success,
            observation.exploration_id,
            observation.intent_id,
            observation.evidence_receipt,
            observation.evidence_binding,
            observation.effect_proof,
            observation.safety_channel,
            observation.facility_duration_ms,
            observation.safety_available,
            observation.safety_fresh,
            observation.operator,
        )
        if expected != actual:
            raise ValueError("semantic action observation differs from its journal row")

    def record_planner_measurement(
        self,
        attempt_id: str,
        receipt: PlannerMeasurementReceiptV1,
    ) -> PlannerMeasurementReceiptV1:
        hypothesis = self._open_hypothesis(attempt_id)
        if (
            receipt.environment_id != hypothesis.environment_id
            or receipt.session_id != hypothesis.session_id
            or receipt.task_id != hypothesis.task_id
        ):
            raise ValueError("planner receipt is not bound to the open training attempt")
        return self.player_store.append_planner_measurement_receipt(receipt)

    def cancel_attempt(
        self,
        cancellation: TrainingAttemptCancellationV1,
        *,
        cancellation_path: Path | str,
    ) -> TrainingAttemptCancellationV1:
        """Close a registration before any device or canonical action I/O."""

        path = Path(cancellation_path).resolve()
        if not path.is_file():
            raise ValueError("cancellation control JSON is missing")
        raw = path.read_bytes()
        persisted = TrainingAttemptCancellationV1.model_validate_json(raw)
        if persisted != cancellation:
            raise ValueError("cancellation differs from its persisted JSON bytes")

        event_id = f"{cancellation.attempt_id}:cancellation"
        existing = next(
            (row for row in self.ledger.rows() if row["event_id"] == event_id),
            None,
        )
        if existing is not None:
            if (
                existing["kind"] != "cancellation"
                or existing["payload"]
                != cancellation.model_dump(mode="json", by_alias=True)
            ):
                raise ValueError("cancellation event id already contains different content")

        hypotheses = self.ledger.payloads("hypothesis")
        hypothesis = next(
            (item for item in reversed(hypotheses) if item.attempt_id == cancellation.attempt_id),
            None,
        )
        if hypothesis is None or not hypotheses or hypotheses[-1].attempt_id != cancellation.attempt_id:
            raise ValueError("cancellation requires the latest registered hypothesis")
        if cancellation.identity != hypothesis.identity:
            raise ValueError("cancellation identity differs from its hypothesis")
        if cancellation.environment_id != hypothesis.environment_id:
            raise ValueError("cancellation environment differs from its hypothesis")
        if cancellation.hypothesis_sha256 != training_hypothesis_sha256(hypothesis):
            raise ValueError("cancellation does not bind the exact hypothesis bytes")
        if existing is None:
            if any(
                item.attempt_id == cancellation.attempt_id
                for item in self._terminal_payloads()
            ):
                raise ValueError("cancellation cannot be added after a terminal result")
            if any(
                item.attempt_id == cancellation.attempt_id
                for item in self.ledger.payloads("action_observation")
            ):
                raise ValueError("cancellation is forbidden after an action observation")

        self.player_store.resolve_evidence_references(cancellation.evidence_refs)
        observatory_store = getattr(self.player_store, "observatory_store", None)
        getter = getattr(observatory_store, "get_artifact", None)
        if getter is None:
            raise ValueError("cancellation requires the canonical artifact store")
        artifact = getter(cancellation.control_artifact_id)
        if artifact is None:
            raise ValueError("cancellation control artifact is not canonical")
        artifact_path = Path(artifact.path)
        if not artifact_path.is_file():
            raise ValueError("cancellation control artifact bytes are missing")
        try:
            control_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("cancellation control artifact is not UTF-8 JSON") from error
        if not isinstance(control_payload, Mapping):
            raise ValueError("cancellation control artifact must be one JSON object")
        persisted_at = str(control_payload.get("created_at") or "")
        if (
            artifact.kind != "runtime_state"
            or artifact.sha256 != hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            or artifact.metadata.get("control_plane") != "training-cancellation"
            or artifact.metadata.get("physical_io_started") is not False
            or artifact.metadata.get("environment_id") != cancellation.environment_id
            or artifact.metadata.get("attempt_id") != cancellation.attempt_id
            or artifact.metadata.get("hypothesis_sha256") != cancellation.hypothesis_sha256
            or persisted_at != cancellation.created_at
            or artifact.captured_at != persisted_at
        ):
            raise ValueError("cancellation control artifact is not an immutable pre-I/O proof")
        if existing is not None:
            return cancellation
        prior_reference_ids = {
            reference_id
            for kind in ("hypothesis", "action_observation", "result", "cancellation")
            for payload in self.ledger.payloads(kind)
            for reference in getattr(payload, "evidence_refs", [])
            for reference_id in self._reference_ids(reference)
        }
        if cancellation.control_artifact_id in prior_reference_ids:
            raise ValueError("cancellation control artifact must be new")

        self.ledger.append("cancellation", event_id, cancellation)
        return cancellation

    def abort_attempt(
        self,
        abort: TrainingAttemptAbortV1,
        *,
        abort_path: Path | str,
    ) -> tuple[TrainingAttemptAbortV1, TrainingSupervisionGateV1]:
        """Close a post-I/O attempt with immutable recovery evidence."""

        path = Path(abort_path).resolve()
        if not path.is_file():
            raise ValueError("post-I/O abort JSON is missing")
        persisted = TrainingAttemptAbortV1.model_validate_json(path.read_bytes())
        if persisted != abort:
            raise ValueError("post-I/O abort differs from its persisted JSON bytes")
        hypotheses = self.ledger.payloads("hypothesis")
        hypothesis = next(
            (item for item in reversed(hypotheses) if item.attempt_id == abort.attempt_id),
            None,
        )
        if hypothesis is None or hypotheses[-1].attempt_id != abort.attempt_id:
            raise ValueError("post-I/O abort requires the latest registered hypothesis")
        if (
            abort.identity != hypothesis.identity
            or abort.environment_id != hypothesis.environment_id
            or abort.hypothesis_sha256 != training_hypothesis_sha256(hypothesis)
        ):
            raise ValueError("post-I/O abort does not bind the exact hypothesis")
        self.player_store.resolve_evidence_references(abort.evidence_refs)
        artifact = self.player_store.observatory_store.get_artifact(
            abort.recovery_artifact_id
        )
        if artifact is None:
            raise ValueError("post-I/O recovery artifact is not canonical")
        artifact_path = Path(artifact.path).resolve()
        if not artifact_path.is_file():
            raise ValueError("post-I/O recovery artifact bytes are missing")
        try:
            recovery = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("post-I/O recovery artifact is not UTF-8 JSON") from error
        if (
            not isinstance(recovery, Mapping)
            or artifact.kind != "runtime_state"
            or artifact.sha256 != hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            or artifact.metadata.get("control_plane") != "training-post-io-abort"
            or artifact.metadata.get("physical_io_started") is not True
            or artifact.metadata.get("attempt_id") != abort.attempt_id
            or artifact.metadata.get("environment_id") != abort.environment_id
            or artifact.metadata.get("hypothesis_sha256") != abort.hypothesis_sha256
            or artifact.captured_at != abort.created_at
            or recovery.get("control_plane") != "training-post-io-abort"
            or recovery.get("physical_io_started") is not True
            or recovery.get("attempt_id") != abort.attempt_id
            or recovery.get("environment_id") != abort.environment_id
            or recovery.get("hypothesis_sha256") != abort.hypothesis_sha256
            or recovery.get("created_at") != abort.created_at
        ):
            raise ValueError("post-I/O recovery artifact is not immutable I/O evidence")
        event_id = f"{abort.attempt_id}:abort"
        existing = next(
            (row for row in self.ledger.rows() if row["event_id"] == event_id),
            None,
        )
        if existing is not None and (
            existing["kind"] != "abort"
            or existing["payload"] != abort.model_dump(mode="json", by_alias=True)
        ):
            raise ValueError("post-I/O abort event id contains different content")
        other_terminals = [
            item
            for item in self._terminal_payloads()
            if item.attempt_id == abort.attempt_id and not isinstance(item, TrainingAttemptAbortV1)
        ]
        if other_terminals:
            raise ValueError("post-I/O abort cannot follow another terminal")
        if existing is None:
            self.ledger.append("abort", event_id, abort)
        gate = TrainingSupervisionGateV1(
            status="blocked",
            source_attempt_id=abort.attempt_id,
            reason="Post-I/O terminalization failed; verified recovery is required.",
            required_work_item="arknights.training.terminalizer_recovery",
            evidence_refs=abort.evidence_refs,
            created_at=abort.created_at,
        )
        gate_id = f"{abort.attempt_id}:gate"
        existing_gate = next(
            (row for row in self.ledger.rows() if row["event_id"] == gate_id),
            None,
        )
        if existing_gate is None:
            self.ledger.append("gate", gate_id, gate)
        elif existing_gate["payload"] != gate.model_dump(mode="json", by_alias=True):
            raise ValueError("post-I/O abort gate contains different content")
        return abort, gate

    def _open_hypothesis(self, attempt_id: str) -> TrainingHypothesisV1:
        hypotheses = self.ledger.payloads("hypothesis")
        terminal_attempts = {item.attempt_id for item in self._terminal_payloads()}
        item = next((value for value in reversed(hypotheses) if value.attempt_id == attempt_id), None)
        if item is None or attempt_id in terminal_attempts:
            raise ValueError("training attempt is absent or already terminal")
        return item

    def finish_attempt(
        self,
        result: TrainingAttemptResultV1,
        *,
        certificate: Mapping[str, Any],
    ) -> tuple[TrainingAttemptResultV1, TrainingSupervisionGateV1]:
        if result.interrupted_metrics is not None:
            validate_interrupted_metrics_evidence(
                result.interrupted_metrics,
                attempt_id=result.attempt_id,
                identity=result.identity,
            )
        existing_result_row = next(
            (
                row
                for row in self.ledger.rows()
                if row["event_id"] == f"{result.attempt_id}:result"
            ),
            None,
        )
        if existing_result_row is not None:
            existing_result = TrainingAttemptResultV1.model_validate(
                existing_result_row["payload"]
            )
            if existing_result != result:
                raise ValueError("attempt result event id already contains different content")
            certificate_path = Path(result.certificate_path).resolve()
            if (
                not certificate_path.is_file()
                or hashlib.sha256(certificate_path.read_bytes()).hexdigest()
                != result.certificate_bytes_sha256
                or json.loads(certificate_path.read_text(encoding="utf-8"))
                != dict(certificate)
            ):
                raise ValueError("existing terminal result certificate differs from persisted bytes")
            existing_hypothesis = next(
                (
                    item
                    for item in self.ledger.payloads("hypothesis")
                    if item.attempt_id == result.attempt_id
                ),
                None,
            )
            if existing_hypothesis is None:
                raise ValueError("terminal result has no registered hypothesis")
            collection = self._planner_collection(existing_hypothesis, result)
            if result.classification in {"natural_loss", "clear"} and (
                collection is None
                or collection.count_status != "measured"
                or result.model_roundtrips is None
            ):
                raise ValueError(
                    "nontechnical result requires an available hash-bound planner count"
                )
            for link in result.planner_receipt_links:
                if self._planner_receipt(existing_hypothesis, link) is None:
                    raise ValueError("attempt result references unavailable planner receipts")
            gate_row = next(
                (
                    row
                    for row in self.ledger.rows()
                    if row["event_id"] == f"{result.attempt_id}:gate"
                ),
                None,
            )
            if gate_row is not None:
                return existing_result, TrainingSupervisionGateV1.model_validate(
                    gate_row["payload"]
                )
            player_gate = self._latest_iteration_gate(existing_hypothesis.environment_id)
            blocked = result.classification in {"technical_void", "unresolved"}
            if not blocked and player_gate.status not in {"open", "remediated"}:
                blocked = True
            gate = TrainingSupervisionGateV1(
                status="blocked" if blocked else "open",
                source_attempt_id=result.attempt_id,
                iteration_assessment_id=result.iteration_assessment_id,
                reason=(
                    "A terminal result requires facility or evidence repair before another run."
                    if result.classification in {"technical_void", "unresolved"}
                    else player_gate.reason
                    if blocked
                    else "Result evidence and canonical iteration checks allow the next hypothesis."
                ),
                required_work_item=(
                    "arknights.training.facility_regression"
                    if result.classification == "technical_void"
                    else "arknights.training.evidence_reconciliation"
                    if result.classification == "unresolved"
                    else player_gate.required_work_item
                    if blocked
                    else None
                ),
                evidence_refs=result.evidence_refs,
            )
            self.ledger.append("gate", f"{result.attempt_id}:gate", gate)
            return existing_result, gate
        hypothesis = self._open_hypothesis(result.attempt_id)
        expected_hypothesis_hash = _hash(hypothesis)
        if result.hypothesis_sha256 != expected_hypothesis_hash:
            raise ValueError("result does not bind the exact hypothesis bytes")
        if result.identity != hypothesis.identity:
            raise ValueError("result identity differs from its hypothesis")
        certificate_path = Path(result.certificate_path).resolve()
        if not certificate_path.is_file():
            raise ValueError("training certificate file is missing")
        if hashlib.sha256(certificate_path.read_bytes()).hexdigest() != result.certificate_bytes_sha256:
            raise ValueError("training certificate bytes changed")
        parsed = json.loads(certificate_path.read_text(encoding="utf-8"))
        if parsed != dict(certificate):
            raise ValueError("supplied certificate differs from its persisted bytes")
        if parsed.get("classification") != result.classification:
            raise ValueError("result classification differs from the certificate")
        if parsed.get("identity") != result.identity.model_dump(mode="json"):
            raise ValueError("certificate identity differs from the supervised attempt")
        self.player_store.resolve_evidence_references(result.evidence_refs)
        stored_samples = [
            self.player_store.get_action_quality_sample(hypothesis.environment_id, sample_id)
            for sample_id in result.action_sample_ids
        ]
        if any(sample is None for sample in stored_samples):
            raise ValueError("attempt result references missing action-quality samples")
        observations_by_id = {
            item.id: item
            for item in self.ledger.payloads("action_observation")
            if item.attempt_id == hypothesis.attempt_id
        }
        stored_observations = [
            observations_by_id.get(observation_id)
            for observation_id in result.semantic_action_ids
        ]
        if any(item is None for item in stored_observations):
            raise ValueError("attempt result references missing semantic action observations")
        for observation in stored_observations:
            assert observation is not None
            self._validate_semantic_action(hypothesis, observation)
        if result.classification in {"natural_loss", "clear"} and not stored_samples:
            raise ValueError(
                "a nontechnical result requires canonical action-quality samples; "
                "semantic observations cannot substitute"
            )
        if result.classification in {"natural_loss", "clear"}:
            if not observations_by_id:
                raise ValueError(
                    "a nontechnical result requires non-empty semantic observations"
                )
            if not (
                len(stored_samples)
                == len(stored_observations)
                == len(observations_by_id)
            ):
                raise ValueError(
                    "nontechnical result requires one canonical sample per semantic observation"
                )
            bound_observation_ids: set[str] = set()
            for sample in stored_samples:
                assert sample is not None
                evidence_step_id = getattr(sample, "evidence_step_id", None)
                step = (
                    self.player_store.observatory_store.get_evidence_step(
                        str(evidence_step_id)
                    )
                    if evidence_step_id
                    else None
                )
                metadata = (
                    step.metadata.get("canonical_semantic_execution")
                    if step is not None
                    else None
                )
                if not isinstance(metadata, Mapping):
                    raise ValueError(
                        "nontechnical action sample lacks canonical semantic metadata"
                    )
                if (
                    not sample.evidence_complete
                    or sample.environment_id != hypothesis.environment_id
                    or sample.session_id != hypothesis.session_id
                    or sample.task_id != hypothesis.task_id
                    or sample.execution_disposition != "executed"
                ):
                    raise ValueError(
                        "nontechnical action sample is incomplete or crosses the attempt"
                    )
                if (
                    metadata.get("attempt_id") != hypothesis.attempt_id
                    or not str(metadata.get("exploration_id") or "").strip()
                    or not str(metadata.get("observation_id") or "").strip()
                ):
                    raise ValueError(
                        "nontechnical action sample lacks canonical attempt binding"
                    )
                bound_observation_ids.add(str(metadata["observation_id"]))
            if bound_observation_ids != set(observations_by_id):
                raise ValueError(
                    "action samples and semantic observations are not one-to-one"
                )
        planner_collection = self._planner_collection(hypothesis, result)
        if result.classification in {"natural_loss", "clear"} and (
            planner_collection is None
            or planner_collection.count_status != "measured"
            or result.model_roundtrips is None
        ):
            raise ValueError(
                "nontechnical result requires an available hash-bound planner count"
            )
        for link in result.planner_receipt_links:
            if self._planner_receipt(hypothesis, link) is None:
                raise ValueError("attempt result references unavailable planner receipts")
        assessment = None
        if stored_samples:
            assessment = self.monitor.assess(
                hypothesis.environment_id,
                "verified_task",
                result.action_sample_ids,
            )
        if result.iteration_assessment_id != (assessment.id if assessment else None):
            raise ValueError("result must carry the deterministic iteration assessment id")
        player_gate = resolve_iteration_remediation_gate(
            self.player_store,
            environment_id=hypothesis.environment_id,
            assessment=assessment,
        )
        blocked_reason: str | None = None
        required: str | None = None
        if result.classification == "technical_void":
            blocked_reason = "A facility-caused technical void requires verified repair before another run."
            required = "arknights.training.facility_regression"
        elif result.classification == "unresolved":
            blocked_reason = "Mandatory result evidence is incomplete; do not spend another run yet."
            required = "arknights.training.evidence_reconciliation"
        elif player_gate.status not in {"open", "remediated"}:
            blocked_reason = player_gate.reason
            required = player_gate.required_work_item
        gate = TrainingSupervisionGateV1(
            status="blocked" if blocked_reason else "open",
            source_attempt_id=result.attempt_id,
            iteration_assessment_id=assessment.id if assessment else None,
            reason=blocked_reason or "Result evidence and canonical iteration checks allow the next hypothesis.",
            required_work_item=required,
            evidence_refs=result.evidence_refs,
        )
        self.ledger.append("result", f"{result.attempt_id}:result", result)
        self.ledger.append("gate", f"{result.attempt_id}:gate", gate)
        self._append_batch_if_due()
        return result, gate

    def record_remediation(
        self,
        *,
        source_attempt_id: str,
        verification: TrainingRemediationVerificationV1,
        verification_path: Path | str,
        reason: str,
    ) -> TrainingSupervisionGateV1:
        gates = self.ledger.payloads("gate")
        if not gates or gates[-1].status != "blocked" or gates[-1].source_attempt_id != source_attempt_id:
            raise ValueError("there is no matching blocked attempt to remediate")
        latest = gates[-1]
        path = Path(verification_path).resolve()
        if not path.is_file():
            raise ValueError("remediation verification artifact is missing")
        raw = path.read_bytes()
        try:
            persisted = TrainingRemediationVerificationV1.model_validate_json(raw)
        except ValueError as error:
            raise ValueError("remediation verification artifact is invalid") from error
        if persisted != verification:
            raise ValueError("remediation verification differs from persisted artifact")
        source_hypothesis = next(
            (item for item in self.ledger.payloads("hypothesis") if item.attempt_id == source_attempt_id),
            None,
        )
        if source_hypothesis is None:
            raise ValueError("remediation source hypothesis is absent")
        terminals = self._terminal_payloads()
        source_terminal = next(
            (item for item in reversed(terminals) if item.attempt_id == source_attempt_id),
            None,
        )
        if source_terminal is None:
            raise ValueError("remediation source attempt has no terminal result")
        if source_terminal is not terminals[-1]:
            raise ValueError("remediation source is not the latest terminal attempt")
        if isinstance(source_terminal, TrainingAttemptCancellationV1):
            raise ValueError("pre-I/O cancellation cannot be remediated as a technical run")
        if not isinstance(
            source_terminal, (TrainingAttemptResultV1, TrainingAttemptAbortV1)
        ):
            raise ValueError("remediation source terminal type is unsupported")
        if verification.source_attempt_id != source_attempt_id:
            raise ValueError("remediation verification source attempt differs from blocked gate")
        if verification.environment_id != source_hypothesis.environment_id:
            raise ValueError("remediation verification crosses the supervised environment")
        if verification.required_work_item != latest.required_work_item:
            raise ValueError("remediation verification required work item differs from blocked gate")
        cutoff = max(
            self._parse_time(latest.created_at, "blocked gate created_at"),
            self._parse_time(
                (
                    source_terminal.finished_at
                    if isinstance(source_terminal, TrainingAttemptResultV1)
                    else source_terminal.created_at
                ),
                "source terminal finished_at",
            ),
        )
        prior_ids = {
            identifier
            for kind in ("hypothesis", "result", "abort", "cancellation", "gate")
            for payload in self.ledger.payloads(kind)
            for reference in getattr(payload, "evidence_refs", [])
            for identifier in self._reference_ids(reference)
        }
        current_ids = {
            identifier
            for reference in verification.evidence_refs
            for identifier in self._reference_ids(reference)
        }
        if current_ids & prior_ids:
            raise ValueError("remediation evidence must not reuse prior evidence references")
        _payload, claimed_at, completed_at, controller_receipt = self._validate_remediation_artifacts(
            verification,
            source_attempt_id=source_attempt_id,
            cutoff=cutoff,
        )
        if list(verification.regression_command) != [
            str(_payload.get("program_id")),
            *[str(item) for item in _payload.get("argv", [])],
        ]:
            raise ValueError("remediation command differs from the controller task receipt")
        if verification.exit_code != controller_receipt.get("exit_code"):
            raise ValueError("remediation exit code differs from the controller task receipt")
        if abs(
            verification.duration_seconds - float(controller_receipt.get("duration_ms")) / 1000
        ) > 1:
            raise ValueError("remediation duration differs from the controller task receipt")
        verified_at = self._parse_time(verification.verified_at, "remediation verified_at")
        if verified_at < completed_at:
            raise ValueError("remediation verification predates the controller completion")
        outputs: dict[str, str] = {
            "stdout": str(controller_receipt["stdout"]),
            "stderr": str(controller_receipt["stderr"]),
        }
        structured = controller_receipt.get("structured_output")
        if structured is not None:
            outputs["structured_output"] = json.dumps(
                structured,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        for check in verification.checks:
            evidence = outputs.get(check.evidence_channel)
            if evidence is None or check.evidence_excerpt not in evidence:
                raise ValueError("remediation check is not bound to trusted controller output")
            if check.observed not in check.evidence_excerpt or check.expected not in check.evidence_excerpt:
                raise ValueError("remediation check observed value is not in trusted evidence")
            if check.passed != (check.observed == check.expected):
                raise ValueError("remediation check pass flag is not derived from expected output")
        trusted_passed = (
            str(_payload.get("status") or controller_receipt.get("status") or "") == "completed"
            and int(controller_receipt["exit_code"]) == 0
            and all(item.passed for item in verification.checks)
        )
        if verification.passed != trusted_passed or verification.status != (
            "passed" if trusted_passed else "failed"
        ):
            raise ValueError("remediation pass status is not derived from trusted receipt output")
        self.player_store.resolve_evidence_references(verification.evidence_refs)
        player_gate = self._latest_iteration_gate(verification.environment_id)
        # A real failed iteration assessment remains governed by the canonical
        # AI-player remediation gate. Domain technical voids have no fabricated
        # assessment and therefore use only this remote proof.
        if player_gate.status not in {"open", "remediated"}:
            raise ValueError(
                "canonical iteration remediation is still blocked; do not manufacture an assessment"
            )
        is_open = (
            verification.passed
            and verification.status == "passed"
            and player_gate.status in {"open", "remediated"}
        )
        gate = TrainingSupervisionGateV1(
            status="open" if is_open else "blocked",
            source_attempt_id=source_attempt_id,
            iteration_assessment_id=player_gate.assessment_id,
            reason=(
                reason
                if is_open
                else f"Regression proof or canonical remediation is still insufficient: {reason}"
            ),
            required_work_item=latest.required_work_item,
            evidence_refs=verification.evidence_refs,
        )
        suffix = hashlib.sha256(
            _canonical([source_attempt_id, verification.id, hashlib.sha256(raw).hexdigest(), reason])
        ).hexdigest()[:16]
        self.ledger.append("gate", f"{source_attempt_id}:remediation:{suffix}", gate)
        return gate

    def _append_batch_if_due(self) -> TrainingSupervisionBatchV1 | None:
        results = self.ledger.payloads("result")
        if not results or len(results) % 3:
            return None
        window = results[-3:]
        certificate_rows = [
            json.loads(Path(item.certificate_path).read_text(encoding="utf-8")) for item in window
        ]
        floors = [item.reached_floor for item in window if item.classification in {"natural_loss", "clear"} and item.reached_floor is not None]
        median = None
        if floors:
            ordered = sorted(floors)
            middle = len(ordered) // 2
            median = float(ordered[middle]) if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
        technical = sum(item.classification == "technical_void" for item in window)
        falsified = sum(item.hypothesis_verdict == "falsified" for item in window)
        recommendation = (
            "repair_facility" if technical else "revise_direction" if falsified >= 2 else "continue_direction"
        )
        attempt_ids = [item.attempt_id for item in window]
        batch = TrainingSupervisionBatchV1(
            batch_id=f"batch.{hashlib.sha256(_canonical(attempt_ids)).hexdigest()[:24]}",
            attempt_ids=attempt_ids,
            technical_void_count=technical,
            nontechnical_count=3 - technical - sum(item.classification == "unresolved" for item in window),
            clear_count=sum(item.classification == "clear" for item in window),
            first_battle_zero_leak_count=sum(
                row.get("first_battle", {}).get("zero_leak") is True
                for row in certificate_rows
                if isinstance(row.get("first_battle"), Mapping)
            ),
            median_reached_floor=median,
            supported_hypotheses=sum(item.hypothesis_verdict == "supported" for item in window),
            falsified_hypotheses=falsified,
            recommendation=recommendation,
            reason=(
                "Three-attempt review keeps technical failures separate and compares only "
                "explicit hypothesis verdicts and certified run outcomes."
            ),
        )
        self.ledger.append("batch", f"{batch.batch_id}:batch", batch)
        return batch


__all__ = [
    "ArknightsTrainingSupervisor",
    "ChangedVariableV1",
    "TrainingAttemptResultV1",
    "TrainingAttemptAbortV1",
    "TrainingAttemptCancellationV1",
    "TrainingHypothesisV1",
    "TrainingIdentityV1",
    "TrainingEnvironmentReceiptV1",
    "TrainingRemediationCheckV1",
    "TrainingRemediationVerificationV1",
    "TrainingSupervisionBatchV1",
    "TrainingSupervisionGateV1",
    "TrainingSupervisionLedger",
    "TrainingUsageV1",
    "build_training_attempt_result",
    "training_attempt_result_sha256",
    "training_hypothesis_sha256",
    "training_supervision_readiness",
    "require_registered_training_attempt",
    "ensure_training_environment",
    "ensure_training_session_capsule",
]
