"""Read-only HTTP surface for resource-monitor evidence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Response

from .latency_monitor import get_latency_monitor
from .focus_monitor import get_focus_monitor
from .resource_monitor import get_resource_monitor, read_jsonl_tail

resource_router = APIRouter(prefix="/cc/resources", tags=["resource-monitor"])


@resource_router.get("/latest")
async def resource_latest() -> dict[str, Any]:
    return get_resource_monitor().read_latest()


@resource_router.get("/status")
async def resource_status() -> dict[str, Any]:
    return get_resource_monitor().status()


def _tail_response_bytes(path: Path, limit: int) -> bytes:
    payload = {
        "items": read_jsonl_tail(path, limit),
        "read_only": True,
        "automatic_cleanup_allowed": False,
    }
    return json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


async def _tail_response(path: Path, limit: int) -> Response:
    # Large history responses used to do file IO, JSONL parsing, and multi-MB
    # response encoding on ccdaemon's event loop. Keep all CPU/disk work in a
    # worker thread so PTY WebSockets and health probes stay responsive.
    content = await asyncio.to_thread(_tail_response_bytes, path, limit)
    return Response(content=content, media_type="application/json")


@resource_router.get("/history")
async def resource_history(
    limit: int = Query(default=60, ge=1, le=500),
) -> Response:
    monitor = get_resource_monitor()
    return await _tail_response(monitor.data_dir / "history.jsonl", limit)


@resource_router.get("/alerts")
async def resource_alerts(
    limit: int = Query(default=100, ge=1, le=500),
) -> Response:
    monitor = get_resource_monitor()
    return await _tail_response(monitor.data_dir / "alerts.jsonl", limit)


@resource_router.get("/latency")
async def resource_latency(response: Response) -> dict[str, Any]:
    """Return passive in-memory timing aggregates for live session traffic."""
    response.headers["Cache-Control"] = "no-store"
    return get_latency_monitor().snapshot()


@resource_router.get("/latency/incidents")
async def resource_latency_incidents(
    response: Response,
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return get_latency_monitor().incidents(limit=limit)


@resource_router.get("/focus")
async def resource_focus(response: Response) -> dict[str, Any]:
    """Recent console/browser foreground transitions with session ownership."""

    response.headers["Cache-Control"] = "no-store"
    return get_focus_monitor().snapshot()
