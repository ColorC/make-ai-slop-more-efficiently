"""Dashboard-local session placement overlay for the multiagent list.

ccdaemon may still only accept visible|hidden. Test probes therefore record
``background`` here so the session page can pin them to the back without a
8201 restart.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from omnicompany.core.config import omni_workspace_root

PLACEMENTS = ("visible", "hidden", "background")
_LOCK = threading.Lock()


def _path():
    return omni_workspace_root() / "data" / "boss_sight" / "session_placements.json"


def list_placements() -> dict[str, str]:
    path = _path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in data.items()
        if str(value) in PLACEMENTS
    }


def set_placement(session_id: str, visibility: str) -> dict[str, str]:
    if visibility not in PLACEMENTS:
        raise ValueError(f"visibility must be one of {PLACEMENTS}")
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id required")
    with _LOCK:
        current = list_placements()
        current[sid] = visibility
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"id": sid, "visibility": visibility}


def remember_background(session_id: str) -> dict[str, str]:
    return set_placement(session_id, "background")


def placements_payload() -> dict[str, Any]:
    rows = list_placements()
    return {"placements": rows, "count": len(rows)}
