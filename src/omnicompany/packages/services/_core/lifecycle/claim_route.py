# [OMNI] origin=codex domain=services/_core/lifecycle ts=2026-07-24T00:00:00Z type=infra status=active
# [OMNI] summary="任务首次认领与 Team 岗位投递的原子设施；复用 Task、Project registry、TeamSpec 与 EventBus，不启动执行"
# [OMNI] why="任务负责人和岗位目录已有唯一真源，但缺少从项目归属检查到唯一认领、岗位落位的无执行闭环"
# [OMNI] tags=lifecycle,task,claim,team-position,routing,eventbus,no-execution
# [OMNI] material_id="material:services._core.lifecycle.claim_route.py"
"""任务首次认领并路由到 Team 岗位。

唯一权威边界：

- 项目与 Team 关系来自 Project registry；
- 岗位来自 TeamSpec.positions；
- 当前负责人、目标 Team 和目标岗位写回 canonical Task；
- EventBus 只保存审计回执；
- 本模块绝不调用 TeamRunner、dispatch_task 或任何领域 Worker。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ulid import ULID

from omnicompany.bus.base import EventBus
from omnicompany.protocol.events import FactoryEvent
from omnicompany.protocol.registry import EventType
from omnicompany.protocol.team import TeamPositionSpec, TeamSpec

from .task import TaskStore, local_task_id


_CLAIM_ERROR_ZH = {
    "task_not_found": "任务不存在",
    "plan_not_found": "计划不存在",
    "task_closed": "任务已经结束或归档",
    "assignee_conflict": "任务已由其他负责人认领",
    "team_conflict": "任务已经路由到其他团队",
    "position_conflict": "任务已经进入其他岗位",
    "missing_claim_field": "认领信息不完整",
    "task_plan_mismatch": "完整任务号不属于指定计划",
}


@dataclass(frozen=True)
class TaskPositionClaimRequest:
    """一次无执行的任务认领与岗位投递请求。"""

    project_id: str
    plan_id: str
    task_id: str
    team_id: str
    position_id: str
    assignee: str
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TaskPositionClaimReceipt:
    """供人和后续设施消费的结构化回执，不是新的状态存储。"""

    ok: bool
    status: str
    reason: str
    project_id: str
    project_name: str
    plan_id: str
    task_id: str
    task_title: str
    team_id: str
    team_name: str
    position_id: str
    position_name: str
    position_activation: str
    assignee: str
    evidence_refs: tuple[str, ...]
    idempotent: bool = False
    execution_started: bool = False
    trace_id: str = ""
    audit_event_id: str = ""
    audit_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def summary_zh(self) -> str:
        if self.ok and self.idempotent:
            return (
                f"任务“{self.task_title}”此前已由“{self.assignee}”认领，"
                f"并进入“{self.position_name}”；本次未重复写入，也未开始执行。"
            )
        if self.ok:
            return (
                f"任务“{self.task_title}”已由“{self.assignee}”认领，"
                f"并进入“{self.position_name}”；尚未开始执行。"
            )
        return f"任务“{self.task_title or self.task_id}”未认领：{self.reason}"


def _non_empty_request_error(request: TaskPositionClaimRequest) -> str:
    values = {
        "project_id": request.project_id,
        "plan_id": request.plan_id,
        "task_id": request.task_id,
        "team_id": request.team_id,
        "position_id": request.position_id,
        "assignee": request.assignee,
    }
    empty = [name for name, value in values.items() if not str(value or "").strip()]
    if empty:
        return f"认领字段不能为空: {empty}"
    if not request.evidence_refs or any(
        not str(reference or "").strip() for reference in request.evidence_refs
    ):
        return "至少需要一条非空的路由依据"
    return ""


def _plan_belongs_to_project(
    project: dict[str, Any],
    plan_id: str,
    *,
    governance: dict[str, dict[str, Any]],
) -> bool:
    governed = governance.get(plan_id)
    if governed is not None:
        return governed.get("project") == project.get("id")
    categories = [
        str(value).strip().rstrip("/")
        for value in project.get("plan_categories", [])
        if str(value).strip()
    ]
    return any(
        plan_id == category or plan_id.startswith(category + "/")
        for category in categories
    )


def _position(team: TeamSpec, position_id: str) -> TeamPositionSpec | None:
    return next(
        (position for position in team.positions if position.id == position_id),
        None,
    )


async def _publish_receipt_event(
    *,
    bus: EventBus | None,
    trace_id: str,
    source: str,
    receipt: TaskPositionClaimReceipt,
) -> tuple[str, str]:
    if bus is None or receipt.idempotent:
        return "", ""
    event_type = (
        EventType.TASK_POSITION_CLAIMED
        if receipt.ok
        else EventType.TASK_POSITION_CLAIM_REJECTED
    )
    event = FactoryEvent(
        trace_id=trace_id,
        event_type=event_type.value,
        source=source,
        payload={
            "status": receipt.status,
            "reason": receipt.reason,
            "project_id": receipt.project_id,
            "plan_id": receipt.plan_id,
            "task_id": receipt.task_id,
            "task_title": receipt.task_title,
            "team_id": receipt.team_id,
            "team_name": receipt.team_name,
            "position_id": receipt.position_id,
            "position_name": receipt.position_name,
            "position_activation": receipt.position_activation,
            "assignee": receipt.assignee,
            "evidence_refs": list(receipt.evidence_refs),
            "execution_started": False,
        },
        tags=["task.claim", "team.position", "no_execution"],
    )
    try:
        await bus.publish(event)
    except Exception as exc:  # 审计失败不能伪装成认领回滚
        return "", str(exc)
    return event.id, ""


async def claim_task_to_position(
    request: TaskPositionClaimRequest,
    *,
    project: dict[str, Any],
    team: TeamSpec,
    store: TaskStore,
    governance: dict[str, dict[str, Any]] | None = None,
    bus: EventBus | None = None,
    trace_id: str | None = None,
    source: str = "lifecycle.claim_route",
) -> TaskPositionClaimReceipt:
    """验证项目/Team/岗位后原子认领；只落位，不启动任何执行。"""
    current_trace = str(trace_id or ULID())
    request_error = _non_empty_request_error(request)
    project_name = str(project.get("name") or project.get("id") or request.project_id)
    position = _position(team, request.position_id)

    reason = request_error
    if not reason and project.get("id") != request.project_id:
        reason = "请求项目与项目注册表记录不一致"
    if not reason and team.id != request.team_id:
        reason = "请求团队与团队定义不一致"
    if not reason and request.team_id not in set(project.get("team_ids") or []):
        reason = "目标团队未被该项目引用"
    if not reason and not _plan_belongs_to_project(
        project,
        request.plan_id,
        governance=governance or {},
    ):
        reason = "该计划不属于目标项目"
    if not reason and position is None:
        reason = "目标岗位不在团队岗位目录中"
    normalized_task_id = ""
    if not reason:
        try:
            normalized_task_id = local_task_id(request.plan_id, request.task_id)
        except ValueError:
            reason = "完整任务号不属于指定计划"
    task = store.get(normalized_task_id, request.plan_id) if not reason else None
    if not reason and task is None:
        reason = "任务不存在"
    task_title = task.title if task is not None else ""

    if reason:
        receipt = TaskPositionClaimReceipt(
            ok=False,
            status="rejected",
            reason=reason,
            project_id=request.project_id,
            project_name=project_name,
            plan_id=request.plan_id,
            task_id=request.task_id,
            task_title=task_title,
            team_id=request.team_id,
            team_name=team.name,
            position_id=request.position_id,
            position_name=position.name if position else "",
            position_activation=position.activation.value if position else "",
            assignee=request.assignee,
            evidence_refs=request.evidence_refs,
            trace_id=current_trace,
        )
    else:
        result = store.claim_for_position(
            request.task_id,
            plan_id=request.plan_id,
            assignee=request.assignee,
            team_id=request.team_id,
            position_id=request.position_id,
        )
        task_payload = result.get("task") or {}
        ok = bool(result.get("ok"))
        status = str(result.get("status") or ("claimed" if ok else "rejected"))
        error = str(result.get("error") or "")
        receipt = TaskPositionClaimReceipt(
            ok=ok,
            status=status,
            reason="" if ok else _CLAIM_ERROR_ZH.get(error, error or "认领冲突"),
            project_id=request.project_id,
            project_name=project_name,
            plan_id=request.plan_id,
            task_id=request.task_id,
            task_title=str(task_payload.get("title") or task_title),
            team_id=request.team_id,
            team_name=team.name,
            position_id=request.position_id,
            position_name=position.name,
            position_activation=position.activation.value,
            assignee=request.assignee,
            evidence_refs=request.evidence_refs,
            idempotent=status == "already_claimed",
            trace_id=current_trace,
        )

    event_id, audit_error = await _publish_receipt_event(
        bus=bus,
        trace_id=current_trace,
        source=source,
        receipt=receipt,
    )
    return TaskPositionClaimReceipt(
        **{
            **receipt.to_dict(),
            "audit_event_id": event_id,
            "audit_error": audit_error,
        }
    )


__all__ = [
    "TaskPositionClaimReceipt",
    "TaskPositionClaimRequest",
    "claim_task_to_position",
]
