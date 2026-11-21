"""Bind a dashboard-selected model to the OpenCode v2 TUI.

The v2 TUI is a client of the background server and has no ``--model`` flag
(the local launcher shim drops the pair), so a model chosen in the dashboard
never reached the running TUI.  The server API does accept a model when a
session is created, and ``opencode --session <id>`` continues that session
with its stored model; pre-create the session here and hand its id to the TUI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_OPENCODE_PROVIDER_ID = "opencode-go"
_SESSION_CREATE_TIMEOUT_S = 20.0


def normalize_opencode_model_ref(model: str | None) -> dict[str, str] | None:
    """Turn a dashboard model id into the server's ``Model.Ref`` shape.

    The dashboard catalog sends bare ids (``deepseek-v4.1-flash``); a
    ``provider/model`` form is also accepted so callers can name the account
    explicitly.
    """
    raw = str(model or "").strip()
    if not raw:
        return None
    if "/" in raw:
        provider_id, model_id = raw.split("/", 1)
    else:
        provider_id, model_id = DEFAULT_OPENCODE_PROVIDER_ID, raw
    provider_id = provider_id.strip()
    model_id = model_id.strip()
    if not provider_id or not model_id:
        return None
    return {"providerID": provider_id, "id": model_id}


def _opencode_executable() -> str:
    """Resolve the native opencode binary, preferring it over the .cmd shim.

    A JSON payload passed through cmd.exe is prone to quote mangling, so the
    npm shim is only a fallback.
    """
    resolved = shutil.which("opencode") or "opencode"
    if os.name == "nt" and resolved.lower().endswith(".cmd"):
        base = Path(resolved).parent
        for candidate in (
            base / "node_modules" / "@opencode" / "cli" / "bin" / "opencode.exe",
            base / "node_modules" / "opencode-ai" / "bin" / "opencode.exe",
        ):
            if candidate.is_file():
                return str(candidate)
    return resolved


def _parse_json_object(text: str) -> dict | None:
    start = text.find("{")
    if start < 0:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def parse_created_session_id(stdout: str) -> str | None:
    payload = _parse_json_object(stdout)
    if payload is None:
        return None
    data = payload.get("data")
    candidates = [
        data.get("id") if isinstance(data, dict) else None,
        payload.get("id"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.startswith("ses"):
            return candidate
    return None


async def _run_session_create(payload: dict, cwd: str) -> str:
    process = await asyncio.create_subprocess_exec(
        _opencode_executable(),
        "api",
        "session.create",
        "--data",
        json.dumps(payload, ensure_ascii=False),
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=_SESSION_CREATE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(
            f"opencode session.create timed out after {_SESSION_CREATE_TIMEOUT_S:.0f}s"
        ) from None
    if process.returncode != 0:
        detail = stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"opencode session.create exited {process.returncode}: {detail[:300]}"
        )
    return stdout.decode("utf-8", "replace")


async def create_opencode_model_session(model: str | None, cwd: str) -> str | None:
    """Create a server-side OpenCode session pinned to ``model``.

    Returns ``None`` when no model was named or the server call failed; the
    caller then falls back to its ordinary launch command.
    """
    ref = normalize_opencode_model_ref(model)
    if ref is None:
        return None
    directory = Path(cwd).expanduser().resolve().as_posix()
    payload = {"model": ref, "location": {"directory": directory}}
    try:
        stdout = await _run_session_create(payload, directory)
    except Exception:
        logger.warning(
            "OpenCode model-bound session creation failed model=%s cwd=%s",
            model,
            directory,
            exc_info=True,
        )
        return None
    session_id = parse_created_session_id(stdout)
    if session_id is None:
        logger.warning("OpenCode session.create returned no session id: %.300s", stdout)
    return session_id
