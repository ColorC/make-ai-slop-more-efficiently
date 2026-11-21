"""Pull a bound parent's explicit action at an existing tool boundary.

This is hook context, never a user prompt or a new provider turn. Root sessions,
forks, unrelated agents, and routine messages keep their quiet inbox behavior.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from omnicompany.core.config import omni_workspace_root
from omnicompany.core.quiet_coordination import QuietCoordinationStore
from omnicompany.packages.services._core.identity import get_session_binding


def _native(value: str) -> str:
    for prefix in ("codex_", "codex:"):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def live_root_identity(pty_id: str) -> dict:
    """Use live primary process handles, not the fork-polluted registry."""
    import psutil

    if not pty_id or any(c not in "0123456789abcdef" for c in pty_id):
        return {}
    state = json.loads((omni_workspace_root() / "data" / "pty_hosts" / f"{pty_id}.json").read_text(encoding="utf-8"))
    if state.get("id") != pty_id or not state.get("alive") or state.get("provider") != "codex":
        return {}
    pending = [psutil.Process(int(state["child_pid"]))]
    while pending:
        proc = pending.pop(0)
        if proc.name().lower() != "codex.exe":
            pending.extend(proc.children())
            continue
        identities = []
        for item in proc.open_files():
            path = Path(item.path)
            if not path.name.startswith("rollout-") or path.suffix != ".jsonl":
                continue
            with path.open(encoding="utf-8") as stream:
                meta = json.loads(stream.readline()).get("payload", {})
            if meta.get("source") == "cli" and meta.get("originator") == "codex-tui" and not meta.get("forked_from_id"):
                identities.append(str(meta.get("id") or ""))
        if len(set(identities)) == 1 and identities[0]:
            return {"native": identities[0], "generation": f"{proc.pid}:{proc.create_time()}"}
        return {}  # Ambiguous primary process: fail closed, never choose a fork.
    return {}


def pull_parent_actions(payload: dict, *, pty_id: str | None = None, store: QuietCoordinationStore | None = None) -> str:
    pty = pty_id or os.environ.get("OMNI_CC_PTY_ID", "")
    recipient = _native(str(payload.get("session_id") or payload.get("sessionId") or ""))
    if not pty or not recipient:
        return ""
    binding = get_session_binding(pty)
    parent = str(binding.get("parent_agent_id") or "")
    if (not parent or parent == pty or binding.get("worker_kind") != "external_agent_worker"
            or binding.get("session_origin") != "agent"):
        return ""
    inbox = store or QuietCoordinationStore()
    # No process scan when there are no explicit actions waiting.
    messages = [row for row in inbox.peek(recipient, limit=500) if row.get("actor_type") == "agent"
                and "\nDelegation intent: action\n" in str(row.get("payload", {}).get("raw_message", ""))]
    if not messages:
        return ""
    child_identity = live_root_identity(pty)
    if child_identity.get("native") != recipient:
        return ""
    parent_identity = live_root_identity(parent)
    if not parent_identity.get("native"):
        return ""
    blocks = []
    for message in messages:
        if len(blocks) >= 3:
            break
        if (_native(str(message.get("source_session_id") or "")) != parent_identity["native"]
                or message.get("transport_target_id") != pty
                or (message.get("target_project") and message["target_project"] != binding.get("project"))
                or message.get("run_id")):
            continue
        # CAS prevents user/project hook duplication at the same tool boundary.
        with inbox._connect() as conn:
            cursor = conn.execute(
                "UPDATE coordination_messages SET status='acked', acked_at=?, acked_by_session_id=?, acked_by_run_id=? "
                "WHERE message_id=? AND target_session_id=? AND status='pending'",
                (datetime.now(timezone.utc).isoformat(), recipient, child_identity["generation"], message["message_id"], recipient),
            )
            conn.commit()
            if cursor.rowcount != 1:
                continue
        request = str(message.get("payload", {}).get("request") or "")
        request = request.split("\n非必要不回复", 1)[0]
        blocks.append(f"[OMNI PARENT ACTION · {message['message_id']}]\n"
                      f"Source: assistant delegation from bound parent {parent}; actor_type=agent, not human.\n"
                      "At this existing tool boundary, incorporate this necessary task correction into the current work. "
                      "Keep the original human goal and all unfinished requirements; this is not a replacement user request.\n"
                      f"Request: {request}")
    return "\n\n".join(blocks)


def claim_idle_parent_action(message: dict, raw_message: str, *, store: QuietCoordinationStore | None = None) -> bool:
    """Admit only an explicit action to its bound, idle, live primary worker.

    Called inside the existing quiet middleware, before the provider route.
    The exact original agent envelope is preserved and never enters goal capture.
    """
    body = message.get("payload") or {}
    if (message.get("status") != "pending" or message.get("actor_type") != "agent"
            or body.get("raw_message") != raw_message
            or "\nDelegation intent: action\n" not in raw_message or message.get("run_id")):
        return False
    pty = str(message.get("transport_target_id") or "")
    binding = get_session_binding(pty)
    parent = str(binding.get("parent_agent_id") or "")
    if (not parent or parent == pty or binding.get("worker_kind") != "external_agent_worker"
            or binding.get("session_origin") != "agent"):
        return False
    child = live_root_identity(pty)
    parent_identity = live_root_identity(parent)
    if (child.get("native") != _native(str(message.get("target_session_id") or ""))
            or not parent_identity.get("native")
            or parent_identity["native"] != _native(str(message.get("source_session_id") or ""))
            or (message.get("target_project") and message["target_project"] != binding.get("project"))):
        return False
    state = json.loads((omni_workspace_root() / "data" / "pty_hosts" / f"{pty}.json").read_text(encoding="utf-8"))
    if (not state.get("alive") or state.get("provider_runtime_phase") != "idle"
            or state.get("provider_runtime_source") != "codex:title-idle"
            or not 0 <= time.time() - float(state.get("updated_at") or 0) < 10):
        return False
    from omnicompany.dashboard.ccdaemon.pty import get_manager

    session = get_manager().get(pty)
    if session is None or session.closed or session.pending_input_len or session.pending_title_input:
        return False
    if session.provider_runtime.working(now=time.time()):
        return False
    inbox = store or QuietCoordinationStore()
    with inbox._connect() as conn:
        cursor = conn.execute(
            "UPDATE coordination_messages SET status='delivery_reserved', acked_by_session_id=?, acked_by_run_id=? "
            "WHERE message_id=? AND target_session_id=? AND status='pending'",
            (message["target_session_id"], child["generation"],
             message["message_id"], message["target_session_id"]),
        )
        conn.commit()
        return cursor.rowcount == 1


def record_parent_action_transport(message: dict, *, submission: str = "unconfirmed") -> None:
    """A transport receipt is not proof that the provider consumed the action.

    Ambiguous attempts remain reserved and cannot automatically write twice.
    Only the provider's UserPromptSubmit hook may mark them acknowledged.
    """
    inbox = QuietCoordinationStore()
    with inbox._connect() as conn:
        conn.execute(
            "UPDATE coordination_messages SET status=? WHERE message_id=? AND status='delivery_reserved'",
            ("delivery_queued" if submission == "queued_behind_turn" else "delivery_unconfirmed",
             message["message_id"]),
        )
        conn.commit()


def acknowledge_parent_action_prompt(payload: dict, raw_message: str, *, pty_id: str | None = None,
                                    store: QuietCoordinationStore | None = None) -> bool:
    """Acknowledge the exact action observed by the original provider hook."""
    from omnicompany.core.quiet_coordination import parse_agent_envelope

    try:
        parsed = parse_agent_envelope(raw_message)
    except ValueError:
        return False
    if parsed is None:
        return False
    inbox = store or QuietCoordinationStore()
    with inbox._connect() as conn:
        message = inbox._find(conn, message_id=parsed.message_id)
    if not message or message.get("status") not in {"delivery_reserved", "delivery_queued", "delivery_unconfirmed"}:
        return False
    pty = pty_id or os.environ.get("OMNI_CC_PTY_ID", "")
    recipient = _native(str(payload.get("session_id") or payload.get("sessionId") or ""))
    if (not pty or message.get("transport_target_id") != pty
            or recipient != message.get("target_session_id")
            or message.get("payload", {}).get("raw_message") != raw_message):
        return False
    identity = live_root_identity(pty)
    if identity.get("native") != recipient or identity.get("generation") != message.get("acked_by_run_id"):
        return False
    with inbox._connect() as conn:
        cursor = conn.execute(
            "UPDATE coordination_messages SET status='acked', acked_at=? WHERE message_id=? "
            "AND status IN ('delivery_reserved','delivery_queued','delivery_unconfirmed')",
            (datetime.now(timezone.utc).isoformat(), message["message_id"]),
        )
        conn.commit()
        return cursor.rowcount == 1
