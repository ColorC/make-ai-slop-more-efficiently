from __future__ import annotations

import hashlib
import ipaddress
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from omnicompany.core.config import omni_workspace_root

lan_access_router = APIRouter(prefix="/api/lan-access", tags=["lan-access"])

_lock = threading.Lock()
_MAX_VALUE_CHARS = 1000
_MAX_LIST_ITEMS = 50
_SENSITIVE_KEYS = {"authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"}
_CAPTURE_HEADERS = (
    "host",
    "origin",
    "referer",
    "user-agent",
    "accept-language",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-ch-ua-platform-version",
    "sec-ch-ua-arch",
    "sec-ch-ua-bitness",
    "x-forwarded-for",
    "x-real-ip",
    "forwarded",
)


class LanAccessRegisterBody(BaseModel):
    source: str = "dashboard-frontend"
    url: str | None = None
    route: str | None = None
    referrer: str | None = None
    browser: dict[str, Any] = Field(default_factory=dict)
    screen: dict[str, Any] = Field(default_factory=dict)
    timezone: str | None = None
    userAgentData: dict[str, Any] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _whitelist_path() -> Path:
    return omni_workspace_root() / "data" / "registry" / "lan_access_whitelist.json"


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


def _read_store() -> dict[str, Any]:
    path = _whitelist_path()
    if not path.is_file():
        return {"version": 1, "kind": "omnicompany.dashboard.lan_access_whitelist", "entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "kind": "omnicompany.dashboard.lan_access_whitelist", "entries": []}
    if not isinstance(data, dict):
        return {"version": 1, "kind": "omnicompany.dashboard.lan_access_whitelist", "entries": []}
    entries = data.get("entries")
    if not isinstance(entries, list):
        data["entries"] = []
    data.setdefault("version", 1)
    data.setdefault("kind", "omnicompany.dashboard.lan_access_whitelist")
    return data


def _write_store(data: dict[str, Any]) -> None:
    path = _whitelist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


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


def _client_ip(info: dict[str, Any]) -> str:
    headers = info.get("headers") or {}
    forwarded = _normalize_ip(headers.get("x-forwarded-for"))
    direct = _normalize_ip((info.get("client") or {}).get("host"))
    return direct or forwarded


def _entry_id(ip: str, user_agent: str) -> str:
    digest = hashlib.sha256(f"{ip}\n{user_agent}".encode("utf-8", errors="replace")).hexdigest()
    return digest[:16]


def _request_snapshot(request: Request) -> dict[str, Any]:
    headers = {
        key.lower(): request.headers.get(key)
        for key in _CAPTURE_HEADERS
        if request.headers.get(key) is not None and key.lower() not in _SENSITIVE_KEYS
    }
    client = request.client
    return {
        "client": {
            "host": client.host if client else "",
            "port": client.port if client else None,
        },
        "method": request.method,
        "path": request.url.path,
        "headers": headers,
    }


def record_lan_visit(info: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = _safe_json(payload or {})
    headers = info.get("headers") or {}
    ip = _client_ip(info)
    user_agent = str(headers.get("user-agent") or payload.get("browser", {}).get("userAgent") or "")
    entry_id = _entry_id(ip, user_agent)
    now = _now()
    with _lock:
        data = _read_store()
        entries: list[dict[str, Any]] = data["entries"]
        entry = next((e for e in entries if e.get("ip") == ip), None)
        if entry is None:
            entry = {
                "id": entry_id,
                "ip": ip,
                "status": "whitelisted",
                "trusted": True,
                "first_seen_at": now,
                "seen_count": 0,
                "created_by": "dashboard_first_visit",
                "notes": (
                    "Auto-whitelisted by first dashboard visit. Browser cannot reliably expose "
                    "Windows computer name."
                ),
            }
            entries.append(entry)
        entry.update(
            {
                "id": entry.get("id") or entry_id,
                "last_seen_at": now,
                "seen_count": int(entry.get("seen_count") or 0) + 1,
                "client": _safe_json(info.get("client") or {}),
                "request": {
                    "method": info.get("method"),
                    "path": info.get("path"),
                    "headers": _safe_json(headers),
                },
                "user_agent": user_agent,
                "browser": _safe_json(payload),
            }
        )
        entries.sort(key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
        _write_store(data)
    return {"ok": True, "entry": entry, "path": str(_whitelist_path())}


@lan_access_router.post("/register")
async def register_lan_access(req: Request, body: LanAccessRegisterBody) -> dict[str, Any]:
    return record_lan_visit(_request_snapshot(req), body.model_dump(exclude_none=True))


@lan_access_router.get("/whitelist")
def lan_access_whitelist(limit: int = 100) -> dict[str, Any]:
    data = _read_store()
    entries = list(data.get("entries") or [])[: max(1, min(int(limit), 500))]
    return {
        "ok": True,
        "path": str(_whitelist_path()),
        "count": len(data.get("entries") or []),
        "entries": entries,
    }


@lan_access_router.get("/me")
async def lan_access_me(req: Request) -> dict[str, Any]:
    info = _request_snapshot(req)
    return {"ok": True, "ip": _client_ip(info), "request": info}
