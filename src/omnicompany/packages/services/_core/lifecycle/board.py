# [OMNI] origin=claude-code domain=services/_core/lifecycle ts=2026-07-05T00:00:00Z type=infra status=active
# [OMNI] summary="plan 全景钻取数据: plan→task→agent绑定→material审阅状态 聚合成 JSON; 消费方=CLI(omni task board)与 work_report"
# [OMNI] why="WORK-LIFECYCLE-AND-DISPATCH N-board 的数据层; 网页任务视图已由任务窗口(QuestBoard, 读 progressd)承担, 本模块未接前端"
# [OMNI] tags=lifecycle,board,drilldown,cli
# [OMNI] material_id="material:services._core.lifecycle.board.py"
"""plan 全景钻取数据聚合。

消费方: `omni task board` CLI 与 work_report。网页侧的任务展示走驾驶舱任务窗口
(QuestBoard → progressd :8230, 含执行子任务), 本模块没有接任何前端路由。
"""
from __future__ import annotations

from typing import Any


def _review_store():
    """拿全局 MaterialStore 单例 (正确构造: 带 root + format_registry)。"""
    from omnicompany.dashboard.boss_sight.reviewstage.routes import get_store
    return get_store()


def _enum_val(v: Any) -> str:
    return str(getattr(v, "value", v) or "")


def _materials_for_plan(plan_id: str) -> list[dict[str, Any]]:
    """该 plan 下的审阅材料 (用 store 内置 plan_id 过滤, 不自己重做)。"""
    try:
        mats = _review_store().list(plan_id=plan_id)
    except Exception:
        return []
    out = []
    for m in mats:
        out.append({
            "id": getattr(m, "id", None) or getattr(m, "material_id", None),
            "title": getattr(m, "title", None),
            "kind": _enum_val(getattr(m, "kind", "")),
            "tier": _enum_val(getattr(m, "tier", "")),
            "status": _enum_val(getattr(m, "status", "")),
            "source_subagent_id": getattr(m, "source_subagent_id", None),
        })
    return out


def board_data(plan_id: str) -> dict[str, Any]:
    """plan 全景钻取: frontmatter + gate + tasks + bindings + materials。"""
    from omnicompany.core.plans_catalogue import _plans_root, parse_plan_frontmatter
    from omnicompany.packages.services._core.lifecycle.dispatch_task import list_task_bindings
    from omnicompany.packages.services._core.lifecycle.task import TaskStore
    from omnicompany.packages.services._core.plan_audit.gate import check_plan_dispatch_gate

    fm = parse_plan_frontmatter(_plans_root() / plan_id / "plan.md")
    store = TaskStore()
    tasks = store.list_tasks(plan_id)
    binds = {b.get("task_id"): b for b in list_task_bindings(plan_id)}
    materials = _materials_for_plan(plan_id)
    mats_by_task: dict[str, list[dict]] = {}
    for m in materials:
        sid = m.get("source_subagent_id") if isinstance(m, dict) else None
        key = sid[len("task-"):] if isinstance(sid, str) and sid.startswith("task-") and sid[len("task-"):] else "_plan"
        mats_by_task.setdefault(key, []).append(m)

    gate = check_plan_dispatch_gate(plan_id)
    task_rows = []
    for t in tasks:
        b = binds.get(t.id, {})
        task_rows.append({
            "id": t.id, "title": t.title, "status": t.status,
            "priority": t.priority, "complexity": t.complexity, "parallel": t.parallel,
            "dependencies": t.dependencies, "assignee": t.assignee,
            "team_id": t.team_id, "position_id": t.position_id,
            "agent": b.get("agent"), "carrier": b.get("carrier"),
            "binding_status": b.get("status"),
            "has_test_strategy": bool((t.test_strategy or "").strip()),
        })
    counts: dict[str, int] = {}
    for t in tasks:
        counts[t.status] = counts.get(t.status, 0) + 1
    return {
        "plan_id": plan_id,
        "title": fm.get("title"),
        "status": fm.get("status"),
        "gate": {"ok": gate["ok"], "summary": gate["summary"], "blocks": gate["blocks"]},
        "task_count": len(tasks),
        "status_counts": counts,
        "tasks": task_rows,
        "materials": materials,
        "materials_by_task": mats_by_task,
    }


__all__ = ["board_data"]
