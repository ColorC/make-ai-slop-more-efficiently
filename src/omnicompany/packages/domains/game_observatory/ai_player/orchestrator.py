"""Game-agnostic autonomous execution kernel for one canonical frontier command.

The kernel selects a task, journals intent, calls an injected executor exactly once,
persists terminal evidence, and delegates canonical write-back.  It intentionally has
no device adapter, guide-research client, or skill-crystallisation behavior.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import NormalizedAction, SourcePixelRect, utc_now
from .account_policy import (
    AccountActionDecisionV1,
    AccountActionIntentV1,
    evaluate_account_action,
)
from .account_metric_observation import (
    AccountMetricDeltaDerivationV1,
    CanonicalAccountMetricProvider,
    attach_account_metric_derivations,
)
from .action_quality_producer import (
    ActionDecisionTelemetryV1,
    ActionQualityHistoryContextV1,
    ActionQualityHistorySnapshotV1,
    produce_action_quality_sample,
    stable_action_quality_sample_id,
)
from .action_history import ActionHistoryGuard
from .consolidation import (
    CanonicalExecutionOutcomeV1,
    ConsolidationResultV1,
    ExecutionConsolidator,
    stable_command_id,
)
from .contracts import (
    ActionQualitySampleV1,
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    FrontierTaskV1,
    PendingActionV1,
    SemanticStateV1,
    SessionCapsuleV1,
)
from .frontier import FrontierGenerator, FrontierSignalV1
from .expected_change_measurement import attach_expected_change_measurement
from .interaction_preflight import (
    InteractionPreflightError,
    InteractionPreflightV1,
    validate_interaction_preflight,
)
from .iteration_monitor import PlayerIterationMonitor
from .physical_readiness import PhysicalReadinessEvaluator
from .planner_measurement import (
    PlannerMeasurementReceiptV1,
    resolve_planner_telemetry,
)
from .operation_memory import OperationMemory
from .remediation import resolve_iteration_remediation_gate
from .recovery import AutonomousRecovery, PendingActionObserver, RecoveryRequiredError
from .session_control import (
    AIPlayerSessionCheckpointCommand,
    AIPlayerSessionControl,
    AIPlayerSessionError,
    AIPlayerSessionV1,
)
from .store import AIPlayerStore
from .task_board import TaskBoard, TaskBoardDecisionV1, named_blockers
from .task_focus import OPEN_DEVICE_ACTION_GATES, resolve_authoritative_task_focus


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class ActionHistoryOverrideV1(_StrictModel):
    reason: str = Field(min_length=1)
    expected_new_information: str = Field(min_length=1)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)


class AutonomousExecutionCommandV1(_StrictModel):
    command_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    expected_identity_hash: str = Field(min_length=16)
    intent: str = Field(min_length=1)
    action: NormalizedAction
    target_bounds: SourcePixelRect | None = None
    interaction_preflight: InteractionPreflightV1 | None = None
    action_history_override: ActionHistoryOverrideV1 | None = None
    decision_telemetry: ActionDecisionTelemetryV1 | None = None
    planned_task_id: str | None = Field(default=None, min_length=1)
    planner_measurement_artifact_id: str | None = Field(default=None, min_length=1)
    account_policy_id: str | None = Field(default=None, min_length=1)
    account_policy_version: int | None = Field(default=None, ge=1)
    account_action_intent: AccountActionIntentV1 | None = None
    actor: str = Field(default="ai-player.autonomous-kernel", min_length=1)

    @field_validator("command_id", "environment_id", "session_id", "intent", "actor")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("自主执行命令字段不能为空")
        return value

    @model_validator(mode="after")
    def keep_account_authority_binding_complete(self) -> "AutonomousExecutionCommandV1":
        fields = (
            self.account_policy_id,
            self.account_policy_version,
            self.account_action_intent,
        )
        if any(value is not None for value in fields) and not all(
            value is not None for value in fields
        ):
            raise ValueError("account policy id, version, and action intent must be bound together")
        return self


class PhysicalReadinessExecutionPolicyV1(_StrictModel):
    """Risk-tiered use of the AFK benchmark for one physical command."""

    disposition: Literal["ready", "warning_e2e_required", "hard_block"]
    consequence_tier: Literal[
        "benchmark_ready",
        "low_consequence_gameplay",
        "protected_high_consequence",
    ]
    benchmark_reason_code: str = Field(min_length=1)
    benchmark_verdict: Literal["PASS", "FAIL"] | None = None
    benchmark_gap_codes: list[str] = Field(default_factory=list)
    warning: str | None = Field(default=None, min_length=1)
    e2e_sample_required: bool
    required_runtime_chain: tuple[str, str, str] = (
        "pre_execution_expectation",
        "real_execution",
        "post_execution_check",
    )

    @model_validator(mode="after")
    def preserve_risk_tier_semantics(self) -> PhysicalReadinessExecutionPolicyV1:
        if self.required_runtime_chain != (
            "pre_execution_expectation",
            "real_execution",
            "post_execution_check",
        ):
            raise ValueError("physical readiness runtime chain cannot be weakened")
        if self.disposition == "warning_e2e_required":
            if not self.e2e_sample_required or self.warning is None:
                raise ValueError("benchmark warning requires an E2E sample and warning")
        elif self.warning is not None:
            raise ValueError("only warning disposition may carry a warning")
        if self.disposition == "hard_block" and self.consequence_tier != (
            "protected_high_consequence"
        ):
            raise ValueError("only protected high-consequence actions may hard block")
        return self


class AutonomousExecutorRequestV1(_StrictModel):
    command: AutonomousExecutionCommandV1
    task: FrontierTaskV1
    pending_capsule_id: str = Field(min_length=1)
    selection_reason: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    physical_readiness_policy: PhysicalReadinessExecutionPolicyV1 | None = None


_POINTER_ACTION_TYPES = {
    "tap",
    "swipe",
    "pinch",
    "two_finger_swipe",
    "mouse_move",
    "mouse_button",
}
_TEST_EXECUTOR_CHANNELS = {"fixture", "test"}
_TEST_SCOPE_MARKERS = ("fixture", "test", "memory")


def _isolated_test_environment_allows_preflight_bypass(
    environment: EnvironmentScopeV1,
) -> bool:
    """Allow the explicit bypass only for synthetic scopes with no real identity leaf."""

    if environment.channel not in _TEST_EXECUTOR_CHANNELS:
        return False
    if environment.server_scope_id is not None or environment.world_scope_id is not None:
        return False
    scoped_identifiers = [
        environment.id,
        environment.game_id,
        environment.build_scope_id,
        environment.account_scope_id,
        environment.device_scope_id,
        *environment.game_id_aliases,
        *environment.build_scope_id_aliases,
        *environment.device_scope_id_aliases,
    ]
    return all(
        any(marker in value.lower() for marker in _TEST_SCOPE_MARKERS)
        for value in scoped_identifiers
    )


class ExecutorPreflightReceiptV1(_StrictModel):
    """Nominal proof that an injected executor ran its read-only pre-reservation gate."""

    schema_id: Literal[
        "game-observatory.ai-player.executor-preflight-receipt.v1"
    ] = Field(
        default="game-observatory.ai-player.executor-preflight-receipt.v1",
        alias="schema",
    )
    command_id: str = Field(min_length=1)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_type: str = Field(min_length=1)
    issuer: Literal["device_executor", "test_executor"]
    pointer_preflight_checked: bool
    validator_version: Literal["executor-preflight.v1"] = "executor-preflight.v1"


class AutonomousCommandExecutor(Protocol):
    """Injected component that performs exactly one command and returns its evidence."""


    def validate_before_reservation(
        self,
        command: AutonomousExecutionCommandV1,
    ) -> ExecutorPreflightReceiptV1: ...

    def execute(self, request: AutonomousExecutorRequestV1) -> CanonicalExecutionOutcomeV1: ...


@dataclass(frozen=True, slots=True)
class AccountMetricDerivationRequest:
    """Canonical post-consolidation input available to a metric provider."""

    command: AutonomousExecutionCommandV1
    task: FrontierTaskV1
    consolidation: ConsolidationResultV1
    sample: ActionQualitySampleV1

    def __post_init__(self) -> None:
        environment_ids = {
            self.command.environment_id,
            self.task.environment_id,
            self.consolidation.environment_id,
            self.sample.environment_id,
        }
        if len(environment_ids) != 1:
            raise ValueError("account metric derivation request crosses environments")
        if self.command.command_id != self.consolidation.command_id:
            raise ValueError("metric request command does not match consolidation")
        if self.sample.command_id != self.command.command_id:
            raise ValueError("metric request sample does not match command")
        if self.task.id != self.consolidation.task_id or self.sample.task_id != self.task.id:
            raise ValueError("metric request task does not match consolidation")
        if self.sample.evidence_step_id is None:
            raise ValueError("metric request requires a terminal evidence step")
        if self.consolidation.evidence_ref != self.sample.evidence_refs[0]:
            raise ValueError("metric request sample does not retain consolidation evidence")


class AccountMetricDerivationProvider(Protocol):
    """Injected deterministic reader; implementations must not operate the device."""

    def derive_account_metric_deltas(
        self,
        request: AccountMetricDerivationRequest,
    ) -> list[AccountMetricDeltaDerivationV1]: ...


class TaskSelectionV1(_StrictModel):
    task: FrontierTaskV1
    reason: str = Field(min_length=1)
    generated_from_existing_evidence: bool = False
    decision: TaskBoardDecisionV1 | None = None


class AutonomousCycleResultV1(_StrictModel):
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    next_task_id: str | None = Field(default=None, min_length=1)
    task_status: str = Field(min_length=1)
    transition_edge_id: str = Field(min_length=1)
    memory_id: str = Field(min_length=1)
    evidence_ref: EvidenceReferenceV1
    final_capsule_id: str = Field(min_length=1)
    selection_reason: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    recovered_from_interruption: bool = False
    idempotent_replay: bool = False
    physical_readiness_policy: PhysicalReadinessExecutionPolicyV1 | None = None
    excluded_capabilities: tuple[str, str] = (
        "实时攻略研究",
        "技能结晶",
    )


class AutonomousExecutionInterrupted(RuntimeError):
    def __init__(self, capsule_id: str, cause: Exception) -> None:
        super().__init__(
            f"外部执行在返回终态证据前中断；已保留检查点 {capsule_id}，恢复时禁止重放动作。"
        )
        self.code = "executor_interrupted"
        self.capsule_id = capsule_id
        self.cause = cause


def autonomous_request_sha256(command: AutonomousExecutionCommandV1) -> str:
    payload = command.model_dump(mode="json", by_alias=True)
    if payload.get("target_bounds") is None:
        payload.pop("target_bounds", None)
    if payload.get("interaction_preflight") is None:
        payload.pop("interaction_preflight", None)
    if payload.get("action_history_override") is None:
        payload.pop("action_history_override", None)
    if payload.get("decision_telemetry") is None:
        payload.pop("decision_telemetry", None)
    if payload.get("account_policy_id") is None:
        payload.pop("account_policy_id", None)
    if payload.get("account_policy_version") is None:
        payload.pop("account_policy_version", None)
    if payload.get("account_action_intent") is None:
        payload.pop("account_action_intent", None)
    body = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


class AutonomousOrchestrator:
    """One-command autonomous loop over existing canonical store interfaces."""

    PENDING_CAPSULE_PREFIX = "capsule.autonomous-pending"
    RESOLVED_CAPSULE_PREFIX = "capsule.autonomous-resolved"
    FINAL_CAPSULE_PREFIX = "capsule.autonomous-final"

    def __init__(
        self,
        player_store: AIPlayerStore,
        *,
        account_metric_provider: AccountMetricDerivationProvider | None = None,
        physical_readiness_evaluator: PhysicalReadinessEvaluator | None = None,
    ) -> None:
        self.player_store = player_store
        self._uses_default_account_metric_provider = account_metric_provider is None
        self.account_metric_provider = account_metric_provider or CanonicalAccountMetricProvider(
            player_store
        )
        self.sessions = AIPlayerSessionControl(player_store)
        self.consolidator = ExecutionConsolidator(player_store)
        self.recovery = AutonomousRecovery(player_store)
        self.task_board = TaskBoard()
        self.frontier = FrontierGenerator(player_store)
        self.action_history = ActionHistoryGuard(player_store)
        self.physical_readiness = (
            physical_readiness_evaluator or PhysicalReadinessEvaluator()
        )

    @staticmethod
    def _request_sha256(command: AutonomousExecutionCommandV1) -> str:
        return autonomous_request_sha256(command)

    def _authorize_account_action(
        self,
        command: AutonomousExecutionCommandV1,
    ) -> AccountActionDecisionV1 | None:
        if command.action.type == "wait" and command.account_action_intent is None:
            return None
        if (
            command.account_policy_id is None
            or command.account_policy_version is None
            or command.account_action_intent is None
        ):
            raise RecoveryRequiredError(
                "account_action_intent_missing",
                "非观察动作缺少 canonical 账号策略和行为意图，已在设备访问前停止。",
            )
        policy = self.player_store.get_account_policy(
            command.environment_id,
            policy_id=command.account_policy_id,
            version=command.account_policy_version,
        )
        latest = self.player_store.get_account_policy(command.environment_id)
        if policy is None or latest is None:
            raise RecoveryRequiredError(
                "account_policy_missing",
                "命令绑定的 canonical 账号策略不存在，已在设备访问前停止。",
            )
        if policy.id != latest.id or policy.version != latest.version:
            raise RecoveryRequiredError(
                "account_policy_stale",
                "命令绑定的账号策略已不是最新版本，必须重新裁决后再执行。",
            )
        decision = evaluate_account_action(command.account_action_intent, policy)
        if decision.disposition == "awaiting_authorization":
            raise RecoveryRequiredError(
                "account_action_authorization_required",
                decision.reason,
            )
        if decision.disposition != "autonomous":
            raise RecoveryRequiredError("account_action_rejected", decision.reason)
        return decision

    def _validate_before_reservation(
        self,
        command: AutonomousExecutionCommandV1,
        executor: AutonomousCommandExecutor,
        *,
        environment: EnvironmentScopeV1,
    ) -> None:
        """Run the physical executor's read-only gate before any journal or budget write."""

        if command.decision_telemetry is None:
            raise RecoveryRequiredError(
                "decision_telemetry_required",
                "新动作缺少 token 与决策时延遥测，未写入检查点、未扣动作预算。",
            )
        test_only_bypass = getattr(executor, "test_only_preflight_bypass", False) is True
        if test_only_bypass:
            if not _isolated_test_environment_allows_preflight_bypass(environment):
                raise RecoveryRequiredError(
                    "executor_preflight_bypass_forbidden",
                    "只有无真实设备、账号、服务器和世界身份的隔离测试环境才可显式绕过预检能力；"
                    "未写入检查点、未扣动作预算。",
                )
            return
        validator = getattr(executor, "validate_before_reservation", None)
        if not callable(validator):
            raise RecoveryRequiredError(
                "executor_preflight_capability_required",
                "物理执行器没有只读预检能力，未写入检查点、未扣动作预算。",
            )
        try:
            receipt = validator(command)
        except Exception as exc:
            raise RecoveryRequiredError(
                "interaction_preflight_rejected",
                f"交互预检未通过，未写入检查点、未扣动作预算：{exc}",
            ) from exc
        if not isinstance(receipt, ExecutorPreflightReceiptV1):
            raise RecoveryRequiredError(
                "executor_preflight_receipt_invalid",
                "物理执行器没有返回可核验的预检回执，未写入检查点、未扣动作预算。",
            )
        expected_pointer_check = command.action.type in _POINTER_ACTION_TYPES
        if (
            receipt.command_id != command.command_id
            or receipt.request_sha256 != self._request_sha256(command)
            or receipt.action_type != command.action.type
            or receipt.pointer_preflight_checked != expected_pointer_check
        ):
            raise RecoveryRequiredError(
                "executor_preflight_receipt_invalid",
                "物理执行器预检回执与当前命令不一致，未写入检查点、未扣动作预算。",
            )
        if environment.channel not in _TEST_EXECUTOR_CHANNELS and receipt.issuer != "device_executor":
            raise RecoveryRequiredError(
                "executor_preflight_receipt_invalid",
                "正式环境拒绝测试执行器签发的预检回执，未写入检查点、未扣动作预算。",
            )

        if not expected_pointer_check:
            return
        preflight = command.interaction_preflight
        if preflight is None:
            raise RecoveryRequiredError(
                "interaction_preflight_rejected",
                "指针动作缺少交互预检，未写入检查点、未扣动作预算。",
            )
        observation = self.player_store.get_state_observation(
            command.environment_id,
            preflight.recognition_observation_id,
        )
        observations = self.player_store.list_state_observations(command.environment_id)
        if observation is None or not observations or observations[-1].id != observation.id:
            raise RecoveryRequiredError(
                "interaction_observation_stale",
                "交互预检没有绑定当前最新 canonical 状态观测。",
            )
        assignment = self.player_store.get_current_state_assignment(
            command.environment_id,
            observation.id,
        )
        if (
            assignment is None
            or assignment.status != "active"
            or assignment.state_id != preflight.captured_state_id
        ):
            raise RecoveryRequiredError(
                "interaction_state_unbound",
                "交互预检中的语义状态与当前 canonical 状态分配不一致。",
            )
        observation_artifact_ids = {
            artifact_id
            for reference in observation.evidence_refs
            for artifact_id in reference.artifact_ids
        }
        if preflight.source_artifact_id not in observation_artifact_ids:
            raise RecoveryRequiredError(
                "interaction_source_unbound",
                "交互原图不属于当前 canonical 状态观测的证据。",
            )
        try:
            validate_interaction_preflight(
                preflight,
                action=command.action,
                target_bounds=command.target_bounds,
                viewport_width=environment.viewport_width,
                viewport_height=environment.viewport_height,
                environment_id=command.environment_id,
                source_artifact=self.player_store.observatory_store.get_artifact(
                    preflight.source_artifact_id
                ),
                local_evidence_artifact=self.player_store.observatory_store.get_artifact(
                    preflight.local_evidence_artifact_id
                ),
            )
        except (InteractionPreflightError, ValueError) as exc:
            raise RecoveryRequiredError(
                "interaction_preflight_rejected",
                f"canonical 交互预检未通过，未写入检查点、未扣动作预算：{exc}",
            ) from exc

    def _enforce_iteration_gate(self, environment_id: str) -> None:
        assessments = self.player_store.list_iteration_assessments(
            environment_id,
            limit=1,
        )
        latest = assessments[0] if assessments else None
        gate = resolve_iteration_remediation_gate(
            self.player_store,
            environment_id=environment_id,
            assessment=latest,
        )
        if gate.status in {"open", "remediated"}:
            return
        code = {
            "shadow_only": "iteration_shadow_only",
            "pause_physical_and_repair_perception_executor": (
                "iteration_perception_repair_required"
            ),
            "revise_planner_and_task_policy": "iteration_planner_repair_required",
            "refresh_guides_and_reprioritize_objectives": (
                "iteration_guide_refresh_required"
            ),
            "expand_discovery_frontier": "iteration_frontier_expansion_required",
        }[gate.directive]
        raise RecoveryRequiredError(
            code,
            "持续迭代门已阻止新的实体动作。"
            f"评估 {gate.assessment_id} 要求 {gate.directive}；"
            f"处理器 {gate.handler}，工作项 {gate.required_work_item or '无'}。"
            f"{gate.reason}",
        )

    def _enforce_authoritative_device_gate(self, environment_id: str) -> None:
        focus = resolve_authoritative_task_focus(
            self.player_store,
            environment_id=environment_id,
        )
        if focus.device_action_gate in OPEN_DEVICE_ACTION_GATES:
            return
        raise RecoveryRequiredError(
            "authoritative_device_gate_closed",
            (
                f"设备动作门为 {focus.device_action_gate}："
                f"{focus.device_action_gate_reason}"
            ),
        )

    @staticmethod
    def _is_protected_high_consequence_action(
        command: AutonomousExecutionCommandV1,
    ) -> bool:
        intent = command.account_action_intent
        if intent is None:
            return False
        return bool(
            intent.involves_real_money
            or intent.submits_external_personal_identity
            or intent.category
            in {
                "real_money_payment",
                "external_personal_identity_submission",
            }
        )

    def _enforce_physical_readiness_gate(
        self,
        command: AutonomousExecutionCommandV1,
    ) -> PhysicalReadinessExecutionPolicyV1:
        """Apply AFK readiness as a risk-tiered hard gate or runtime warning."""

        environment = self.player_store.get_environment(command.environment_id)
        if environment is None:
            raise RecoveryRequiredError(
                "physical_readiness_environment_missing",
                "实体游戏环境不存在，无法评估 AFK 实体基准。",
            )
        gate = self.physical_readiness.evaluate(environment)
        reason_code = str(getattr(gate, "reason_code", "benchmark_passed"))
        benchmark_verdict = getattr(gate, "benchmark_verdict", None)
        gap_codes = [
            str(item["code"])
            for item in list(getattr(gate, "gaps", []) or [])
            if isinstance(item, dict) and item.get("code")
        ]
        if gate.physical_play_unlocked:
            return PhysicalReadinessExecutionPolicyV1(
                disposition="ready",
                consequence_tier="benchmark_ready",
                benchmark_reason_code=reason_code,
                benchmark_verdict=benchmark_verdict,
                benchmark_gap_codes=gap_codes,
                e2e_sample_required=False,
            )
        if self._is_protected_high_consequence_action(command):
            raise RecoveryRequiredError(
                "physical_readiness_high_consequence_blocked",
                "AFK 实体基准尚未通过；真实货币支付和外部个人身份资料提交保持硬阻断。",
            )
        return PhysicalReadinessExecutionPolicyV1(
            disposition="warning_e2e_required",
            consequence_tier="low_consequence_gameplay",
            benchmark_reason_code=reason_code,
            benchmark_verdict=benchmark_verdict,
            benchmark_gap_codes=gap_codes,
            warning=(
                "AFK 实体基准尚未完全冻结；本次普通游戏内操作必须保留运行前预期、"
                "真实执行和运行后检查，并进入 E2E 动作质量样本。"
            ),
            e2e_sample_required=True,
        )

    @staticmethod
    def _decision_telemetry_payload(
        telemetry: ActionDecisionTelemetryV1,
    ) -> dict[str, object]:
        return telemetry.model_dump(mode="json", by_alias=True)

    def _planner_receipt_payload(
        self,
        receipt: PlannerMeasurementReceiptV1 | None,
    ) -> dict[str, object] | None:
        if receipt is None:
            return None
        artifact = self.player_store.observatory_store.get_artifact(receipt.artifact_id)
        if artifact is None:
            raise RecoveryRequiredError(
                "planner_measurement_artifact_missing",
                "planner 测量凭据引用的 artifact 已缺失。",
            )
        return {
            "receipt": receipt.model_dump(mode="json", by_alias=True),
            "artifact_id": artifact.id,
            "artifact_sha256": artifact.sha256,
        }

    def _resolved_planner_measurement(
        self,
        command: AutonomousExecutionCommandV1,
        *,
        task_id: str | None = None,
    ) -> tuple[ActionDecisionTelemetryV1, PlannerMeasurementReceiptV1 | None]:
        environment = self.player_store.get_environment(command.environment_id)
        if environment is None:
            raise RecoveryRequiredError(
                "planner_measurement_environment_missing",
                "planner 测量无法绑定不存在的环境。",
            )
        try:
            return resolve_planner_telemetry(
                self.player_store,
                environment=environment,
                command=command,
                task_id=task_id,
            )
        except ValueError as error:
            raise RecoveryRequiredError(
                "planner_measurement_required",
                f"planner token 与延迟测量不可验证：{error}",
            ) from error

    def _action_quality_history_snapshot(
        self,
        *,
        command: AutonomousExecutionCommandV1,
        request_sha256: str,
        task: FrontierTaskV1,
        known_state_ids_before_command: list[str],
        known_transition_ids_before_command: list[str],
        matched_transition_ids: list[str],
    ) -> ActionQualityHistorySnapshotV1:
        return ActionQualityHistorySnapshotV1(
            environment_id=command.environment_id,
            command_id=command.command_id,
            request_sha256=request_sha256,
            session_id=command.session_id,
            task_id=task.id,
            known_state_ids_before_command=known_state_ids_before_command,
            known_transition_ids_before_command=known_transition_ids_before_command,
            matched_transition_ids=matched_transition_ids,
        )

    def _history_snapshot_from_outcome(
        self,
        *,
        command: AutonomousExecutionCommandV1,
        outcome: CanonicalExecutionOutcomeV1,
        task: FrontierTaskV1,
    ) -> ActionQualityHistorySnapshotV1:
        raw = outcome.evidence_step.metadata.get("action_quality_history")
        if not isinstance(raw, dict):
            raise RecoveryRequiredError(
                "action_quality_history_missing",
                "canonical 动作证据缺少动作前质量历史快照，无法安全补写监控记录。",
            )
        snapshot = ActionQualityHistorySnapshotV1.model_validate(raw)
        expected = (
            command.environment_id,
            command.command_id,
            self._request_sha256(command),
            command.session_id,
            task.id,
        )
        actual = (
            snapshot.environment_id,
            snapshot.command_id,
            snapshot.request_sha256,
            snapshot.session_id,
            snapshot.task_id,
        )
        if actual != expected:
            raise RecoveryRequiredError(
                "action_quality_history_mismatch",
                "canonical 动作证据中的质量历史快照与当前幂等命令不一致。",
            )
        return snapshot

    def _ensure_action_quality_assessments(
        self,
        *,
        sample_id: str,
        environment_id: str,
        session_id: str,
        task_completed: bool,
    ) -> None:
        monitor = PlayerIterationMonitor(self.player_store)
        stored = self.player_store.get_action_quality_sample(environment_id, sample_id)
        if stored is None:
            raise ValueError("action-quality sample disappeared before assessment reconciliation")
        ordered = list(
            reversed(
                self.player_store.list_action_quality_samples(
                    environment_id,
                    session_id=session_id,
                    limit=1_000_000,
                )
            )
        )
        position = next(
            (index for index, item in enumerate(ordered) if item.id == stored.id),
            None,
        )
        if position is None:
            raise ValueError("action-quality sample is not present in its canonical session")
        window_size = monitor.policy.actions_per_review
        if (position + 1) % window_size == 0:
            window = ordered[position + 1 - window_size : position + 1]
            monitor.assess(environment_id, "actions_10", [item.id for item in window])
        incident = any(
            (
                stored.policy_violation,
                stored.invalid_target_execution,
                not stored.evidence_complete and stored.execution_disposition == "executed",
                stored.prior_cluster_failures >= 2,
                stored.outcome
                in {"blocked_by_overlay", "wrong_target", "failed"},
            )
        )
        if incident:
            monitor.assess(environment_id, "incident", [stored.id])
        if task_completed:
            monitor.assess(environment_id, "verified_task", [stored.id])

    def _record_action_quality(
        self,
        *,
        command: AutonomousExecutionCommandV1,
        outcome: CanonicalExecutionOutcomeV1,
        consolidation: ConsolidationResultV1,
        session: AIPlayerSessionV1,
        task: FrontierTaskV1,
        history_snapshot: ActionQualityHistorySnapshotV1,
    ) -> None:
        result_transition = self.player_store.get_transition_edge(
            command.environment_id,
            consolidation.transition_edge_id,
        )
        if result_transition is None:
            raise ValueError("归并后的 canonical 状态转移不存在，无法生成动作质量样本。")
        matching_prior = [
            transition
            for transition_id in history_snapshot.matched_transition_ids
            if (
                transition := self.player_store.get_transition_edge(
                    command.environment_id,
                    transition_id,
                )
            )
            is not None
        ]
        if len(matching_prior) != len(history_snapshot.matched_transition_ids):
            raise ValueError("动作前质量历史快照引用的 canonical 转移已经缺失")
        task_decision = self.task_board.select(
            self.player_store.list_tasks(command.environment_id)
        )
        selectable_task_ids = [
            item.task_id
            for item in task_decision.dispositions
            if item.disposition == "eligible"
        ]
        history_context = ActionQualityHistoryContextV1(
            environment_id=command.environment_id,
            command_id=command.command_id,
            result_transition=result_transition,
            known_state_ids_before_command=history_snapshot.known_state_ids_before_command,
            known_transition_ids_before_command=(
                history_snapshot.known_transition_ids_before_command
            ),
            matching_prior_transitions=matching_prior,
            selectable_task_ids_after_consolidation=selectable_task_ids,
        )
        telemetry, _receipt = self._resolved_planner_measurement(
            command,
            task_id=task.id,
        )
        sample = produce_action_quality_sample(
            command=command,
            outcome=outcome,
            consolidation=consolidation,
            session=session,
            task=task,
            history=history_context,
            telemetry=telemetry,
        )
        sample = self._attach_account_metric_derivations(
            command=command,
            task=task,
            consolidation=consolidation,
            sample=sample,
        )
        sample = attach_expected_change_measurement(
            self.player_store.observatory_store,
            sample=sample,
            command=command,
            outcome=outcome,
        )
        monitor = PlayerIterationMonitor(self.player_store)
        stored, _assessment = monitor.record(sample)
        self._ensure_action_quality_assessments(
            sample_id=stored.id,
            environment_id=command.environment_id,
            session_id=command.session_id,
            task_completed=consolidation.task_status == "completed",
        )

    def _persisted_metric_derivations_for_step(
        self,
        environment_id: str,
        evidence_step_id: str,
    ) -> list[AccountMetricDeltaDerivationV1]:
        derivations = self.player_store.list_account_metric_derivations(
            environment_id,
            limit=1_000_000,
        )
        return sorted(
            (
                item
                for item in derivations
                if item.after_observation.evidence_step_id == evidence_step_id
            ),
            key=lambda item: (item.definition.metric_key, item.id),
        )

    @staticmethod
    def _validated_provider_derivations(
        produced: object,
    ) -> list[AccountMetricDeltaDerivationV1]:
        if not isinstance(produced, list):
            raise TypeError("account metric provider must return a concrete list")
        if not all(isinstance(item, AccountMetricDeltaDerivationV1) for item in produced):
            raise TypeError("account metric provider returned an invalid derivation type")
        validated = [
            AccountMetricDeltaDerivationV1.model_validate_json(
                item.model_dump_json(by_alias=True)
            )
            for item in produced
        ]
        ids = [item.id for item in validated]
        if len(ids) != len(set(ids)):
            raise ValueError("account metric provider returned duplicate derivation ids")
        return sorted(
            validated,
            key=lambda item: (item.definition.metric_key, item.id),
        )

    def _attach_account_metric_derivations(
        self,
        *,
        command: AutonomousExecutionCommandV1,
        task: FrontierTaskV1,
        consolidation: ConsolidationResultV1,
        sample: ActionQualitySampleV1,
    ) -> ActionQualitySampleV1:
        if sample.evidence_step_id is None:
            return sample
        persisted = self._persisted_metric_derivations_for_step(
            command.environment_id,
            sample.evidence_step_id,
        )
        provider = self.account_metric_provider
        if provider is None:
            return attach_account_metric_derivations(
                self.player_store.observatory_store,
                sample,
                persisted,
            )

        request = AccountMetricDerivationRequest(
            command=command,
            task=task,
            consolidation=consolidation,
            sample=sample,
        )
        produced = self._validated_provider_derivations(
            provider.derive_account_metric_deltas(request)
        )
        persisted_by_id = {item.id: item for item in persisted}
        produced_by_id = {item.id: item for item in produced}
        if self._uses_default_account_metric_provider:
            # A crash-repair replay may reopen frozen, canonically validated
            # derivations created by a versioned provider that is no longer loaded.
            # Keep those immutable facts while still discovering current defaults.
            produced_by_id = {**persisted_by_id, **produced_by_id}
            produced = sorted(
                produced_by_id.values(),
                key=lambda item: (item.definition.metric_key, item.id),
            )
        unexpected_ids = sorted(set(persisted_by_id).difference(produced_by_id))
        if unexpected_ids:
            raise ValueError(
                "account metric provider omitted already persisted derivations: "
                + ", ".join(unexpected_ids)
            )
        for derivation_id, existing in persisted_by_id.items():
            if existing != produced_by_id[derivation_id]:
                raise ValueError(
                    "account metric provider contradicted persisted derivation: "
                    f"{derivation_id}"
                )

        # Validate the complete provider batch against canonical artifacts before
        # allowing any part of it into the durable derivation ledger.
        attached = attach_account_metric_derivations(
            self.player_store.observatory_store,
            sample,
            produced,
        )
        for derivation in produced:
            if derivation.id not in persisted_by_id:
                self.player_store.append_account_metric_derivation(derivation)
        return attached

    def _reconcile_completed_action_quality(
        self,
        *,
        command: AutonomousExecutionCommandV1,
        consolidation: ConsolidationResultV1,
        session: AIPlayerSessionV1,
    ) -> None:
        sample_id = stable_action_quality_sample_id(command.command_id)
        existing = self.player_store.get_action_quality_sample(
            command.environment_id,
            sample_id,
        )
        if existing is not None:
            if (
                existing.command_id != command.command_id
                or existing.session_id != command.session_id
                or existing.task_id != consolidation.task_id
                or existing.evidence_refs != [consolidation.evidence_ref]
            ):
                raise ValueError("existing action-quality sample contradicts replay consolidation")
            self._ensure_action_quality_assessments(
                sample_id=existing.id,
                environment_id=command.environment_id,
                session_id=command.session_id,
                task_completed=consolidation.task_status == "completed",
            )
            return
        task = self.player_store.get_task(command.environment_id, consolidation.task_id)
        if task is None:
            raise ValueError("replay consolidation task is missing")
        resolved_id = stable_command_id(self.RESOLVED_CAPSULE_PREFIX, command.command_id)
        resolved = self.player_store.get_session_capsule(command.environment_id, resolved_id)
        if resolved is None:
            raise RecoveryRequiredError(
                "resolved_capsule_missing",
                "canonical 归并已经完成，但缺少可重建监控样本的 resolved checkpoint。",
            )
        outcome, _evidence_ref, task_id = self._preflight_resolved_capsule(
            resolved,
            self.player_store.get_session_capsule(
                command.environment_id,
                stable_command_id(self.PENDING_CAPSULE_PREFIX, command.command_id),
            ),
            command,
            self._request_sha256(command),
        )
        if task_id != task.id:
            raise ValueError("resolved checkpoint task contradicts replay consolidation")
        history_snapshot = self._history_snapshot_from_outcome(
            command=command,
            outcome=outcome,
            task=task,
        )
        self._record_action_quality(
            command=command,
            outcome=outcome,
            consolidation=consolidation,
            session=session,
            task=task,
            history_snapshot=history_snapshot,
        )

    def select_next_task(
        self,
        *,
        environment_id: str,
        command_id: str,
    ) -> TaskSelectionV1:
        """Select a ready canonical task or create one evidence-only coverage audit."""

        if self.player_store.get_environment(environment_id) is None:
            raise ValueError("当前 AI 玩家环境不存在")
        focus = resolve_authoritative_task_focus(
            self.player_store,
            environment_id=environment_id,
        )
        tasks = self.player_store.list_tasks(environment_id)
        decision = self.task_board.select(
            tasks,
            preferred_task_ids=focus.preferred_task_ids,
            deferred_task_ids=focus.deferred_task_ids,
            prefer_active=True,
            restrict_to_preferred=focus.restrict_to_preferred,
        )
        generated = False
        selected = (
            self.player_store.get_task(environment_id, decision.selected_task_id)
            if decision.selected_task_id is not None
            else None
        )
        audit_before_repair = bool(
            selected is not None
            and selected.status != "active"
            and selected.source
            in {"failed_skill", "stale_memory", "missing_transition", "coverage_gap"}
        )
        if decision.selected_task_id is None or audit_before_repair:
            audited = self.frontier.generate(environment_id)
            generated = bool(audited.generated_task_ids)
            if decision.selected_task_id is None or generated:
                decision = self.task_board.select(
                    self.player_store.list_tasks(environment_id),
                    preferred_task_ids=focus.preferred_task_ids,
                    deferred_task_ids=focus.deferred_task_ids,
                    prefer_active=True,
                    restrict_to_preferred=focus.restrict_to_preferred,
                )
        if decision.selected_task_id is None:
            signal = FrontierSignalV1(
                source="coverage_gap",
                subject_id=f"autonomous-command:{command_id}",
                title="复核当前已知状态的未闭合证据",
                reason=(
                    "当前没有可执行的 canonical 前沿任务；下一步只复核已有状态和证据能否"
                    "形成一个可验证缺口，不预设界面、玩法或规则事实。"
                ),
                evidence_refs=self._fallback_evidence(environment_id),
                value_score=1.0,
                novelty_score=0.5,
                expected_coverage_gain=1.0,
                risk_score=0.0,
                action_budget=1,
                time_budget_seconds=60,
                max_attempts=1,
            )
            generated_result = self.frontier.generate(environment_id, signals=[signal])
            generated = generated or bool(generated_result.generated_task_ids)
            decision = self.task_board.select(self.player_store.list_tasks(environment_id))
        if decision.selected_task_id is None:
            blockers = "；".join(named_blockers(decision)) or decision.reason
            raise ValueError(f"覆盖审计后仍没有可执行任务：{blockers}")
        task = self.player_store.get_task(environment_id, decision.selected_task_id)
        if task is None:
            raise ValueError("TaskBoard 选中的 canonical task 已经失效")
        return TaskSelectionV1(
            task=task,
            reason=decision.reason,
            generated_from_existing_evidence=generated,
            decision=decision,
        )

    def _append_capsule_once(self, capsule: SessionCapsuleV1) -> SessionCapsuleV1:
        existing = self.player_store.get_session_capsule(capsule.environment_id, capsule.id)
        if existing is not None:
            if existing != capsule:
                raise ValueError("检查点 id 已存在且内容不同")
            return existing
        return self.player_store.append_session_capsule(capsule)

    def _next_sequence(self, environment_id: str, session_id: str) -> int:
        latest = self.player_store.get_latest_session_capsule(
            environment_id,
            session_id=session_id,
        )
        return 1 if latest is None else latest.sequence + 1

    def _checkpoint_session(
        self,
        session: AIPlayerSessionV1,
        capsule: SessionCapsuleV1,
        *,
        command_id: str,
        consume_action: bool,
        active_task_ids: list[str],
        evidence_refs: list[EvidenceReferenceV1],
    ) -> AIPlayerSessionV1:
        current = self.sessions.get_session(session.environment_id, session.id)
        if current is None:
            raise AIPlayerSessionError("session_not_found", "当前 AI 玩家会话不存在。")
        if current.last_capsule_id == capsule.id:
            return current
        if current.state != "running":
            raise AIPlayerSessionError("session_not_running", "只有运行中的会话可以推进自主执行。")
        remaining_actions = current.remaining_action_budget - (1 if consume_action else 0)
        if remaining_actions < 0:
            raise AIPlayerSessionError("budget_exhausted", "会话动作预算已经耗尽。")
        return self.sessions.checkpoint(
            current.id,
            AIPlayerSessionCheckpointCommand(
                command_id=command_id,
                environment_id=current.environment_id,
                expected_version=current.version,
                actor="ai-player.autonomous-kernel",
                reason=capsule.stop_reason,
                remaining_action_budget=remaining_actions,
                remaining_token_budget=current.remaining_token_budget,
                remaining_time_seconds=current.remaining_time_seconds,
                active_task_ids=active_task_ids,
                last_capsule_id=capsule.id,
                last_evidence_refs=evidence_refs,
            ),
        )

    def _pending_capsule(
        self,
        *,
        command: AutonomousExecutionCommandV1,
        session: AIPlayerSessionV1,
        task: FrontierTaskV1,
    ) -> SessionCapsuleV1:
        capsule_id = stable_command_id(self.PENDING_CAPSULE_PREFIX, command.command_id)
        existing = self.player_store.get_session_capsule(command.environment_id, capsule_id)
        if existing is not None:
            return existing
        latest = self.player_store.get_latest_session_capsule(
            command.environment_id,
            session_id=command.session_id,
        )
        pending = PendingActionV1(
            id=command.command_id,
            request_sha256=self._request_sha256(command),
            environment_id=command.environment_id,
            evidence_refs=task.evidence_refs,
            intent=command.intent,
            action=command.action,
        )
        return SessionCapsuleV1(
            id=capsule_id,
            environment_id=command.environment_id,
            evidence_refs=task.evidence_refs,
            session_id=command.session_id,
            sequence=self._next_sequence(command.environment_id, command.session_id),
            last_confirmed_state_id=latest.last_confirmed_state_id if latest else None,
            active_task_ids=[task.id],
            subgoal_stack=[task.title],
            remaining_action_budget=max(0, session.remaining_action_budget - 1),
            remaining_token_budget=session.remaining_token_budget,
            remaining_time_seconds=session.remaining_time_seconds,
            pending_frontier_task_ids=[task.id],
            known_external_side_effects=[],
            pending_action=pending,
            stop_reason="执行意图已持久化；若进程中断，先重观察动作效果，禁止盲目重放。",
        )

    def _resolved_capsule(
        self,
        *,
        command: AutonomousExecutionCommandV1,
        session: AIPlayerSessionV1,
        task: FrontierTaskV1,
        outcome: CanonicalExecutionOutcomeV1,
        evidence_ref: EvidenceReferenceV1,
    ) -> SessionCapsuleV1:
        capsule_id = stable_command_id(self.RESOLVED_CAPSULE_PREFIX, command.command_id)
        existing = self.player_store.get_session_capsule(command.environment_id, capsule_id)
        if existing is not None:
            return existing
        latest = self.player_store.get_latest_session_capsule(
            command.environment_id,
            session_id=command.session_id,
        )
        pending = PendingActionV1(
            id=command.command_id,
            request_sha256=self._request_sha256(command),
            environment_id=command.environment_id,
            evidence_refs=task.evidence_refs,
            intent=command.intent,
            action=command.action,
            action_run_id=outcome.action_run.id,
            effect_status="confirmed" if outcome.status == "succeeded" else "failed",
            after_evidence_refs=[evidence_ref],
            effect_checked_at=outcome.evidence_step.ended_at or utc_now(),
            result_summary=outcome.observed_change,
        )
        return SessionCapsuleV1(
            id=capsule_id,
            environment_id=command.environment_id,
            evidence_refs=[evidence_ref],
            session_id=command.session_id,
            sequence=self._next_sequence(command.environment_id, command.session_id),
            last_confirmed_state_id=latest.last_confirmed_state_id if latest else None,
            active_task_ids=[task.id],
            subgoal_stack=[task.title],
            remaining_action_budget=session.remaining_action_budget,
            remaining_token_budget=session.remaining_token_budget,
            remaining_time_seconds=session.remaining_time_seconds,
            pending_frontier_task_ids=[task.id],
            known_external_side_effects=[outcome.observed_change],
            pending_action=pending,
            stop_reason="动作效果已有终态证据；尚待完成 canonical 归并。",
        )

    def _final_capsule(
        self,
        *,
        command: AutonomousExecutionCommandV1,
        session: AIPlayerSessionV1,
        consolidation: ConsolidationResultV1,
    ) -> SessionCapsuleV1:
        capsule_id = stable_command_id(self.FINAL_CAPSULE_PREFIX, command.command_id)
        existing = self.player_store.get_session_capsule(command.environment_id, capsule_id)
        if existing is not None:
            return existing
        next_task = (
            self.player_store.get_task(command.environment_id, consolidation.next_task_id)
            if consolidation.next_task_id is not None
            else None
        )
        if consolidation.next_task_id is not None and next_task is None:
            raise ValueError("归并结果指向的下一任务不存在")
        return SessionCapsuleV1(
            id=capsule_id,
            environment_id=command.environment_id,
            evidence_refs=[consolidation.evidence_ref],
            session_id=command.session_id,
            sequence=self._next_sequence(command.environment_id, command.session_id),
            last_confirmed_state_id=consolidation.after_state_id,
            active_task_ids=[],
            subgoal_stack=[],
            remaining_action_budget=session.remaining_action_budget,
            remaining_token_budget=session.remaining_token_budget,
            remaining_time_seconds=session.remaining_time_seconds,
            pending_frontier_task_ids=[next_task.id] if next_task is not None else [],
            known_external_side_effects=[consolidation.reason],
            pending_action=None,
            stop_reason="本次动作已完成证据、状态、任务与记忆写回，可以选择下一任务。",
        )

    def _next_task(
        self,
        *,
        environment_id: str,
        current_task_id: str,
        evidence_ref: EvidenceReferenceV1,
        after_state: SemanticStateV1,
    ) -> FrontierTaskV1 | None:
        tasks = self.player_store.list_tasks(environment_id)
        projected = [
            task.model_copy(update={"status": "completed"})
            if task.id == current_task_id
            else task
            for task in tasks
        ]
        focus = resolve_authoritative_task_focus(
            self.player_store,
            environment_id=environment_id,
        )
        decision = self.task_board.select(
            projected,
            preferred_task_ids=focus.preferred_task_ids,
            deferred_task_ids=focus.deferred_task_ids,
            prefer_active=True,
            restrict_to_preferred=focus.restrict_to_preferred,
        )
        if decision.selected_task_id is not None:
            selected = self.player_store.get_task(environment_id, decision.selected_task_id)
            if selected is None:
                raise ValueError("TaskBoard 选中的下一任务已经失效")
            return selected
        if after_state.status != "candidate":
            return None
        title = f"补证并复核界面状态：{after_state.title}"
        reason = (
            f"语义状态 {after_state.id}@{after_state.version} 仍是候选；需要补齐界面身份、"
            "关键可交互项、入口、出口或与相邻状态的区别。"
        )
        signal = FrontierSignalV1(
            title=title,
            source="coverage_gap",
            subject_id=f"semantic-state:{after_state.id}@{after_state.version}",
            reason=reason,
            evidence_refs=[evidence_ref],
            value_score=1.0,
            novelty_score=0.5,
            expected_coverage_gain=0.5,
            risk_score=0.0,
            action_budget=1,
            time_budget_seconds=60,
            max_attempts=1,
        )
        generated = self.frontier.generate(environment_id, signals=[signal])
        task_ids = [*generated.generated_task_ids, *generated.existing_task_ids]
        if len(task_ids) != 1:
            raise ValueError("FrontierGenerator 未能唯一生成下一任务")
        task = self.player_store.get_task(environment_id, task_ids[0])
        if task is None:
            raise ValueError("FrontierGenerator 返回的下一任务不存在")
        return task

    def _outcome_from_resolved_capsule(
        self,
        capsule: SessionCapsuleV1,
    ) -> CanonicalExecutionOutcomeV1:
        pending = capsule.pending_action
        if pending is None or not pending.after_evidence_refs:
            raise ValueError("已解决检查点缺少动作结果证据")
        reference = pending.after_evidence_refs[0]
        if len(reference.evidence_run_ids) != 1 or len(reference.evidence_step_ids) != 1:
            raise ValueError("自主执行检查点必须精确绑定一个 EvidenceRun 和 EvidenceStep")
        run = self.player_store.observatory_store.get_evidence_run(reference.evidence_run_ids[0])
        step = self.player_store.observatory_store.get_evidence_step(reference.evidence_step_ids[0])
        if run is None or step is None or step.action_run_id is None:
            raise ValueError("检查点引用的终态证据已经失效")
        action_run = self.player_store.observatory_store.get_run(step.action_run_id)
        if action_run is None:
            raise ValueError("检查点引用的 action run 已经失效")
        artifacts = []
        for artifact_id in reference.artifact_ids:
            artifact = self.player_store.observatory_store.get_artifact(artifact_id)
            if artifact is None:
                raise ValueError(f"检查点引用的 artifact 已经失效：{artifact_id}")
            artifacts.append(artifact)
        metadata = step.metadata.get("autonomous_execution")
        if not isinstance(metadata, dict):
            raise ValueError("EvidenceStep 缺少可恢复的自主执行元数据")
        return CanonicalExecutionOutcomeV1(
            environment_id=metadata["environment_id"],
            command_id=metadata["command_id"],
            task_id=metadata["task_id"],
            status=metadata["status"],
            evidence_run=run,
            evidence_step=step,
            artifacts=artifacts,
            action_run=action_run,
            before_state=SemanticStateV1.model_validate(metadata["before_state"]),
            after_state=SemanticStateV1.model_validate(metadata["after_state"]),
            observed_change=metadata["observed_change"],
            failure_reason=metadata.get("failure_reason"),
            recovered_from_interruption=bool(metadata.get("recovered_from_interruption")),
        )

    @staticmethod
    def _single_checkpoint_task_id(capsule: SessionCapsuleV1, *, label: str) -> str:
        if (
            len(capsule.active_task_ids) != 1
            or len(capsule.pending_frontier_task_ids) != 1
            or capsule.active_task_ids[0] != capsule.pending_frontier_task_ids[0]
        ):
            raise ValueError(
                f"{label} capsule must bind exactly one identical active/pending task"
            )
        return capsule.active_task_ids[0]

    def _preflight_resolved_capsule(
        self,
        resolved: SessionCapsuleV1,
        stable_pending: SessionCapsuleV1 | None,
        command: AutonomousExecutionCommandV1,
        request_sha256: str,
    ) -> tuple[CanonicalExecutionOutcomeV1, EvidenceReferenceV1, str]:
        def reference_signature(
            references: list[EvidenceReferenceV1],
        ) -> tuple[str, ...]:
            return tuple(
                self.consolidator._canonical_json(reference) for reference in references
            )

        pending = resolved.pending_action
        if (
            resolved.environment_id != command.environment_id
            or resolved.session_id != command.session_id
            or pending is None
            or pending.environment_id != command.environment_id
            or pending.id != command.command_id
            or pending.request_sha256 != request_sha256
            or pending.intent != command.intent
            or pending.action != command.action
            or pending.effect_status not in {"confirmed", "failed"}
        ):
            raise ValueError("resolved capsule does not exactly match this autonomous command")
        task_id = self._single_checkpoint_task_id(resolved, label="resolved")
        outcome = self._outcome_from_resolved_capsule(resolved)
        expected_status = "succeeded" if pending.effect_status == "confirmed" else "failed"
        expected_reference = self.consolidator._evidence_reference(outcome)
        metadata = outcome.evidence_step.metadata.get("autonomous_execution")
        if (
            outcome.environment_id != command.environment_id
            or outcome.command_id != command.command_id
            or outcome.task_id != task_id
            or outcome.evidence_step.action != command.action
            or outcome.evidence_step.action_run_id != outcome.action_run.id
            or pending.action_run_id is None
            or pending.action_run_id != outcome.action_run.id
            or outcome.status != expected_status
            or len(resolved.evidence_refs) != 1
            or self.consolidator._canonical_json(resolved.evidence_refs[0])
            != self.consolidator._canonical_json(expected_reference)
            or len(pending.after_evidence_refs) != 1
            or self.consolidator._canonical_json(pending.after_evidence_refs[0])
            != self.consolidator._canonical_json(expected_reference)
            or not isinstance(metadata, dict)
            or metadata.get("environment_id") != command.environment_id
            or metadata.get("command_id") != command.command_id
            or metadata.get("task_id") != task_id
        ):
            raise ValueError("resolved capsule outcome/evidence binding is inconsistent")

        if stable_pending is not None:
            stable_action = stable_pending.pending_action
            stable_task_id = self._single_checkpoint_task_id(
                stable_pending,
                label="pending",
            )
            if (
                stable_pending.environment_id != resolved.environment_id
                or stable_pending.session_id != resolved.session_id
                or stable_action is None
                or stable_action.environment_id != pending.environment_id
                or stable_action.id != pending.id
                or stable_action.request_sha256 != pending.request_sha256
                or stable_action.intent != pending.intent
                or stable_action.action != pending.action
                or stable_task_id != task_id
                or stable_action.effect_status != "unknown"
                or stable_action.action_run_id is not None
                or stable_action.effect_checked_at is not None
                or stable_action.result_summary is not None
                or stable_action.after_evidence_refs
                or stable_action.blind_replay_allowed
                or stable_action.resume_rule != "reobserve_before_any_action"
                or reference_signature(stable_pending.evidence_refs)
                != reference_signature(stable_action.evidence_refs)
                or reference_signature(pending.evidence_refs)
                != reference_signature(stable_action.evidence_refs)
            ):
                raise ValueError("stable pending capsule does not match resolved capsule")

        original_references = (
            stable_pending.pending_action.evidence_refs
            if stable_pending is not None and stable_pending.pending_action is not None
            else pending.evidence_refs
        )
        current_task = self.player_store.get_task(command.environment_id, task_id)
        if current_task is not None:
            if current_task.status in {"queued", "active", "cooldown"}:
                expected_task_references = original_references
            elif current_task.status in {"completed", "failed"}:
                expected_task_references = self.consolidator._merge_evidence_refs(
                    original_references,
                    expected_reference,
                )
            else:
                raise ValueError("resolved task has an unsupported status")
            if reference_signature(current_task.evidence_refs) != reference_signature(
                expected_task_references
            ):
                raise ValueError("resolved task evidence does not match original/result evidence")
        return outcome, expected_reference, task_id


    def _running_session_for_resolved_finalize(
        self,
        command: AutonomousExecutionCommandV1,
    ) -> AIPlayerSessionV1:
        session = self.sessions.get_session(command.environment_id, command.session_id)
        if session is None:
            raise AIPlayerSessionError(
                "session_not_found",
                "当前 AI 玩家会话不存在。",
                status_code=404,
            )
        if session.state != "running":
            code = (
                "session_terminal"
                if session.state in {"safe_stopped", "completed"}
                else "session_not_running"
            )
            raise AIPlayerSessionError(
                code,
                "resolved action 只能在仍处于 running 的原会话中完成归并。",
            )
        return session

    def _cycle_result(
        self,
        *,
        command: AutonomousExecutionCommandV1,
        consolidation: ConsolidationResultV1,
        final_capsule: SessionCapsuleV1,
        selection_reason: str,
        recovered: bool,
        physical_readiness_policy: PhysicalReadinessExecutionPolicyV1 | None = None,
    ) -> AutonomousCycleResultV1:
        return AutonomousCycleResultV1(
            environment_id=command.environment_id,
            session_id=command.session_id,
            command_id=command.command_id,
            task_id=consolidation.task_id,
            next_task_id=consolidation.next_task_id,
            task_status=consolidation.task_status,
            transition_edge_id=consolidation.transition_edge_id,
            memory_id=consolidation.memory_id,
            evidence_ref=consolidation.evidence_ref,
            final_capsule_id=final_capsule.id,
            selection_reason=selection_reason,
            reason=consolidation.reason,
            recovered_from_interruption=recovered,
            idempotent_replay=consolidation.idempotent_replay,
            physical_readiness_policy=physical_readiness_policy,
        )

    def run_once(
        self,
        command: AutonomousExecutionCommandV1,
        *,
        executor: AutonomousCommandExecutor | None,
        recovery_observer: PendingActionObserver | None = None,
    ) -> AutonomousCycleResultV1:
        """Run or recover exactly one canonical command."""

        request_sha256 = self._request_sha256(command)
        replay = self.consolidator.completed_result(
            environment_id=command.environment_id,
            command_id=command.command_id,
            request_sha256=request_sha256,
        )
        if replay is not None:
            memory = self.player_store.get_memory(command.environment_id, replay.memory_id)
            selection_reason = str(memory.payload["selection_reason"]) if memory else "幂等重放"
            session = self.sessions.get_session(command.environment_id, command.session_id)
            if session is None:
                raise AIPlayerSessionError("session_not_found", "当前 AI 玩家会话不存在。")
            final_capsule = self._final_capsule(
                command=command,
                session=session,
                consolidation=replay,
            )
            final_capsule = self._append_capsule_once(final_capsule)
            if session.state == "running":
                session = self._checkpoint_session(
                    session,
                    final_capsule,
                    command_id=stable_command_id("command.autonomous-final", command.command_id),
                    consume_action=False,
                    active_task_ids=[],
                    evidence_refs=[replay.evidence_ref],
                )
            self._reconcile_completed_action_quality(
                command=command,
                consolidation=replay,
                session=session,
            )
            return self._cycle_result(
                command=command,
                consolidation=replay,
                final_capsule=final_capsule,
                selection_reason=selection_reason,
                recovered=bool(memory and memory.payload.get("recovered_from_interruption")),
            )

        environment = self.player_store.get_environment(command.environment_id)
        if environment is None:
            raise ValueError("当前 AI 玩家环境不存在")
        if environment.identity_hash != command.expected_identity_hash:
            raise RecoveryRequiredError(
                "identity_mismatch",
                "当前游戏、账号或设备环境与命令绑定不一致，已拒绝执行。",
            )
        resolved_telemetry: ActionDecisionTelemetryV1 | None = None
        planner_receipt: PlannerMeasurementReceiptV1 | None = None
        known_state_ids_before_command = [
            item.id for item in self.player_store.list_semantic_states(command.environment_id)
        ]
        known_transition_ids_before_command = [
            item.id for item in self.player_store.list_transition_edges(command.environment_id)
        ]
        account_action_decision = self._authorize_account_action(command)
        history = self.action_history.evaluate(
            environment_id=command.environment_id,
            session_id=command.session_id,
            action=command.action,
            target_bounds=command.target_bounds,
        )
        if not history.allowed:
            raise RecoveryRequiredError(
                "repeated_failed_action",
                f"{history.reason} 历史转移：{', '.join(history.matched_transition_ids)}。"
                "新的截图编号不能绕过失败动作簇；请改变语义状态、目标或执行方案后生成新任务。",
            )
        pending_id = stable_command_id(self.PENDING_CAPSULE_PREFIX, command.command_id)
        resolved_id = stable_command_id(self.RESOLVED_CAPSULE_PREFIX, command.command_id)
        pending_capsule = self.player_store.get_session_capsule(command.environment_id, pending_id)
        resolved_capsule = self.player_store.get_session_capsule(
            command.environment_id, resolved_id
        )
        physical_readiness_policy: PhysicalReadinessExecutionPolicyV1 | None = None
        if pending_capsule is None and resolved_capsule is None:
            self._enforce_authoritative_device_gate(command.environment_id)
            if executor is None:
                raise RecoveryRequiredError("executor_required", "新命令必须注入外部执行器。")
            physical_readiness_policy = self._enforce_physical_readiness_gate(command)
            self._enforce_iteration_gate(command.environment_id)
            self._validate_before_reservation(
                command,
                executor,
                environment=environment,
            )
            resolved_telemetry, planner_receipt = self._resolved_planner_measurement(
                command
            )
        if resolved_telemetry is None:
            resolved_telemetry, planner_receipt = self._resolved_planner_measurement(
                command
            )
        resolved_outcome: CanonicalExecutionOutcomeV1 | None = None
        resolved_evidence_ref: EvidenceReferenceV1 | None = None
        resolved_task_id: str | None = None
        if resolved_capsule is not None:
            resolved_outcome, resolved_evidence_ref, resolved_task_id = (
                self._preflight_resolved_capsule(
                    resolved_capsule,
                    pending_capsule,
                    command,
                    request_sha256,
                )
            )
            session = self._running_session_for_resolved_finalize(command)
        else:
            session = self.sessions.assert_session_can_act(
                command.environment_id,
                command.session_id,
            )
        had_pending_capsule = pending_capsule is not None

        resume_capsule = resolved_capsule or pending_capsule
        if resume_capsule is not None:
            pending_action = resume_capsule.pending_action
            if (
                pending_action is None
                or pending_action.request_sha256 != request_sha256
                or pending_action.id != command.command_id
                or pending_action.intent != command.intent
                or pending_action.action != command.action
            ):
                raise ValueError("这个 command_id 的中断检查点属于不同的自主执行请求")
            task_id = resolved_task_id or (
                resume_capsule.active_task_ids[0]
                if resume_capsule.active_task_ids
                else resume_capsule.pending_frontier_task_ids[0]
            )
            task = self.player_store.get_task(command.environment_id, task_id)
            if task is None:
                raise ValueError("中断检查点引用的 canonical task 已经失效")
            selection = TaskSelectionV1(
                task=task,
                reason="从中断检查点恢复原任务，禁止重新选择后盲目重放。",
                decision=TaskBoardDecisionV1(
                    selected_task_id=task.id,
                    selected_expected_status=task.status
                    if task.status in {"queued", "active", "cooldown"}
                    else None,
                    idle_allowed=False,
                    reason="恢复检查点已固定原任务。",
                ),
            )
        else:
            selection = self.select_next_task(
                environment_id=command.environment_id,
                command_id=command.command_id,
            )
            task = selection.task
            if planner_receipt is not None and planner_receipt.task_id != task.id:
                raise RecoveryRequiredError(
                    "planner_measurement_task_mismatch",
                    "planner 测量绑定的任务与即将预留的 canonical 任务不一致。",
                )
            pending_capsule = self._pending_capsule(
                command=command,
                session=session,
                task=task,
            )
            pending_capsule = self._append_capsule_once(pending_capsule)
            session = self._checkpoint_session(
                session,
                pending_capsule,
                command_id=stable_command_id("command.autonomous-pending", command.command_id),
                consume_action=True,
                active_task_ids=[task.id],
                evidence_refs=task.evidence_refs,
            )

        if planner_receipt is not None and planner_receipt.task_id != task.id:
            raise RecoveryRequiredError(
                "planner_measurement_task_mismatch",
                "planner 测量绑定的任务与本次 canonical 任务不一致。",
            )
        if task.status != "active" and not (
            resolved_capsule is not None and task.status in {"completed", "failed"}
        ):
            if selection.decision is None:
                decision = self.task_board.select(
                    self.player_store.list_tasks(command.environment_id)
                )
            else:
                decision = selection.decision
            task = self.task_board.activate_selected(
                self.player_store,
                command.environment_id,
                decision,
            )
            if task is None:
                raise ValueError("TaskBoard 无法激活已选择任务，请重新读取 canonical frontier")
        history_snapshot = (
            self._history_snapshot_from_outcome(
                command=command,
                outcome=resolved_outcome,
                task=task,
            )
            if resolved_outcome is not None
            else self._action_quality_history_snapshot(
                command=command,
                request_sha256=request_sha256,
                task=task,
                known_state_ids_before_command=known_state_ids_before_command,
                known_transition_ids_before_command=known_transition_ids_before_command,
                matched_transition_ids=list(history.matched_transition_ids),
            )
        )
        if resolved_capsule is not None:
            if resolved_outcome is None or resolved_evidence_ref is None:
                raise AssertionError("resolved outcome preflight result is missing")
            if command.decision_telemetry is None:
                raise RecoveryRequiredError(
                    "decision_telemetry_required",
                    "恢复命令缺少原动作决策遥测，无法完成质量归档。",
                )
            outcome = resolved_outcome
            expected_telemetry = self._decision_telemetry_payload(
                resolved_telemetry
            )
            if outcome.evidence_step.metadata.get("action_decision_telemetry") != (
                expected_telemetry
            ):
                raise RecoveryRequiredError(
                    "decision_telemetry_mismatch",
                    "resolved checkpoint 没有绑定原命令的动作决策遥测。",
                )
            expected_receipt = self._planner_receipt_payload(planner_receipt)
            if expected_receipt is not None and (
                outcome.evidence_step.metadata.get("planner_measurement_receipt")
                != expected_receipt
            ):
                raise RecoveryRequiredError(
                    "planner_measurement_receipt_mismatch",
                    "resolved checkpoint 未绑定原命令的 planner 测量凭据。",
                )
            evidence_ref = resolved_evidence_ref
            evidence_already_persisted = True
        elif had_pending_capsule:
            outcome = self.recovery.recover(
                environment_id=command.environment_id,
                session_id=command.session_id,
                command_id=command.command_id,
                expected_identity_hash=command.expected_identity_hash,
                task=task,
                capsule=pending_capsule,
                observer=recovery_observer,
            )
            if command.decision_telemetry is None:
                raise RecoveryRequiredError(
                    "decision_telemetry_required",
                    "恢复命令缺少原动作决策遥测，无法完成质量归档。",
                )
            outcome, evidence_ref = self.consolidator.persist_evidence(
                outcome,
                decision_telemetry=self._decision_telemetry_payload(
                    resolved_telemetry
                ),
                planner_measurement_receipt=self._planner_receipt_payload(
                    planner_receipt
                ),
                action_quality_history=history_snapshot.model_dump(
                    mode="json",
                    by_alias=True,
                ),
                action_quality_command=command.model_dump(
                    mode="json",
                    by_alias=True,
                ),
            )
            evidence_already_persisted = True
        else:
            if executor is None:
                raise RecoveryRequiredError("executor_required", "新命令必须注入外部执行器。")
            operation_memory = OperationMemory(self.player_store)
            operation = operation_memory.ensure_operation(
                environment_id=command.environment_id,
                action=command.action,
                alias_kind="external_trace",
                alias_id=command.command_id,
            )
            visit_plan = operation_memory.plan_visit(command.environment_id, operation.id)
            request = AutonomousExecutorRequestV1(
                command=command,
                task=task,
                pending_capsule_id=pending_capsule.id,
                selection_reason=(
                    f"{selection.reason} canonical_operation={operation.id}; "
                    f"visit_mode={visit_plan.mode}; requires_model={visit_plan.requires_model}"
                ),
                physical_readiness_policy=physical_readiness_policy,
                instruction="只执行命令中给定的一次动作，并返回完整终态证据。",
            )
            try:
                outcome = executor.execute(request)
            except Exception as exc:
                operation_memory.record_outcome(
                    command.environment_id,
                    operation.id,
                    "interrupted",
                )
                raise AutonomousExecutionInterrupted(pending_capsule.id, exc) from exc
            operation_memory.record_outcome(
                command.environment_id,
                operation.id,
                "success" if outcome.status == "succeeded" else "failed",
            )
            if outcome.command_id != command.command_id or outcome.task_id != task.id:
                raise ValueError("外部执行结果没有绑定本次 command 和 canonical task")
            if outcome.environment_id != command.environment_id:
                raise ValueError("外部执行结果属于其他环境")
            if outcome.evidence_step.action != command.action:
                raise ValueError("外部执行结果没有记录实际下发的命令动作")
            if command.decision_telemetry is None:
                raise RecoveryRequiredError(
                    "decision_telemetry_required",
                    "命令缺少动作决策遥测，无法完成质量归档。",
                )
            outcome, evidence_ref = self.consolidator.persist_evidence(
                outcome,
                decision_telemetry=self._decision_telemetry_payload(
                    resolved_telemetry
                ),
                planner_measurement_receipt=self._planner_receipt_payload(
                    planner_receipt
                ),
                action_quality_history=history_snapshot.model_dump(
                    mode="json",
                    by_alias=True,
                ),
                action_quality_command=command.model_dump(
                    mode="json",
                    by_alias=True,
                ),
            )
            evidence_already_persisted = True

        session = self.sessions.get_session(command.environment_id, command.session_id) or session
        resolved_capsule = self._resolved_capsule(
            command=command,
            session=session,
            task=task,
            outcome=outcome,
            evidence_ref=evidence_ref,
        )
        resolved_capsule = self._append_capsule_once(resolved_capsule)
        session = self._checkpoint_session(
            session,
            resolved_capsule,
            command_id=stable_command_id("command.autonomous-resolved", command.command_id),
            consume_action=False,
            active_task_ids=[task.id],
            evidence_refs=[evidence_ref],
        )
        next_task = self._next_task(
            environment_id=command.environment_id,
            current_task_id=task.id,
            evidence_ref=evidence_ref,
            after_state=outcome.after_state,
        )
        consolidation = self.consolidator.consolidate(
            outcome=outcome,
            request_sha256=request_sha256,
            selected_task=task,
            expected_action=command.action,
            next_task=next_task,
            selection_reason=selection.reason,
            account_action_decision=account_action_decision,
            evidence_already_persisted=evidence_already_persisted,
        )
        session = self.sessions.get_session(command.environment_id, command.session_id) or session
        final_capsule = self._final_capsule(
            command=command,
            session=session,
            consolidation=consolidation,
        )
        final_capsule = self._append_capsule_once(final_capsule)
        final_session = self._checkpoint_session(
            session,
            final_capsule,
            command_id=stable_command_id("command.autonomous-final", command.command_id),
            consume_action=False,
            active_task_ids=[],
            evidence_refs=[consolidation.evidence_ref],
        )
        self._record_action_quality(
            command=command,
            outcome=outcome,
            consolidation=consolidation,
            session=final_session,
            task=task,
            history_snapshot=self._history_snapshot_from_outcome(
                command=command,
                outcome=outcome,
                task=task,
            ),
        )
        return self._cycle_result(
            command=command,
            consolidation=consolidation,
            final_capsule=final_capsule,
            selection_reason=selection.reason,
            recovered=outcome.recovered_from_interruption,
            physical_readiness_policy=physical_readiness_policy,
        )


__all__ = [
    "AccountMetricDerivationProvider",
    "AccountMetricDerivationRequest",
    "AutonomousCommandExecutor",
    "AutonomousCycleResultV1",
    "AutonomousExecutionCommandV1",
    "AutonomousExecutionInterrupted",
    "AutonomousExecutorRequestV1",
    "AutonomousOrchestrator",
    "PhysicalReadinessExecutionPolicyV1",
    "TaskSelectionV1",
]
