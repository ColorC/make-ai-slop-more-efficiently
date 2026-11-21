"""Explicit human ownership of an existing runtime, without restarting it.

The identity ledger's session_origin is the current lifecycle owner consumed by
residents. Creation metadata and parent lineage are historical and stay intact.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from . import resolver


def current_owner(pty_id: str, native_id: str | None, creation_origin: str) -> str:
    """Read a cached exact-native ownership projection; never inherit a child's owner."""
    if not native_id:
        return creation_origin
    try:
        bindings = resolver.bindings_by_session_key()
        for key in (f"pty:{pty_id}", f"session:{native_id}"):
            binding = bindings.get(key, {})
            history = binding.get("ownership_history") or []
            if (binding.get("pty_id") == pty_id and binding.get("lifecycle_owner") == "human"
                    and history and history[-1].get("provider_session_id") == native_id):
                return "human"
    except (OSError, ValueError, TypeError):
        pass
    return creation_origin


def take_human_ownership(
    pty_id: str, *, expected_native: str, actor: str, reason: str,
    user_authorized: bool, live: dict[str, Any],
) -> dict[str, Any]:
    if not user_authorized or not actor.strip() or not reason.strip():
        raise ValueError("explicit user authorization, actor and reason are required")
    if not re.fullmatch(r"[0-9a-f]{16}", pty_id) or live.get("id") != pty_id:
        raise ValueError("expected one exact OmniCompany PTY")
    native = str(live.get("provider_session_id") or "")
    if not native or native != expected_native:
        raise ValueError("provider-native identity changed or is unknown")
    if not live.get("alive") or not live.get("process_alive", live.get("processAlive")):
        raise ValueError("a live provider process is required")
    if live.get("visibility") != "visible":
        raise ValueError("only an already-visible PTY can be taken over in place")
    with resolver._bindings_write_guard():
        store = resolver._read_bindings()
        keys = [key for key, row in store.items() if key == pty_id or
                str(row.get("pty_id") or "") == pty_id or
                str(row.get("session_id") or "") == native or
                key == f"{live.get('provider')}_{native}"]
        # A resumed conversation keeps its original trace_id as the store key while
        # ``pty_id`` moves to the new tab, so the canonical row is the one carrying
        # this PTY -- not necessarily one keyed by it.
        canonical = next((key for key in keys
                          if key == pty_id or str(store[key].get("pty_id") or "") == pty_id), "")
        if not canonical:
            raise ValueError("missing canonical PTY binding")
        already = all(store[key].get("lifecycle_owner") == "human" for key in keys)
        if not already:
            now = datetime.now(timezone.utc).isoformat()
            for key in keys:
                row = dict(store[key])
                previous = row.get("lifecycle_owner") or row.get("session_origin") or live.get("origin")
                row.setdefault("creation_origin", row.get("session_origin") or live.get("origin"))
                row.update(lifecycle_owner="human", session_origin="human", visibility="visible")
                history = list(row.get("ownership_history") or [])
                history.append({"at": now, "actor": actor, "reason": reason.strip(),
                                "from": previous, "to": "human", "provider_session_id": native})
                row["ownership_history"] = history
                row["updated_at"] = now
                store[key] = row
            resolver._write_bindings(store)
        return {"ok": True, "pty_id": pty_id, "provider_session_id": native,
                "lifecycle_owner": "human", "visibility": "visible",
                "creation_origin": store[canonical].get("creation_origin"),
                "changed": not already, "binding_keys": keys}
