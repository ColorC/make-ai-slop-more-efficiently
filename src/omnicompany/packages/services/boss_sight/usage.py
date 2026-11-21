# [OMNI] origin=ai-ide ts=2026-06-07 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.usage_meter.py"
"""usage — Claude **官方剩余配额**, 直接读 Anthropic OAuth usage 端点。

用户 2026-06-07: "不要本地估! 我要官方的百分比, 直接读官方的" + "有不少开源插件...完全照抄就行"。
照抄社区事实标准 github.com/ohugonnot/claude-code-statusline 的做法:
用 `claude login` 存在 ~/.claude/.credentials.json 的 OAuth token 直接打官方端点
  GET https://api.anthropic.com/api/oauth/usage
      Authorization: Bearer <claudeAiOauth.accessToken>
      anthropic-beta: oauth-2025-04-20
返回各窗口 `{utilization(已用%), resets_at(ISO)}`: five_hour / seven_day / seven_day_opus /
seven_day_sonnet。**剩余% = 100 - utilization**。这是 Anthropic 官方数字(非本地日志估算)。

Codex(2026-09-17 起主路径): 用该账号 auth.json 的 ChatGPT OAuth token 直接打官方端点
  GET https://chatgpt.com/backend-api/wham/usage
      Authorization: Bearer <tokens.access_token>;  ChatGPT-Account-Id: <tokens.account_id>
返回 rate_limit.primary_window / secondary_window 各带 used_percent + limit_window_seconds + reset_at。
改走端点的原因: 同一 CODEX_HOME 也跑 the_company 等第三方 model_provider, 这些会话写的 rate_limits
全是 null, 且 token 刷新会改写 auth.json mtime, 日志读数因此长期"尚无成功读数"。
下面的日志读法仅在端点失败时兜底。

Codex(兜底): codex CLI 每轮把官方限额写进会话日志 ~/.codex/sessions/**/*.jsonl 的 `rate_limits`
(primary/secondary 窗口, 各带 used_percent + resets_at epoch + window_minutes) —— 这就是
`/status` 显示的同一份官方数据, 直接读最近日志即可, 不必驱动 TUI 模拟输入。剩余% = 100 - used_percent。
注意: 限额耗尽后最新记录会退化成无窗口(primary=null), 读取时按事件时间戳挑最后一条仍带
窗口的记录, 而不是无脑取最后一行(否则永远回退到旧读数)。窗口按 window_minutes 映射 5h/7天。
"""

from __future__ import annotations

import json
import hashlib
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_TTL = 300.0  # 官方 oauth/usage 限流敏感, 5 分钟缓存(配额%变化慢, 没必要勤拉)
_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_ACCOUNT_CACHE: dict[str, dict[str, Any]] = {}
_ACCOUNT_LOCKS: dict[str, threading.Lock] = {}
_STATE_LOCK = threading.RLock()
# 上次成功读到的值(按 provider), 临时失败(429/网络)时回退展示而非报错盖掉。
_LAST_GOOD: dict[str, dict[str, Any]] = {}
# 被 429 限流后的冷却: 这段时间内不再打官方端点, 直接回退 last-good。
# 按账号(claude / claude_2)分别冷却: 一个账号被限流不该挡住另一个账号的读数。
_CLAUDE_COOLDOWN_UNTIL: dict[str, float] = {}
_CLAUDE_429_COOLDOWN = 600.0  # 命中 429 后冷却 10 分钟(不再打端点, 给它恢复)
_CODEX_RATE_LIMIT_LOG_CAP = 80

_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


def _claude_credentials(provider: str = "claude_code") -> Path:
    """该 Claude 账号的凭据文件。第二个账号整份凭据在自己的 CLAUDE_CONFIG_DIR 下,
    读主号的 ~/.claude/.credentials.json 会把主号额度当成它的额度。"""
    from omnicompany.core.provider_identity import claude_home_for

    home = claude_home_for(provider)
    # A reader imported by an official-2 worker must not relabel its ambient
    # CLAUDE_CONFIG_DIR as the primary account (same boundary as Codex below).
    if provider in {"claude", "claude_code"} and home.resolve() == claude_home_for("claude_2").resolve():
        home = Path.home() / ".claude"
    return home / ".credentials.json"


def account_identity(provider: str, *, token_snapshot: str | None = None) -> dict[str, str]:
    """A non-secret cache namespace pinned to this account's home and login.

    Changing login must invalidate even persisted last-good data. Legacy rows
    without this identity cannot safely be attributed to today's account.
    """
    if provider in {"claude", "claude_code", "claude_2"}:
        path = _claude_credentials(provider)
        token = token_snapshot if token_snapshot is not None else (_oauth_token(provider) or "missing")
        marker = token
    else:
        path = _codex_account_home(provider) / "auth.json"
        # 以 ChatGPT account_id 定账号: access token 每次刷新都会改写 auth.json,
        # 按整文件内容算身份会让每次刷新都被当成"换号", 清空读数。
        marker = _codex_auth(provider).get("account_id") or "missing"
    digest = hashlib.sha256((str(path.resolve()).casefold() + "\0" + marker).encode()).hexdigest()
    return {"account_key": f"{provider}:{digest}", "account_home": path.parent.name,
            "account_fingerprint": digest[:10]}

# last-good 落盘, 让 ccdaemon 重启后遇 429 仍能显示上次读数(而非空着报错)。
try:
    from omnicompany.core.config import omni_workspace_root as _ws_root
    _LAST_GOOD_FILE: Path | None = _ws_root() / "data" / "boss_sight" / "usage_last_good.json"
except Exception:  # noqa: BLE001
    _LAST_GOOD_FILE = None


def _load_last_good() -> None:
    if not _LAST_GOOD_FILE:
        return
    try:
        d = json.loads(_LAST_GOOD_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            _LAST_GOOD.update({k: v for k, v in d.items() if isinstance(v, dict)})
    except (OSError, json.JSONDecodeError):
        pass


def _save_last_good() -> None:
    if not _LAST_GOOD_FILE:
        return
    try:
        _LAST_GOOD_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _STATE_LOCK:
            _LAST_GOOD_FILE.write_text(json.dumps(_LAST_GOOD, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


_load_last_good()  # 进程启动即载入上次读数, 重启后遇 429 也能显示


def _oauth_token(provider: str = "claude_code") -> str | None:
    """读 claude login 存的 OAuth access token(同 claude-code-statusline)。"""
    try:
        d = json.loads(_claude_credentials(provider).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (d.get("claudeAiOauth") or {}).get("accessToken") or None


def _fetch_official(provider: str = "claude_code", *, token: str | None = None) -> dict[str, Any]:
    token = _oauth_token(provider) if token is None else token
    if not token:
        raise RuntimeError(
            f"未找到 claude OAuth token({_claude_credentials(provider)}); 先 `claude login`"
        )
    req = urllib.request.Request(  # noqa: S310 - 官方 anthropic 端点, 固定 URL
        _USAGE_URL,
        headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"},
    )
    with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310
        return json.loads(r.read().decode())


def _reset_in_sec(resets_at: Any) -> int | None:
    """resets_at → 距现在还有多少秒。兼容 Claude 的 ISO 8601 与 codex 的 epoch 秒。
    过去/解析失败 → None。"""
    if resets_at is None or resets_at == "":
        return None
    try:
        if isinstance(resets_at, (int, float)):
            delta = float(resets_at) - time.time()
        else:
            dt = datetime.fromisoformat(str(resets_at))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = dt.timestamp() - time.time()
    except (ValueError, TypeError):
        return None
    return int(delta) if delta > 0 else 0


def _window(d: dict[str, Any], key: str) -> dict[str, Any] | None:
    """把官方某窗口转成 {已用%, 剩余%, resets_at, reset_in_sec}。"""
    w = d.get(key)
    if not isinstance(w, dict) or w.get("utilization") is None:
        return None
    util = float(w.get("utilization") or 0.0)
    return {
        "used_pct": round(util, 1),
        "remaining_pct": round(max(0.0, 100.0 - util), 1),
        "resets_at": w.get("resets_at"),
        "reset_in_sec": _reset_in_sec(w.get("resets_at")),
    }


def _claude(provider: str = "claude_code") -> dict[str, Any]:
    token = _oauth_token(provider) or ""
    identity = account_identity(provider, token_snapshot=token or "missing")
    cache_key = identity["account_key"]
    now = time.time()
    last = _LAST_GOOD.get(cache_key)
    # 429 冷却期内不再打端点(给它恢复时间): 有上次读数就回退展示, 没有就给干净的"冷却中"提示。
    if now < _CLAUDE_COOLDOWN_UNTIL.get(cache_key, 0.0):
        if last:
            return {**last, "stale": True, "stale_reason": "官方端点限流(429)冷却中, 显示上次读数"}
        return {"available": False, "reason": "官方 usage 端点暂被限流(429), 冷却中, 稍后自动恢复"}
    try:
        d = _fetch_official(provider, token=token)
    except Exception as e:  # noqa: BLE001
        is_429 = isinstance(e, urllib.error.HTTPError) and getattr(e, "code", None) == 429
        if is_429:
            _CLAUDE_COOLDOWN_UNTIL[cache_key] = now + _CLAUDE_429_COOLDOWN
        if last:  # 临时失败优先回退上次成功值, 别用错误盖掉好数据
            why = "官方端点限流(429)" if is_429 else type(e).__name__
            return {**last, "stale": True, "stale_reason": f"{why}, 显示上次读数"}
        if is_429:
            return {"available": False, "reason": "官方 usage 端点暂被限流(429), 稍后自动恢复"}
        return {"available": False, "reason": f"读官方 usage 失败: {type(e).__name__}: {str(e)[:80]}"}
    out: dict[str, Any] = {"available": True}
    for key in ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet"):
        w = _window(d, key)
        if w is not None:
            out[key] = w
    if len(out) == 1:  # 只有 available, 一个窗口都没解析出
        if last:
            return {**last, "stale": True, "stale_reason": "官方端点返回无可识别窗口, 显示上次读数"}
        return {"available": False, "reason": "官方端点返回里没有可识别的配额窗口"}
    out.update(identity, observed_at=datetime.now(timezone.utc).isoformat())
    if account_identity(provider)["account_key"] != cache_key:
        return {"available": False, "reason": "账号登录已变化，等待重新读取"}
    with _STATE_LOCK:
        _LAST_GOOD[cache_key] = out
        _CLAUDE_COOLDOWN_UNTIL[cache_key] = 0.0
        _save_last_good()
    return out


def _codex_account_home(provider: str = "codex") -> Path:
    """Resolve a logical Codex account independently of the current process.

    A Codex official-2 worker necessarily runs with ``CODEX_HOME=.codex-2``.
    The dashboard usage reader may be imported from that worker, so blindly
    accepting the ambient variable for the primary account would make both
    cards read official-2. When the ambient primary path is exactly the known
    second-account path, fall back to Codex's real primary default.
    """
    from omnicompany.dashboard.ccdaemon.provider_identity import codex_home_for

    if provider == "codex_2":
        return codex_home_for("codex_2")
    primary = codex_home_for("codex")
    second = codex_home_for("codex_2")
    try:
        if primary.resolve() == second.resolve():
            return Path.home() / ".codex"
    except OSError:
        pass
    return primary


_CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


def _codex_auth(provider: str = "codex") -> dict[str, Any]:
    """该账号 auth.json 里的 ChatGPT 登录 token(access_token / account_id); 读不到返回 {}。"""
    try:
        d = json.loads((_codex_account_home(provider) / "auth.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    tokens = d.get("tokens") if isinstance(d, dict) else None
    return tokens if isinstance(tokens, dict) else {}


def _fetch_codex_official(provider: str = "codex") -> dict[str, Any]:
    tokens = _codex_auth(provider)
    if not tokens.get("access_token"):
        raise RuntimeError("auth.json 无 ChatGPT access_token(非 ChatGPT 登录?)")
    headers = {"Authorization": f"Bearer {tokens['access_token']}", "User-Agent": "codex_cli_rs",
               "originator": "codex_cli_rs"}
    if tokens.get("account_id"):
        headers["ChatGPT-Account-Id"] = str(tokens["account_id"])
    req = urllib.request.Request(_CODEX_USAGE_URL, headers=headers)  # noqa: S310 - 固定官方端点
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
        return json.loads(r.read().decode())


def _codex_official_windows(d: dict[str, Any]) -> dict[str, Any]:
    """wham/usage → {five_hour, seven_day}; 按 limit_window_seconds 分槽, 不按位置。"""
    rl = d.get("rate_limit") if isinstance(d.get("rate_limit"), dict) else {}
    out: dict[str, Any] = {}
    for key in ("primary_window", "secondary_window"):
        w = rl.get(key)
        if not isinstance(w, dict) or w.get("used_percent") is None:
            continue
        secs = w.get("limit_window_seconds")
        mins = int(secs) // 60 if isinstance(secs, (int, float)) else None
        slot = "seven_day" if (mins or 0) >= 4320 or (mins is None and key == "secondary_window") else "five_hour"
        if slot in out:
            continue
        used = float(w.get("used_percent") or 0.0)
        resets_at = w.get("reset_at")
        if resets_at is None and isinstance(w.get("reset_after_seconds"), (int, float)):
            resets_at = time.time() + float(w["reset_after_seconds"])
        out[slot] = {
            "used_pct": round(used, 1),
            "remaining_pct": round(max(0.0, 100.0 - used), 1),
            "resets_at": resets_at,
            "reset_in_sec": _reset_in_sec(resets_at),
            "window_minutes": mins,
        }
    return out


def _codex_sessions_dir(provider: str = "codex") -> Path:
    """Sessions root of one Codex account.

    Each account is a separate CODEX_HOME, so each writes its own rollout logs
    and therefore its own ``rate_limits`` records. Reading the primary account's
    tree for ``codex_2`` would report the wrong subscription's remaining quota —
    which is the entire point of counting them apart.
    """
    return _codex_account_home(provider) / "sessions"


def _codex_window(w: Any) -> dict[str, Any] | None:
    """codex rate_limits 的 primary/secondary → 统一窗口结构(used_percent + epoch resets_at)。"""
    if not isinstance(w, dict) or w.get("used_percent") is None:
        return None
    used = float(w.get("used_percent") or 0.0)
    return {
        "used_pct": round(used, 1),
        "remaining_pct": round(max(0.0, 100.0 - used), 1),
        "resets_at": w.get("resets_at"),
        "reset_in_sec": _reset_in_sec(w.get("resets_at")),
        "window_minutes": w.get("window_minutes"),
    }


def _codex_last_rate_limits(provider: str = "codex") -> tuple[dict[str, Any] | None, float]:
    """读最近 codex 会话日志里的 rate_limits(codex 每轮把官方限额写进 rollout jsonl)。

    返回 (rate_limits, 事件时间戳 epoch 秒)。这就是 codex `/status` 显示的同一份官方数据
    —— 不用驱动 TUI 模拟输入。大文件只读尾部。

    坑: 限额耗尽后 codex 写的最后一条 rate_limits 会退化成
    `limit_id=premium, primary=null, secondary=null`(没有任何窗口数据), 直接取最后一条
    会解析不出窗口 → 永远回退到旧的 last-good, 面板一直显示过期读数。所以按**事件时间戳**
    在最近 5 个文件里挑最新一条仍带可用窗口(used_percent)的记录; 一条都没有才退回最新的
    那条(拿 plan_type/credits 信息)。
    """
    try:
        logs = sorted(
            _codex_sessions_dir(provider).glob("**/*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None, 0.0
    best: dict[str, Any] | None = None  # 最新一条带可用窗口的
    best_ts = 0.0
    newest: dict[str, Any] | None = None  # 最新一条(无窗口时兜底)
    newest_ts = 0.0
    # A quota-exhausted account can produce many terminal rollouts whose
    # primary/secondary windows are null. Scan bounded recent history so the
    # latest valid official window is not hidden by those terminal records.
    for lg in logs[:_CODEX_RATE_LIMIT_LOG_CAP]:
        try:
            size = lg.stat().st_size
            with lg.open("rb") as f:
                if size > 1_000_000:  # 大日志只读尾部 1MB
                    f.seek(size - 1_000_000)
                    f.readline()  # 丢弃可能被截断的半行
                text = f.read().decode("utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            if '"rate_limits"' not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_raw = obj.get("timestamp")
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp() if ts_raw else 0.0
            except (ValueError, TypeError):
                ts = 0.0
            rl = _deep_find_key(obj, "rate_limits")
            if not isinstance(rl, dict):
                continue
            if ts > newest_ts:
                newest_ts, newest = ts, rl
            if ts < best_ts:
                continue
            for key in ("primary", "secondary"):
                w = rl.get(key)
                if isinstance(w, dict) and w.get("used_percent") is not None:
                    best_ts, best = ts, rl
                    break
    if best is not None:
        return best, best_ts
    return newest, newest_ts


def _deep_find_key(d: Any, key: str) -> Any:
    if isinstance(d, dict):
        if key in d and d[key]:
            return d[key]
        for v in d.values():
            r = _deep_find_key(v, key)
            if r is not None:
                return r
    elif isinstance(d, list):
        for v in d:
            r = _deep_find_key(v, key)
            if r is not None:
                return r
    return None


_STALE_AFTER_SEC = 12 * 3600  # 最近一条官方读数超过 12h 就标记 stale, 别把旧数当现况


def _codex(provider: str = "codex") -> dict[str, Any]:
    """codex 官方剩余: 读该账号 sessions 最近日志里的 rate_limits(同 /status 数据源)。

    ``provider`` 选账号: ``codex`` 是主号, ``codex_2`` 是第二个官方账号(独立
    CODEX_HOME、独立额度)。两者的 last-good 缓存分开存, 否则一个账号的读数会在
    另一个暂时读不到时被当成它的"上次读数"顶上来。
    """
    identity = account_identity(provider)
    try:
        official = _fetch_codex_official(provider)
    except Exception:  # noqa: BLE001 - 端点失败走日志兜底
        official = None
    if isinstance(official, dict):
        windows = _codex_official_windows(official)
        if windows:
            out = {"available": True, "plan_type": official.get("plan_type"), **identity, **windows,
                   "limit_reached": bool((official.get("rate_limit") or {}).get("limit_reached")),
                   "observed_at": datetime.now(timezone.utc).isoformat(), "source": "official_api"}
            if account_identity(provider)["account_key"] != identity["account_key"]:
                return {"available": False, "reason": "账号登录已变化，等待重新读取"}
            with _STATE_LOCK:
                _LAST_GOOD[identity["account_key"]] = out
                _save_last_good()
            return out
    last = _LAST_GOOD.get(identity["account_key"])
    # Legacy or cross-account last-good values are not safe to reuse. A stale
    # unknown is preferable to confidently showing another account's quota.
    if last and last.get("account_key") != identity["account_key"]:
        last = None
    rl, observed_ts = _codex_last_rate_limits(provider)
    # Rollouts in one home can outlive a logout/login. Without an account id on
    # the rate-limit event, only post-authentication observations are attributable.
    try:
        auth_changed_at = (_codex_account_home(provider) / "auth.json").stat().st_mtime
    except OSError:
        return {"available": False, "reason": "该账号尚未登录，无法确认限额归属"}
    if rl and observed_ts < auth_changed_at:
        rl = None
        if not last:
            return {"available": False, "reason": "登录凭据更新后尚无新限额读数，等待该账号下一轮记录"}
    if not rl:
        if last:
            return {**last, "stale": True, "stale_reason": "暂未读到 codex 限额日志, 显示上次读数"}
        sessions_dir = _codex_sessions_dir(provider)
        return {
            "available": False,
            "reason": f"未找到 codex 限额日志({sessions_dir}; 先用该账号跑一轮 codex)",
        }
    out: dict[str, Any] = {
        "available": True,
        "plan_type": rl.get("plan_type"),
        **identity,
    }
    # 窗口按 window_minutes 映射(不按 primary/secondary 位置): codex 现在 primary 也可能是
    # 周窗口(window_minutes=10080), 按位置硬套会把周限额标成 "5小时"。缺 window_minutes 才
    # 退回位置映射(primary→5h, secondary→7天)。
    five: dict[str, Any] | None = None
    week: dict[str, Any] | None = None
    for key, default_slot in (("primary", "five"), ("secondary", "week")):
        w = _codex_window(rl.get(key))
        if not w:
            continue
        mins = w.get("window_minutes")
        slot = default_slot if not isinstance(mins, (int, float)) else ("week" if mins >= 4320 else "five")
        if slot == "week" and week is None:
            week = w
        elif slot == "five" and five is None:
            five = w
    if five:
        out["five_hour"] = five
    if week:
        out["seven_day"] = week
    if observed_ts > 0:
        out["observed_at"] = datetime.fromtimestamp(observed_ts, tz=timezone.utc).isoformat()
        if time.time() - observed_ts > _STALE_AFTER_SEC:
            out["stale"] = True
            out["stale_reason"] = "日志里最新官方读数已超过 12 小时(此后 codex 没跑过或没写新限额)"
    if "five_hour" not in out and "seven_day" not in out:
        if last:
            return {**last, "stale": True, "stale_reason": "codex 日志无可识别窗口, 显示上次读数"}
        return {"available": False, "reason": "codex 日志里无可识别的限额窗口"}
    with _STATE_LOCK:
        _LAST_GOOD[identity["account_key"]] = out
        _save_last_good()
    return out


def codex_second_account_configured() -> bool:
    """Whether a second official Codex account has actually been logged in.

    Gate the extra quota row on this: an unconfigured second account would
    otherwise render as a permanently "unavailable" panel entry for everyone who
    never set one up. Presence of ``auth.json`` is the honest signal — it is the
    file ``codex login`` writes and the only thing that makes the account usable.
    """
    from omnicompany.dashboard.ccdaemon.provider_identity import codex_home_for

    try:
        return (codex_home_for("codex_2") / "auth.json").is_file()
    except OSError:
        return False


def claude_second_account_configured() -> bool:
    """Whether a second official Anthropic account has actually been logged in.

    Same gate as ``codex_second_account_configured``: presence of the
    credential file ``claude /login`` writes into that account's
    CLAUDE_CONFIG_DIR is the honest signal, and without it the extra quota row
    would render as a permanently "unavailable" panel entry.
    """
    try:
        return _claude_credentials("claude_2").is_file()
    except OSError:
        return False


def provider_usage(provider: str, *, force: bool = False) -> dict[str, Any]:
    """One account, one in-flight request; independent accounts never wait here."""
    if provider not in {"claude_code", "claude_2", "codex", "codex_2"}:
        raise ValueError("unknown official subscription account")
    identity = account_identity(provider)
    key = identity["account_key"]
    started = time.time()
    with _STATE_LOCK:
        lock = _ACCOUNT_LOCKS.setdefault(key, threading.Lock())
    with lock:
        hit = _ACCOUNT_CACHE.get(key)
        if hit and (hit["ts"] >= started or (not force and started - hit["ts"] < _TTL)):
            return dict(hit["data"])
        value = _claude(provider) if provider.startswith("claude") else _codex(provider)
        value = {**value, **identity}
        # If credentials changed during I/O, discard this generation.
        if account_identity(provider)["account_key"] != key:
            return {"available": False, "reason": "账号登录已变化，等待重新读取"}
        _ACCOUNT_CACHE[key] = {"ts": time.time(), "data": value}
        return dict(value)


def build_usage(force: bool = False) -> dict[str, Any]:
    """Compatibility aggregate; the subscriptions page reads accounts independently."""
    from concurrent.futures import ThreadPoolExecutor
    from omnicompany.core.agent_environment import agent_environment

    accounts = {"claude": "claude_code", "claude_2": "claude_2", "codex": "codex"}
    if agent_environment("codex_2").is_active and codex_second_account_configured():
        accounts["codex_2"] = "codex_2"
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {key: pool.submit(provider_usage, provider, force=force) for key, provider in accounts.items()}
        values = {key: job.result() for key, job in jobs.items()}
    data = {
        "generated_at": time.time(),
        "source": "Anthropic 官方 oauth/usage 端点",
        **values,
        "note": "官方剩余配额(100-已用%): Claude 读 oauth/usage 端点, Codex 读各账号 CODEX_HOME 下会话日志的 rate_limits(同 /status)。",
    }
    # 第二个官方 Codex 账号额度独立统计; 没登录过就整条不出现, 不摆一个永远
    # unavailable 的空行。
    return data


__all__ = [
    "build_usage",
    "claude_second_account_configured",
    "codex_second_account_configured",
]
