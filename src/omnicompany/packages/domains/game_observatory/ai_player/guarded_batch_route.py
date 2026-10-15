# [OMNI] origin=codex created_by=codex intent="execute one evidence-bound guarded route batch"
"""Guarded high-repetition route validation and execution.

Static validation is deliberately device-free.  Live execution requires a current,
evidence-backed preflight snapshot, a running leased AI-player session, one active
frontier task, and an exclusive DeviceGateway lease before the one-shot route
authorization is consumed.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..game_modeling import (
    ExecutionAttemptV1,
    ExecutionBatchV1,
    ExecutionBenchmarkV1,
    ExecutionSafetyReceiptV1,
    FacilityTimingV1,
    GuardedBatchRoutePreparationV1,
    NewSessionPreparationV1,
)
from ..runtime import GameObservatory
from .contracts import EvidenceReferenceV1, SkillRunV1, utc_now
from .execution_benchmark import build_execution_batch, summarize_execution_benchmark
from .live_step import (
    LiveStepExpectationV1,
    LiveStepRequestV1,
    LiveStepRouteRuntime,
    run_live_step,
)
from .session_control import AIPlayerSessionControl
from .skill_attestation import skill_runtime_signer_and_trust_store
from .skill_provenance import SkillRunEvidenceWriter
from .skills import SkillLifecycle
from .store import AIPlayerStore

LIVE_PREFLIGHT_REQUIREMENTS = (
    "empty_cells",
    "energy",
    "generator_visible",
    "generator_selected",
    "generator_next_tap_candidate",
    "board_ready",
    "forbidden_surface_signals",
    "device_gateway_lease",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class _DynamicPreflightRejected(Exception):
    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


class _PreActionGuardRejected(Exception):
    def __init__(
        self,
        *,
        action_index: int,
        reasons: Sequence[str],
        evidence_refs: Sequence[EvidenceReferenceV1],
        observed_detail: str | None,
    ) -> None:
        self.action_index = action_index
        self.reasons = list(reasons)
        self.evidence_refs = list(evidence_refs)
        self.observed_detail = observed_detail
        super().__init__("; ".join(self.reasons))


class GuardedBatchLivePreflightV1(_StrictModel):
    """Current-screen recognition facts produced before any route device action."""

    schema_id: Literal["game-observatory.ai-player.guarded-batch-live-preflight.v1"] = Field(
        default="game-observatory.ai-player.guarded-batch-live-preflight.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    current_artifact_id: str = Field(min_length=1)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    source_state_matches_reference: bool
    empty_cells: int = Field(ge=0)
    energy: int = Field(ge=0)
    generator_visible: bool = False
    generator_selected: bool = False
    generator_next_tap_candidate: bool = False
    # Deprecated compatibility projection; equals generator_next_tap_candidate.
    generator_ready: bool = False
    board_ready: bool
    detected_forbidden_surface_signals: list[str] = Field(default_factory=list)
    captured_at: str = Field(default_factory=utc_now)


class GuardedBatchContractValidationV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.guarded-batch-contract-validation.v1"] = Field(
        default="game-observatory.ai-player.guarded-batch-contract-validation.v1",
        alias="schema",
    )
    contract_valid: bool
    route_id: str
    route_version: int
    canonical_seed_present: bool
    canonical_gaps: list[str] = Field(default_factory=list)
    ready_for_device_action: Literal[False] = False
    unverified_live_preflight: list[str] = Field(
        default_factory=lambda: list(LIVE_PREFLIGHT_REQUIREMENTS)
    )
    device_action_count: Literal[0] = 0


class GuardedBatchRunResultV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.guarded-batch-run-result.v1"] = Field(
        default="game-observatory.ai-player.guarded-batch-run-result.v1",
        alias="schema",
    )
    ok: bool
    batch_id: str
    route_id: str
    route_version: int
    session_id: str
    task_id: str | None = None
    authorization_consumed: bool = False
    status: Literal["rejected", "completed", "interrupted", "failed"]
    rejection_reasons: list[str] = Field(default_factory=list)
    planned_action_count: int = Field(ge=0)
    attempted_action_count: int = Field(ge=0)
    completed_action_count: int = Field(ge=0)
    effective_action_count: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
    effective_actions_per_minute: float = Field(ge=0)
    evidence_refs: list[EvidenceReferenceV1] = Field(default_factory=list)
    step_results: list[dict[str, Any]] = Field(default_factory=list)
    skill_run_id: str | None = None
    receipt_path: str | None = None
    execution_batch_path: str | None = None
    execution_benchmark_path: str | None = None
    execution_batch_gate_status: Literal["passed", "failed", "not_applicable"] | None = None
    execution_benchmark_gate_status: (
        Literal["passed", "failed", "in_progress", "not_applicable"] | None
    ) = None
    canonical_gaps: list[str] = Field(default_factory=list)
    device_action_count: int = Field(ge=0)


@dataclass(frozen=True)
class GuardedBatchAuthorizationLedger:
    root: Path

    @property
    def authorization_dir(self) -> Path:
        return self.root / "ai_player_runtime" / "guarded_batch_routes" / "authorizations"

    @property
    def receipt_dir(self) -> Path:
        return self.root / "ai_player_runtime" / "guarded_batch_routes" / "receipts"

    @property
    def execution_batch_dir(self) -> Path:
        return self.root / "ai_player_runtime" / "guarded_batch_routes" / "execution_batches"

    @property
    def benchmark_snapshot_dir(self) -> Path:
        return self.root / "ai_player_runtime" / "guarded_batch_routes" / "benchmark_snapshots"

    @staticmethod
    def _safe_id(value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)

    def authorization_path(self, route: GuardedBatchRoutePreparationV1, digest: str) -> Path:
        name = (
            f"{self._safe_id(route.environment_id)}--{self._safe_id(route.route_id)}"
            f"--v{route.route_version}--{digest}.json"
        )
        return self.authorization_dir / name

    def is_consumed(self, route: GuardedBatchRoutePreparationV1, digest: str) -> bool:
        return self.authorization_path(route, digest).exists()

    def consume(
        self,
        route: GuardedBatchRoutePreparationV1,
        digest: str,
        *,
        batch_id: str,
        session_id: str,
    ) -> Path:
        path = self.authorization_path(route, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "game-observatory.ai-player.guarded-batch-authorization-consumption.v1",
            "route_id": route.route_id,
            "route_version": route.route_version,
            "environment_id": route.environment_id,
            "contract_sha256": digest,
            "batch_id": batch_id,
            "session_id": session_id,
            "consumed_at": utc_now(),
        }
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ValueError(
                "single_live_validation authorization has already been consumed"
            ) from exc
        try:
            os.write(descriptor, encoded)
        finally:
            os.close(descriptor)
        return path

    @staticmethod
    def _write_once(path: Path, payload: dict[str, Any], *, label: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ValueError(f"{label} already exists: {path.name}") from exc
        try:
            os.write(descriptor, encoded)
        finally:
            os.close(descriptor)
        return path

    def write_receipt(self, payload: dict[str, Any]) -> Path:
        batch_id = str(payload["batch_id"])
        path = self.receipt_dir / f"{self._safe_id(batch_id)}.json"
        return self._write_once(path, payload, label="guarded batch receipt")

    def _route_prefix(self, route: GuardedBatchRoutePreparationV1) -> str:
        return f"{self._safe_id(route.environment_id)}--{self._safe_id(route.route_id)}"

    def write_execution_batch(
        self, route: GuardedBatchRoutePreparationV1, batch: ExecutionBatchV1
    ) -> Path:
        path = self.execution_batch_dir / (
            f"{self._route_prefix(route)}--{self._safe_id(batch.id)}.json"
        )
        return self._write_once(
            path,
            batch.model_dump(mode="json", by_alias=True),
            label="execution batch",
        )

    def load_execution_batches(
        self, route: GuardedBatchRoutePreparationV1
    ) -> list[ExecutionBatchV1]:
        if not self.execution_batch_dir.exists():
            return []
        prefix = self._route_prefix(route) + "--"
        retained = [
            ExecutionBatchV1.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.execution_batch_dir.glob(f"{prefix}*.json")
        ]
        return sorted(retained, key=lambda item: item.timing.request_received_at)

    def write_benchmark_snapshot(
        self, route: GuardedBatchRoutePreparationV1, benchmark: ExecutionBenchmarkV1
    ) -> Path:
        path = self.benchmark_snapshot_dir / (
            f"{self._route_prefix(route)}--{self._safe_id(benchmark.id)}.json"
        )
        return self._write_once(
            path,
            benchmark.model_dump(mode="json", by_alias=True),
            label="execution benchmark snapshot",
        )


GuardedBatchContractV1 = NewSessionPreparationV1 | GuardedBatchRoutePreparationV1


def load_guarded_batch_contract(path: str | Path) -> GuardedBatchContractV1:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if "guarded_batch_route" in payload:
        return NewSessionPreparationV1.model_validate(payload)
    return GuardedBatchRoutePreparationV1.model_validate(payload)


def guarded_batch_contract_sha256(contract: GuardedBatchContractV1) -> str:
    encoded = contract.model_dump_json(by_alias=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _route_from_contract(contract: GuardedBatchContractV1) -> GuardedBatchRoutePreparationV1:
    if isinstance(contract, NewSessionPreparationV1):
        return contract.guarded_batch_route
    return contract


def _derived_skill_version_id(route: GuardedBatchRoutePreparationV1) -> str:
    stem = route.route_id.removeprefix("route.")
    return f"skill-version.{stem}.{route.route_version}"


def validate_guarded_batch_contract(
    contract: GuardedBatchContractV1,
    *,
    player: AIPlayerStore | None = None,
) -> GuardedBatchContractValidationV1:
    """Validate the frozen route and canonical references without device I/O."""

    route = _route_from_contract(contract)
    gaps: list[str] = []
    if isinstance(contract, NewSessionPreparationV1):
        if contract.game_id != route.game_id:
            gaps.append("contract and route game_id differ")
        if contract.environment_id != route.environment_id:
            gaps.append("contract and route environment_id differ")
    allowed_authority = (
        route.execution_status == "ready_for_live_validation"
        and route.execution_authorization == "single_live_validation"
    ) or (
        route.execution_status == "live_validated" and route.execution_authorization == "benchmark"
    )
    if not allowed_authority:
        gaps.append(
            "route must be ready_for_live_validation/single_live_validation "
            "or live_validated/benchmark"
        )
    if not route.executable:
        gaps.append("route is not executable")

    if player is not None:
        environment = player.get_environment(route.environment_id)
        if environment is None:
            gaps.append(f"missing environment: {route.environment_id}")
        elif environment.game_id != route.game_id:
            gaps.append("canonical environment belongs to another game")
        observatory = player.observatory_store
        artifact_ids = {route.source_state_guard.reference_artifact_id}
        for reference in route.source_state_guard.source_evidence_refs:
            artifact_ids.update(reference.artifact_ids)
            for run_id in reference.evidence_run_ids:
                if observatory.get_evidence_run(run_id) is None:
                    gaps.append(f"missing evidence run: {run_id}")
            for step_id in reference.evidence_step_ids:
                if observatory.get_evidence_step(step_id) is None:
                    gaps.append(f"missing evidence step: {step_id}")
        for locator in route.locators:
            artifact_ids.update(locator.source_artifact_ids)
            for step_id in locator.source_evidence_step_ids:
                if observatory.get_evidence_step(step_id) is None:
                    gaps.append(f"missing locator evidence step: {step_id}")
        for artifact_id in sorted(artifact_ids):
            if observatory.get_artifact(artifact_id) is None:
                gaps.append(f"missing artifact: {artifact_id}")
        skill_version_id = _derived_skill_version_id(route)
        if player.get_skill_version_by_id(route.environment_id, skill_version_id) is None:
            gaps.append(f"missing candidate skill version: {skill_version_id}")

    unique_gaps = list(dict.fromkeys(gaps))
    return GuardedBatchContractValidationV1(
        contract_valid=not unique_gaps,
        route_id=route.route_id,
        route_version=route.route_version,
        canonical_seed_present=not unique_gaps,
        canonical_gaps=unique_gaps,
    )


def _initial_reference(route: GuardedBatchRoutePreparationV1) -> EvidenceReferenceV1:
    return EvidenceReferenceV1(
        environment_id=route.environment_id,
        artifact_ids=[route.source_state_guard.reference_artifact_id],
        note=f"Frozen source-state reference for {route.route_id} v{route.route_version}.",
    )


def _result_reference(
    environment_id: str,
    result: dict[str, Any],
    *,
    sequence: int,
) -> EvidenceReferenceV1:
    return EvidenceReferenceV1(
        environment_id=environment_id,
        artifact_ids=[str(result["after_artifact_id"])],
        evidence_run_ids=[str(result["evidence_run_id"])],
        evidence_step_ids=[str(result["evidence_step_id"])],
        trace_run_ids=[str(result["action_run_id"])],
        note=f"Guarded batch terminal evidence after action {sequence}.",
    )


def _build_request(
    *,
    route: GuardedBatchRoutePreparationV1,
    environment: Any,
    session_id: str,
    task_id: str,
    action_index: int,
    initial_evidence: EvidenceReferenceV1,
) -> LiveStepRequestV1:
    planned = route.planned_actions[action_index]
    locator = next(item for item in route.locators if item.id == planned.locator_id)
    uses_deferred_batch_evidence = bool(
        route.execution_capture_mode == "guarded_batch_checkpoints"
        and len(route.planned_actions) > 1
    )
    is_terminal_action = action_index + 1 == len(route.planned_actions)
    return LiveStepRequestV1(
        target_id=environment.device_scope_id,
        environment_id=route.environment_id,
        session_id=session_id,
        task_id=task_id,
        initial_evidence=initial_evidence,
        viewport_width=environment.viewport_width,
        viewport_height=environment.viewport_height,
        game_id=route.game_id,
        build_scope_id=environment.build_scope_id,
        action=planned.action,
        target_name=locator.target_name,
        target_bounds=locator.bounds,
        account_action_intent=planned.account_action_intent,
        expectation=LiveStepExpectationV1(
            summary=planned.expectation.summary,
            kind=planned.expectation.kind,
            min_visual_distance=planned.expectation.min_visual_distance,
            region=locator.bounds,
            stop_conditions=planned.expectation.stop_conditions,
        ),
        actor="ai-player-guarded-batch",
        holder="ai-player-guarded-batch",
        locator_resolution={
            "locator_id": locator.id,
            "kind": locator.kind,
            "source_artifact_ids": locator.source_artifact_ids,
        },
        skill_replay_version_id=_derived_skill_version_id(route),
        defer_semantic_sedimentation=True,
        required_consecutive=route.required_consecutive_terminal_frames,
        settle_timeout_seconds=route.settle_timeout_seconds,
        sample_interval_seconds=route.sample_interval_seconds,
        capture_profile=(
            "guarded_batch_checkpoint"
            if uses_deferred_batch_evidence and not is_terminal_action
            else "compact_static"
        ),
        defer_evidence_run_completion=(
            uses_deferred_batch_evidence and not is_terminal_action
        ),
        complete_deferred_evidence_run=(
            uses_deferred_batch_evidence and is_terminal_action
        ),
    )


def _preflight_rejection_reasons(
    *,
    route: GuardedBatchRoutePreparationV1,
    preflight: GuardedBatchLivePreflightV1,
) -> list[str]:
    reasons: list[str] = []
    guard = route.source_state_guard
    if preflight.environment_id != route.environment_id:
        reasons.append("live preflight belongs to another environment")
    if not preflight.source_state_matches_reference:
        reasons.append("current source state does not match the frozen reference guard")
    if preflight.empty_cells < guard.minimum_empty_cells:
        reasons.append(
            f"empty cells {preflight.empty_cells} < required {guard.minimum_empty_cells}"
        )
    if preflight.energy < guard.minimum_energy:
        reasons.append(f"energy {preflight.energy} < required {guard.minimum_energy}")
    if not preflight.generator_visible:
        reasons.append("generator is not visible")
    first_action_type = (
        route.planned_actions[0].action.type if route.planned_actions else None
    )
    if first_action_type == "tap":
        if not preflight.generator_selected:
            reasons.append("generator is not selected")
        if not preflight.generator_next_tap_candidate:
            reasons.append("generator next tap is not a legal candidate")
    if not preflight.board_ready:
        reasons.append("board is not ready")
    if preflight.detected_forbidden_surface_signals:
        reasons.append(
            "forbidden surface signals detected: "
            + ", ".join(preflight.detected_forbidden_surface_signals)
        )
    return reasons


def _coerce_evidence_references(values: Any) -> list[EvidenceReferenceV1]:
    if values is None:
        return []
    return [EvidenceReferenceV1.model_validate(item) for item in values]


def _run_pre_action_guard(
    guard: Callable[..., dict[str, Any]],
    *,
    route: GuardedBatchRoutePreparationV1,
    planned_action: Any,
    action_index: int,
    runtime: LiveStepRouteRuntime,
    player: AIPlayerStore,
    session_id: str,
    task_id: str,
    current_artifact_id: str,
    current_evidence_refs: Sequence[EvidenceReferenceV1],
) -> dict[str, Any]:
    try:
        raw = guard(
            route=route,
            planned_action=planned_action,
            action_index=action_index,
            runtime=runtime,
            player=player,
            session_id=session_id,
            task_id=task_id,
            current_artifact_id=current_artifact_id,
            current_evidence_refs=list(current_evidence_refs),
        )
        decision = dict(raw)
    except Exception as exc:
        decision = {
            "allowed": False,
            "rejection_reasons": [f"pre-action guard failed: {exc}"],
            "evidence_refs": list(current_evidence_refs),
            "observed_detail": None,
        }
    if "allowed" not in decision:
        raise ValueError("pre-action guard result is missing allowed")
    evidence_refs = _coerce_evidence_references(decision.get("evidence_refs"))
    if not evidence_refs:
        evidence_refs = list(current_evidence_refs)
    reasons = [
        str(item).strip() for item in decision.get("rejection_reasons", []) if str(item).strip()
    ]
    receipt = decision.get("receipt")
    receipt_id = receipt.get("id") if isinstance(receipt, dict) else None
    receipt_path = str(decision.get("receipt_path") or "").strip() or None
    observed_parts = []
    if receipt_id:
        observed_parts.append(f"pre-action guard receipt: {receipt_id}")
    if receipt_path:
        observed_parts.append(f"pre-action guard receipt path: {receipt_path}")
    if reasons:
        observed_parts.append("pre-action guard rejection: " + "; ".join(reasons))
    decision["evidence_refs"] = evidence_refs
    decision["rejection_reasons"] = reasons
    decision["observed_detail"] = "; ".join(observed_parts) or None
    return decision


def _runtime_branch_decision(
    planned_action: Any,
    guard_decision: dict[str, Any] | None,
) -> str | None:
    """Bind a declared semi-fixed branch to the guard's actual current state."""

    if "state-branch" not in tuple(planned_action.lightweight_guard_checks):
        return None
    if not guard_decision or not guard_decision.get("allowed"):
        return None
    receipt = guard_decision.get("receipt")
    if not isinstance(receipt, dict):
        return None
    measurement = receipt.get("measurement")
    if not isinstance(measurement, dict):
        return None
    artifact_id = str(receipt.get("current_artifact_id") or "").strip()
    empty_cells = measurement.get("empty_cells")
    energy = measurement.get("energy")
    if not artifact_id or not isinstance(empty_cells, list) or not isinstance(energy, int):
        return None
    action_kind = str(receipt.get("action_kind") or "unknown")
    return (
        f"{planned_action.id}: current artifact {artifact_id} measured "
        f"empty_cells={len(empty_cells)}, energy={energy}; selected {action_kind} "
        f"locator {planned_action.locator_id} after the typed guard passed"
    )


def _receipt_payload(result: GuardedBatchRunResultV1) -> dict[str, Any]:
    payload = result.model_dump(mode="json", by_alias=True)
    payload["recorded_at"] = utc_now()
    return payload


def _record_route_skill_run(
    *,
    player: AIPlayerStore,
    route: GuardedBatchRoutePreparationV1,
    batch_id: str,
    outcome: str,
    action_count: int,
    elapsed_seconds: float,
    source_evidence_refs: Sequence[EvidenceReferenceV1],
    visual_variant_id: str,
) -> SkillRunV1:
    skill_version_id = _derived_skill_version_id(route)
    skill = player.get_skill_version_by_id(route.environment_id, skill_version_id)
    if skill is None:
        raise ValueError(f"unknown guarded route skill version: {skill_version_id}")
    signer, trust_store = skill_runtime_signer_and_trust_store(player.observatory_store.root)
    player.skill_validator_trust_store = trust_store
    validation_passed = outcome == "success"
    objective_success = outcome == "success"
    run_id = f"skill-run.{uuid.uuid4().hex}"
    evidence_refs, provenance_run_id, provenance_step_ids = SkillRunEvidenceWriter(player).write(
        skill=skill,
        validator_id=signer.validator_id,
        run_id=run_id,
        independent_reset_id=f"guarded-batch:{batch_id}",
        visual_variant_id=visual_variant_id,
        outcome=outcome,
        precondition_satisfied=True,
        objective_success=objective_success,
        validation_passed=validation_passed,
        false_success=False,
        safety_violation_count=0,
        recovery_attempted=False,
        recovery_succeeded=False,
        action_count=action_count,
        model_input_tokens=0,
        baseline_model_input_tokens=0,
        decision_latency_ms=elapsed_seconds * 1000,
        baseline_decision_latency_ms=0,
        source_evidence_refs=source_evidence_refs,
    )
    unsigned = SkillRunV1(
        environment_id=route.environment_id,
        evidence_refs=evidence_refs,
        id=run_id,
        skill_version_id=skill.id,
        validator_id=signer.validator_id,
        provenance_evidence_run_id=provenance_run_id,
        provenance_evidence_step_ids=provenance_step_ids,
        provenance_sha256=player.skill_run_provenance_sha256(evidence_refs),
        attempt_index=len(player.list_skill_runs(route.environment_id, skill_version_id=skill.id))
        + 1,
        independent_reset_id=f"guarded-batch:{batch_id}",
        visual_variant_id=visual_variant_id,
        outcome=outcome,
        precondition_satisfied=True,
        objective_success=objective_success,
        validation_passed=validation_passed,
        false_success=False,
        safety_violation_count=0,
        recovery_attempted=False,
        recovery_succeeded=False,
        action_count=action_count,
        model_input_tokens=0,
        baseline_model_input_tokens=0,
        decision_latency_ms=elapsed_seconds * 1000,
        baseline_decision_latency_ms=0,
    )
    return SkillLifecycle(player).record_run(signer.sign(unsigned))


def _utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


def _telemetry_event_ids(result: dict[str, Any]) -> list[str]:
    raw = result.get("telemetry_event_ids")
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item)]
    single = result.get("telemetry_event_id")
    return [str(single)] if single else []


def _attempt_outcome(
    result: dict[str, Any],
    *,
    business_state_changed_present: bool,
) -> tuple[Literal["effective", "no_change", "failed"], str | None]:
    completed = bool(result.get("execution_completed"))
    expectation_met = bool(result.get("expectation_met"))
    business_changed = bool(result.get("business_state_changed"))
    if not business_state_changed_present:
        return "failed", "business_state_changed is missing from the live-step result"
    if bool(result.get("ok")) and completed and expectation_met and business_changed:
        return "effective", None
    if completed and not business_changed:
        return "no_change", None
    reasons: list[str] = []
    if not bool(result.get("ok")):
        reasons.append("live step returned ok=false")
    if not completed:
        reasons.append("execution did not complete")
    if not expectation_met:
        reasons.append("expectation was not met")
    if bool(result.get("stop_recommended")):
        reasons.append("live step recommended stop")
    if not reasons:
        reasons.append("terminal guard failed")
    return "failed", "; ".join(reasons)


def _terminal_capsule_pending_count(
    player: AIPlayerStore,
    *,
    environment_id: str,
    session: Any,
) -> int:
    capsule_id = getattr(session, "last_capsule_id", None)
    if capsule_id is None:
        return 0
    capsule = player.get_session_capsule(environment_id, capsule_id)
    return int(capsule is not None and capsule.pending_action is not None)


def run_guarded_batch_route(
    contract: GuardedBatchContractV1,
    *,
    session_id: str,
    preflight: GuardedBatchLivePreflightV1 | None,
    player: AIPlayerStore,
    root: Path | None = None,
    task_id: str | None = None,
    ledger: GuardedBatchAuthorizationLedger | None = None,
    runtime_factory: Callable[..., LiveStepRouteRuntime] = LiveStepRouteRuntime.create,
    step_runner: Callable[..., dict[str, Any]] = run_live_step,
    skill_run_recorder: Callable[..., SkillRunV1] = _record_route_skill_run,
    preflight_provider: Callable[..., GuardedBatchLivePreflightV1] | None = None,
    current_screen_capturer: Callable[..., str] | None = None,
    business_state_judge: Callable[..., dict[str, Any]] | None = None,
    pre_action_guard: Callable[..., dict[str, Any]] | None = None,
    batch_id: str | None = None,
) -> GuardedBatchRunResultV1:
    """Execute one authorized route batch and stop at the first failed invariant."""

    request_received_at = _utc_datetime()
    started = time.perf_counter()
    route = _route_from_contract(contract)
    batch_id = batch_id or f"guarded-batch.{uuid.uuid4().hex}"
    resolved_root = Path(root or player.observatory_store.root)
    ledger = ledger or GuardedBatchAuthorizationLedger(resolved_root)
    digest = guarded_batch_contract_sha256(contract)
    rejection_reasons: list[str] = []

    validation = validate_guarded_batch_contract(contract, player=player)
    rejection_reasons.extend(validation.canonical_gaps)
    if preflight is None and preflight_provider is None:
        rejection_reasons.append(
            "live recognition preflight is absent: " + ", ".join(LIVE_PREFLIGHT_REQUIREMENTS)
        )
    elif preflight is not None:
        rejection_reasons.extend(_preflight_rejection_reasons(route=route, preflight=preflight))
    if route.execution_authorization == "single_live_validation" and ledger.is_consumed(
        route, digest
    ):
        rejection_reasons.append("single_live_validation authorization has already been consumed")

    environment = player.get_environment(route.environment_id)
    session = None
    selected_task_id = task_id
    session_control = AIPlayerSessionControl(player)
    if environment is None:
        rejection_reasons.append(f"unknown environment: {route.environment_id}")
    else:
        try:
            session = session_control.assert_session_lease_active(route.environment_id, session_id)
        except Exception as exc:  # exact canonical reason is preserved for the caller
            rejection_reasons.append(f"session preflight failed: {exc}")
    if session is not None:
        if session.environment_id != route.environment_id:
            rejection_reasons.append("session belongs to another environment")
        if session.remaining_action_budget < route.source_state_guard.minimum_session_action_budget:
            rejection_reasons.append(
                "remaining action budget "
                f"{session.remaining_action_budget} < required "
                f"{route.source_state_guard.minimum_session_action_budget}"
            )
        if not session.active_task_ids:
            rejection_reasons.append("session has no active task")
        elif selected_task_id is None:
            selected_task_id = session.active_task_ids[0]
        if selected_task_id is not None and selected_task_id not in session.active_task_ids:
            rejection_reasons.append("selected task is not active in the session")
        try:
            pending_count = _terminal_capsule_pending_count(
                player, environment_id=route.environment_id, session=session
            )
        except Exception as exc:
            rejection_reasons.append(f"session capsule preflight failed: {exc}")
        else:
            if pending_count:
                rejection_reasons.append("session capsule contains a pending action")
    if selected_task_id is not None:
        task = player.get_task(route.environment_id, selected_task_id)
        if task is None:
            rejection_reasons.append(f"unknown frontier task: {selected_task_id}")
        elif task.status != "active":
            rejection_reasons.append(f"frontier task is not active: {task.status}")

    if rejection_reasons:
        elapsed = time.perf_counter() - started
        result = GuardedBatchRunResultV1(
            ok=False,
            batch_id=batch_id,
            route_id=route.route_id,
            route_version=route.route_version,
            session_id=session_id,
            task_id=selected_task_id,
            status="rejected",
            rejection_reasons=list(dict.fromkeys(rejection_reasons)),
            planned_action_count=len(route.planned_actions),
            attempted_action_count=0,
            completed_action_count=0,
            effective_action_count=0,
            elapsed_seconds=elapsed,
            effective_actions_per_minute=0,
            device_action_count=0,
        )
        receipt = ledger.write_receipt(_receipt_payload(result))
        return result.model_copy(update={"receipt_path": str(receipt)})

    assert environment is not None
    assert selected_task_id is not None
    assert session is not None

    runtime: LiveStepRouteRuntime | None = None
    step_results: list[dict[str, Any]] = []
    attempt_drafts: list[dict[str, Any]] = []
    evidence_refs: list[EvidenceReferenceV1] = [_initial_reference(route)]
    if preflight is not None:
        evidence_refs.extend(preflight.evidence_refs)
    attempted = 0
    effective = 0
    authorization_consumed = False
    execution_started = False
    staged_generator_output_pending = False
    runtime_branch_decisions: list[str] = []
    status: Literal["rejected", "completed", "interrupted", "failed"] = "completed"
    run_errors: list[str] = []
    canonical_gaps: list[str] = []
    safety_violations: list[str] = []
    current_reference = _initial_reference(route)
    session_budget_at_start = int(session.remaining_action_budget)
    terminal_session = session
    device_lease_id: str | None = None
    released_device_lease: Any = None

    try:
        runtime = runtime_factory(root=resolved_root, player=player)
        first_request = _build_request(
            route=route,
            environment=environment,
            session_id=session_id,
            task_id=selected_task_id,
            action_index=0,
            initial_evidence=current_reference,
        )
        lease = runtime.ensure_lease(
            first_request,
            owner_context={
                "route_id": route.route_id,
                "route_version": route.route_version,
                "batch_id": batch_id,
                "session_id": session_id,
            },
        )
        device_lease_id = str(getattr(lease, "id", None) or getattr(lease, "token", "")) or None
        if preflight is None:
            try:
                capturer = current_screen_capturer
                if capturer is None:
                    from .guarded_batch_recognition import capture_guarded_batch_current_screen

                    capturer = capture_guarded_batch_current_screen
                current_artifact_id = capturer(
                    runtime=runtime,
                    route=route,
                    player=player,
                    session_id=session_id,
                    task_id=selected_task_id,
                )
                assert preflight_provider is not None
                preflight = preflight_provider(
                    route=route,
                    runtime=runtime,
                    player=player,
                    session_id=session_id,
                    task_id=selected_task_id,
                    current_artifact_id=current_artifact_id,
                )
            except Exception as exc:
                raise _DynamicPreflightRejected(
                    [f"automatic live recognition preflight failed: {exc}"]
                ) from exc
            evidence_refs.extend(preflight.evidence_refs)
            dynamic_rejections = _preflight_rejection_reasons(route=route, preflight=preflight)
            if dynamic_rejections:
                raise _DynamicPreflightRejected(dynamic_rejections)
        current_artifact_id = preflight.current_artifact_id
        cached_guard_decision: dict[str, Any] | None = None
        if pre_action_guard is not None:
            cached_guard_decision = _run_pre_action_guard(
                pre_action_guard,
                route=route,
                planned_action=route.planned_actions[0],
                action_index=0,
                runtime=runtime,
                player=player,
                session_id=session_id,
                task_id=selected_task_id,
                current_artifact_id=current_artifact_id,
                current_evidence_refs=preflight.evidence_refs,
            )
            evidence_refs.extend(cached_guard_decision["evidence_refs"])
            if not cached_guard_decision["allowed"]:
                raise _PreActionGuardRejected(
                    action_index=0,
                    reasons=cached_guard_decision["rejection_reasons"]
                    or ["pre-action guard rejected action 1"],
                    evidence_refs=cached_guard_decision["evidence_refs"],
                    observed_detail=cached_guard_decision["observed_detail"],
                )
        if route.execution_authorization == "single_live_validation":
            ledger.consume(route, digest, batch_id=batch_id, session_id=session_id)
            authorization_consumed = True
        execution_started = True

        for index, planned in enumerate(route.planned_actions):
            if (
                staged_generator_output_pending
                and str(planned.action.type) == "swipe"
            ):
                try:
                    capturer = current_screen_capturer
                    if capturer is None:
                        from .guarded_batch_recognition import (
                            capture_guarded_batch_current_screen,
                        )

                        capturer = capture_guarded_batch_current_screen
                    if preflight_provider is None:
                        raise ValueError(
                            "staged-output settlement requires the typed preflight provider"
                        )
                    settled_artifact_id = capturer(
                        runtime=runtime,
                        route=route,
                        player=player,
                        session_id=session_id,
                        task_id=selected_task_id,
                    )
                    settled = preflight_provider(
                        route=route,
                        runtime=runtime,
                        player=player,
                        session_id=session_id,
                        task_id=selected_task_id,
                        current_artifact_id=settled_artifact_id,
                    )
                    settlement_rejections: list[str] = []
                    if settled.environment_id != route.environment_id:
                        settlement_rejections.append(
                            "staged-output settlement belongs to another environment"
                        )
                    if not settled.source_state_matches_reference:
                        settlement_rejections.append(
                            "staged-output settlement is outside the allowed surface"
                        )
                    if not settled.board_ready:
                        settlement_rejections.append(
                            "staged-output settlement board is not ready"
                        )
                    if settled.detected_forbidden_surface_signals:
                        settlement_rejections.append(
                            "forbidden surface signals detected during staged-output "
                            "settlement: "
                            + ", ".join(settled.detected_forbidden_surface_signals)
                        )
                    if settlement_rejections:
                        raise _PreActionGuardRejected(
                            action_index=index,
                            reasons=settlement_rejections,
                            evidence_refs=list(settled.evidence_refs),
                            observed_detail=(
                                "fresh read-only settlement capture rejected before merge"
                            ),
                        )
                    current_artifact_id = settled.current_artifact_id
                    current_reference = settled.evidence_refs[0]
                    evidence_refs.extend(settled.evidence_refs)
                    staged_generator_output_pending = False
                except _PreActionGuardRejected:
                    raise
                except Exception as exc:
                    raise _PreActionGuardRejected(
                        action_index=index,
                        reasons=[f"staged-output settlement refresh failed: {exc}"],
                        evidence_refs=[current_reference],
                        observed_detail=(
                            "fresh read-only settlement capture failed before merge"
                        ),
                    ) from exc
            guard_decision = cached_guard_decision if index == 0 else None
            if pre_action_guard is not None and guard_decision is None:
                guard_decision = _run_pre_action_guard(
                    pre_action_guard,
                    route=route,
                    planned_action=planned,
                    action_index=index,
                    runtime=runtime,
                    player=player,
                    session_id=session_id,
                    task_id=selected_task_id,
                    current_artifact_id=current_artifact_id,
                    current_evidence_refs=[current_reference],
                )
                evidence_refs.extend(guard_decision["evidence_refs"])
            if guard_decision is not None and not guard_decision["allowed"]:
                reasons = guard_decision["rejection_reasons"] or [
                    f"pre-action guard rejected action {index + 1}"
                ]
                attempt_drafts.append(
                    {
                        "sequence": index + 1,
                        "planned": planned,
                        "started_at": _utc_datetime(),
                        "ended_at": _utc_datetime(),
                        "outcome": "rejected",
                        "expectation_met": False,
                        "business_state_changed": False,
                        "business_change": None,
                        "observed_detail": guard_decision["observed_detail"],
                        "rejection_category": "source_guard",
                        "rejection_reason": "; ".join(reasons),
                        "failure_reason": None,
                        "telemetry_event_ids": [],
                        "evidence_refs": guard_decision["evidence_refs"],
                    }
                )
                run_errors.extend(reasons)
                status = "failed" if attempted else "rejected"
                break
            branch_decision = _runtime_branch_decision(planned, guard_decision)
            if branch_decision is not None:
                runtime_branch_decisions.append(branch_decision)
            request = _build_request(
                route=route,
                environment=environment,
                session_id=session_id,
                task_id=selected_task_id,
                action_index=index,
                initial_evidence=current_reference,
            )
            attempted += 1
            attempt_started_at = _utc_datetime()
            try:
                raw = step_runner(request, root=resolved_root, route_runtime=runtime)
            except Exception as exc:
                attempt_ended_at = _utc_datetime()
                message = f"action {index + 1} raised: {exc}"
                status = "interrupted"
                run_errors.append(message)
                attempt_drafts.append(
                    {
                        "sequence": index + 1,
                        "planned": planned,
                        "started_at": attempt_started_at,
                        "ended_at": attempt_ended_at,
                        "outcome": "failed",
                        "expectation_met": False,
                        "business_state_changed": False,
                        "business_change": None,
                        "observed_detail": None,
                        "failure_reason": message,
                        "telemetry_event_ids": [],
                        "evidence_refs": [current_reference],
                    }
                )
                break
            attempt_ended_at = _utc_datetime()
            result = dict(raw)
            if guard_decision is not None:
                result["pre_action_guard"] = {
                    key: value for key, value in guard_decision.items() if key != "evidence_refs"
                }
            if business_state_judge is not None:
                result["live_step_ok"] = bool(result.get("ok"))
                result["live_step_expectation_met"] = bool(result.get("expectation_met"))
                result["live_step_stop_recommended"] = bool(result.get("stop_recommended"))
                try:
                    judged = business_state_judge(
                        route=route,
                        planned_action=planned,
                        action_index=index,
                        runtime=runtime,
                        player=player,
                        session_id=session_id,
                        task_id=selected_task_id,
                        result=result,
                    )
                except Exception as exc:
                    gap = f"action {index + 1} business measurement failed: {exc}"
                    canonical_gaps.append(gap)
                    judged = {
                        "business_state_changed": False,
                        "business_state_judged": False,
                        "business_expectation_met": False,
                        "telemetry_event_ids": [],
                        "business_measurement_gaps": [gap],
                    }
                result.update(dict(judged))
                business_expectation_met = bool(result.get("business_expectation_met"))
                combined_expectation_met = bool(
                    result["live_step_expectation_met"] and business_expectation_met
                )
                result["expectation_met"] = combined_expectation_met
                result["ok"] = bool(
                    result["live_step_ok"]
                    and result.get("execution_completed")
                    and combined_expectation_met
                    and result.get("business_state_changed")
                )
                result["stop_recommended"] = bool(
                    result["live_step_stop_recommended"]
                    or not result["live_step_ok"]
                    or not combined_expectation_met
                )
                for gap in result.get("business_measurement_gaps", []):
                    canonical_gaps.append(f"action {index + 1} business gap: {gap}")
            step_results.append(result)
            business_present = "business_state_changed" in result
            business_changed = (
                bool(result.get("business_state_changed")) if business_present else False
            )
            business_change = str(result.get("business_change") or "").strip() or None
            if business_changed and business_change is None:
                if business_state_judge is None:
                    business_change = planned.expectation.expected_business_change
                else:
                    gap = f"action {index + 1} business change detail is missing"
                    canonical_gaps.append(gap)
                    result["expectation_met"] = False
                    result["ok"] = False
                    result["stop_recommended"] = True
            if business_change and "generator output staged" in business_change:
                staged_generator_output_pending = True
            receipt = result.get("business_measurement_receipt")
            receipt_id = receipt.get("id") if isinstance(receipt, dict) else None
            measurement_gaps = [str(item) for item in result.get("business_measurement_gaps", [])]
            observed_parts = []
            if receipt_id:
                observed_parts.append(f"business measurement receipt: {receipt_id}")
            if measurement_gaps:
                observed_parts.append("business measurement gaps: " + "; ".join(measurement_gaps))
            observed_detail = "; ".join(observed_parts) or None
            outcome, failure_reason = _attempt_outcome(
                result,
                business_state_changed_present=business_present,
            )
            if not business_present:
                canonical_gaps.append(
                    f"action {index + 1} lacks canonical business_state_changed adjudication"
                )
            terminal_reference = current_reference
            if (
                result.get("after_artifact_id")
                and result.get("evidence_run_id")
                and result.get("evidence_step_id")
                and result.get("action_run_id")
            ):
                terminal_reference = _result_reference(
                    route.environment_id, result, sequence=index + 1
                )
                current_reference = terminal_reference
                current_artifact_id = str(result["after_artifact_id"])
                evidence_refs.append(current_reference)
            attempt_drafts.append(
                {
                    "sequence": index + 1,
                    "planned": planned,
                    "started_at": attempt_started_at,
                    "ended_at": attempt_ended_at,
                    "outcome": outcome,
                    "expectation_met": bool(result.get("expectation_met")),
                    "business_state_changed": business_changed,
                    "business_change": business_change,
                    "observed_detail": observed_detail,
                    "failure_reason": failure_reason,
                    "telemetry_event_ids": _telemetry_event_ids(result),
                    "evidence_refs": [terminal_reference],
                }
            )
            if outcome == "effective":
                effective += 1
            if outcome != "effective" or bool(result.get("stop_recommended")):
                status = "failed"
                run_errors.append(
                    failure_reason or f"action {index + 1} produced no canonical business change"
                )
                break
        if attempted != len(route.planned_actions) or effective != len(route.planned_actions):
            if status == "completed":
                status = "failed"
    except _PreActionGuardRejected as exc:
        status = "rejected" if attempted == 0 else "failed"
        run_errors.extend(exc.reasons)
        planned = route.planned_actions[exc.action_index]
        attempt_drafts.append(
            {
                "sequence": exc.action_index + 1,
                "planned": planned,
                "started_at": request_received_at,
                "ended_at": _utc_datetime(),
                "outcome": "rejected",
                "expectation_met": False,
                "business_state_changed": False,
                "business_change": None,
                "observed_detail": exc.observed_detail,
                "rejection_category": "source_guard",
                "rejection_reason": "; ".join(exc.reasons),
                "failure_reason": None,
                "telemetry_event_ids": [],
                "evidence_refs": exc.evidence_refs,
            }
        )
    except _DynamicPreflightRejected as exc:
        status = "rejected"
        run_errors.extend(exc.reasons)
        if preflight is not None and route.planned_actions:
            attempt_drafts.append(
                {
                    "sequence": 1,
                    "planned": route.planned_actions[0],
                    "started_at": request_received_at,
                    "ended_at": _utc_datetime(),
                    "outcome": "rejected",
                    "expectation_met": False,
                    "business_state_changed": False,
                    "business_change": None,
                    "observed_detail": (
                        "live preflight rejected the route before any device action"
                    ),
                    "rejection_category": "source_guard",
                    "rejection_reason": "; ".join(exc.reasons),
                    "failure_reason": None,
                    "telemetry_event_ids": [],
                    "evidence_refs": list(preflight.evidence_refs),
                }
            )
    except Exception as exc:
        status = "interrupted"
        run_errors.append(str(exc))
    finally:
        if runtime is not None:
            try:
                released_device_lease = runtime.close()
            except Exception as exc:  # never hide the primary run error
                safety_violations.append(f"DeviceGateway lease close failed: {exc}")
                canonical_gaps.append(f"runtime close failed: {exc}")
                if status == "completed":
                    status = "interrupted"
                run_errors.append(f"runtime close failed: {exc}")

    checkpoint_completed_at = _utc_datetime()
    terminal_session_lease_valid = True
    try:
        terminal_session = session_control.assert_session_lease_active(
            route.environment_id, session_id
        )
    except Exception as exc:
        terminal_session_lease_valid = False
        safety_violations.append(f"terminal session lease validation failed: {exc}")

    elapsed = time.perf_counter() - started
    completed_count = len(step_results)
    throughput = effective * 60 / elapsed if elapsed > 0 else 0
    skill_run_id = None
    if execution_started:
        assert preflight is not None
        outcome = (
            "success"
            if status == "completed"
            else "failed"
            if status == "failed"
            else "interrupted"
        )
        try:
            skill_run = skill_run_recorder(
                player=player,
                route=route,
                batch_id=batch_id,
                outcome=outcome,
                action_count=attempted,
                elapsed_seconds=elapsed,
                source_evidence_refs=evidence_refs,
                visual_variant_id=preflight.current_artifact_id,
            )
            skill_run_id = skill_run.id
        except Exception as exc:
            canonical_gaps.append(f"route-wide SkillRun persistence failed: {exc}")
            status = "failed"

    execution_batch_path: Path | None = None
    execution_benchmark_path: Path | None = None
    execution_batch_gate_status = None
    execution_benchmark_gate_status = None
    if attempt_drafts:
        try:
            route_result_ids = [skill_run_id] if skill_run_id else []
            attempts: list[ExecutionAttemptV1] = []
            for draft in attempt_drafts:
                telemetry_ids = list(draft["telemetry_event_ids"])
                source_gaps = []
                if not telemetry_ids:
                    source_gaps.append("telemetry_event_ids")
                if not route_result_ids:
                    source_gaps.append("route_result_ids")
                planned = draft["planned"]
                attempts.append(
                    ExecutionAttemptV1(
                        id=f"execution-attempt.{batch_id}.{draft['sequence']}",
                        sequence=draft["sequence"],
                        environment_id=route.environment_id,
                        session_id=session_id,
                        route_id=route.route_id,
                        planned_action_id=planned.id,
                        action_kind=planned.action.type,
                        started_at=draft["started_at"],
                        ended_at=draft["ended_at"],
                        outcome=draft["outcome"],
                        expectation_met=draft["expectation_met"],
                        business_state_changed=draft["business_state_changed"],
                        business_change=(
                            draft["business_change"] if draft["business_state_changed"] else None
                        ),
                        rejection_category=(
                            draft.get("rejection_category")
                            if draft["outcome"] == "rejected"
                            else None
                        ),
                        rejection_reason=(
                            draft.get("rejection_reason")
                            if draft["outcome"] == "rejected"
                            else None
                        ),
                        failure_reason=(
                            draft["failure_reason"] if draft["outcome"] == "failed" else None
                        ),
                        observed_detail=(
                            draft["observed_detail"]
                            or (
                                "business_state_changed was explicitly false"
                                if draft["outcome"] == "no_change"
                                else None
                            )
                        ),
                        telemetry_event_ids=telemetry_ids,
                        route_result_ids=route_result_ids,
                        canonical_source_gaps=source_gaps,
                        evidence_refs=draft["evidence_refs"],
                    )
                )

            terminal_budget = next(
                (
                    int(item["session_remaining_actions"])
                    for item in reversed(step_results)
                    if item.get("session_remaining_actions") is not None
                ),
                int(getattr(terminal_session, "remaining_action_budget", session_budget_at_start)),
            )
            pending_count = _terminal_capsule_pending_count(
                player, environment_id=route.environment_id, session=terminal_session
            )
            if pending_count:
                safety_violations.append("terminal session capsule retains a pending action")
            session_lease_id = str(getattr(terminal_session, "lease_id", "") or "")
            heartbeat_observed = bool(getattr(terminal_session, "last_heartbeat_at", None))
            if not session_lease_id:
                safety_violations.append("canonical session lease id is unavailable")
            if not heartbeat_observed:
                safety_violations.append("canonical session heartbeat is unavailable")
            released_status = getattr(released_device_lease, "status", None)
            if released_status not in {None, "released"}:
                safety_violations.append(
                    f"DeviceGateway lease terminal state is not released: {released_status}"
                )
            terminal_receipt_completed_at = _utc_datetime()
            measured_wall = (terminal_receipt_completed_at - request_received_at).total_seconds()
            if measured_wall <= 0:
                terminal_receipt_completed_at = request_received_at + timedelta(microseconds=1)
                measured_wall = (
                    terminal_receipt_completed_at - request_received_at
                ).total_seconds()
            timing = FacilityTimingV1(
                request_received_at=request_received_at,
                checkpoint_completed_at=checkpoint_completed_at,
                terminal_receipt_completed_at=terminal_receipt_completed_at,
                wall_clock_seconds=measured_wall,
                device_action_seconds=sum(
                    max(0.0, (item["ended_at"] - item["started_at"]).total_seconds())
                    for item in attempt_drafts
                    if item["outcome"] != "rejected"
                ),
            )
            safety = ExecutionSafetyReceiptV1(
                source_guard_checked=True,
                source_guard_rejection_count=sum(
                    item["outcome"] == "rejected" for item in attempt_drafts
                ),
                lease_id=session_lease_id or device_lease_id or "unknown-lease",
                lease_state_at_terminal=("held" if terminal_session_lease_valid else "lost"),
                heartbeat_observed=heartbeat_observed,
                remaining_action_budget_at_start=session_budget_at_start,
                remaining_action_budget_at_terminal=terminal_budget,
                checkpoint_completed=(
                    completed_count == attempted
                    and all(bool(item.get("execution_completed")) for item in step_results)
                ),
                pending_action_count_at_terminal=pending_count,
                safety_violations=list(dict.fromkeys(safety_violations)),
                evidence_refs=evidence_refs,
            )
            try:
                execution_batch = build_execution_batch(
                    batch_id=batch_id,
                    game_id=route.game_id,
                    environment_id=route.environment_id,
                    session_id=session_id,
                    chain_kind=route.chain_kind,
                    route_id=route.route_id,
                    planned_action_count=len(route.planned_actions),
                    legal_business_terminal_reached=False,
                    branch_decisions=runtime_branch_decisions,
                    knowledge_outcomes=[],
                    timing=timing,
                    attempts=attempts,
                    recovery_events=[],
                    safety=safety,
                    evidence_refs=evidence_refs,
                )
                execution_batch_gate_status = execution_batch.gate_status
                execution_batch_path = ledger.write_execution_batch(route, execution_batch)
                retained = ledger.load_execution_batches(route)
                benchmark = summarize_execution_benchmark(
                    benchmark_id=f"execution-benchmark.{batch_id}",
                    game_id=route.game_id,
                    environment_id=route.environment_id,
                    workload_kind=route.chain_kind,
                    batches=retained,
                    notes=[
                        "Append-only guarded route benchmark; failed and interrupted batches are retained.",
                        "A passed benchmark requires three consecutive passing batches.",
                    ],
                    updated_at=_utc_datetime(),
                )
                execution_benchmark_gate_status = benchmark.gate_status
                execution_benchmark_path = ledger.write_benchmark_snapshot(route, benchmark)
                if execution_batch.gate_status != "passed" and status != "rejected":
                    status = "failed"
            except Exception as exc:
                canonical_gaps.append(f"formal execution batch persistence failed: {exc}")
                status = "failed"
        except Exception as exc:
            canonical_gaps.append(f"formal execution receipt construction failed: {exc}")
            status = "failed"

    reasons = list(dict.fromkeys(item for item in run_errors if item))
    result = GuardedBatchRunResultV1(
        ok=(
            status == "completed" and not canonical_gaps and execution_batch_gate_status == "passed"
        ),
        batch_id=batch_id,
        route_id=route.route_id,
        route_version=route.route_version,
        session_id=session_id,
        task_id=selected_task_id,
        authorization_consumed=authorization_consumed,
        status=status,
        rejection_reasons=reasons,
        planned_action_count=len(route.planned_actions),
        attempted_action_count=attempted,
        completed_action_count=completed_count,
        effective_action_count=effective,
        elapsed_seconds=elapsed,
        effective_actions_per_minute=throughput,
        evidence_refs=evidence_refs,
        step_results=step_results,
        skill_run_id=skill_run_id,
        execution_batch_path=str(execution_batch_path) if execution_batch_path else None,
        execution_benchmark_path=(
            str(execution_benchmark_path) if execution_benchmark_path else None
        ),
        execution_batch_gate_status=execution_batch_gate_status,
        execution_benchmark_gate_status=execution_benchmark_gate_status,
        canonical_gaps=list(dict.fromkeys(canonical_gaps)),
        device_action_count=attempted,
    )
    receipt = ledger.write_receipt(_receipt_payload(result))
    return result.model_copy(update={"receipt_path": str(receipt)})


__all__ = [
    "GuardedBatchAuthorizationLedger",
    "GuardedBatchContractValidationV1",
    "GuardedBatchContractV1",
    "GuardedBatchLivePreflightV1",
    "GuardedBatchRunResultV1",
    "LIVE_PREFLIGHT_REQUIREMENTS",
    "guarded_batch_contract_sha256",
    "load_guarded_batch_contract",
    "run_guarded_batch_route",
    "validate_guarded_batch_contract",
]
