from __future__ import annotations

import hashlib
import json
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .contracts import EvidenceReferenceV1, FrontierTaskV1
from .store import AIPlayerStore


class FrontierSignalV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal[
        "user_goal",
        "unknown_interaction",
        "missing_transition",
        "stale_memory",
        "interface_family_gap",
        "new_unlock",
        "guide_update",
        "failed_skill",
        "gameplay_candidate",
        "coverage_gap",
    ]
    subject_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    dependency_task_ids: list[str] = Field(default_factory=list)
    value_score: float = 0.0
    novelty_score: float = 0.0
    expected_coverage_gain: float = 0.0
    risk_score: float = Field(default=0.0, ge=0.0)
    action_budget: int = Field(default=12, ge=0)
    token_budget: int | None = Field(default=None, ge=0)
    time_budget_seconds: float = Field(default=900, gt=0)
    max_attempts: int = Field(default=2, ge=1)

    @field_validator("dependency_task_ids")
    @classmethod
    def unique_dependencies(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("dependency_task_ids must be unique")
        return value


class FrontierGenerationResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_task_ids: list[str] = Field(default_factory=list)
    existing_task_ids: list[str] = Field(default_factory=list)
    signal_count: int = 0
    audit_summary: str


class FrontierGenerator:
    """Turns evidence-bound coverage signals into idempotent canonical tasks."""

    def __init__(self, store: AIPlayerStore) -> None:
        self.store = store

    @staticmethod
    def _task_id(environment_id: str, signal: FrontierSignalV1) -> str:
        key = json.dumps(
            {
                "environment_id": environment_id,
                "source": signal.source,
                "subject_id": signal.subject_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"task.frontier.{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}"

    def audit_signals(self, environment_id: str) -> list[FrontierSignalV1]:
        signals: list[FrontierSignalV1] = []
        for state in self.store.list_semantic_states(environment_id):
            if state.status != "candidate":
                continue
            signals.append(
                FrontierSignalV1(
                    source="coverage_gap",
                    subject_id=f"semantic-state:{state.id}@{state.version}",
                    title=f"补证并复核界面状态：{state.title}",
                    reason=(
                        f"语义状态 {state.id}@{state.version} 仍是候选；需要补齐界面身份、"
                        "关键可交互项、入口、出口或与相邻状态的区别。"
                    ),
                    evidence_refs=state.evidence_refs,
                    value_score=4,
                    novelty_score=3,
                    expected_coverage_gain=3,
                    risk_score=1,
                )
            )
        for edge in self.store.list_transition_edges(environment_id):
            if edge.outcome not in {"deferred", "failed"}:
                continue
            signals.append(
                FrontierSignalV1(
                    source="missing_transition",
                    subject_id=f"transition:{edge.id}@{edge.version}",
                    title=f"闭合状态转移：{edge.from_state_id}",
                    reason=(
                        f"转移 {edge.id}@{edge.version} 当前结果为 {edge.outcome}；"
                        "需要从声明起点重新观察，执行一次有终态判定的动作并验证恢复路径。"
                    ),
                    evidence_refs=edge.evidence_refs,
                    value_score=5,
                    novelty_score=2,
                    expected_coverage_gain=4,
                    risk_score=2 if edge.outcome == "failed" else 1,
                )
            )
        return signals

    def generate(
        self,
        environment_id: str,
        *,
        signals: Sequence[FrontierSignalV1] | None = None,
    ) -> FrontierGenerationResultV1:
        if self.store.get_environment(environment_id) is None:
            raise ValueError(f"unknown AI-player environment: {environment_id}")
        candidates = list(signals) if signals is not None else self.audit_signals(environment_id)
        generated: list[str] = []
        existing: list[str] = []
        for signal in candidates:
            if any(ref.environment_id != environment_id for ref in signal.evidence_refs):
                raise ValueError("frontier signal evidence belongs to another environment")
            task_id = self._task_id(environment_id, signal)
            task = FrontierTaskV1(
                id=task_id,
                environment_id=environment_id,
                title=signal.title,
                source=signal.source,
                reason=signal.reason,
                dependency_task_ids=signal.dependency_task_ids,
                value_score=signal.value_score,
                novelty_score=signal.novelty_score,
                expected_coverage_gain=signal.expected_coverage_gain,
                risk_score=signal.risk_score,
                action_budget=signal.action_budget,
                token_budget=signal.token_budget,
                time_budget_seconds=signal.time_budget_seconds,
                max_attempts=signal.max_attempts,
                evidence_refs=signal.evidence_refs,
            )
            current = self.store.get_task(environment_id, task_id)
            if current is not None:
                existing.append(task_id)
                continue
            self.store.enqueue_task(task)
            generated.append(task_id)
        return FrontierGenerationResultV1(
            generated_task_ids=generated,
            existing_task_ids=existing,
            signal_count=len(candidates),
            audit_summary=(
                f"覆盖审计得到 {len(candidates)} 个证据绑定前沿；"
                f"新建 {len(generated)} 个，已有 {len(existing)} 个。"
                if candidates
                else "覆盖审计没有找到可由现有证据生成的前沿；不得凭空编造任务，"
                "应列出阻断或等待新的实机观察。"
            ),
        )