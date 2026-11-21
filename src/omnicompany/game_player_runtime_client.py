"""Shared standard-library client for the persistent game-player runtime.

This module intentionally lives above the game-observatory package.  Importing
``ai_player`` initializes the full facility, while external ``omni`` processes
need only this small authenticated loopback protocol before forwarding an
already-learned route.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


EXTERNAL_AGENT_INVOCATION_ID_ENV = "OMNICOMPANY_GAME_EXTERNAL_AGENT_INVOCATION_ID"
EXTERNAL_AGENT_SESSION_ID_ENV = "OMNICOMPANY_GAME_EXTERNAL_AGENT_SESSION_ID"
EXTERNAL_AGENT_INVOCATION_SEQUENCE_ENV = (
    "OMNICOMPANY_GAME_EXTERNAL_AGENT_INVOCATION_SEQUENCE"
)
SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_ENV = (
    "OMNICOMPANY_GAME_PLAYER_RUNTIME_DESCRIPTOR"
)
SESSION_GAME_PLAYER_RUNTIME_REQUEST_ID_ENV = (
    "OMNICOMPANY_GAME_PLAYER_RUNTIME_REQUEST_ID"
)
SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_SCHEMA = (
    "game-observatory.ai-player.session-runtime-descriptor.v1"
)
SESSION_GAME_PLAYER_RUNTIME_REQUEST_SCHEMA = (
    "game-observatory.ai-player.session-runtime-request.v1"
)
SESSION_GAME_PLAYER_RUNTIME_RESPONSE_SCHEMA = (
    "game-observatory.ai-player.session-runtime-response.v1"
)


class SessionGamePlayerRuntimeError(RuntimeError):
    """A published session runtime could not safely serve the request."""


class SessionRuntimeForwardResult:
    """Small immutable receipt without importing the dataclasses package."""

    __slots__ = ("emissions", "exit_code", "error", "replayed")

    def __init__(
        self,
        *,
        emissions: tuple[dict[str, object], ...],
        exit_code: int,
        error: str | None,
        replayed: bool,
    ) -> None:
        object.__setattr__(self, "emissions", emissions)
        object.__setattr__(self, "exit_code", exit_code)
        object.__setattr__(self, "error", error)
        object.__setattr__(self, "replayed", replayed)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("SessionRuntimeForwardResult is immutable")


def _load_json_object(path: str) -> dict[str, object]:
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SessionGamePlayerRuntimeError(
            f"published session runtime descriptor is unreadable: {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise SessionGamePlayerRuntimeError(
            f"published session runtime descriptor is not an object: {path}"
        )
    return value


def resolve_session_runtime_request_id(
    *,
    invocation_id: str,
    environment_id: str,
    session_id: str,
    goal: str,
    source_step_id: str,
    max_skills: int,
    max_safety: str,
    request_id: str | None = None,
) -> str:
    """Resolve the one canonical idempotency key for a navigate request."""

    resolved = request_id or os.environ.get(SESSION_GAME_PLAYER_RUNTIME_REQUEST_ID_ENV)
    if resolved:
        return resolved
    material = json.dumps(
        {
            "invocation_id": invocation_id,
            "environment_id": environment_id,
            "session_id": session_id,
            "goal": goal,
            "source_step_id": source_step_id,
            "max_skills": max_skills,
            "max_safety": max_safety,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "session-runtime-request." + hashlib.sha256(material).hexdigest()[:32]


def forward_navigate_to_session_runtime(
    *,
    environment_id: str,
    session_id: str,
    goal: str,
    source_step_id: str,
    max_skills: int,
    max_safety: str,
    timeout_seconds: float = 300.0,
    request_id: str | None = None,
) -> SessionRuntimeForwardResult | None:
    """Forward to a published worker, or return ``None`` when none is published.

    Once a descriptor is published, every failure is fail-closed.  The caller
    must never fall back to local device execution because the server may have
    accepted the request before the transport failed.
    """

    descriptor_value = os.environ.get(SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_ENV)
    if not descriptor_value:
        return None
    descriptor_path = os.path.abspath(descriptor_value)
    descriptor = _load_json_object(descriptor_path)
    expected = {
        "schema": SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_SCHEMA,
        "environment_id": environment_id,
        "session_id": session_id,
    }
    for key, value in expected.items():
        if descriptor.get(key) != value:
            raise SessionGamePlayerRuntimeError(
                f"published session runtime identity mismatch for {key}"
            )
    endpoint = str(descriptor.get("endpoint") or "")
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise SessionGamePlayerRuntimeError(
            "published session runtime endpoint is not loopback-only"
        )
    invocation_id = os.environ.get(EXTERNAL_AGENT_INVOCATION_ID_ENV)
    invocation_session_id = os.environ.get(EXTERNAL_AGENT_SESSION_ID_ENV)
    invocation_sequence = os.environ.get(EXTERNAL_AGENT_INVOCATION_SEQUENCE_ENV)
    if (
        not invocation_id
        or invocation_session_id != session_id
        or not invocation_sequence
    ):
        raise SessionGamePlayerRuntimeError(
            "published session runtime invocation ownership is incomplete"
        )
    resolved_request_id = resolve_session_runtime_request_id(
        invocation_id=invocation_id,
        environment_id=environment_id,
        session_id=session_id,
        goal=goal,
        source_step_id=source_step_id,
        max_skills=max_skills,
        max_safety=max_safety,
        request_id=request_id,
    )
    body = {
        "schema": SESSION_GAME_PLAYER_RUNTIME_REQUEST_SCHEMA,
        "request_id": resolved_request_id,
        "operation": "navigate",
        "environment_id": environment_id,
        "session_id": session_id,
        "run_id": descriptor.get("run_id"),
        "process_nonce": descriptor.get("process_nonce"),
        "runtime_snapshot_sha256": descriptor.get("runtime_snapshot_sha256"),
        "invocation_id": invocation_id,
        "invocation_sequence": invocation_sequence,
        "deadline_at": time.time() + timeout_seconds,
        "arguments": {
            "goal": goal,
            "source_step_id": source_step_id,
            "max_skills": max_skills,
            "max_safety": max_safety,
        },
    }
    raw = json.dumps(
        body,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=raw,
        headers={
            "Authorization": f"Bearer {descriptor.get('bearer_token') or ''}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
        raise SessionGamePlayerRuntimeError(
            f"published session runtime did not return a trusted receipt: {exc}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema") != (
        SESSION_GAME_PLAYER_RUNTIME_RESPONSE_SCHEMA
    ):
        raise SessionGamePlayerRuntimeError(
            "published session runtime returned an invalid receipt"
        )
    if payload.get("request_id") != resolved_request_id:
        raise SessionGamePlayerRuntimeError(
            "published session runtime returned a mismatched request_id"
        )
    emissions = payload.get("emissions")
    if not isinstance(emissions, list) or not all(
        isinstance(item, dict) for item in emissions
    ):
        raise SessionGamePlayerRuntimeError(
            "published session runtime returned invalid emissions"
        )
    try:
        exit_code = int(payload.get("exit_code", 1))
    except (TypeError, ValueError) as exc:
        raise SessionGamePlayerRuntimeError(
            "published session runtime returned an invalid exit_code"
        ) from exc
    return SessionRuntimeForwardResult(
        emissions=tuple(emissions),
        exit_code=exit_code,
        error=(str(payload["error"]) if payload.get("error") else None),
        replayed=bool(payload.get("replayed")),
    )


__all__ = [
    "EXTERNAL_AGENT_INVOCATION_ID_ENV",
    "EXTERNAL_AGENT_INVOCATION_SEQUENCE_ENV",
    "EXTERNAL_AGENT_SESSION_ID_ENV",
    "SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_ENV",
    "SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_SCHEMA",
    "SESSION_GAME_PLAYER_RUNTIME_REQUEST_ID_ENV",
    "SESSION_GAME_PLAYER_RUNTIME_REQUEST_SCHEMA",
    "SESSION_GAME_PLAYER_RUNTIME_RESPONSE_SCHEMA",
    "SessionGamePlayerRuntimeError",
    "SessionRuntimeForwardResult",
    "forward_navigate_to_session_runtime",
    "resolve_session_runtime_request_id",
]
