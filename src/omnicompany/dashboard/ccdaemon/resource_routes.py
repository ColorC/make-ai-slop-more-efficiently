"""Read-only HTTP surface for resource-monitor evidence."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from .resource_monitor import get_resource_monitor, read_jsonl_tail

resource_router = APIRouter(prefix="/cc/resources", tags=["resource-monitor"])


@resource_router.get("/latest")
async def resource_latest() -> dict[str, Any]:
    return get_resource_monitor().read_latest()


@resource_router.get("/status")
async def resource_status() -> dict[str, Any]:
    return get_resource_monitor().status()


@resource_router.get("/history")
async def resource_history(
    limit: int = Query(default=60, ge=1, le=500),
) -> dict[str, Any]:
    monitor = get_resource_monitor()
    return {
        "items": read_jsonl_tail(monitor.data_dir / "history.jsonl", limit),
        "read_only": True,
        "automatic_cleanup_allowed": False,
    }


@resource_router.get("/alerts")
async def resource_alerts(
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    monitor = get_resource_monitor()
    return {
        "items": read_jsonl_tail(monitor.data_dir / "alerts.jsonl", limit),
        "read_only": True,
        "automatic_cleanup_allowed": False,
    }
