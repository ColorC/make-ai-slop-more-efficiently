# [OMNI] origin=claude-code domain=dashboard/boss_sight/services ts=2026-06-24T00:00:00Z type=service
# [OMNI] material_id="material:dashboard.boss_sight.services.residents.py"
"""统一常驻 agent 列表 —— 给 multiagent view 网格消费。

权威: omnicompany/docs/plans/[2026-06-24]MULTIAGENT-AND-REVIEW-REDESIGN/plan.md (P1/P3)
      + omnicompany/docs/plans/[2026-06-24]RUST-PYTHON-HYBRID/plan.md (H4 消费侧)

来源:
  1. Rust agent-scanner 索引(rust_scanner_client)—— 机器级扫描+确定性派生,优先。
  2. 回落: 现有 Python agent_registry.rebuild()(Rust 不可用时)。
然后:
  - 用 agent_digest(性价比模型缓存)回填 title/last_step/project(LLM 那部分留 Python)。
  - 注入「总控」作第一公民常驻卡(钉顶)。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from . import rust_scanner_client

CONTROLLER_KEY = "controller:main"
_VOID_PROJECTS = {"", "无", "信息不足"}


def _controller_resident(managed: dict[str, Any] | None) -> dict[str, Any]:
    """合成总控常驻卡;有活跃托管态则点亮。"""
    rec: dict[str, Any] = {
        "key": CONTROLLER_KEY,
        "provider": "controller",
        "session_id": "main",
        "name": "总控",
        "project": "omnicompany",
        "role": "总控",
        "identity": "omnicompany-总控",
        "location": "omni-web",
        "current_task": "派发与统筹",
        "run_status": "idle",
        "running": False,
        "mtime": 0.0,
        "is_controller": True,
        "pinned": True,
    }
    if managed:
        alive = bool(managed.get("alive"))
        rec["running"] = alive
        rec["run_status"] = "working" if alive else "idle"
        if managed.get("active_plan"):
            rec["active_plan"] = managed["active_plan"]
            rec["current_task"] = f"统筹计划 {managed['active_plan']}"
    return rec


def _enrich_digest(rec: dict[str, Any], get_digest: Callable[[str, str], dict[str, Any] | None]) -> dict[str, Any]:
    """用便宜模型摘要回填 title/last_step/project（Rust 不算这部分）。"""
    dg = get_digest(rec.get("provider", ""), rec.get("session_id", "")) or {}
    title = (dg.get("title") or "").strip()
    last_step = (dg.get("last_step") or "").strip()
    if title:
        rec["title"] = title
    if last_step:
        rec["last_step"] = last_step
    if title and last_step:
        rec["current_task"] = f"{title} · {last_step}"
    elif title:
        rec["current_task"] = title
    dproj = (dg.get("project") or "").strip()
    if dproj and dproj not in _VOID_PROJECTS:
        rec["project"] = dproj
        rec["identity"] = f"{dproj}-{rec.get('role', '')}-{rec.get('name', '')}"
    return rec


def build_residents(
    now: float | None = None,
    *,
    limit: int = 80,
    rust_fetch: Callable[[], list[dict[str, Any]] | None] | None = None,
    python_fallback: Callable[[], list[dict[str, Any]]] | None = None,
    get_digest: Callable[[str, str], dict[str, Any] | None] | None = None,
    load_controller: Callable[[], dict[str, Any] | None] | None = None,
    get_attention: Callable[[], dict[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """统一常驻列表。依赖可注入(便于测试)。

    limit: 截断到前 limit 条(数据源已按 在跑→最近 排序),防 UI 被历史会话淹没。
    get_attention: {session_id → 举手请求};命中者标 attention + headline,且即便越过 cap
                   也保留(agent 主动请审阅的对话绝不能被历史会话淹掉)。
    """
    now = now if now is not None else time.time()

    if rust_fetch is None:
        rust_fetch = rust_scanner_client.residents
    if get_digest is None:
        from .agent_digest import get_digest as _gd

        get_digest = _gd
    if get_attention is None:
        from . import agent_attention

        def get_attention() -> dict[str, dict[str, Any]]:
            return agent_attention.attention_by_session(now=now)

    residents = rust_fetch()
    source = "rust"
    if residents is None:
        source = "python"
        if python_fallback is None:
            from . import agent_registry

            def python_fallback() -> list[dict[str, Any]]:
                return agent_registry.rebuild(now=now)

        residents = python_fallback() or []

    try:
        attn = get_attention() or {}
    except Exception:  # noqa: BLE001 — 举手取数失败不该挡住整列表
        attn = {}

    enriched: list[dict[str, Any]] = []
    for rec in residents:
        rec = dict(rec)
        try:
            rec = _enrich_digest(rec, get_digest)
        except Exception:  # noqa: BLE001 — 摘要回填失败不该挡住整列表
            pass
        a = attn.get(rec.get("session_id", "")) or attn.get(rec.get("key", ""))
        if a:
            rec["attention"] = True
            rec["attention_headline"] = a.get("headline", "")
            rec["attention_ts"] = a.get("ts")
        enriched.append(rec)

    total = len(enriched)
    if limit and limit > 0 and total > limit:
        head = enriched[:limit]
        # cap 之外仍保留任何举手请审阅的对话(去重)
        kept = {id(r) for r in head}
        extra_attn = [r for r in enriched[limit:] if r.get("attention") and id(r) not in kept]
        enriched = head + extra_attn
    controller_managed = load_controller() if load_controller else _load_controller_managed()
    out = [_controller_resident(controller_managed)] + enriched
    return {"source": source, "count": len(out), "total": total + 1, "now": now, "residents": out}


def _load_controller_managed() -> dict[str, Any] | None:
    """从 cc_sessions.json 找 kind=controller 的托管态(可选,失败静默)。"""
    try:
        from omnicompany.core.config import omni_workspace_root

        import json

        raw = json.loads((omni_workspace_root() / "data" / "cc_sessions.json").read_text(encoding="utf-8"))
        sessions = raw.get("sessions") if isinstance(raw, dict) else raw
        if isinstance(sessions, dict):
            sessions = list(sessions.values())
        for s in sessions or []:
            if isinstance(s, dict) and (s.get("kind") == "controller" or s.get("caller_identity") == "controller"):
                return s
    except Exception:  # noqa: BLE001
        return None
    return None
