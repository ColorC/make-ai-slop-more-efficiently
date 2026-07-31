# [OMNI] origin=ai-ide ts=2026-05-24 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.routes.py"
"""routes — BOSS SIGHT FastAPI 路由.

块 1 重构后总控不是常驻 service, 而是按需 AgentNodeLoop. 本模块含健康/上下文/prompt/
材料注册表/驾驶舱/洞察/常驻agent/关注等约40个端点, 明细见代码 (下方 include_router /
@boss_sight_router 装饰器逐条列出, 本 docstring 不再逐一枚举以免与实现脱节).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from omnicompany.core.config import omni_workspace_root

from .aggregator.plan_index_scanner import PlanIndexScanner
from .aggregator.subagent_status_aggregator import SubagentStatusAggregator
from .cockpit import build_attention_state, build_cockpit_snapshot
from .cockpit_actions import (
    CockpitActionError,
    execute_cockpit_action,
    list_cockpit_action_events,
    resolve_action_target,
)
from .cockpit_workflow import build_workflow_summary
from .entity_registry import parse_entity_uri, resolve_entity_uri, search_entities
from .llm_runtime_usage import build_llm_runtime_usage
from .material_registry import build_material_registry
from .token_ledger_view import build_token_ledger_view
from .services.control_observability_store import get_control_observability_store

boss_sight_router = APIRouter(prefix="/api/boss-sight", tags=["boss-sight"])

# ── InsightsTab 聚合常量 ──────────────────────────────────────────────
# 价格 USD per 1M tokens, [input, output]
_PRICING = {
    "opus": (5.0, 25.0),
    "sonnet": (3.0, 15.0),
    "haiku": (1.0, 5.0),
    "gpt-5.4": (2.5, 15.0),
    "gpt-5.3-codex": (1.75, 14.0),
}
_DEFAULT_PRICING = _PRICING["opus"]
_SCAN_FILE_CAP = 50  # 每次扫描 jsonl 上限, 控延迟
_MAX_FILE_BYTES = 4 * 1024 * 1024  # 单文件最多读 4MB
_MAX_AGE_DAYS = 35  # 老于此跳过 (省 mtime 排序之外的 IO)
# 简易内存缓存: { key: (expire_ts, payload) }
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 60.0  # 秒


def _cached(key: str, ttl: float = _CACHE_TTL):
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def _set_cache(key: str, payload: dict, ttl: float = _CACHE_TTL) -> dict:
    _CACHE[key] = (time.time() + ttl, payload)
    return payload


# 单独缓存 session 活动 (active-time 与 projects 共用, 避免双倍扫描)
_SESSION_ACTIVITY_CACHE: dict[str, Any] = {}


class ControlUpdateBody(BaseModel):
    value: bool
    actor: str = Field(default="human")
    reason: str = Field(default="", max_length=500)


class ObservabilitySettingsBody(BaseModel):
    dimensions: dict[str, bool] = Field(default_factory=dict)
    actor: str = Field(default="human")
    reason: str = Field(default="", max_length=500)


class ObservationEventBody(BaseModel):
    dimension: str
    surface: str = Field(default="", max_length=160)
    target: str | None = Field(default=None, max_length=300)
    value: Any = None
    meta: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(default="human")


class PermanentAllowBody(BaseModel):
    scope: str = Field(default="user", max_length=160)
    tool: str = Field(..., min_length=1, max_length=160)
    pattern: str = Field(default="", max_length=500)
    reason: str = Field(default="", max_length=500)
    actor: str = Field(default="human")


class CockpitResolveBody(BaseModel):
    target: dict[str, Any] = Field(default_factory=dict)


class CockpitActionBody(BaseModel):
    kind: str = Field(..., min_length=1, max_length=80)
    target: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(default="human", max_length=80)
    note: str = Field(default="", max_length=500)
    payload: dict[str, Any] = Field(default_factory=dict)


def _get_session_activity() -> tuple[list[dict], dict, bool]:
    now = time.time()
    if _SESSION_ACTIVITY_CACHE.get("expire", 0) > now:
        return (
            _SESSION_ACTIVITY_CACHE["sessions"],
            _SESSION_ACTIVITY_CACHE["totals"],
            _SESSION_ACTIVITY_CACHE["partial"],
        )
    sessions, totals, partial = _scan_session_activity()
    _SESSION_ACTIVITY_CACHE.update(
        {"sessions": sessions, "totals": totals, "partial": partial, "expire": now + _CACHE_TTL}
    )
    return sessions, totals, partial


def _classify_model(model_str: str | None) -> str:
    """把任意 model 字符串归到 _PRICING key, 兜底 opus."""
    if not model_str:
        return "opus"
    m = model_str.lower()
    if "haiku" in m:
        return "haiku"
    if "sonnet" in m:
        return "sonnet"
    if "opus" in m:
        return "opus"
    if "codex" in m:
        return "gpt-5.3-codex"
    if "gpt-5" in m or m.startswith("gpt5") or "gpt-5.4" in m:
        return "gpt-5.4"
    return "opus"


def _bucket_for(ts: datetime, now: datetime) -> tuple[bool, bool, bool]:
    """(today, week, month) 三个布尔."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())
    month_start = today_start.replace(day=1)
    return (ts >= today_start, ts >= week_start, ts >= month_start)


def _empty_bucket() -> dict[str, Any]:
    """Per-provider bucket with per-window token & USD + per-model breakdown.

    Old fields kept for backward compat:
      - today_tokens / week_tokens / month_tokens
      - estimated_usd = month_usd (累计本月)
    New fields:
      - today_usd / week_usd / month_usd: 按时间窗口分别累计 USD
      - by_model: { model_str → { tokens, usd } } 真正的模型粒度细分
    """
    return {
        "today_tokens": 0,
        "week_tokens": 0,
        "month_tokens": 0,
        "today_usd": 0.0,
        "week_usd": 0.0,
        "month_usd": 0.0,
        "estimated_usd": 0.0,  # alias of month_usd, 保留前端旧字段不破坏
        "by_model": {},
    }


def _accum_tokens(
    bucket: dict[str, Any],
    input_toks: int,
    output_toks: int,
    pricing_key: str,
    when_today: bool,
    when_week: bool,
    when_month: bool,
    *,
    raw_model: str | None = None,
) -> None:
    total = input_toks + output_toks
    in_p, out_p = _PRICING.get(pricing_key, _DEFAULT_PRICING)
    cost = (input_toks * in_p + output_toks * out_p) / 1_000_000

    if when_month:
        bucket["month_tokens"] += total
        bucket["month_usd"] += cost
        bucket["estimated_usd"] = bucket["month_usd"]  # alias
    if when_week:
        bucket["week_tokens"] += total
        bucket["week_usd"] += cost
    if when_today:
        bucket["today_tokens"] += total
        bucket["today_usd"] += cost

    # Per-model breakdown (累计全期, 不分窗口 — 模型分布看长期占比意图)
    key = raw_model or pricing_key or "unknown"
    by_model = bucket["by_model"]
    if key not in by_model:
        by_model[key] = {"tokens": 0, "usd": 0.0, "pricing_key": pricing_key}
    by_model[key]["tokens"] += total
    by_model[key]["usd"] += cost


def _recent_jsonls(root: Path, cap: int) -> tuple[list[Path], bool]:
    """递归取最近修改的 cap 个 .jsonl, 跳过太老的; 返回 (files, partial)."""
    if not root.is_dir():
        return [], True
    cutoff = time.time() - _MAX_AGE_DAYS * 86400
    files: list[tuple[float, Path]] = []
    skipped = 0
    try:
        for p in root.rglob("*.jsonl"):
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            if mt < cutoff:
                skipped += 1
                continue
            files.append((mt, p))
    except OSError:
        pass
    files.sort(key=lambda x: x[0], reverse=True)
    partial = len(files) > cap or skipped > 0
    return [p for _, p in files[:cap]], partial


def _scan_claude_jsonls() -> tuple[dict[str, dict], bool]:
    """扫 ~/.claude/projects/*.jsonl. 返回 (by_provider, partial).

    分类: 文件路径包含 controller/boss_sight → controller; 含 omni_agent → omni_agent;
    其它 → claude.
    """
    out = {
        "claude": _empty_bucket(),
        "controller": _empty_bucket(),
        "omni_agent": _empty_bucket(),
    }
    root = Path.home() / ".claude" / "projects"
    now = datetime.now(timezone.utc)
    files, partial = _recent_jsonls(root, _SCAN_FILE_CAP)
    if not files and not root.is_dir():
        return out, True
    for f in files:
        path_lower = str(f).lower()
        if "controller" in path_lower or "boss_sight" in path_lower or "boss-sight" in path_lower:
            provider = "controller"
        elif "omni_agent" in path_lower or "omni-agent" in path_lower:
            provider = "omni_agent"
        else:
            provider = "claude"
        try:
            size = f.stat().st_size
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                if size > _MAX_FILE_BYTES:
                    fh.seek(size - _MAX_FILE_BYTES)
                    fh.readline()  # 丢半行
                    partial = True
                for line in fh:
                    if "input_tokens" not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    msg = obj.get("message") or {}
                    usage = msg.get("usage") or {}
                    if not usage:
                        continue
                    ts_raw = obj.get("timestamp")
                    try:
                        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")) if ts_raw else now
                    except Exception:  # noqa: BLE001
                        ts = now
                    in_t = int(usage.get("input_tokens", 0) or 0) + int(usage.get("cache_creation_input_tokens", 0) or 0) + int(usage.get("cache_read_input_tokens", 0) or 0)
                    out_t = int(usage.get("output_tokens", 0) or 0)
                    raw_model = msg.get("model")
                    pk = _classify_model(raw_model)
                    a, b, c = _bucket_for(ts, now)
                    _accum_tokens(out[provider], in_t, out_t, pk, a, b, c, raw_model=raw_model)
        except OSError:
            partial = True
            continue
    return out, partial


def _scan_codex_jsonls() -> tuple[dict, dict | None, bool]:
    """扫 ~/.codex/sessions/*.jsonl.

    返回 (codex_bucket, rate_limits_or_none, partial).

    rate_limits 来自 codex 自身 token_count 事件里的 payload.info.rate_limits — 这是真实
    OpenAI 余额数据 (primary/secondary used_percent + resets_at + plan_type)。我们取所有
    session 中 timestamp 最新的那个 token_count 的 rate_limits 作为 "当前余额"。
    """
    out = _empty_bucket()
    rate_limits: dict | None = None
    rate_limits_ts: float = 0.0
    root = Path.home() / ".codex" / "sessions"
    now = datetime.now(timezone.utc)
    files, partial = _recent_jsonls(root, _SCAN_FILE_CAP)
    if not files and not root.is_dir():
        return out, None, True
    for f in files:
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                file_last_in = 0
                file_last_out = 0
                file_last_ts = now
                model_str = None
                for line in fh:
                    if "token_count" not in line and "model" not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if obj.get("type") == "session_meta":
                        model_str = (obj.get("payload") or {}).get("model")
                    payload = obj.get("payload") or {}
                    if payload.get("type") == "token_count":
                        info = payload.get("info") or {}
                        usage = info.get("total_token_usage") or {}
                        file_last_in = int(usage.get("input_tokens", 0) or 0)
                        file_last_out = int(usage.get("output_tokens", 0) or 0)
                        ts_raw = obj.get("timestamp")
                        try:
                            file_last_ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")) if ts_raw else now
                        except Exception:  # noqa: BLE001
                            file_last_ts = now
                        # 真余额: codex 自报 rate_limits — 取所有 session 中最新的那条
                        rl = payload.get("rate_limits") or info.get("rate_limits")
                        if rl and isinstance(rl, dict):
                            ts_epoch = file_last_ts.timestamp()
                            if ts_epoch > rate_limits_ts:
                                rate_limits_ts = ts_epoch
                                rate_limits = {
                                    **rl,
                                    "_observed_at": file_last_ts.isoformat(),
                                    "_observed_model_context_window": info.get("model_context_window"),
                                }
                pk = _classify_model(model_str)
                a, b, c = _bucket_for(file_last_ts, now)
                _accum_tokens(out, file_last_in, file_last_out, pk, a, b, c, raw_model=model_str)
        except OSError:
            partial = True
            continue
    return out, rate_limits, partial


def _scan_session_activity() -> tuple[list[dict], dict, bool]:
    """读 claude jsonl 推算每个 session 的 (cwd, started, ended, msg_count).

    返回 (sessions, totals, partial). totals = {today_minutes, week_minutes, month_minutes, last_active_at}.
    """
    sessions: list[dict] = []
    totals = {"today_minutes": 0.0, "week_minutes": 0.0, "month_minutes": 0.0, "last_active_at": None}
    root = Path.home() / ".claude" / "projects"
    now = datetime.now(timezone.utc)
    files, partial = _recent_jsonls(root, _SCAN_FILE_CAP)
    if not files and not root.is_dir():
        return sessions, totals, True
    last_overall: datetime | None = None
    for f in files:
        # cwd 推断: 父目录名形如 "E--WindowsWorkspace-omnicompany" → "E:/WindowsWorkspace/omnicompany"
        parent_name = f.parent.name
        cwd_guess = parent_name.replace("--", ":/", 1).replace("-", "/") if "--" in parent_name else parent_name
        first_ts: datetime | None = None
        last_ts: datetime | None = None
        try:
            # 仅取首行时间戳作为 started_at; last_ts 用文件 mtime
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                head = fh.readline()
            try:
                obj = json.loads(head)
                ts_raw = obj.get("timestamp")
                if ts_raw:
                    first_ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                pass
            try:
                last_ts = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            except OSError:
                last_ts = None
        except OSError:
            partial = True
            continue
        if not first_ts or not last_ts:
            continue
        duration_s = (last_ts - first_ts).total_seconds()
        if duration_s < 60:
            continue
        minutes = duration_s / 60.0
        sessions.append({
            "cwd": cwd_guess,
            "session_id": f.stem,
            "started_at": first_ts.isoformat(),
            "last_active_at": last_ts.isoformat(),
            "minutes": minutes,
        })
        # 正确的窗口分钟算法: 计算 session 区间 [first_ts, last_ts] 与 [bucket_start, now]
        # 的 OVERLAP, 而不是把整个 duration 塞进 last_ts 所在的桶。
        # 之前的 bug: 一个 30 天前开始、今天 mtime 的 jsonl, today_minutes 会被加 +30 天 ×
        # 1440 分钟 → 三窗口全相同。
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=now.weekday())
        month_start = today_start.replace(day=1)

        def _overlap_min(bucket_start: datetime) -> float:
            a = max(first_ts, bucket_start)
            b = min(last_ts, now)
            return max(0.0, (b - a).total_seconds() / 60.0)

        totals["today_minutes"] += _overlap_min(today_start)
        totals["week_minutes"] += _overlap_min(week_start)
        totals["month_minutes"] += _overlap_min(month_start)

        if last_overall is None or last_ts > last_overall:
            last_overall = last_ts
    if last_overall:
        totals["last_active_at"] = last_overall.isoformat()
    return sessions, totals, partial


def _workspace_root() -> Path:
    # 委托到唯一权威 core.config.omni_workspace_root(), 不再硬编码 parents[N]
    return omni_workspace_root()


def _value(v: Any) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _reviewstage_briefing() -> dict[str, Any]:
    try:
        from .reviewstage.routes import get_store
        store = get_store()
        store.reload()
        items = store.list()
    except Exception as e:  # noqa: BLE001
        return {
            "available": False,
            "error": f"{type(e).__name__}: {e}",
            "total": 0,
            "by_status": {},
            "by_tier": {},
            "mandatory_unaccepted": 0,
            "pushed_unread": 0,
            "recent": [],
        }

    by_status: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    mandatory_unaccepted = 0
    pushed_unread = 0
    recent: list[dict[str, Any]] = []
    for m in items:
        status = _value(m.status)
        tier = _value(m.tier)
        by_status[status] = by_status.get(status, 0) + 1
        by_tier[tier] = by_tier.get(tier, 0) + 1
        if tier == "mandatory" and status in {"pending", "rejected", "blocked"}:
            mandatory_unaccepted += 1
        if m.pushed_to_user and status == "pending":
            pushed_unread += 1
        if len(recent) < 8:
            recent.append({
                "id": m.id,
                "title": m.title,
                "kind": _value(m.kind),
                "tier": tier,
                "status": status,
                "source_plan_id": m.source_plan_id,
                "source_subagent_id": m.source_subagent_id,
                "pushed_to_user": m.pushed_to_user,
                "updated_at": m.updated_at,
                "open_ref": {"type": "review_material", "id": m.id},
            })
    return {
        "available": True,
        "total": len(items),
        "by_status": by_status,
        "by_tier": by_tier,
        "mandatory_unaccepted": mandatory_unaccepted,
        "pushed_unread": pushed_unread,
        "recent": recent,
    }


def _control_observability_summary() -> dict[str, Any]:
    try:
        return get_control_observability_store().summary(recent_limit=10)
    except Exception as e:  # noqa: BLE001
        return {
            "controls": {"available": False, "error": f"{type(e).__name__}: {e}", "items": [], "by_key": {}},
            "observability": {
                "available": False,
                "error": f"{type(e).__name__}: {e}",
                "settings": {"dimensions": {}, "history": []},
                "recent": [],
            },
        }


def _briefing_from_parts(
    plan_entries: list[Any],
    sub_payload: dict[str, Any],
    review: dict[str, Any],
    control_observability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # 用户明示(2026-06-05): 不要模糊的"活跃"判定 —— 直接按更新时间排列, 突出 24h 内有更新的。
    # plan_entries 已由 scanner 按 last_modified_ts 倒序。非终态优先入列表; 计数=24h 内更新数。
    _fresh_cutoff = datetime.now(timezone.utc).timestamp() - 86400  # 24 小时

    def _terminal(e: Any) -> bool:
        return (getattr(e, "status", "") or "").lower() in {"done", "archived", "superseded"}

    def _plan_mtime(e: Any) -> float:
        ts = getattr(e, "last_modified_ts", None)
        if not ts:
            return 0.0
        try:
            return datetime.fromisoformat(ts).timestamp()
        except (ValueError, TypeError):
            return 0.0

    def _fresh24(e: Any) -> bool:
        return _plan_mtime(e) >= _fresh_cutoff

    active_plans = [e for e in plan_entries if not _terminal(e)]  # 列表源(已按时间排序)
    fresh_plans = [e for e in active_plans if _fresh24(e)]        # 24h 内更新的(高亮+计数)
    done_plans = [e for e in plan_entries if (getattr(e, "status", "") or "").lower() == "done"]
    subagents = sub_payload.get("subagents") or []
    running_subagents = [s for s in subagents if s.get("state") == "running"]
    blocked_subagents = [s for s in subagents if s.get("state") == "blocked"]
    by_status = review.get("by_status") or {}
    rejected = int(by_status.get("rejected", 0) or 0)
    blocked_materials = int(by_status.get("blocked", 0) or 0)
    pending = int(by_status.get("pending", 0) or 0)
    mandatory_unaccepted = int(review.get("mandatory_unaccepted") or 0)
    pushed_unread = int(review.get("pushed_unread") or 0)

    if mandatory_unaccepted or blocked_subagents or blocked_materials:
        severity = "critical"
        headline = "需要处理阻断"
    elif rejected or pushed_unread or pending or running_subagents:
        severity = "attention"
        headline = "有待审阅事项"
    else:
        severity = "calm"
        headline = "系统平稳"

    next_actions: list[dict[str, Any]] = []
    if mandatory_unaccepted:
        next_actions.append({
            "kind": "review",
            "label": f"{mandatory_unaccepted} 个必验收 material 待处理",
            "priority": "critical",
            "target": "reviewstage",
        })
    if blocked_subagents:
        next_actions.append({
            "kind": "subagent",
            "label": f"{len(blocked_subagents)} 个 subagent 阻断",
            "priority": "critical",
            "target": "subagents",
        })
    if pushed_unread:
        next_actions.append({
            "kind": "review",
            "label": f"{pushed_unread} 个总控推送待看",
            "priority": "attention",
            "target": "reviewstage",
        })
    if pending and not mandatory_unaccepted:
        next_actions.append({
            "kind": "review",
            "label": f"{pending} 个普通 material 待审",
            "priority": "attention",
            "target": "reviewstage",
        })
    if running_subagents:
        next_actions.append({
            "kind": "subagent",
            "label": f"{len(running_subagents)} 个 subagent 正在运行",
            "priority": "info",
            "target": "subagents",
        })
    if not next_actions:
        next_actions.append({
            "kind": "calm",
            "label": "当前没有必须立即处理的事项",
            "priority": "calm",
            "target": None,
        })

    control_observability = control_observability or _control_observability_summary()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "severity": severity,
        "headline": headline,
        "all_green": severity == "calm",
        "summary": {
            "plans_total": len(plan_entries),
            "plans_active": len(fresh_plans),  # 24h 内有更新的(突出口径), 不再是"所有非终态"
            "plans_done": len(done_plans),
            "review_total": review.get("total", 0),
            "review_pending": pending,
            "mandatory_unaccepted": mandatory_unaccepted,
            "pushed_unread": pushed_unread,
            "subagents_total": len(subagents),
            "subagents_running": len(running_subagents),
            "subagents_blocked": len(blocked_subagents),
        },
        "review": review,
        "controls": control_observability.get("controls", {}),
        "observability": control_observability.get("observability", {}),
        "plans": {
            "total": len(plan_entries),
            "active": [
                {
                    "plan_id": getattr(e, "plan_id", ""),
                    "title": getattr(e, "title", "") or getattr(e, "plan_id", ""),
                    "status": getattr(e, "status", ""),
                    "todo_done": getattr(e, "todo_done", 0),
                    "todo_total": getattr(e, "todo_total", 0),
                    "last_modified_ts": getattr(e, "last_modified_ts", None),
                    "fresh_24h": _fresh24(e),
                    "open_ref": {"type": "plan", "id": getattr(e, "plan_id", "")},
                }
                for e in active_plans[:15]
            ],
        },
        "subagents": {
            "total": len(subagents),
            "running": running_subagents[:10],
            "blocked": blocked_subagents[:10],
        },
        "next_actions": next_actions[:8],
        "secretary": {
            "title": headline,
            "body": (
                "没有阻断和待审推送, 可以继续推进下一阶段。"
                if severity == "calm"
                else "先处理阻断和待审材料, 再继续派工。"
                if severity == "critical"
                else "建议先扫一遍待审材料和后台运行线程。"
            ),
        },
    }


@boss_sight_router.get("/health")
async def health() -> dict[str, Any]:
    """探测 BOSS SIGHT 模块是否能正常 import + 总控 prompt 是否就位."""
    ws = _workspace_root()
    try:
        from .controller.worker import BossSightControllerWorker  # noqa: F401
        worker_import_ok = True
    except Exception as e:  # noqa: BLE001
        worker_import_ok = False
        return {
            "status": "broken",
            "worker_import": False,
            "error": f"{type(e).__name__}: {e}",
            "workspace_root": str(ws),
        }
    from .controller.worker import _SYSTEM_PROMPT
    return {
        "status": "ok",
        "worker_import": True,
        "system_prompt_chars": len(_SYSTEM_PROMPT),
        "workspace_root": str(ws),
    }


@boss_sight_router.get("/control")
async def get_control_state() -> dict[str, Any]:
    return get_control_observability_store().list_controls()


@boss_sight_router.post("/control/{key:path}")
async def update_control_state(key: str, body: ControlUpdateBody) -> dict[str, Any]:
    try:
        item = get_control_observability_store().set_control(
            key,
            body.value,
            actor=body.actor,
            reason=body.reason,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"unknown control key: {str(e).strip(chr(39))}") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return item


@boss_sight_router.get("/user-prefs")
async def get_user_prefs() -> dict[str, Any]:
    return get_control_observability_store().get_user_prefs()


@boss_sight_router.post("/user-prefs/permanent_allow")
async def add_permanent_allow(body: PermanentAllowBody) -> dict[str, Any]:
    try:
        entry = get_control_observability_store().add_permanent_allow(
            scope=body.scope,
            tool=body.tool,
            pattern=body.pattern,
            reason=body.reason,
            actor=body.actor,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return entry


# ── WORK-LIFECYCLE: 网页端启动 plan 执行 ──────────────────────────────
class PlanRunBody(BaseModel):
    plan_id: str
    carrier: str = "sdk"
    cwd: str | None = None
    steps: int | None = None
    until_task: str | None = None
    hold_at_review: bool = False
    keep_going: bool = False
    override_gate: bool = False


def _plan_runs_dir() -> Path:
    d = omni_workspace_root() / "data" / "lifecycle" / "plan_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


@boss_sight_router.post("/plan/run")
async def start_plan_run(body: PlanRunBody) -> dict[str, Any]:
    """网页端启动一个 plan 的执行(后台线程跑, 进度走 task board / multiagent view 观测)。

    先过完成度硬门(不过且未 override → 400 拒绝); 通过则后台驱动, 立即返回 run_id。
    """
    import threading
    import uuid as _uuid

    from omnicompany.packages.services._core.lifecycle.run_plan import run_plan
    from omnicompany.packages.services._core.plan_audit.gate import check_plan_dispatch_gate

    gate = check_plan_dispatch_gate(body.plan_id)
    if not gate["ok"] and not body.override_gate:
        raise HTTPException(status_code=400, detail={"error": "plan 完成度硬门未过, 拒绝执行",
                                                     "gate": gate})
    run_id = f"run-{_uuid.uuid4().hex[:10]}"
    status_path = _plan_runs_dir() / f"{run_id}.json"
    status: dict[str, Any] = {"run_id": run_id, "plan_id": body.plan_id,
                              "state": "running", "events": []}
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    def _on_event(ev: dict[str, Any]) -> None:
        status["events"].append(ev)
        try:
            status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _drive() -> None:
        try:
            rep = run_plan(body.plan_id, carrier=body.carrier, cwd=body.cwd,
                           max_steps=body.steps, until_task=body.until_task,
                           hold_at_review=body.hold_at_review,
                           keep_going_on_fail=body.keep_going,
                           override_gate=body.override_gate, on_event=_on_event)
            status.update({"state": "done", "report": rep})
        except Exception as e:  # noqa: BLE001
            status.update({"state": "error", "error": str(e)})
        try:
            status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    threading.Thread(target=_drive, daemon=True, name=f"plan-run-{run_id}").start()
    return {"started": True, "run_id": run_id, "plan_id": body.plan_id,
            "observe": f"omni task board '{body.plan_id}'  (或 GET /api/boss-sight/plan/run/{run_id})"}


@boss_sight_router.get("/plan/run/{run_id}")
async def get_plan_run(run_id: str) -> dict[str, Any]:
    """轮询一次 plan run 的进度/结果。"""
    p = _plan_runs_dir() / f"{run_id}.json"
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")
    return json.loads(p.read_text(encoding="utf-8"))


@boss_sight_router.get("/observability/settings")
async def get_observability_settings() -> dict[str, Any]:
    return get_control_observability_store().observability_settings()


@boss_sight_router.post("/observability/settings")
async def update_observability_settings(body: ObservabilitySettingsBody) -> dict[str, Any]:
    try:
        return get_control_observability_store().set_observability_settings(
            body.dimensions,
            actor=body.actor,
            reason=body.reason,
        )
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"unknown observability dimension: {str(e).strip(chr(39))}") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@boss_sight_router.post("/observability/event")
async def record_observability_event(body: ObservationEventBody) -> dict[str, Any]:
    try:
        return get_control_observability_store().record_observation(
            dimension=body.dimension,
            surface=body.surface,
            target=body.target,
            value=body.value,
            meta=body.meta,
            actor=body.actor,
        )
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"unknown observability dimension: {str(e).strip(chr(39))}") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@boss_sight_router.get("/observability/recent")
async def get_recent_observability(limit: int = 20) -> dict[str, Any]:
    items = get_control_observability_store().recent_observations(limit)
    return {"items": items, "count": len(items)}


@boss_sight_router.get("/plans")
async def list_plans() -> dict[str, Any]:
    """plan 索引平铺 — 供 claudecodeui sidebar 做"按内部 plan 分组对话"用.

    NOT folder-derived: 数据源是 docs/plans/{category}/[ts]ID/plan.md 的
    frontmatter (PlanIndexScanner), 跟仓库目录扫描无关.

    返回值字段贴 PlanIndexEntry.to_dict(), 关键字段:
    - plan_id        (category/[ts]ID)
    - category       (顶层分组, 如 dashboard / voxelcraft / cli)
    - project_path   (关联的 docs/plans/<category>/project.md 相对路径; 给前端做 cwd→plan 推断时, 用 category 段)
    - title / status / todo_done / todo_total / last_modified_ts
    """
    ws = _workspace_root()
    scanner = PlanIndexScanner(ws)
    entries = scanner.scan()
    return {
        "plans": [e.to_dict() for e in entries],
        "total": len(entries),
    }


@boss_sight_router.get("/ctx")
async def get_ctx() -> dict[str, Any]:
    """当前 ctx 快照: plan 索引 + subagent 活跃情况.

    外部维护会话用这个看总控每次唤起会看到什么."""
    ws = _workspace_root()
    plan_scanner = PlanIndexScanner(ws)
    plan_entries = plan_scanner.scan()
    plan_payload = plan_scanner.to_material_payload(plan_entries)
    sub_agg = SubagentStatusAggregator(ws)
    sub_agg.refresh_from_cc_sessions()
    sub_payload = sub_agg.to_material_payload()
    control_observability = _control_observability_summary()
    material_registry = build_material_registry(limit=120, ws=ws)
    cockpit = build_cockpit_snapshot(
        ws=ws,
        attention_limit=10,
        notification_limit=10,
        material_limit=80,
    )
    workflow = build_workflow_summary(
        ws=ws,
        cockpit_snapshot=cockpit,
        action_limit=20,
    )
    return {
        "plan_index": plan_payload,
        "subagent_status": sub_payload,
        "material_registry": material_registry.get("summary", {}),
        "cockpit": cockpit.get("ctx_summary", {}),
        "workflow_summary": workflow.get("ctx_summary", {}),
        "controls": control_observability.get("controls", {}),
        "observability": control_observability.get("observability", {}),
    }


@boss_sight_router.get("/briefing")
async def get_briefing() -> dict[str, Any]:
    """First-screen deterministic briefing for the BOSS SIGHT shell."""
    ws = _workspace_root()
    plan_scanner = PlanIndexScanner(ws)
    plan_entries = plan_scanner.scan()
    sub_agg = SubagentStatusAggregator(ws)
    sub_agg.refresh_from_cc_sessions()
    review = _reviewstage_briefing()
    briefing = _briefing_from_parts(
        plan_entries,
        sub_agg.to_material_payload(),
        review,
        _control_observability_summary(),
    )
    briefing["workflow_summary"] = build_workflow_summary(
        ws=ws,
        action_limit=20,
    ).get("ctx_summary", {})
    return briefing


# residents 取数:常态走 Rust scanner(127.0.0.1:8765,~ms,带真 5 态),仅 scanner 断了才回落
# Python 全扫(~6.8s)。薄合并层 + 后台刷新:永不阻塞 UI,且 Rust 在跑时近实时(TTL 3s)。
# (不是"快照"—— 源是活的 scanner;此层只为合并突发轮询 + 兜底 Python 慢路径。)
_RESIDENTS_CACHE: dict[str, Any] = {"payload": None, "ts": 0.0, "refreshing": False}
_RESIDENTS_TTL = 3.0
_RESIDENT_TAIL_TTL = 5.0
_RESIDENT_TAIL_CACHE_MAX = 256
_RESIDENT_TAIL_CACHE: dict[tuple[str, int], tuple[float, list[dict[str, Any]] | None]] = {}
_RESIDENT_TAIL_INFLIGHT: dict[
    tuple[str, int], asyncio.Task[list[dict[str, Any]] | None]
] = {}


def _refresh_residents() -> None:
    from .services.residents import build_residents
    try:
        payload = build_residents()
        _RESIDENTS_CACHE["payload"] = payload
        _RESIDENTS_CACHE["ts"] = time.time()
    finally:
        _RESIDENTS_CACHE["refreshing"] = False


@boss_sight_router.get("/residents")
async def get_residents() -> dict[str, Any]:
    """多-agent 监控:本机所有在跑 agent 的统一列表(后台刷新缓存,永不阻塞 UI)。

    来源 Rust agent-scanner 索引(回落 Python 扫描),agent_digest 回填 title/last_step,
    总控作第一公民钉顶。给 multiagent view 网格消费。见
    docs/plans/[2026-06-24]MULTIAGENT-AND-REVIEW-REDESIGN/plan.md (P1/P3)。
    """
    import asyncio
    import threading

    c = _RESIDENTS_CACHE
    now = time.time()
    if c["payload"] is None:  # 冷启:首帧丢线程池跑, 别阻塞事件循环
        c["payload"] = await asyncio.get_running_loop().run_in_executor(None, _residents_now)
        c["ts"] = time.time()
        return c["payload"]
    if now - c["ts"] > _RESIDENTS_TTL and not c["refreshing"]:
        c["refreshing"] = True
        threading.Thread(target=_refresh_residents, daemon=True, name="residents-refresh").start()
    return c["payload"]  # 立刻返回上一份(stale-while-revalidate)


def _residents_now() -> dict[str, Any]:
    from .services.residents import build_residents
    return build_residents()


@boss_sight_router.get("/active-bindings")
async def get_active_bindings() -> dict[str, Any]:
    """活跃对话按 plan / project / task 聚合(计数 + 清单)。

    给计划/项目/任务/对话四页显示"有多少活跃对话在推进它"。数据来自 build_residents
    (Rust 优先 + digest 回填,覆盖 chat + PTY 全部本机 claude/codex 会话),只做确定性
    分组计数。见 docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.5)。
    """
    import asyncio

    from .services.binding_aggregate import aggregate

    return await asyncio.get_running_loop().run_in_executor(None, aggregate)


@boss_sight_router.get("/residents/{session_id}/tail")
async def get_resident_tail(session_id: str, n: int = 14) -> dict[str, Any]:
    """某 agent 会话最近活动行 [{role,text}] —— multiagent 详情面板的"它在干嘛"feed。
    数据来自 Rust scanner 的 /sessions/<id>/tail;scanner 不在 → 空(前端退只显摘要)。"""
    from .services import rust_scanner_client

    n = max(1, min(int(n), 40))
    key = (session_id, n)
    cached = _RESIDENT_TAIL_CACHE.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] <= _RESIDENT_TAIL_TTL:
        lines = cached[1]
        return {"lines": (lines or [])[-n:], "available": lines is not None}

    async def load() -> list[dict[str, Any]] | None:
        try:
            # urllib is synchronous and may wait for the Rust scanner timeout.
            # Never run it on the same event loop that forwards terminal bytes.
            lines = await asyncio.to_thread(rust_scanner_client.tail, session_id, n=n)
            _RESIDENT_TAIL_CACHE[key] = (time.monotonic(), lines)
            if len(_RESIDENT_TAIL_CACHE) > _RESIDENT_TAIL_CACHE_MAX:
                oldest = min(_RESIDENT_TAIL_CACHE, key=lambda item: _RESIDENT_TAIL_CACHE[item][0])
                _RESIDENT_TAIL_CACHE.pop(oldest, None)
            return lines
        finally:
            if _RESIDENT_TAIL_INFLIGHT.get(key) is asyncio.current_task():
                _RESIDENT_TAIL_INFLIGHT.pop(key, None)

    task = _RESIDENT_TAIL_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(load())
        _RESIDENT_TAIL_INFLIGHT[key] = task
    lines = await asyncio.shield(task)
    return {"lines": (lines or [])[-n:], "available": lines is not None}


class ActiveContextBody(BaseModel):
    session_id: str | None = None
    kind: str = "conversation"
    source: str = "ui"


@boss_sight_router.get("/context/active")
async def get_active_context() -> dict[str, Any]:
    """当前选中的对话/上下文(跟随脊梁)。审阅台跟随视图/peek 据此过滤。

    见 docs/plans/[2026-06-24]MULTIAGENT-AND-REVIEW-REDESIGN/plan.md (P0)。
    """
    from .services.active_context import get_active

    return get_active()


@boss_sight_router.post("/context/active")
async def set_active_context(body: ActiveContextBody) -> dict[str, Any]:
    """设当前选中上下文并广播给订阅者(页签焦点解析器 / multiagent 点选 / 审阅台调用)。"""
    from .services.active_context import set_active

    return set_active(body.session_id, body.kind, body.source)


class ResolveTabBody(BaseModel):
    label: str | None = None
    cwd: str | None = None
    set_active: bool = True


@boss_sight_router.post("/context/resolve-tab")
async def resolve_focused_tab(body: ResolveTabBody) -> dict[str, Any]:
    """把"聚焦的 Claude 对话页签"(标题 + workspace cwd)解析成 session_id 并(可选)设为当前上下文。

    omnichat 扩展监听 tabGroups,焦点切到 claudeVSCodePanel 时调本接口。低置信(needs_escalation)
    时前端/扩展可再走 OCR/UIA 拿更准的 label 重试。见 plan P1。
    """
    from .services.active_context import set_active
    from .services.residents import build_residents
    from .services.session_resolver import needs_escalation, resolve

    residents = build_residents().get("residents", [])
    result = resolve(body.label, body.cwd, residents)
    if result is None:
        return {"resolved": False, "escalate": False}
    if body.set_active:
        set_active(result["session_id"], kind="conversation", source="tab-focus")
    return {
        "resolved": True,
        "session_id": result["session_id"],
        "confidence": result["confidence"],
        "reason": result["reason"],
        "escalate": needs_escalation(result),
    }


# ── AGENT 自我身份 + 主动请求审阅本对话 + 发回意见 ───────────────────────
# 用户(2026-06-25): agent 应能感知/输入自己的身份, 主动让用户专注审阅本对话;
# 详情里「发回意见」按钮把指示经 hook 发回 agent。见 plan 的 work-report 控制台。
def _identity_of(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": r.get("session_id"),
        "provider": r.get("provider"),
        "identity": r.get("identity"),
        "project": r.get("project"),
        "role": r.get("role"),
        "name": r.get("name"),
        "location": r.get("location"),
        "current_task": r.get("current_task") or r.get("title"),
    }


def _resolve_self(session_id: str | None, cwd: str | None) -> dict[str, Any] | None:
    """把 (session_id | cwd) 解析成本机某个常驻 agent 的身份记录。"""
    from .services.residents import build_residents
    from .services.session_resolver import resolve

    residents = build_residents().get("residents", [])
    if session_id:
        for r in residents:
            if r.get("session_id") == session_id:
                return r
    if cwd:
        res = resolve(None, cwd, residents)
        if res:
            sid = res["session_id"]
            for r in residents:
                if r.get("session_id") == sid:
                    return r
    return None


class AgentWhoamiBody(BaseModel):
    session_id: str | None = None
    cwd: str | None = None


@boss_sight_router.post("/agent/whoami")
async def agent_whoami(body: AgentWhoamiBody) -> dict[str, Any]:
    """agent 感知自己被派生出的身份(从 cwd/session 解析常驻列表里的自己)。"""
    r = _resolve_self(body.session_id, body.cwd)
    if r is None:
        return {"resolved": False, "cwd": body.cwd}
    return {"resolved": True, **_identity_of(r)}


class AgentRequestReviewBody(BaseModel):
    headline: str = Field(..., min_length=1, max_length=280)
    session_id: str | None = None
    cwd: str | None = None
    identity: str = ""
    project: str = ""
    role: str = ""
    name: str = ""
    kind: str = "conversation"
    set_active: bool = True


@boss_sight_router.post("/agent/request-review")
async def agent_request_review(body: AgentRequestReviewBody) -> dict[str, Any]:
    """agent 主动举手:让用户在审阅总览/多Agent 顶部专注于本对话的工作报告。"""
    from .services import agent_attention
    from .services.active_context import set_active

    sid = body.session_id
    ident = {"identity": body.identity, "project": body.project, "role": body.role, "name": body.name}
    r = _resolve_self(body.session_id, body.cwd)
    if r is not None:
        sid = sid or r.get("session_id")
        for k in ("identity", "project", "role", "name"):
            if not ident[k]:
                ident[k] = r.get(k, "") or ""
    rec = agent_attention.request_attention(sid or "", headline=body.headline, kind=body.kind, **ident)
    if body.set_active and sid:
        set_active(sid, kind="conversation", source="agent-request")
    return {"ok": True, "request": rec, "session_id": sid, "resolved_identity": r is not None}


@boss_sight_router.get("/agent/attention")
async def agent_attention_list() -> dict[str, Any]:
    """当前活跃的「请审阅」举手列表(多Agent / 审阅总览顶部消费)。"""
    from .services import agent_attention

    items = agent_attention.list_attention()
    return {"items": items, "count": len(items)}


@boss_sight_router.post("/agent/resolve/{session_id}")
async def agent_attention_resolve(session_id: str) -> dict[str, Any]:
    """用户审完/发回意见后,把该对话的举手标记为已处理。"""
    from .services import agent_attention

    return {"ok": agent_attention.resolve_attention(session_id)}


class AgentFeedbackBody(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=2000)
    author: str = "user"
    resolve: bool = False


@boss_sight_router.post("/agent/feedback")
async def agent_feedback_post(body: AgentFeedbackBody) -> dict[str, Any]:
    """「发回意见」:把用户指示压入该对话的反馈队列(agent 侧 hook 取走继续)。"""
    from .services import agent_attention

    item = agent_attention.queue_feedback(body.session_id, body.message, author=body.author)
    if body.resolve:
        agent_attention.resolve_attention(body.session_id)
    return {"ok": True, "item": item}


@boss_sight_router.get("/agent/feedback/{session_id}")
async def agent_feedback_pull(session_id: str, mark: bool = True) -> dict[str, Any]:
    """agent hook 取走某对话未消费的反馈。mark=False 仅 peek。"""
    from .services import agent_attention

    items = agent_attention.pop_feedback(session_id, mark=mark)
    return {"items": items, "count": len(items)}


@boss_sight_router.get("/cockpit")
async def get_cockpit(
    attention_limit: int = 30,
    notification_limit: int = 30,
    material_limit: int = 200,
) -> dict[str, Any]:
    """Backend-first cockpit contract for humans and AI."""
    return build_cockpit_snapshot(
        ws=_workspace_root(),
        attention_limit=max(1, min(int(attention_limit), 100)),
        notification_limit=max(1, min(int(notification_limit), 100)),
        material_limit=max(1, min(int(material_limit), 500)),
    )


@boss_sight_router.get("/attention")
async def get_attention(
    attention_limit: int = 50,
    notification_limit: int = 50,
) -> dict[str, Any]:
    """Attention and notification queue for cockpit/header surfaces."""
    return build_attention_state(
        ws=_workspace_root(),
        attention_limit=max(1, min(int(attention_limit), 100)),
        notification_limit=max(1, min(int(notification_limit), 100)),
    )


@boss_sight_router.post("/actions/resolve")
async def resolve_cockpit_target(body: CockpitResolveBody) -> dict[str, Any]:
    """Resolve an action target/open_ref without mutating state."""
    try:
        resolved = resolve_action_target(ws=_workspace_root(), target=body.target)
    except CockpitActionError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    return {"ok": True, "resolved": resolved}


@boss_sight_router.post("/actions/execute")
async def execute_cockpit_action_route(body: CockpitActionBody) -> dict[str, Any]:
    """Execute a cockpit action and record an auditable backend event."""
    try:
        return execute_cockpit_action(
            ws=_workspace_root(),
            kind=body.kind,
            target=body.target,
            actor=body.actor,
            note=body.note,
            payload=body.payload,
        )
    except CockpitActionError as e:
        detail: dict[str, Any] = {"error": e.message}
        if e.event:
            detail["event"] = e.event
        raise HTTPException(status_code=e.status_code, detail=detail) from e


@boss_sight_router.get("/actions/events")
async def get_cockpit_action_events(limit: int = 50) -> dict[str, Any]:
    items = list_cockpit_action_events(ws=_workspace_root(), limit=limit)
    return {"items": items, "count": len(items)}


@boss_sight_router.get("/workflow-summary")
async def get_workflow_summary(action_limit: int = 40) -> dict[str, Any]:
    """Workflow-level summary for controller and cockpit surfaces."""
    return build_workflow_summary(
        ws=_workspace_root(),
        action_limit=max(1, min(int(action_limit), 100)),
    )


@boss_sight_router.get("/prompt")
async def get_prompt() -> dict[str, Any]:
    """当前 system prompt (外部维护会话查看用). 来自 controller/prompts/system.md."""
    try:
        from .controller.worker import _SYSTEM_PROMPT
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"prompt load failed: {e}") from e
    return {"system_prompt": _SYSTEM_PROMPT, "chars": len(_SYSTEM_PROMPT)}


@boss_sight_router.get("/entities")
async def list_entities(
    q: str = "",
    kind: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Unified entity index for @mentions.

    Same source as /search; display is human-short, uri is the stable storage key.
    """
    limit = max(1, min(int(limit), 100))
    items = search_entities(q, kind=kind, limit=limit, ws=_workspace_root())
    return {"items": items, "count": len(items), "query": q, "kind": kind}


@boss_sight_router.get("/entities/resolve")
async def resolve_entity(uri: str) -> dict[str, Any]:
    try:
        parse_entity_uri(uri)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    resolved = resolve_entity_uri(uri, ws=_workspace_root())
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"entity not found: {uri}")
    return resolved


@boss_sight_router.get("/search")
async def ultra_search(
    q: str = "",
    kind: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Ultra search endpoint. Reuses the entity index instead of notes-only search."""
    limit = max(1, min(int(limit), 100))
    items = search_entities(q, kind=kind, limit=limit, ws=_workspace_root())
    return {"items": items, "count": len(items), "query": q, "kind": kind}


@boss_sight_router.get("/material-registry")
async def get_material_registry(
    q: str = "",
    kind: str | None = None,
    role: str | None = None,
    layer: str | None = None,
    status: str | None = None,
    limit: int = 250,
) -> dict[str, Any]:
    """Semantic material registry for task context and execution boundaries."""
    limit = max(1, min(int(limit), 500))
    return build_material_registry(
        q=q,
        kind=kind,
        role=role,
        layer=layer,
        status=status,
        limit=limit,
        ws=_workspace_root(),
    )


# ── BOSS SIGHT 会话上下文聚合 (供 claudecodeui SessionContextPanel 调用) ──
# pty_routes 已有 GET /cc/sessions/{sid}/context, 但 /cc 不在 /api/boss-sight
# 命名空间下, 反代时不便统一。在此暴露同样数据的别名, 路径 /api/boss-sight/sessions/{sid}/context。
@boss_sight_router.get("/sessions/{sid}/context")
async def get_session_context_alias(sid: str) -> dict[str, Any]:
    """转发到 pty_routes.get_session_context — 保持唯一数据源, 仅作为命名空间别名。"""
    try:
        from ..ccdaemon.pty_routes import get_session_context
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"context handler not loadable: {e}") from e
    return await get_session_context(sid)


# ── InsightsTab 4 endpoints ─────────────────────────────────────────


@boss_sight_router.get("/insights/tokens")
async def insights_tokens() -> dict[str, Any]:
    """Token 用量 + 估算花费 (按 provider 拆 + 按 today/week/month 三窗口拆 + 按真实 model 拆).

    新增 (相对前一版):
      - {today,week,month}_usd 分别累计 (旧 estimated_usd = month_usd 保留兼容)
      - by_provider.*.by_model: 按 message.model 真实粒度拆 (claude-opus-4-6 等)
      - pricing: 暴露定价常量表, 让前端展示倍率
    """
    cached = _cached("tokens")
    if cached is not None:
        return cached
    by_provider, partial_c = _scan_claude_jsonls()
    codex_bucket, codex_rate_limits, partial_x = _scan_codex_jsonls()
    by_provider["codex"] = codex_bucket
    total = _empty_bucket()
    for b in by_provider.values():
        for k in ("today_tokens", "week_tokens", "month_tokens", "today_usd", "week_usd", "month_usd"):
            total[k] = total.get(k, 0) + b.get(k, 0)
        # 合并 by_model
        for model_key, m in (b.get("by_model") or {}).items():
            tm = total.setdefault("by_model", {}).setdefault(model_key, {"tokens": 0, "usd": 0.0, "pricing_key": m.get("pricing_key")})
            tm["tokens"] += m["tokens"]
            tm["usd"] += m["usd"]
    total["estimated_usd"] = total["month_usd"]  # alias
    payload: dict[str, Any] = {
        "by_provider": by_provider,
        "total": total,
        "pricing": {
            # USD per 1M tokens, [input, output]
            "opus":        {"input": _PRICING["opus"][0],        "output": _PRICING["opus"][1],        "note": "claude opus 任意子版本"},
            "sonnet":      {"input": _PRICING["sonnet"][0],      "output": _PRICING["sonnet"][1],      "note": "claude sonnet 任意子版本"},
            "haiku":       {"input": _PRICING["haiku"][0],       "output": _PRICING["haiku"][1],       "note": "claude haiku 任意子版本"},
            "gpt-5.4":     {"input": _PRICING["gpt-5.4"][0],     "output": _PRICING["gpt-5.4"][1],     "note": "codex gpt-5.4 系列"},
            "gpt-5.3-codex": {"input": _PRICING["gpt-5.3-codex"][0], "output": _PRICING["gpt-5.3-codex"][1], "note": "codex gpt-5.3 系列"},
        },
        "pricing_unit": "USD per 1M tokens",
        "pricing_default_fallback": "opus (5/25)",
    }
    if codex_rate_limits:
        payload["codex_rate_limits"] = codex_rate_limits
    if partial_c or partial_x:
        payload["partial"] = True
    return _set_cache("tokens", payload)


@boss_sight_router.get("/insights/active-time")
async def insights_active_time() -> dict[str, Any]:
    """活跃时长聚合."""
    cached = _cached("active_time")
    if cached is not None:
        return cached
    _sessions, totals, partial = _get_session_activity()
    payload: dict[str, Any] = {
        "today_minutes": round(totals["today_minutes"], 1),
        "week_minutes": round(totals["week_minutes"], 1),
        "month_minutes": round(totals["month_minutes"], 1),
        "last_active_at": totals["last_active_at"],
    }
    if partial:
        payload["partial"] = True
    return _set_cache("active_time", payload)


@boss_sight_router.get("/insights/projects")
async def insights_projects() -> dict[str, Any]:
    """按 cwd 聚合 top 20."""
    cached = _cached("projects")
    if cached is not None:
        return cached
    sessions, _totals, partial = _get_session_activity()
    agg: dict[str, dict] = {}
    for s in sessions:
        cwd = s["cwd"]
        slot = agg.setdefault(cwd, {"cwd": cwd, "total_minutes": 0.0, "session_count": 0, "last_active_at": None})
        slot["total_minutes"] += s["minutes"]
        slot["session_count"] += 1
        if slot["last_active_at"] is None or s["last_active_at"] > slot["last_active_at"]:
            slot["last_active_at"] = s["last_active_at"]
    items = sorted(agg.values(), key=lambda x: x["total_minutes"], reverse=True)[:20]
    for it in items:
        it["total_minutes"] = round(it["total_minutes"], 1)
    payload: dict[str, Any] = {"items": items}
    if partial:
        payload["partial"] = True
    return _set_cache("projects", payload)


@boss_sight_router.get("/insights/plans")
async def insights_plans() -> dict[str, Any]:
    """PlanIndexScanner 输出 + 每个 plan 关联的 session 计数 (按 category↔cwd 名匹配, 启发式)."""
    cached = _cached("insights_plans")
    if cached is not None:
        return cached
    ws = _workspace_root()
    scanner = PlanIndexScanner(ws)
    entries = scanner.scan()
    sessions, _t, partial = _get_session_activity()
    cat_counts: dict[str, int] = {}
    for s in sessions:
        cwd_l = s["cwd"].lower()
        for e in entries:
            if e.category.lower() in cwd_l:
                cat_counts[e.category] = cat_counts.get(e.category, 0) + 1
                break
    items = []
    for e in entries:
        d = e.to_dict()
        d["session_count"] = cat_counts.get(e.category, 0)
        items.append(d)
    payload: dict[str, Any] = {"plans": items, "total": len(items)}
    if partial:
        payload["partial"] = True
    return _set_cache("insights_plans", payload)


# (workboard 三态 lane 看板已于 2026-06-12 退役 — 项目模型唯一权威在 core/projects_registry,
#  API 在 dashboard 进程 controlplane/projects.py。用户原话: "本体应该独立于 dashboard 存放,
#  有唯一权威, 任何其他位置都应该被删除"。)


# 时间线 progress (project/plan 历史条目; CRUD 走 `omni progress` CLI, 网页只读看时间线)
@boss_sight_router.get("/progress")
async def get_progress(type: str | None = None, id: str | None = None) -> dict[str, Any]:
    """列出某 plan/project 的历史条目(按时间升序)。前端把它与 plan 目录文件 mtime 合并成时间线。"""
    from .progress import list_entries
    return {"entries": list_entries(type, id)}


# 用量 usage (#5): 用开源标准 ccusage 取 claude/codex 用量(5h 计费块 + 近 7 天), 工作板小组件用。
@boss_sight_router.get("/usage")
async def get_usage(force: bool = False) -> dict[str, Any]:
    """claude 5h 计费块 + 近 7天 / codex 近 7天 的实际消耗(ccusage 算)。非官方剩余%(见模块注释)。
    ccusage 走子进程(阻塞), 丢线程池跑, 别卡事件循环; 命中 180s 缓存时很快。"""
    import asyncio
    from .usage import build_usage
    data = await asyncio.get_running_loop().run_in_executor(None, build_usage, force)
    data["internal"] = build_llm_runtime_usage()
    return data


@boss_sight_router.get("/llm-runtime")
async def get_llm_runtime_usage() -> dict[str, Any]:
    return build_llm_runtime_usage()


# token 记账(#6 监督设施追加件): claude/codex 会话用量 + 内部调用账三路归并, 只读不落盘。
# 落盘版由 `omni token-ledger run` CLI / gov-token-ledger-daily 定时任务负责写 data/llm/token_ledger/。
# 权威: docs/plans/[2026-07-02]SEMANTIC-OS-MAP/overnight-run.md 第六节。
@boss_sight_router.get("/token-ledger")
async def get_token_ledger() -> dict[str, Any]:
    """按会话/按天/按项目三种聚合 + 未关联桶 + 内部调用账 + cron 字符串匹配(读文件, 丢线程池跑)。"""
    import asyncio
    return await asyncio.get_running_loop().run_in_executor(None, build_token_ledger_view)


__all__ = ["boss_sight_router"]
