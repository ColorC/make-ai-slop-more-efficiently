"""Session-window canvas pointers, plan/todo reuse, and favorites.

A companion window is bound to a session. The canvas currently shown in that
window is a pointer that follows the session, so history/favorites can switch
canvases without creating a new session. Sessions that share a plan or todo
reuse the same canvas by default until a session explicitly opens another one.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

INDEX_NAME = "canvas-session-index.json"
INDEX_VERSION = 1

_LOCK = threading.RLock()


def empty_index() -> dict[str, Any]:
    return {
        "version": INDEX_VERSION,
        "favorites": [],
        "sessions": {},
        "plans": {},
        "todos": {},
    }


def index_path(root: Path) -> Path:
    return Path(root) / INDEX_NAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def load_index(root: Path) -> dict[str, Any]:
    path = index_path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_index()
    except (OSError, json.JSONDecodeError):
        return empty_index()
    if not isinstance(raw, dict):
        return empty_index()
    index = empty_index()
    favorites = raw.get("favorites")
    if isinstance(favorites, list):
        index["favorites"] = [str(item) for item in favorites if isinstance(item, str) and item]
    for bucket in ("sessions", "plans", "todos"):
        value = raw.get(bucket)
        if isinstance(value, dict):
            index[bucket] = deepcopy(value)
    return index


def save_index(root: Path, index: Mapping[str, Any]) -> None:
    payload = empty_index()
    payload.update(deepcopy(dict(index)))
    payload["version"] = INDEX_VERSION
    _atomic_write(
        index_path(root),
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
    )


def bare_session_id(session_id: str) -> str:
    value = str(session_id or "").strip()
    if value.startswith("external:"):
        return value[len("external:"):]
    if value.startswith("session-"):
        try:
            encoded = value[len("session-"):]
            encoded += "=" * (-len(encoded) % 4)
            raw = base64.urlsafe_b64decode(encoded).decode("utf-8")
            return raw[len("external:"):] if raw.startswith("external:") else raw
        except Exception:
            return value
    return value


def canvas_id_for_session(session_id: str) -> str:
    value = str(session_id or "").strip()
    if value.startswith("session-"):
        return value
    raw = value if value.startswith(("external:", "canvas:", "whiteboard:")) else f"external:{value}"
    encoded = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return f"session-{encoded[:180]}"


def _session_entry(canvas_id: str, *, now: str) -> dict[str, str]:
    return {"canvas_id": canvas_id, "opened_at": now}


def resolve_session_canvas(
    index: Mapping[str, Any],
    *,
    session_id: str,
    plan_id: str = "",
    todo_id: str = "",
    now: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Choose the canvas a session window should open.

    Priority: last opened canvas for this session, then the todo's canvas,
    then the plan's canvas, then this session's default canvas. The first
    canvas used by a plan/todo is recorded so later sessions reuse it.
    """
    next_index = empty_index()
    next_index.update(deepcopy(dict(index)))
    pty = bare_session_id(session_id)
    timestamp = now or _utc_now()
    sessions = next_index["sessions"]
    plans = next_index["plans"]
    todos = next_index["todos"]
    default_id = canvas_id_for_session(pty)
    current = sessions.get(pty) if isinstance(sessions.get(pty), dict) else None
    canvas_id = ""
    reason = "default"
    if current and str(current.get("canvas_id") or "").strip():
        canvas_id = str(current["canvas_id"])
        reason = "session"
    elif todo_id and str(todos.get(todo_id) or "").strip():
        canvas_id = str(todos[todo_id])
        reason = "todo"
    elif plan_id and str(plans.get(plan_id) or "").strip():
        canvas_id = str(plans[plan_id])
        reason = "plan"
    else:
        canvas_id = default_id
        reason = "default"
    sessions[pty] = _session_entry(canvas_id, now=timestamp)
    if todo_id and not str(todos.get(todo_id) or "").strip():
        todos[todo_id] = canvas_id
    if plan_id and not str(plans.get(plan_id) or "").strip():
        plans[plan_id] = canvas_id
    return (
        {
            "canvas_id": canvas_id,
            "session_id": pty,
            "reason": reason,
            "plan_id": plan_id or "",
            "todo_id": todo_id or "",
        },
        next_index,
    )


def open_session_canvas(
    index: Mapping[str, Any],
    *,
    session_id: str,
    canvas_id: str,
    plan_id: str = "",
    todo_id: str = "",
    now: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Point this session window at an explicit canvas and remember it."""
    next_index = empty_index()
    next_index.update(deepcopy(dict(index)))
    pty = bare_session_id(session_id)
    target = str(canvas_id or "").strip() or canvas_id_for_session(pty)
    timestamp = now or _utc_now()
    next_index["sessions"][pty] = _session_entry(target, now=timestamp)
    if todo_id and not str(next_index["todos"].get(todo_id) or "").strip():
        next_index["todos"][todo_id] = target
    if plan_id and not str(next_index["plans"].get(plan_id) or "").strip():
        next_index["plans"][plan_id] = target
    return (
        {
            "canvas_id": target,
            "session_id": pty,
            "reason": "session",
            "plan_id": plan_id or "",
            "todo_id": todo_id or "",
        },
        next_index,
    )


def toggle_favorite(index: Mapping[str, Any], canvas_id: str) -> dict[str, Any]:
    next_index = empty_index()
    next_index.update(deepcopy(dict(index)))
    target = str(canvas_id or "").strip()
    if not target:
        raise ValueError("canvas_id is required")
    favorites = [str(item) for item in next_index.get("favorites") or [] if item]
    if target in favorites:
        favorites = [item for item in favorites if item != target]
        favorite = False
    else:
        favorites = [target, *[item for item in favorites if item != target]]
        favorite = True
    next_index["favorites"] = favorites
    return {"canvas_id": target, "favorite": favorite, "index": next_index}


def with_index_lock(root: Path, mutator) -> Any:
    """Load-modify-save under a process-local lock."""
    with _LOCK:
        index = load_index(root)
        result, next_index = mutator(index)
        save_index(root, next_index)
        return result
