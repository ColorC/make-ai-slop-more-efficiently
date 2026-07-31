"""Persistent BOSS SIGHT dual-control and observability state.

This store is intentionally small and JSON-backed. The data is user/workspace
state, not project contract state: permanent allow preferences live in
user_prefs.json and never mutate guard files.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root


ACTORS = {"human", "controller"}

CONTROL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "controller.auto_wake": {
        "label": "Controller auto wake",
        "description": "Allow controller wakeups from review/subagent events.",
        "default": True,
    },
    "reviewstage.push_to_user": {
        "label": "Review push to user",
        "description": "Allow review materials to be pushed into the user's attention surface.",
        "default": True,
    },
    "spawn.hard_block": {
        "label": "Hard guard block",
        "description": "Keep hard guard blocks active for unsafe subagent actions.",
        "default": True,
    },
    "observability.enabled": {
        "label": "Observability enabled",
        "description": "Allow BOSS SIGHT UI observations to be recorded.",
        "default": True,
    },
}

OBSERVABILITY_DIMENSIONS = {"click", "selection", "toggle_change", "view_dwell"}
MAX_EVENTS = 200
MAX_HISTORY = 80


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_id(prefix: str) -> str:
    return f"{prefix}_{time.time_ns()}"


def _compact(value: Any, limit: int = 500) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, dict):
        return {str(k)[:80]: _compact(v, limit=limit) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [_compact(v, limit=limit) for v in value[:40]]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:limit]


class ControlObservabilityStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (omni_workspace_root() / "data" / "boss_sight")
        self.state_path = self.root / "control_observability.json"
        self.user_prefs_path = self.root / "user_prefs.json"
        self._lock = threading.RLock()

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            state = self._load_state()
            self._save_state(state)
            return state

    def list_controls(self) -> dict[str, Any]:
        state = self.get_state()
        controls = state["controls"]
        return {
            "items": list(controls.values()),
            "by_key": controls,
            "count": len(controls),
        }

    def set_control(self, key: str, value: bool, *, actor: str, reason: str = "") -> dict[str, Any]:
        self._validate_actor(actor)
        if key not in CONTROL_DEFINITIONS:
            raise KeyError(key)
        with self._lock:
            state = self._load_state()
            control = state["controls"][key]
            previous = bool(control["value"])
            next_value = bool(value)
            entry = {
                "id": _event_id("ctrl"),
                "actor": actor,
                "updated_at": _now_iso(),
                "reason": reason[:500],
                "previous": previous,
                "next": next_value,
            }
            control["value"] = next_value
            control["updated_by"] = actor
            control["updated_at"] = entry["updated_at"]
            control["reason"] = reason[:500]
            control.setdefault("history", []).append(entry)
            control["history"] = control["history"][-MAX_HISTORY:]
            self._save_state(state)
            return control

    def observability_settings(self) -> dict[str, Any]:
        state = self.get_state()
        obs = state["observability"]
        return {
            "dimensions": dict(obs["dimensions"]),
            "updated_by": obs.get("updated_by", "system"),
            "updated_at": obs.get("updated_at"),
            "reason": obs.get("reason", "default"),
            "history": list(obs.get("history", [])),
        }

    def set_observability_settings(
        self,
        dimensions: dict[str, bool],
        *,
        actor: str,
        reason: str = "",
    ) -> dict[str, Any]:
        self._validate_actor(actor)
        unknown = sorted(set(dimensions) - OBSERVABILITY_DIMENSIONS)
        if unknown:
            raise KeyError(",".join(unknown))
        with self._lock:
            state = self._load_state()
            obs = state["observability"]
            previous = dict(obs["dimensions"])
            next_dimensions = dict(previous)
            for key, value in dimensions.items():
                next_dimensions[key] = bool(value)
            entry = {
                "id": _event_id("obs_settings"),
                "actor": actor,
                "updated_at": _now_iso(),
                "reason": reason[:500],
                "previous": previous,
                "next": next_dimensions,
            }
            obs["dimensions"] = next_dimensions
            obs["updated_by"] = actor
            obs["updated_at"] = entry["updated_at"]
            obs["reason"] = reason[:500]
            obs.setdefault("history", []).append(entry)
            obs["history"] = obs["history"][-MAX_HISTORY:]
            self._save_state(state)
            return self.observability_settings()

    def record_observation(
        self,
        *,
        dimension: str,
        surface: str = "",
        target: str | None = None,
        value: Any = None,
        meta: dict[str, Any] | None = None,
        actor: str = "human",
    ) -> dict[str, Any]:
        self._validate_actor(actor)
        if dimension not in OBSERVABILITY_DIMENSIONS:
            raise KeyError(dimension)
        with self._lock:
            state = self._load_state()
            if not state["controls"]["observability.enabled"]["value"]:
                return {"recorded": False, "skipped": True, "reason": "observability_disabled"}
            if not state["observability"]["dimensions"].get(dimension, True):
                return {"recorded": False, "skipped": True, "reason": "dimension_disabled"}
            event = {
                "id": _event_id("obs"),
                "dimension": dimension,
                "surface": str(surface or "")[:160],
                "target": str(target)[:300] if target is not None else None,
                "value": _compact(value),
                "meta": _compact(meta or {}),
                "actor": actor,
                "recorded_at": _now_iso(),
            }
            state.setdefault("events", []).append(event)
            state["events"] = state["events"][-MAX_EVENTS:]
            self._save_state(state)
            return {"recorded": True, "skipped": False, "event": event}

    def recent_observations(self, limit: int = 20) -> list[dict[str, Any]]:
        state = self.get_state()
        limit = max(1, min(int(limit), 100))
        return list(reversed(state.get("events", [])[-limit:]))

    def get_user_prefs(self) -> dict[str, Any]:
        with self._lock:
            prefs = self._load_user_prefs()
            self._save_user_prefs(prefs)
            return prefs

    def add_permanent_allow(
        self,
        *,
        scope: str,
        tool: str,
        pattern: str = "",
        reason: str = "",
        actor: str = "human",
    ) -> dict[str, Any]:
        if actor != "human":
            raise ValueError("permanent allow can only be recorded by human actor")
        if not tool.strip():
            raise ValueError("tool is required")
        with self._lock:
            prefs = self._load_user_prefs()
            entry = {
                "id": _event_id("allow"),
                "scope": scope[:160],
                "tool": tool[:160],
                "pattern": pattern[:500],
                "reason": reason[:500],
                "actor": actor,
                "created_at": _now_iso(),
            }
            prefs.setdefault("permanent_allow", []).append(entry)
            prefs["permanent_allow"] = prefs["permanent_allow"][-MAX_HISTORY:]
            self._save_user_prefs(prefs)
            return entry

    def summary(self, *, recent_limit: int = 10) -> dict[str, Any]:
        return {
            "controls": self.list_controls(),
            "observability": {
                "settings": self.observability_settings(),
                "recent": self.recent_observations(recent_limit),
            },
        }

    def _default_state(self) -> dict[str, Any]:
        now = _now_iso()
        controls = {}
        for key, spec in CONTROL_DEFINITIONS.items():
            controls[key] = {
                "key": key,
                "label": spec["label"],
                "description": spec["description"],
                "value": bool(spec["default"]),
                "updated_by": "system",
                "updated_at": now,
                "reason": "default",
                "history": [],
            }
        return {
            "version": 1,
            "controls": controls,
            "observability": {
                "dimensions": {dimension: True for dimension in sorted(OBSERVABILITY_DIMENSIONS)},
                "updated_by": "system",
                "updated_at": now,
                "reason": "default",
                "history": [],
            },
            "events": [],
        }

    def _load_state(self) -> dict[str, Any]:
        state = self._default_state()
        raw = self._read_json(self.state_path)
        if isinstance(raw, dict):
            controls = raw.get("controls")
            if isinstance(controls, dict):
                for key in CONTROL_DEFINITIONS:
                    if isinstance(controls.get(key), dict):
                        state["controls"][key].update({
                            k: controls[key].get(k)
                            for k in ("value", "updated_by", "updated_at", "reason", "history")
                            if k in controls[key]
                        })
                        state["controls"][key]["value"] = bool(state["controls"][key]["value"])
                        if not isinstance(state["controls"][key].get("history"), list):
                            state["controls"][key]["history"] = []
            obs = raw.get("observability")
            if isinstance(obs, dict):
                dimensions = obs.get("dimensions")
                if isinstance(dimensions, dict):
                    for dimension in OBSERVABILITY_DIMENSIONS:
                        if dimension in dimensions:
                            state["observability"]["dimensions"][dimension] = bool(dimensions[dimension])
                for k in ("updated_by", "updated_at", "reason", "history"):
                    if k in obs:
                        state["observability"][k] = obs[k]
                if not isinstance(state["observability"].get("history"), list):
                    state["observability"]["history"] = []
            events = raw.get("events")
            if isinstance(events, list):
                state["events"] = [e for e in events[-MAX_EVENTS:] if isinstance(e, dict)]
        return state

    def _load_user_prefs(self) -> dict[str, Any]:
        prefs = {"version": 1, "permanent_allow": []}
        raw = self._read_json(self.user_prefs_path)
        if isinstance(raw, dict):
            if isinstance(raw.get("permanent_allow"), list):
                prefs["permanent_allow"] = [e for e in raw["permanent_allow"] if isinstance(e, dict)][-MAX_HISTORY:]
            for key, value in raw.items():
                if key not in prefs:
                    prefs[key] = value
        return prefs

    def _read_json(self, path: Path) -> Any:
        try:
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def _save_state(self, state: dict[str, Any]) -> None:
        self._write_json(self.state_path, state)

    def _save_user_prefs(self, prefs: dict[str, Any]) -> None:
        self._write_json(self.user_prefs_path, prefs)

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _validate_actor(self, actor: str) -> None:
        if actor not in ACTORS:
            raise ValueError(f"invalid actor: {actor}")


_singleton: ControlObservabilityStore | None = None
_singleton_lock = threading.Lock()


def get_control_observability_store() -> ControlObservabilityStore:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = ControlObservabilityStore()
        return _singleton


__all__ = [
    "ACTORS",
    "CONTROL_DEFINITIONS",
    "OBSERVABILITY_DIMENSIONS",
    "ControlObservabilityStore",
    "get_control_observability_store",
]
