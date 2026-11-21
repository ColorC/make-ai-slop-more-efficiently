"""Crash-report intake for first-party desktop clients.

A desktop host that dies silently usually does so on somebody else's machine, so
a log that only exists there explains nothing.  The client keeps its own rotating
log and, on the next launch, hands the previous session's unexplained ending to
this endpoint.

The POST route is deliberately unauthenticated: a client whose *last* session
crashed may also have lost its session cookie, and refusing the report would
throw away the only evidence.  Everything that reads reports back is local-only,
and the write path is bounded on every axis a stranger could push on — body
size, per-source rate, and total reports retained.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from omnicompany.core.config import resolve_service_data_dir

client_diagnostics_router = APIRouter(
    prefix="/api/clients/desktop/diagnostics", tags=["client-diagnostics"]
)

SCHEMA_VERSION = 1
MAX_BODY_BYTES = 256 * 1024
MAX_LOG_TAIL_CHARS = 64 * 1024
MAX_BACKTRACE_CHARS = 16 * 1024
MAX_REPORTS = 500
#: One host in a crash loop must not be able to bury the others.
MAX_REPORTS_PER_SOURCE_PER_HOUR = 12

_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
_LOCK = threading.RLock()
_recent_by_source: dict[str, list[float]] = {}


def data_dir() -> Path:
    return resolve_service_data_dir("client_diagnostics")


def reports_dir() -> Path:
    path = data_dir() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _rate_limited(source: str, now: float) -> bool:
    """Sliding one-hour window, evaluated under the module lock."""
    window = [stamp for stamp in _recent_by_source.get(source, []) if now - stamp < 3600]
    if len(window) >= MAX_REPORTS_PER_SOURCE_PER_HOUR:
        _recent_by_source[source] = window
        return True
    window.append(now)
    _recent_by_source[source] = window
    return False


def normalize_report(payload: dict[str, Any], *, source: str, user_agent: str) -> dict[str, Any]:
    """Keep only the fields we understand, each individually bounded.

    The client is trusted to be honest but not to be well-behaved: a corrupted
    session marker or a runaway log must not produce an unbounded record.
    """
    fatal_input = payload.get("fatal")
    fatal: dict[str, Any] | None = None
    if isinstance(fatal_input, dict):
        fatal = {
            "message": _clip(fatal_input.get("message"), 4_096),
            "location": _clip(fatal_input.get("location"), 512) or None,
            "thread": _clip(fatal_input.get("thread"), 128),
            "backtrace": _clip(fatal_input.get("backtrace"), MAX_BACKTRACE_CHARS),
            "occurredAtMs": _int(fatal_input.get("occurredAtMs")),
        }

    kind = _clip(payload.get("kind"), 32) or "unknown"
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": kind if kind in {"panic", "unclean_exit"} else "unknown",
        "appVersion": _clip(payload.get("appVersion"), 64),
        "sessionId": _clip(payload.get("sessionId"), 96),
        "pid": _int(payload.get("pid")),
        "startedAtMs": _int(payload.get("startedAtMs")),
        "lastSeenAtMs": _int(payload.get("lastSeenAtMs")),
        "uptimeMs": _int(payload.get("uptimeMs")),
        "reportedAtMs": _int(payload.get("reportedAtMs")),
        "fatal": fatal,
        "logTail": _clip(payload.get("logTail"), MAX_LOG_TAIL_CHARS),
        # Server-observed, never client-supplied.
        "receivedAtMs": _now_ms(),
        "sourceAddress": source,
        "userAgent": _clip(user_agent, 256),
    }


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    fatal = report.get("fatal") or {}
    return {
        "sessionId": report.get("sessionId"),
        "kind": report.get("kind"),
        "appVersion": report.get("appVersion"),
        "uptimeMs": report.get("uptimeMs"),
        "receivedAtMs": report.get("receivedAtMs"),
        "sourceAddress": report.get("sourceAddress"),
        "message": fatal.get("message") or "",
        "location": fatal.get("location") or "",
    }


def _prune_unlocked() -> None:
    files = sorted(
        reports_dir().glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in files[MAX_REPORTS:]:
        stale.unlink(missing_ok=True)


def store_report(report: dict[str, Any]) -> str:
    """Persist one report and return its id. Newest wins on a repeat session id."""
    session_id = report.get("sessionId") or ""
    if not _SESSION_ID.match(session_id):
        session_id = f"unknown-{report['receivedAtMs']}"
        report["sessionId"] = session_id
    report_id = f"{report['receivedAtMs']}-{session_id}"
    with _LOCK:
        path = reports_dir() / f"{report_id}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _prune_unlocked()
    return report_id


def list_reports(limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        files = sorted(
            reports_dir().glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:limit]
        results = []
        for path in files:
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            results.append({"reportId": path.stem, **summarize(report)})
        return results


def read_report(report_id: str) -> dict[str, Any] | None:
    if "/" in report_id or "\\" in report_id or ".." in report_id:
        return None
    path = reports_dir() / f"{report_id}.json"
    with _LOCK:
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None


@client_diagnostics_router.post("")
async def ingest(request: Request) -> JSONResponse:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return JSONResponse({"ok": False, "error": "report_too_large"}, status_code=413)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "invalid_report"}, status_code=400)

    source = request.client.host if request.client else "unknown"
    with _LOCK:
        if _rate_limited(source, time.time()):
            return JSONResponse({"ok": False, "error": "rate_limited"}, status_code=429)

    report = normalize_report(
        payload, source=source, user_agent=request.headers.get("user-agent", "")
    )
    report_id = store_report(report)
    return JSONResponse({"ok": True, "reportId": report_id}, status_code=202)


@client_diagnostics_router.get("")
async def reports(request: Request) -> JSONResponse:
    rejected = _local_only(request)
    if rejected:
        return rejected
    limit = _int(request.query_params.get("limit"), 50)
    return JSONResponse({"ok": True, "reports": list_reports(max(1, min(limit, 200)))})


@client_diagnostics_router.get("/{report_id}")
async def report_detail(report_id: str, request: Request) -> JSONResponse:
    rejected = _local_only(request)
    if rejected:
        return rejected
    report = read_report(report_id)
    if report is None:
        return JSONResponse({"ok": False, "error": "report_not_found"}, status_code=404)
    return JSONResponse({"ok": True, "report": report})
