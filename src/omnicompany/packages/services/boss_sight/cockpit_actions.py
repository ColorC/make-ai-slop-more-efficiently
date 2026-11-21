"""Executable backend actions for BOSS SIGHT cockpit attention items.

The cockpit contract is read-only. This module is the small write-side bridge:
it resolves open_ref targets, advances review comment feedback state, and records
auditable action events for human/controller actions.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aggregator.plan_index_scanner import PlanIndexScanner


ACTION_KINDS = {
    "open_review",
    "open_plan",
    "open_session",
    "open_controller",
    "open_active_plan",
    "resolve_attention",
    "acknowledge_attention",
    "mark_todo",
    "complete_todo",
}
OPEN_ACTION_KINDS = {
    "open_review",
    "open_plan",
    "open_session",
    "open_controller",
    "open_active_plan",
    "resolve_attention",
}
COMMENT_FEEDBACK_ACTIONS = {
    "acknowledge_attention": "read",
    "mark_todo": "to_todo",
    "complete_todo": "todo_done",
}
ACTORS = {"human", "controller"}
MAX_EVENTS = 200


class CockpitActionError(Exception):
    def __init__(self, status_code: int, message: str, *, event: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.event = event


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_id() -> str:
    return f"cockpit_action_{time.time_ns()}"


def _compact(value: Any, *, limit: int = 500) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, dict):
        return {str(k)[:80]: _compact(v, limit=limit) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [_compact(v, limit=limit) for v in value[:40]]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:limit]


def _events_path(ws: str | Path) -> Path:
    return Path(ws) / "data" / "boss_sight" / "cockpit_action_events.json"


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
        suffix=".tmp",
    )
    try:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    os.replace(tmp.name, str(path))


def _read_events(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)][-MAX_EVENTS:]


def _record_action_event(
    *,
    ws: str | Path,
    kind: str,
    target: dict[str, Any],
    actor: str,
    note: str,
    status: str,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    path = _events_path(ws)
    event = {
        "id": _event_id(),
        "kind": kind,
        "actor": actor,
        "target": _compact(target),
        "note": note[:500],
        "status": status,
        "result": _compact(result or {}),
        "error": error[:500],
        "created_at": _now_iso(),
    }
    events = _read_events(path)
    events.append(event)
    events = events[-MAX_EVENTS:]
    _atomic_write(path, json.dumps(events, ensure_ascii=False, indent=2))
    return event


def list_cockpit_action_events(*, ws: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), MAX_EVENTS))
    return list(reversed(_read_events(_events_path(ws))[-limit:]))


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _target_type(target: dict[str, Any]) -> str:
    target_type = str(target.get("type") or "").strip()
    if target_type:
        return target_type
    if target.get("comment_id") and target.get("material_id"):
        return "review_comment"
    if target.get("material_id") or str(target.get("id") or "").startswith("mat_"):
        return "review_material"
    return ""


def _legacy_review_store():
    """旧 reviewstage MaterialStore —— 仅剩 `_set_comment_feedback` 的遗留写路径在用。

    2026-08-20 审阅库融合: 数据面读取已全部换到共享核心 review_store（画布
    card.submission 投影）。comment feedback 的推进(acknowledge/mark_todo/complete_todo)
    仍写旧库，是 boss_sight 内最后一处生产写调用；画布侧没有对应写口，未接到画布，
    已在融合拆除轮报告中作为遗留问题上报。
    """
    from .reviewstage.routes import get_store

    store = get_store()
    store.reload()
    return store


def _get_review_material(material_id: str) -> dict[str, Any] | None:
    """按 id 读一条审阅材料（共享核心 review_store 投影，dict 形状）。"""
    from omnicompany.packages.services._core import review_store

    return review_store.get_material(material_id)


def _resolve_review_material(target: dict[str, Any]) -> dict[str, Any]:
    material_id = str(target.get("material_id") or target.get("id") or "").strip()
    if not material_id:
        raise CockpitActionError(400, "review material target requires id/material_id")
    material = _get_review_material(material_id)
    if material is None:
        raise CockpitActionError(404, f"review material not found: {material_id}")
    return {
        "type": "review_material",
        "id": material.get("id"),
        "title": material.get("title"),
        "status": _value(material.get("status")),
        "tier": _value(material.get("tier")),
        "url": f"/review-stage?material={material.get('id')}",
        "exists": True,
    }


def _resolve_review_comment(target: dict[str, Any]) -> dict[str, Any]:
    material_id = str(target.get("material_id") or "").strip()
    comment_id = str(target.get("comment_id") or "").strip()
    if not material_id or not comment_id:
        raise CockpitActionError(400, "review comment target requires material_id and comment_id")
    material = _get_review_material(material_id)
    if material is None:
        raise CockpitActionError(404, f"review material not found: {material_id}")
    comment = next(
        (c for c in material.get("comments") or []
         if isinstance(c, dict) and c.get("id") == comment_id),
        None,
    )
    if comment is None:
        raise CockpitActionError(404, f"review comment not found: {comment_id}")
    return {
        "type": "review_comment",
        "material_id": material.get("id"),
        "comment_id": comment.get("id"),
        "title": material.get("title"),
        "feedback_status": comment.get("feedback_status") or "delivered",
        "url": f"/review-stage?material={material.get('id')}&comment={comment.get('id')}",
        "exists": True,
    }


def _resolve_plan(ws: str | Path, target: dict[str, Any]) -> dict[str, Any]:
    plan_id = str(target.get("id") or target.get("plan_id") or "").strip()
    if not plan_id:
        raise CockpitActionError(400, "plan target requires id/plan_id")
    for entry in PlanIndexScanner(Path(ws)).scan():
        if entry.plan_id == plan_id:
            return {
                "type": "plan",
                "id": entry.plan_id,
                "title": entry.title or entry.plan_id,
                "status": entry.status,
                "path": entry.plan_path,
                "project_path": entry.project_path,
                "exists": True,
            }
    raise CockpitActionError(404, f"plan not found: {plan_id}")


def _resolve_cc_session(ws: str | Path, target: dict[str, Any]) -> dict[str, Any]:
    session_id = str(target.get("id") or target.get("session_id") or "").strip()
    if not session_id:
        raise CockpitActionError(400, "cc_session target requires id/session_id")
    path = Path(ws) / "data" / "cc_sessions.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise CockpitActionError(404, f"cc session index unavailable: {type(exc).__name__}") from exc
    if not isinstance(raw, dict) or session_id not in raw or not isinstance(raw.get(session_id), dict):
        raise CockpitActionError(404, f"cc_session not found: {session_id}")
    session = raw[session_id]
    return {
        "type": "cc_session",
        "id": session_id,
        "provider": session.get("provider"),
        "status": session.get("status") or ("running" if session.get("alive") else "done"),
        "active_plan": session.get("active_plan"),
        "cwd": session.get("cwd"),
        "exists": True,
    }


def resolve_action_target(*, ws: str | Path, target: dict[str, Any]) -> dict[str, Any]:
    target = dict(target or {})
    target_type = _target_type(target)
    if target_type in {"review_material", "material"}:
        return _resolve_review_material(target)
    if target_type == "review_comment":
        return _resolve_review_comment(target)
    if target_type == "plan":
        return _resolve_plan(ws, target)
    if target_type in {"cc_session", "session"}:
        return _resolve_cc_session(ws, target)
    if target_type == "controller":
        return {"type": "controller", "id": str(target.get("id") or "main"), "exists": True}
    if target_type == "material_registry":
        return {"type": "material_registry", "id": str(target.get("id") or "main"), "exists": True}
    # page_element 札记(圈选页面元素生成的评论)回指: 用 url+selector 重进同页高亮。
    if target_type == "page_element" or target.get("kind") == "page_element":
        return {
            "type": "page_element",
            "url": str(target.get("url") or "")[:500],
            "route": str(target.get("route") or "")[:500],
            "selector": str(target.get("selector") or "")[:500],
            "title": str(target.get("title") or "")[:200],
            "exists": True,
        }
    if target.get("url"):
        return {"type": "url", "url": str(target["url"])[:500], "exists": True}
    raise CockpitActionError(400, "unsupported or missing action target type")


def _set_comment_feedback(
    *,
    target: dict[str, Any],
    status: str,
    actor: str,
    note: str,
) -> dict[str, Any]:
    # 遗留写路径（2026-08-20 融合后未迁移）：仍写旧 reviewstage 库。新评论真源是中心
    # authored store、材料真源是画布 submission，两者都没有 comment feedback 写口；
    # 这里只对旧式 Material.comments[] 里的历史评论有效，保留行为不变，待裁决退役方式。
    resolved_before = _resolve_review_comment(target)
    store = _legacy_review_store()
    comment = store.set_comment_feedback(
        resolved_before["material_id"],
        resolved_before["comment_id"],
        status=status,
        by=actor,
        note=note,
    )
    resolved_after = _resolve_review_comment({
        "type": "review_comment",
        "material_id": resolved_before["material_id"],
        "comment_id": resolved_before["comment_id"],
    })
    return {
        "effect": "comment_feedback",
        "previous_feedback_status": resolved_before["feedback_status"],
        "feedback_status": comment.feedback_status,
        "resolved": resolved_after,
    }


def execute_cockpit_action(
    *,
    ws: str | Path,
    kind: str,
    target: dict[str, Any],
    actor: str = "human",
    note: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind = str(kind or "").strip()
    actor = str(actor or "human").strip()
    target = dict(target or {})
    note = str(note or "")
    payload = dict(payload or {})

    try:
        if kind not in ACTION_KINDS:
            raise CockpitActionError(400, f"unsupported cockpit action: {kind}")
        if actor not in ACTORS:
            raise CockpitActionError(400, f"invalid actor: {actor}")

        if kind in OPEN_ACTION_KINDS:
            resolved = resolve_action_target(ws=ws, target=target)
            result = {"ok": True, "kind": kind, "effect": "resolved_open_ref", "resolved": resolved}
        elif kind in COMMENT_FEEDBACK_ACTIONS:
            target_type = _target_type(target)
            if target_type == "review_comment":
                result = {
                    "ok": True,
                    "kind": kind,
                    **_set_comment_feedback(
                        target=target,
                        status=COMMENT_FEEDBACK_ACTIONS[kind],
                        actor=actor,
                        note=note or str(payload.get("note") or ""),
                    ),
                }
            elif kind == "acknowledge_attention":
                resolved = resolve_action_target(ws=ws, target=target)
                result = {"ok": True, "kind": kind, "effect": "acknowledged", "resolved": resolved}
            else:
                raise CockpitActionError(400, f"{kind} requires a review_comment target")
        else:
            raise CockpitActionError(400, f"unsupported cockpit action: {kind}")
    except CockpitActionError as exc:
        exc.event = _record_action_event(
            ws=ws,
            kind=kind,
            target=target,
            actor=actor,
            note=note,
            status="failed",
            error=exc.message,
        )
        raise

    event = _record_action_event(
        ws=ws,
        kind=kind,
        target=target,
        actor=actor,
        note=note,
        status="succeeded",
        result=result,
    )
    return {**result, "event": event}


__all__ = [
    "ACTION_KINDS",
    "CockpitActionError",
    "execute_cockpit_action",
    "list_cockpit_action_events",
    "resolve_action_target",
]
