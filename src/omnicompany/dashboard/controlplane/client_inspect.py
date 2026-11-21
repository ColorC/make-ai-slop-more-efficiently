"""Loopback inspect hub for Dashboard pages and the desktop host.

Pages poll this router and execute screenshot / eval / input commands so an
agent on this machine can see the user's actual view and operate the web UI.
Command issuance is local-only. Page hello/poll/result stay open because the
desktop WebView talks to the LAN origin, not 127.0.0.1.
"""
from __future__ import annotations

import threading
import time
import uuid
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

client_inspect_router = APIRouter(
    prefix="/api/clients/desktop/inspect", tags=["client-inspect"]
)

SCHEMA_VERSION = 1
CLIENT_TTL_MS = 45_000
COMMAND_TTL_MS = 30_000
MAX_PENDING = 16
# A full WebTwin 1080x1920 PNG commonly exceeds 1.5 MB. Clipping the data URL
# made the inspect command report success while returning an undecodable image.
# Keep a bounded but lossless envelope; the page-side command timeout still
# protects the queue from a broken client.
MAX_RESULT_CHARS = 12_000_000
CONNECTION_ID_MAX = 128

_LOCK = threading.RLock()
_clients: dict[str, dict[str, Any]] = {}
_commands: dict[str, dict[str, Any]] = {}
_TRUSTED_FORWARDERS = frozenset(
    item.strip()
    for item in os.environ.get("OMNI_INSPECT_TRUSTED_FORWARDERS", "").split(",")
    if item.strip()
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _local_only(request: Request) -> JSONResponse | None:
    if _is_local(request):
        return None
    return JSONResponse({"ok": False, "error": "local_only"}, status_code=403)


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… clipped, {len(text) - limit} more characters"


def _peer_ip(request: Request) -> str | None:
    value = str(request.client.host if request.client else "").strip()
    return value or None


def _trusted_forwarded_ip(request: Request) -> str | None:
    peer = _peer_ip(request)
    if not peer or peer not in _TRUSTED_FORWARDERS:
        return None
    forwarded = str(request.headers.get("x-forwarded-for") or "").strip()
    return next((part.strip() for part in forwarded.split(",") if part.strip()), None)


def _authenticated_node_for_install_id(install_id: Any) -> dict[str, Any] | None:
    """Match a desktop self-report to a node that recently signed a poll.

    The browser-provided id is only an association hint.  It becomes an
    authenticated-node observation only when the controller already has a
    non-revoked node with the same persistent id and a fresh signed poll.  No
    IP or forwarded header is promoted to device identity, and a stale node is
    deliberately not treated as connected.
    """
    value = str(install_id or "").strip()
    if not value:
        return None
    try:
        from . import desktop_nodes_core

        state = desktop_nodes_core.load_state()
        node = (state.get("nodes") or {}).get(value)
        if not isinstance(node, dict) or node.get("revoked"):
            return None
        last_seen = int(node.get("last_seen_ms") or 0)
        if not last_seen or _now_ms() - last_seen > 60_000:
            return None
        return {
            "node_id": value,
            "node_fingerprint": node.get("node_fingerprint"),
            "last_seen_ms": last_seen,
        }
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _purge_locked(now: int) -> None:
    stale_clients = [
        client_id
        for client_id, client in _clients.items()
        if now - int(client.get("lastSeenAtMs") or 0) > CLIENT_TTL_MS
    ]
    for client_id in stale_clients:
        _clients.pop(client_id, None)
    stale_commands = [
        command_id
        for command_id, command in _commands.items()
        if now - int(command.get("createdAtMs") or 0) > COMMAND_TTL_MS
    ]
    for command_id in stale_commands:
        _commands.pop(command_id, None)


def reset_for_tests() -> None:
    with _LOCK:
        _clients.clear()
        _commands.clear()


def enqueue_command(
    kind: str,
    payload: dict[str, Any] | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    now = _now_ms()
    with _LOCK:
        _purge_locked(now)
        pending = [item for item in _commands.values() if item.get("status") == "pending"]
        if len(pending) >= MAX_PENDING:
            raise RuntimeError("too_many_pending")
        target = (client_id or "").strip()
        if target:
            if target not in _clients:
                raise RuntimeError("client_not_connected")
        else:
            newest = max(
                _clients.values(),
                key=lambda item: int(item.get("lastSeenAtMs") or 0),
                default=None,
            )
            target = str((newest or {}).get("id") or "")
        if not target:
            raise RuntimeError("no_viewer")
        command_id = uuid.uuid4().hex
        command = {
            "id": command_id,
            "clientId": target,
            "kind": kind,
            "payload": payload or {},
            "status": "pending",
            "createdAtMs": now,
            "result": None,
            "error": None,
        }
        _commands[command_id] = command
        return dict(command)


def _requeue_inflight_for_reconnect_locked(client_id: str, now: int) -> None:
    """Return commands abandoned by a page reload to the fresh page poll.

    The inspect client id is intentionally persistent so observations can be
    joined across reloads.  A per-page connection id lets us distinguish a
    reload from the normal periodic hello heartbeat without re-delivering a
    slow command to the same page.
    """
    for command in _commands.values():
        if command.get("clientId") != client_id or command.get("status") != "in_flight":
            continue
        command["status"] = "pending"
        command.pop("deliveredAtMs", None)
        command["requeuedAtMs"] = now
        command["requeueReason"] = "client_reconnected_before_result"
        command["error"] = None


def take_pending(client_id: str) -> list[dict[str, Any]]:
    now = _now_ms()
    with _LOCK:
        _purge_locked(now)
        waiting = [
            command
            for command in _commands.values()
            if command.get("clientId") == client_id and command.get("status") == "pending"
        ]
        # Deliver one command at a time and mark it in-flight. A page command
        # is allowed to await a bounded screenshot/eval, but a hung command
        # must never be returned on every poll or share a response with later
        # commands and block the entire inspect queue.
        if not waiting:
            return []
        command = min(waiting, key=lambda item: int(item.get("createdAtMs") or 0))
        command["status"] = "in_flight"
        command["deliveredAtMs"] = now
        return [dict(command)]


def complete_command(command_id: str, ok: bool, result: Any, error: str | None) -> dict[str, Any] | None:
    now = _now_ms()
    with _LOCK:
        command = _commands.get(command_id)
        if command is None:
            return None
        command["status"] = "ok" if ok else "error"
        command["completedAtMs"] = now
        if isinstance(result, str):
            command["result"] = _clip(result, MAX_RESULT_CHARS)
        else:
            command["result"] = result
        command["error"] = _clip(error or "", 4_096) or None
        if command.get("kind") in {"click", "type", "key"}:
            client = _clients.get(str(command.get("clientId") or ""))
            if client is not None:
                client["lastInputAtMs"] = now
        return dict(command)


def get_command(command_id: str) -> dict[str, Any] | None:
    with _LOCK:
        command = _commands.get(command_id)
        return dict(command) if command is not None else None


def list_clients() -> list[dict[str, Any]]:
    now = _now_ms()
    with _LOCK:
        _purge_locked(now)
        return [dict(item) for item in _clients.values()]


@client_inspect_router.post("/hello")
def inspect_hello(request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = body or {}
    client_id = str(payload.get("id") or "").strip()
    if not client_id or len(client_id) > 64:
        return {"ok": False, "error": "invalid_id"}
    now = _now_ms()
    peer_ip = _peer_ip(request)
    forwarded_for = _clip(request.headers.get("x-forwarded-for"), 512) or None
    reported_hostname = _clip(payload.get("reportedHostname"), 256) or None
    reported_install_id = _clip(payload.get("reportedInstallId"), 256) or None
    connection_id = _clip(payload.get("connectionId"), CONNECTION_ID_MAX) or None
    associated_node = _authenticated_node_for_install_id(reported_install_id)
    if associated_node:
        identity_source = "authenticated-desktop-node"
    elif reported_hostname or reported_install_id:
        identity_source = "desktop-self-report-unassociated"
    else:
        identity_source = "browser-unknown"
    record = {
        "id": client_id,
        "href": _clip(payload.get("href"), 512),
        "title": _clip(payload.get("title"), 256),
        "userAgent": _clip(payload.get("userAgent"), 256),
        "hostRuntime": _clip(payload.get("hostRuntime") or "web", 32),
        # These are separate facts.  A forwarded header is not trusted as a
        # device identity unless the immediate peer is explicitly configured
        # as a trusted forwarder; a browser cannot self-report the OS host.
        "peerIp": peer_ip,
        "forwardedFor": forwarded_for,
        "trustedForwardedIp": _trusted_forwarded_ip(request),
        "connectionMethod": _clip(payload.get("connectionMethod"), 64) or "inspect-page",
        "reportedHostname": reported_hostname,
        "reportedInstallId": reported_install_id,
        "identitySource": identity_source,
        "authenticatedNodeId": (associated_node or {}).get("node_id"),
        "authenticatedNodeFingerprint": (associated_node or {}).get("node_fingerprint"),
        "nodeLastSeenAtMs": (associated_node or {}).get("last_seen_ms"),
        "nodeAssociation": "signed-poll+host-self-report" if associated_node else None,
        "lastSeenAtMs": now,
        "lastHeartbeatAtMs": now,
        "lastInputAtMs": None,
        "schemaVersion": SCHEMA_VERSION,
    }
    with _LOCK:
        _purge_locked(now)
        previous = _clients.get(client_id) or {}
        previous_connection_id = str(previous.get("connectionId") or "").strip()
        if connection_id and (not previous or connection_id != previous_connection_id):
            _requeue_inflight_for_reconnect_locked(client_id, now)
        record["lastInputAtMs"] = previous.get("lastInputAtMs")
        record["connectionId"] = connection_id
        _clients[client_id] = record
    return {"ok": True, "client": record}


@client_inspect_router.get("/poll")
def inspect_poll(id: str) -> dict[str, Any]:
    client_id = (id or "").strip()
    if not client_id:
        return {"ok": False, "error": "invalid_id", "commands": []}
    now = _now_ms()
    with _LOCK:
        client = _clients.get(client_id)
        if client is not None:
            client["lastSeenAtMs"] = now
            client["lastHeartbeatAtMs"] = now
    return {"ok": True, "commands": take_pending(client_id)}


@client_inspect_router.post("/result")
def inspect_result(body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = body or {}
    command_id = str(payload.get("commandId") or "").strip()
    if not command_id:
        return {"ok": False, "error": "invalid_command"}
    command = complete_command(
        command_id,
        bool(payload.get("ok", True)),
        payload.get("result"),
        str(payload.get("error") or ""),
    )
    if command is None:
        return {"ok": False, "error": "unknown_command"}
    return {"ok": True, "command": {"id": command["id"], "status": command["status"]}}


@client_inspect_router.get("/clients", response_model=None)
def inspect_clients(request: Request):
    denied = _local_only(request)
    if denied is not None:
        return denied
    return {"ok": True, "clients": list_clients()}


@client_inspect_router.post("/command", response_model=None)
def inspect_command(request: Request, body: dict[str, Any] | None = None):
    denied = _local_only(request)
    if denied is not None:
        return denied
    payload = body or {}
    kind = str(payload.get("kind") or "").strip()
    if kind not in {"screenshot", "eval", "click", "type", "key", "info"}:
        return {"ok": False, "error": "invalid_kind"}
    try:
        command = enqueue_command(
            kind,
            payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
            str(payload.get("clientId") or "") or None,
        )
    except RuntimeError as error:
        return {"ok": False, "error": str(error)}
    return {"ok": True, "command": command}


@client_inspect_router.get("/command/{command_id}", response_model=None)
def inspect_command_status(request: Request, command_id: str):
    denied = _local_only(request)
    if denied is not None:
        return denied
    command = get_command(command_id)
    if command is None:
        return JSONResponse({"ok": False, "error": "unknown_command"}, status_code=404)
    return {"ok": True, "command": command}
