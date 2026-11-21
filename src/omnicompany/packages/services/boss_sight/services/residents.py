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
from .project_resolution import canonical_project_id
from .session_lineage import attach_parent_titles, infer_session_lineage

CONTROLLER_KEY = "controller:main"
_VOID_PROJECTS = {"", "无", "信息不足"}


def _controller_resident(managed: dict[str, Any] | None) -> dict[str, Any]:
    """合成总控常驻卡;有活跃托管态则点亮。"""
    rec: dict[str, Any] = {
        "key": CONTROLLER_KEY,
        "provider": "controller",
        "session_id": "main",
        "name": "总控",
        "project": "omnidashboard",
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


def _canonicalize_project(
    rec: dict[str, Any], projects: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Keep only registered project ids; cwd folder names are not projects."""
    if projects is None:  # registry unavailable: preserve the last known projection
        return rec
    # A resident's workspace is transport/runtime metadata, not project
    # evidence. Project grouping is content/explicit-binding driven; passing
    # cwd here would silently recreate the old directory-based authority.
    project = canonical_project_id(label=rec.get("project"), projects=projects)
    if not project and rec.get("active_plan"):
        project = canonical_project_id(
            label=rec.get("active_plan"),
            projects=projects,
        )
    rec["project"] = project
    rec["identity"] = f"{project or '未绑定'}-{rec.get('role', '')}-{rec.get('name', '')}"
    return rec


def _enrich_binding(
    rec: dict[str, Any],
    binds: dict[str, dict[str, Any]],
    projects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """叠加会话自己声明的权威绑定(plan/project/task),优先于 Rust/digest 推测。

    见 docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.1/4.5)。
    """
    bd = _binding_for(rec, binds)
    if not bd:
        return rec
    if bd.get("active_plan"):
        rec["active_plan"] = bd["active_plan"]
    if bd.get("project"):
        project = (
            canonical_project_id(
                label=bd["project"], projects=projects
            )
            if projects is not None
            else str(bd["project"])
        )
        if project:
            rec["project"] = project
            rec["identity"] = f"{project}-{rec.get('role', '')}-{rec.get('name', '')}"
    if bd.get("workstream"):
        rec["workstream"] = bd["workstream"]
        try:
            from omnicompany.core.workstreams import get_workstream

            workstream = get_workstream(str(bd["workstream"]))
            if workstream:
                rec["workstream_name"] = workstream.get("name")
        except Exception:  # noqa: BLE001
            pass
    if bd.get("task_id"):
        rec["task_id"] = bd["task_id"]
    if bd.get("topic"):  # 会话自述主题盖过 digest 推测的 title
        rec["title"] = bd["topic"]
        rec["topic"] = bd["topic"]
    rec["authoritative"] = any(
        bd.get(field) for field in ("project", "workstream", "active_plan", "task_id", "topic")
    )
    if not str(rec.get("project") or "").strip():
        # companion 判出项目但 make_default=false 时, 只写成 scope_segments 里的候选段,
        # 顶层 project 一直是空 —— 以前这里只读顶层, 于是答案就躺在账本里、列表上却一直显示
        # "未绑定"(2026-09-20 实测 1995 条绑定里有 161 条属于这种)。这里只做展示回退, 不回写
        # 账本, authoritative 仍为假: 它是推测, 和 digest 推测同级。
        inferred = _segment_project(bd, projects)
        if inferred:
            rec["project"] = inferred
            rec["identity"] = f"{inferred}-{rec.get('role', '')}-{rec.get('name', '')}"
    return rec


def _segment_project(
    bd: dict[str, Any], projects: list[dict[str, Any]] | None
) -> str:
    """scope_segments 里唯一的那个项目; 有分歧就不猜(和 agent_digest 的口径一致)。"""
    by_state: dict[str, set[str]] = {}
    for segment in bd.get("scope_segments") or []:
        if not isinstance(segment, dict):
            continue
        label = str(segment.get("project") or "").strip()
        if not label:
            continue
        project = (
            canonical_project_id(label=label, projects=projects)
            if projects is not None
            else label
        )
        if not project:
            continue
        by_state.setdefault(str(segment.get("binding_state") or ""), set()).add(project)
    # 已确认的段压过候选段; 同一档里有两个不同项目就是分歧, 不选。
    for state in ("confirmed", "observed", "candidate", ""):
        projects_in_state = by_state.get(state)
        if projects_in_state and len(projects_in_state) == 1:
            return next(iter(projects_in_state))
        if projects_in_state:
            return ""
    return ""


def _binding_for(
    rec: dict[str, Any], binds: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    key = rec.get("key") or f"{rec.get('provider')}:{rec.get('session_id')}"
    session_id = str(rec.get("session_id") or "").strip()
    provider = str(rec.get("provider") or "").strip()
    candidates = [
        f"pty:{rec.get('pty_id')}" if rec.get("pty_id") else "",
        f"session:{session_id}" if session_id else "",
        f"{provider}:{session_id}" if provider and session_id else "",
        str(key),
    ]
    for candidate_key in candidates:
        if not candidate_key or candidate_key not in binds:
            continue
        binding = binds[candidate_key]
        if candidate_key.startswith("pty:") and session_id:
            native_ids = {
                str(binding.get(field) or "").strip()
                for field in ("session_id", "claude_session_id")
                if str(binding.get(field) or "").strip()
            }
            # A reused PTY must not hand the previous native conversation's
            # project or plan to the current resident.
            if native_ids and session_id not in native_ids:
                continue
        return binding
    return {}


def build_residents(
    now: float | None = None,
    *,
    limit: int = 80,
    rust_fetch: Callable[[], list[dict[str, Any]] | None] | None = None,
    python_fallback: Callable[[], list[dict[str, Any]]] | None = None,
    get_digest: Callable[[str, str], dict[str, Any] | None] | None = None,
    load_controller: Callable[[], dict[str, Any] | None] | None = None,
    get_attention: Callable[[], dict[str, dict[str, Any]]] | None = None,
    projects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """统一常驻列表。依赖可注入(便于测试)。

    limit: 截断到前 limit 条(数据源已按 在跑→最近 排序),防 UI 被历史会话淹没。
    get_attention: {session_id → 举手请求};命中者标 attention + headline,且即便越过 cap
                   也保留(agent 主动请审阅的对话绝不能被历史会话淹掉)。
    """
    now = now if now is not None else time.time()

    if projects is None:
        try:
            from omnicompany.core.projects_registry import list_projects

            projects = list_projects()
        except Exception:  # noqa: BLE001 — registry outage must not blank the view
            projects = None

    if rust_fetch is None:
        rust_fetch = rust_scanner_client.residents
    if get_digest is None:
        from .agent_digest import load_digests

        # One residents refresh may enrich dozens of sessions. Loading and
        # parsing the complete digest JSON once per resident caused repeated
        # GIL-heavy work every few seconds.
        digest_store = load_digests()

        def get_digest(provider: str, session_id: str) -> dict[str, Any] | None:
            digest = digest_store.get(f"{provider}:{session_id}")
            if not digest:
                return None
            return {
                key: digest.get(key, "")
                for key in ("project", "plan", "title", "last_step")
            }
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
                # Request paths must never inherit the scanner's multi-second
                # Python fallback. Serve the durable snapshot immediately and
                # refresh it under the background resource budget.
                agent_registry.schedule_rebuild()
                return agent_registry.list_records(
                    running_only=True,
                    rebuild_first=False,
                )

            source = "python-cache"

        residents = python_fallback() or []

    try:
        attn = get_attention() or {}
    except Exception:  # noqa: BLE001 — 举手取数失败不该挡住整列表
        attn = {}

    try:
        from omnicompany.packages.services._core.identity import bindings_by_session_key

        binds = bindings_by_session_key() or {}
    except Exception:  # noqa: BLE001 — 台账读失败不该挡住整列表
        binds = {}

    enriched: list[dict[str, Any]] = []
    for rec in residents:
        rec = dict(rec)
        try:
            rec = _enrich_digest(rec, get_digest)
            rec = _canonicalize_project(rec, projects)
        except Exception:  # noqa: BLE001 — 摘要回填失败不该挡住整列表
            pass
        try:
            rec = _enrich_binding(rec, binds, projects)
        except Exception:  # noqa: BLE001 — 权威绑定叠加失败不该挡住整列表
            pass
        try:
            binding = _binding_for(rec, binds)
            rec["lineage"] = infer_session_lineage(rec, binding=binding)
            session_origin = binding.get("session_origin")
            if session_origin:
                rec["origin"] = session_origin
        except Exception:  # noqa: BLE001 — 来源投影失败不该挡住整列表
            pass
        a = attn.get(rec.get("session_id", "")) or attn.get(rec.get("key", ""))
        if a:
            rec["attention"] = True
            rec["attention_headline"] = a.get("headline", "")
            rec["attention_ts"] = a.get("ts")
        enriched.append(rec)

    attach_parent_titles(enriched)

    total = len(enriched)
    if limit and limit > 0 and total > limit:
        head = enriched[:limit]
        # cap 之外仍保留任何举手请审阅的对话(去重)
        kept = {id(r) for r in head}
        extra_attn = [r for r in enriched[limit:] if r.get("attention") and id(r) not in kept]
        enriched = head + extra_attn
    controller_managed = load_controller() if load_controller else _load_controller_managed()
    out = [_controller_resident(controller_managed)] + enriched
    return {
        "source": source,
        "stale_while_revalidate": source == "python-cache",
        "count": len(out),
        "total": total + 1,
        "now": now,
        "residents": out,
    }


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
