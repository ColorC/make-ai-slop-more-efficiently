# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.controlplane.cc_proxy.reverse_proxy_router.py"
"""controlplane/cc_proxy.py — Claude Code 反向代理路由.

dashboard 主进程 (8200) 上挂的代理, 把所有 `/api/cc/*` 前缀转到 ccdaemon
进程 (默认 8201, 实际端口由 `data/cc_daemon.port` 决定). HTTP 跟 WebSocket
都透传, 浏览器无感知.

核心约束 ([2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE D2):
- dashboard 进程**不**装载 chat / pty 真业务路由, 全部走代理. 这样 dashboard
  开 --reload 自动 reload 时不会触发 chat 会话断 (会话由 ccdaemon 进程持有).
- daemon 不在跑时, HTTP 端点返 503 + 提示 `omni cc daemon start`; WebSocket
  连立即关闭 (code 1011).
- 代理本身无状态, dashboard reload 时仅断开 WS 桥接 task, 浏览器走自动重连协议
  恢复 (前端 wsAutoReconnect.ts 接管).
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


cc_proxy_router = APIRouter(prefix="/api/cc", tags=["cc-proxy"])

_PTY_CLIENT_PROTOCOL = "focused-visible-v1"
_LOFA_WEBVIEW_ORIGINS = frozenset({
    "http://localhost",
    "https://localhost",
    "capacitor://localhost",
    "ionic://localhost",
})


def _browser_client_needs_upgrade(
    *,
    origin: str | None,
    client_protocol: str | None,
    user_agent: str | None = None,
) -> bool:
    """Return True only for an outdated browser Dashboard PTY client.

    Browser WebSockets carry Origin. CLI probes and native clients generally do
    not, so they remain protocol-compatible without a query marker. LOFA 1.0.49
    already implements the focused-visible snapshot protocol but omitted the
    query marker; accept only its local App origin plus Android WebView UA while
    deployed clients move to an explicit marker.
    """
    if not origin or client_protocol == _PTY_CLIENT_PROTOCOL:
        return False
    is_lofa_webview = (
        origin in _LOFA_WEBVIEW_ORIGINS
        and "Android" in (user_agent or "")
        and "; wv)" in (user_agent or "")
    )
    return not is_lofa_webview


def _daemon_http_base() -> str:
    """读 daemon 状态 → http://127.0.0.1:<port>; 没活就 503."""
    s = lifecycle.read_status(probe=False)  # 热路径: 不在每次代理请求时打 /health
    if not (s.alive and s.port):
        raise HTTPException(
            status_code=503,
            detail="ccdaemon not running. Start it with `omni cc daemon start`.",
        )
    return f"http://127.0.0.1:{s.port}"


def _daemon_ws_base() -> str | None:
    """daemon 不活时返 None, 让 WebSocket 端点 close(1011) 而非抛 HTTPException."""
    s = lifecycle.read_status(probe=False)  # 热路径: 不在每次代理请求时打 /health
    if not (s.alive and s.port):
        return None
    return f"ws://127.0.0.1:{s.port}"


# 透传时 hop-by-hop headers 不该转 (HTTP/1.1 RFC 7230 §6.1).
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
}


def _filter_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


_HTTP_CLIENT: httpx.AsyncClient | None = None
_HTTP_CLIENT_LOOP: asyncio.AbstractEventLoop | None = None


async def _daemon_http_client() -> httpx.AsyncClient:
    """Reuse the loopback connection instead of paying client setup per request."""
    global _HTTP_CLIENT, _HTTP_CLIENT_LOOP

    loop = asyncio.get_running_loop()
    if (
        _HTTP_CLIENT is None
        or _HTTP_CLIENT.is_closed
        or _HTTP_CLIENT_LOOP is not loop
    ):
        previous = _HTTP_CLIENT
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=120.0),
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=60.0,
            ),
            trust_env=False,
        )
        _HTTP_CLIENT_LOOP = loop
        if previous is not None and not previous.is_closed:
            try:
                await previous.aclose()
            except RuntimeError:
                # A test server may already have closed the loop that owned it.
                pass
    return _HTTP_CLIENT


# ── chatui 自启动 ensure (非透传, 必须在下面 catch-all 之前注册) ──────────────
# 前端 /chat-standalone 跳转到收编 chatui(:7348)之前先打这个端点; chatui 没起就
# 在这里拉起来(ensure_chatui_running 内部 spawn + 轮询 ready), 避免落"连接被拒"死页。
# 注意: 必须定义在 catch-all `/{path:path}` 之前 — FastAPI 按定义顺序匹配, 否则
# /chatui/ensure 会被当作 /api/cc/{path} 透传给 ccdaemon(那边没这路由 → 404/503)。
@cc_proxy_router.post("/chatui/ensure")
async def chatui_ensure() -> Response:
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import JSONResponse

    # 懒导入: 避免 controlplane 导入期拉起整个 CLI click 树。
    from omnicompany.cli.commands.cc import ensure_chatui_running
    res = await run_in_threadpool(ensure_chatui_running)
    return JSONResponse(res)


# ── HTTP 透传 (catch-all) ──────────────────────────────────────────────────
@cc_proxy_router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def http_proxy(path: str, request: Request) -> Response:
    """所有 /api/cc/{path} HTTP 请求转 daemon /cc/{path}, 透传 method / headers / body / query."""
    base = _daemon_http_base()
    target = f"{base}/cc/{path}"
    body = await request.body()

    client = await _daemon_http_client()
    try:
        upstream = await client.request(
            method=request.method,
            url=target,
            headers=_filter_headers(dict(request.headers)),
            params=request.query_params,
            content=body,
        )
    except httpx.ConnectError as e:
        # daemon pid/port 文件说活但 connect 失败 (启动中 / 刚崩)
        raise HTTPException(status_code=503, detail=f"ccdaemon unreachable: {e}")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"ccdaemon proxy error: {e}")

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filter_headers(dict(upstream.headers)),
        media_type=upstream.headers.get("content-type"),
    )


# ── WebSocket 透传 ──────────────────────────────────────────────────────────
async def _ws_bridge(
    client_ws: WebSocket,
    daemon_path: str,
    *,
    enforce_frontend_protocol: bool = False,
) -> None:
    """双向桥接: 浏览器 WS ↔ dashboard ↔ daemon WS.

    任一侧关闭则关闭另一侧. dashboard reload 时本 task 被 cancelled, daemon
    侧收到 disconnect 但内部 session 不动 (浏览器跟 daemon 间会话状态由 daemon
    持有, dashboard 只是穿透).
    """
    base = _daemon_ws_base()
    if base is None:
        await client_ws.accept()
        await client_ws.close(code=1011, reason="ccdaemon not running")
        return

    target = f"{base}{daemon_path}"
    await client_ws.accept()
    if enforce_frontend_protocol and _browser_client_needs_upgrade(
        origin=client_ws.headers.get("origin"),
        client_protocol=client_ws.query_params.get("client_protocol"),
        user_agent=client_ws.headers.get("user-agent"),
    ):
        # The legacy UI's update guard defers reload while entity.alive is true.
        # Mark only its local view ended; the daemon/PTY keeps running. On its
        # next 3-second update tick the page reloads and reconnects with the
        # current protocol marker.
        await client_ws.send_json({
            "type": "exit",
            "reason": "Dashboard UI updated; reloading",
        })
        await client_ws.close(code=1012, reason="Dashboard UI update required")
        return
    upstream_close_code = 1012
    upstream_close_reason = "PTY proxy reconnect"

    try:
        async with websockets.connect(target, max_size=None) as upstream:
            async def browser_to_daemon() -> None:
                try:
                    while True:
                        # 浏览器侧可能发文本帧或二进制帧
                        msg = await client_ws.receive()
                        if msg["type"] == "websocket.disconnect":
                            return
                        if "text" in msg and msg["text"] is not None:
                            await upstream.send(msg["text"])
                        elif "bytes" in msg and msg["bytes"] is not None:
                            await upstream.send(msg["bytes"])
                except WebSocketDisconnect:
                    return

            async def daemon_to_browser() -> None:
                nonlocal upstream_close_code, upstream_close_reason
                try:
                    async for msg in upstream:
                        if client_ws.client_state != WebSocketState.CONNECTED:
                            return
                        if isinstance(msg, str):
                            await client_ws.send_text(msg)
                        else:
                            await client_ws.send_bytes(msg)
                    close_code = getattr(upstream, "close_code", None)
                    close_reason = getattr(upstream, "close_reason", None)
                    if isinstance(close_code, int):
                        upstream_close_code = close_code
                    if close_reason:
                        upstream_close_reason = str(close_reason)
                except websockets.ConnectionClosed as exc:
                    code = int(getattr(exc, "code", 1012) or 1012)
                    # 1006 is a receive-side sentinel and cannot be sent in a
                    # close frame. Treat every abnormal upstream loss as a
                    # restartable proxy interruption.
                    upstream_close_code = code if code != 1006 else 1012
                    upstream_close_reason = str(getattr(exc, "reason", "") or "PTY proxy reconnect")
                    return

            bridge_tasks = [
                asyncio.create_task(browser_to_daemon()),
                asyncio.create_task(daemon_to_browser()),
            ]
            _done, pending = await asyncio.wait(
                bridge_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            # Always retrieve both task results. Otherwise an upstream close
            # racing a browser send can leave "Task exception was never
            # retrieved" warnings and retain traceback/frame references during
            # long multi-browser runs.
            results = await asyncio.gather(*bridge_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, asyncio.CancelledError):
                    continue
                if isinstance(result, BaseException):
                    logger.warning(
                        "cc_proxy ws direction ended unexpectedly for %s: %s",
                        daemon_path,
                        result,
                    )
    except (websockets.WebSocketException, ConnectionRefusedError, OSError) as e:
        logger.warning("cc_proxy ws bridge failed for %s: %s", daemon_path, e)
    finally:
        if client_ws.client_state == WebSocketState.CONNECTED:
            try:
                await client_ws.close(
                    code=upstream_close_code,
                    reason=upstream_close_reason[:123],
                )
            except Exception:
                pass


@cc_proxy_router.websocket("/chat/sessions/{sid}/ws")
async def chat_ws_proxy(ws: WebSocket, sid: str) -> None:
    await _ws_bridge(ws, f"/cc/chat/sessions/{sid}/ws")


@cc_proxy_router.websocket("/sessions/{sid}/ws")
async def pty_ws_proxy(ws: WebSocket, sid: str) -> None:
    await _ws_bridge(
        ws,
        f"/cc/sessions/{sid}/ws",
        enforce_frontend_protocol=True,
    )


@cc_proxy_router.websocket("/echo")
async def echo_ws_proxy(ws: WebSocket) -> None:
    """阶段二骨架基线测试用. 浏览器连上后跟 daemon 跑 echo 双向 RTT 基线."""
    await _ws_bridge(ws, "/cc/echo")
