"""Persistent, revocable-at-the-gateway sessions for the desktop WebView.

The browser's HTTP Basic credential cache belongs to the WebView process and is
lost when an installer restarts it.  Caddy still performs the interactive Basic
login, then proves that fact to this router with a private gateway secret.  The
router converts that one successful login into a signed HttpOnly cookie which
survives application updates in WebView2's persistent profile.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

desktop_sessions_router = APIRouter(
    prefix="/api/desktop/session",
    tags=["desktop-session"],
)

DESKTOP_SESSION_COOKIE = "omni_desktop_session"
HTTP_DESKTOP_SESSION_COOKIE = "omni_desktop_session_http"
GATEWAY_SECRET_HEADER = "x-omnicompany-gateway-session"
DESKTOP_MARKER_HEADER = "x-omnicompany-desktop"
# 2026-08-16 用户裁决: 会话永久(登录一次不再登)。取 10 年(3650 天)而非真无限 ——
# cookie Max-Age 需要有限值, 滑动续期下等效"用着就一直有效"。
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 3650
WEBVIEW_SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 3650
WEBVIEW_SESSION_AUDIENCE = "native-webview"
_INSTANCE_ID = re.compile(r"^[a-f0-9]{32}$")


class DesktopSessionBody(BaseModel):
    instance_id: str = Field(min_length=32, max_length=32)


def _gateway_secret_path() -> Path:
    configured = os.environ.get("OMNI_GATEWAY_SESSION_SECRET_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "codeweb" / "omni-desktop-session.secret"


def _gateway_secret() -> bytes:
    try:
        value = _gateway_secret_path().read_text(encoding="utf-8").strip()
    except OSError:
        return b""
    return value.encode("utf-8") if len(value) >= 32 else b""


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign_session(
    instance_id: str,
    secret: bytes,
    now: int | None = None,
    *,
    max_age_seconds: int = SESSION_MAX_AGE_SECONDS,
    audience: str | None = None,
) -> str:
    issued_at = int(time.time() if now is None else now)
    payload = {
        "exp": issued_at + max_age_seconds,
        "iat": issued_at,
        "sub": hashlib.sha256(instance_id.encode("ascii")).hexdigest()[:32],
        "v": 1,
    }
    if audience is not None:
        payload["aud"] = audience
    encoded = _base64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = _base64url_encode(
        hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{encoded}.{signature}"


def _verify_session(token: str, secret: bytes, now: int | None = None) -> dict[str, Any] | None:
    if not secret or token.count(".") != 1:
        return None
    encoded, supplied_signature = token.split(".", 1)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", supplied_signature
    ):
        return None
    expected_signature = _base64url_encode(
        hmac.new(secret, encoded.encode("ascii", errors="ignore"), hashlib.sha256).digest()
    )
    if not secrets.compare_digest(supplied_signature, expected_signature):
        return None
    try:
        payload = json.loads(_base64url_decode(encoded))
    except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("v") != 1:
        return None
    current = int(time.time() if now is None else now)
    try:
        issued_at = int(payload["iat"])
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError):
        return None
    if issued_at > current + 300 or expires_at <= current:
        return None
    audience = payload.get("aud")
    expected_lifetime = (
        WEBVIEW_SESSION_MAX_AGE_SECONDS
        if audience == WEBVIEW_SESSION_AUDIENCE
        else SESSION_MAX_AGE_SECONDS
    )
    if audience not in (None, WEBVIEW_SESSION_AUDIENCE):
        return None
    if expires_at - issued_at != expected_lifetime:
        return None
    subject = str(payload.get("sub") or "")
    if not re.fullmatch(r"[a-f0-9]{32}", subject):
        return None
    return payload


def _request_session(req: Request, secret: bytes) -> dict[str, Any] | None:
    for name in (DESKTOP_SESSION_COOKIE, HTTP_DESKTOP_SESSION_COOKIE):
        token = str(req.cookies.get(name) or "")
        if token and (payload := _verify_session(token, secret)) is not None:
            return payload
    return None


def _has_lofa_device_session(req: Request) -> bool:
    """A paired LOFA device session counts as "already logged in at the gateway".

    2026-08-19, from a real report ("远程网页里无限要求登录"): LOFA's WebView
    authenticates with `omni_lofa_session`, minted natively and marked
    SameSite=None, and it never owns a desktop cookie. When this reflector only
    looked for desktop cookies it answered `has_session: false` on *every* load,
    so the Dashboard bootstrap (frontend/src/lib/sessionBootstrap.ts) posted the
    Basic-challenged bootstrap endpoint each time and the WebView raised a login
    dialog. Logging in could not end it either: the desktop cookie is
    SameSite=Lax, and LOFA embeds the Dashboard in a cross-site iframe (the top
    document is the app's own localhost origin), so the fresh cookie was never
    sent back — login, 401, login, forever.

    The gateway lets `GET /api/desktop/session/state` through anonymously and
    does not strip this cookie on that route, so the check works from here.
    """
    try:
        from omnicompany.dashboard.controlplane.android import lofa_web_session_device

        return bool(lofa_web_session_device(req))
    except Exception:
        # Never let an optional identity source break the reflector; a wrong
        # `false` only costs one bootstrap attempt, an exception costs the page.
        return False


def _secure_request(req: Request) -> bool:
    forwarded = str(req.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded.lower() == "https"
    return req.url.scheme.lower() == "https"


def _set_session_cookie(response: Response, req: Request, token: str) -> None:
    secure = _secure_request(req)
    response.set_cookie(
        DESKTOP_SESSION_COOKIE if secure else HTTP_DESKTOP_SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def request_is_owner_device(req: Request | WebSocket) -> bool:
    """驾驶舱视角的「机主设备」：带有效桌面会话 cookie、已配对的 LOFA 设备会话，或真实客户端就是本机回环。

    2026-09-21 起 /bside 代理只对这些请求注入 Workshop 的个人身份。此前任何 LAN 访客打开
    /bside 都被当成机主直接落进「我的工作台」——多租户隔离前这是最大的越权口；其他访客
    现在会看到 Workshop 自己的登录页（collab platform / 测试账号）。
    """
    secret = _gateway_secret()
    if secret and _request_session(req, secret) is not None:  # type: ignore[arg-type]
        return True
    if _has_lofa_device_session(req):  # type: ignore[arg-type]
        return True
    forwarded = str(req.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    client = forwarded or (req.client.host if req.client else "")
    return client in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def _gateway_authenticated_desktop(req: Request, secret: bytes) -> bool:
    supplied = str(req.headers.get(GATEWAY_SECRET_HEADER) or "").encode("utf-8")
    return (
        bool(secret)
        and req.headers.get(DESKTOP_MARKER_HEADER) == "1"
        and secrets.compare_digest(supplied, secret)
    )


@desktop_sessions_router.post("")
async def create_or_renew_desktop_session(
    req: Request,
    body: DesktopSessionBody,
) -> Response:
    secret = _gateway_secret()
    if not secret:
        return JSONResponse(
            {"ok": False, "error": "desktop session gateway is not configured"},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )
    if not _gateway_authenticated_desktop(req, secret):
        return JSONResponse(
            {"ok": False, "error": "authenticated desktop gateway required"},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    instance_id = body.instance_id.strip().lower()
    if not _INSTANCE_ID.fullmatch(instance_id):
        return JSONResponse(
            {"ok": False, "error": "invalid desktop instance id"},
            status_code=422,
            headers={"Cache-Control": "no-store"},
        )
    now = int(time.time())
    token = _sign_session(instance_id, secret, now=now)
    expires_at = now + SESSION_MAX_AGE_SECONDS
    webview_token = _sign_session(
        instance_id,
        secret,
        now=now,
        max_age_seconds=WEBVIEW_SESSION_MAX_AGE_SECONDS,
        audience=WEBVIEW_SESSION_AUDIENCE,
    )
    response = JSONResponse(
        {
            "ok": True,
            "expires_at": expires_at,
            "webview_session": {
                "token": webview_token,
                "expires_at": now + WEBVIEW_SESSION_MAX_AGE_SECONDS,
            },
        },
        headers={"Cache-Control": "no-store"},
    )
    _set_session_cookie(response, req, token)
    return response


@desktop_sessions_router.api_route("/authorize", methods=["GET", "POST", "HEAD"])
async def authorize_desktop_session(req: Request) -> Response:
    secret = _gateway_secret()
    session = _request_session(req, secret)
    if session is None:
        return Response(
            status_code=401,
            headers={
                "WWW-Authenticate": 'Basic realm="Omnicompany protected surface"',
                "Cache-Control": "no-store",
            },
        )
    return Response(
        status_code=204,
        headers={
            "X-Omni-Desktop-Session": str(session["sub"]),
            "Cache-Control": "no-store",
        },
    )


@desktop_sessions_router.get("/state")
async def desktop_session_state(req: Request) -> Response:
    """惰性反射端点: 只回报本请求是否携带有效会话 cookie, 不创建、不续期。

    前端 bootstrap 用它在页面加载时判断"要不要换一次网关登录为持久会话"。
    刻意匿名(Caddy 放行 GET /api/desktop/session/state): 泄露面仅为一个布尔值,
    与 /api/healthz 同级惰性; 没有它, 前端只能盲目打 POST /api/desktop/session,
    对已有会话的用户平白多一次 Basic 挑战。
    """
    secret = _gateway_secret()
    session = _request_session(req, secret)
    return JSONResponse(
        {"ok": True, "has_session": session is not None or _has_lofa_device_session(req)},
        headers={"Cache-Control": "no-store"},
    )
