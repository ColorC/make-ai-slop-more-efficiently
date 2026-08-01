from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import secrets
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from omnicompany.core.config import omni_workspace_root

lan_access_router = APIRouter(prefix="/api/lan-access", tags=["lan-access"])

DEVICE_COOKIE = "omni_device"
CLAIM_COOKIE = "omni_device_claim"
HTTP_DEVICE_COOKIE = "omni_device_http"
HTTP_CLAIM_COOKIE = "omni_device_claim_http"
_lock = threading.RLock()
_MAX_VALUE_CHARS = 1000
_MAX_LIST_ITEMS = 50
_MAX_AUDIT_ITEMS = 1000
_MAX_ACTIVE_CLAIM_HASHES = 64
_CLAIM_RETRY_QUERY = "_omni_claim_retry"
_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "proxy-authorization",
    "x-lofa-control-token",
    "x-lofa-device-token",
}
_CAPTURE_HEADERS = (
    "host",
    "origin",
    "referer",
    "user-agent",
    "accept",
    "accept-language",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-ch-ua-platform-version",
    "sec-ch-ua-arch",
    "sec-ch-ua-bitness",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-uri",
    "x-real-ip",
    "forwarded",
)
_PUBLIC_PATHS = {
    "/device-access",
    "/api/lan-access/authorize",
    "/api/lan-access/request",
    "/api/lan-access/status",
    "/api/lan-access/me",
}
_NATIVE_DEVICE_PATHS = {
    "/api/android/automation/pair",
    "/api/android/automation/register",
    "/api/android/automation/poll",
    "/api/android/automation/result",
}
_PUBLIC_GAME_OBSERVATORY_READ_PATHS = {
    "/game-observatory",
    "/game-observatory/",
    "/game-observatory/studio",
    "/game-observatory/studio/",
    "/game-observatory/app.js",
    "/game-observatory/studio.js",
    "/game-observatory/studio.css",
    "/game-observatory/styles.css",
    "/game-observatory/live",
    "/game-observatory/live/",
    "/game-observatory/live.js",
    "/game-observatory/live.css",
    "/game-observatory/sitemap.xml",
    "/robots.txt",
    "/api/game-observatory/health",
    "/api/game-observatory/catalog",
    "/api/game-observatory/search",
    "/api/game-observatory/tag-taxonomy",
    "/api/game-observatory/content-taxonomy",
    "/api/game-observatory/ai-player/live",
    "/api/game-observatory/ai-player/live/frame.png",
    "/api/game-observatory/ai-player/live/stream.mjpg",
    "/api/game-observatory/ai-player/live/stream/status",
    "/api/game-observatory/workspace/design-specs",
    "/api/game-observatory/workspace/partial-fact-bundles",
    "/api/game-observatory/workspace/partial-fact-bundle",
}
_PUBLIC_GAME_OBSERVATORY_STUDIO_SURFACES = {
    "game",
    "group",
    "play",
    "demo",
    "search",
    "spec",
    "interpretation",
    "reader",
}
_PUBLIC_GAME_OBSERVATORY_READ_PREFIXES = (
    "/game-observatory/report/",
    "/game-observatory/game/",
    "/game-observatory/play/",
    "/game-observatory/reports/",
    "/api/game-observatory/reports/",
    "/api/game-observatory/artifacts/",
    "/api/game-observatory/diagrams/",
    "/api/game-observatory/fragments/",
    "/api/game-observatory/internal/artifacts/",
    "/game-observatory/live-media/",
)
_PUBLIC_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


class LanAccessRegisterBody(BaseModel):
    source: str = "dashboard-frontend"
    url: str | None = None
    route: str | None = None
    referrer: str | None = None
    browser: dict[str, Any] = Field(default_factory=dict)
    screen: dict[str, Any] = Field(default_factory=dict)
    timezone: str | None = None
    userAgentData: dict[str, Any] | None = None


class DeviceRequestBody(BaseModel):
    label: str = ""
    claimed_hostname: str = ""
    kind: str = "computer"
    fingerprint: dict[str, Any] = Field(default_factory=dict)


class DeviceDecisionBody(BaseModel):
    request_id: str
    action: str = "approve"
    existing_device_id: str = ""
    label: str = ""
    confirmed_hostname: str = ""
    kind: str = "computer"
    role: str = "operator"
    network_policy: str = "exact_ip"
    notes: str = ""


class DeviceRevokeBody(BaseModel):
    device_id: str
    reason: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unix_now() -> float:
    return time.time()


def _store_path() -> Path:
    return omni_workspace_root() / "data" / "security" / "device_access.json"


def _bootstrap_path() -> Path:
    return omni_workspace_root() / "config" / "security" / "device_access_bootstrap.json"


def _legacy_observation_path() -> Path:
    return omni_workspace_root() / "data" / "registry" / "lan_access_whitelist.json"


def _empty_store() -> dict[str, Any]:
    return {
        "version": 2,
        "kind": "omnicompany.device_access_registry",
        "enforce": False,
        "devices": [],
        "pending": [],
        "audit": [],
    }


def _safe_json(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return str(value)[:_MAX_VALUE_CHARS]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_VALUE_CHARS]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, raw in list(value.items())[:_MAX_LIST_ITEMS]:
            k = str(key)[:120]
            if k.lower() in _SENSITIVE_KEYS:
                continue
            out[k] = _safe_json(raw, depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [_safe_json(v, depth + 1) for v in list(value)[:_MAX_LIST_ITEMS]]
    return str(value)[:_MAX_VALUE_CHARS]


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.is_file():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _merge_bootstrap(data: dict[str, Any]) -> dict[str, Any]:
    bootstrap = _read_json(_bootstrap_path(), {})
    if not isinstance(bootstrap, dict):
        return data
    if "enforce" in bootstrap:
        data["enforce"] = bool(bootstrap["enforce"])
    configured = bootstrap.get("devices")
    if not isinstance(configured, list):
        return data
    devices = data.setdefault("devices", [])
    by_id = {
        str(item.get("id")): item
        for item in devices
        if isinstance(item, dict) and item.get("id")
    }
    managed_fields = {
        "label",
        "kind",
        "status",
        "roles",
        "hostnames",
        "ips",
        "lofa_device_ids",
        "fingerprint_rule",
        "credential_networks",
        "network_policy",
        "trusted_clients",
        "bootstrap",
        "notes",
    }
    for configured_device in configured:
        if not isinstance(configured_device, dict) or not configured_device.get("id"):
            continue
        device_id = str(configured_device["id"])
        target = by_id.get(device_id)
        if target is None:
            target = {"id": device_id, "credentials": [], "created_at": _now()}
            devices.append(target)
            by_id[device_id] = target
        for field in managed_fields:
            if field in configured_device:
                if field == "bootstrap" and isinstance(configured_device[field], dict):
                    runtime_bootstrap = (
                        target.get("bootstrap")
                        if isinstance(target.get("bootstrap"), dict)
                        else {}
                    )
                    merged_bootstrap = {
                        **_safe_json(configured_device[field]),
                        **{
                            key: value
                            for key, value in runtime_bootstrap.items()
                            if key in {"completed_at"}
                        },
                    }
                    if merged_bootstrap.get("completed_at"):
                        merged_bootstrap["allow_without_credential"] = False
                    target[field] = merged_bootstrap
                else:
                    target[field] = _safe_json(configured_device[field])
        target["managed_by"] = "config/security/device_access_bootstrap.json"
    return data


def _read_store() -> dict[str, Any]:
    raw = _read_json(_store_path(), _empty_store())
    data = raw if isinstance(raw, dict) else _empty_store()
    data.setdefault("version", 2)
    data.setdefault("kind", "omnicompany.device_access_registry")
    data.setdefault("enforce", False)
    for key in ("devices", "pending", "audit"):
        if not isinstance(data.get(key), list):
            data[key] = []
    return _merge_bootstrap(data)


def _write_store(data: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    data["audit"] = list(data.get("audit") or [])[-_MAX_AUDIT_ITEMS:]
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _audit(data: dict[str, Any], event: str, **fields: Any) -> None:
    data.setdefault("audit", []).append(
        {"at": _now(), "event": event, **_safe_json(fields)}
    )


def _normalize_ip(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    raw = raw.split(",", 1)[0].strip()
    if raw.startswith("[") and "]" in raw:
        raw = raw[1 : raw.index("]")]
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return raw[:128]


def _is_loopback(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return value in {"localhost", "testclient"}


def _request_headers(request: Request) -> dict[str, str]:
    return {
        key.lower(): request.headers.get(key) or ""
        for key in _CAPTURE_HEADERS
        if request.headers.get(key) is not None
    }


def _client_ip_from_parts(client_host: str, headers: dict[str, str]) -> str:
    direct = _normalize_ip(client_host)
    # Uvicorn's trusted ProxyHeadersMiddleware normally replaces request.client
    # with Caddy's first X-Forwarded-For hop. This fallback is only for a
    # loopback proxy/test transport; a remote direct peer may not self-assert XFF.
    if not direct or _is_loopback(direct):
        forwarded = _normalize_ip(headers.get("x-forwarded-for"))
        if forwarded:
            return forwarded
    return direct


def _client_ip(request: Request) -> str:
    client = request.client
    return _client_ip_from_parts(client.host if client else "", _request_headers(request))


def _cookie_value(headers: dict[str, str], name: str) -> str:
    raw = headers.get("cookie") or ""
    if not raw:
        return ""
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:
        return ""
    morsel = jar.get(name)
    return morsel.value if morsel else ""


def _cookie_names(name: str) -> tuple[str, ...]:
    if name == DEVICE_COOKIE:
        return DEVICE_COOKIE, HTTP_DEVICE_COOKIE
    if name == CLAIM_COOKIE:
        return CLAIM_COOKIE, HTTP_CLAIM_COOKIE
    return (name,)


def _access_cookie_value(headers: dict[str, str], name: str) -> str:
    return next(
        (
            value
            for candidate in _cookie_names(name)
            if (value := _cookie_value(headers, candidate))
        ),
        "",
    )


def _secure_transport(*, scheme: str, headers: dict[str, str]) -> bool:
    return (
        scheme.lower() == "https"
        or headers.get("x-forwarded-proto", "").split(",", 1)[0].strip() == "https"
    )


def _transport_cookie_name(
    name: str,
    *,
    secure: bool,
    headers: dict[str, str],
) -> str:
    if secure:
        return name
    forwarded_host = headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
    host = (forwarded_host or headers.get("host") or "").lower()
    # The legacy LAN dashboard is intentionally still available over
    # http://<host>:8210. It cannot overwrite or send the Secure cookies issued
    # by HTTPS on the same hostname, so it needs a distinct compatibility
    # namespace. Loopback/test HTTP keeps the historical names.
    if host.endswith(":8210") and not host.startswith(("127.0.0.1:", "localhost:", "[::1]:")):
        if name == DEVICE_COOKIE:
            return HTTP_DEVICE_COOKIE
        if name == CLAIM_COOKIE:
            return HTTP_CLAIM_COOKIE
    return name


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", errors="replace")).hexdigest()


def _credential_token(device_id: str) -> tuple[str, dict[str, Any]]:
    secret = secrets.token_urlsafe(32)
    token = f"{device_id}.{secret}"
    return token, {
        "hash": _token_digest(token),
        "created_at": _now(),
        "last_seen_at": _now(),
    }


def _fingerprint_from_request(
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    browser = payload.get("browser") if isinstance(payload.get("browser"), dict) else {}
    ua_data = (
        payload.get("userAgentData")
        if isinstance(payload.get("userAgentData"), dict)
        else {}
    )
    screen = payload.get("screen") if isinstance(payload.get("screen"), dict) else {}
    return _safe_json(
        {
            "user_agent": headers.get("user-agent") or browser.get("userAgent") or "",
            "sec_ch_ua": headers.get("sec-ch-ua") or "",
            "platform": (
                ua_data.get("platform")
                or headers.get("sec-ch-ua-platform")
                or browser.get("platform")
                or ""
            ),
            "platform_version": (
                ua_data.get("platformVersion")
                or headers.get("sec-ch-ua-platform-version")
                or ""
            ),
            "model": ua_data.get("model") or "",
            "architecture": (
                ua_data.get("architecture")
                or headers.get("sec-ch-ua-arch")
                or ""
            ),
            "bitness": ua_data.get("bitness") or headers.get("sec-ch-ua-bitness") or "",
            "language": browser.get("language") or headers.get("accept-language") or "",
            "screen": {
                "width": screen.get("width"),
                "height": screen.get("height"),
                "pixel_ratio": screen.get("devicePixelRatio"),
                "touch_points": browser.get("maxTouchPoints"),
                "hardware_concurrency": browser.get("hardwareConcurrency"),
                "device_memory": browser.get("deviceMemory"),
            },
        }
    )


def _ip_matches(ip: str, allowed: list[Any]) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for raw in allowed:
        try:
            if "/" in str(raw):
                if address in ipaddress.ip_network(str(raw), strict=False):
                    return True
            elif address == ipaddress.ip_address(str(raw)):
                return True
        except ValueError:
            continue
    return False


def _fingerprint_matches(
    fingerprint: dict[str, Any],
    rule: dict[str, Any] | None,
) -> bool:
    if not rule:
        return True
    ua = str(fingerprint.get("user_agent") or "").lower()
    platform = str(fingerprint.get("platform") or "").strip('"').lower()
    model = str(fingerprint.get("model") or "").lower()
    all_terms = [str(v).lower() for v in rule.get("ua_contains_all") or []]
    any_terms = [str(v).lower() for v in rule.get("ua_contains_any") or []]
    platforms = [str(v).strip('"').lower() for v in rule.get("platforms") or []]
    models = [str(v).lower() for v in rule.get("models") or []]
    if all_terms and not all(term in ua for term in all_terms):
        return False
    if any_terms and not any(term in ua for term in any_terms):
        return False
    if platforms and not any(term in platform or term in ua for term in platforms):
        return False
    if models and not any(term in model or term in ua for term in models):
        return False
    return True


def _stable_fingerprint(fingerprint: dict[str, Any]) -> dict[str, str]:
    """Reduce request metadata to device traits that survive browser upgrades."""
    ua = str(fingerprint.get("user_agent") or "").lower()
    platform = str(fingerprint.get("platform") or "").strip('"').lower()
    model = str(fingerprint.get("model") or "").strip().lower()
    if "windows" in platform or "windows" in ua:
        os_family = "windows"
    elif "android" in platform or "android" in ua:
        os_family = "android"
    elif "iphone" in ua or "ipad" in ua or "ios" in platform:
        os_family = "ios"
    elif "mac" in platform or "macintosh" in ua:
        os_family = "macos"
    elif "linux" in platform or "linux" in ua:
        os_family = "linux"
    else:
        os_family = ""
    if "edg/" in ua:
        browser_family = "edge"
    elif "firefox/" in ua:
        browser_family = "firefox"
    elif "opr/" in ua or "opera/" in ua:
        browser_family = "opera"
    elif "android webview" in ua or ("; wv)" in ua and "android" in ua):
        browser_family = "android-webview"
    elif "chrome/" in ua or "crios/" in ua:
        browser_family = "chrome"
    elif "safari/" in ua:
        browser_family = "safari"
    elif "windowspowershell/" in ua:
        browser_family = "powershell"
    else:
        browser_family = ""
    return {
        "os_family": os_family,
        "browser_family": browser_family,
        "model": model,
    }


def _stable_fingerprint_matches(
    bound: dict[str, Any],
    current: dict[str, str],
) -> bool:
    for key in ("os_family", "browser_family", "model"):
        expected = str(bound.get(key) or "")
        if expected and expected != str(current.get(key) or ""):
            return False
    return True


def _roles(device: dict[str, Any]) -> list[str]:
    return [str(value) for value in device.get("roles") or []]


def _device_by_id(data: dict[str, Any], device_id: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in data.get("devices") or []
            if isinstance(item, dict) and str(item.get("id") or "") == device_id
        ),
        None,
    )


def _credential_device(
    data: dict[str, Any],
    token: str,
    ip: str,
    fingerprint: dict[str, Any],
) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    device_id = token.split(".", 1)[0]
    device = _device_by_id(data, device_id)
    if not device or device.get("status") != "approved":
        return None
    # Dynamic LOFA devices authenticate with the issued credential plus the
    # stable browser/device fingerprint. Their current IP is telemetry, not identity.
    network_policy = str(device.get("network_policy") or "").strip().lower()
    credential_networks = list(device.get("credential_networks") or device.get("ips") or [])
    if network_policy != "observe_only" and not _ip_matches(ip, credential_networks):
        return None
    if not _fingerprint_matches(fingerprint, device.get("fingerprint_rule")):
        return None
    digest = _token_digest(token)
    credential = next(
        (
            item
            for item in device.get("credentials") or []
            if secrets.compare_digest(str(item.get("hash") or ""), digest)
            and not item.get("revoked_at")
        ),
        None,
    )
    if credential is None:
        return None
    now_unix = _unix_now()
    retired_credentials = 0
    for candidate in device.get("credentials") or []:
        if (
            candidate is credential
            or candidate.get("revoked_at")
            or candidate.get("bound_fingerprint")
        ):
            continue
        try:
            created_at = datetime.fromisoformat(
                str(candidate.get("created_at") or "")
            ).timestamp()
        except ValueError:
            created_at = 0
        if created_at and created_at > now_unix - 300:
            continue
        candidate["revoked_at"] = _now()
        candidate["revocation_reason"] = "superseded_after_device_binding"
        retired_credentials += 1
    if retired_credentials:
        _audit(
            data,
            "stale_unbound_credentials_retired",
            device_id=device["id"],
            count=retired_credentials,
        )
    stable_fingerprint = _stable_fingerprint(fingerprint)
    bound_fingerprint = credential.get("bound_fingerprint")
    if isinstance(bound_fingerprint, dict):
        if not _stable_fingerprint_matches(bound_fingerprint, stable_fingerprint):
            return None
    else:
        credential["bound_fingerprint"] = stable_fingerprint
        _audit(
            data,
            "device_credential_fingerprint_bound",
            device_id=device["id"],
            ip=ip,
            fingerprint=stable_fingerprint,
        )
    now = _now()
    previous_ip = str(credential.get("last_ip") or "")
    credential["last_seen_at"] = now
    credential["last_ip"] = ip
    device["last_seen_at"] = now
    device["last_ip"] = ip
    observed_ips = [
        item
        for item in device.get("observed_ips") or []
        if isinstance(item, dict) and str(item.get("ip") or "") != ip
    ]
    observed_ips.append({"ip": ip, "last_seen_at": now})
    device["observed_ips"] = observed_ips[-20:]
    if previous_ip and previous_ip != ip:
        _audit(
            data,
            "device_credential_ip_changed",
            device_id=device["id"],
            previous_ip=previous_ip,
            ip=ip,
        )
    bootstrap = device.get("bootstrap")
    if isinstance(bootstrap, dict) and bootstrap.get("allow_without_credential"):
        bootstrap["completed_at"] = _now()
        bootstrap["allow_without_credential"] = False
    return device


def _local_workstation_device(data: dict[str, Any], ip: str) -> dict[str, Any] | None:
    """Trust the host's explicitly configured LAN address as a local recovery path."""
    device = _device_by_id(data, "local-workstation")
    if not device or device.get("status") != "approved":
        return None
    configured = [
        str(value)
        for value in device.get("ips") or []
        if "/" not in str(value) and not _is_loopback(str(value))
    ]
    return device if ip in configured else None


def _trusted_service_client(
    data: dict[str, Any],
    ip: str,
    fingerprint: dict[str, Any],
    path: str,
    method: str,
) -> dict[str, Any] | None:
    """Match a narrowly scoped non-browser client on an already approved device."""
    for device in data.get("devices") or []:
        if (
            not isinstance(device, dict)
            or device.get("status") != "approved"
        ):
            continue
        for client in device.get("trusted_clients") or []:
            if not isinstance(client, dict):
                continue
            client_networks = list(client.get("networks") or device.get("ips") or [])
            if not _ip_matches(ip, client_networks):
                continue
            methods = [str(value).upper() for value in client.get("methods") or []]
            paths = [str(value) for value in client.get("paths") or []]
            prefixes = [str(value) for value in client.get("path_prefixes") or []]
            if methods and method.upper() not in methods:
                continue
            if paths or prefixes:
                if path not in paths and not any(
                    path.startswith(value) for value in prefixes
                ):
                    continue
            if _fingerprint_matches(fingerprint, client.get("fingerprint_rule")):
                return device
    return None


def _bootstrap_device(
    data: dict[str, Any],
    ip: str,
    fingerprint: dict[str, Any],
) -> dict[str, Any] | None:
    now = _unix_now()
    for device in data.get("devices") or []:
        bootstrap = device.get("bootstrap") if isinstance(device.get("bootstrap"), dict) else {}
        if (
            device.get("status") != "approved"
            or not bootstrap.get("allow_without_credential")
            or bootstrap.get("completed_at")
        ):
            continue
        expires_at = float(bootstrap.get("expires_at") or 0)
        if expires_at and expires_at < now:
            continue
        # An observe-only IP policy must never turn a currently observed address
        # into an authentication factor. Such devices must present a credential.
        if str(device.get("network_policy") or "").strip().lower() == "observe_only":
            continue
        if not _ip_matches(ip, list(device.get("ips") or [])):
            continue
        if _fingerprint_matches(fingerprint, device.get("fingerprint_rule")):
            return device
    return None


def _request_snapshot(request: Request) -> dict[str, Any]:
    headers = _request_headers(request)
    client = request.client
    ip = _client_ip_from_parts(client.host if client else "", headers)
    return {
        "client": {
            "host": ip,
            "port": client.port if client else None,
        },
        "method": request.method,
        "path": request.url.path,
        "headers": _safe_json(headers),
    }


def _decision(
    *,
    client_host: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    allow_bootstrap: bool = True,
    path: str = "",
    method: str = "",
) -> dict[str, Any]:
    ip = _client_ip_from_parts(client_host, headers)
    fingerprint = _fingerprint_from_request(headers, payload)
    if _is_loopback(ip):
        return {
            "allowed": True,
            "method": "loopback",
            "ip": ip,
            "device_id": "local-workstation",
            "roles": ["owner", "approver", "operator"],
            "fingerprint": fingerprint,
        }
    with _lock:
        data = _read_store()
        if not data.get("enforce"):
            return {
                "allowed": True,
                "method": "disabled",
                "ip": ip,
                "device_id": "",
                "roles": [],
                "fingerprint": fingerprint,
            }
        device = _local_workstation_device(data, ip)
        if device is not None:
            return {
                "allowed": True,
                "method": "local_host_ip",
                "ip": ip,
                "device_id": device["id"],
                "roles": _roles(device),
                "fingerprint": fingerprint,
            }
        token = _access_cookie_value(headers, DEVICE_COOKIE)
        device = _credential_device(data, token, ip, fingerprint)
        if device is not None:
            _write_store(data)
            return {
                "allowed": True,
                "method": "credential",
                "ip": ip,
                "device_id": device["id"],
                "roles": _roles(device),
                "fingerprint": fingerprint,
            }
        device = _trusted_service_client(data, ip, fingerprint, path, method)
        if device is not None:
            return {
                "allowed": True,
                "method": "trusted_service_client",
                "ip": ip,
                "device_id": device["id"],
                "roles": _roles(device),
                "fingerprint": fingerprint,
            }
        if allow_bootstrap:
            device = _bootstrap_device(data, ip, fingerprint)
            if device is not None:
                return {
                    "allowed": True,
                    "method": "migration_bootstrap",
                    "ip": ip,
                    "device_id": device["id"],
                    "roles": _roles(device),
                    "fingerprint": fingerprint,
                }
    return {
        "allowed": False,
        "method": "unapproved",
        "ip": ip,
        "device_id": "",
        "roles": [],
        "fingerprint": fingerprint,
    }


def evaluate_request(
    request: Request,
    payload: dict[str, Any] | None = None,
    *,
    allow_bootstrap: bool = True,
) -> dict[str, Any]:
    client = request.client
    headers = dict(request.headers)
    return _decision(
        client_host=client.host if client else "",
        headers={str(k).lower(): str(v) for k, v in headers.items()},
        payload=payload,
        allow_bootstrap=allow_bootstrap,
        path=request.url.path,
        method=request.method,
    )


def _claim_id(ip: str, fingerprint: dict[str, Any]) -> str:
    material = json.dumps(
        {"ip": ip, "ua": fingerprint.get("user_agent"), "model": fingerprint.get("model")},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _pending_claim(
    data: dict[str, Any],
    *,
    ip: str,
    fingerprint: dict[str, Any],
    label: str = "",
    claimed_hostname: str = "",
    kind: str = "computer",
    claim_token: str = "",
    source: str = "blocked_request",
) -> tuple[dict[str, Any], str]:
    request_id = _claim_id(ip, fingerprint)
    pending = next(
        (
            item
            for item in data.get("pending") or []
            if item.get("request_id") == request_id
            and item.get("status") in {"pending", "approved"}
        ),
        None,
    )
    now = _now()
    if pending is None:
        pending = {
            "request_id": request_id,
            "status": "pending",
            "first_seen_at": now,
            "claim_hash": "",
        }
        data.setdefault("pending", []).append(pending)
        _audit(data, "device_request_created", request_id=request_id, ip=ip, source=source)
    if not claim_token:
        claim_token = secrets.token_urlsafe(24)
    claim_digest = _token_digest(claim_token)
    active_claim_hashes = [
        str(value)
        for value in pending.get("claim_hashes") or []
        if str(value)
    ]
    legacy_claim_hash = str(pending.get("claim_hash") or "")
    if legacy_claim_hash and legacy_claim_hash not in active_claim_hashes:
        active_claim_hashes.append(legacy_claim_hash)
    if claim_digest not in active_claim_hashes:
        active_claim_hashes.append(claim_digest)
    active_claim_hashes = active_claim_hashes[-_MAX_ACTIVE_CLAIM_HASHES:]
    pending.update(
        {
            "last_seen_at": now,
            "seen_count": int(pending.get("seen_count") or 0) + 1,
            "ip": ip,
            "fingerprint": _safe_json(fingerprint),
            "label": str(label or pending.get("label") or "")[:120],
            "claimed_hostname": str(
                claimed_hostname or pending.get("claimed_hostname") or ""
            )[:255],
            "kind": str(kind or pending.get("kind") or "computer")[:40],
            # Keep the legacy singleton for old registry readers, but accept a
            # bounded set because one browser may have several forward-auth
            # requests in flight before any Set-Cookie response lands.
            "claim_hash": claim_digest,
            "claim_hashes": active_claim_hashes,
            "source": source,
        }
    )
    return pending, claim_token


def _record_unknown_request(request: Request, source: str) -> tuple[str, str]:
    decision = evaluate_request(request)
    if decision["allowed"]:
        return "", ""
    with _lock:
        data = _read_store()
        pending, claim_token = _pending_claim(
            data,
            ip=decision["ip"],
            fingerprint=decision["fingerprint"],
            claim_token=_access_cookie_value(dict(request.headers), CLAIM_COOKIE),
            source=source,
        )
        _write_store(data)
    return str(pending["request_id"]), claim_token


def _set_cookie(
    response: Response,
    name: str,
    value: str,
    request: Request,
    *,
    max_age: int,
    samesite: str = "lax",
) -> None:
    headers = dict(request.headers)
    secure = _secure_transport(
        scheme=request.url.scheme,
        headers=headers,
    )
    cookie_name = _transport_cookie_name(
        name,
        secure=secure,
        headers=headers,
    )
    response.set_cookie(
        cookie_name,
        value,
        max_age=max_age,
        httponly=True,
        secure=secure,
        # Dashboard deep links are commonly opened from the legacy HTTP
        # launcher. Modern browsers treat that HTTP -> HTTPS navigation as
        # cross-site (schemeful SameSite), so Strict suppresses both the
        # existing device credential and a newly issued claim for the whole
        # redirect chain. Lax still excludes cross-site subrequests/POSTs while
        # allowing this top-level GET enrollment/navigation flow.
        samesite=samesite,
        path="/",
    )


def _asgi_cookie_header(
    *,
    name: str,
    value: str,
    scope: dict[str, Any],
    headers: dict[str, str],
    max_age: int,
) -> bytes:
    secure = _secure_transport(
        scheme=str(scope.get("scheme") or ""),
        headers=headers,
    )
    cookie_name = _transport_cookie_name(
        name,
        secure=secure,
        headers=headers,
    )
    attributes = [
        f"{cookie_name}={value}",
        f"Max-Age={max_age}",
        "Path=/",
        "HttpOnly",
        # See _set_cookie: direct HTTP -> HTTPS dashboard links must be able to
        # carry the short-lived claim during a top-level GET redirect.
        "SameSite=Lax",
    ]
    if secure:
        attributes.append("Secure")
    return "; ".join(attributes).encode("latin-1")


def _issue_device_cookie(
    data: dict[str, Any],
    device: dict[str, Any],
    request: Request,
    *,
    migrated: bool,
) -> tuple[str, dict[str, Any]]:
    token, credential = _credential_token(str(device["id"]))
    credential["bound_fingerprint"] = _stable_fingerprint(
        _fingerprint_from_request(_request_headers(request))
    )
    credential["last_ip"] = _client_ip(request)
    device.setdefault("credentials", []).append(credential)
    device["last_seen_at"] = _now()
    if migrated:
        bootstrap = device.setdefault("bootstrap", {})
        bootstrap["completed_at"] = _now()
        bootstrap["allow_without_credential"] = False
    _audit(
        data,
        "device_credential_issued",
        device_id=device["id"],
        ip=_client_ip(request),
        migrated=migrated,
    )
    return token, credential


def _public_game_observatory_read_path(path: str) -> bool:
    if path in _PUBLIC_GAME_OBSERVATORY_READ_PATHS:
        return True
    studio_prefix = "/game-observatory/studio/"
    if path.startswith(studio_prefix):
        surface = path[len(studio_prefix):].strip("/")
        return surface in _PUBLIC_GAME_OBSERVATORY_STUDIO_SURFACES
    return any(
        path.startswith(prefix)
        for prefix in _PUBLIC_GAME_OBSERVATORY_READ_PREFIXES
    )


def _public_path(path: str, method: str = "GET") -> bool:
    if path in _PUBLIC_PATHS:
        return True
    if path.startswith("/api/lan-access/status/"):
        return True
    if path in _NATIVE_DEVICE_PATHS:
        return True
    if method.upper() in _PUBLIC_READ_METHODS:
        return _public_game_observatory_read_path(path)
    return False


def _public_next_url(value: str) -> str | None:
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or not parts.path.startswith("/"):
        return None
    if not _public_path(parts.path, "GET"):
        return None
    return value


def _local_next_url(value: str) -> str | None:
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or not parts.path.startswith("/"):
        return None
    if parts.path == "/device-access":
        return None
    return value


def _claim_retry_url(value: str) -> str | None:
    """Add a one-shot marker so missing cookies can never create a redirect loop."""
    parts = urlsplit(value)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if any(key == _CLAIM_RETRY_QUERY for key, _value in query):
        return None
    query.append((_CLAIM_RETRY_QUERY, "1"))
    return urlunsplit(("", "", parts.path, urlencode(query), parts.fragment))


def _html_request(headers: dict[str, str]) -> bool:
    accept = headers.get("accept") or ""
    return "text/html" in accept.lower()


def _blocked_http_response(
    *,
    request_id: str,
    path: str,
    headers: dict[str, str],
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    if _html_request(headers):
        location = f"/device-access?request={quote(request_id)}&next={quote(path)}"
        return (
            307,
            [(b"location", location.encode("utf-8")), (b"cache-control", b"no-store")],
            b"",
        )
    body = json.dumps(
        {
            "ok": False,
            "error": "device_approval_required",
            "request_id": request_id,
            "approval_url": f"/device-access?request={request_id}",
        }
    ).encode("utf-8")
    return (
        403,
        [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
        body,
    )


def _scope_request_target(scope: dict[str, Any]) -> str:
    path = str(scope.get("path") or "/")
    raw_query = scope.get("query_string") or b""
    if isinstance(raw_query, bytes):
        query = raw_query.decode("latin-1")
    else:
        query = str(raw_query)
    return f"{path}?{query}" if query else path


class DeviceAccessMiddleware:
    """Application-level device gate for every dashboard HTTP/WebSocket route."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "WEBSOCKET")
        if _public_path(path, method):
            await self.app(scope, receive, send)
            return
        headers: dict[str, str] = {}
        for key, value in scope.get("headers") or []:
            # ASGI permits repeated fields. Preserve the first browser identity
            # header; some test/proxy transports append their own default UA.
            headers.setdefault(key.decode("latin-1").lower(), value.decode("latin-1"))
        client = scope.get("client") or ("", 0)
        decision = _decision(
            client_host=str(client[0]),
            headers=headers,
            path=path,
            method=method,
        )
        if decision["allowed"]:
            await self.app(scope, receive, send)
            return
        request_id = ""
        claim_token = ""
        with _lock:
            data = _read_store()
            pending, claim_token = _pending_claim(
                data,
                ip=decision["ip"],
                fingerprint=decision["fingerprint"],
                claim_token=_access_cookie_value(headers, CLAIM_COOKIE),
                source=f"{scope.get('type')}:{path}",
            )
            request_id = str(pending["request_id"])
            _write_store(data)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "device approval required"})
            return
        status, response_headers, body = _blocked_http_response(
            request_id=request_id,
            path=_scope_request_target(scope),
            headers=headers,
        )
        response_headers.append(
            (
                b"set-cookie",
                _asgi_cookie_header(
                    name=CLAIM_COOKIE,
                    value=claim_token,
                    scope=scope,
                    headers=headers,
                    max_age=86400,
                ),
            )
        )
        response_headers.append((b"content-length", str(len(body)).encode("ascii")))
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": body})


def record_lan_visit(info: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compatibility entrypoint: record an observation without granting trust."""
    payload = _safe_json(payload or {})
    headers = {
        str(k).lower(): str(v)
        for k, v in (info.get("headers") or {}).items()
        if str(k).lower() not in _SENSITIVE_KEYS
    }
    ip = _client_ip_from_parts(str((info.get("client") or {}).get("host") or ""), headers)
    fingerprint = _fingerprint_from_request(headers, payload)
    with _lock:
        data = _read_store()
        pending, _ = _pending_claim(
            data,
            ip=ip,
            fingerprint=fingerprint,
            source=str(payload.get("source") or "lan_access_register"),
        )
        _write_store(data)
    return {
        "ok": False,
        "status": "pending",
        "trusted": False,
        "request_id": pending["request_id"],
        "path": str(_store_path()),
    }


@lan_access_router.post("/register")
async def register_lan_access(req: Request, body: LanAccessRegisterBody) -> Response:
    payload = body.model_dump(exclude_none=True)
    decision = evaluate_request(req, payload)
    if not decision["allowed"]:
        with _lock:
            data = _read_store()
            pending, claim_token = _pending_claim(
                data,
                ip=decision["ip"],
                fingerprint=decision["fingerprint"],
                source=body.source,
            )
            _write_store(data)
        response = JSONResponse(
            {
                "ok": False,
                "status": "pending",
                "trusted": False,
                "request_id": pending["request_id"],
            },
            status_code=202,
        )
        _set_cookie(response, CLAIM_COOKIE, claim_token, req, max_age=86400)
        return response

    response_payload: dict[str, Any] = {
        "ok": True,
        "status": "approved",
        "trusted": True,
        "device_id": decision["device_id"],
        "method": decision["method"],
    }
    response = JSONResponse(response_payload)
    if decision["method"] == "migration_bootstrap":
        with _lock:
            data = _read_store()
            device = _device_by_id(data, str(decision["device_id"]))
            if device is None:
                return JSONResponse(
                    {"ok": False, "error": "bootstrap device disappeared"},
                    status_code=409,
                )
            token, _ = _issue_device_cookie(data, device, req, migrated=True)
            device["last_fingerprint"] = decision["fingerprint"]
            _write_store(data)
        _set_cookie(response, DEVICE_COOKIE, token, req, max_age=60 * 60 * 24 * 180)
    return response


@lan_access_router.api_route("/authorize", methods=["GET", "POST", "HEAD"])
async def authorize_device(req: Request) -> Response:
    decision = evaluate_request(req)
    if decision["allowed"]:
        if decision["method"] == "migration_bootstrap":
            with _lock:
                data = _read_store()
                device = _device_by_id(data, str(decision["device_id"]))
                if device is None:
                    return JSONResponse(
                        {"ok": False, "error": "bootstrap device disappeared"},
                        status_code=409,
                    )
                token, _ = _issue_device_cookie(data, device, req, migrated=True)
                _write_store(data)
            original = _local_next_url(req.headers.get("x-forwarded-uri") or "/") or "/"
            # Caddy forward_auth only copies 2xx response headers into the
            # upstream request. A non-2xx redirect is required for the browser
            # itself to receive the newly issued cookie before both gates run.
            response = RedirectResponse(
                original,
                status_code=307,
                headers={"Cache-Control": "no-store"},
            )
            _set_cookie(
                response,
                DEVICE_COOKIE,
                token,
                req,
                max_age=60 * 60 * 24 * 180,
            )
            return response
        response = Response(
            status_code=204,
            headers={
                "X-Omni-Device-ID": str(decision["device_id"]),
                "X-Omni-Device-Role": ",".join(decision["roles"]),
                "Cache-Control": "no-store",
            },
        )
        return response
    request_id, claim_token = _record_unknown_request(req, "caddy_forward_auth")
    original = req.headers.get("x-forwarded-uri") or "/"
    if "text/html" in (req.headers.get("accept") or "").lower():
        response = RedirectResponse(
            f"/device-access?request={quote(request_id)}&next={quote(original)}",
            status_code=307,
            headers={"Cache-Control": "no-store"},
        )
        _set_cookie(response, CLAIM_COOKIE, claim_token, req, max_age=86400)
        return response
    response = JSONResponse(
        {
            "ok": False,
            "error": "device_approval_required",
            "request_id": request_id,
            "approval_url": f"/device-access?request={request_id}",
        },
        status_code=403,
        headers={"Cache-Control": "no-store"},
    )
    _set_cookie(response, CLAIM_COOKIE, claim_token, req, max_age=86400)
    return response


@lan_access_router.post("/request")
async def request_device_access(req: Request, body: DeviceRequestBody) -> Response:
    decision = evaluate_request(req)
    if decision["allowed"]:
        return JSONResponse(
            {
                "ok": True,
                "status": "approved",
                "device_id": decision["device_id"],
                "method": decision["method"],
            }
        )
    with _lock:
        data = _read_store()
        pending, claim_token = _pending_claim(
            data,
            ip=decision["ip"],
            fingerprint={
                **decision["fingerprint"],
                "client_claim": _safe_json(body.fingerprint),
            },
            label=body.label,
            claimed_hostname=body.claimed_hostname,
            kind=body.kind,
            claim_token=_access_cookie_value(dict(req.headers), CLAIM_COOKIE),
            source="device_access_form",
        )
        _write_store(data)
    response = JSONResponse(
        {
            "ok": True,
            "status": pending["status"],
            "request_id": pending["request_id"],
            "observed_ip": pending["ip"],
        },
        status_code=202,
    )
    _set_cookie(response, CLAIM_COOKIE, claim_token, req, max_age=86400)
    return response


@lan_access_router.get("/status/{request_id}")
async def device_request_status(req: Request, request_id: str) -> Response:
    claim_token = _access_cookie_value(dict(req.headers), CLAIM_COOKIE)
    with _lock:
        data = _read_store()
        pending = next(
            (
                item
                for item in reversed(data.get("pending") or [])
                if str(item.get("request_id") or "") == request_id
            ),
            None,
        )
        if pending is None:
            return JSONResponse({"ok": False, "error": "request_not_found"}, status_code=404)
        claim_digest = _token_digest(claim_token) if claim_token else ""
        active_claim_hashes = {
            str(value)
            for value in pending.get("claim_hashes") or []
            if str(value)
        }
        legacy_claim_hash = str(pending.get("claim_hash") or "")
        if legacy_claim_hash:
            active_claim_hashes.add(legacy_claim_hash)
        if not claim_digest or not any(
            secrets.compare_digest(candidate, claim_digest)
            for candidate in active_claim_hashes
        ):
            return JSONResponse({"ok": False, "error": "claim_authorization_required"}, status_code=403)
        if pending.get("status") != "approved":
            return JSONResponse(
                {"ok": True, "status": pending.get("status"), "request_id": request_id}
            )
        device = _device_by_id(data, str(pending.get("approved_device_id") or ""))
        if device is None:
            return JSONResponse({"ok": False, "error": "approved_device_missing"}, status_code=409)
        token, _ = _issue_device_cookie(data, device, req, migrated=False)
        enrolled_at = _now()
        pending.setdefault("enrolled_at", enrolled_at)
        pending["last_enrolled_at"] = enrolled_at
        pending["enrollment_count"] = int(pending.get("enrollment_count") or 0) + 1
        pending["claim_hash"] = ""
        pending["claim_hashes"] = []
        _write_store(data)
    response = JSONResponse(
        {
            "ok": True,
            "status": "approved",
            "request_id": request_id,
            "device_id": device["id"],
            "enrolled": True,
        }
    )
    _set_cookie(response, DEVICE_COOKIE, token, req, max_age=60 * 60 * 24 * 180)
    response.delete_cookie(CLAIM_COOKIE, path="/")
    return response


def _require_approver(req: Request) -> tuple[dict[str, Any] | None, Response | None]:
    decision = evaluate_request(req, allow_bootstrap=False)
    if decision["allowed"] and "approver" in decision["roles"]:
        return decision, None
    return None, JSONResponse(
        {"ok": False, "error": "approved administrator device required"},
        status_code=403,
    )


def _rule_from_pending(pending: dict[str, Any]) -> dict[str, Any]:
    fingerprint = pending.get("fingerprint") if isinstance(pending.get("fingerprint"), dict) else {}
    ua = str(fingerprint.get("user_agent") or "")
    model = str(fingerprint.get("model") or "")
    platform = str(fingerprint.get("platform") or "").strip('"')
    terms: list[str] = []
    if "Android" in ua:
        terms.append("Android")
    elif "Windows" in ua:
        terms.append("Windows")
    if "Edg/" in ua:
        terms.append("Edg/")
    elif "Android WebView" in ua:
        terms.append("wv")
    rule: dict[str, Any] = {}
    if terms:
        rule["ua_contains_all"] = terms
    if platform:
        rule["platforms"] = [platform]
    if model:
        rule["models"] = [model]
    return rule


def _recovery_candidates(
    data: dict[str, Any],
    pending: dict[str, Any],
) -> list[dict[str, Any]]:
    fingerprint = (
        pending.get("fingerprint")
        if isinstance(pending.get("fingerprint"), dict)
        else {}
    )
    ip = str(pending.get("ip") or "")
    candidates: list[dict[str, Any]] = []
    for device in data.get("devices") or []:
        if (
            not isinstance(device, dict)
            or device.get("status") != "approved"
            or device.get("id") == "local-workstation"
        ):
            continue
        networks = list(device.get("credential_networks") or device.get("ips") or [])
        if not _ip_matches(ip, networks):
            continue
        if not _fingerprint_matches(fingerprint, device.get("fingerprint_rule")):
            continue
        candidates.append(
            {
                "id": str(device.get("id") or ""),
                "label": str(device.get("label") or device.get("id") or ""),
                "hostnames": _safe_json(device.get("hostnames") or []),
                "last_ip": str(device.get("last_ip") or ""),
                "last_seen_at": str(device.get("last_seen_at") or ""),
            }
        )
    return candidates


@lan_access_router.post("/decision")
async def decide_device_access(req: Request, body: DeviceDecisionBody) -> Response:
    approver, denied = _require_approver(req)
    if denied is not None:
        return denied
    action = body.action.strip().lower()
    if action not in {"approve", "reject"}:
        return JSONResponse({"ok": False, "error": "action must be approve or reject"}, status_code=400)
    existing_device_id = body.existing_device_id.strip()
    if action == "approve" and not existing_device_id and not body.confirmed_hostname.strip():
        return JSONResponse(
            {"ok": False, "error": "confirmed_hostname is required for approval"},
            status_code=400,
        )
    if body.role not in {"operator", "approver"}:
        return JSONResponse({"ok": False, "error": "role must be operator or approver"}, status_code=400)
    if body.network_policy not in {"exact_ip", "feilian_private"}:
        return JSONResponse(
            {"ok": False, "error": "network_policy must be exact_ip or feilian_private"},
            status_code=400,
        )
    with _lock:
        data = _read_store()
        pending = next(
            (
                item
                for item in reversed(data.get("pending") or [])
                if str(item.get("request_id") or "") == body.request_id
            ),
            None,
        )
        if pending is None:
            return JSONResponse({"ok": False, "error": "request_not_found"}, status_code=404)
        repeat_recovery = (
            action == "approve"
            and bool(existing_device_id)
            and pending.get("status") == "approved"
            and str(pending.get("approved_device_id") or "") == existing_device_id
        )
        if pending.get("status") != "pending" and not repeat_recovery:
            return JSONResponse(
                {"ok": False, "error": f"request already {pending.get('status')}"},
                status_code=409,
            )
        if action == "reject":
            pending.update(
                {
                    "status": "rejected",
                    "decided_at": _now(),
                    "decided_by": approver["device_id"],
                    "decision_notes": body.notes[:1000],
                }
            )
            _audit(
                data,
                "device_request_rejected",
                request_id=body.request_id,
                decided_by=approver["device_id"],
            )
            _write_store(data)
            return JSONResponse({"ok": True, "status": "rejected", "request_id": body.request_id})

        if existing_device_id:
            candidates = {
                str(item["id"]): item
                for item in _recovery_candidates(data, pending)
            }
            device = _device_by_id(data, existing_device_id)
            if device is None or existing_device_id not in candidates:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "existing device does not match observed network and fingerprint",
                    },
                    status_code=409,
                )
            pending.update(
                {
                    "status": "approved",
                    "decided_at": _now(),
                    "decided_by": approver["device_id"],
                    "approved_device_id": existing_device_id,
                    "confirmed_hostname": (
                        body.confirmed_hostname.strip()[:255]
                        or str((device.get("hostnames") or [""])[0])[:255]
                    ),
                    "decision_notes": body.notes[:1000],
                }
            )
            _audit(
                data,
                "device_access_recovered",
                request_id=body.request_id,
                device_id=existing_device_id,
                decided_by=approver["device_id"],
                ip=pending["ip"],
            )
            _write_store(data)
            return JSONResponse(
                {
                    "ok": True,
                    "status": "approved",
                    "request_id": body.request_id,
                    "device_id": existing_device_id,
                    "recovered_existing_device": True,
                    "waiting_for_claimant_enrollment": True,
                }
            )

        device_id = f"{body.kind.strip() or 'device'}-{secrets.token_hex(5)}"
        roles = ["operator"]
        if body.role == "approver":
            roles.append("approver")
        device = {
            "id": device_id,
            "label": (body.label or pending.get("label") or device_id)[:120],
            "kind": (body.kind or pending.get("kind") or "computer")[:40],
            "status": "approved",
            "roles": roles,
            "hostnames": [body.confirmed_hostname.strip()[:255]],
            "ips": [pending["ip"]],
            "credential_networks": (
                ["10.0.0.0/8"]
                if body.network_policy == "feilian_private"
                else [pending["ip"]]
            ),
            "network_policy": body.network_policy,
            "fingerprint_rule": _rule_from_pending(pending),
            "approved_fingerprint": _safe_json(pending.get("fingerprint") or {}),
            "credentials": [],
            "created_at": _now(),
            "approved_at": _now(),
            "approved_by": approver["device_id"],
            "notes": body.notes[:1000],
        }
        data.setdefault("devices", []).append(device)
        pending.update(
            {
                "status": "approved",
                "decided_at": _now(),
                "decided_by": approver["device_id"],
                "approved_device_id": device_id,
                "confirmed_hostname": body.confirmed_hostname.strip()[:255],
                "decision_notes": body.notes[:1000],
            }
        )
        _audit(
            data,
            "device_request_approved",
            request_id=body.request_id,
            device_id=device_id,
            decided_by=approver["device_id"],
            ip=pending["ip"],
        )
        _write_store(data)
    return JSONResponse(
        {
            "ok": True,
            "status": "approved",
            "request_id": body.request_id,
            "device_id": device_id,
            "waiting_for_claimant_enrollment": True,
        }
    )


@lan_access_router.post("/revoke")
async def revoke_device(req: Request, body: DeviceRevokeBody) -> Response:
    approver, denied = _require_approver(req)
    if denied is not None:
        return denied
    if body.device_id == "local-workstation":
        return JSONResponse({"ok": False, "error": "local recovery device cannot be revoked"}, status_code=409)
    with _lock:
        data = _read_store()
        device = _device_by_id(data, body.device_id)
        if device is None:
            return JSONResponse({"ok": False, "error": "device_not_found"}, status_code=404)
        device["status"] = "revoked"
        device["revoked_at"] = _now()
        device["revoked_by"] = approver["device_id"]
        device["revocation_reason"] = body.reason[:1000]
        for credential in device.get("credentials") or []:
            credential["revoked_at"] = _now()
        _audit(
            data,
            "device_revoked",
            device_id=body.device_id,
            revoked_by=approver["device_id"],
        )
        _write_store(data)
    return JSONResponse({"ok": True, "device_id": body.device_id, "status": "revoked"})


def _redacted_registry(data: dict[str, Any]) -> dict[str, Any]:
    devices = []
    for item in data.get("devices") or []:
        device = {k: _safe_json(v) for k, v in item.items() if k != "credentials"}
        device["credential_count"] = sum(
            1 for credential in item.get("credentials") or [] if not credential.get("revoked_at")
        )
        devices.append(device)
    pending = []
    for item in data.get("pending") or []:
        redacted = {
            k: _safe_json(v)
            for k, v in item.items()
            if k not in {"claim_hash", "claim_hashes"}
        }
        if item.get("status") == "pending":
            redacted["recovery_candidates"] = _recovery_candidates(data, item)
        pending.append(redacted)
    return {
        "version": data.get("version"),
        "enforce": bool(data.get("enforce")),
        "devices": devices,
        "pending": pending,
        "audit": list(data.get("audit") or [])[-100:],
    }


@lan_access_router.get("/registry")
async def device_registry(req: Request) -> Response:
    _, denied = _require_approver(req)
    if denied is not None:
        return denied
    with _lock:
        return JSONResponse({"ok": True, **_redacted_registry(_read_store())})


@lan_access_router.get("/me")
async def lan_access_me(req: Request) -> dict[str, Any]:
    decision = evaluate_request(req)
    return {
        "ok": True,
        "allowed": decision["allowed"],
        "method": decision["method"],
        "ip": decision["ip"],
        "device_id": decision["device_id"],
        "roles": decision["roles"],
        "fingerprint": decision["fingerprint"],
        "request": _request_snapshot(req),
    }


@lan_access_router.get("/whitelist")
async def lan_access_whitelist(req: Request) -> Response:
    """Compatibility alias for the redacted administrator registry."""
    return await device_registry(req)


def is_known_lofa_device(_ip: str, device_id: str) -> bool:
    """Match an approved native LOFA identity without treating its IP as identity."""
    normalized_device_id = str(device_id or "").strip()
    if not normalized_device_id:
        return False
    with _lock:
        data = _read_store()
        return any(
            device.get("status") == "approved"
            and normalized_device_id in list(device.get("lofa_device_ids") or [])
            for device in data.get("devices") or []
        )


def is_local_client_ip(ip: str) -> bool:
    return _is_loopback(_normalize_ip(ip))


def record_native_device_pending(
    *,
    ip: str,
    device_id: str,
    user_agent: str = "",
) -> str:
    fingerprint = {
        "user_agent": user_agent,
        "platform": "Android",
        "model": "",
        "native_device_id": device_id,
    }
    with _lock:
        data = _read_store()
        pending, _ = _pending_claim(
            data,
            ip=ip,
            fingerprint=fingerprint,
            label=device_id,
            kind="phone",
            source="lofa_native_register",
        )
        _write_store(data)
        return str(pending["request_id"])


def _device_access_html(decision: dict[str, Any]) -> str:
    is_admin = decision["allowed"] and "approver" in decision["roles"]
    title = "设备准入管理" if is_admin else "此设备尚未获准访问"
    admin_block = """
      <section id="admin" class="card">
        <h2>待确认设备</h2>
        <p class="muted">批准前请逐项核对 IP、申报主机名和指纹。主机名必须由管理员现场确认，浏览器无法可信读取操作系统主机名。</p>
        <div id="pending">加载中…</div>
      </section>
    """ if is_admin else """
      <section class="card">
        <h2>申请访问</h2>
        <label>设备名称<input id="label" autocomplete="off" placeholder="例如：工作笔记本"></label>
        <label>本机主机名<input id="hostname" autocomplete="off" placeholder="Windows 可运行 hostname 查看"></label>
        <label>设备类型<select id="kind"><option value="computer">电脑</option><option value="tablet">平板</option><option value="phone">手机</option></select></label>
        <button id="request">提交给管理员确认</button>
        <pre id="status"></pre>
      </section>
    """
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
:root{{color-scheme:dark;font-family:Segoe UI,system-ui,sans-serif;background:#0b1020;color:#e5e7eb}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:28px}}
main{{width:min(920px,100%);display:grid;gap:18px}}.card{{background:#121a2d;border:1px solid #293551;border-radius:14px;padding:22px}}
h1,h2{{margin:0 0 12px}}p{{line-height:1.55}}.muted{{color:#9ca3af}}
label{{display:grid;gap:6px;margin:14px 0}}input,select,button{{font:inherit;border-radius:8px;border:1px solid #41506d;padding:10px 12px;background:#0c1426;color:#fff}}
button{{background:#2563eb;border-color:#3b82f6;cursor:pointer}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;color:#b9d2ff}}
.item{{border-top:1px solid #293551;padding:14px 0}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
code{{color:#93c5fd}}
</style></head><body><main>
<section class="card"><h1>{title}</h1><p>观测来源 IP：<code>{html.escape(str(decision["ip"]))}</code></p>
<p class="muted">Dashboard、ChatUI、远程 CLI、WebSocket 与同网关下的高敏入口均使用同一设备准入策略。</p></section>
{admin_block}
</main><script>
const fp=()=>({{userAgent:navigator.userAgent,platform:navigator.platform,language:navigator.language,
screen:{{width:screen.width,height:screen.height,dpr:devicePixelRatio,touch:navigator.maxTouchPoints}},
timezone:Intl.DateTimeFormat().resolvedOptions().timeZone}});
async function json(url, options={{}}){{const r=await fetch(url,{{credentials:'same-origin',...options}});return [r,await r.json()]}}
const statusEl=document.querySelector('#status');
document.querySelector('#request')?.addEventListener('click',async()=>{{
 const body={{label:label.value,claimed_hostname:hostname.value,kind:kind.value,fingerprint:fp()}};
 const [r,d]=await json('/api/lan-access/request',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
 statusEl.textContent=JSON.stringify(d,null,2);if(d.request_id)poll(d.request_id);
}});
async function poll(id){{const timer=setInterval(async()=>{{const [r,d]=await json('/api/lan-access/status/'+encodeURIComponent(id));
 if(statusEl)statusEl.textContent=JSON.stringify(d,null,2);if(d.enrolled){{clearInterval(timer);location.href=new URLSearchParams(location.search).get('next')||'/';}}}},2500)}}
const initialRequest=new URLSearchParams(location.search).get('request');if(initialRequest)poll(initialRequest);
async function loadAdmin(){{
 const el=document.querySelector('#pending');if(!el)return;const [r,d]=await json('/api/lan-access/registry');
 if(!r.ok){{el.textContent=JSON.stringify(d,null,2);return}}
 const items=d.pending.filter(x=>x.status==='pending');el.innerHTML=items.length?'':'<p class="muted">当前没有待确认设备。</p>';
 for(const x of items){{const box=document.createElement('div');box.className='item';
 box.innerHTML=`<div><b>${{x.label||x.request_id}}</b> · <code>${{x.ip}}</code></div>
 <pre>${{JSON.stringify(x.fingerprint,null,2)}}</pre><div class="grid">
 <input class="host" placeholder="确认后的主机名" value="${{x.claimed_hostname||''}}">
 <select class="role"><option value="operator">普通操控设备</option><option value="approver">管理员设备</option></select>
 <select class="network"><option value="exact_ip">固定 IP</option><option value="feilian_private">飞连动态 IP (10.0.0.0/8)</option></select></div>
 <div class="recover"></div><button class="approve">核对无误并批准为新设备</button>`;
 for(const c of x.recovery_candidates||[]){{const b=document.createElement('button');b.textContent=`恢复到已登记设备：${{c.label}} (${{c.id}})`;
 b.onclick=async()=>{{const [rr,dd]=await json('/api/lan-access/decision',{{method:'POST',headers:{{'Content-Type':'application/json'}},
 body:JSON.stringify({{request_id:x.request_id,action:'approve',existing_device_id:c.id,notes:'administrator approved credential recovery'}})}});
 alert(JSON.stringify(dd));if(rr.ok)loadAdmin();}};box.querySelector('.recover').appendChild(b);}}
 box.querySelector('.approve').onclick=async()=>{{
 const body={{request_id:x.request_id,action:'approve',label:x.label||'',confirmed_hostname:box.querySelector('.host').value,
 kind:x.kind||'computer',role:box.querySelector('.role').value,network_policy:box.querySelector('.network').value}};
 const [rr,dd]=await json('/api/lan-access/decision',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
 alert(JSON.stringify(dd));if(rr.ok)loadAdmin();}};el.appendChild(box);}}
}}
loadAdmin();
</script></body></html>"""


@lan_access_router.get("/page", response_class=HTMLResponse, include_in_schema=False)
async def device_access_page_alias(req: Request) -> HTMLResponse:
    return HTMLResponse(_device_access_html(evaluate_request(req)), headers={"Cache-Control": "no-store"})


def mount_device_access_page(app) -> None:
    @app.get("/device-access", response_class=HTMLResponse, include_in_schema=False)
    async def _page(req: Request) -> Response:
        raw_next = str(req.query_params.get("next") or "")
        public_next = _public_next_url(raw_next)
        if public_next:
            return RedirectResponse(public_next, status_code=307)
        local_next = _local_next_url(raw_next)
        request_id = str(req.query_params.get("request") or "")
        claim_token = _access_cookie_value(dict(req.headers), CLAIM_COOKIE)
        if local_next and request_id and claim_token:
            # An already-approved claimant should never sit on a generic
            # "restricted" page waiting for JavaScript. Complete enrollment on
            # the first recovery-page request, copy both device/claim cookie
            # headers, and return directly to the original dashboard target.
            enrollment = await device_request_status(req, request_id)
            try:
                payload = json.loads(bytes(enrollment.body))
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            if enrollment.status_code == 200 and payload.get("enrolled"):
                redirected = RedirectResponse(
                    local_next,
                    status_code=307,
                    headers={"Cache-Control": "no-store"},
                )
                redirected.raw_headers.extend(
                    (key, value)
                    for key, value in enrollment.raw_headers
                    if key.lower() == b"set-cookie"
                )
                return redirected
        if local_next and request_id and not claim_token:
            with _lock:
                data = _read_store()
                approved = next(
                    (
                        item
                        for item in reversed(data.get("pending") or [])
                        if str(item.get("request_id") or "") == request_id
                        and item.get("status") == "approved"
                        and not item.get("enrolled_at")
                    ),
                    None,
                )
            if approved is not None:
                # Legacy HTTP middleware did not issue a claim cookie. Bounce
                # once through the protected route so the same IP/fingerprint
                # can receive a claim token, then complete enrollment normally.
                # The URL marker makes "once" literal: even if browser policy
                # rejects every cookie, the recovery page renders on the next
                # pass instead of alternating redirects forever.
                retry_url = _claim_retry_url(local_next)
                if retry_url is not None:
                    return RedirectResponse(retry_url, status_code=307)
        return HTMLResponse(
            _device_access_html(evaluate_request(req)),
            headers={"Cache-Control": "no-store"},
        )
