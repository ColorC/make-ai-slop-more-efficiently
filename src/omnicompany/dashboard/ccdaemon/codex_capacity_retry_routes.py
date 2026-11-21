from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .codex_capacity_retry import disable, retry_status_async, retry_tick

retry_router = APIRouter(prefix="/cc/capacity-retry", tags=["codex-capacity-retry"])


@retry_router.get("")
async def status() -> dict[str, Any]:
    return {"schema": "omnicompany.codex-capacity-retry.v1", "records": await retry_status_async()}


@retry_router.post("/tick")
async def tick() -> dict[str, Any]:
    return await retry_tick()


@retry_router.post("/disable")
async def disable_retry(session_id: str | None = None) -> dict[str, Any]:
    return {"disabled": await disable(session_id)}
