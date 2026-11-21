"""HTTP control plane for outbound-only governed desktop execution nodes.

Every route here answers to exactly one of three principals and there is no
fourth:

``operator``
    The controller itself — a loopback peer, or a caller that presents the
    gateway secret Caddy injects once its Basic Auth has succeeded.  May do
    anything, including dispatch tasks.

``node:<id>``
    A registered, unrevoked node proving possession of its own Ed25519 key.
    May only open a pairing window bound to itself.  This is what makes the
    one-click flow work from wherever the desktop happens to be, without
    widening what a network position buys.

anonymous
    ``pairing/claim`` (one-time code), ``poll`` and ``receipts`` (Ed25519
    signed) authenticate themselves and are unchanged.

There is one declared exception, and it is not a fourth principal: while the
operator has explicitly opened a *bootstrap window* (``POST /bootstrap``, closed
by default, minutes not days), ``pairing/open`` also answers a caller who is
neither the operator nor an enrolled node.  It exists because node
self-authentication cannot bootstrap itself — the first window for a machine has
to come from somewhere — and it is time-boxed, revocable in one call, and
recorded in the audit ledger both when it opens and for every node that enrolls
through it.  Nothing else in this file consults it.
"""
from __future__ import annotations

import asyncio
import secrets
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from omnicompany.dashboard.controlplane import desktop_nodes_core as core

# The desktop session router owns the gateway secret Caddy shares with this
# process (``~/codeweb/omni-desktop-session.secret``, ACL'd to the dashboard's
# Windows user). Reusing it keeps one secret, one file and one rotation story
# instead of minting a second controller credential nobody would remember.
from omnicompany.dashboard.controlplane.desktop_sessions import _gateway_secret

desktop_nodes_router = APIRouter(prefix="/api/desktop-nodes", tags=["desktop-nodes"])
#: Caddy sets this to the gateway secret after its Basic Auth succeeds. It used
#: to carry the literal "1", which any other already-authenticated principal —
#: a paired LOFA tablet, say — could simply send along and inherit full control
#: plane access with. A value only the gateway knows cannot be volunteered.
TRUSTED_CONTROLLER_HEADER = "x-omnicompany-controller-auth"
#: Optional self-declared caller name, recorded in the audit ledger next to the
#: address. Advisory only — it grants nothing.
ACTOR_HEADER = "x-omnicompany-actor"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _is_operator(request: Request) -> bool:
    """Whether this caller is the controller rather than someone reaching it.

    Loopback is meaningful here because the dashboard binds ``127.0.0.1`` only;
    everything from the LAN arrives through Caddy, and uvicorn's proxy-header
    middleware rewrites ``client.host`` to the address Caddy appended, which a
    remote caller cannot forge by self-asserting ``X-Forwarded-For``.
    """
    host = request.client.host if request.client else ""
    if host in _LOOPBACK_HOSTS:
        return True
    secret = _gateway_secret()
    supplied = str(request.headers.get(TRUSTED_CONTROLLER_HEADER) or "").encode("utf-8")
    return bool(secret) and secrets.compare_digest(supplied, secret)


async def _json(request: Request) -> dict[str, Any] | None:
    try:
        value = await request.json()
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _operator_only(request: Request) -> JSONResponse | None:
    if _is_operator(request):
        return None
    return JSONResponse({"ok": False, "error": "local_controller_required"}, status_code=403)


def _actor(request: Request) -> str:
    """Who the audit ledger will name for this call.

    The control plane has no user accounts, so the honest answer is the caller's
    address plus whatever the client volunteered about itself.
    """
    host = request.client.host if request.client else "unknown"
    label = str(request.headers.get(ACTOR_HEADER) or "").strip()[:64]
    return f"{host} ({label})" if label else host


#: Why a node envelope was not accepted, in words the settings page can act on.
_SIGNATURE_HINTS = {
    "unknown_node": "node_not_enrolled",
    "revoked": "node_revoked",
    "replay": "node_signature_replayed",
    "stale": "node_clock_skew",
}
#: The two ways of arriving un-enrolled: no envelope at all (an old or ACL-blocked
#: client), or a signed envelope from a machine the controller has never met.
#: Both are first-enrollment, and only these two may fall through to the
#: bootstrap window. Everything else — a revoked node, a replayed nonce, a bad
#: signature — is a refusal on its own terms and stays a refusal.
_FIRST_ENROLLMENT_REASONS = {"no_envelope", "unknown_node"}


def _bootstrap_window_open(request: Request, *, claimed_node_id: str = "") -> JSONResponse:
    """Open a window under the temporary bootstrap grant, and say so out loud.

    The window that comes back is unbound: the envelope that got us here, if
    there was one, could not be verified — that is the whole reason this path
    exists — so the claimed node id is recorded in the ledger as a claim rather
    than acted on as an identity.
    """
    opened = core.open_pairing(bootstrap=True)
    core.append_audit(
        "pairing.opened",
        actor=_actor(request),
        mode="",
        bound=False,
        bootstrap=True,
        claimed_node_id=claimed_node_id,
    )
    return JSONResponse({"ok": True, **opened})


@desktop_nodes_router.post("/pairing/open")
async def open_pairing(request: Request) -> JSONResponse:
    """Open a five-minute window, for the controller or for a node itself.

    A node that is already enrolled can ask for its own window from anywhere,
    which is the whole point: adding the execution grant no longer requires
    being at the controller.  What it gets back is bound to itself, so the
    window is worthless to anyone else who sees the code.
    """
    body = await _json(request) or {}
    mode = str(body.get("mode") or "").strip()
    if mode and mode not in core.NODE_MODES:
        return JSONResponse({"ok": False, "error": "invalid_mode"}, status_code=400)

    node_id, reason = core.authenticate_node_request(
        body,
        method="POST",
        path=request.url.path,
    )
    if node_id is not None:
        opened = core.open_pairing(bound_node_id=node_id, bound_mode=mode)
        core.append_audit(
            "pairing.opened",
            node_id=node_id,
            actor=f"node:{node_id} @ {_actor(request)}",
            mode=mode,
            bound=True,
        )
        return JSONResponse({"ok": True, **opened})

    if reason != "no_envelope":
        # A malformed or rejected envelope is never silently downgraded to the
        # operator check; saying which one failed is what lets the settings page
        # tell "you have never enrolled this machine" from "something is wrong".
        if reason in _FIRST_ENROLLMENT_REASONS and core.bootstrap_is_open():
            auth = body.get("auth")
            claimed = str(auth.get("node_id") or "")[:80] if isinstance(auth, dict) else ""
            return _bootstrap_window_open(request, claimed_node_id=claimed)
        return JSONResponse(
            {"ok": False, "error": _SIGNATURE_HINTS.get(reason, "node_signature_rejected")},
            status_code=403,
        )

    if not _is_operator(request):
        if core.bootstrap_is_open():
            return _bootstrap_window_open(request)
        return JSONResponse(
            {"ok": False, "error": "local_controller_required"},
            status_code=403,
        )
    opened = core.open_pairing()
    core.append_audit("pairing.opened", actor=_actor(request), mode=mode, bound=False)
    return JSONResponse({"ok": True, **opened})


@desktop_nodes_router.get("/bootstrap")
async def bootstrap_state(request: Request) -> JSONResponse:
    """Is the temporary first-enrollment window open?

    Readable by anyone the gateway already let through, on purpose: a degraded
    security posture that only the operator can see is one nobody notices. What
    a non-operator gets is the fact and the deadline; who opened it, why, and who
    walked through stay operator-only.
    """
    status = core.bootstrap_status()
    if _is_operator(request):
        return JSONResponse({"ok": True, "bootstrap": status, "operator": True})
    return JSONResponse({
        "ok": True,
        "operator": False,
        "bootstrap": {
            "open": status["open"],
            "expires_at_ms": status["expires_at_ms"],
            "remaining_ms": status["remaining_ms"],
        },
    })


@desktop_nodes_router.post("/bootstrap")
async def bootstrap_open(request: Request) -> JSONResponse:
    """Open the window. Operator only — this is the thing being delegated."""
    rejected = _operator_only(request)
    if rejected:
        return rejected
    body = await _json(request) or {}
    try:
        minutes = int(body.get("minutes") or core.BOOTSTRAP_DEFAULT_MINUTES)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "invalid_minutes"}, status_code=400)
    if not 1 <= minutes <= core.BOOTSTRAP_MAX_MINUTES:
        return JSONResponse({"ok": False, "error": "invalid_minutes"}, status_code=400)
    status = core.open_bootstrap_window(
        minutes=minutes,
        actor=_actor(request),
        reason=str(body.get("reason") or "")[:200],
    )
    return JSONResponse({"ok": True, "bootstrap": status})


@desktop_nodes_router.post("/bootstrap/close")
async def bootstrap_close(request: Request) -> JSONResponse:
    """Shut it now. Operator only, idempotent, immediate."""
    rejected = _operator_only(request)
    if rejected:
        return rejected
    status = core.close_bootstrap_window(actor=_actor(request))
    return JSONResponse({"ok": True, "bootstrap": status})


@desktop_nodes_router.post("/pairing/claim")
async def claim_pairing(request: Request) -> JSONResponse:
    body = await _json(request)
    if body is None:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    status, result = core.claim_pairing(body, actor=_actor(request))
    return JSONResponse(result, status_code=status)


@desktop_nodes_router.get("")
async def nodes(request: Request) -> JSONResponse:
    rejected = _operator_only(request)
    if rejected:
        return rejected
    # Registry reconciliation parses the signed node ledger and may touch
    # multi-megabyte state. Never make the Dashboard event loop pay that cost.
    listed = await asyncio.to_thread(core.list_nodes)
    return JSONResponse({"ok": True, "nodes": listed})


@desktop_nodes_router.post("/{node_id}/tasks")
async def create_task(node_id: str, request: Request) -> JSONResponse:
    rejected = _operator_only(request)
    if rejected:
        return rejected
    body = await _json(request)
    if body is None:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    status, result = await asyncio.to_thread(
        core.queue_task, node_id, body, actor=_actor(request)
    )
    return JSONResponse(result, status_code=status)


@desktop_nodes_router.get("/tasks/{task_id}")
async def task(task_id: str, request: Request) -> JSONResponse:
    rejected = _operator_only(request)
    if rejected:
        return rejected
    result = await asyncio.to_thread(core.get_task, task_id)
    if result is None:
        return JSONResponse({"ok": False, "error": "task_not_found"}, status_code=404)
    return JSONResponse({"ok": True, "task": result})


@desktop_nodes_router.post("/{node_id}/poll")
async def poll(node_id: str, request: Request) -> JSONResponse:
    body = await _json(request)
    if body is None:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    # State is multi-megabyte on long-lived controllers. Keep JSON parse/signature
    # verification off the asyncio event loop so unrelated UI and note requests
    # stay responsive while desktop nodes reconnect.
    status, node = await asyncio.to_thread(core.authenticate_poll, node_id, body)
    if status != 200 or node is None:
        return JSONResponse({"ok": False, "error": "poll_rejected"}, status_code=status)
    if node.get("revoked"):
        return JSONResponse({"ok": False, "error": "node_revoked", "revoked": True}, status_code=410)

    wait_ms = min(max(int(request.query_params.get("wait_ms", "20000")), 0), 25_000)
    deadline = asyncio.get_running_loop().time() + wait_ms / 1000
    while True:
        revoked, signed_task = await asyncio.to_thread(core.poll_next_task, node_id)
        if revoked:
            return JSONResponse({"ok": False, "error": "node_revoked", "revoked": True}, status_code=410)
        if signed_task is not None:
            return JSONResponse({"ok": True, "task": signed_task, "server_time_ms": core._now_ms()})
        if asyncio.get_running_loop().time() >= deadline:
            return JSONResponse({"ok": True, "task": None, "server_time_ms": core._now_ms()})
        await asyncio.sleep(0.25)


@desktop_nodes_router.post("/{node_id}/receipts")
async def receipt(node_id: str, request: Request) -> JSONResponse:
    body = await _json(request)
    if body is None:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    status, result = await asyncio.to_thread(core.accept_receipt, node_id, body)
    return JSONResponse(result, status_code=status)


@desktop_nodes_router.post("/{node_id}/revoke")
async def revoke(node_id: str, request: Request) -> JSONResponse:
    rejected = _operator_only(request)
    if rejected:
        return rejected
    revoked = await asyncio.to_thread(core.revoke_node, node_id, actor=_actor(request))
    if not revoked:
        return JSONResponse({"ok": False, "error": "node_not_found"}, status_code=404)
    return JSONResponse({"ok": True, "node_id": node_id, "revoked": True})


@desktop_nodes_router.post("/{node_id}/revoke/{mode}")
async def revoke_mode(node_id: str, mode: str, request: Request) -> JSONResponse:
    """Withdraw one grant, leaving the node's other mode running."""
    rejected = _operator_only(request)
    if rejected:
        return rejected
    status, result = await asyncio.to_thread(
        core.revoke_node_mode, node_id, mode, actor=_actor(request)
    )
    return JSONResponse(result, status_code=status)


@desktop_nodes_router.get("/audit")
async def audit(request: Request) -> JSONResponse:
    """Who widened or narrowed which node, and when."""
    rejected = _operator_only(request)
    if rejected:
        return rejected
    try:
        limit = min(max(int(request.query_params.get("limit", "50")), 1), 500)
    except ValueError:
        limit = 50
    node_id = str(request.query_params.get("node_id") or "")
    entries = await asyncio.to_thread(core.read_audit, limit, node_id=node_id)
    return JSONResponse({"ok": True, "entries": entries})
