"""Workflow summary for BOSS SIGHT cockpit/controller context.

This layer explains what the raw cockpit/action streams mean operationally:
what is still unresolved, what was already read/converted/done, and which
backend actions recently succeeded or failed.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .cockpit import build_cockpit_snapshot
from .cockpit_actions import list_cockpit_action_events


COMMENT_UNRESOLVED_STATUSES = {"delivered", "read", "to_todo"}


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _review_materials() -> tuple[list[Any], dict[str, Any]]:
    try:
        from .reviewstage.routes import get_store

        store = get_store()
        store.reload()
        return store.list(), {"available": True}
    except Exception as exc:  # noqa: BLE001
        return [], {"available": False, "error": f"{type(exc).__name__}: {exc}"}


def _comment_feedback_summary(materials: list[Any], *, limit: int = 40) -> dict[str, Any]:
    by_status: Counter[str] = Counter()
    unresolved: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    for material in materials:
        material_id = getattr(material, "id", "")
        title = getattr(material, "title", "") or material_id
        for comment in getattr(material, "comments", []) or []:
            status = getattr(comment, "feedback_status", None) or "delivered"
            by_status[status] += 1
            item = {
                "material_id": material_id,
                "comment_id": getattr(comment, "id", ""),
                "title": title,
                "content": (getattr(comment, "content", "") or "")[:240],
                "author": getattr(comment, "author", "user"),
                "feedback_status": status,
                "created_at": getattr(comment, "created_at", ""),
                "open_ref": {
                    "type": "review_material",
                    "id": material_id,
                    "url": f"/review-stage?material={material_id}",
                },
            }
            if status in COMMENT_UNRESOLVED_STATUSES:
                unresolved.append(item)
            else:
                resolved.append(item)
    unresolved.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    resolved.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {
        "by_status": dict(by_status),
        "total": sum(by_status.values()),
        "unresolved_count": len(unresolved),
        "todo_done_count": by_status.get("todo_done", 0),
        "unresolved": unresolved[:limit],
        "recent_resolved": resolved[:limit],
    }


def _action_history_summary(ws: str | Path, *, limit: int = 40) -> dict[str, Any]:
    events = list_cockpit_action_events(ws=ws, limit=limit)
    by_status: Counter[str] = Counter(str(event.get("status") or "unknown") for event in events)
    by_kind: Counter[str] = Counter(str(event.get("kind") or "unknown") for event in events)
    failed = [event for event in events if event.get("status") == "failed"]
    succeeded = [event for event in events if event.get("status") == "succeeded"]
    return {
        "recent": events,
        "count": len(events),
        "by_status": dict(by_status),
        "by_kind": dict(by_kind),
        "failed_count": by_status.get("failed", 0),
        "succeeded_count": by_status.get("succeeded", 0),
        "last_failed": failed[0] if failed else None,
        "last_succeeded": succeeded[0] if succeeded else None,
    }


def _workflow_status(*, attention: dict[str, Any], comments: dict[str, Any], actions: dict[str, Any]) -> tuple[str, str]:
    critical_count = int(attention.get("critical_count") or 0)
    attention_count = int(attention.get("attention_count") or 0)
    todo_count = int((comments.get("by_status") or {}).get("to_todo") or 0)
    failed_count = int(actions.get("failed_count") or 0)

    if critical_count:
        return "blocked", "Critical attention remains unresolved."
    if failed_count:
        return "action_failed", "A cockpit action failed and needs inspection."
    if todo_count:
        return "todo_open", "Review feedback has been converted to todo and is still open."
    if attention_count:
        return "attention", "Attention items remain open."
    return "clear", "No unresolved workflow blockers."


def _unresolved_summary(cockpit_snapshot: dict[str, Any]) -> dict[str, Any]:
    attention = cockpit_snapshot.get("attention") or {}
    items = list(attention.get("items") or [])
    by_reason: Counter[str] = Counter(str(item.get("reason") or "unknown") for item in items)
    by_kind: Counter[str] = Counter(str(item.get("kind") or "unknown") for item in items)
    return {
        "count": len(items),
        "critical_count": int(attention.get("critical_count") or 0),
        "attention_count": int(attention.get("attention_count") or 0),
        "by_reason": dict(by_reason),
        "by_kind": dict(by_kind),
        "items": items[:20],
    }


def build_workflow_summary(
    *,
    ws: str | Path,
    cockpit_snapshot: dict[str, Any] | None = None,
    action_limit: int = 40,
) -> dict[str, Any]:
    root = Path(ws)
    snapshot = cockpit_snapshot or build_cockpit_snapshot(
        ws=root,
        attention_limit=50,
        notification_limit=20,
        material_limit=120,
    )
    materials, review_state = _review_materials()
    comments = _comment_feedback_summary(materials)
    actions = _action_history_summary(root, limit=action_limit)
    unresolved = _unresolved_summary(snapshot)
    # AI 主动推送的提醒(带 open_ref 跳转)并入铃铛 —— 用户要"AI 发提醒让我跳转去某页面"(2026-06-27)
    try:
        from .services import agent_notify
        ai_notices = agent_notify.list_notices()
    except Exception:  # noqa: BLE001 — 提醒读取失败不该挡 workflow summary
        ai_notices = []
    blocked_agents = [
        agent
        for agent in (snapshot.get("running_agents") or {}).get("items", [])
        if agent.get("state") == "blocked"
    ]
    status, headline = _workflow_status(
        attention=snapshot.get("attention") or {},
        comments=comments,
        actions=actions,
    )
    summary = {
        "status": status,
        "headline": headline,
        "unresolved_count": unresolved["count"],
        "critical_count": unresolved["critical_count"],
        "comment_unresolved_count": comments["unresolved_count"],
        "comment_todo_done_count": comments["todo_done_count"],
        "blocked_agent_count": len(blocked_agents),
        "action_failed_count": actions["failed_count"],
        "action_succeeded_count": actions["succeeded_count"],
    }
    return {
        "generated_at": snapshot.get("generated_at"),
        "status": status,
        "headline": headline,
        "summary": summary,
        "unresolved": unresolved,
        "comment_feedback": comments,
        "blocked_agents": blocked_agents,
        "action_history": actions,
        "reviewstage": review_state,
        "ctx_summary": {
            "status": status,
            "headline": headline,
            "summary": summary,
            "unresolved": (ai_notices + unresolved["items"])[:10],
            "comment_feedback": {
                "by_status": comments["by_status"],
                "unresolved_count": comments["unresolved_count"],
                "todo_done_count": comments["todo_done_count"],
                "unresolved": comments["unresolved"][:10],
                "recent_resolved": comments["recent_resolved"][:10],
            },
            "action_history": {
                "recent": actions["recent"][:10],
                "failed_count": actions["failed_count"],
                "succeeded_count": actions["succeeded_count"],
                "last_failed": actions["last_failed"],
            },
            "blocked_agents": blocked_agents[:10],
            "decisions": _authored_decisions_for_ctx(),
        },
    }


def _authored_decisions_for_ctx(max_items: int = 10) -> list[dict[str, Any]]:
    """用户标记为 llm_input 的札记被提取出的结构化决策 → 进总控上下文。"""
    try:
        from .authored.extract import load_decisions
        return [
            {"gist": d.get("decision_gist"), "scope": d.get("scope"),
             "applies_to": d.get("applies_to"), "constraint": d.get("constraint"),
             "project": d.get("project")}
            for d in load_decisions()[:max_items]
        ]
    except Exception:
        return []


def format_workflow_ctx_summary(summary: dict[str, Any] | None, *, max_items: int = 5) -> str:
    """Render workflow ctx into a compact controller-readable markdown block."""
    if not isinstance(summary, dict):
        return "## workflow summary\n\n- unavailable"
    ctx = summary.get("ctx_summary") if isinstance(summary.get("ctx_summary"), dict) else summary
    headline = str(ctx.get("headline") or "")
    status = str(ctx.get("status") or "unknown")
    counts = ctx.get("summary") if isinstance(ctx.get("summary"), dict) else {}
    comments = ctx.get("comment_feedback") if isinstance(ctx.get("comment_feedback"), dict) else {}
    actions = ctx.get("action_history") if isinstance(ctx.get("action_history"), dict) else {}
    unresolved = ctx.get("unresolved") if isinstance(ctx.get("unresolved"), list) else []
    blocked_agents = ctx.get("blocked_agents") if isinstance(ctx.get("blocked_agents"), list) else []

    lines = ["## workflow summary", ""]
    lines.append(f"- status={status} headline={headline or '-'}")
    lines.append(
        "- counts: "
        f"unresolved={counts.get('unresolved_count', 0)} "
        f"critical={counts.get('critical_count', 0)} "
        f"comment_unresolved={counts.get('comment_unresolved_count', 0)} "
        f"comment_todo_done={counts.get('comment_todo_done_count', 0)} "
        f"blocked_agents={counts.get('blocked_agent_count', 0)} "
        f"action_failed={counts.get('action_failed_count', 0)}"
    )
    by_status = comments.get("by_status") if isinstance(comments.get("by_status"), dict) else {}
    if by_status:
        compact = ", ".join(f"{k}={v}" for k, v in sorted(by_status.items()))
        lines.append(f"- comment_feedback_by_status: {compact}")
    last_failed = actions.get("last_failed") if isinstance(actions.get("last_failed"), dict) else None
    if last_failed:
        lines.append(
            "- last_failed_action: "
            f"kind={last_failed.get('kind')} "
            f"error={(last_failed.get('error') or '-')[:160]}"
        )
    if unresolved:
        lines.append("")
        lines.append(f"unresolved_top_{min(max_items, len(unresolved))}:")
        for item in unresolved[:max_items]:
            lines.append(
                "- "
                f"{item.get('priority', '?')}/{item.get('reason', '?')} "
                f"kind={item.get('kind', '?')} title={(item.get('title') or '-')[:120]}"
            )
    if blocked_agents:
        lines.append("")
        lines.append(f"blocked_agents_top_{min(max_items, len(blocked_agents))}:")
        for agent in blocked_agents[:max_items]:
            lines.append(
                "- "
                f"{agent.get('id', '?')} plan={agent.get('plan_id') or '-'} "
                f"last_event={agent.get('last_event_at') or '-'}"
            )
    return "\n".join(lines)


__all__ = ["build_workflow_summary", "format_workflow_ctx_summary"]
