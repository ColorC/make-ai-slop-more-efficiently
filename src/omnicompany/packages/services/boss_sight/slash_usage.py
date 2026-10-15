"""Run TUI ``/usage`` in a hidden Agent Worker and parse remaining credits.

Kiro and Grok expose remaining quota as an interactive slash command, not a
CLI subcommand. This starts a short hidden worker, types ``/usage``, reads
the existing PTY websocket, then kills the session.
"""

from __future__ import annotations

import calendar
import json
import re
import time
from datetime import datetime, timedelta
from typing import Any

from omnicompany.packages.services._core.agent.control_plane import (
    AgentLaunchRequest,
    _daemon_base_url,
    launch_agent_worker,
    send_agent_input,
    _request_json,
)

_ANSI = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|].*?(?:\x07|\x1b\\))")
_PCT = re.compile(
    r"(?:remaining|left|剩余|remain)\D{0,12}(\d+(?:\.\d+)?)\s*%|"
    r"(\d+(?:\.\d+)?)\s*%\s*(?:remaining|left|剩余)|"
    r"creditUsagePercent[\"']?\s*[:=]\s*(\d+(?:\.\d+)?)",
    re.I,
)
_USED_PCT = re.compile(r"(?:used|已用|已使用|用量)\D{0,16}(\d+(?:\.\d+)?)\s*%", re.I)
_CN_REMAIN = re.compile(
    r"(?:剩余(?:额度|量|credits?)?|余量|可用额度|可用)\D{0,10}(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)",
    re.I,
)
_CN_TOTAL = re.compile(
    r"(?:总量|总额度|总额|套餐额度|额度上限)\D{0,10}(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)",
    re.I,
)
_BAR_PCT = re.compile(r"[█░▓▒]{4,}\s*(\d+(?:\.\d+)?)\s*%")
_FRACTION = re.compile(
    r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*(credits?|额度)",
    re.I,
)
_RESET = re.compile(
    r"(?:reset|重置|renew|resets?\s+at)[^\d]{0,20}(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?)",
    re.I,
)
_RESET_HUMAN = re.compile(
    r"Resets?:\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{1,2}):(\d{2})",
    re.I,
)
_MONTHS = {name.lower(): index for index, name in enumerate(calendar.month_name) if name}


def _human_reset(month: str, day: str, hour: str, minute: str) -> str | None:
    month_num = _MONTHS.get(month.lower())
    if not month_num:
        return None
    local = datetime.now().astimezone()
    try:
        stamp = datetime(local.year, month_num, int(day), int(hour), int(minute), tzinfo=local.tzinfo)
    except ValueError:
        return None
    if stamp < local - timedelta(days=2):
        try:
            stamp = stamp.replace(year=local.year + 1)
        except ValueError:
            return stamp.isoformat()
    return stamp.isoformat()


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", str(text or ""))


def looks_like_login(url: str = "", title: str = "", text: str = "") -> bool:
    loc = f"{url} {title}".lower()
    if any(token in loc for token in ("/login", "/signin", "/passport", "signin?")):
        return True
    body = str(text or "")
    has_secret = "password" in body.lower() or "密码" in body or "验证码" in body
    has_login = "登录" in body or "sign in" in body.lower() or "log in" in body.lower()
    return has_secret and has_login


def parse_usage_text(text: str) -> dict[str, Any] | None:
    blob = _strip_ansi(text)
    if "/usage is not available" in blob.lower():
        return None
    if looks_like_login(text=blob):
        return None
    used = None
    remaining_pct = None
    remaining = None
    capacity = None
    unit = "percent"
    bar = _BAR_PCT.search(blob)
    if bar:
        used = float(bar.group(1))
        remaining_pct = max(0.0, 100.0 - used)
    match = None if remaining_pct is not None else _PCT.search(blob)
    if match:
        remaining_pct = float(next(g for g in match.groups() if g is not None))
        if match.group(3) is not None:
            remaining_pct = max(0.0, 100.0 - float(match.group(3)))
            used = float(match.group(3))
    if remaining_pct is None:
        used_match = _USED_PCT.search(blob)
        if used_match:
            used = float(used_match.group(1))
            remaining_pct = max(0.0, 100.0 - used)
    if remaining_pct is None:
        remain_abs = _CN_REMAIN.search(blob)
        total_abs = _CN_TOTAL.search(blob)
        if remain_abs:
            remaining = float(remain_abs.group(1).replace(",", ""))
            capacity = float(total_abs.group(1).replace(",", "")) if total_abs else None
            unit = "credits"
            if remaining_pct is None and capacity:
                remaining_pct = round(100.0 * remaining / capacity, 1)
    frac = _FRACTION.search(blob)
    if frac:
        remaining = float(frac.group(1))
        capacity = float(frac.group(2))
        unit = (frac.group(3) or "credits").lower()
        if remaining_pct is None and capacity:
            remaining_pct = round(100.0 * remaining / capacity, 1)
    elif remaining is None and remaining_pct is not None:
        remaining = remaining_pct
        capacity = 100.0
    if remaining is None:
        return None
    reset = None
    reset_match = _RESET.search(blob)
    if reset_match:
        reset = reset_match.group(1)
        if "T" not in reset and " " not in reset:
            reset = reset + "T00:00:00Z"
        elif reset[-1] not in "Zz" and "+" not in reset:
            reset = reset.replace(" ", "T") + "Z"
    else:
        human = _RESET_HUMAN.search(blob)
        if human:
            reset = _human_reset(human.group(1), human.group(2), human.group(3), human.group(4))
    return {
        "remaining": remaining,
        "capacity": capacity,
        "unit": unit,
        "remaining_pct": remaining_pct,
        "used_pct": used if used is not None else (None if remaining_pct is None else round(100.0 - remaining_pct, 1)),
        "reset_at": reset,
        "evidence": blob[-1500:],
    }


def _ws_url(worker_id: str) -> str:
    return _daemon_base_url().replace("http://", "ws://", 1) + f"/cc/sessions/{worker_id}/ws"


def _recv_session_text(worker_id: str, *, seconds: float) -> str:
    from websockets.sync.client import connect

    chunks: list[str] = []
    deadline = time.time() + max(1.0, seconds)
    with connect(_ws_url(worker_id), open_timeout=6, close_timeout=2) as ws:
        while time.time() < deadline:
            remaining = max(0.05, deadline - time.time())
            try:
                raw = ws.recv(timeout=min(1.2, remaining))
            except TimeoutError:
                continue
            except Exception:  # noqa: BLE001
                break
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = message.get("type")
            if kind == "snapshot_chunk":
                chunks.extend(str(item) for item in (message.get("chunks") or []))
            elif kind == "output":
                chunks.append(str(message.get("data") or ""))
            elif kind == "exit":
                break
    return "".join(chunks)


def _launch_probe_worker(runtime: str):
    from .session_placement import remember_background

    request = AgentLaunchRequest(
        provider=runtime,
        visibility="background",
        topic=f"subscription-/usage-{runtime}",
        from_controller=True,
    )
    try:
        handle = launch_agent_worker(request)
    except RuntimeError as exc:
        if "visibility" not in str(exc).lower() and "HTTP 400" not in str(exc):
            raise
        handle = launch_agent_worker(
            AgentLaunchRequest(
                provider=runtime,
                visibility="hidden",
                topic=f"subscription-/usage-{runtime}",
                from_controller=True,
            )
        )
    remember_background(handle.worker_id)
    return handle


def probe_slash_usage(provider: str, *, timeout: float = 24.0) -> dict[str, Any]:
    """Start a hidden CLI session, send /usage, parse, kill."""
    runtime = "kimi" if provider == "kimi" else ("kiro" if provider == "kiro" else "grok")
    handle = None
    text = ""
    try:
        handle = _launch_probe_worker(runtime)
        warmup = min(6.0, max(3.0, timeout / 4.0))
        text = _recv_session_text(handle.worker_id, seconds=warmup)
        send_agent_input(handle.worker_id, "/usage", submit=True)
        deadline = time.time() + max(8.0, timeout - warmup)
        parsed = parse_usage_text(text)
        while time.time() < deadline and parsed is None:
            more = _recv_session_text(handle.worker_id, seconds=min(3.0, deadline - time.time()))
            if more:
                text = more if len(more) >= len(text) else text + more
            parsed = parse_usage_text(text)
        if not parsed:
            return {
                "ok": False,
                "reason": "隐藏会话已拉起并发送了 /usage，但输出里没有可解析的剩余额度",
                "evidence": _strip_ansi(text)[-800:],
            }
        parsed["via"] = "hidden_worker"
        return {"ok": True, **parsed}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "reason": f"拉起 {runtime} 会话失败: {exc}",
            "evidence": _strip_ansi(text)[-800:],
        }
    finally:
        if handle is not None:
            try:
                _request_json(f"/cc/sessions/{handle.worker_id}", method="DELETE")
            except Exception:  # noqa: BLE001
                pass
