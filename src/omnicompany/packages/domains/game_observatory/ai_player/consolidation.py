"""Evidence-first canonical write-back for one autonomous AI-player command.

This module deliberately knows nothing about devices, games, guide research, or skill
crystallisation.  An injected executor supplies observations and terminal evidence; the
consolidator validates and persists those facts before it changes canonical state.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import ArtifactRef, EvidenceRun, EvidenceStep, NormalizedAction, RunResult
from .account_policy import AccountActionDecisionV1
from .contracts import (
    EvidenceReferenceV1,
    FrontierTaskV1,
    MemoryRecordV1,
    SemanticStateV1,
    TransitionEdgeV1,
)
from .store import AIPlayerStore


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


def stable_command_id(prefix: str, command_id: str) -> str:
    """Return a storage-safe deterministic id without exposing a long command id."""

    digest = hashlib.sha256(command_id.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}.{digest}"


class CanonicalExecutionOutcomeV1(_StrictModel):
    """Terminal facts returned by an injected command executor or recovery observer."""

    environment_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    status: Literal["succeeded", "failed"]
    evidence_run: EvidenceRun
    evidence_step: EvidenceStep
    artifacts: list[ArtifactRef] = Field(min_length=1)
    action_run: RunResult
    before_state: SemanticStateV1
    after_state: SemanticStateV1
    observed_change: str = Field(min_length=1)
    failure_reason: str | None = Field(default=None, min_length=1)
    recovered_from_interruption: bool = False

    @model_validator(mode="after")
    def validate_terminal_bundle(self) -> "CanonicalExecutionOutcomeV1":
        run = self.evidence_run
        step = self.evidence_step
        if self.status == "succeeded" and (run.status != "passed" or step.status != "passed"):
            raise ValueError("成功结果必须绑定终态通过的 EvidenceRun 和 EvidenceStep")
        if self.status == "failed" and (run.status == "running" or step.status == "running"):
            raise ValueError("失败结果也必须先结束 EvidenceRun 和 EvidenceStep")
        if self.status == "failed" and not self.failure_reason:
            raise ValueError("失败结果必须说明失败原因")
        if step.evidence_run_id != run.id or step.id not in run.step_ids:
            raise ValueError("EvidenceRun 必须完整保留本次 EvidenceStep")
        if step.action_run_id != self.action_run.id or self.action_run.id not in run.action_run_ids:
            raise ValueError("EvidenceStep 与 EvidenceRun 必须共同绑定本次 action run")
        if run.environment.get("environment_id") != self.environment_id:
            raise ValueError("EvidenceRun 没有精确绑定当前环境")
        if run.scope_id not in {None, self.environment_id}:
            raise ValueError("EvidenceRun scope 与当前环境不一致")
        if any(
            state.environment_id != self.environment_id
            for state in (self.before_state, self.after_state)
        ):
            raise ValueError("前后语义状态必须属于当前环境")

        artifacts = {item.id: item for item in self.artifacts}
        if len(artifacts) != len(self.artifacts):
            raise ValueError("证据包不能包含重复 artifact id")
        required_artifact_ids = {
            item
            for item in (
                step.before_frame_id,
                step.after_frame_id,
                step.video_artifact_id,
                step.before_ui_tree_id,
                step.after_ui_tree_id,
                *step.intermediate_frame_ids,
            )
            if item is not None
        }
        missing = sorted(required_artifact_ids.difference(artifacts))
        if missing:
            raise ValueError("执行结果缺少 EvidenceStep 引用的 artifact：" + ", ".join(missing))
        if not set(step.artifact_ids).issubset(artifacts):
            raise ValueError("EvidenceStep artifact_ids 含有未随结果提交的 artifact")
        if not set(run.artifact_ids).issubset(artifacts):
            raise ValueError("EvidenceRun artifact_ids 含有未随结果提交的 artifact")
        for artifact in self.artifacts:
            if artifact.metadata.get("environment_id") != self.environment_id:
                raise ValueError(f"artifact 未精确绑定当前环境：{artifact.id}")
        if self.status == "succeeded":
            issues = step.publication_issues()
            if issues:
                raise ValueError("成功动作的证据不完整：" + "；".join(issues))
        return self


class ConsolidationResultV1(_StrictModel):
    environment_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    request_sha256: str = Field(min_length=64, max_length=64)
    task_id: str = Field(min_length=1)
    task_status: Literal["completed", "failed"]
    before_state_id: str = Field(min_length=1)
    after_state_id: str = Field(min_length=1)
    transition_edge_id: str = Field(min_length=1)
    memory_id: str = Field(min_length=1)
    next_task_id: str | None = Field(default=None, min_length=1)
    evidence_ref: EvidenceReferenceV1
    reason: str = Field(min_length=1)
    idempotent_replay: bool = False


class ExecutionConsolidator:
    """Persist terminal evidence and derive canonical facts exactly once."""

    MEMORY_PREFIX = "memory.autonomous-command"
    EDGE_PREFIX = "edge.autonomous-command"

    def __init__(self, player_store: AIPlayerStore) -> None:
        self.player_store = player_store
        self.observatory_store = player_store.observatory_store

    @staticmethod
    def _canonical_json(value: BaseModel | dict[str, Any]) -> str:
        payload = (
            value.model_dump(mode="json", by_alias=True)
            if isinstance(value, BaseModel)
            else value
        )
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def command_memory_id(cls, command_id: str) -> str:
        return stable_command_id(cls.MEMORY_PREFIX, command_id)

    def completed_result(
        self,
        *,
        environment_id: str,
        command_id: str,
        request_sha256: str,
    ) -> ConsolidationResultV1 | None:
        memory = self.player_store.get_memory(
            environment_id,
            self.command_memory_id(command_id),
        )
        if memory is None:
            return None
        if memory.payload.get("request_sha256") != request_sha256:
            raise ValueError("这个 command_id 已用于不同的自主执行请求")
        result = ConsolidationResultV1.model_validate(memory.payload["result"])
        return result.model_copy(update={"idempotent_replay": True})

    def _persist_model_once(
        self,
        *,
        label: str,
        existing: BaseModel | None,
        incoming: BaseModel,
        writer: Any,
    ) -> None:
        if existing is not None:
            if self._canonical_json(existing) != self._canonical_json(incoming):
                raise ValueError(f"{label} id 已存在且内容不同")
            return
        writer(incoming)

    def persist_evidence(
        self,
        outcome: CanonicalExecutionOutcomeV1,
        *,
        decision_telemetry: dict[str, Any] | None = None,
        planner_measurement_receipt: dict[str, Any] | None = None,
        action_quality_history: dict[str, Any] | None = None,
        action_quality_command: dict[str, Any] | None = None,
    ) -> tuple[CanonicalExecutionOutcomeV1, EvidenceReferenceV1]:
        """Validate then durably retain a complete terminal bundle, idempotently."""

        original_step = outcome.evidence_step
        reference = self._evidence_reference(outcome)
        prepared_before, prepared_after = self._prepare_states(
            outcome.before_state,
            outcome.after_state,
            reference,
            persist=False,
        )
        autonomous_metadata = {
            "schema": "game-observatory.ai-player.autonomous-evidence.v1",
            "environment_id": outcome.environment_id,
            "command_id": outcome.command_id,
            "task_id": outcome.task_id,
            "status": outcome.status,
            "before_state": prepared_before.model_dump(mode="json", by_alias=True),
            "after_state": prepared_after.model_dump(mode="json", by_alias=True),
            "observed_change": outcome.observed_change,
            "failure_reason": outcome.failure_reason,
            "recovered_from_interruption": outcome.recovered_from_interruption,
        }
        metadata = dict(outcome.evidence_step.metadata)
        if decision_telemetry is not None:
            prior_telemetry = metadata.get("action_decision_telemetry")
            if prior_telemetry is not None and prior_telemetry != decision_telemetry:
                raise ValueError("EvidenceStep 已绑定不同的动作决策遥测")
            metadata["action_decision_telemetry"] = decision_telemetry
        if planner_measurement_receipt is not None:
            prior_receipt = metadata.get("planner_measurement_receipt")
            if prior_receipt is not None and prior_receipt != planner_measurement_receipt:
                raise ValueError("EvidenceStep already binds another planner receipt")
            metadata["planner_measurement_receipt"] = planner_measurement_receipt
        if action_quality_history is not None:
            prior_history = metadata.get("action_quality_history")
            if prior_history is not None and prior_history != action_quality_history:
                raise ValueError("EvidenceStep 已绑定不同的动作前质量历史快照")
            metadata["action_quality_history"] = action_quality_history
        if action_quality_command is not None:
            prior_command = metadata.get("action_quality_command")
            if prior_command is not None and prior_command != action_quality_command:
                raise ValueError(
                    "EvidenceStep 已绑定不同的动作质量 canonical command"
                )
            metadata["action_quality_command"] = action_quality_command
        prior = metadata.get("autonomous_execution")
        if prior is not None and prior != autonomous_metadata:
            raise ValueError("EvidenceStep 已绑定不同的自主执行结果")
        metadata["autonomous_execution"] = autonomous_metadata
        step = outcome.evidence_step.model_copy(update={"metadata": metadata})
        outcome = outcome.model_copy(update={"evidence_step": step})
        # Re-run all model-level checks after metadata normalisation.
        outcome = CanonicalExecutionOutcomeV1.model_validate(outcome.model_dump())

        artifact_root = self.observatory_store.artifact_root.resolve()
        preflight: list[tuple[str, BaseModel | None, BaseModel]] = []
        for artifact in outcome.artifacts:
            artifact_path = Path(artifact.path).resolve()
            if artifact_path != artifact_root and artifact_root not in artifact_path.parents:
                raise ValueError(f"artifact 路径逃逸 canonical 根目录：{artifact.id}")
            if not artifact_path.is_file():
                raise ValueError(f"artifact 文件不存在：{artifact.id}")
            if hashlib.sha256(artifact_path.read_bytes()).hexdigest() != artifact.sha256:
                raise ValueError(f"artifact 文件哈希不匹配：{artifact.id}")
            preflight.append(
                (
                    f"artifact {artifact.id}",
                    self.observatory_store.get_artifact(artifact.id),
                    artifact,
                )
            )
        existing_step = self.observatory_store.get_evidence_step(step.id)
        if existing_step is not None and all(
            self._canonical_json(existing_step) != self._canonical_json(candidate)
            for candidate in (original_step, step)
        ):
            raise ValueError(f"EvidenceStep {step.id} id 已存在且内容不同")
        preflight.extend(
            [
                (
                    f"action run {outcome.action_run.id}",
                    self.observatory_store.get_run(outcome.action_run.id),
                    outcome.action_run,
                ),
                (
                    f"EvidenceRun {outcome.evidence_run.id}",
                    self.observatory_store.get_evidence_run(outcome.evidence_run.id),
                    outcome.evidence_run,
                ),
                (
                    f"EvidenceStep {step.id}",
                    self.observatory_store.get_evidence_step(step.id),
                    step,
                ),
            ]
        )
        for label, existing, incoming in preflight:
            if existing is not None and self._canonical_json(existing) != self._canonical_json(
                incoming
            ):
                raise ValueError(f"{label} id 已存在且内容不同")

        for artifact in outcome.artifacts:
            self._persist_model_once(
                label=f"artifact {artifact.id}",
                existing=self.observatory_store.get_artifact(artifact.id),
                incoming=artifact,
                writer=self.observatory_store.save_artifact,
            )
        self._persist_model_once(
            label=f"action run {outcome.action_run.id}",
            existing=self.observatory_store.get_run(outcome.action_run.id),
            incoming=outcome.action_run,
            writer=self.observatory_store.save_run,
        )
        self._persist_model_once(
            label=f"EvidenceRun {outcome.evidence_run.id}",
            existing=self.observatory_store.get_evidence_run(outcome.evidence_run.id),
            incoming=outcome.evidence_run,
            writer=self.observatory_store.save_evidence_run,
        )
        if existing_step is None or self._canonical_json(existing_step) == self._canonical_json(
            original_step
        ):
            self.observatory_store.save_evidence_step(step)
        self.player_store.resolve_evidence_references([reference])
        return outcome, reference

    @staticmethod
    def _evidence_reference(outcome: CanonicalExecutionOutcomeV1) -> EvidenceReferenceV1:
        return EvidenceReferenceV1(
            environment_id=outcome.environment_id,
            artifact_ids=[item.id for item in outcome.artifacts],
            evidence_run_ids=[outcome.evidence_run.id],
            evidence_step_ids=[outcome.evidence_step.id],
            trace_run_ids=[outcome.action_run.id],
            note="本次自主执行的完整终态证据。",
        )

    def _put_state_once(self, state: SemanticStateV1) -> None:
        existing = self.player_store.get_semantic_state(
            state.environment_id,
            state.id,
            version=state.version,
        )
        self._persist_model_once(
            label=f"semantic state {state.id}@{state.version}",
            existing=existing,
            incoming=state,
            writer=self.player_store.put_semantic_state,
        )

    def _merge_evidence_refs(
        self,
        existing: list[EvidenceReferenceV1],
        incoming: EvidenceReferenceV1,
    ) -> list[EvidenceReferenceV1]:
        merged: list[EvidenceReferenceV1] = []
        seen: set[str] = set()
        for reference in [*existing, incoming]:
            canonical = self._canonical_json(reference)
            if canonical in seen:
                continue
            seen.add(canonical)
            merged.append(reference)
        return merged

    def _prepare_states(
        self,
        before_state: SemanticStateV1,
        after_state: SemanticStateV1,
        evidence_ref: EvidenceReferenceV1,
        *,
        persist: bool = True,
    ) -> tuple[SemanticStateV1, SemanticStateV1]:
        """Preflight both states before persisting any new state rows."""

        incoming_by_key: dict[tuple[str, str, int], SemanticStateV1] = {}
        prepared_by_key: dict[tuple[str, str, int], SemanticStateV1] = {}
        existing_by_key: dict[tuple[str, str, int], SemanticStateV1 | None] = {}
        ordered_keys: list[tuple[str, str, int]] = []
        for incoming in (before_state, after_state):
            key = (incoming.environment_id, incoming.id, incoming.version)
            prior = incoming_by_key.get(key)
            if prior is not None:
                if self._canonical_json(prior) != self._canonical_json(incoming):
                    raise ValueError(
                        "before/after semantic states use the same key with different content"
                    )
                continue
            incoming_by_key[key] = incoming
            ordered_keys.append(key)
            existing = self.player_store.get_semantic_state(
                incoming.environment_id,
                incoming.id,
                version=incoming.version,
            )
            existing_by_key[key] = existing
            if existing is not None:
                if self._canonical_json(existing) != self._canonical_json(incoming):
                    raise ValueError(
                        f"semantic state {incoming.id}@{incoming.version} "
                        "already exists with different canonical content"
                    )
                prepared_by_key[key] = existing
                continue
            prepared_by_key[key] = incoming.model_copy(
                update={
                    "evidence_refs": self._merge_evidence_refs(
                        incoming.evidence_refs,
                        evidence_ref,
                    )
                }
            )

        # No state write occurs until every before/after key has passed the checks above.
        if persist:
            for key in ordered_keys:
                if existing_by_key[key] is None:
                    self._put_state_once(prepared_by_key[key])
        before_key = (before_state.environment_id, before_state.id, before_state.version)
        after_key = (after_state.environment_id, after_state.id, after_state.version)
        return prepared_by_key[before_key], prepared_by_key[after_key]

    def _put_edge_once(self, edge: TransitionEdgeV1) -> None:
        existing = self.player_store.get_transition_edge(
            edge.environment_id,
            edge.id,
            version=edge.version,
        )
        self._persist_model_once(
            label=f"transition edge {edge.id}@{edge.version}",
            existing=existing,
            incoming=edge,
            writer=self.player_store.put_transition_edge,
        )

    def _finish_task(
        self,
        task: FrontierTaskV1,
        *,
        status: Literal["completed", "failed"],
        evidence_ref: EvidenceReferenceV1,
    ) -> FrontierTaskV1:
        current = self.player_store.get_task(task.environment_id, task.id)
        if current is None:
            raise ValueError(f"canonical task 不存在：{task.id}")
        if current.status == status:
            if evidence_ref not in current.evidence_refs:
                raise ValueError("任务已结束，但没有绑定本次 command 的证据")
            return current
        if current.status != "active":
            raise ValueError(f"任务 {task.id} 当前为 {current.status}，不能结束")
        references = list(current.evidence_refs)
        if evidence_ref not in references:
            references.append(evidence_ref)
        updated = self.player_store.compare_and_swap_task_status(
            current.environment_id,
            current.id,
            "active",
            status,
            expected_version=current.version,
            updates={"evidence_refs": references},
        )
        if updated is None:
            raise ValueError("任务刚刚被其他执行者更新，请重新读取 canonical task")
        return updated

    def _preflight_next_task(
        self,
        environment_id: str,
        task: FrontierTaskV1 | None,
    ) -> FrontierTaskV1 | None:
        """Validate and freeze the exact next-task snapshot without writing."""

        if task is None:
            return None
        if task.environment_id != environment_id or task.status not in {
            "queued",
            "active",
            "cooldown",
        }:
            raise ValueError(
                "next task must be a selectable canonical task in the current environment"
            )
        existing = self.player_store.get_task(task.environment_id, task.id)
        if existing is not None:
            if self._canonical_json(existing) != self._canonical_json(task):
                raise ValueError("next task does not exactly match its canonical task")
            return existing
        if task.status != "queued":
            raise ValueError("a missing next task must be a new queued canonical task")
        return task

    def _ensure_next_task(self, task: FrontierTaskV1 | None) -> FrontierTaskV1 | None:
        if task is None:
            return None
        existing = self.player_store.get_task(task.environment_id, task.id)
        if existing is not None:
            if self._canonical_json(existing) != self._canonical_json(task):
                raise ValueError("下一任务 id 已存在且内容不同")
            return existing
        return self.player_store.enqueue_task(task)

    def consolidate(
        self,
        *,
        outcome: CanonicalExecutionOutcomeV1,
        request_sha256: str,
        selected_task: FrontierTaskV1,
        expected_action: NormalizedAction,
        next_task: FrontierTaskV1 | None,
        selection_reason: str,
        account_action_decision: AccountActionDecisionV1 | None = None,
        evidence_already_persisted: bool = False,
    ) -> ConsolidationResultV1:
        replay = self.completed_result(
            environment_id=outcome.environment_id,
            command_id=outcome.command_id,
            request_sha256=request_sha256,
        )
        if replay is not None:
            return replay
        if outcome.task_id != selected_task.id:
            raise ValueError("执行结果绑定了不同的 canonical task")
        if outcome.evidence_step.action != expected_action and not outcome.recovered_from_interruption:
            raise ValueError("EvidenceStep 记录的动作与自主执行命令不一致")
        next_task = self._preflight_next_task(outcome.environment_id, next_task)

        if evidence_already_persisted:
            step = self.observatory_store.get_evidence_step(outcome.evidence_step.id)
            run = self.observatory_store.get_evidence_run(outcome.evidence_run.id)
            if step != outcome.evidence_step or run != outcome.evidence_run:
                raise ValueError("已落库证据与待归并结果不一致")
            evidence_ref = EvidenceReferenceV1(
                environment_id=outcome.environment_id,
                artifact_ids=[item.id for item in outcome.artifacts],
                evidence_run_ids=[outcome.evidence_run.id],
                evidence_step_ids=[outcome.evidence_step.id],
                trace_run_ids=[outcome.action_run.id],
                note="本次自主执行的完整终态证据。",
            )
            self.player_store.resolve_evidence_references([evidence_ref])
        else:
            outcome, evidence_ref = self.persist_evidence(outcome)

        before_state, after_state = self._prepare_states(
            outcome.before_state,
            outcome.after_state,
            evidence_ref,
        )

        changed = before_state.id != after_state.id
        edge_outcome: Literal[
            "verified_transition",
            "verified_no_change",
            "failed",
            "deferred",
        ]
        if outcome.status == "failed":
            edge_outcome = "failed"
        elif before_state.status != "accepted" or after_state.status != "accepted":
            edge_outcome = "deferred"
        elif changed:
            edge_outcome = "verified_transition"
        else:
            edge_outcome = "verified_no_change"
        edge = TransitionEdgeV1(
            id=stable_command_id(self.EDGE_PREFIX, outcome.command_id),
            environment_id=outcome.environment_id,
            evidence_refs=[evidence_ref],
            from_state_id=before_state.id,
            to_state_id=after_state.id,
            action=expected_action,
            target_bounds=outcome.evidence_step.target_bounds,
            expected_change=selected_task.reason,
            observed_change=outcome.observed_change,
            outcome=edge_outcome,
            created_at=outcome.evidence_step.ended_at or outcome.evidence_step.started_at,
        )
        self._put_edge_once(edge)
        final_status: Literal["completed", "failed"] = (
            "completed" if outcome.status == "succeeded" else "failed"
        )
        finished_task = self._finish_task(
            selected_task,
            status=final_status,
            evidence_ref=evidence_ref,
        )
        next_task = self._ensure_next_task(next_task)
        if final_status == "completed" and next_task is not None:
            reason = (
                f"任务“{finished_task.title}”已完成，证据、状态与转移边均已写回；"
                f"下一任务为“{next_task.title}”。"
            )
        elif final_status == "completed":
            reason = (
                f"任务“{finished_task.title}”已完成，证据、状态与转移边均已写回；"
                "当前没有由 canonical 候选或缺失边支持的下一任务。"
            )
        elif next_task is not None:
            reason = f"任务“{finished_task.title}”已失败并留证；下一任务为“{next_task.title}”。"
        else:
            reason = (
                f"任务“{finished_task.title}”已失败并留证；"
                "当前没有由 canonical 候选或缺失边支持的下一任务。"
            )
        result = ConsolidationResultV1(
            environment_id=outcome.environment_id,
            command_id=outcome.command_id,
            request_sha256=request_sha256,
            task_id=finished_task.id,
            task_status=final_status,
            before_state_id=before_state.id,
            after_state_id=after_state.id,
            transition_edge_id=edge.id,
            memory_id=self.command_memory_id(outcome.command_id),
            next_task_id=next_task.id if next_task is not None else None,
            evidence_ref=evidence_ref,
            reason=reason,
        )
        memory = MemoryRecordV1(
            id=result.memory_id,
            environment_id=outcome.environment_id,
            evidence_refs=[evidence_ref],
            kind="episodic",
            subject_id=finished_task.id,
            payload={
                "command_id": outcome.command_id,
                "request_sha256": request_sha256,
                "selection_reason": selection_reason,
                "account_action_decision": (
                    account_action_decision.model_dump(mode="json")
                    if account_action_decision is not None
                    else None
                ),
                "executor_status": outcome.status,
                "observed_change": outcome.observed_change,
                "failure_reason": outcome.failure_reason,
                "recovered_from_interruption": outcome.recovered_from_interruption,
                "result": result.model_dump(mode="json", by_alias=True),
            },
            created_at=outcome.evidence_step.ended_at or outcome.evidence_step.started_at,
        )
        existing_memory = self.player_store.get_memory(outcome.environment_id, memory.id)
        self._persist_model_once(
            label=f"memory {memory.id}",
            existing=existing_memory,
            incoming=memory,
            writer=self.player_store.append_memory,
        )
        return result


__all__ = [
    "CanonicalExecutionOutcomeV1",
    "ConsolidationResultV1",
    "ExecutionConsolidator",
    "stable_command_id",
]
