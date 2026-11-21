# [OMNI] origin=codex created_by=codex intent="原生 Codex 容量失败与后续活动的只读取证"
# [OMNI] domain=core type=module status=active tags=codex,capacity,evidence
"""Read native capacity failure provenance and later activity, without transcript text."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

CAPACITY_SUFFIX = "Turn error: Selected model is at capacity. Please try a different model."


@lru_cache(maxsize=128)
def _later_activity(path: str, size: int, mtime_ns: int, failed_at: float) -> str | None:
    # Size and mtime are cache identity, not evidence of provider progress.
    del size, mtime_ns
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
                stamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).timestamp()
            except (ValueError, KeyError, TypeError):
                continue
            if stamp <= failed_at:
                continue
            payload = event.get("payload") or {}
            if event.get("type") == "response_item" and payload.get("type") == "message":
                if payload.get("role") == "user":
                    return "later-input"
                if payload.get("role") == "assistant":
                    return "later-provider-progress"
            if event.get("type") != "event_msg":
                continue
            kind = payload.get("type")
            if kind in {"turn_aborted", "task_started", "user_message", "thread_settings_applied"}:
                return "later-input-or-turn-change"
            if kind in {"item_completed", "agent_message", "agent_reasoning", "tool_use", "tool_result"}:
                return "later-provider-progress"
            if kind == "thread_goal_updated":
                status = (payload.get("goal") or {}).get("status")
                if status != "blocked" or stamp - failed_at > 2:
                    return "later-goal-change"
            # task_complete and token_count are emitted during failed-turn cleanup.
    return None


_PROGRESS_EVENTS = frozenset({"item_completed", "agent_message", "agent_reasoning", "tool_use", "tool_result"})


@lru_cache(maxsize=128)
def _last_progress_at(path: str, size: int, mtime_ns: int, since: float) -> float:
    """Newest provider-owned progress timestamp after ``since`` (0.0 if none)."""
    del size, mtime_ns
    latest = 0.0
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
                stamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).timestamp()
            except (ValueError, KeyError, TypeError):
                continue
            if stamp <= since:
                continue
            payload = event.get("payload") or {}
            if event.get("type") == "response_item" and payload.get("type") == "message":
                if payload.get("role") == "assistant":
                    latest = max(latest, stamp)
                continue
            if event.get("type") == "event_msg" and payload.get("type") in _PROGRESS_EVENTS:
                latest = max(latest, stamp)
    return latest


def resolve_rollout(provider_home: Path, native_session_id: str) -> Path | None:
    """Exact, non-archived rollout file inside the verified provider HOME."""
    try:
        if str(UUID(native_session_id)) != native_session_id.lower():
            return None
        home = Path(provider_home)
        with sqlite3.connect((home / "state_5.sqlite").as_uri() + "?mode=ro", uri=True, timeout=.5) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute("SELECT rollout_path,archived FROM threads WHERE id=?", (native_session_id,)).fetchone()
        if not row or row[1]:
            return None
        rollout = Path(row[0]).resolve()
        return rollout if rollout.is_relative_to((home / "sessions").resolve()) and rollout.is_file() else None
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None


def read_last_provider_progress(
    provider_home: Path,
    native_session_id: str,
    *,
    since: float = 0.0,
) -> tuple[float, bool]:
    """Return ``(last_progress_at, known)`` for the exact native thread.

    ``known`` is False when the rollout cannot be resolved or read.  An unknown
    silence is never evidence of a stalled session.
    """
    rollout = resolve_rollout(provider_home, native_session_id)
    if rollout is None:
        return 0.0, False
    try:
        stat = rollout.stat()
        return _last_progress_at(str(rollout), stat.st_size, stat.st_mtime_ns, float(since)), True
    except (OSError, ValueError):
        return 0.0, False


def read_capacity_failure(
    provider_home: Path,
    native_session_id: str,
    *,
    generation_started_at: float,
    last_input_at: float = 0,
    goal_updated_at: float = 0,
    process_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Return only a current, unresolved, provider-owned failure.

    The caller must have verified the live process and its exact provider HOME.
    Missing metadata fails closed. No user/assistant content is returned or used
    for capacity matching; that match is exclusively the native turn error log.
    """
    try:
        if str(UUID(native_session_id)) != native_session_id.lower():
            return None
        home = Path(provider_home)
        with sqlite3.connect((home / "logs_2.sqlite").as_uri() + "?mode=ro", uri=True, timeout=.5) as db:
            db.execute("PRAGMA query_only=ON")
            params = (native_session_id, int(generation_started_at), "codex_core::session::turn", len(CAPACITY_SUFFIX), CAPACITY_SUFFIX)
            where = "thread_id=? AND ts>=? AND target=? AND substr(feedback_log_body,-?)=?"
            if process_uuid:
                where += " AND process_uuid=?"
                params = (*params, process_uuid)
            row = db.execute(
                "SELECT id,ts,ts_nanos FROM logs WHERE " + where + " ORDER BY ts DESC,ts_nanos DESC,id DESC LIMIT 1",
                params,
            ).fetchone()
            if row is None:
                return None
            failed_at = float(row[1]) + float(row[2] or 0) / 1_000_000_000
            if failed_at < generation_started_at or last_input_at > failed_at:
                return None
            # A goal stays blocked across subsequent capacity failures. Match
            # its transition against ANY owned failure, not just the last one.
            blocked_by_capacity = bool(goal_updated_at and db.execute(
                "SELECT 1 FROM logs WHERE " + where
                + " AND abs((ts + ts_nanos/1000000000.0)-?) <= 2 LIMIT 1",
                (*params, goal_updated_at),
            ).fetchone())
        with sqlite3.connect((home / "state_5.sqlite").as_uri() + "?mode=ro", uri=True, timeout=.5) as db:
            db.execute("PRAGMA query_only=ON")
            thread = db.execute("SELECT rollout_path,archived FROM threads WHERE id=?", (native_session_id,)).fetchone()
        if not thread or thread[1]:
            return None
        rollout = Path(thread[0]).resolve()
        if not rollout.is_relative_to((home / "sessions").resolve()):
            return None
        stat = rollout.stat()
        if _later_activity(str(rollout), stat.st_size, stat.st_mtime_ns, failed_at):
            return None
        return {"log_id": int(row[0]), "failed_at": failed_at, "capacity_blocked": blocked_by_capacity}
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None
