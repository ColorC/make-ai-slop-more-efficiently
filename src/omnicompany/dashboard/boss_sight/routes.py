# [OMNI] origin=ai-ide ts=2026-05-24 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.routes.py"
"""routes — BOSS SIGHT FastAPI 路由.

块 1 重构后总控不是常驻 service, 而是按需 AgentNodeLoop. 本模块含健康/上下文/prompt/
材料注册表/驾驶舱/洞察/常驻agent/关注等约40个端点, 明细见代码 (下方 include_router /
@boss_sight_router 装饰器逐条列出, 本 docstring 不再逐一枚举以免与实现脱节).
"""

from __future__ import annotations

import asyncio
import threading
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
from .ccusage_stats import build_ccusage_stats
from .services.control_observability_store import get_control_observability_store

boss_sight_router = APIRouter(prefix="/api/boss-sight", tags=["boss-sight"])



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
        if tier == "mandatory" and status == "pending":  # 与 reviewstage/_stats 口径一致: 只数待审, 已驳回/卡住不再挂"必验收"
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


# ── 慢扫描端点的快照缓存(通用小件, 与 _RESIDENTS_CACHE 同思路) ────────────────
# briefing / workflow-summary / plans / ctx 的数据源都是全仓文件扫描(暖机后单次约 0.2-1.4s),
# 消费端是驾驶舱开页/轮询, 容忍十几秒陈旧。策略: 有快照永远秒回旧快照, 过期(>TTL)时起后台
# 线程刷新(stale-while-revalidate); 只有进程冷启第一问才真等一次构建(调用方已在线程池里)。
_SNAPSHOT_CACHE: dict[str, dict[str, Any]] = {}
_SNAPSHOT_LOCK = threading.Lock()


def _snapshot_refresh(key: str, builder) -> None:
    try:
        payload = builder()
    except Exception:  # noqa: BLE001 — 刷新失败保留旧快照, 下个过期请求再试
        with _SNAPSHOT_LOCK:
            ent = _SNAPSHOT_CACHE.get(key)
            if ent is not None:
                ent["refreshing"] = False
        return
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_CACHE[key] = {"payload": payload, "ts": time.time(), "refreshing": False}


def _snapshot_cached(key: str, ttl: float, builder) -> dict[str, Any]:
    """同步调用(端点经 asyncio.to_thread 进来): 秒回快照, 过期时后台刷新。"""
    now = time.time()
    with _SNAPSHOT_LOCK:
        ent = _SNAPSHOT_CACHE.get(key)
        if ent is not None:
            if now - float(ent["ts"]) > ttl and not ent["refreshing"]:
                ent["refreshing"] = True
                threading.Thread(
                    target=_snapshot_refresh, args=(key, builder),
                    daemon=True, name=f"snapshot-{key}",
                ).start()
            return ent["payload"]
    payload = builder()  # 冷启首问: 本线程(线程池)同步构建, 不堵事件循环
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_CACHE[key] = {"payload": payload, "ts": time.time(), "refreshing": False}
    return payload


def _list_plans_sync() -> dict[str, Any]:
    """list_plans 的同步实现体: PlanIndexScanner.scan() 全是阻塞文件扫描 I/O, 抽出来给
    asyncio.to_thread 用, 不堵事件循环。"""
    ws = _workspace_root()
    scanner = PlanIndexScanner(ws)
    entries = scanner.scan()
    return {
        "plans": [e.to_dict() for e in entries],
        "total": len(entries),
    }


@boss_sight_router.get("/plans")
async def list_plans() -> dict[str, Any]:
    """plan 索引平铺 — 供 claudecodeui sidebar 做"按内部 plan 分组对话"用.

    NOT folder-derived: 数据源是 docs/plans/{category}/[ts]ID/plan.md 的
    frontmatter (PlanIndexScanner), 跟仓库目录扫描无关.

    返回值字段贴 PlanIndexEntry.to_dict(), 关键字段:
    - plan_id        (category/[ts]ID)
    - category       (顶层分组, 如 dashboard / cli / research)
    - project_path   (关联的 docs/plans/<category>/project.md 相对路径; 给前端做 cwd→plan 推断时, 用 category 段)
    - title / status / todo_done / todo_total / last_modified_ts
    """
    return await asyncio.to_thread(_snapshot_cached, "plans", 15.0, _list_plans_sync)


def _get_ctx_sync() -> dict[str, Any]:
    """get_ctx 的同步实现体: plan 索引扫描 + subagent 状态刷新 + material registry + cockpit
    + workflow_summary 全是阻塞文件 I/O, 抽出来给 asyncio.to_thread 用, 不堵事件循环。"""
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


@boss_sight_router.get("/ctx")
async def get_ctx() -> dict[str, Any]:
    """当前 ctx 快照: plan 索引 + subagent 活跃情况.

    外部维护会话用这个看总控每次唤起会看到什么."""
    return await asyncio.to_thread(_snapshot_cached, "ctx", 30.0, _get_ctx_sync)


def _get_briefing_sync() -> dict[str, Any]:
    """get_briefing 的同步实现体: plan/subagent/reviewstage/workflow 全量扫描聚合, 全是阻塞
    文件 I/O, 抽出来给 asyncio.to_thread 用, 不堵事件循环。"""
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


@boss_sight_router.get("/briefing")
async def get_briefing() -> dict[str, Any]:
    """First-screen deterministic briefing for the BOSS SIGHT shell."""
    return await asyncio.to_thread(_snapshot_cached, "briefing", 15.0, _get_briefing_sync)


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

    给计划/项目/任务/对话四页显示"有多少活跃对话在推进它"。**复用 /residents 的
    stale-while-revalidate 缓存**(build_residents 冷路径是秒级全盘扫描,不能每请求重扫),
    上面只做确定性分组计数。见 docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.5)。
    """
    from .services.binding_aggregate import aggregate

    payload = await get_residents()  # 走 residents 缓存(冷启在 executor 里跑, 暖后即时)
    return aggregate(build=lambda: payload)


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


def _get_workflow_summary_sync(action_limit: int) -> dict[str, Any]:
    """get_workflow_summary 的同步实现体: build_workflow_summary 内部走文件扫描, 阻塞 I/O,
    抽出来给 asyncio.to_thread 用, 不堵事件循环。"""
    return build_workflow_summary(
        ws=_workspace_root(),
        action_limit=max(1, min(int(action_limit), 100)),
    )


@boss_sight_router.get("/workflow-summary")
async def get_workflow_summary(action_limit: int = 40) -> dict[str, Any]:
    """Workflow-level summary for controller and cockpit surfaces."""
    return await asyncio.to_thread(
        _snapshot_cached, f"workflow-summary:{action_limit}", 15.0,
        lambda: _get_workflow_summary_sync(action_limit),
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




# ccusage 统计(设置>Token统计 页签): 首次真正接入 ccusage 取「按天×软件×模型」token+成本 + 5h 计费块。
# ccusage 子进程扫 GB 级本地日志(阻塞且慢), 丢线程池跑 + 模块内 TTL 缓存; since/until = YYYYMMDD。
# 会话归属统一设施状态(唯一入口=session_attribution; 各渠道禁止自建会话分类)
@boss_sight_router.get("/session-attribution")
async def get_session_attribution() -> dict[str, Any]:
    """归属链状态 + LLM标注/词表规模(巡检与消费方自检用)。"""
    from .session_attribution import attribution_summary
    return attribution_summary()


@boss_sight_router.get("/ccusage")
async def get_ccusage_stats(
    since: str | None = None,
    until: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """各软件(agent) token 用量 + 成本(ccusage daily) + 当前 5h 计费块燃烧率(ccusage blocks)。"""
    import asyncio
    return await asyncio.get_running_loop().run_in_executor(
        None, lambda: build_ccusage_stats(since, until, force)
    )


__all__ = ["boss_sight_router"]
