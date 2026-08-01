"""Backend data contract for the BOSS SIGHT cockpit.

This module intentionally stays UI-agnostic. It aggregates the existing
plan/material/subagent/entity surfaces into stable objects a future cockpit UI
can render without re-deriving product semantics in React.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aggregator.plan_index_scanner import PlanIndexEntry, PlanIndexScanner
from .aggregator.subagent_status_aggregator import SubagentStatusAggregator
from .material_registry import build_material_registry
from .reviewstage import Material


INACTIVE_PLAN_STATUSES = {"done", "archived", "superseded"}
REVIEW_BLOCKING_STATUSES = {"pending", "rejected", "blocked"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _review_store_items() -> tuple[list[Material], dict[str, Any]]:
    try:
        from .reviewstage.routes import get_store

        store = get_store()
        store.reload()
        return store.list(), {"available": True}
    except Exception as exc:  # noqa: BLE001
        return [], {"available": False, "error": f"{type(exc).__name__}: {exc}"}


def _plan_to_dict(entry: PlanIndexEntry | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    return {
        "plan_id": entry.plan_id,
        "category": entry.category,
        "title": entry.title or entry.plan_id,
        "status": entry.status,
        "todo_done": entry.todo_done,
        "todo_total": entry.todo_total,
        "path": entry.plan_path,
        "project_path": entry.project_path,
        "updated_at": entry.last_modified_ts,
        "open_ref": {"type": "plan", "id": entry.plan_id},
    }


def _active_plan(entries: list[PlanIndexEntry]) -> PlanIndexEntry | None:
    for entry in entries:
        status = (entry.status or "").lower()
        if status not in INACTIVE_PLAN_STATUSES:
            return entry
    return entries[0] if entries else None


def _material_open_ref(material: Material) -> dict[str, Any]:
    return {
        "type": "review_material",
        "id": material.id,
        "url": f"/review-stage?material={material.id}",
    }


def _material_summary(material: Material) -> dict[str, Any]:
    status = _value(material.status)
    tier = _value(material.tier)
    kind = _value(material.kind)
    warnings = material.extra.get("structure_warnings") if isinstance(material.extra, dict) else None
    return {
        "id": material.id,
        "title": material.title,
        "kind": kind,
        "tier": tier,
        "status": status,
        "source_plan_id": material.source_plan_id,
        "source_subagent_id": material.source_subagent_id,
        "pushed_to_user": bool(material.pushed_to_user),
        "pushed_reason": material.pushed_reason,
        "pushed_at": material.pushed_at,
        "updated_at": material.updated_at,
        "created_at": material.created_at,
        "comment_count": len(material.comments),
        "annotation_count": len(material.annotations),
        "structure_warning_count": len(warnings) if isinstance(warnings, list) else 0,
        "open_ref": _material_open_ref(material),
    }


def _attention_action(kind: str, label: str, *, target: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"kind": kind, "label": label, "target": target or {}}


def _attention_from_material(material: Material) -> list[dict[str, Any]]:
    status = _value(material.status)
    tier = _value(material.tier)
    out: list[dict[str, Any]] = []
    base = _material_summary(material)

    if tier == "mandatory" and status in REVIEW_BLOCKING_STATUSES:
        attention_id = f"review:{material.id}:mandatory"
        out.append({
            "id": attention_id,
            "kind": "review_material",
            "priority": "critical",
            "reason": "mandatory_material_unaccepted",
            "title": material.title,
            "summary": "Mandatory material is not accepted yet.",
            "target": base,
            "open_ref": base["open_ref"],
            "actions": [
                _attention_action("acknowledge_attention", "Mark read", target={"type": "review_material", "id": material.id, "attention_id": attention_id}),
                _attention_action("open_review", "Open review", target=base["open_ref"]),
                _attention_action("open_plan", "Open source plan", target={"type": "plan", "id": material.source_plan_id} if material.source_plan_id else None),
            ],
            "created_at": material.updated_at or material.created_at,
        })

    if status in {"rejected", "blocked"} and tier != "mandatory":
        attention_id = f"review:{material.id}:{status}"
        out.append({
            "id": attention_id,
            "kind": "review_material",
            "priority": "critical" if status == "blocked" else "attention",
            "reason": f"material_{status}",
            "title": material.title,
            "summary": f"Review material is {status}.",
            "target": base,
            "open_ref": base["open_ref"],
            "actions": [
                _attention_action("acknowledge_attention", "Mark read", target={"type": "review_material", "id": material.id, "attention_id": attention_id}),
                _attention_action("open_review", "Open review", target=base["open_ref"]),
            ],
            "created_at": material.updated_at or material.created_at,
        })

    if material.pushed_to_user and status == "pending":
        attention_id = f"review:{material.id}:pushed"
        out.append({
            "id": attention_id,
            "kind": "review_material",
            "priority": "attention",
            "reason": "pushed_pending_material",
            "title": material.title,
            "summary": material.pushed_reason or "Controller pushed this material for user attention.",
            "target": base,
            "open_ref": base["open_ref"],
            "actions": [
                _attention_action("acknowledge_attention", "Mark read", target={"type": "review_material", "id": material.id, "attention_id": attention_id}),
                _attention_action("open_review", "Open review", target=base["open_ref"]),
            ],
            "created_at": material.pushed_at or material.updated_at or material.created_at,
        })

    for comment in material.comments:
        feedback_status = comment.feedback_status or "delivered"
        # Merely saving a comment must not wake the controller or create an
        # unresolved cockpit item.  Only an explicit delivery enters this
        # workflow; completed todo feedback is terminal as before.
        if feedback_status in {"saved", "todo_done"}:
            continue
        attention_id = f"comment:{material.id}:{comment.id}"
        out.append({
            "id": attention_id,
            "kind": "review_comment",
            "priority": "attention" if feedback_status in {"delivered", "read"} else "info",
            "reason": f"comment_feedback_{feedback_status}",
            "title": f"Comment on {material.title}",
            "summary": comment.content[:240],
            "target": {
                **base,
                "comment_id": comment.id,
                "feedback_status": feedback_status,
                "comment_author": comment.author,
                "comment_created_at": comment.created_at,
            },
            "open_ref": base["open_ref"],
            "actions": [
                _attention_action("acknowledge_attention", "Mark read", target={"type": "review_comment", "material_id": material.id, "comment_id": comment.id, "attention_id": attention_id}),
                _attention_action("open_review", "Open review", target=base["open_ref"]),
                _attention_action("mark_todo", "Convert to todo", target={"type": "review_comment", "material_id": material.id, "comment_id": comment.id, "attention_id": attention_id}),
                _attention_action("complete_todo", "Mark todo done", target={"type": "review_comment", "material_id": material.id, "comment_id": comment.id, "attention_id": attention_id}),
            ],
            "created_at": comment.created_at,
        })

    return out


def _agent_dict(agent: dict[str, Any]) -> dict[str, Any]:
    sid = agent.get("subagent_id") or agent.get("id") or ""
    return {
        "id": sid,
        "kind": agent.get("kind") or "standalone",
        "state": agent.get("state") or "idle",
        "plan_id": agent.get("plan_id"),
        "cwd": agent.get("cwd"),
        "started_at": agent.get("started_at_ts"),
        "last_event_at": agent.get("last_event_ts"),
        "open_ref": {"type": "cc_session", "id": sid} if sid else {},
    }


def _attention_from_agent(agent: dict[str, Any]) -> list[dict[str, Any]]:
    item = _agent_dict(agent)
    if item["state"] != "blocked":
        return []
    attention_id = f"subagent:{item['id']}:blocked"
    return [{
        "id": attention_id,
        "kind": "subagent",
        "priority": "critical",
        "reason": "subagent_blocked",
        "title": f"Subagent blocked: {item['id']}",
        "summary": "A running executor is blocked and needs controller/user attention.",
        "target": item,
        "open_ref": item["open_ref"],
        "actions": [
            _attention_action("acknowledge_attention", "Mark read", target={"type": "cc_session", "id": item["id"], "attention_id": attention_id}),
            _attention_action("open_session", "Open session", target=item["open_ref"]),
            _attention_action("open_plan", "Open plan", target={"type": "plan", "id": item["plan_id"]} if item["plan_id"] else None),
        ],
        "created_at": item["last_event_at"],
    }]


def _priority_rank(priority: str) -> int:
    return {"critical": 0, "attention": 1, "info": 2, "calm": 3}.get(priority, 9)


def _sort_attention(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            _priority_rank(str(item.get("priority") or "")),
            str(item.get("created_at") or ""),
        ),
        reverse=False,
    )


def _notification_from_material_event(material: Material, event: dict[str, Any]) -> dict[str, Any]:
    event_name = str(event.get("event") or "updated")
    priority = "info"
    if event_name == "verdict" and event.get("to") in {"rejected", "blocked"}:
        priority = "critical" if event.get("to") == "blocked" else "attention"
    elif event_name in {"pushed", "comment"}:
        priority = "attention"
    elif event_name == "structure_warning":
        priority = "info"
    base = _material_summary(material)
    return {
        "id": f"material:{material.id}:{event_name}:{event.get('at') or material.updated_at or material.created_at}",
        "kind": "material_event",
        "priority": priority,
        "title": material.title,
        "event": event_name,
        "summary": _event_summary(event),
        "target": base,
        "open_ref": base["open_ref"],
        "created_at": event.get("at") or material.updated_at or material.created_at,
    }


def _event_summary(event: dict[str, Any]) -> str:
    name = str(event.get("event") or "updated")
    if name == "verdict":
        return f"Verdict changed from {event.get('from')} to {event.get('to')}."
    if name == "comment":
        return f"Comment added by {event.get('by') or 'unknown'}."
    if name == "pushed":
        return str(event.get("reason") or "Material pushed to user.")
    if name == "structure_warning":
        return f"{event.get('count') or 0} structure warning(s)."
    if name == "tier_change":
        return f"Tier changed from {event.get('from')} to {event.get('to')}."
    return name.replace("_", " ")


def _material_notifications(materials: list[Material], *, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for material in materials:
        for event in list(material.history or [])[-5:]:
            if isinstance(event, dict):
                out.append(_notification_from_material_event(material, event))
    out.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return out[:limit]


def _running_agents(sub_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _agent_dict(agent)
        for agent in sub_payload.get("subagents", [])
        if (agent.get("state") or "") in {"running", "blocked", "idle"}
    ]


def _top_actions(attention_items: list[dict[str, Any]], active_plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    first_critical = next((item for item in attention_items if item.get("priority") == "critical"), None)
    if first_critical:
        actions.append({
            "kind": "resolve_attention",
            "label": "Resolve highest priority item",
            "target": first_critical.get("open_ref") or {},
        })
    if active_plan:
        actions.append({
            "kind": "open_active_plan",
            "label": "Open active plan",
            "target": active_plan.get("open_ref") or {},
        })
    actions.append({
        "kind": "open_controller",
        "label": "Open controller",
        "target": {"type": "controller", "id": "main"},
    })
    return actions


def build_cockpit_snapshot(
    *,
    ws: str | Path,
    attention_limit: int = 30,
    notification_limit: int = 30,
    material_limit: int = 200,
) -> dict[str, Any]:
    root = Path(ws)
    plan_entries = PlanIndexScanner(root).scan()
    active_entry = _active_plan(plan_entries)
    active = _plan_to_dict(active_entry)

    sub_agg = SubagentStatusAggregator(root)
    sub_agg.refresh_from_cc_sessions()
    sub_payload = sub_agg.to_material_payload()
    agents = _running_agents(sub_payload)

    review_materials, review_state = _review_store_items()
    material_registry = build_material_registry(limit=material_limit, ws=root)

    attention: list[dict[str, Any]] = []
    for material in review_materials:
        attention.extend(_attention_from_material(material))
    for agent in sub_payload.get("subagents", []):
        attention.extend(_attention_from_agent(agent))
    attention = _sort_attention(attention)[: max(1, min(int(attention_limit), 100))]

    notifications = _material_notifications(
        review_materials,
        limit=max(1, min(int(notification_limit), 100)),
    )

    plan_status_counts = Counter((entry.status or "unknown") for entry in plan_entries)
    agent_state_counts = Counter(agent["state"] for agent in agents)
    review_status_counts = Counter(_value(m.status) for m in review_materials)
    review_tier_counts = Counter(_value(m.tier) for m in review_materials)

    recent_materials = [_material_summary(material) for material in review_materials[:12]]
    priority_counts = Counter(str(item.get("priority") or "unknown") for item in attention)

    return {
        "generated_at": _now_iso(),
        "active_plan": active,
        "summary": {
            "plans_total": len(plan_entries),
            "plans_by_status": dict(plan_status_counts),
            "attention_total": len(attention),
            "attention_by_priority": dict(priority_counts),
            "notifications_total": len(notifications),
            "running_agents_total": len(agents),
            "agents_by_state": dict(agent_state_counts),
            "review_total": len(review_materials),
            "review_by_status": dict(review_status_counts),
            "review_by_tier": dict(review_tier_counts),
        },
        "attention": {
            "items": attention,
            "count": len(attention),
            "critical_count": priority_counts.get("critical", 0),
            "attention_count": priority_counts.get("attention", 0),
        },
        "notifications": {
            "items": notifications,
            "count": len(notifications),
            "unread_count": len(notifications),
        },
        "running_agents": {
            "items": agents,
            "count": len(agents),
            "blocked_count": agent_state_counts.get("blocked", 0),
            "running_count": agent_state_counts.get("running", 0),
        },
        "recent_materials": recent_materials,
        "material_registry": material_registry.get("summary", {}),
        "reviewstage": {
            **review_state,
            "count": len(review_materials),
            "by_status": dict(review_status_counts),
            "by_tier": dict(review_tier_counts),
        },
        "top_actions": _top_actions(attention, active),
        "ctx_summary": {
            "active_plan": active,
            "attention": attention[:10],
            "running_agents": agents[:10],
            "material_registry": material_registry.get("summary", {}),
        },
    }


def build_attention_state(
    *,
    ws: str | Path,
    attention_limit: int = 50,
    notification_limit: int = 50,
) -> dict[str, Any]:
    snapshot = build_cockpit_snapshot(
        ws=ws,
        attention_limit=attention_limit,
        notification_limit=notification_limit,
        material_limit=120,
    )
    return {
        "generated_at": snapshot["generated_at"],
        "attention": snapshot["attention"],
        "notifications": snapshot["notifications"],
        "top_actions": snapshot["top_actions"],
    }


__all__ = ["build_attention_state", "build_cockpit_snapshot"]
