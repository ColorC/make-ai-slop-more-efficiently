# [OMNI] origin=claude-code ts=2026-08-14 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.web_proxy.py"
"""Unified-channel reverse proxy for web-display sessions (DeepSeek Harness Web UI).

The dsh Web UI binds to a loopback port per session (assigned by the OS, see
``dsh-web.py``). Browsers must never learn that port: every request travels the
same channel as the PTY panes —

    browser → dashboard /api/cc/web/<sid>/*  (catch-all)
            → ccdaemon   /cc/web/<sid>/*     (this module)
            → 127.0.0.1:<internal port>/*

Three adaptations make a sub-path mount work although the dsh client hardcodes
origin-root paths (``/api``, ``/plugins/…``, ``/assets/…``):

1. HTML responses get every absolute ref rewritten to the session prefix and a
   small bootstrap shim injected that patches ``fetch`` / ``WebSocket`` /
   ``EventSource`` to prefix those same roots at runtime (the client builds
   them as strings and ``URL`` objects from ``location.origin``).
2. ``Host`` is rewritten to the loopback authority and ``Origin`` is stripped
   before reaching dsh: its browser-trust fence then accepts the request as
   loopback (Origin is absent → the Host fence alone applies, and Host is
   loopback). ``Sec-Fetch-Site`` stays ``same-origin`` because the browser
   only ever talks to the dashboard origin.
3. WebSocket upgrades are bridged the same way; dsh's fence applies the same
   Host/Origin rules and passes.

Trust model: identical to the PTY channel — anyone who can reach the
dashboard's /api/cc can reach these sessions.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from .web_host import get_web_manager

logger = logging.getLogger(__name__)

web_proxy_router = APIRouter(prefix="/cc/web", tags=["cc-web-proxy"])

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
}

# Origin-root paths the dsh client hardcodes; prefixed under the session path.
_PREFIX_ROOTS = ("/api", "/plugins/", "/assets/", "/favicon.svg", "/manifest.webmanifest")


def _filter_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _wanted_path(path: str) -> bool:
    if path in ("/favicon.svg", "/manifest.webmanifest", "/api"):
        return True
    if path.startswith("/api/"):
        return True
    return any(path.startswith(root) for root in ("/plugins/", "/assets/"))


def _prefix(sid: str, text: str) -> str:
    base = f"/api/cc/web/{sid}"
    # string literals inside HTML attributes and the __DSH_BOOT__ JSON
    return text.replace('"/plugins/', f'"{base}/plugins/').replace(
        '"/assets/', f'"{base}/assets/'
    ).replace('"/favicon.svg', f'"{base}/favicon.svg').replace(
        '"/manifest.webmanifest', f'"{base}/manifest.webmanifest'
    )


_SHIM_TEMPLATE = """<script>(function(){
// crypto.randomUUID 只在 secure context(localhost/HTTPS)存在; 远程 LAN http://<ip>
// 访问时 dsh 客户端的 mintRpcId 会直接抛 "crypto.randomUUID is not a function",
// 工作区选择器等所有 RPC 全部瘫痪。这里用 getRandomValues(非安全上下文可用)补上。
if (!window.crypto || typeof window.crypto.randomUUID !== 'function') {
  window.crypto = window.crypto || {};
  window.crypto.randomUUID = function () {
    var bytes = window.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    var hex = [];
    for (var i = 0; i < 16; i++) hex.push((bytes[i] + 256).toString(16).slice(1));
    return hex.slice(0, 4).join('') + '-' + hex.slice(4, 6).join('') + '-' + hex.slice(6, 8).join('') + '-' + hex.slice(8, 10).join('') + '-' + hex.slice(10, 16).join('');
  };
}
var base = "/api/cc/web/{sid}";
function wanted(p) {
  return p === "/favicon.svg" || p === "/manifest.webmanifest"
    || p === "/api" || p.indexOf("/api/") === 0
    || p.indexOf("/plugins/") === 0 || p.indexOf("/assets/") === 0;
}
function rewriteString(s) {
  if (typeof s === "string" && s.charAt(0) === "/") {
    var q = s.indexOf("?"), h = s.indexOf("#");
    var cut = s.length;
    if (q >= 0) cut = Math.min(cut, q);
    if (h >= 0) cut = Math.min(cut, h);
    var path = s.slice(0, cut);
    if (wanted(path)) return base + s;
  }
  return s;
}
function rewriteUrl(u) {
  if (!(u instanceof URL)) return u;
  // 不能比 u.origin: 客户端把 WS 地址的 protocol 换成 ws: 之后 origin 变成
  // ws://host, 与 location.origin(http://host)永不相等。只比 host(:port)。
  if (u.host !== location.host) return u;
  if (wanted(u.pathname)) return new URL(base + u.pathname + u.search + u.hash, location.origin);
  return u;
}
var nativeFetch = window.fetch.bind(window);
window.fetch = function(input, init) {
  try {
    if (typeof input === "string") input = rewriteString(input);
    else if (input instanceof Request) {
      var u = rewriteUrl(new URL(input.url, location.origin));
      if (u.href !== input.url) input = new Request(u, input);
    } else if (input instanceof URL) input = rewriteUrl(input);
  } catch (e) {}
  return nativeFetch(input, init);
};
var NativeWS = window.WebSocket;
var WSShim = function(url, protocols) {
  try { url = typeof url === "string" ? rewriteString(url) : rewriteUrl(url); } catch (e) {}
  return protocols === undefined ? new NativeWS(url) : new NativeWS(url, protocols);
};
WSShim.prototype = NativeWS.prototype;
WSShim.CONNECTING = NativeWS.CONNECTING; WSShim.OPEN = NativeWS.OPEN;
WSShim.CLOSING = NativeWS.CLOSING; WSShim.CLOSED = NativeWS.CLOSED;
window.WebSocket = WSShim;
if (window.EventSource) {
  var NativeES = window.EventSource;
  var ESShim = function(url, init) {
    try { url = typeof url === "string" ? rewriteString(url) : String(url); } catch (e) {}
    return init === undefined ? new NativeES(url) : new NativeES(url, init);
  };
  ESShim.prototype = NativeES.prototype;
  ESShim.CONNECTING = NativeES.CONNECTING; ESShim.OPEN = NativeES.OPEN; ESShim.CLOSED = NativeES.CLOSED;
  window.EventSource = ESShim;
}
})();</script>"""

_HEAD_RE = re.compile(r"<head[^>]*>", re.IGNORECASE)


def _adapt_html(sid: str, body: bytes) -> bytes:
    """Rewrite origin-root refs and inject the runtime URL-rewrite shim."""
    text = body.decode("utf-8", errors="replace")
    text = _prefix(sid, text)
    shim = _SHIM_TEMPLATE.replace("{sid}", sid)
    head_match = _HEAD_RE.search(text)
    if head_match:
        text = text[: head_match.end()] + shim + text[head_match.end():]
    else:
        text = shim + text
    return text.encode("utf-8")


_HTTP_CLIENT: httpx.AsyncClient | None = None
_HTTP_CLIENT_LOOP = None


async def _client() -> httpx.AsyncClient:
    global _HTTP_CLIENT, _HTTP_CLIENT_LOOP

    import asyncio

    loop = asyncio.get_running_loop()
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed or _HTTP_CLIENT_LOOP is not loop:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=180.0),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
            trust_env=False,
        )
        _HTTP_CLIENT_LOOP = loop
    return _HTTP_CLIENT


def _upstream_base(sid: str) -> tuple[Any, int]:
    sess = get_web_manager().get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"web session not found: {sid}")
    if sess.web_port is None or not sess.alive:
        raise HTTPException(status_code=410, detail=f"web session not running: {sid}")
    return sess, sess.web_port


@web_proxy_router.api_route(
    "/{sid}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def web_http_proxy(sid: str, path: str, request: Request) -> Response:
    _sess, port = _upstream_base(sid)
    target = f"http://127.0.0.1:{port}/{path}"
    headers = _filter_headers(dict(request.headers))
    headers["host"] = f"127.0.0.1:{port}"
    # Origin must not reach dsh: its fence compares Origin against the Host it
    # sees, and the browser's Origin names the dashboard. Loopback Host alone
    # passes the fence; an absent Origin skips the cross-site comparison.
    headers.pop("origin", None)

    client = await _client()
    try:
        upstream = await client.request(
            method=request.method,
            url=target,
            headers=headers,
            params=request.query_params,
            content=await request.body(),
            follow_redirects=True,
        )
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=502, detail=f"web upstream unreachable: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"web proxy error: {exc}") from exc

    body = upstream.content
    content_type = upstream.headers.get("content-type", "")
    if content_type.startswith("text/html"):
        body = _adapt_html(sid, body)
    return Response(
        content=body,
        status_code=upstream.status_code,
        headers=_filter_headers(dict(upstream.headers)),
        media_type=upstream.headers.get("content-type"),
    )


@web_proxy_router.websocket("/{sid}/{path:path}")
async def web_ws_proxy(ws: WebSocket, sid: str, path: str) -> None:
    """Bridge browser WebSocket ↔ dsh loopback WebSocket (both event streams)."""
    _sess, port = _upstream_base(sid)
    target = f"ws://127.0.0.1:{port}/{path}"
    await ws.accept()
    try:
        async with websockets.connect(target, max_size=None) as upstream:
            async def browser_to_upstream() -> None:
                try:
                    while True:
                        message = await ws.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        if "text" in message and message["text"] is not None:
                            await upstream.send(message["text"])
                        elif "bytes" in message and message["bytes"] is not None:
                            await upstream.send(message["bytes"])
                except WebSocketDisconnect:
                    return

            async def upstream_to_browser() -> None:
                async for message in upstream:
                    if ws.client_state != WebSocketState.CONNECTED:
                        return
                    if isinstance(message, str):
                        await ws.send_text(message)
                    else:
                        await ws.send_bytes(message)

            import asyncio

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(browser_to_upstream()),
                    asyncio.create_task(upstream_to_browser()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
    except (websockets.WebSocketException, ConnectionRefusedError, OSError) as exc:
        logger.warning("web ws bridge failed for %s: %s", sid, exc)
    finally:
        if ws.client_state == WebSocketState.CONNECTED:
            try:
                await ws.close(code=1012, reason="web proxy closed")
            except Exception:
                pass


__all__ = ["web_proxy_router"]
