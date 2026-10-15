# [OMNI] origin=ai-ide ts=2026-05-24 type=infra
# [OMNI] material_id="material:dashboard.controlplane.boss_sight_proxy.reverse_proxy.py"
"""controlplane/boss_sight_proxy.py — BOSS SIGHT 反向代理.

dashboard 主进程 (8200) 上挂的代理, 把所有 `/api/boss-sight/*` 转到 ccdaemon
(8201). 跟 cc_proxy 同套设计:
- HTTP + WebSocket 透传
- daemon 不活时 HTTP 503 / WS code 1011

为什么要这层: 块 1-4 的 BOSS SIGHT 全部 API + WS 都跑在 ccdaemon 上 (controller
worker / waker / reviewstage hub 都是 ccdaemon 进程内的 in-memory state).
dashboard 进程开 --reload 时不希望这些状态被重置, 所以走代理穿透.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

import anyio.to_process
import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from omnicompany.dashboard.ccdaemon import lifecycle
from omnicompany.packages.services.boss_sight.reviewstage.links import review_material_open_path

logger = logging.getLogger(__name__)


boss_sight_proxy_router = APIRouter(prefix="/api/boss-sight", tags=["boss-sight-proxy"])


def _daemon_http_base() -> str:
    s = lifecycle.read_status(probe=False)  # 热路径: 不在每次代理请求时打 /health
    if not (s.alive and s.port):
        raise HTTPException(
            status_code=503,
            detail="ccdaemon not running. Start it with `omni cc daemon start`.",
        )
    return f"http://127.0.0.1:{s.port}"


def _daemon_ws_base() -> str | None:
    s = lifecycle.read_status(probe=False)  # 热路径: 不在每次代理请求时打 /health
    if not (s.alive and s.port):
        return None
    return f"ws://127.0.0.1:{s.port}"


_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


_workshop_token_cache: dict[str, str | None] = {"at": "", "token": None}


def _workshop_token() -> str | None:
    """workshop 写接口要 Bearer token(data/token.txt, 本机文件)。

    浏览器侧消费方(审阅台裁决等)不带凭证 —— 代理统一注入; 单机工作区前提下
    dashboard 后端与 workshop 同机, 读该文件即合法领取。带缓存, 文件丢失返回 None。
    """
    from pathlib import Path
    import time as _time
    path = Path(__file__).resolve().parents[5] / "spark.workshop" / "workshop" / "data" / "token.txt"
    now = _time.time()
    if _workshop_token_cache["at"] and now - float(_workshop_token_cache["at"]) < 300:
        return _workshop_token_cache["token"]
    token: str | None = None
    try:
        token = path.read_text(encoding="utf-8").strip() or None
    except OSError:
        token = None
    _workshop_token_cache.update({"at": str(now), "token": token})
    return token


def _filter_headers(headers: dict[str, str]) -> dict[str, str]:
    forwarded = {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}
    # Host 属逐跳头被剥掉, 但捕获 URL 等产物需要浏览器真正的访问口(远程办公是
    # 192.0.2.10:8210 而非内环 8201)。按标准转发语义补 X-Forwarded-Host。
    original_host = headers.get("host")
    if original_host:
        forwarded["x-forwarded-host"] = original_host
    # 写接口(POST/PUT/PATCH/DELETE)统一补 workshop Bearer(浏览器消费方无凭证)。
    if "authorization" not in {k.lower() for k in forwarded}:
        token = _workshop_token()
        if token:
            forwarded["authorization"] = f"Bearer {token}"
    return forwarded


_REVIEWSTAGE_FILE_PATH = re.compile(r"reviewstage/([^/]+)/file")


def _reviewstage_markdown_navigation_id(
    path: str,
    *,
    method: str,
    sec_fetch_dest: str | None,
    raw: str | None,
    status_code: int,
    content_type: str | None,
) -> str | None:
    """Recognize a human navigation to a Markdown material carrier.

    A current ccdaemon redirects this request itself.  Keeping the same narrow
    check in the Dashboard proxy also protects a live daemon that has not yet
    been restarted: renderer fetches and explicit raw-source requests must
    continue to receive the carrier bytes, while a browser document navigation
    should enter the native Review material tab.
    """

    if method.upper() != "GET" or (sec_fetch_dest or "").lower() != "document":
        return None
    if raw is not None or status_code != 200:
        return None
    if not (content_type or "").lower().startswith("text/markdown"):
        return None
    match = _REVIEWSTAGE_FILE_PATH.fullmatch(path.strip("/"))
    return match.group(1) if match else None


# ── WebSocket 透传 (catch-all 之前注册, 否则被 HTTP catch-all 抢) ─────────
@boss_sight_proxy_router.websocket("/reviewstage/stream")
async def reviewstage_stream_proxy(client_ws: WebSocket) -> None:
    """实时审阅台事件流. 透传到 daemon /api/boss-sight/reviewstage/stream."""
    base = await asyncio.to_thread(_daemon_ws_base)
    if base is None:
        await client_ws.accept()
        await client_ws.close(code=1011, reason="ccdaemon not running")
        return

    target = f"{base}/api/boss-sight/reviewstage/stream"
    await client_ws.accept()

    try:
        async with websockets.connect(target, max_size=None) as upstream:

            async def browser_to_daemon() -> None:
                try:
                    while True:
                        msg = await client_ws.receive()
                        if msg["type"] == "websocket.disconnect":
                            return
                        if "text" in msg and msg["text"] is not None:
                            await upstream.send(msg["text"])
                        elif "bytes" in msg and msg["bytes"] is not None:
                            await upstream.send(msg["bytes"])
                except WebSocketDisconnect:
                    return
                except Exception:
                    return

            async def daemon_to_browser() -> None:
                try:
                    async for msg in upstream:
                        if client_ws.client_state == WebSocketState.DISCONNECTED:
                            return
                        if isinstance(msg, bytes):
                            await client_ws.send_bytes(msg)
                        else:
                            await client_ws.send_text(msg)
                except websockets.ConnectionClosed:
                    return
                except Exception:
                    return

            t1 = asyncio.create_task(browser_to_daemon())
            t2 = asyncio.create_task(daemon_to_browser())
            done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()
    except Exception as e:  # noqa: BLE001
        logger.warning("reviewstage WS bridge error: %s", e)
    finally:
        if client_ws.client_state != WebSocketState.DISCONNECTED:
            try:
                await client_ws.close()
            except Exception:
                pass


# 共享 httpx 客户端: 原来每个代理请求都新建 AsyncClient(含创建 SSL context, 很贵, stackdump 抓到)。
# 复用单例 + keep-alive 连接池, 对齐 app.py 里 walker/vilo 代理写法。
_proxy_client: "httpx.AsyncClient | None" = None


def _get_proxy_client() -> httpx.AsyncClient:
    global _proxy_client
    if _proxy_client is None:
        _proxy_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=120.0),
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64),
            verify=False,
            trust_env=False,
        )
    return _proxy_client


# Plan discovery is read-only Dashboard metadata, not CLI control-plane state.
# Keep it on 8210 so an expensive filesystem scan can never stall the 8201 PTY
# event loop.  This also lets us deploy the isolation without restarting the
# long-lived daemon (and therefore without disrupting any live CLI session).
_PLAN_PROXY_CACHE_TTL = max(
    5.0,
    float(os.environ.get("OMNI_PLAN_INDEX_CACHE_TTL_S") or 30.0),
)
_PLAN_PROXY_CACHE: dict[str, Any] = {
    "payload": None,
    "expires_at": 0.0,
    "task": None,
}


def _build_local_plan_snapshot() -> dict[str, Any]:
    # Delay the heavier BOSS SIGHT imports until the worker thread is running;
    # even first-use module loading must not occupy the Dashboard event loop.
    from omnicompany.packages.services.boss_sight.routes import _build_plan_index_payload

    return _build_plan_index_payload()


async def _refresh_local_plan_snapshot() -> dict[str, Any]:
    previous = _PLAN_PROXY_CACHE.get("payload")
    try:
        payload = await asyncio.to_thread(_build_local_plan_snapshot)
        _PLAN_PROXY_CACHE["payload"] = payload
        _PLAN_PROXY_CACHE["expires_at"] = time.monotonic() + _PLAN_PROXY_CACHE_TTL
        return payload
    except Exception:
        if previous is not None:
            _PLAN_PROXY_CACHE["expires_at"] = time.monotonic() + 5.0
            return previous
        raise
    finally:
        if _PLAN_PROXY_CACHE.get("task") is asyncio.current_task():
            _PLAN_PROXY_CACHE["task"] = None


def _ensure_local_plan_refresh() -> asyncio.Task[dict[str, Any]]:
    task = _PLAN_PROXY_CACHE.get("task")
    if task is None or task.done():
        task = asyncio.create_task(
            _refresh_local_plan_snapshot(),
            name="dashboard-local-plan-index-refresh",
        )
        _PLAN_PROXY_CACHE["task"] = task
    return task


@boss_sight_proxy_router.get("/insights/tokens")
async def local_token_insights(force: bool = False) -> dict[str, Any]:
    """Keep the expensive token rebuild on Dashboard, without restarting PTYs."""
    from omnicompany.dashboard.boss_sight.routes import insights_tokens

    return await insights_tokens(force=force)


@boss_sight_proxy_router.post("/insights/tokens/refresh")
async def local_token_insights_refresh() -> dict[str, Any]:
    from omnicompany.dashboard.boss_sight.routes import refresh_insights_tokens

    return await refresh_insights_tokens()


@boss_sight_proxy_router.get("/plans")
async def local_plan_index() -> dict[str, Any]:
    """Serve plan decoration locally; never queue it on the PTY daemon."""
    payload = _PLAN_PROXY_CACHE.get("payload")
    if payload is not None and time.monotonic() < _PLAN_PROXY_CACHE["expires_at"]:
        return payload

    task = _ensure_local_plan_refresh()
    if payload is not None:
        return payload
    return await asyncio.shield(task)


# The briefing builder parses enough Python metadata to retain the GIL for
# several seconds. Running it in a thread still starves ccdaemon's PTY loop.
# Dashboard is the public consumer, so serve this one expensive read locally
# from AnyIO's bounded worker-process pool and collapse duplicate page loads.
_BRIEFING_PROXY_CACHE_TTL = max(
    5.0,
    float(os.environ.get("OMNI_BOSS_SIGHT_BRIEFING_CACHE_TTL_S") or 300.0),
)
_BRIEFING_PROXY_CACHE: dict[str, Any] = {
    "payload": None,
    "expires_at": 0.0,
    "task": None,
    "persisted_loaded": False,
}


def _build_local_briefing_snapshot() -> dict[str, Any]:
    from omnicompany.packages.services.boss_sight.routes import _build_briefing_snapshot

    payload = _build_briefing_snapshot()
    payload["served_by"] = "dashboard-process-isolated"
    return payload


def _briefing_proxy_cache_path():
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[4]
        / "data"
        / "services"
        / "dashboard"
        / "briefing-cache.json"
    )


def _load_persisted_briefing_snapshot() -> dict[str, Any] | None:
    try:
        envelope = json.loads(_briefing_proxy_cache_path().read_text(encoding="utf-8"))
        payload = envelope.get("payload")
        saved_at = float(envelope.get("saved_at") or 0.0)
        if envelope.get("schema") != "omni.dashboard-briefing-cache.v1":
            return None
        if not isinstance(payload, dict) or time.time() - saved_at > 7 * 86400:
            return None
        return payload
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _persist_local_briefing_snapshot(payload: dict[str, Any]) -> None:
    path = _briefing_proxy_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    envelope = {
        "schema": "omni.dashboard-briefing-cache.v1",
        "saved_at": time.time(),
        "payload": payload,
    }
    temp.write_text(
        json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temp.replace(path)


async def _refresh_local_briefing_snapshot() -> dict[str, Any]:
    previous = _BRIEFING_PROXY_CACHE.get("payload")
    try:
        payload = await anyio.to_process.run_sync(_build_local_briefing_snapshot)
        await asyncio.to_thread(_persist_local_briefing_snapshot, payload)
        _BRIEFING_PROXY_CACHE.update(
            payload=payload,
            expires_at=time.monotonic() + _BRIEFING_PROXY_CACHE_TTL,
        )
        return payload
    except Exception:
        if previous is not None:
            _BRIEFING_PROXY_CACHE["expires_at"] = time.monotonic() + 5.0
            return previous
        raise
    finally:
        if _BRIEFING_PROXY_CACHE.get("task") is asyncio.current_task():
            _BRIEFING_PROXY_CACHE["task"] = None


def _ensure_local_briefing_refresh() -> asyncio.Task[dict[str, Any]]:
    task = _BRIEFING_PROXY_CACHE.get("task")
    if task is None or task.done():
        task = asyncio.create_task(
            _refresh_local_briefing_snapshot(),
            name="dashboard-local-briefing-refresh",
        )
        _BRIEFING_PROXY_CACHE["task"] = task
    return task


@boss_sight_proxy_router.get("/briefing")
async def local_briefing_proxy() -> dict[str, Any]:
    if not _BRIEFING_PROXY_CACHE["persisted_loaded"]:
        _BRIEFING_PROXY_CACHE["persisted_loaded"] = True
        persisted = await asyncio.to_thread(_load_persisted_briefing_snapshot)
        if persisted is not None:
            _BRIEFING_PROXY_CACHE["payload"] = persisted

    payload = _BRIEFING_PROXY_CACHE.get("payload")
    if payload is not None and time.monotonic() < _BRIEFING_PROXY_CACHE["expires_at"]:
        return payload

    task = _ensure_local_briefing_refresh()
    if payload is not None:
        stale = dict(payload)
        stale["stale_while_revalidate"] = True
        return stale
    return await asyncio.shield(task)


def _overlay_current_resident_bindings(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply current identity rules to a snapshot from a long-lived daemon."""
    from omnicompany.core.projects_registry import list_projects
    from omnicompany.packages.services.boss_sight.services.residents import (
        _canonicalize_project,
        _enrich_binding,
    )
    from omnicompany.packages.services._core.identity import bindings_by_session_key

    residents = payload.get("residents")
    if not isinstance(residents, list):
        return payload
    bindings = bindings_by_session_key()
    projects = list_projects()
    payload = dict(payload)
    payload["residents"] = [
        _enrich_binding(
            _canonicalize_project(dict(resident), projects),
            bindings,
            projects,
        )
        if isinstance(resident, dict)
        else resident
        for resident in residents
    ]
    payload["binding_overlay"] = "dashboard-current"
    return payload


_RESIDENTS_PROXY_CACHE: dict[str, Any] = {
    "payload": None,
    "expires_at": 0.0,
    "task": None,
    "limit": 80,
}


def _build_local_resident_snapshot(limit: int) -> dict[str, Any]:
    from omnicompany.packages.services.boss_sight.services.residents import build_residents

    payload = build_residents(limit=limit)
    payload["served_by"] = "dashboard-local-index"
    return payload


async def _refresh_local_resident_snapshot(limit: int) -> dict[str, Any]:
    try:
        payload = await asyncio.to_thread(_build_local_resident_snapshot, limit)
        _RESIDENTS_PROXY_CACHE.update(
            payload=payload,
            expires_at=time.monotonic() + 5.0,
            limit=limit,
        )
        return payload
    finally:
        if _RESIDENTS_PROXY_CACHE.get("task") is asyncio.current_task():
            _RESIDENTS_PROXY_CACHE["task"] = None


def _ensure_local_resident_refresh(limit: int) -> asyncio.Task[dict[str, Any]]:
    task = _RESIDENTS_PROXY_CACHE.get("task")
    if task is None or task.done():
        task = asyncio.create_task(
            _refresh_local_resident_snapshot(limit),
            name="dashboard-local-resident-index-refresh",
        )
        _RESIDENTS_PROXY_CACHE["task"] = task
    return task


async def _local_resident_index(limit: int = 80) -> dict[str, Any]:
    limit = max(1, min(int(limit), 500))
    payload = _RESIDENTS_PROXY_CACHE.get("payload")
    same_limit = _RESIDENTS_PROXY_CACHE.get("limit") == limit
    if (
        payload is not None
        and same_limit
        and time.monotonic() < _RESIDENTS_PROXY_CACHE["expires_at"]
    ):
        return payload

    task = _ensure_local_resident_refresh(limit)
    if payload is not None and same_limit:
        stale = dict(payload)
        stale["stale_while_revalidate"] = True
        return stale
    return await asyncio.shield(task)


@boss_sight_proxy_router.get("/residents")
async def current_residents_proxy(request: Request) -> dict[str, Any]:
    """Keep discovery work off the interactive PTY daemon."""
    try:
        limit = int(request.query_params.get("limit") or 80)
    except (TypeError, ValueError):
        limit = 80
    return await _local_resident_index(limit)


# ── HTTP 透传 (catch-all) ──────────────────────────────────────────────
@boss_sight_proxy_router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def http_proxy(path: str, request: Request) -> Response:
    base = await asyncio.to_thread(_daemon_http_base)
    target = f"{base}/api/boss-sight/{path}"
    body = await request.body()

    client = _get_proxy_client()
    try:
        upstream = await client.request(
            method=request.method,
            url=target,
            headers=_filter_headers(dict(request.headers)),
            params=request.query_params,
            content=body,
        )
    except httpx.ConnectError as e:
        raise HTTPException(status_code=503, detail=f"ccdaemon unreachable: {e}")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"ccdaemon proxy error: {e}")

    material_id = _reviewstage_markdown_navigation_id(
        path,
        method=request.method,
        sec_fetch_dest=request.headers.get("sec-fetch-dest"),
        raw=request.query_params.get("raw"),
        status_code=upstream.status_code,
        content_type=upstream.headers.get("content-type"),
    )
    if material_id is not None and "download" not in request.query_params:
        return Response(
            status_code=307,
            headers={"location": review_material_open_path(material_id)},
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filter_headers(dict(upstream.headers)),
        media_type=upstream.headers.get("content-type"),
    )


__all__ = ["boss_sight_proxy_router", "_reviewstage_markdown_navigation_id"]
