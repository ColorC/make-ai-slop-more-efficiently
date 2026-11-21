"""State and signing primitives for governed desktop execution nodes.

This control plane is intentionally separate from the legacy SSH home-node
facility.  A desktop node only polls outbound for signed, structured tasks;
there is no inbound listener and no arbitrary shell transport.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:  # Optional acceleration; the stdlib path remains the compatibility fallback.
    import orjson as _orjson
except ImportError:  # pragma: no cover - exercised only in minimal installs
    _orjson = None

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from omnicompany.core.config import resolve_service_data_dir

SCHEMA_VERSION = 1

#: Read-only structured adapters (the original contract) and arbitrary execution.
#: They are separate grants on the same node: pairing one never registers the
#: other, and revoking one leaves the other running.
MODE_READONLY = "readonly-l1"
MODE_TRUSTED_EXEC = "trusted-exec-l2"
NODE_MODES = (MODE_READONLY, MODE_TRUSTED_EXEC)

#: What a task may ask for, per mode. `process.exec` is what separates "run this
#: allowlisted adapter" from "run this command", so it is never implicit.
MODE_REQUIRED_CAPABILITIES = {
    MODE_READONLY: {"fs.read", "process.spawn"},
    MODE_TRUSTED_EXEC: {"process.exec"},
}
MODE_ALLOWED_CAPABILITIES = {
    MODE_READONLY: {"fs.read", "process.spawn"},
    MODE_TRUSTED_EXEC: {"fs.read", "fs.write", "process.exec", "process.spawn", "net.access"},
}
#: A read-only adapter answers in seconds; a build does not.
MODE_MAX_TIMEOUT_MS = {
    MODE_READONLY: 120_000,
    MODE_TRUSTED_EXEC: 1_800_000,
}

PAIRING_TTL_MS = 5 * 60 * 1000
#: First enrollment is a chicken-and-egg: node self-authentication only works for
#: a node the controller already knows, so the very first window for a machine
#: has to come from the operator.  When reaching the operator is impractical, the
#: bootstrap window is the declared, time-boxed substitute — see
#: ``open_bootstrap_window``.  It is never open by default and never permanent.
BOOTSTRAP_DEFAULT_MINUTES = 30
BOOTSTRAP_MAX_MINUTES = 120
MAX_CLOCK_SKEW_MS = 5 * 60 * 1000
MAX_PAIR_ATTEMPTS = 5
#: A node may hold a few unclaimed windows at once (a retried one-click leaves
#: the previous one behind), but not an unbounded number.
MAX_LIVE_PAIRINGS_PER_NODE = 8
#: Replay ring for signed control requests. Kept apart from ``poll_nonces`` so a
#: busy poll loop can never evict the nonce that would catch a replayed grant.
MAX_AUTH_NONCES = 256
MAX_PENDING_TASKS_PER_NODE = 64
MAX_TASK_RECORDS = 512
MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_STORED_STREAM_BYTES = 256 * 1024
RECEIPT_SIGNATURE_ALGORITHM = "Ed25519"
AUTH_PERSIST_INTERVAL_MS = 60_000
#: Grants outlive the state file they are written into, so the record of who
#: widened a node and when lives in its own append-only ledger.
MAX_AUDIT_BYTES = 4 * 1024 * 1024
_LOCK = threading.RLock()
_POLL_CACHE_PATH: Path | None = None
_POLL_CACHE_SIGNATURE: tuple[int, int] | None = None
_POLL_CACHE_STATE: dict[str, Any] | None = None
# Poll nonces must be rejected immediately, but persisting presence on every
# 20-second long poll rewrites the entire multi-megabyte controller state. Keep
# the replay window process-local and checkpoint presence at a bounded cadence.
_AUTH_NONCES: dict[tuple[str, str, str], list[str]] = {}
_AUTH_LAST_PERSIST_MS: dict[tuple[str, str, str], int] = {}


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def data_dir() -> Path:
    return resolve_service_data_dir("desktop_nodes")


def state_path() -> Path:
    return data_dir() / "state.json"


def controller_key_path() -> Path:
    return data_dir() / "controller-key.dpapi"


def audit_path() -> Path:
    return data_dir() / "audit.jsonl"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso(timestamp_ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp_ms / 1000))


def append_audit(event: str, *, node_id: str = "", actor: str = "unknown", **fields: Any) -> None:
    """Append one governance event to the controller-side ledger.

    Grant width is the one thing about a node that cannot be reconstructed after
    the fact: ``state.json`` only ever shows the current shape.  Widening,
    narrowing and dispatch each leave a line here instead.
    """
    now = _now_ms()
    entry = {
        "ts_ms": now,
        "ts": _iso(now),
        "event": event,
        "node_id": node_id,
        "actor": actor,
        **fields,
    }
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    try:
        path = audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_AUDIT_BYTES:
            os.replace(path, path.with_suffix(".jsonl.1"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        # An unwritable ledger must not take the control plane down with it; the
        # state file still records the resulting shape.
        pass


def read_audit(limit: int = 50, *, node_id: str = "") -> list[dict[str, Any]]:
    """Most recent ledger entries first."""
    try:
        lines = audit_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries: list[dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        if node_id and entry.get("node_id") != node_id:
            continue
        entries.append(entry)
        if len(entries) >= max(1, limit):
            break
    return entries


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "pairings": {},
        "nodes": {},
        "tasks": {},
        "bootstrap": {},
    }


def _load_state_unlocked() -> dict[str, Any]:
    try:
        raw = state_path().read_bytes()
        value = _orjson.loads(raw) if _orjson is not None else json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return _empty_state()
    for key in ("pairings", "nodes", "tasks", "bootstrap"):
        # ``bootstrap`` post-dates the schema version on purpose: a state file
        # written before it existed is a file with the window closed, which is
        # exactly the default. Bumping the version would discard enrollments.
        if not isinstance(value.get(key), dict):
            value[key] = {}
    return value


def _state_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def _load_poll_state_unlocked() -> dict[str, Any]:
    """Reuse the parsed state while the on-disk file is unchanged.

    Desktop-node long polls wake every 250 ms. The state can contain bounded but
    large task receipts (9+ MB in production), so reparsing it on every wake is
    still a full CPU core even after the work leaves asyncio's main thread.
    """
    global _POLL_CACHE_PATH, _POLL_CACHE_SIGNATURE, _POLL_CACHE_STATE
    path = state_path()
    signature = _state_signature(path)
    if (
        _POLL_CACHE_STATE is not None
        and _POLL_CACHE_PATH == path
        and _POLL_CACHE_SIGNATURE == signature
    ):
        return _POLL_CACHE_STATE
    state = _load_state_unlocked()
    _POLL_CACHE_PATH = path
    _POLL_CACHE_SIGNATURE = signature
    _POLL_CACHE_STATE = state
    return state


def load_state() -> dict[str, Any]:
    """Read the controller state without reparsing an unchanged state file.

    ``client_inspect`` calls this from a worker thread on every desktop-node
    hello.  The state file can be tens of megabytes because it retains bounded
    task receipts, so decoding it for each hello needlessly holds the GIL and
    delays unrelated Dashboard requests.  The poll cache is protected by the
    same lock and invalidated by ``_save_state_unlocked``; using it here keeps
    read-only callers on the same mtime/size guarded path.
    """
    with _LOCK:
        return _load_poll_state_unlocked()


def _save_state_unlocked(state: dict[str, Any]) -> None:
    global _POLL_CACHE_PATH, _POLL_CACHE_SIGNATURE, _POLL_CACHE_STATE
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique temp prevents two briefly overlapping Dashboard processes from
    # deleting/replacing each other's temp file during a restart race.
    temp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    if _orjson is not None:
        # This file is currently about 20 MB because it retains bounded task
        # receipts.  The faster encoder shortens each auth checkpoint and the
        # time it can delay unrelated Dashboard requests.
        encoded = _orjson.dumps(state, option=_orjson.OPT_INDENT_2 | _orjson.OPT_SORT_KEYS)
        temp.write_bytes(encoded)
    else:
        temp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    os.replace(temp, path)
    _POLL_CACHE_PATH = path
    _POLL_CACHE_SIGNATURE = _state_signature(path)
    _POLL_CACHE_STATE = state


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _truncate_receipt_stream(value: Any) -> tuple[Any, dict[str, Any] | None]:
    """Bound captured command output without making a node retry forever.

    The signature is checked against the original receipt before this helper is
    used.  Only the controller-side stored copy is compacted.  Keeping the head,
    tail, byte count, and digest is enough to diagnose accidental large output
    while allowing the node to acknowledge the task and resume polling.
    """
    if not isinstance(value, str):
        return value, None
    raw = value.encode("utf-8")
    if len(raw) <= MAX_STORED_STREAM_BYTES:
        return value, None
    marker = b"\n... [desktop-node output compacted by controller] ...\n"
    side = (MAX_STORED_STREAM_BYTES - len(marker)) // 2
    compacted = raw[:side].decode("utf-8", errors="ignore")
    compacted += marker.decode("ascii")
    compacted += raw[-side:].decode("utf-8", errors="ignore")
    return compacted, {
        "original_bytes": len(raw),
        "stored_bytes": len(compacted.encode("utf-8")),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _compact_receipt_outputs(receipt: dict[str, Any]) -> dict[str, Any]:
    stored = dict(receipt)
    truncation: dict[str, Any] = {}
    for field in ("stdout", "stderr"):
        stored[field], details = _truncate_receipt_stream(stored.get(field))
        if details is not None:
            truncation[field] = details
    if truncation:
        stored["output_compaction"] = truncation
    return stored


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("desktop node controller keys require Windows DPAPI")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buffer = ctypes.create_string_buffer(data)
    in_blob = _DataBlob(len(data), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    out_blob = _DataBlob()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "Omnicompany Desktop Node Controller",
            None,
            None,
            None,
            flags,
            ctypes.byref(out_blob),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            flags,
            ctypes.byref(out_blob),
        )
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows DPAPI operation failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _controller_private_key() -> Ed25519PrivateKey:
    path = controller_key_path()
    with _LOCK:
        try:
            raw = _dpapi(path.read_bytes(), protect=False)
        except FileNotFoundError:
            key = Ed25519PrivateKey.generate()
            raw = key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_bytes(_dpapi(raw, protect=True))
            os.replace(temp, path)
            return key
        if len(raw) != 32:
            raise RuntimeError("invalid protected controller signing key")
        return Ed25519PrivateKey.from_private_bytes(raw)


def controller_public_key() -> str:
    raw = _controller_private_key().public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return _b64(raw)


def controller_fingerprint() -> str:
    return hashlib.sha256(_unb64(controller_public_key())).hexdigest()


def sign_payload(payload: dict[str, Any]) -> str:
    return _b64(_controller_private_key().sign(canonical_json(payload)))


def verify_node_signature(public_key_b64: str, payload: dict[str, Any], signature_b64: str) -> bool:
    try:
        key_bytes = _unb64(public_key_b64)
        signature = _unb64(signature_b64)
        if len(key_bytes) != 32 or len(signature) != 64:
            return False
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(signature, canonical_json(payload))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def authenticate_node_request(
    payload: dict[str, Any],
    *,
    method: str,
    path: str,
) -> tuple[str | None, str]:
    """Which registered node signed this request, and why not if it did not.

    This is what lets an already-known node act on itself from wherever it
    happens to be running, without the caller having to be on the controller
    machine.  Proof of possession of the node's Ed25519 key replaces proof of
    network position; nothing else here is a credential.

    The signature covers the method, the exact path and a digest of the rest of
    the body, so one envelope can never be lifted onto a different endpoint or
    have its ``mode`` rewritten in flight.  Returns ``(node_id, "")`` on success
    and ``(None, reason)`` otherwise — the reason is what lets the caller tell
    "this node was never enrolled" apart from "this signature is wrong".
    """
    auth = payload.get("auth")
    signature = str(payload.get("signature") or "")
    if not isinstance(auth, dict) or not signature:
        return None, "no_envelope"
    node_id = str(auth.get("node_id") or "").strip()
    nonce = str(auth.get("nonce") or "")
    if not node_id or len(node_id) > 80 or len(nonce) < 16 or len(nonce) > 128:
        return None, "malformed"
    if str(auth.get("method") or "") != method or str(auth.get("path") or "") != path:
        return None, "malformed"
    try:
        timestamp_ms = int(auth.get("timestamp_ms") or 0)
    except (TypeError, ValueError):
        return None, "malformed"
    if abs(_now_ms() - timestamp_ms) > MAX_CLOCK_SKEW_MS:
        return None, "stale"
    body = {key: value for key, value in payload.items() if key not in {"auth", "signature"}}
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    if not hmac.compare_digest(str(auth.get("body_sha256") or ""), digest):
        return None, "body_mismatch"

    with _LOCK:
        state = _load_state_unlocked()
        node = state["nodes"].get(node_id)
        if not isinstance(node, dict):
            return None, "unknown_node"
        # A revoked node must not be able to re-admit itself; otherwise
        # revocation would only last until the node asked again. Re-admission is
        # deliberately an operator decision.
        if node.get("revoked"):
            return None, "revoked"
        if not verify_node_signature(str(node.get("node_public_key") or ""), auth, signature):
            return None, "bad_signature"
        nonces = node.get("auth_nonces") if isinstance(node.get("auth_nonces"), list) else []
        if nonce in nonces:
            return None, "replay"
        nonces.append(nonce)
        node["auth_nonces"] = nonces[-MAX_AUTH_NONCES:]
        _save_state_unlocked(state)
    return node_id, ""


# ── 首次注册的临时通路(bootstrap window)────────────────────────────────────
#
# Node self-authentication answers "which machine is asking", but only for a
# machine the controller has already met.  Getting the *first* window therefore
# needs the operator — loopback on the controller box, or the gateway secret.
# When that is out of reach (the controller is a different machine and its shell
# is not at hand), this window is the declared alternative: for a bounded number
# of minutes, a caller that already got through the gateway may open a pairing
# window without being the operator.
#
# What it deliberately does *not* do: it never widens an existing node, never
# re-admits a revoked one, never grants a capability, and never survives its own
# deadline.  It only shortens the road to the eight-digit code — which still has
# to be claimed by a node that then registers a grant of its own accord.


def _bootstrap_view(window: dict[str, Any], *, now: int) -> dict[str, Any]:
    expires_at = int(window.get("expires_at_ms") or 0)
    is_open = bool(window.get("open")) and expires_at > now
    return {
        "open": is_open,
        "opened_at_ms": int(window.get("opened_at_ms") or 0),
        "expires_at_ms": expires_at,
        "remaining_ms": max(0, expires_at - now) if is_open else 0,
        "opened_by": str(window.get("opened_by") or ""),
        "reason": str(window.get("reason") or ""),
        "enrolled": list(window.get("enrolled") or []),
    }


def bootstrap_status() -> dict[str, Any]:
    """Whether the temporary first-enrollment window is open, and until when.

    Expiry is enforced on read rather than by a timer: nothing here runs on a
    schedule, so a deadline that is only honoured by a background task would be a
    deadline that a stalled task quietly extends forever.
    """
    now = _now_ms()
    with _LOCK:
        state = _load_state_unlocked()
        window = state["bootstrap"]
        view = _bootstrap_view(window, now=now)
        # Lapsing is a governance event of its own: the ledger should show the
        # window closing even when nobody was watching.
        if window.get("open") and not view["open"]:
            window["open"] = False
            window["closed_at_ms"] = now
            window["closed_by"] = "expiry"
            _save_state_unlocked(state)
            expired = True
        else:
            expired = False
    if expired:
        append_audit(
            "bootstrap.expired",
            actor="controller",
            expires_at_ms=view["expires_at_ms"],
            enrolled=[entry.get("node_id") for entry in view["enrolled"]],
        )
    return view


def bootstrap_is_open() -> bool:
    return bool(bootstrap_status()["open"])


def open_bootstrap_window(
    *,
    minutes: int = BOOTSTRAP_DEFAULT_MINUTES,
    actor: str = "controller",
    reason: str = "",
) -> dict[str, Any]:
    """Open the window for at most ``BOOTSTRAP_MAX_MINUTES``.

    Re-opening replaces the deadline instead of extending it silently, and the
    list of who enrolled starts over so a later status answer never blends two
    separate windows into one.
    """
    minutes = max(1, min(int(minutes), BOOTSTRAP_MAX_MINUTES))
    now = _now_ms()
    with _LOCK:
        state = _load_state_unlocked()
        state["bootstrap"] = {
            "open": True,
            "opened_at_ms": now,
            "expires_at_ms": now + minutes * 60 * 1000,
            "opened_by": str(actor)[:120],
            "reason": str(reason)[:200],
            "enrolled": [],
        }
        _save_state_unlocked(state)
        view = _bootstrap_view(state["bootstrap"], now=now)
    append_audit(
        "bootstrap.opened",
        actor=actor,
        minutes=minutes,
        expires_at=_iso(view["expires_at_ms"]),
        expires_at_ms=view["expires_at_ms"],
        reason=reason,
    )
    return view


def close_bootstrap_window(*, actor: str = "controller") -> dict[str, Any]:
    """Shut the window now. Safe to call when it is already shut."""
    now = _now_ms()
    with _LOCK:
        state = _load_state_unlocked()
        window = state["bootstrap"]
        was_open = _bootstrap_view(window, now=now)["open"]
        enrolled = list(window.get("enrolled") or [])
        window["open"] = False
        window["closed_at_ms"] = now
        window["closed_by"] = str(actor)[:120]
        _save_state_unlocked(state)
        view = _bootstrap_view(window, now=now)
    if was_open:
        append_audit(
            "bootstrap.closed",
            actor=actor,
            enrolled=[entry.get("node_id") for entry in enrolled],
        )
    return {**view, "was_open": was_open}


def _record_bootstrap_enrollment_unlocked(state: dict[str, Any], node_id: str, mode: str) -> None:
    """Name, in the window's own record, who walked through it."""
    window = state["bootstrap"]
    enrolled = window.get("enrolled")
    window["enrolled"] = enrolled if isinstance(enrolled, list) else []
    window["enrolled"].append({"node_id": node_id, "mode": mode, "at_ms": _now_ms()})
    window["enrolled"] = window["enrolled"][-32:]


def open_pairing(
    *,
    bound_node_id: str = "",
    bound_mode: str = "",
    bootstrap: bool = False,
) -> dict[str, Any]:
    """Open a one-time pairing window.

    A window opened by the controller itself is unbound, because at first
    enrollment the controller does not yet know who is coming.  A window a node
    opened for itself is bound to that node and, when asked for, to one mode:
    the eight-digit code then buys nothing to anyone else even if it leaks.
    """
    now = _now_ms()
    pairing_id = str(uuid.uuid4())
    code = f"{secrets.randbelow(100_000_000):08d}"
    with _LOCK:
        state = _load_state_unlocked()
        state["pairings"] = {
            key: value
            for key, value in state["pairings"].items()
            if int(value.get("expires_at_ms") or 0) >= now and not value.get("consumed")
        }
        if bound_node_id:
            live = sorted(
                (
                    (int(value.get("expires_at_ms") or 0), key)
                    for key, value in state["pairings"].items()
                    if str(value.get("bound_node_id") or "") == bound_node_id
                ),
            )
            for _expiry, key in live[: max(0, len(live) - MAX_LIVE_PAIRINGS_PER_NODE + 1)]:
                state["pairings"].pop(key, None)
        state["pairings"][pairing_id] = {
            "code_hash": hashlib.sha256(code.encode("ascii")).hexdigest(),
            "expires_at_ms": now + PAIRING_TTL_MS,
            "attempts": 0,
            "consumed": False,
            "bound_node_id": bound_node_id,
            "bound_mode": bound_mode,
            # Carried on the window rather than re-derived at claim time: the
            # window may outlive the bootstrap deadline by a few minutes, and the
            # honest answer to "how was this node admitted" is how the *window*
            # was opened.
            "bootstrap": bool(bootstrap),
        }
        _save_state_unlocked(state)
    return {
        "pairing_id": pairing_id,
        "code": code,
        "expires_at_ms": now + PAIRING_TTL_MS,
        "controller_public_key": controller_public_key(),
        "controller_fingerprint": controller_fingerprint(),
        "bound_node_id": bound_node_id,
        "bound_mode": bound_mode,
        "bootstrap": bool(bootstrap),
    }


def claim_pairing(payload: dict[str, Any], *, actor: str = "node") -> tuple[int, dict[str, Any]]:
    now = _now_ms()
    pairing_id = str(payload.get("pairing_id") or "").strip()
    code = str(payload.get("code") or "").strip()
    node_id = str(payload.get("node_id") or "").strip()
    node_public_key = str(payload.get("node_public_key") or "").strip()
    label = str(payload.get("label") or "").strip()[:80]
    grant_id = str(payload.get("grant_id") or "").strip()
    policy_hash = str(payload.get("policy_hash") or "").strip().lower()
    # A claim that says nothing about mode is a read-only claim. Execution is
    # only ever registered by asking for it.
    mode = str(payload.get("mode") or MODE_READONLY).strip()
    if mode not in NODE_MODES:
        return 400, {"ok": False, "error": "invalid_mode"}
    try:
        generation = int(payload.get("generation") or 0)
        public_key_bytes = _unb64(node_public_key)
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "invalid_pairing_payload"}
    if (
        not pairing_id
        or len(code) != 8
        or not code.isdigit()
        or not node_id
        or len(node_id) > 80
        or len(public_key_bytes) != 32
        or not grant_id
        or generation < 1
        or len(policy_hash) != 64
    ):
        return 400, {"ok": False, "error": "invalid_pairing_payload"}

    with _LOCK:
        state = _load_state_unlocked()
        pairing = state["pairings"].get(pairing_id)
        if not isinstance(pairing, dict):
            return 404, {"ok": False, "error": "pairing_not_found"}
        if pairing.get("consumed") or int(pairing.get("expires_at_ms") or 0) < now:
            return 410, {"ok": False, "error": "pairing_expired"}
        attempts = int(pairing.get("attempts") or 0) + 1
        pairing["attempts"] = attempts
        expected = str(pairing.get("code_hash") or "")
        actual = hashlib.sha256(code.encode("ascii")).hexdigest()
        if attempts > MAX_PAIR_ATTEMPTS or not hmac.compare_digest(expected, actual):
            _save_state_unlocked(state)
            return 403, {"ok": False, "error": "pairing_code_rejected"}
        # Binding is checked only after the code, so a caller who does not know
        # the code never learns who the window was opened for. A bound window is
        # spent by its owner or by nobody.
        bound_node_id = str(pairing.get("bound_node_id") or "")
        bound_mode = str(pairing.get("bound_mode") or "")
        if bound_node_id and bound_node_id != node_id:
            _save_state_unlocked(state)
            return 409, {"ok": False, "error": "pairing_bound_to_other_node"}
        if bound_mode and bound_mode != mode:
            _save_state_unlocked(state)
            return 409, {"ok": False, "error": "pairing_bound_to_other_mode"}
        pairing["consumed"] = True
        via_bootstrap = bool(pairing.get("bootstrap"))
        registration = {
            "grant_id": grant_id,
            "generation": generation,
            "policy_hash": policy_hash,
            "capabilities": sorted(MODE_ALLOWED_CAPABILITIES[mode]),
            "paired_at_ms": now,
            "revoked": False,
        }
        existing = state["nodes"].get(node_id)
        if isinstance(existing, dict) and not existing.get("revoked"):
            # Pairing a second mode must not discard the first one's grant, its
            # replay nonces, or its pairing timestamp.
            node = existing
            if str(existing.get("node_public_key") or "") != node_public_key:
                return 409, {"ok": False, "error": "node_identity_changed"}
        else:
            node = {
                "node_id": node_id,
                "node_public_key": node_public_key,
                "node_fingerprint": hashlib.sha256(public_key_bytes).hexdigest(),
                "paired_at_ms": now,
                "last_seen_ms": now,
                "revoked": False,
                "poll_nonces": [],
                "status": {},
                "grants": {},
            }
        node["label"] = label or node_id
        grants = node.get("grants")
        node["grants"] = grants if isinstance(grants, dict) else {}
        node["grants"][mode] = registration
        # Mirror the read-only registration into the flat fields the pre-L2
        # readers still use. Execution never appears there.
        if mode == MODE_READONLY:
            node["grant_id"] = grant_id
            node["generation"] = generation
            node["policy_hash"] = policy_hash
            node["capabilities"] = registration["capabilities"]
        if via_bootstrap:
            node["bootstrap_enrolled"] = True
            _record_bootstrap_enrollment_unlocked(state, node_id, mode)
        state["nodes"][node_id] = node
        _save_state_unlocked(state)
        profile = permission_profile(node)
    append_audit(
        "grant.registered",
        node_id=node_id,
        actor=actor,
        mode=mode,
        grant_id=grant_id,
        generation=generation,
        capabilities=registration["capabilities"],
        granted_modes=profile["granted_modes"],
        level=profile["level"],
        bootstrap=via_bootstrap,
    )
    return 200, {
        "ok": True,
        "node_id": node_id,
        "controller_public_key": controller_public_key(),
        "controller_fingerprint": controller_fingerprint(),
        "node_fingerprint": node["node_fingerprint"],
        "paired_at_ms": now,
    }


def permission_profile(node: dict[str, Any]) -> dict[str, Any]:
    """One answer to "how wide is this node right now?".

    Grant width is otherwise spread over ``grants``, the pre-L2 flat fields and
    the node-level ``revoked`` flag, which is exactly why nobody could tell at a
    glance.  Everything that reports node state — CLI, HTTP, settings page —
    reads this instead of re-deriving it.
    """
    if node.get("revoked"):
        return {
            "granted_modes": [],
            "missing_modes": list(NODE_MODES),
            "capabilities": [],
            "full": False,
            "level": "revoked",
        }
    granted = [mode for mode in NODE_MODES if node_grant(node, mode) is not None]
    capabilities: set[str] = set()
    for mode in granted:
        capabilities |= MODE_ALLOWED_CAPABILITIES[mode]
    full = len(granted) == len(NODE_MODES)
    return {
        "granted_modes": granted,
        "missing_modes": [mode for mode in NODE_MODES if mode not in granted],
        "capabilities": sorted(capabilities),
        "full": full,
        "level": "all" if full else ("none" if not granted else "partial"),
    }


def _public_node(node: dict[str, Any]) -> dict[str, Any]:
    public = {
        key: value
        for key, value in node.items()
        if key not in {"node_public_key", "poll_nonces", "auth_nonces"}
    }
    public["permissions"] = permission_profile(node)
    return public


def list_nodes() -> list[dict[str, Any]]:
    with _LOCK:
        state = _load_state_unlocked()
        return [_public_node(node) for node in state["nodes"].values() if isinstance(node, dict)]


def revoke_node(node_id: str, *, actor: str = "controller") -> bool:
    with _LOCK:
        state = _load_state_unlocked()
        node = state["nodes"].get(node_id)
        if not isinstance(node, dict):
            return False
        withdrawn = permission_profile(node)["granted_modes"]
        node["revoked"] = True
        node["revoked_at_ms"] = _now_ms()
        for registration in (node.get("grants") or {}).values():
            if isinstance(registration, dict):
                registration["revoked"] = True
        for task in state["tasks"].values():
            if isinstance(task, dict) and task.get("node_id") == node_id and task.get("status") in {"queued", "claimed"}:
                task["status"] = "revoked"
        _save_state_unlocked(state)
    append_audit(
        "node.revoked",
        node_id=node_id,
        actor=actor,
        withdrawn_modes=withdrawn,
        level="revoked",
    )
    return True


def revoke_node_mode(node_id: str, mode: str, *, actor: str = "controller") -> tuple[int, dict[str, Any]]:
    """Withdraw one grant and leave the other alone.

    This is the whole reason the two modes are separate registrations: taking
    execution away should never mean giving up read-only access as well.
    """
    if mode not in NODE_MODES:
        return 400, {"ok": False, "error": "invalid_mode"}
    with _LOCK:
        state = _load_state_unlocked()
        node = state["nodes"].get(node_id)
        if not isinstance(node, dict):
            return 404, {"ok": False, "error": "node_not_found"}
        grants = node.get("grants")
        if not isinstance(grants, dict) or mode not in grants:
            return 404, {"ok": False, "error": "mode_not_registered"}
        grants.pop(mode)
        if mode == MODE_READONLY:
            for key in ("grant_id", "generation", "policy_hash", "capabilities"):
                node.pop(key, None)
        for task in state["tasks"].values():
            if (
                isinstance(task, dict)
                and task.get("node_id") == node_id
                and task.get("mode") == mode
                and task.get("status") in {"queued", "claimed"}
            ):
                task["status"] = "revoked"
        _save_state_unlocked(state)
        profile = permission_profile(node)
    append_audit(
        "grant.revoked",
        node_id=node_id,
        actor=actor,
        mode=mode,
        granted_modes=profile["granted_modes"],
        level=profile["level"],
    )
    return 200, {
        "ok": True,
        "node_id": node_id,
        "mode": mode,
        "remaining": profile["granted_modes"],
    }


def node_grant(node: dict[str, Any], mode: str) -> dict[str, Any] | None:
    """The registration for one mode, tolerating pre-L2 node records."""
    grants = node.get("grants")
    if isinstance(grants, dict) and isinstance(grants.get(mode), dict):
        registration = grants[mode]
        return None if registration.get("revoked") else registration
    if mode == MODE_READONLY and node.get("grant_id"):
        return {
            "grant_id": node["grant_id"],
            "generation": node.get("generation") or 0,
            "policy_hash": node.get("policy_hash") or "",
            "capabilities": node.get("capabilities") or sorted(MODE_ALLOWED_CAPABILITIES[mode]),
        }
    return None


def queue_task(node_id: str, request: dict[str, Any], *, actor: str = "controller") -> tuple[int, dict[str, Any]]:
    now = _now_ms()
    with _LOCK:
        state = _load_state_unlocked()
        node = state["nodes"].get(node_id)
        if not isinstance(node, dict):
            return 404, {"ok": False, "error": "node_not_found"}
        if node.get("revoked"):
            return 409, {"ok": False, "error": "node_revoked"}
        if len(state["tasks"]) >= MAX_TASK_RECORDS:
            terminal = sorted(
                (
                    (task_id, task)
                    for task_id, task in state["tasks"].items()
                    if isinstance(task, dict)
                    and task.get("status") not in {"queued", "claimed"}
                ),
                key=lambda item: int(item[1].get("completed_at_ms") or item[1].get("created_at_ms") or 0),
            )
            for task_id, _ in terminal[: max(1, len(state["tasks"]) - MAX_TASK_RECORDS + 1)]:
                state["tasks"].pop(task_id, None)
            if len(state["tasks"]) >= MAX_TASK_RECORDS:
                return 429, {"ok": False, "error": "controller_task_ledger_full"}
        pending = sum(
            1
            for task in state["tasks"].values()
            if isinstance(task, dict)
            and task.get("node_id") == node_id
            and task.get("status") in {"queued", "claimed"}
        )
        if pending >= MAX_PENDING_TASKS_PER_NODE:
            return 429, {"ok": False, "error": "node_queue_full"}
        task_id = str(request.get("task_id") or uuid.uuid4()).strip()
        if not task_id or len(task_id) > 96 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in task_id):
            return 400, {"ok": False, "error": "invalid_task_id"}
        if task_id in state["tasks"]:
            return 409, {"ok": False, "error": "duplicate_task_id"}
        mode = str(request.get("mode") or MODE_READONLY).strip()
        if mode not in NODE_MODES:
            return 400, {"ok": False, "error": "invalid_mode"}
        # Only a grant the node actually registered can authorize a task. A
        # request for execution against a node that only paired read-only is
        # refused here, and refused again by the node itself.
        registration = node_grant(node, mode)
        if registration is None:
            return 409, {"ok": False, "error": "mode_not_registered"}
        try:
            timeout_ms = int(request.get("timeout_ms") or 30_000)
            ttl_ms = int(request.get("ttl_ms") or 120_000)
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "invalid_limits"}
        watch = bool(request.get("watch") or False)
        if watch and mode != MODE_TRUSTED_EXEC:
            return 400, {"ok": False, "error": "watch_requires_trusted_exec"}
        if (
            not 1_000 <= timeout_ms <= MODE_MAX_TIMEOUT_MS[mode]
            or not 5_000 <= ttl_ms <= 60 * 60 * 1000
        ):
            return 400, {"ok": False, "error": "invalid_limits"}
        argv = request.get("argv")
        capabilities = request.get("requested_capabilities") or sorted(
            MODE_REQUIRED_CAPABILITIES[mode]
        )
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            return 400, {"ok": False, "error": "invalid_argv"}
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) for item in capabilities
        ):
            return 400, {"ok": False, "error": "invalid_capabilities"}
        requested = set(capabilities)
        if not requested <= MODE_ALLOWED_CAPABILITIES[mode] or not (
            MODE_REQUIRED_CAPABILITIES[mode] <= requested
        ):
            return 400, {"ok": False, "error": "invalid_capabilities"}
        payload = {
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "node_id": node_id,
            "grant_id": registration["grant_id"],
            "generation": registration["generation"],
            "policy_hash": registration["policy_hash"],
            "nonce": secrets.token_urlsafe(24),
            "issued_at_ms": now,
            "expires_at_ms": now + ttl_ms,
            "program_id": str(request.get("program_id") or ""),
            "argv": argv,
            "cwd_relative": str(request.get("cwd_relative") or "."),
            "timeout_ms": timeout_ms,
            "watch": watch,
            "requested_capabilities": sorted(requested),
            "mode": mode,
        }
        signature = sign_payload(payload)
        state["tasks"][task_id] = {
            "task_id": task_id,
            "node_id": node_id,
            "mode": mode,
            "status": "queued",
            "created_at_ms": now,
            "payload": payload,
            "signature": signature,
        }
        _save_state_unlocked(state)
    append_audit(
        "task.queued",
        node_id=node_id,
        actor=actor,
        mode=mode,
        task_id=task_id,
        program_id=payload["program_id"],
        argv=argv,
        cwd_relative=payload["cwd_relative"],
        requested_capabilities=payload["requested_capabilities"],
    )
    return 202, {"ok": True, "task_id": task_id, "status": "queued"}


def _receipt_verification_status_unlocked(
    state: dict[str, Any], task: dict[str, Any]
) -> dict[str, Any]:
    """Describe whether a settled receipt can be independently replay-verified.

    The controller may retain a compact diagnostic copy of a receipt, but it
    must not present that copy as proof of the original signed body.  This
    status is deliberately additive to the operator-facing task response so
    older callers can continue inspecting task output.
    """
    receipt = task.get("receipt")
    if not isinstance(receipt, dict):
        return {"status": "unavailable", "reason": "receipt_not_settled"}
    replayable = task.get("receipt_signature_replayable")
    if replayable is False:
        return {
            "status": "unavailable",
            "reason": "receipt_compacted_without_original_signed_body",
        }
    if replayable is not True:
        return {"status": "unavailable", "reason": "receipt_signature_unavailable"}
    signature = task.get("receipt_signature")
    public_key = task.get("receipt_node_public_key")
    body_sha256 = task.get("receipt_signed_body_sha256")
    verified_at_ms = task.get("receipt_verified_at_ms")
    if not all(
        isinstance(value, str) and value for value in (signature, public_key, body_sha256)
    ):
        return {"status": "unavailable", "reason": "receipt_signature_unavailable"}
    if not isinstance(verified_at_ms, int) or verified_at_ms <= 0:
        return {"status": "unavailable", "reason": "receipt_verification_time_unavailable"}
    if task.get("receipt_signature_algorithm") != RECEIPT_SIGNATURE_ALGORITHM:
        return {
            "status": "unavailable",
            "reason": "receipt_signature_algorithm_unavailable",
        }
    node_id = str(task.get("node_id") or "")
    if receipt.get("task_id") != task.get("task_id") or receipt.get("node_id") != node_id:
        return {"status": "invalid", "reason": "receipt_task_or_node_mismatch"}
    node = state.get("nodes", {}).get(node_id)
    if not isinstance(node, dict):
        return {"status": "unavailable", "reason": "receipt_node_unavailable"}
    current_key = str(node.get("node_public_key") or "")
    if public_key != current_key:
        return {"status": "invalid", "reason": "receipt_node_key_changed"}
    actual_sha256 = hashlib.sha256(canonical_json(receipt)).hexdigest()
    if body_sha256 != actual_sha256:
        return {"status": "invalid", "reason": "receipt_body_hash_mismatch"}
    if not verify_node_signature(current_key, receipt, signature):
        return {"status": "invalid", "reason": "receipt_signature_invalid"}
    return {
        "status": "verified",
        "algorithm": RECEIPT_SIGNATURE_ALGORITHM,
        "body_sha256": body_sha256,
        "node_id": node_id,
        "node_public_key": public_key,
        "verified_at_ms": verified_at_ms,
    }


def get_task(task_id: str) -> dict[str, Any] | None:
    with _LOCK:
        state = _load_state_unlocked()
        task = state["tasks"].get(task_id)
        if not isinstance(task, dict):
            return None
        public = dict(task)
        # This is the controller's task-dispatch signature, not a receipt
        # proof.  Never expose it through the operator task read endpoint.
        public.pop("signature", None)
        public["receipt_verification"] = _receipt_verification_status_unlocked(state, task)
        return public


def get_verified_task(task_id: str) -> dict[str, Any] | None:
    """Return a task only when its original node receipt verifies end-to-end.

    ``None`` means either the task is absent or its receipt is unavailable or
    invalid.  In particular, compacted output is intentionally not replayable
    because the original signed body is not retained in a CAS.
    """
    with _LOCK:
        state = _load_state_unlocked()
        task = state["tasks"].get(task_id)
        if not isinstance(task, dict):
            return None
        status = _receipt_verification_status_unlocked(state, task)
        if status.get("status") != "verified":
            return None
        public = dict(task)
        public.pop("signature", None)
        public["receipt_verification"] = status
        return public


def authenticate_poll(node_id: str, signed: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
    request_payload = signed.get("payload")
    signature = str(signed.get("signature") or "")
    if not isinstance(request_payload, dict):
        return 400, None
    now = _now_ms()
    if request_payload.get("node_id") != node_id:
        return 403, None
    try:
        timestamp_ms = int(request_payload.get("timestamp_ms") or 0)
    except (TypeError, ValueError):
        return 400, None
    nonce = str(request_payload.get("nonce") or "")
    if abs(now - timestamp_ms) > MAX_CLOCK_SKEW_MS or len(nonce) < 16:
        return 401, None
    with _LOCK:
        state = _load_poll_state_unlocked()
        node = state["nodes"].get(node_id)
        if not isinstance(node, dict):
            return 404, None
        if not verify_node_signature(str(node.get("node_public_key") or ""), request_payload, signature):
            return 401, None
        # Include the public key so re-pairing the same logical node starts a
        # fresh in-memory replay window and gets an immediate first checkpoint.
        auth_key = (str(state_path()), node_id, str(node.get("node_public_key") or ""))
        nonces = _AUTH_NONCES.get(auth_key)
        if nonces is None:
            stored_nonces = node.get("poll_nonces")
            nonces = list(stored_nonces) if isinstance(stored_nonces, list) else []
        if nonce in nonces:
            return 409, None
        nonces.append(nonce)
        nonces = nonces[-MAX_AUTH_NONCES:]
        _AUTH_NONCES[auth_key] = nonces
        status = request_payload.get("status")
        public_node = dict(node)
        public_node["last_seen_ms"] = now
        public_node["status"] = status if isinstance(status, dict) else {}

        last_persist = _AUTH_LAST_PERSIST_MS.get(auth_key)
        if last_persist is None or now - last_persist >= AUTH_PERSIST_INTERVAL_MS:
            node["poll_nonces"] = nonces
            node["last_seen_ms"] = public_node["last_seen_ms"]
            node["status"] = public_node["status"]
            _save_state_unlocked(state)
            _AUTH_LAST_PERSIST_MS[auth_key] = now
        return 200, public_node


def _poll_next_task_unlocked(
    state: dict[str, Any],
    node_id: str,
    now: int,
) -> tuple[bool, dict[str, Any] | None, bool]:
    """Inspect one node from one state snapshot.

    Returns ``(revoked, signed_task, dirty)``. Empty polls are deliberately not
    dirty: the state is multi-megabyte and rewriting it four times a second per
    desktop node previously monopolized Dashboard's event loop.
    """
    node = state["nodes"].get(node_id)
    if not isinstance(node, dict) or node.get("revoked"):
        return True, None, False
    dirty = False
    for task in state["tasks"].values():
        if not isinstance(task, dict) or task.get("node_id") != node_id or task.get("status") != "claimed":
            continue
        payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        expires_at = int(payload.get("expires_at_ms") or 0)
        claimed_at = int(task.get("claimed_at_ms") or 0)
        timeout_ms = int(payload.get("timeout_ms") or 30_000)
        if expires_at < now:
            task["status"] = "expired"
            dirty = True
        elif claimed_at + min(max(timeout_ms + 30_000, 60_000), 180_000) < now:
            # A crashed node may have accepted this task already. Re-delivery is
            # safe because the node persists task-id + nonce before execution
            # and will answer with a replay-rejected receipt instead of rerunning.
            task["status"] = "queued"
            task["requeued_at_ms"] = now
            dirty = True
    candidates = [
        task
        for task in state["tasks"].values()
        if isinstance(task, dict) and task.get("node_id") == node_id and task.get("status") == "queued"
    ]
    candidates.sort(key=lambda item: int(item.get("created_at_ms") or 0))
    for task in candidates:
        payload = task.get("payload")
        if not isinstance(payload, dict):
            task["status"] = "invalid"
            dirty = True
            continue
        if int(payload.get("expires_at_ms") or 0) < now:
            task["status"] = "expired"
            dirty = True
            continue
        task["status"] = "claimed"
        task["claimed_at_ms"] = now
        return False, {"payload": payload, "signature": task["signature"]}, True
    return False, None, dirty


def poll_next_task(node_id: str) -> tuple[bool, dict[str, Any] | None]:
    """Combined revocation/task check for the long-poll hot path."""
    with _LOCK:
        state = _load_poll_state_unlocked()
        revoked, signed_task, dirty = _poll_next_task_unlocked(state, node_id, _now_ms())
        if dirty:
            _save_state_unlocked(state)
        return revoked, signed_task


def claim_next_task(node_id: str) -> dict[str, Any] | None:
    return poll_next_task(node_id)[1]


def accept_receipt(node_id: str, signed: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    receipt = signed.get("receipt")
    signature = str(signed.get("signature") or "")
    if not isinstance(receipt, dict):
        return 400, {"ok": False, "error": "invalid_receipt"}
    task_id = str(receipt.get("task_id") or "")
    if receipt.get("node_id") != node_id or not task_id:
        return 400, {"ok": False, "error": "invalid_receipt"}
    with _LOCK:
        state = _load_state_unlocked()
        node = state["nodes"].get(node_id)
        task = state["tasks"].get(task_id)
        if not isinstance(node, dict) or not isinstance(task, dict) or task.get("node_id") != node_id:
            return 404, {"ok": False, "error": "task_not_found"}
        if not verify_node_signature(str(node.get("node_public_key") or ""), receipt, signature):
            return 401, {"ok": False, "error": "invalid_signature"}
        if task.get("status") in {"completed", "rejected", "failed", "revoked"}:
            return 409, {"ok": False, "error": "receipt_replay"}
        receipt_verified_at_ms = _now_ms()
        receipt_signed_body_sha256 = hashlib.sha256(canonical_json(receipt)).hexdigest()
        receipt_node_public_key = str(node.get("node_public_key") or "")
        receipt_signature_replayable = True
        serialized_size = len(canonical_json(receipt))
        if serialized_size > MAX_RESULT_BYTES:
            receipt = _compact_receipt_outputs(receipt)
            receipt_signature_replayable = False
            serialized_size = len(canonical_json(receipt))
            if serialized_size > MAX_RESULT_BYTES:
                return 413, {"ok": False, "error": "receipt_too_large"}
        task["status"] = str(receipt.get("status") or "failed")
        task["receipt"] = receipt
        task["receipt_signature"] = signature
        task["receipt_node_public_key"] = receipt_node_public_key
        task["receipt_signed_body_sha256"] = receipt_signed_body_sha256
        task["receipt_signature_algorithm"] = RECEIPT_SIGNATURE_ALGORITHM
        task["receipt_verified_at_ms"] = receipt_verified_at_ms
        task["receipt_signature_replayable"] = receipt_signature_replayable
        task["completed_at_ms"] = _now_ms()
        _save_state_unlocked(state)
        settled_mode = str(task.get("mode") or "")
    append_audit(
        "task.settled",
        node_id=node_id,
        actor="node",
        mode=settled_mode,
        task_id=task_id,
        status=task["status"],
        exit_code=receipt.get("exit_code"),
        termination=receipt.get("termination"),
        duration_ms=receipt.get("duration_ms"),
    )
    return 200, {"ok": True, "task_id": task_id, "status": task["status"]}


def node_is_revoked(node_id: str) -> bool:
    with _LOCK:
        node = _load_state_unlocked()["nodes"].get(node_id)
        return not isinstance(node, dict) or bool(node.get("revoked"))
