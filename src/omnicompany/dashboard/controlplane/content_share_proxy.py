"""Thin 8210 proxy for the dashboard-managed Slip service."""
from __future__ import annotations

import ipaddress

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

content_share_proxy_router = APIRouter(tags=["content-share-proxy"])
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
_HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade", "proxy-authenticate", "proxy-authorization", "te", "trailers", "content-length"}
_client: httpx.AsyncClient | None = None


def _upstream_base() -> str:
    from .content_share_service import service_url
    return service_url()


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=60.0, write=60.0, pool=3.0), follow_redirects=False, trust_env=False)
    return _client


def _direct_loopback(request: Request) -> bool:
    forwarded = str(request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    if forwarded:
        return False
    host = str(request.client.host if request.client else "").strip()
    if host in {"localhost", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _session_authenticated(request: Request) -> bool:
    try:
        from .desktop_sessions import _gateway_secret, _has_lofa_device_session, _request_session

        secret = _gateway_secret()
        return bool(_request_session(request, secret) or _has_lofa_device_session(request))
    except Exception:
        return False


def _management_authenticated(request: Request) -> bool:
    return bool(_direct_loopback(request) or request.headers.get("authorization") or _session_authenticated(request))


async def _proxy(request: Request, path: str, *, authorized: bool = False) -> Response:
    url = _upstream_base() + path + (("?" + request.url.query) if request.url.query else "")
    headers = {key: value for key, value in request.headers.items() if key.lower() not in _HOP and key.lower() != "host"}
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if forwarded_host:
        headers["x-forwarded-host"] = forwarded_host
    headers["x-forwarded-proto"] = request.headers.get("x-forwarded-proto") or request.url.scheme
    if authorized:
        headers["x-omni-content-share-authorized"] = "1"
    try:
        upstream_request = _get_client().build_request(request.method, url, content=await request.body(), headers=headers)
        upstream = await _get_client().send(upstream_request, stream=True)
    except Exception as error:
        return Response(f"content-share upstream error: {error}", status_code=502, media_type="text/plain")
    response_headers = {key: value for key, value in upstream.headers.items() if key.lower() not in _HOP}
    return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=response_headers, media_type=upstream.headers.get("content-type"), background=BackgroundTask(upstream.aclose))


@content_share_proxy_router.api_route("/api/content-share/{path:path}", methods=_METHODS)
async def api_proxy(path: str, request: Request):
    if not _management_authenticated(request):
        return JSONResponse(
            {"ok": False, "error": "content_share_management_auth_required"},
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )
    return await _proxy(request, "/api/content-share/" + path, authorized=True)


@content_share_proxy_router.api_route("/s/{path:path}", methods=["GET", "HEAD"])
async def share_proxy(path: str, request: Request):
    return await _proxy(request, "/s/" + path, authorized=_management_authenticated(request))


@content_share_proxy_router.api_route("/agent-session-shares/{path:path}", methods=["GET", "HEAD"])
async def legacy_agent_session_share_proxy(path: str, request: Request):
    return await _proxy(
        request,
        "/agent-session-shares/" + path,
        authorized=_management_authenticated(request),
    )
