"""Interruption recovery that reobserves pending effects and never replays actions."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .consolidation import CanonicalExecutionOutcomeV1
from .contracts import FrontierTaskV1, PendingActionV1, SessionCapsuleV1
from .store import AIPlayerStore


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class RecoveryObservationRequestV1(_StrictModel):
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    task: FrontierTaskV1
    capsule: SessionCapsuleV1
    pending_action: PendingActionV1
    instruction: str = Field(min_length=1)


class PendingActionObserver(Protocol):
    """Injected read-only observer; implementations must not replay the pending action."""

    def reobserve(self, request: RecoveryObservationRequestV1) -> CanonicalExecutionOutcomeV1:
        ...


class RecoveryRequiredError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AutonomousRecovery:
    """Validate a persisted pending action and classify it through fresh evidence."""

    def __init__(self, player_store: AIPlayerStore) -> None:
        self.player_store = player_store

    def recover(
        self,
        *,
        environment_id: str,
        session_id: str,
        command_id: str,
        expected_identity_hash: str,
        task: FrontierTaskV1,
        capsule: SessionCapsuleV1,
        observer: PendingActionObserver | None,
    ) -> CanonicalExecutionOutcomeV1:
        environment = self.player_store.get_environment(environment_id)
        if environment is None:
            raise RecoveryRequiredError("unknown_environment", "恢复环境不存在，已停止执行。")
        if environment.identity_hash != expected_identity_hash:
            raise RecoveryRequiredError(
                "identity_mismatch",
                "当前游戏、账号或设备环境与中断前不一致，已拒绝恢复。",
            )
        if capsule.environment_id != environment_id or capsule.session_id != session_id:
            raise RecoveryRequiredError(
                "capsule_scope_mismatch",
                "检查点不属于当前环境和会话，已拒绝跨环境恢复。",
            )
        pending = capsule.pending_action
        if pending is None:
            raise RecoveryRequiredError("pending_action_missing", "检查点没有待判定动作。")
        if pending.environment_id != environment_id or pending.id != command_id:
            raise RecoveryRequiredError(
                "pending_action_mismatch",
                "待判定动作与当前 command_id 不一致，已停止执行。",
            )
        if pending.request_sha256 is None:
            raise RecoveryRequiredError(
                "pending_request_hash_missing",
                "待判定动作缺少原始请求哈希，不能证明恢复命令未被替换。",
            )
        if pending.blind_replay_allowed or pending.resume_rule != "reobserve_before_any_action":
            raise RecoveryRequiredError(
                "unsafe_recovery_contract",
                "待判定动作没有声明先重观察，已拒绝恢复。",
            )
        if pending.effect_status != "unknown":
            raise RecoveryRequiredError(
                "pending_action_already_resolved",
                "待处理动作已有结论，应直接继续归并，不能再次观察。",
            )
        if task.id not in {*capsule.active_task_ids, *capsule.pending_frontier_task_ids}:
            raise RecoveryRequiredError(
                "task_not_in_capsule",
                "检查点没有保留当前任务，无法证明恢复上下文。",
            )
        if observer is None:
            raise RecoveryRequiredError(
                "fresh_observation_required",
                "中断动作的效果未知；必须先注入只读观察器取得新证据，不能重放动作。",
            )
        request = RecoveryObservationRequestV1(
            environment_id=environment_id,
            session_id=session_id,
            command_id=command_id,
            task=task,
            capsule=capsule,
            pending_action=pending,
            instruction="只重新观察当前画面并判定上次动作是否生效，禁止再次下发原动作。",
        )
        outcome = observer.reobserve(request)
        if outcome.environment_id != environment_id:
            raise RecoveryRequiredError(
                "observation_environment_mismatch",
                "恢复观察返回了其他环境的证据。",
            )
        if outcome.command_id != command_id or outcome.task_id != task.id:
            raise RecoveryRequiredError(
                "observation_command_mismatch",
                "恢复观察没有绑定原 command 和 task。",
            )
        if outcome.evidence_step.action.type != "wait":
            raise RecoveryRequiredError(
                "recovery_action_forbidden",
                "恢复观察只能记录 wait 观察步骤，不能执行新的游戏操作。",
            )
        return outcome.model_copy(update={"recovered_from_interruption": True})


__all__ = [
    "AutonomousRecovery",
    "PendingActionObserver",
    "RecoveryObservationRequestV1",
    "RecoveryRequiredError",
]
