# [OMNI] origin=codex created_by=codex intent="原生 Codex goal 元数据的精确只读查询"
# [OMNI] domain=ccdaemon type=module status=active tags=codex,capacity,goal
"""Read exact native Codex goal metadata without loading conversation content.

Lives in the ccdaemon layer because it resolves the account/provider HOME
through ``provider_identity``; a packages-layer module may not depend on the
dashboard runtime (arch_gate R1).

Provider-home resolution is shared with the native runtime. Missing databases,
unknown threads and schema drift are unknown, never "ordinary non-goal work".
The caller must still verify the live PTY/thread identity before any input.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import UUID

from .provider_identity import (
    CODEX_FAMILY_PROVIDER_IDS,
    canonical_cli_provider_id,
    codex_home_for,
)

_KNOWN_STATUSES = {"active", "paused", "blocked", "usage_limited", "budget_limited", "complete"}


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
    connection.execute("PRAGMA query_only=ON")
    return connection


def read_native_goal_objective(
    provider: str,
    native_session_id: str,
    *,
    provider_home: Path | None = None,
) -> dict[str, Any]:
    """Read the goal *text* of one exact thread — inheritance only.

    ``read_native_goal`` deliberately never selects a body column: the retry
    path must not depend on conversation content.  A migration is different —
    the successor has to receive the original objective verbatim — so the text
    is read here, separately, and only when a migration is already decided.
    A schema without the column is "unknown", never an empty objective.
    """
    result = read_native_goal(provider, native_session_id, provider_home=provider_home)
    out = dict(result) | {"objective": "", "objective_known": False}
    if not result.get("known") or not result.get("exists"):
        return out
    home = Path(result.get("provider_home") or (provider_home or ""))
    try:
        with closing(_readonly(home / "goals_1.sqlite")) as connection:
            row = connection.execute(
                "SELECT objective FROM thread_goals WHERE thread_id=? LIMIT 1",
                (str(native_session_id),),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return out
    if row is None or row[0] is None:
        return out
    return out | {"objective": str(row[0]), "objective_known": True}


def read_native_goal(
    provider: str,
    native_session_id: str,
    *,
    provider_home: Path | None = None,
) -> dict[str, Any]:
    """Return goal metadata only for a known exact thread in the selected home.

    ``provider_home`` may be supplied when a caller has independently verified
    the target runtime's explicit CODEX_HOME. Otherwise use the existing account
    registry. No cross-account search or fallback is performed.
    """
    result: dict[str, Any] = {"known": False, "exists": None, "status": "unknown",
                              "resume_command": None, "native_session_id": native_session_id}
    canonical = canonical_cli_provider_id(provider)
    if canonical not in CODEX_FAMILY_PROVIDER_IDS:
        return result | {"reason": "not-registered-codex-provider"}
    try:
        exact_id = str(UUID(native_session_id))
    except (ValueError, TypeError, AttributeError):
        return result | {"reason": "invalid-native-session-id"}
    if native_session_id != exact_id:
        return result | {"reason": "noncanonical-native-session-id"}
    home = Path(provider_home) if provider_home is not None else codex_home_for(canonical)
    result.update(provider=canonical, provider_home=str(home), source="codex-native-goals-sqlite")
    state_path, goals_path = home / "state_5.sqlite", home / "goals_1.sqlite"
    if not state_path.is_file() or not goals_path.is_file():
        return result | {"reason": "native-metadata-database-missing"}
    try:
        with closing(_readonly(state_path)) as connection:
            thread = connection.execute("SELECT id FROM threads WHERE id=? LIMIT 1", (exact_id,)).fetchone()
        if thread is None:
            return result | {"reason": "native-thread-not-in-provider-home"}
        with closing(_readonly(goals_path)) as connection:
            row = connection.execute(
                "SELECT goal_id,status,created_at_ms,updated_at_ms FROM thread_goals WHERE thread_id=? LIMIT 1",
                (exact_id,),
            ).fetchone()
    except (OSError, sqlite3.Error) as exc:
        return result | {"reason": "native-metadata-unreadable", "error_type": type(exc).__name__}
    if row is None:
        return result | {"known": True, "exists": False, "status": "none", "reason": "no-native-goal"}
    goal_id, status, created_at_ms, updated_at_ms = row
    if status not in _KNOWN_STATUSES:
        return result | {"exists": True, "reason": "unknown-native-goal-status"}
    return result | {"known": True, "exists": True, "status": status, "goal_id": goal_id,
                     "created_at_ms": created_at_ms, "updated_at_ms": updated_at_ms,
                     "resume_command": "/goal resume" if status == "active" else None,
                     "reason": "native-goal-active" if status == "active" else "native-goal-not-active"}
