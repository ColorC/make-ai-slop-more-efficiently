# [OMNI] origin=ai-ide ts=2026-08-18 type=infra
"""controlplane/game_observatory_proxy.py — game_observatory 域反代（DOMAIN-AUTONOMY-P1 A2）。

域主体已出背板为独立托管服务（127.0.0.1:8213, 见 game_observatory_service.py），
8210 上只留这层薄反代，对外路径与行为不变：

    /api/game-observatory/*  →  127.0.0.1:8213（同路径）
    /game-observatory/*      →  127.0.0.1:8213（同路径; 页面与静态资源）
    /robots.txt              →  127.0.0.1:8213（该路由历史上就由本域提供）

注意:
- /game-observatory/live-media/* 属 lofa_proxy（注册在前, 不经本反代）, 勿收编。
- 上游有无限流端点(ai-player live 的 stream.mjpg、gateway/events), 必须流式透传,
  禁止整包缓冲; read 超时必须为 None。
- lan_access 匿名白名单在 dashboard 侧按路径判定, 反代不改路径 = 白名单零改动。
- 上游不活时回 502(纯文本), 不抛 500; 域进程崩溃只黑这一面, 不拖垮 8210。
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

import httpx
import websockets
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketState

logger = logging.getLogger("omnicompany.game_observatory_proxy")

game_observatory_proxy_router = APIRouter(tags=["game-observatory-proxy"])

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
# content-length/transfer-encoding 交给 starlette 重算; content-encoding 保留
# (aiter_raw 透传的是未解压原始字节)。
_HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade",
        "proxy-authenticate", "proxy-authorization", "te", "trailers", "content-length"}

_client: httpx.AsyncClient | None = None


def _upstream_base() -> str:
    from omnicompany.dashboard.controlplane.game_observatory_service import service_url

    return service_url()


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=None, write=60.0, pool=5.0),
            follow_redirects=False,
            trust_env=False,  # 别让系统代理截胡 127.0.0.1 内部口
        )
    return _client


async def _proxy(request: Request, path: str) -> Response:
    url = _upstream_base() + path
    if request.url.query:
        url += "?" + request.url.query
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    body = await request.body()
    client = _get_client()
    try:
        upstream_req = client.build_request(request.method, url, content=body, headers=headers)
        upstream = await client.send(upstream_req, stream=True)
    except Exception as error:  # noqa: BLE001 — 上游死 = 502, 不能变全站 500
        return Response(
            content=f"game-observatory upstream error: {error}".encode(),
            status_code=502, media_type="text/plain")
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP}
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
        background=BackgroundTask(upstream.aclose),
    )


@game_observatory_proxy_router.api_route("/api/game-observatory/{path:path}", methods=_METHODS)
async def _api_proxy(path: str, request: Request):
    return await _proxy(request, "/api/game-observatory/" + path)


@game_observatory_proxy_router.api_route("/game-observatory", methods=["GET", "HEAD"])
async def _root_proxy(request: Request):
    return await _proxy(request, "/game-observatory")


@game_observatory_proxy_router.api_route("/game-observatory/{path:path}", methods=_METHODS)
async def _pages_proxy(path: str, request: Request):
    return await _proxy(request, "/game-observatory/" + path)


@game_observatory_proxy_router.api_route("/robots.txt", methods=["GET", "HEAD"])
async def _robots_proxy(request: Request):
    return await _proxy(request, "/robots.txt")


def _same_origin(ws: WebSocket) -> bool:
    """Allow browser same-origin sockets while retaining native-client access."""
    origin = ws.headers.get("origin")
    if not origin:
        return True
    expected = ws.headers.get("x-forwarded-host") or ws.headers.get("host") or ""
    parsed = urlsplit(origin)
    return bool(parsed.netloc) and parsed.netloc.casefold() == expected.casefold()


async def _ws_proxy(path: str, ws: WebSocket) -> None:
    if not _same_origin(ws):
        await ws.close(code=1008, reason="cross-origin websocket")
        return
    await ws.accept()
    upstream_uri = _upstream_base().replace("http://", "ws://", 1) + path
    if ws.url.query:
        upstream_uri += "?" + ws.url.query
    upstream = None
    try:
        upstream = await websockets.connect(
            upstream_uri, max_size=None, open_timeout=10,
            compression=None if path.endswith("/video") else "deflate",
        )

        async def client_to_upstream() -> None:
            while True:
                message = await ws.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                if message.get("bytes") is not None:
                    await upstream.send(message["bytes"])
                elif message.get("text") is not None:
                    await upstream.send(message["text"])

        async def upstream_to_client() -> None:
            async for message in upstream:
                if isinstance(message, (bytes, bytearray)):
                    await ws.send_bytes(bytes(message))
                else:
                    await ws.send_text(message)

        tasks = {asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())}
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    except (WebSocketDisconnect, websockets.WebSocketException, ConnectionError, OSError):
        pass
    finally:
        if upstream is not None:
            await upstream.close()
        if ws.client_state != WebSocketState.DISCONNECTED:
            await ws.close()


@game_observatory_proxy_router.websocket("/api/game-observatory/play/video")
async def _play_video_ws(ws: WebSocket) -> None:
    await _ws_proxy("/api/game-observatory/play/video", ws)


@game_observatory_proxy_router.websocket("/api/game-observatory/play/input")
async def _play_input_ws(ws: WebSocket) -> None:
    await _ws_proxy("/api/game-observatory/play/input", ws)
