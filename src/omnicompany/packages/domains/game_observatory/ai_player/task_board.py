from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .contracts import FrontierTaskV1
from .store import AIPlayerStore


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("task timestamps must include a timezone")
    return parsed


class TaskScoreV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    total: float
    value: float
    novelty: float
    coverage_gain: float
    active_task_bonus: float
    attempt_penalty: float
    risk_penalty: float
    explanation: str


class TaskDispositionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    disposition: Literal[
        "eligible",
        "duplicate",
        "dependency_unmet",
        "cooldown_active",
        "blocked",
        "attempts_exhausted",
        "risk_exceeds_limit",
        "terminal",
    ]
    reason: str
    duplicate_of: str | None = None
    reactivate_from_cooldown: bool = False
    score: TaskScoreV1 | None = None


class TaskBoardDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_task_id: str | None = None
    selected_expected_status: Literal["queued", "active", "cooldown"] | None = None
    idle_allowed: bool
    reason: str
    dispositions: list[TaskDispositionV1] = Field(default_factory=list)

    def disposition(self, task_id: str) -> TaskDispositionV1:
        return next(item for item in self.dispositions if item.task_id == task_id)


@dataclass(frozen=True)
class TaskBoardPolicy:
    max_risk_score: float = 100.0
    active_task_bonus: float = 2.0
    attempt_penalty: float = 0.5
    risk_weight: float = 1.0


class TaskBoard:
    """Deterministic, explainable selection over canonical frontier tasks."""

    def __init__(self, policy: TaskBoardPolicy | None = None) -> None:
        self.policy = policy or TaskBoardPolicy()

    @staticmethod
    def _dedup_key(task: FrontierTaskV1) -> tuple[object, ...]:
        return (
            task.source,
            task.title.strip().casefold(),
            task.reason.strip().casefold(),
            tuple(sorted(task.dependency_task_ids)),
        )

    def _score(self, task: FrontierTaskV1) -> TaskScoreV1:
        active_bonus = self.policy.active_task_bonus if task.status == "active" else 0.0
        attempt_penalty = task.attempt_count * self.policy.attempt_penalty
        risk_penalty = task.risk_score * self.policy.risk_weight
        total = (
            task.value_score
            + task.novelty_score
            + task.expected_coverage_gain
            + active_bonus
            - attempt_penalty
            - risk_penalty
        )
        explanation = (
            f"价值 {task.value_score:.2f} + 新颖度 {task.novelty_score:.2f} + "
            f"预计覆盖增量 {task.expected_coverage_gain:.2f} + 活跃任务续跑 {active_bonus:.2f} - "
            f"尝试惩罚 {attempt_penalty:.2f} - 风险 {risk_penalty:.2f} = {total:.2f}"
        )
        return TaskScoreV1(
            task_id=task.id,
            total=total,
            value=task.value_score,
            novelty=task.novelty_score,
            coverage_gain=task.expected_coverage_gain,
            active_task_bonus=active_bonus,
            attempt_penalty=attempt_penalty,
            risk_penalty=risk_penalty,
            explanation=explanation,
        )

    def select(
        self,
        tasks: Sequence[FrontierTaskV1],
        *,
        now: datetime | None = None,
    ) -> TaskBoardDecisionV1:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("task-board clock must include a timezone")
        by_id = {task.id: task for task in tasks}
        completed_ids = {
            task.id for task in tasks if task.status in {"completed", "invalidated"}
        }
        dispositions: list[TaskDispositionV1] = []
        eligible: list[tuple[FrontierTaskV1, TaskScoreV1, bool]] = []
        dedup_winners: dict[tuple[object, ...], tuple[FrontierTaskV1, TaskScoreV1, bool]] = {}

        for task in sorted(tasks, key=lambda item: (item.created_at, item.id)):
            if task.status in {"completed", "failed", "invalidated"}:
                dispositions.append(
                    TaskDispositionV1(
                        task_id=task.id,
                        disposition="terminal",
                        reason=f"任务已经处于终态 {task.status}。",
                    )
                )
                continue
            missing = [
                task_id
                for task_id in task.dependency_task_ids
                if task_id not in by_id or task_id not in completed_ids
            ]
            if missing:
                dispositions.append(
                    TaskDispositionV1(
                        task_id=task.id,
                        disposition="dependency_unmet",
                        reason=f"依赖任务尚未完成：{', '.join(missing)}。",
                    )
                )
                continue
            if task.status == "blocked":
                dispositions.append(
                    TaskDispositionV1(
                        task_id=task.id,
                        disposition="blocked",
                        reason=(
                            f"{task.blocked_reason}；重新激活条件："
                            f"{task.reactivation_condition}。"
                        ),
                    )
                )
                continue
            reactivate = False
            if task.status == "cooldown":
                assert task.cooldown_until is not None
                if _parse_timestamp(task.cooldown_until) > now:
                    dispositions.append(
                        TaskDispositionV1(
                            task_id=task.id,
                            disposition="cooldown_active",
                            reason=f"任务冷却至 {task.cooldown_until}。",
                        )
                    )
                    continue
                reactivate = True
            if task.attempt_count >= task.max_attempts:
                dispositions.append(
                    TaskDispositionV1(
                        task_id=task.id,
                        disposition="attempts_exhausted",
                        reason=(
                            f"已使用 {task.attempt_count}/{task.max_attempts} 次尝试；"
                            "需要新证据或人工调整尝试上限。"
                        ),
                    )
                )
                continue
            if task.risk_score > self.policy.max_risk_score:
                dispositions.append(
                    TaskDispositionV1(
                        task_id=task.id,
                        disposition="risk_exceeds_limit",
                        reason=(
                            f"风险分 {task.risk_score:.2f} 超过本轮上限 "
                            f"{self.policy.max_risk_score:.2f}。"
                        ),
                    )
                )
                continue
            score = self._score(task)
            key = self._dedup_key(task)
            current = dedup_winners.get(key)
            candidate = (task, score, reactivate)
            if current is None or (-score.total, task.created_at, task.id) < (
                -current[1].total,
                current[0].created_at,
                current[0].id,
            ):
                if current is not None:
                    previous = current[0]
                    dispositions.append(
                        TaskDispositionV1(
                            task_id=previous.id,
                            disposition="duplicate",
                            reason=f"与更高优先级任务 {task.id} 内容重复。",
                            duplicate_of=task.id,
                        )
                    )
                dedup_winners[key] = candidate
            else:
                dispositions.append(
                    TaskDispositionV1(
                        task_id=task.id,
                        disposition="duplicate",
                        reason=f"与任务 {current[0].id} 内容重复。",
                        duplicate_of=current[0].id,
                    )
                )

        eligible.extend(dedup_winners.values())
        for task, score, reactivate in eligible:
            dispositions.append(
                TaskDispositionV1(
                    task_id=task.id,
                    disposition="eligible",
                    reason=score.explanation,
                    reactivate_from_cooldown=reactivate,
                    score=score,
                )
            )
        if not eligible:
            remaining = [
                item for item in dispositions if item.disposition != "terminal"
            ]
            reason = (
                "当前没有安全且可达的任务；所有剩余前沿均已给出阻断或重启条件。"
                if remaining
                else "当前没有非终态任务；必须先运行覆盖审计再允许空闲。"
            )
            return TaskBoardDecisionV1(
                idle_allowed=bool(remaining),
                reason=reason,
                dispositions=sorted(dispositions, key=lambda item: item.task_id),
            )
        selected, selected_score, reactivate = min(
            eligible,
            key=lambda item: (-item[1].total, item[0].created_at, item[0].id),
        )
        return TaskBoardDecisionV1(
            selected_task_id=selected.id,
            selected_expected_status=("cooldown" if reactivate else selected.status),
            idle_allowed=False,
            reason=f"选择 {selected.title}：{selected_score.explanation}",
            dispositions=sorted(dispositions, key=lambda item: item.task_id),
        )

    def activate_selected(
        self,
        store: AIPlayerStore,
        environment_id: str,
        decision: TaskBoardDecisionV1,
    ) -> FrontierTaskV1 | None:
        if decision.selected_task_id is None or decision.selected_expected_status is None:
            return None
        task = store.get_task(environment_id, decision.selected_task_id)
        if task is None:
            return None
        if task.status == "active":
            return task
        return store.compare_and_swap_task_status(
            environment_id,
            task.id,
            decision.selected_expected_status,
            "active",
            expected_version=task.version,
            updates={"cooldown_until": None},
        )


def named_blockers(decision: TaskBoardDecisionV1) -> Iterable[str]:
    for item in decision.dispositions:
        if item.disposition not in {"eligible", "terminal", "duplicate"}:
            yield f"{item.task_id}：{item.reason}"