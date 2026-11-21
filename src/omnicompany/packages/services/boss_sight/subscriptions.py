# [OMNI] origin=grok-build type=dashboard
"""集中订阅配额: 便宜的官方/本地读当场查, TUI /usage 一天最多藏一次会话, 网页站走通用登录档。

不扫 ~/.claude 或 ~/.codex 全量日志, 不跑 ccusage。默认页加载不阻塞等 TUI。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from omnicompany.packages.services._core.agent.resource_control import (
    ResourceControl,
    default_catalog_path,
    default_state_dir,
)

_TTL_LIVE = 300.0
_TTL_DAILY = 24 * 3600.0
_HTTP_TIMEOUT = 4.0
_CACHE_LOCK = threading.Lock()
_LIVE_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_ROW_CACHE: dict[str, dict[str, Any]] = {}
_REFRESHING: set[str] = set()
_REFRESH_POOL = ThreadPoolExecutor(max_workers=12, thread_name_prefix="subscription")
_PROBE_CACHE: dict[str, dict[str, Any]] = {}
_SLASH_THREAD: threading.Thread | None = None
_WEB_THREAD: threading.Thread | None = None
_WEB_PROVIDERS = ("qwen-token-plan", "codebuddy")

_THE_COMPANY_MODELS = "https://internal-llm-proxy.example.com/v1/models"
_QWEN_MODELS = "https://coding.dashscope.aliyuncs.com/v1/models"
_OPENCODE_MODELS = "https://opencode.ai/zen/go/v1/models"
_OPENCODE_USAGE = "https://opencode.ai/zen/go/v1/usage"
_GROK_MODELS = "https://api.x.ai/v1/models"
# GLM Coding Plan 官方额度端点(与 z.ai/bigmodel 用量页同源, 同一把 coding plan key
# 即可读)。两个域名返回同一份数据, 只有 msg 语言不同; 用国际域避免国内域偶发跳转。
_GLM_QUOTA = "https://api.z.ai/api/monitor/usage/quota/limit"
_KIMI_USAGES = "https://api.kimi.com/coding/v1/usages"
_KIMI_OAUTH_TOKEN = "https://auth.kimi.com/api/oauth/token"
_KIMI_OAUTH_CLIENT = "17e5f671-d194-4dfb-9706-5516cb48c098"
_GROK_BIN = Path.home() / ".grok" / "bin" / "grok.exe"
_KIMI_CRED = Path.home() / ".kimi-code" / "credentials" / "kimi-code.json"
_OPENCODE_AUTH = Path.home() / ".local" / "share" / "opencode" / "auth.json"
_GROK_AUTH = Path.home() / ".grok" / "auth.json"
_THE_COMPANY_SNAPSHOT_PATH = Path(__file__).resolve().parents[4] / "data" / "llm" / "the_company-account-snapshot.json"
_THE_COMPANY_EXCLUDE_MODELS = {"k3", "kimi-for-coding", "kimi-for-coding-highspeed"}
_SHANGHAI = timezone(timedelta(hours=8))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _parse_time(raw: Any) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        text = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _reset_in_sec(resets_at: Any, now: datetime | None = None) -> int | None:
    reset = _parse_time(resets_at)
    if reset is None:
        return None
    delta = (reset - (now or _utc_now())).total_seconds()
    return int(delta) if delta > 0 else 0


def _pct(remaining: float | None, capacity: float | None) -> float | None:
    if remaining is None or not capacity:
        return None
    return round(max(0.0, min(100.0, 100.0 * float(remaining) / float(capacity))), 1)


def estimate_window(
    last: dict[str, Any],
    *,
    consumed: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """用上次校准值估当前剩余。窗口已过则不再假装还有额度。"""
    current = now or _utc_now()
    remaining = last.get("remaining")
    capacity = last.get("capacity")
    reset_at = last.get("reset_at") or last.get("resets_at")
    reset = _parse_time(reset_at)
    observed = _parse_time(last.get("observed_at"))
    out = {
        "id": str(last.get("window_id") or last.get("id") or "primary"),
        "label": str(last.get("label") or last.get("window_id") or "主窗口"),
        "remaining": None if remaining is None else float(remaining),
        "capacity": None if capacity is None else float(capacity),
        "unit": str(last.get("unit") or "credits"),
        "resets_at": reset_at,
        "reset_in_sec": _reset_in_sec(reset_at, current),
        "observed_at": last.get("observed_at"),
        "estimated": True,
        "note": None,
    }
    if reset is not None and current >= reset:
        out["remaining"] = None
        out["used_pct"] = None
        out["remaining_pct"] = None
        out["note"] = "窗口可能已重置，等网页校准"
        return out
    if remaining is not None and consumed is not None and str(out["unit"]) != "percent":
        out["remaining"] = round(max(0.0, float(remaining) - float(consumed)), 4)
        out["note"] = "上次网页校准后按本地用量估算"
    elif remaining is not None:
        age_h = None if not observed else (current - observed).total_seconds() / 3600
        if age_h is not None and age_h > 1:
            out["note"] = "沿用上次校准，未找到可扣减的本地用量"
        else:
            out["estimated"] = False
    remaining_now = out["remaining"]
    out["remaining_pct"] = _pct(remaining_now, out["capacity"])
    if out["remaining_pct"] is not None:
        out["used_pct"] = round(max(0.0, 100.0 - float(out["remaining_pct"])), 1)
    else:
        out["used_pct"] = last.get("used_pct")
        if out["remaining_pct"] is None and last.get("remaining_pct") is not None:
            out["remaining_pct"] = last.get("remaining_pct")
    return out


def sort_providers(rows: list[dict[str, Any]], key: str = "reset_at") -> list[dict[str, Any]]:
    def rank(row: dict[str, Any]) -> tuple[int, float, str]:
        if key == "remaining":
            ratio = row.get("remaining_ratio")
            if ratio is None:
                return (1, 0.0, str(row.get("id") or ""))
            return (0, float(ratio), str(row.get("id") or ""))
        if key == "observed_at":
            observed = _parse_time(row.get("observed_at"))
            if observed is None:
                return (1, 0.0, str(row.get("id") or ""))
            return (0, -observed.timestamp(), str(row.get("id") or ""))
        reset_sec = row.get("next_reset_in_sec")
        if reset_sec is None:
            return (1, 0.0, str(row.get("id") or ""))
        return (0, float(reset_sec), str(row.get("id") or ""))

    return sorted(rows, key=rank)


def _window_from_usage(
    window_id: str,
    label: str,
    raw: dict[str, Any] | None,
    *,
    observed_at: Any = None,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    used = raw.get("used_pct")
    remaining_pct = raw.get("remaining_pct")
    if used is None and remaining_pct is None:
        return None
    if remaining_pct is None and used is not None:
        remaining_pct = round(max(0.0, 100.0 - float(used)), 1)
    if used is None and remaining_pct is not None:
        used = round(max(0.0, 100.0 - float(remaining_pct)), 1)
    return {
        "id": window_id,
        "label": label,
        "used_pct": None if used is None else float(used),
        "remaining_pct": None if remaining_pct is None else float(remaining_pct),
        "remaining": None if remaining_pct is None else float(remaining_pct),
        "capacity": 100.0,
        "unit": "percent",
        "resets_at": raw.get("resets_at"),
        "reset_in_sec": raw.get("reset_in_sec"),
        # Codex quota is read from the latest rollout event. Preserve that
        # timestamp instead of making a stale log look fresh on every poll.
        "observed_at": observed_at or raw.get("observed_at") or _iso_now(),
        "estimated": False,
        "note": None,
    }


def _http_json(url: str, token: str, *, extra_headers: dict[str, str] | None = None) -> tuple[int, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "OmniCompany-Subscriptions/1.0",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310 - 固定官方端点
            raw = resp.read(400_000)
            try:
                return int(resp.status), json.loads(raw.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                return int(resp.status), None
    except urllib.error.HTTPError as exc:
        return int(exc.code), None
    except (OSError, TimeoutError, urllib.error.URLError):
        return 0, None


def _cached_probe(key: str, fn: Any, *, ttl: float = _TTL_DAILY, force: bool = False) -> dict[str, Any]:
    now = time.time()
    with _CACHE_LOCK:
        hit = _PROBE_CACHE.get(key)
        if not force and hit and (now - float(hit.get("ts") or 0)) < ttl:
            return dict(hit["value"])
    value = fn()
    with _CACHE_LOCK:
        _PROBE_CACHE[key] = {"ts": now, "value": value}
    return dict(value)


def _token_from_env(*names: str) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _grok_token() -> str:
    try:
        data = json.loads(_GROK_AUTH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    first = next(iter(data.values()), None)
    if isinstance(first, dict):
        return str(first.get("key") or "")
    return ""


def _opencode_token() -> str:
    try:
        data = json.loads(_OPENCODE_AUTH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    row = data.get("opencode-go") if isinstance(data, dict) else None
    if isinstance(row, dict):
        return str(row.get("key") or "")
    return ""


def _kimi_token() -> str:
    env = _token_from_env("KIMI_API_KEY")
    if env:
        return env
    try:
        data = json.loads(_KIMI_CRED.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("access_token") or "") if isinstance(data, dict) else ""


def _kimi_oauth_token(*, force_refresh: bool = False) -> str:
    """Return a fresh Kimi Code membership OAuth token; API keys cannot call /usages."""
    try:
        data = json.loads(_KIMI_CRED.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    expires_at = data.get("expires_at")
    access = str(data.get("access_token") or "")
    refresh = str(data.get("refresh_token") or "")
    if access and not force_refresh and isinstance(expires_at, (int, float)) and float(expires_at) > time.time() + 30:
        return access
    if not refresh:
        return access
    body = urllib.parse.urlencode(
        {
            "client_id": _KIMI_OAUTH_CLIENT,
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        _KIMI_OAUTH_TOKEN,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "OmniCompany-Subscriptions/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
        return access
    next_access = str(payload.get("access_token") or "")
    if not next_access:
        return access
    data["access_token"] = next_access
    if payload.get("refresh_token"):
        data["refresh_token"] = payload["refresh_token"]
    if payload.get("expires_in"):
        data["expires_in"] = payload["expires_in"]
        data["expires_at"] = int(time.time()) + int(payload["expires_in"])
    if payload.get("scope"):
        data["scope"] = payload["scope"]
    if payload.get("token_type"):
        data["token_type"] = payload["token_type"]
    try:
        _KIMI_CRED.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return next_access


def _models_alive(url: str, token: str) -> dict[str, Any]:
    if not token:
        return {"ok": False, "reason": "未找到凭证"}
    status, payload = _http_json(url, token)
    if status == 200 and isinstance(payload, dict) and payload.get("data") is not None:
        n = len(payload.get("data") or []) if isinstance(payload.get("data"), list) else 0
        return {"ok": True, "models": n}
    if status == 0:
        return {"ok": False, "reason": "探测超时或网络失败"}
    return {"ok": False, "reason": f"凭证探测 HTTP {status}"}


def _kimi_int(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(float(str(raw)))
    except (TypeError, ValueError):
        return None


def _kimi_reset(raw: dict[str, Any]) -> Any:
    for key in ("resetTime", "reset_at", "resetAt", "reset_time"):
        if raw.get(key):
            return raw.get(key)
    return None


def _kimi_window_label(item: dict[str, Any], detail: dict[str, Any], index: int) -> str:
    for key in ("name", "title", "scope"):
        value = item.get(key) or detail.get(key)
        if value:
            return str(value)
    window = item.get("window") if isinstance(item.get("window"), dict) else {}
    duration = _kimi_int(window.get("duration") or item.get("duration") or detail.get("duration"))
    unit = str(window.get("timeUnit") or item.get("timeUnit") or detail.get("timeUnit") or "")
    if duration is not None:
        if "MINUTE" in unit:
            if duration >= 60 and duration % 60 == 0:
                return f"{duration // 60}小时"
            return f"{duration}分钟"
        if "HOUR" in unit:
            return f"{duration}小时"
        if "DAY" in unit:
            return f"{duration}天"
    return f"窗口{index + 1}"


def parse_kimi_usages(payload: Any) -> list[dict[str, Any]]:
    """Parse official GET /coding/v1/usages (Kimi Code membership OAuth)."""
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []

    def add(detail: dict[str, Any], window_id: str, label: str) -> None:
        limit = _kimi_int(detail.get("limit"))
        used = _kimi_int(detail.get("used"))
        remaining = _kimi_int(detail.get("remaining"))
        if used is None and remaining is not None and limit is not None:
            used = max(0, limit - remaining)
        if remaining is None and used is not None and limit is not None:
            remaining = max(0, limit - used)
        if remaining is None and used is None and limit is None:
            return
        capacity = 100.0 if limit is None else float(limit)
        remain = float(remaining) if remaining is not None else None
        used_pct = None
        if used is not None and capacity:
            used_pct = round(min(100.0, 100.0 * used / capacity), 1)
        remaining_pct = _pct(remain, capacity) if remain is not None else (
            None if used_pct is None else round(max(0.0, 100.0 - used_pct), 1)
        )
        reset_at = _kimi_reset(detail)
        rows.append(
            {
                "id": window_id,
                "label": label,
                "remaining": remain if remain is not None else (None if remaining_pct is None else remaining_pct),
                "capacity": capacity,
                "unit": "percent" if capacity == 100.0 else "credits",
                "resets_at": reset_at,
                "reset_in_sec": _reset_in_sec(reset_at),
                "observed_at": _iso_now(),
                "estimated": False,
                "used_pct": used_pct,
                "remaining_pct": remaining_pct,
                "note": None,
            }
        )

    usage = payload.get("usage")
    if isinstance(usage, dict):
        add(usage, "weekly", str(usage.get("name") or usage.get("title") or "周"))
    for index, item in enumerate(payload.get("limits") or []):
        if not isinstance(item, dict):
            continue
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else item
        add(detail, f"limit-{index}", _kimi_window_label(item, detail, index))
    return rows


def _collect_claude_codex(force: bool = False, provider: str | None = None) -> dict[str, dict[str, Any]]:
    from .usage import build_usage, provider_usage

    runtimes = {"claude": "claude_code", "claude-official-2": "claude_2",
                "codex": "codex", "codex-official-2": "codex_2"}
    usage_key = "claude" if provider == "claude" else runtimes.get(provider or "")
    data = ({usage_key: provider_usage(runtimes[provider], force=force)} if provider else build_usage(force=force))
    out: dict[str, dict[str, Any]] = {}
    # Catalog ids name quota surfaces; build_usage keys name runtime accounts.
    # Keep the translation explicit so the second Codex account is never
    # silently dropped or merged into the primary account.
    mapping = {
        "claude": (
            "claude",
            "official_api",
            (("five_hour", "5小时"), ("seven_day", "7天"), ("seven_day_opus", "7天 Opus"), ("seven_day_sonnet", "7天 Sonnet")),
        ),
        "claude-official-2": (
            "claude_2",
            "official_api",
            (("five_hour", "5小时"), ("seven_day", "7天"), ("seven_day_opus", "7天 Opus"), ("seven_day_sonnet", "7天 Sonnet")),
        ),
        "codex": (
            "codex",
            "official_api",
            (("five_hour", "5小时"), ("seven_day", "7天")),
        ),
        "codex-official-2": (
            "codex_2",
            "official_api",
            (("five_hour", "5小时"), ("seven_day", "7天")),
        ),
    }
    for provider, (usage_key, method, windows) in mapping.items():
        raw = data.get(usage_key) if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            continue
        if raw.get("available") is False:
            out[provider] = {
                **{k: raw.get(k) for k in ("account_home", "account_fingerprint", "account_key", "observed_at")},
                "method": method,
                "query_cost": "cheap",
                "available": False,
                "reason": raw.get("reason") or "不可用",
                "stale": bool(raw.get("stale")),
                "stale_reason": raw.get("stale_reason"),
                "windows": [],
            }
            continue
        parsed = []
        for window_id, label in windows:
            item = _window_from_usage(
                window_id,
                label,
                raw.get(window_id) if isinstance(raw.get(window_id), dict) else None,
                observed_at=raw.get("observed_at"),
            )
            if item:
                parsed.append(item)
        out[provider] = {
            **{k: raw.get(k) for k in ("account_home", "account_fingerprint", "account_key", "observed_at")},
            "method": method,
            "query_cost": "cheap",
            "available": bool(parsed),
            "reason": None if parsed else (raw.get("reason") or "无可识别窗口"),
            "stale": bool(raw.get("stale")),
            "stale_reason": raw.get("stale_reason"),
            "windows": parsed,
        }
    return out


def parse_opencode_go_usage(payload: Any) -> list[dict[str, Any]]:
    """Parse official GET /zen/go/v1/usage. percent = 已用%."""
    if not isinstance(payload, dict):
        return []
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else payload
    labels = (("rolling", "5小时"), ("weekly", "7天"), ("monthly", "月"))
    windows: list[dict[str, Any]] = []
    for window_id, label in labels:
        item = usage.get(window_id) if isinstance(usage, dict) else None
        if not isinstance(item, dict) or item.get("percent") is None:
            continue
        used = float(item["percent"])
        resets_at = item.get("resetsAt") or item.get("resets_at")
        windows.append(
            {
                "id": window_id,
                "label": label,
                "used_pct": used,
                "remaining_pct": round(max(0.0, 100.0 - used), 1),
                "remaining": round(max(0.0, 100.0 - used), 1),
                "capacity": 100.0,
                "unit": "percent",
                "resets_at": resets_at,
                "reset_in_sec": _reset_in_sec(resets_at),
                "observed_at": _iso_now(),
                "estimated": False,
                "note": None if item.get("status") in {None, "ok"} else str(item.get("status")),
            }
        )
    return windows


def _collect_opencode(force: bool = False) -> dict[str, Any]:
    def probe() -> dict[str, Any]:
        token = _opencode_token()
        if not token:
            return {"available": False, "method": "daily", "reason": "未找到 OpenCode Go key"}
        status, payload = _http_json(_OPENCODE_USAGE, token)
        if status == 200:
            windows = parse_opencode_go_usage(payload)
            if windows:
                return {"available": True, "method": "official_api", "query_cost": "cheap", "windows": windows}
            return {"available": False, "method": "daily", "reason": "Go usage 无窗口字段"}
        if status == 401:
            return {"available": False, "method": "daily", "reason": "Go usage 401，当前 key 没有有效 Go 订阅"}
        if status == 0:
            return {"available": False, "method": "daily", "reason": "Go usage 探测失败"}
        return {"available": False, "method": "daily", "reason": f"Go usage HTTP {status}"}

    result = _cached_probe("opencode-go.usage", probe, ttl=_TTL_LIVE, force=force)
    result.setdefault("query_cost", "cheap")
    result.setdefault("windows", [])
    return result


def parse_glm_coding_plan_quota(payload: Any) -> list[dict[str, Any]]:
    """Parse GLM Coding Plan ``/api/monitor/usage/quota/limit``.

    实测返回(2026-08-17, pro 档)::

        data.level = "pro"
        data.limits = [
          {"type": "CREDIT_LIMIT", "unit": 3, "number": 5,
           "usage": 12000, "currentValue": 12070, "remaining": 0,
           "percentage": 100, "nextResetTime": 1786969594323},
          {"type": "CREDIT_LIMIT", "unit": 6, "number": 1,
           "usage": 60000, "currentValue": 47578, "remaining": 12421,
           "percentage": 79, "nextResetTime": 1787417585998},
        ]

    字段名反直觉, 按实测对齐: ``usage`` 是**额度上限**, ``currentValue`` 是**已用**,
    ``percentage`` 是已用百分比, ``nextResetTime`` 是毫秒时间戳。``unit``/``number``
    的组合决定窗口长度 —— 实测 (3,5) 的 nextResetTime 正好是 +5h, (6,1) 约 +5.2 天,
    所以 3=小时、6=周。未见过的 unit 不猜, 原样带出 ``unit{u}x{n}`` 供人工确认。
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    limits = data.get("limits")
    if not isinstance(limits, list):
        return []

    unit_names = {3: ("hour", "小时"), 6: ("week", "周")}
    windows: list[dict[str, Any]] = []
    for item in limits:
        if not isinstance(item, dict) or item.get("remaining") is None:
            continue
        raw_unit = item.get("unit")
        number = item.get("number")
        name, zh = unit_names.get(raw_unit, ("", ""))
        if name:
            window_id = f"{number}{name}" if str(number) != "1" else name
            label = f"{number}{zh}" if str(number) != "1" else f"1{zh}"
        else:
            window_id = f"unit{raw_unit}x{number}"
            label = window_id
        capacity = item.get("usage")
        reset_at = _parse_time(
            float(item["nextResetTime"]) / 1000.0 if item.get("nextResetTime") else None
        )
        used_pct = item.get("percentage")
        windows.append(
            {
                "id": window_id,
                "label": label,
                "used_pct": None if used_pct is None else round(float(used_pct), 1),
                "remaining_pct": _pct(item.get("remaining"), capacity),
                "remaining": float(item["remaining"]),
                "capacity": None if capacity is None else float(capacity),
                "consumed": None if item.get("currentValue") is None else float(item["currentValue"]),
                "unit": "credits",
                "resets_at": None if reset_at is None else reset_at.isoformat().replace("+00:00", "Z"),
                "reset_in_sec": None if reset_at is None else _reset_in_sec(reset_at),
                "observed_at": _iso_now(),
                "estimated": False,
                "note": None if float(item["remaining"]) > 0 else "窗口额度已用尽",
            }
        )
    return windows


def _collect_glm(force: bool = False) -> dict[str, Any]:
    """Live GLM Coding Plan credits. Cheap: one authenticated GET, no model call."""

    def probe() -> dict[str, Any]:
        from omnicompany.dashboard.ccdaemon.provider_runtime import zai_api_key

        token = zai_api_key()
        if not token:
            return {
                "available": False,
                "method": "daily",
                "credential_ok": False,
                "reason": "未找到 ZAI_API_KEY(env 或 omnicompany/.env)",
            }
        status, payload = _http_json(_GLM_QUOTA, token)
        if status == 200:
            windows = parse_glm_coding_plan_quota(payload)
            level = ((payload or {}).get("data") or {}).get("level")
            if windows:
                return {
                    "available": True,
                    "method": "official_api",
                    "query_cost": "cheap",
                    "credential_ok": True,
                    "plan_level": level,
                    "windows": windows,
                }
            return {"available": False, "method": "daily", "reason": "额度接口无可识别窗口"}
        if status in (401, 403):
            return {
                "available": False,
                "method": "daily",
                "credential_ok": False,
                "reason": f"额度接口 HTTP {status}, coding plan key 无效或未订阅",
            }
        if status == 0:
            return {"available": False, "method": "daily", "reason": "额度接口探测失败"}
        return {"available": False, "method": "daily", "reason": f"额度接口 HTTP {status}"}

    result = _cached_probe("glm-coding-plan.quota", probe, ttl=_TTL_LIVE, force=force)
    result.setdefault("query_cost", "cheap")
    result.setdefault("windows", [])
    return result


def _collect_kimi(force: bool = False) -> dict[str, Any]:
    def probe() -> dict[str, Any]:
        token = _kimi_oauth_token()
        if not token:
            return {"available": False, "method": "session_slash", "reason": "未找到 Kimi 会员登录态"}
        status, payload = _http_json(_KIMI_USAGES, token)
        if status == 401:
            token = _kimi_oauth_token(force_refresh=True)
            status, payload = _http_json(_KIMI_USAGES, token) if token else (401, None)
        if status == 200:
            windows = parse_kimi_usages(payload)
            if windows:
                return {"available": True, "method": "official_api", "query_cost": "cheap", "windows": windows}
            return {"available": False, "method": "session_slash", "reason": "usages 无余额字段"}
        if status == 403:
            return {"available": False, "method": "session_slash", "reason": "usages 无权限"}
        if status == 0:
            return {"available": False, "method": "session_slash", "reason": "Kimi usages 探测失败"}
        return {"available": False, "method": "session_slash", "reason": f"Kimi usages HTTP {status}"}

    result = _cached_probe("kimi.usages", probe, ttl=_TTL_LIVE, force=force)
    result.setdefault("query_cost", "cheap")
    result.setdefault("windows", [])
    return result


def _slash_to_live(provider: str, parsed: dict[str, Any]) -> dict[str, Any]:
    reset_at = parsed.get("reset_at")
    reset_note = None
    if not reset_at:
        # Grok occasionally omits reset metadata after a quota reaches zero.
        # Keep the last future reset from the provider CLI history instead of
        # overwriting it with null.
        try:
            state = _control()._state()  # noqa: SLF001 - read-only history lookup
            observations = ((state.get("providers") or {}).get(provider) or {}).get("observations") or []
            for previous in reversed(observations):
                candidate = previous.get("reset_at")
                if candidate and _reset_in_sec(candidate) is not None:
                    reset_at = candidate
                    reset_note = "reset metadata omitted by current /usage; retained last future observation"
                    break
        except Exception:  # noqa: BLE001
            pass
    try:
        observe_subscription(
            provider,
            remaining=float(parsed["remaining"]),
            capacity=parsed.get("capacity"),
            unit=str(parsed.get("unit") or "credits"),
            reset_at=reset_at,
            window_id="primary",
            source="provider_cli",
            evidence="hidden PTY /usage",
            confidence="reported",
        )
    except Exception:  # noqa: BLE001
        pass
    remaining = float(parsed["remaining"])
    capacity = parsed.get("capacity")
    remaining_pct = parsed.get("remaining_pct")
    if remaining_pct is None and capacity:
        remaining_pct = _pct(remaining, float(capacity))
    return {
        "available": True,
        "method": "session_slash",
        "query_cost": "daily",
        "windows": [
            {
                "id": "primary",
                "label": "/usage",
                "remaining": remaining,
                "capacity": None if capacity is None else float(capacity),
                "unit": str(parsed.get("unit") or "credits"),
                "remaining_pct": remaining_pct,
                "used_pct": parsed.get("used_pct"),
                "resets_at": reset_at,
                "reset_in_sec": _reset_in_sec(reset_at),
                "observed_at": _iso_now(),
                "estimated": False,
                "note": reset_note or "来自隐藏会话 /usage",
            }
        ],
    }


def _collect_slash_usage(provider: str, force: bool = False) -> dict[str, Any]:
    def probe() -> dict[str, Any]:
        from .slash_usage import probe_slash_usage

        result = probe_slash_usage(provider)
        if result.get("ok"):
            return _slash_to_live(provider, result)
        return {
            "available": False,
            "method": "session_slash",
            "query_cost": "daily",
            "reason": result.get("reason") or " /usage 没读到余额",
            "windows": [],
        }

    result = _cached_probe(f"{provider}.slash", probe, ttl=_TTL_DAILY, force=force)
    result.setdefault("query_cost", "daily")
    result.setdefault("windows", [])
    return result


def _slash_cache_fresh(provider: str) -> bool:
    now = time.time()
    with _CACHE_LOCK:
        hit = _PROBE_CACHE.get(f"{provider}.slash")
    return bool(hit and (now - float(hit.get("ts") or 0)) < _TTL_DAILY)


def _cached_slash(provider: str) -> dict[str, Any] | None:
    with _CACHE_LOCK:
        hit = _PROBE_CACHE.get(f"{provider}.slash")
    if not hit:
        return None
    return dict(hit["value"])


def _ensure_slash_background() -> None:
    """Start hidden /usage probes once a day without blocking the page."""
    global _SLASH_THREAD
    if all(_slash_cache_fresh(provider) for provider in ("kiro", "grok")):
        return
    with _CACHE_LOCK:
        if _SLASH_THREAD is not None and _SLASH_THREAD.is_alive():
            return

        def work() -> None:
            for provider in ("kiro", "grok"):
                try:
                    _collect_slash_usage(provider, force=False)
                except Exception:  # noqa: BLE001
                    continue
            with _CACHE_LOCK:
                _LIVE_CACHE["data"] = None
                _LIVE_CACHE["ts"] = 0.0

        _SLASH_THREAD = threading.Thread(target=work, name="sub-slash-usage", daemon=True)
        _SLASH_THREAD.start()


def _provider_usage_url(provider: str) -> str:
    catalog = _control().catalog
    for row in catalog.get("providers") or []:
        if str(row.get("id")) == provider:
            return str((row.get("usage_query") or {}).get("url") or "")
    return ""


def _web_to_live(provider: str, parsed: dict[str, Any], *, url: str = "") -> dict[str, Any]:
    try:
        observe_subscription(
            provider,
            remaining=float(parsed["remaining"]),
            capacity=parsed.get("capacity"),
            unit=str(parsed.get("unit") or "credits"),
            reset_at=parsed.get("reset_at"),
            window_id="primary",
            source="usage_page",
            evidence=url or "auto webpage",
            confidence="reported",
        )
    except Exception:  # noqa: BLE001
        pass
    remaining = float(parsed["remaining"])
    capacity = parsed.get("capacity")
    remaining_pct = parsed.get("remaining_pct")
    if remaining_pct is None and capacity:
        remaining_pct = _pct(remaining, float(capacity))
    return {
        "available": True,
        "method": "webpage",
        "query_cost": "daily",
        "windows": [
            {
                "id": "primary",
                "label": "用量页",
                "remaining": remaining,
                "capacity": None if capacity is None else float(capacity),
                "unit": str(parsed.get("unit") or "credits"),
                "remaining_pct": remaining_pct,
                "used_pct": parsed.get("used_pct"),
                "resets_at": parsed.get("reset_at"),
                "reset_in_sec": _reset_in_sec(parsed.get("reset_at")),
                "observed_at": _iso_now(),
                "estimated": False,
                "note": "自动打开用量页读到",
            }
        ],
    }


def _collect_web_usage(provider: str, force: bool = False) -> dict[str, Any]:
    def probe() -> dict[str, Any]:
        from .slash_usage import looks_like_login, parse_usage_text
        from .subscription_browser import capture_page

        url = _provider_usage_url(provider)
        if not url:
            return {"available": False, "method": "webpage", "reason": "没有用量页地址", "windows": []}
        try:
            result = capture_page(url)
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "method": "webpage", "reason": f"打开用量页失败: {exc}", "windows": []}
        text = str(result.get("text") or "")
        if looks_like_login(str(result.get("url") or ""), str(result.get("title") or ""), text):
            return {
                "available": False,
                "method": "webpage",
                "needs_login": True,
                "reason": "登录档还没登这个站，点 ⋯ 登录一次",
                "windows": [],
            }
        parsed = parse_usage_text(text)
        if not parsed:
            return {
                "available": False,
                "method": "webpage",
                "reason": "打开了用量页，但没读到余额数字",
                "windows": [],
            }
        return _web_to_live(provider, parsed, url=str(result.get("url") or url))

    result = _cached_probe(f"{provider}.web", probe, ttl=_TTL_DAILY, force=force)
    result.setdefault("query_cost", "daily")
    result.setdefault("windows", [])
    return result


def _cached_web(provider: str) -> dict[str, Any] | None:
    with _CACHE_LOCK:
        hit = _PROBE_CACHE.get(f"{provider}.web")
    if not hit:
        return None
    return dict(hit["value"])


def _web_cache_fresh(provider: str) -> bool:
    now = time.time()
    with _CACHE_LOCK:
        hit = _PROBE_CACHE.get(f"{provider}.web")
    return bool(hit and (now - float(hit.get("ts") or 0)) < _TTL_DAILY)


def _ensure_web_background() -> None:
    global _WEB_THREAD
    if all(_web_cache_fresh(provider) for provider in _WEB_PROVIDERS):
        return
    with _CACHE_LOCK:
        if _WEB_THREAD is not None and _WEB_THREAD.is_alive():
            return

        def work() -> None:
            for provider in _WEB_PROVIDERS:
                try:
                    _collect_web_usage(provider, force=False)
                except Exception:  # noqa: BLE001
                    continue
            with _CACHE_LOCK:
                _LIVE_CACHE["data"] = None
                _LIVE_CACHE["ts"] = 0.0

        _WEB_THREAD = threading.Thread(target=work, name="sub-web-usage", daemon=True)
        _WEB_THREAD.start()


def natural_week_bounds(now: datetime | None = None) -> tuple[datetime, datetime, datetime]:
    """Return (last_monday, this_monday, next_monday) in Asia/Shanghai."""
    current = (now or datetime.now(_SHANGHAI)).astimezone(_SHANGHAI)
    this_monday = current.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=current.weekday())
    return this_monday - timedelta(days=7), this_monday, this_monday + timedelta(days=7)


def _row_when(row: dict[str, Any]) -> datetime | None:
    stamp = row.get("timestamp")
    if isinstance(stamp, (int, float)):
        value = float(stamp)
        if value > 10_000_000_000:
            value = value / 1000.0
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return _parse_time(stamp)


def _row_cost_usd(row: dict[str, Any]) -> float:
    raw = row.get("cost_usd")
    if raw not in {None, ""}:
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            pass
    try:
        from omnicompany.runtime.llm.llm import _estimate_cost
    except Exception:  # noqa: BLE001
        return 0.0
    return max(
        0.0,
        float(
            _estimate_cost(
                str(row.get("model") or ""),
                int(row.get("input_tokens") or 0),
                int(row.get("output_tokens") or 0),
                int(row.get("cache_read_tokens") or 0),
                int(row.get("cache_creation_tokens") or 0),
            )
        ),
    )


def _is_the_company_meter_row(row: dict[str, Any]) -> bool:
    model = str(row.get("model") or "").strip().lower()
    if not model or model in _THE_COMPANY_EXCLUDE_MODELS:
        return False
    provider = str(row.get("provider") or "").strip().lower()
    if provider and provider not in {"the_company", "the_company-api", "llm-proxy"}:
        return False
    return True


def sum_the_company_week_costs(
    records: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    last_monday, this_monday, next_monday = natural_week_bounds(now)
    this_week = 0.0
    last_week = 0.0
    calls = 0
    for row in records:
        if not _is_the_company_meter_row(row):
            continue
        when = _row_when(row)
        if when is None:
            continue
        if last_monday <= when < this_monday:
            last_week += _row_cost_usd(row)
        elif this_monday <= when < next_monday:
            this_week += _row_cost_usd(row)
            calls += 1
    reference = last_week if last_week > 0 else this_week
    used_pct = None if reference <= 0 else round(min(100.0, 100.0 * this_week / reference), 1)
    remaining_pct = None if used_pct is None else round(max(0.0, 100.0 - used_pct), 1)
    current = (now or datetime.now(_SHANGHAI)).astimezone(_SHANGHAI)
    return {
        "this_week": round(this_week, 4),
        "last_week": round(last_week, 4),
        "calls": calls,
        "used_pct": used_pct,
        "remaining_pct": remaining_pct,
        "resets_at": next_monday.isoformat(),
        "reset_in_sec": max(0, int((next_monday - current).total_seconds())),
        "week_start": this_monday.isoformat(),
    }


def _read_meter_since(earliest: datetime) -> list[dict[str, Any]]:
    from omnicompany.core.config import omni_workspace_root

    path = Path(os.environ.get("OMNI_LLM_METER_PATH") or (omni_workspace_root() / "data" / "llm" / "meter.jsonl"))
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("rb") as handle:
            if size > 8_000_000:
                handle.seek(size - 8_000_000)
                handle.readline()
            for raw in handle:
                try:
                    row = json.loads(raw.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def _read_the_company_snapshot() -> dict[str, Any] | None:
    """Read the latest account-dashboard snapshot, when one was supplied."""
    try:
        payload = json.loads(_THE_COMPANY_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    reset_at = _parse_time(payload.get("next_reset_at"))
    if reset_at is None or reset_at <= _utc_now():
        return None
    try:
        weekly = float(payload["weekly_used_usd"])
        budget = float(payload["weekly_budget_usd"])
    except (KeyError, TypeError, ValueError):
        return None
    if weekly < 0 or budget <= 0 or weekly > budget:
        return None
    return {**payload, "weekly_used_usd": weekly, "weekly_budget_usd": budget}


def _collect_the_company_week(force: bool = False) -> dict[str, Any]:
    def probe() -> dict[str, Any]:
        last_monday, _, _ = natural_week_bounds()
        snapshot = _read_the_company_snapshot()
        summary = sum_the_company_week_costs(_read_meter_since(last_monday))
        if snapshot:
            weekly = snapshot["weekly_used_usd"]
            budget = snapshot["weekly_budget_usd"]
            summary.update(
                {
                    "this_week": weekly,
                    "used_pct": round(100.0 * weekly / budget, 1),
                    "remaining_pct": round(100.0 - 100.0 * weekly / budget, 1),
                    "resets_at": snapshot["next_reset_at"],
                    "reset_in_sec": _reset_in_sec(snapshot["next_reset_at"]),
                }
            )
        alive = _models_alive(_THE_COMPANY_MODELS, _token_from_env("THE_COMPANY_API_KEY"))
        window = {
            "id": "natural_week",
            "label": "本周",
            "remaining": summary["this_week"],
            "capacity": snapshot["weekly_budget_usd"] if snapshot else (summary["last_week"] or None),
            "unit": "usd",
            "used_pct": summary["used_pct"],
            "remaining_pct": summary["remaining_pct"],
            "resets_at": summary["resets_at"],
            "reset_in_sec": summary["reset_in_sec"],
            "observed_at": _iso_now(),
            "estimated": False,
            "note": "account dashboard snapshot (user supplied)" if snapshot else (None if not summary["last_week"] else f"对照上周 ${summary['last_week']:.2f}"),
        }
        result = {
            "available": True,
            "method": "local_meter",
            "query_cost": "cheap",
            "credential_ok": bool(alive.get("ok")),
            "windows": [window],
        }
        if snapshot:
            result["method"] = "account_snapshot"
            result["account_snapshot"] = snapshot
        return result

    result = _cached_probe("the_company-api.week", probe, ttl=_TTL_LIVE, force=force)
    result.setdefault("query_cost", "cheap")
    result.setdefault("windows", [])
    return result


def _collect_key_alive(provider: str, force: bool = False) -> dict[str, Any]:
    def probe() -> dict[str, Any]:
        if provider == "the_company-api":
            alive = _models_alive(_THE_COMPANY_MODELS, _token_from_env("THE_COMPANY_API_KEY"))
        elif provider == "qwen-token-plan":
            alive = _models_alive(_QWEN_MODELS, _token_from_env("DASHSCOPE_TOKEN_PLAN_API_KEY", "QWENCLOUD_TOKEN_PLAN_API_KEY"))
        elif provider == "grok":
            alive = _models_alive(_GROK_MODELS, _grok_token())
        else:
            return {"available": False, "method": "daily", "reason": "无便宜探测口"}
        if alive.get("ok"):
            return {
                "available": False,
                "method": "daily",
                "credential_ok": True,
                "reason": f"凭证有效({alive.get('models') or 0} 模型)，余额需网页日校准",
            }
        return {"available": False, "method": "daily", "credential_ok": False, "reason": alive.get("reason") or "凭证无效"}

    result = _cached_probe(f"{provider}.alive", probe, force=force)
    result.setdefault("query_cost", "daily")
    result.setdefault("windows", [])
    return result


def _meter_consumed_since(observed_at: str | None, unit: str) -> float | None:
    if not observed_at:
        return None
    start = _parse_time(observed_at)
    if start is None:
        return None
    from omnicompany.core.config import omni_workspace_root

    path = Path(os.environ.get("OMNI_LLM_METER_PATH") or (omni_workspace_root() / "data" / "llm" / "meter.jsonl"))
    if not path.is_file():
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    consumed = 0.0
    try:
        with path.open("rb") as handle:
            if size > 512_000:
                handle.seek(size - 512_000)
                handle.readline()
            for raw in handle:
                try:
                    row = json.loads(raw.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                stamp = row.get("timestamp")
                when = None
                if isinstance(stamp, (int, float)):
                    if stamp > 10_000_000_000:
                        stamp = stamp / 1000.0
                    when = datetime.fromtimestamp(float(stamp), timezone.utc)
                else:
                    when = _parse_time(stamp)
                if when is None or when < start:
                    continue
                if unit in {"usd", "cost", "dollar"}:
                    consumed += float(row.get("cost_usd") or 0.0)
                else:
                    consumed += float(row.get("input_tokens") or 0) + float(row.get("output_tokens") or 0)
                    consumed += float(row.get("cache_read_tokens") or 0) + float(row.get("cache_creation_tokens") or 0)
    except OSError:
        return None
    return consumed


def _control() -> ResourceControl:
    return ResourceControl(catalog_path=default_catalog_path(), state_dir=default_state_dir())


def _latest_windows(status_row: dict[str, Any] | None, provider: str | None = None) -> list[dict[str, Any]]:
    if not status_row:
        return []
    windows = []
    for item in status_row.get("windows") or []:
        if not isinstance(item, dict):
            continue
        windows.append(
            {
                "id": str(item.get("window_id") or "primary"),
                "label": str(item.get("window_id") or "primary"),
                "remaining": item.get("remaining"),
                "capacity": item.get("capacity"),
                "unit": item.get("unit") or "credits",
                "reset_at": item.get("reset_at"),
                "resets_at": item.get("reset_at"),
                "observed_at": item.get("observed_at"),
                "window_id": item.get("window_id") or "primary",
            }
        )
    if provider:
        try:
            state = _control()._state()  # noqa: SLF001 - read-only history lookup
            history = ((state.get("providers") or {}).get(provider) or {}).get("observations") or []
            for window in windows:
                if window.get("reset_at"):
                    continue
                for previous in reversed(history):
                    if previous.get("window_id") == window.get("id") and previous.get("reset_at"):
                        if _reset_in_sec(previous["reset_at"]) is not None:
                            window["reset_at"] = previous["reset_at"]
                            window["resets_at"] = previous["reset_at"]
                            break
        except Exception:  # noqa: BLE001
            pass
    return windows


def _decorate(
    config: dict[str, Any],
    live: dict[str, Any] | None,
    status_row: dict[str, Any] | None,
) -> dict[str, Any]:
    query = config.get("usage_query") or {}
    windows = [{**w, "reset_in_sec": _reset_in_sec(w.get("resets_at")) if w.get("resets_at") else w.get("reset_in_sec")}
               for w in (live or {}).get("windows") or []]
    available = bool((live or {}).get("available")) and bool(windows)
    method = str((live or {}).get("method") or "daily")
    reason = (live or {}).get("reason")
    stale = bool((live or {}).get("stale"))
    if not windows:
        # Unscoped historical calibration cannot be attributed to today's login.
        control_windows = [] if config["id"] in _ACCOUNT_PROVIDERS else _latest_windows(status_row, str(config["id"]))
        estimated = []
        for item in control_windows:
            consumed = None
            if str(config["id"]) == "the_company-api":
                consumed = _meter_consumed_since(item.get("observed_at"), str(item.get("unit") or "tokens"))
            estimated.append(estimate_window(item, consumed=consumed))
        windows = estimated
        if windows:
            available = any(item.get("remaining") is not None or item.get("remaining_pct") is not None for item in windows)
            method = "estimated"
            reason = reason if live else None
            stale = True
        elif live and live.get("credential_ok"):
            reason = live.get("reason")
        elif not reason:
            reason = "尚无校准。打开官网看一次余额，填进本页即可开始估算。"
    next_reset_in_sec = None
    next_reset_at = None
    observed_at = None
    remaining_ratio = None
    for item in windows:
        reset_sec = item.get("reset_in_sec")
        if reset_sec is not None and (next_reset_in_sec is None or reset_sec < next_reset_in_sec):
            next_reset_in_sec = reset_sec
            next_reset_at = item.get("resets_at")
        observed = item.get("observed_at")
        if observed and (observed_at is None or str(observed) > str(observed_at)):
            observed_at = observed
        ratio = item.get("remaining_pct")
        if ratio is not None:
            remaining_ratio = (ratio / 100.0) if remaining_ratio is None else min(remaining_ratio, ratio / 100.0)
        elif item.get("remaining") is not None and item.get("capacity"):
            ratio = float(item["remaining"]) / float(item["capacity"])
            remaining_ratio = ratio if remaining_ratio is None else min(remaining_ratio, ratio)
    needs_calibration = method in {"daily", "estimated"} and (
        not windows or any(item.get("remaining") is None for item in windows) or stale
    )
    observed = _parse_time(observed_at)
    if observed is not None and (_utc_now() - observed).total_seconds() > _TTL_DAILY:
        needs_calibration = True
    installed = None
    if str(config.get("id")) == "grok":
        installed = _GROK_BIN.is_file()
    return {
        "id": config["id"],
        "display_name": {"claude": "Claude-1", "claude-official-2": "Claude-2"}.get(config["id"], config.get("display_name") or config["id"]),
        "runtime_provider": config.get("runtime_provider"),
        "quota_model": config.get("quota_model"),
        "available": available,
        "method": method,
        "query_cost": (live or {}).get("query_cost") or ("cheap" if method in {"official_api", "local_log", "local_meter"} else "daily"),
        "reason": reason,
        "stale": stale,
        "stale_reason": (live or {}).get("stale_reason"),
        "credential_ok": (live or {}).get("credential_ok"),
        "needs_calibration": needs_calibration,
        "usage_url": query.get("url"),
        "usage_command": query.get("command"),
        "windows": windows,
        "next_reset_at": next_reset_at,
        "next_reset_in_sec": next_reset_in_sec,
        "observed_at": observed_at,
        "remaining_ratio": remaining_ratio,
        "installed": installed,
        "account_snapshot": (live or {}).get("account_snapshot"),
        **{k: (live or {}).get(k) for k in ("account_home", "account_fingerprint", "checked_at", "refreshing", "refresh_error")},
    }


_ACCOUNT_PROVIDERS = {"claude": "claude_code", "claude-official-2": "claude_2",
                      "codex": "codex", "codex-official-2": "codex_2"}


def _subscription_collectors() -> dict[str, Any]:
    return {
        **{key: (lambda force, key=key: _collect_claude_codex(force=force, provider=key)[key])
           for key in _ACCOUNT_PROVIDERS},
        "kimi": _collect_kimi,
        "opencode-go": _collect_opencode,
        "the_company-api": _collect_the_company_week,
        "glm-coding-plan": _collect_glm,
        "qwen-token-plan": lambda force: _collect_key_alive("qwen-token-plan", force=force),
    }


def _refresh_subscription(key: str, identity: str, collector: Any, force: bool) -> None:
    try:
        try:
            value = collector(force=force)
        except Exception as exc:  # a failed account never fails the snapshot
            value = {"available": False, "windows": [], "reason": f"读取失败：{type(exc).__name__}"}
        with _CACHE_LOCK:
            prior = _ROW_CACHE.get(key, {})
            if prior.get("identity") != identity:
                return
            old = prior.get("value") or {}
            failed = not value.get("available") or bool(value.get("stale"))
            if failed and not value.get("windows") and old.get("windows"):
                value = {**old, **value, "windows": old["windows"], "available": True, "stale": True}
            value = {**value, "checked_at": _iso_now(), "refreshing": False,
                     "refresh_error": (value.get("stale_reason") or value.get("reason")) if failed else None}
            _ROW_CACHE[key] = {"identity": identity, "ts": time.time(), "value": value}
    finally:
        with _CACHE_LOCK:
            _REFRESHING.discard(identity)


def build_subscriptions(*, force: bool = False, sort: str = "reset_at") -> dict[str, Any]:
    """Return current rows immediately; each account refreshes independently.

    Cache age is the last attempt, observation age belongs to the source data.
    Concurrent readers and repeated force clicks share the same in-flight job.
    """
    from . import usage
    from omnicompany.core.agent_environment import agent_environment

    control = _control()
    status_index = {str(row.get("id")): row for row in (control.status().get("providers") or [])}
    collectors = _subscription_collectors()
    if force:
        with _CACHE_LOCK:
            if not (_WEB_THREAD and _WEB_THREAD.is_alive()):
                for provider in _WEB_PROVIDERS:
                    _PROBE_CACHE.pop(f"{provider}.web", None)
    _ensure_web_background()
    _ensure_slash_background()
    rows = []
    now = time.time()
    for config in control.catalog.get("providers") or []:
        key = str(config["id"])
        runtime = agent_environment(config.get("runtime_provider") or _ACCOUNT_PROVIDERS.get(key, ""))
        if runtime is not None and not runtime.is_active:
            continue
        account = usage.account_identity(_ACCOUNT_PROVIDERS[key]) if key in _ACCOUNT_PROVIDERS else {}
        identity = account.get("account_key") or key
        with _CACHE_LOCK:
            state = _ROW_CACHE.get(key, {})
            if state.get("identity") != identity:
                state = {"identity": identity, "ts": 0.0, "value": {**account}}
                # Only identity-bound observations survive a service restart.
                last = usage._LAST_GOOD.get(identity)
                if last and key in _ACCOUNT_PROVIDERS:
                    windows = []
                    for window_id, label in (("five_hour", "5小时"), ("seven_day", "7天"),
                                             ("seven_day_opus", "7天 Opus"), ("seven_day_sonnet", "7天 Sonnet")):
                        window = _window_from_usage(window_id, label, last.get(window_id), observed_at=last.get("observed_at"))
                        if window:
                            windows.append(window)
                    state["value"].update(available=bool(windows), windows=windows, stale=True,
                                          method="official_api", stale_reason="上次读数，正在重新核验")
                _ROW_CACHE[key] = state
            live = dict(state.get("value") or {})
            if key in collectors and (force or now - state["ts"] >= _TTL_LIVE) and identity not in _REFRESHING:
                _REFRESHING.add(identity)
                _REFRESH_POOL.submit(_refresh_subscription, key, identity, collectors[key], force)
            live["refreshing"] = identity in _REFRESHING
        if key in {"kiro", "grok"}:
            live = _cached_slash(key) or live
            live["refreshing"] = bool(_SLASH_THREAD and _SLASH_THREAD.is_alive() and not _slash_cache_fresh(key))
        if key in _WEB_PROVIDERS:
            refreshing = live.get("refreshing")
            live = _cached_web(key) or live
            live["refreshing"] = bool(refreshing or (_WEB_THREAD and _WEB_THREAD.is_alive() and not _web_cache_fresh(key)))
        rows.append(_decorate(config, live, status_index.get(key)))
    # Existing expensive daily probes keep their established background policy.
    return {"generated_at": _iso_now(), "source": "independent account snapshots",
            "sort": sort, "cache": "snapshot", "refreshing": any(row.get("refreshing") for row in rows),
            "providers": sort_providers(rows, sort)}


def observe_subscription(
    provider: str,
    *,
    remaining: float,
    capacity: float | None = None,
    unit: str = "credits",
    reset_at: str | None = None,
    window_id: str = "primary",
    source: str = "usage_page",
    evidence: str = "",
    confidence: str = "reported",
) -> dict[str, Any]:
    row = _control().observe(
        provider,
        remaining=remaining,
        capacity=capacity,
        unit=unit,
        reset_at=reset_at,
        window_id=window_id,
        source=source,
        evidence=evidence,
        confidence=confidence,
    )
    with _CACHE_LOCK:
        _LIVE_CACHE["data"] = None
        _LIVE_CACHE["ts"] = 0.0
    return row


# 官方"便宜读数"→观测账本。渲染路径保持只读; 由这条入口(CLI/cron)把 exact 读数
# 落进 ResourceControl 账本, 这样 `omni agent budget status` 不用人工报数也能保鲜。
_OFFICIAL_API_COLLECTORS: dict[str, Any] = {
    "glm-coding-plan": _collect_glm,
    "opencode-go": _collect_opencode,
}


def refresh_official_api_observations(
    providers: Iterable[str] | None = None,
    *,
    force: bool = True,
) -> list[dict[str, Any]]:
    """Read each provider's official usage API and record the windows as observations."""
    selected = [p for p in (providers or _OFFICIAL_API_COLLECTORS) if p in _OFFICIAL_API_COLLECTORS]
    unknown = sorted(set(providers or []) - set(_OFFICIAL_API_COLLECTORS))
    results: list[dict[str, Any]] = []
    for provider in unknown:
        results.append({"provider": provider, "recorded": 0, "reason": "no official-api collector"})
    for provider in selected:
        live = _OFFICIAL_API_COLLECTORS[provider](force=force)
        endpoint = ""
        for config in _control().catalog.get("providers") or []:
            if str(config.get("id")) == provider:
                query = config.get("usage_query") or {}
                endpoint = str(query.get("endpoint") or query.get("url") or "")
                break
        windows = live.get("windows") or []
        if not live.get("available") or not windows:
            results.append({
                "provider": provider,
                "recorded": 0,
                "reason": live.get("reason") or "no windows",
            })
            continue
        recorded = 0
        for window in windows:
            if window.get("remaining") is None:
                continue
            observe_subscription(
                provider,
                remaining=float(window["remaining"]),
                capacity=float(window["capacity"]) if window.get("capacity") else None,
                unit=str(window.get("unit") or "credits"),
                reset_at=window.get("resets_at"),
                window_id=str(window.get("id") or "primary"),
                source="billing_api",
                evidence=endpoint or "official usage API",
                confidence="exact",
            )
            recorded += 1
        results.append({
            "provider": provider,
            "recorded": recorded,
            "plan_level": live.get("plan_level"),
            "windows": [
                {k: window.get(k) for k in ("id", "remaining", "capacity", "unit", "used_pct", "resets_at")}
                for window in windows
            ],
        })
    return results


class SubscriptionObserveBody(BaseModel):
    provider: str
    remaining: float
    capacity: float | None = None
    unit: str = "credits"
    reset_at: str | None = None
    window_id: str = "primary"
    source: str = "usage_page"
    evidence: str = ""
    confidence: str = "reported"


subscription_router = APIRouter()
dashboard_subscription_router = APIRouter(prefix="/api/boss-sight", tags=["subscriptions"])


@subscription_router.get("/subscriptions")
async def get_subscriptions(force: bool = False, sort: str = "reset_at") -> dict[str, Any]:
    import asyncio

    if sort not in {"reset_at", "observed_at", "remaining"}:
        raise HTTPException(status_code=400, detail="sort must be reset_at, observed_at or remaining")
    return await asyncio.get_running_loop().run_in_executor(
        None, lambda: build_subscriptions(force=force, sort=sort)
    )


@subscription_router.post("/subscriptions/observe")
async def post_subscription_observe(body: SubscriptionObserveBody) -> dict[str, Any]:
    try:
        return observe_subscription(
            body.provider,
            remaining=body.remaining,
            capacity=body.capacity,
            unit=body.unit,
            reset_at=body.reset_at,
            window_id=body.window_id,
            source=body.source,
            evidence=body.evidence,
            confidence=body.confidence,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


class SubscriptionBrowserBody(BaseModel):
    provider: str | None = None
    url: str | None = None


def _provider_login_url(provider: str | None, url: str | None) -> str:
    if url:
        return url
    if not provider:
        raise HTTPException(status_code=400, detail="provider or url required")
    catalog = _control().catalog
    for row in catalog.get("providers") or []:
        if str(row.get("id")) == provider:
            found = (row.get("usage_query") or {}).get("url")
            if found:
                return str(found)
    raise HTTPException(status_code=404, detail=f"no login url for {provider}")


@subscription_router.get("/session-placements")
async def get_session_placements() -> dict[str, Any]:
    from .session_placement import placements_payload

    return placements_payload()


@subscription_router.get("/subscriptions/browser")
async def get_subscription_browser() -> dict[str, Any]:
    from .subscription_browser import browser_status

    return browser_status()


@subscription_router.post("/subscriptions/browser/open")
async def post_subscription_browser_open(body: SubscriptionBrowserBody) -> dict[str, Any]:
    import asyncio

    from .subscription_browser import open_login

    try:
        return await asyncio.get_running_loop().run_in_executor(
            None, lambda: open_login(_provider_login_url(body.provider, body.url))
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None


@subscription_router.post("/subscriptions/browser/capture")
async def post_subscription_browser_capture(body: SubscriptionBrowserBody) -> dict[str, Any]:
    import asyncio

    from .subscription_browser import capture_page
    from .slash_usage import parse_usage_text

    target = body.url or (_provider_login_url(body.provider, None) if body.provider else None)

    def work() -> dict[str, Any]:
        result = capture_page(target)
        parsed = parse_usage_text(str(result.get("text") or ""))
        if parsed and body.provider:
            try:
                observe_subscription(
                    body.provider,
                    remaining=float(parsed["remaining"]),
                    capacity=parsed.get("capacity"),
                    unit=str(parsed.get("unit") or "credits"),
                    reset_at=parsed.get("reset_at"),
                    source="usage_page",
                    evidence=result.get("url") or "",
                )
                result["observed"] = True
                result["parsed"] = {
                    key: parsed[key]
                    for key in ("remaining", "capacity", "unit", "reset_at", "remaining_pct")
                    if key in parsed
                }
            except Exception as exc:  # noqa: BLE001
                result["observe_error"] = str(exc)
        else:
            result["observed"] = False
            result["parsed"] = None
        return result

    try:
        return await asyncio.get_running_loop().run_in_executor(None, work)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None


@subscription_router.post("/subscriptions/slash")
async def post_subscription_slash(body: SubscriptionBrowserBody) -> dict[str, Any]:
    import asyncio

    if body.provider not in {"kiro", "grok", "kimi"}:
        raise HTTPException(status_code=400, detail="slash /usage probe only supports kiro, grok and kimi")
    from .slash_usage import probe_slash_usage

    result = await asyncio.get_running_loop().run_in_executor(
        None, lambda: probe_slash_usage(str(body.provider))
    )
    if result.get("ok"):
        live = _slash_to_live(str(body.provider), result)
        return {"ok": True, **live}
    return result


dashboard_subscription_router.include_router(subscription_router)

__all__ = [
    "build_subscriptions",
    "dashboard_subscription_router",
    "estimate_window",
    "observe_subscription",
    "parse_kimi_usages",
    "parse_opencode_go_usage",
    "sort_providers",
    "sum_the_company_week_costs",
    "natural_week_bounds",
    "subscription_router",
]
