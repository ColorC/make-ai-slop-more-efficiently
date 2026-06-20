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
import logging

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from omnicompany.dashboard.ccdaemon import lifecycle

logger = logging.getLogger(__name__)


boss_sight_proxy_router = APIRouter(prefix="/api/boss-sight", tags=["boss-sight-proxy"])


def _daemon_http_base() -> str:
    s = lifecycle.read_status()
    if not (s.alive and s.port):
        raise HTTPException(
            status_code=503,
            detail="ccdaemon not running. Start it with `omni cc daemon start`.",
        )
    return f"http://127.0.0.1:{s.port}"


def _daemon_ws_base() -> str | None:
    s = lifecycle.read_status()
    if not (s.alive and s.port):
        return None
    return f"ws://127.0.0.1:{s.port}"


_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
}


def _filter_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


# ── WebSocket 透传 (catch-all 之前注册, 否则被 HTTP catch-all 抢) ─────────
@boss_sight_proxy_router.websocket("/reviewstage/stream")
async def reviewstage_stream_proxy(client_ws: WebSocket) -> None:
    """实时审阅台事件流. 透传到 daemon /api/boss-sight/reviewstage/stream."""
    base = _daemon_ws_base()
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
        )
    return _proxy_client


# ── HTTP 透传 (catch-all) ──────────────────────────────────────────────
@boss_sight_proxy_router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def http_proxy(path: str, request: Request) -> Response:
    base = _daemon_http_base()
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

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filter_headers(dict(upstream.headers)),
        media_type=upstream.headers.get("content-type"),
    )


__all__ = ["boss_sight_proxy_router"]
