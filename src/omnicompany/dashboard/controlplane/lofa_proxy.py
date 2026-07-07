# [OMNI] origin=ai-ide ts=2026-06-28 type=infra
"""controlplane/lofa_proxy.py — 把 LOFA 对外要用的两个本地工具反代到 dashboard(8210)下。

对外只暴露 8210 一个口:手机/平板用 app 连的同一个地址就能看「实机操作台」镜像,
不必再各自暴露 8770/8781,也不必各开防火墙。

    /lofa/devview/*  → 127.0.0.1:8770/*   (HTTP: 控制台页 / 切分辨率 /dev/size 等)
    /lofa/scrcpy/*   → 127.0.0.1:8781/*   (HTTP: ws-scrcpy SPA 与静态资源)
    /lofa/scrcpy/*   → 127.0.0.1:8781/*   (WebSocket: H264 实时流 / 设备追踪)

ws-scrcpy 用相对资源(bundle.js/main.css)且按 location 构造 ws,所以挂在子路径下可用。
"""
from __future__ import annotations

import asyncio

import httpx
import websockets
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import Response

lofa_proxy_router = APIRouter(tags=["lofa-proxy"])

_UPSTREAM = {"devview": "127.0.0.1:8770", "scrcpy": "127.0.0.1:8781"}
_HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade", "proxy-authenticate",
        "proxy-authorization", "te", "trailers", "content-encoding", "content-length"}


async def _http_proxy(which: str, path: str, request: Request) -> Response:
    host = _UPSTREAM[which]
    url = f"http://{host}/{path}"
    if request.url.query:
        url += "?" + request.url.query
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            r = await client.request(request.method, url, content=body, headers=headers)
    except Exception as e:  # noqa: BLE001
        return Response(content=f"lofa-proxy upstream error: {e}".encode(), status_code=502)
    resp_headers = {k: v for k, v in r.headers.items() if k.lower() not in _HOP}
    return Response(content=r.content, status_code=r.status_code, headers=resp_headers,
                    media_type=r.headers.get("content-type"))


@lofa_proxy_router.api_route("/lofa/devview/{path:path}",
                             methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def _devview_http(path: str, request: Request):
    return await _http_proxy("devview", path, request)


@lofa_proxy_router.api_route("/lofa/scrcpy/{path:path}",
                             methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def _scrcpy_http(path: str, request: Request):
    return await _http_proxy("scrcpy", path, request)


async def _ws_proxy(which: str, path: str, ws: WebSocket) -> None:
    host = _UPSTREAM[which]
    q = ws.url.query
    up_uri = f"ws://{host}/{path}" + (f"?{q}" if q else "")
    await ws.accept()
    try:
        upstream = await websockets.connect(up_uri, max_size=None, open_timeout=10)
    except Exception:  # noqa: BLE001
        try:
            await ws.close()
        except Exception:
            pass
        return

    async def c2u() -> None:
        try:
            while True:
                m = await ws.receive()
                if m.get("type") == "websocket.disconnect":
                    break
                if m.get("bytes") is not None:
                    await upstream.send(m["bytes"])
                elif m.get("text") is not None:
                    await upstream.send(m["text"])
        except Exception:  # noqa: BLE001
            pass

    async def u2c() -> None:
        try:
            async for m in upstream:
                if isinstance(m, (bytes, bytearray)):
                    await ws.send_bytes(bytes(m))
                else:
                    await ws.send_text(m)
        except Exception:  # noqa: BLE001
            pass

    t1 = asyncio.create_task(c2u())
    t2 = asyncio.create_task(u2c())
    _, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    try:
        await upstream.close()
    except Exception:
        pass
    try:
        await ws.close()
    except Exception:
        pass


@lofa_proxy_router.websocket("/lofa/scrcpy/{path:path}")
async def _scrcpy_ws(ws: WebSocket, path: str):
    await _ws_proxy("scrcpy", path, ws)
