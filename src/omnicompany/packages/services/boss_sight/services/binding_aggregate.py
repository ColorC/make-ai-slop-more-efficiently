# [OMNI] origin=claude-code domain=dashboard/boss_sight/services ts=2026-07-09T00:00:00Z type=service
# [OMNI] material_id="material:dashboard.boss_sight.services.binding_aggregate.py"
"""活跃对话按 plan / project / task 聚合 —— 给计划/项目/任务/对话四页显示"有多少活跃对话在推进"。

权威: docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.5)

数据源: build_residents()(Rust 优先 + digest 回填),已覆盖本机所有 claude/codex 会话
(chat + PTY,补上旧计划/项目页只算 PTY 的缺口)。本层只做确定性分组计数,不另扫、不调模型。

每个分组桶: {key, active(在跑数), total(含已停), sessions:[精简卡...]}。
- active = 在跑(running=True): transcript 近 5 分钟有写入,或托管态 alive。
- total  = 绑到该 key 的全部会话(含已停),把"活着/已结束"分开,不混成一个数。

task 维度: 会话级 task 绑定要到阶段三(session task_id)才填满,现在多为空桶,属预期。
"""
from __future__ import annotations

from typing import Any, Callable


def _is_active(r: dict[str, Any]) -> bool:
    return bool(r.get("running"))


def _compact(r: dict[str, Any]) -> dict[str, Any]:
    """精简会话卡,给四页的下拉/悬浮清单用(别把整条 resident 塞给前端)。"""
    return {
        "key": r.get("key"),
        "name": r.get("name"),
        "provider": r.get("provider"),
        "session_id": r.get("session_id"),
        "run_status": r.get("run_status"),
        "running": bool(r.get("running")),
        "title": r.get("title") or r.get("current_task") or "",
        "active_plan": r.get("active_plan"),
        "project": r.get("project"),
        "task_id": r.get("task_id"),
        "authoritative": bool(r.get("authoritative")),  # 认领(会话自声明) vs 推测
        "attention": bool(r.get("attention")),
        "mtime": r.get("mtime"),
    }


def _bucket(residents: list[dict[str, Any]], keyfn: Callable[[dict[str, Any]], Any]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for r in residents:
        raw = keyfn(r)
        if raw is None or raw == "":
            continue
        k = str(raw)
        g = groups.setdefault(k, {"key": k, "active": 0, "total": 0, "sessions": []})
        g["total"] += 1
        if _is_active(r):
            g["active"] += 1
        g["sessions"].append(_compact(r))
    return groups


def aggregate(*, build: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
    """活跃对话按 plan / project / task 聚合。build 可注入便于测试。"""
    if build is None:
        from .residents import build_residents

        build = build_residents
    payload = build() or {}
    # 排除合成的"总控"常驻卡,避免污染计数。
    residents = [r for r in payload.get("residents", []) if not r.get("is_controller")]

    by_plan = _bucket(residents, lambda r: r.get("active_plan"))
    by_project = _bucket(residents, lambda r: r.get("project"))
    by_task = _bucket(residents, lambda r: r.get("task_id"))
    total_active = sum(1 for r in residents if _is_active(r))

    return {
        "generated_at": payload.get("now"),
        "source": payload.get("source"),
        "total": len(residents),
        "total_active": total_active,
        "by_plan": by_plan,
        "by_project": by_project,
        "by_task": by_task,
    }


__all__ = ["aggregate"]
