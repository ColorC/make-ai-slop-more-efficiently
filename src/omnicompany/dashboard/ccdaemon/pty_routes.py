# [OMNI] origin=claude-code ts=2026-05-01 type=infra
# [OMNI] material_id="material:dashboard.cc_wrapper.http_router.endpoints.py"
"""HTTP + WebSocket API for the Claude Code wrapper.

Endpoints
---------
GET  /api/cc/sessions                — list live sessions
POST /api/cc/sessions                — spawn a new claude session
DELETE /api/cc/sessions/{sid}        — kill a session
WS   /api/cc/sessions/{sid}/ws       — bidirectional terminal IO

WebSocket protocol (newline-free JSON envelopes both directions)
----------------------------------------------------------------
client → server:
  {"type":"input", "data":"<utf-8 keystrokes>"}
  {"type":"resize", "cols":120, "rows":32}
server → client:
  {"type":"snapshot", "chunks":["...", ...]}   # sent once, on attach
  {"type":"output", "data":"<utf-8 chunk>"}
  {"type":"exit", "reason":"..."}              # session closed
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from omnicompany.dashboard.session_workdir import default_session_cwd

from .pty import (
    DEFAULT_COLS, DEFAULT_ROWS, get_manager, resolve_claude_cmd,
    list_recoverable_sessions, _resume_command,
)
from . import codex_installer as ci
from . import installer as si

logger = logging.getLogger(__name__)

cc_router = APIRouter(prefix="/cc", tags=["cc-wrapper"])
SNAPSHOT_WS_CHARS = 256 * 1024
SESSION_LIST_CACHE_TTL_S = 2.0
_session_list_cache: dict[
    tuple[bool, str | None],
    tuple[float, dict[str, Any]],
] = {}
_session_list_tasks: dict[
    tuple[bool, str | None],
    asyncio.Task[dict[str, Any]],
] = {}


class CreateSessionBody(BaseModel):
    cmd: list[str] | None = Field(default=None, description="Override command; defaults to claude CLI on PATH.")
    cwd: str | None = Field(
        default=None,
        description="Working directory; defaults to E:\\WindowsWorkspace. Other directories are preserved in session metadata.",
    )
    cols: int = DEFAULT_COLS
    rows: int = DEFAULT_ROWS
    safe_mode: bool = Field(
        default=False,
        description="If true, do not add the remote CLI's default permission-bypass flag. "
                    "Default false: Claude bypasses permissions, Codex bypasses approvals "
                    "and sandboxing, Kimi uses yolo, and OpenCode auto-approves.",
    )


class ResumeProviderSessionBody(BaseModel):
    provider: str = Field(..., min_length=1, max_length=40)
    provider_session_id: str = Field(..., min_length=1, max_length=200)
    cwd: str | None = Field(default=None, max_length=4000)
    cols: int = DEFAULT_COLS
    rows: int = DEFAULT_ROWS


@cc_router.get("/health")
async def cc_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "claude_cli_found": resolve_claude_cmd() is not None,
        "session_count": len(get_manager().list_meta()),
        "default_cwd": default_session_cwd(),
    }


# ── settings install / status (single source of truth — dashboard button calls this,
#    CLI `omni cc install` calls the same `settings_installer` module) ──

def _integration_installer(provider: str):
    if provider == "codex":
        return ci
    if provider in ("claude", "claude_code", "claude-code"):
        return si
    raise HTTPException(400, "provider must be 'claude_code' or 'codex'")


@cc_router.get("/install/status")
async def install_status(scope: str = "project", provider: str = "claude_code") -> dict[str, Any]:
    if scope not in ("project", "user"):
        raise HTTPException(400, "scope must be 'project' or 'user'")
    report = _integration_installer(provider).status(scope=scope)  # type: ignore[arg-type]
    report["provider"] = "codex" if provider == "codex" else "claude_code"
    return report


@cc_router.post("/install")
async def install(scope: str = "project", provider: str = "claude_code") -> dict[str, Any]:
    if scope not in ("project", "user"):
        raise HTTPException(400, "scope must be 'project' or 'user'")
    rep = _integration_installer(provider).install(scope=scope)  # type: ignore[arg-type]
    payload = {
        "provider": "codex" if provider == "codex" else "claude_code",
        "settings_path": rep.settings_path,
        "backup": rep.backup,
        "hooks_added_or_updated": rep.hooks_added,
        "hooks_unchanged": rep.hooks_unchanged,
        "note": rep.note,
        "equivalent_cli": f"omni cc install --provider {provider} --scope {scope}",
    }
    if hasattr(rep, "mcp_added"):
        payload["mcp_added_or_updated"] = rep.mcp_added
    if hasattr(rep, "requires_trust"):
        payload["requires_trust"] = rep.requires_trust
    return payload


@cc_router.delete("/install")
async def uninstall(scope: str = "project", provider: str = "claude_code") -> dict[str, Any]:
    if scope not in ("project", "user"):
        raise HTTPException(400, "scope must be 'project' or 'user'")
    rep = _integration_installer(provider).uninstall(scope=scope)  # type: ignore[arg-type]
    rep["provider"] = "codex" if provider == "codex" else "claude_code"
    rep["equivalent_cli"] = f"omni cc uninstall --provider {provider} --scope {scope}"
    return rep


def _list_sessions_sync(
    include_recoverable: bool = True,
    active_plan: str | None = None,
) -> dict[str, Any]:
    """Return live in-process sessions plus (optionally) recoverable ones whose
    PTY died (e.g. across a backend restart) but whose claude conversation log
    still exists on disk and can be revived via `--resume`.

    `active_plan` 反查过滤 (CC-PLAN-SESSION-CONTEXT 段四-1): 只列绑定指定 plan_id
    的 session, 用于 plan 详情页"关联 cc_sessions"块.
    """
    alive = get_manager().list_meta()
    if active_plan:
        alive = [a for a in alive if a.get("active_plan") == active_plan]
    out: dict[str, Any] = {"items": alive, "alive_count": len(alive)}
    if include_recoverable:
        # exclude any recoverable id that's also in `alive` (just in case the user
        # already resumed it this run)
        alive_ids = {a["id"] for a in alive}
        rec = [r for r in list_recoverable_sessions() if r["id"] not in alive_ids]
        if active_plan:
            rec = [r for r in rec if r.get("active_plan") == active_plan]
        out["recoverable"] = rec
        out["recoverable_count"] = len(rec)
    return out


def _invalidate_session_list_cache() -> None:
    _session_list_cache.clear()


@cc_router.get("/sessions")
async def list_sessions(
    include_recoverable: bool = True,
    active_plan: str | None = None,
) -> dict[str, Any]:
    """Coalesce disk-heavy registry scans without blocking terminal IO."""
    key = (include_recoverable, active_plan)
    now = time.monotonic()
    cached = _session_list_cache.get(key)
    if cached is not None and now - cached[0] <= SESSION_LIST_CACHE_TTL_S:
        return cached[1]

    task = _session_list_tasks.get(key)
    if task is None:
        task = asyncio.create_task(
            asyncio.to_thread(
                _list_sessions_sync,
                include_recoverable,
                active_plan,
            ),
            name=f"cc-session-list-{int(include_recoverable)}",
        )
        _session_list_tasks[key] = task
    try:
        payload = await task
    finally:
        if _session_list_tasks.get(key) is task:
            _session_list_tasks.pop(key, None)
    _session_list_cache[key] = (time.monotonic(), payload)
    return payload


@cc_router.get("/tab-states")
async def tab_states() -> dict[str, Any]:
    """前端页签活跃徽章的高频运行态快照(2s 轮询, 多浏览器并存)。

    只取纯内存投影: PTY 侧走 PtyManager.tab_states() —— 不读数 MB 的
    cc_sessions.json、不做 host 发现(那些在 /sessions、get()、WS attach
    等低频路径同步); chat 侧只取内存 list_meta(), 不走带消息预览与
    搜索打分的分页列表。单次请求零磁盘 IO, 状态最多滞后一个轮询周期。
    """
    from .chat import get_chat_manager  # 惰性导入: chat 依赖 pty, 避免模块期成环

    chat_items = [
        {
            "id": meta["id"],
            # Multiagent/页签运行态只认 daemon 仍持有的真实 runtime；持久化历史
            # 会话虽然逻辑上可续接，也不能伪装成正在运行。
            "alive": bool(meta.get("runtime_alive", meta.get("alive"))),
            "runtime_alive": bool(meta.get("runtime_alive", meta.get("alive"))),
            "running": bool(meta.get("running")),
            "status": (
                (meta.get("status") or "alive")
                if meta.get("runtime_alive", meta.get("alive"))
                else "ended"
            ),
            "provider": meta.get("provider"),
            "cwd": meta.get("cwd"),
            "name": meta.get("name"),
            "provider_session_id": meta.get("provider_session_id"),
            "started_at": meta.get("started_at"),
            "last_message": meta.get("last_message"),
            "message_count": meta.get("message_count"),
            "token_usage": meta.get("token_usage"),
        }
        for meta in get_chat_manager().list_meta()
    ]
    return {"pty": get_manager().tab_states(), "chat": chat_items}


@cc_router.post("/sessions")
async def create_session(body: CreateSessionBody) -> dict[str, Any]:
    try:
        sess = await get_manager().create(
            cmd=body.cmd,
            cwd=body.cwd,
            cols=body.cols,
            rows=body.rows,
            safe_mode=body.safe_mode,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _invalidate_session_list_cache()
    return sess.to_meta()


@cc_router.post("/sessions/resume-provider")
async def resume_provider_session(body: ResumeProviderSessionBody) -> dict[str, Any]:
    """Open a provider-native conversation in a real PTY-backed CLI."""
    provider = body.provider.strip().lower().replace("-", "_")
    if provider in {"claude", "claude_code"}:
        provider = "claude_code"
    if provider not in {"claude_code", "codex", "kimi", "opencode", "codebuddy"}:
        raise HTTPException(
            status_code=400,
            detail=f"provider {body.provider!r} does not support CLI resume",
        )

    manager = get_manager()
    for meta in manager.list_meta():
        native_id = meta.get("provider_session_id") or (
            meta.get("claude_session_id") if provider == "claude_code" else None
        )
        if (
            meta.get("alive")
            and str(meta.get("provider") or "") == provider
            and str(native_id or "") == body.provider_session_id
        ):
            return {**meta, "resumed_existing": True}

    try:
        cmd = _resume_command(provider, body.provider_session_id)
        sess = await manager.create(
            cmd=cmd,
            cwd=body.cwd,
            cols=body.cols,
            rows=body.rows,
            safe_mode=False,
            resume_claude_session_id=(
                body.provider_session_id if provider == "claude_code" else None
            ),
            resume_provider_session_id=body.provider_session_id,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    _invalidate_session_list_cache()
    return {
        **sess.to_meta(),
        "resumed_provider_session_id": body.provider_session_id,
    }


@cc_router.post("/sessions/{recoverable_id}/resume")
async def resume_session(recoverable_id: str) -> dict[str, Any]:
    """Spawn a fresh PTY pointing at the same claude conversation (`claude --resume`).
    Returns the NEW session's metadata (it has a new pty id; the old one stays in
    cc_sessions.json marked terminated)."""
    try:
        sess = await get_manager().resume(recoverable_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    out = sess.to_meta()
    out["resumed_from"] = recoverable_id
    _invalidate_session_list_cache()
    return out


@cc_router.delete("/sessions/{sid}")
async def kill_session(sid: str) -> dict[str, Any]:
    ok = await get_manager().kill(sid)
    if not ok:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    _invalidate_session_list_cache()
    return {"ok": True, "id": sid}


# ── S16: session context aggregator ─────────────────────────────────────────
#
# GET /sessions/{sid}/context  →  structured asset summary (per user round 21 b)
# Three sections served as one bundle:
#   1. context: active plan / cwd / claude_session_id / user-added (work_type, standards)
#   2. modified_files: files this session edited (Edit/Write/MultiEdit/NotebookEdit)
#   3. added_workers / added_materials: new files matching worker.py / materials.py
#                                       / formats.py patterns under packages/
# Plus bash_writes: best-effort extraction of `> path`, `tee path` etc. from Bash calls.

import sqlite3
import re
from pathlib import Path
from .pty import _read_meta_store


def _events_db() -> Path:
    """Path to the unified events.db (where hooks write trace events)."""
    state_dir = os.environ.get("OMNI_CC_DAEMON_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "ide_events.db"
    try:
        from omnicompany.core.config import resolve_unified_db_path
        return resolve_unified_db_path("ide_events.db")
    except Exception:
        pass
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root() / "data" / "ide_events.db"


def _query_session_events(sid: str) -> list[dict]:
    """Pull all events for cc_<sid> trace from events.db."""
    db = _events_db()
    if not db.is_file():
        return []
    try:
        conn = sqlite3.connect(str(db), timeout=2.0)
        rows = conn.execute(
            "SELECT data FROM events WHERE trace_id=? ORDER BY timestamp", (sid,),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    out: list[dict] = []
    for (raw,) in rows:
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


_BASH_REDIRECT = re.compile(r"(?:>\s*|>>\s*|tee\s+(?:-a\s+)?)([^\s'\"|&;]+)")
# Worker / Team / Material file patterns under `packages/`.
_WORKER_PAT  = re.compile(r"packages[/\\][^/\\]+[/\\]workers[/\\][^/\\]+\.py$")
_TEAM_PAT    = re.compile(r"packages[/\\][^/\\]+[/\\]team[^/\\]*\.py$", re.IGNORECASE)
_MATERIAL_PAT = re.compile(r"packages[/\\][^/\\]+[/\\](?:materials|formats)\.py$")


def _aggregate_session_io(events: list[dict]) -> dict:
    """Walk events, classify into modified_files / bash_writes / added_workers / added_materials."""
    modified: dict[str, dict] = {}  # path -> {path, count, last_ts, last_tool}
    bash_writes: list[dict] = []
    added_workers: list[str] = []
    added_materials: list[str] = []
    seen_assets: set[str] = set()

    def _bump(path: str, ts: str, tool: str):
        if not path:
            return
        if path not in modified:
            modified[path] = {"path": path, "count": 0, "last_ts": ts, "last_tool": tool}
        modified[path]["count"] += 1
        if ts > modified[path]["last_ts"]:
            modified[path]["last_ts"] = ts
            modified[path]["last_tool"] = tool

    for ev in events:
        if ev.get("event_type") != "agent.tool.call":
            continue
        p = ev.get("payload") or {}
        tool = p.get("tool", "")
        args = p.get("args") or {}
        ts = ev.get("timestamp") or ""

        if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            fp = args.get("file_path") or args.get("notebook_path") or ""
            if fp:
                _bump(fp, ts, tool)
                if fp not in seen_assets:
                    seen_assets.add(fp)
                    if _WORKER_PAT.search(fp): added_workers.append(fp)
                    elif _TEAM_PAT.search(fp): added_workers.append(fp)  # treat team as same bucket
                    elif _MATERIAL_PAT.search(fp): added_materials.append(fp)

        elif tool == "Bash":
            cmd = args.get("command") or ""
            for m in _BASH_REDIRECT.finditer(cmd):
                target = m.group(1)
                bash_writes.append({
                    "path": target,
                    "snippet": cmd[:120],
                    "ts": ts,
                })
                # also count as modified
                _bump(target, ts, "Bash")

    # Convert modified dict to sorted list (most-recent first)
    mod_list = sorted(modified.values(), key=lambda x: x["last_ts"], reverse=True)
    return {
        "modified_files": mod_list,
        "bash_writes": bash_writes,
        "added_workers": added_workers,
        "added_materials": added_materials,
    }


def _get_session_context_sync(sid: str) -> dict[str, Any]:
    store = _read_meta_store()
    entry = store.get(sid) or {}
    if not entry:
        try:
            from .chat import get_chat_manager
            chat_sess = get_chat_manager()._sessions.get(sid)
            if chat_sess is not None:
                entry = chat_sess.to_meta()
        except Exception:
            entry = {}
    events = _query_session_events(sid)
    io = _aggregate_session_io(events)
    session_binding: dict[str, Any] = {}
    try:
        from omnicompany.packages.services._core.identity import all_session_bindings

        provider = str(entry.get("provider") or "")
        provider_session_id = str(
            entry.get("provider_session_id")
            or entry.get("claude_session_id")
            or ""
        )
        for binding in all_session_bindings().values():
            binding_trace_id = str(binding.get("trace_id") or "")
            trace_matches_provider_session = bool(
                provider_session_id
                and binding_trace_id in {
                    provider_session_id,
                    f"codex_{provider_session_id}",
                    f"cc_{provider_session_id}",
                }
            )
            if binding.get("pty_id") == sid or (
                provider_session_id
                and (
                    trace_matches_provider_session
                    or (
                        binding.get("provider") == provider
                        and (
                            binding.get("session_id") == provider_session_id
                            or binding.get("claude_session_id") == provider_session_id
                        )
                    )
                )
            ):
                session_binding = dict(binding)
                break
    except Exception:
        session_binding = {}
    active_plan = entry.get("active_plan") or session_binding.get("active_plan")
    # plan_meta = plan.md frontmatter, project_meta = 所属 project 的 project.md frontmatter
    # 两者都是真信息源 (明文 yaml), AI IDE / Claude Code 共编共看
    plan_meta: dict[str, Any] = {}
    project_meta: dict[str, Any] = {}
    if active_plan:
        try:
            from omnicompany.dashboard.controlplane.plans import parse_plan_frontmatter, parse_project_meta, _plans_root
            plan_meta = parse_plan_frontmatter(_plans_root() / active_plan / "plan.md")
            project_meta = parse_project_meta(active_plan)
        except Exception:
            plan_meta = {}
            project_meta = {}
    try:
        from .context_progressive import resolve_progressive_context
        resolved_context = resolve_progressive_context(
            active_plan=active_plan,
            cwd=entry.get("cwd"),
            plan_meta=plan_meta,
        )
    except Exception as exc:
        resolved_context = {
            "plan_id": active_plan,
            "contexts": [],
            "total": 0,
            "missing": [],
            "missing_total": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    # PTY alive 由 PtyManager 决定；chat session 由 cc_sessions.json 的 ended_at 判定。
    alive = sid in get_manager()._sessions if hasattr(get_manager(), "_sessions") else False
    if entry.get("kind") == "chat" and not entry.get("ended_at"):
        alive = True
    return {
        "session_id": sid,
        "kind": "cc",
        "context": {
            "active_plan": active_plan,
            "project": session_binding.get("project") or plan_meta.get("project"),
            "plan_meta": plan_meta,
            "project_meta": project_meta,
            "cwd": entry.get("cwd"),
            "provider": entry.get("provider") or entry.get("kind") or "claude_code",
            "provider_session_id": (
                entry.get("provider_session_id")
                or entry.get("claude_session_id")
            ),
            "trace_id": session_binding.get("trace_id") or sid,
            "claude_session_id": entry.get("claude_session_id"),
            "started_at": entry.get("started_at"),
            "ended_at": entry.get("ended_at"),
            "agent_state": "alive" if alive else "recoverable" if entry.get("ended_at") else "ended",
            "user_context": entry.get("user_context") or {},  # legacy fallback (deprecate)
            "resolved_context": resolved_context,
        },
        **io,
        "event_count": len(events),
    }


@cc_router.get("/sessions/{sid}/context")
async def get_session_context(sid: str) -> dict[str, Any]:
    # Registry parsing, SQLite reads, plan resolution and filesystem identity
    # lookup are control-plane work. Keep them off the terminal WebSocket loop.
    return await asyncio.to_thread(_get_session_context_sync, sid)


# (REMOVED 2026-05-02 round 4) PATCH /sessions/{sid}/context with UserContextPatch
# 旧设计: work_type / standards / notes 写到 cc_sessions.json.user_context (私有 JSON 字段)
# 新设计: 这些值是 plan.md frontmatter (work_type / standards / project / exit_criteria)
#         用户编辑 plan.md 即改值, 跟现有目录有机结合, 明文人和 agent 都读同一份
# 旧 cc_sessions.json.user_context 字段不删 (avoid breaking existing entries),
# context endpoint 仍在 fallback 读 (legacy compat) — 但不再写入新值
# CLI 等价 mirror 也撤回, 不做 `omni cc context set`


# ── plan binding switcher (CC-PLAN-SESSION-CONTEXT 段三-1) ───────────────────
#
# PATCH /sessions/{sid}/active_plan body={plan_id|null}
#   - 显式切 cc_session 绑定的 plan, 跟 omni plan use <id> CLI 同源逻辑
#   - 走 pty_service.update_meta_field 写 cc_sessions.json (持久, 跨重启)
#   - 同步更新 in-memory PtySession.active_plan (alive session)
#   - plan_id=null 解绑
#   - 返回新 state + alive 是否立即生效说明
#
# 段二审议点 (待用户拍板): alive 进程切 plan 后是否立即生效?
#   现状: 只更新元数据 + in-memory, 已运行的 claude code 进程当 turn 注入 context 已
#   定型, 下次 SessionStart 才看到新 plan. 重注入留 TODO 待用户拍板 a/b/c.

from .pty import update_meta_field as _pty_update_meta


class ActivePlanPatch(BaseModel):
    plan_id: str | None = Field(
        default=None,
        description="Plan id relative to docs/plans/ (e.g. `_infra/dashboard/[2026-05-03]CC-PLAN-SESSION-CONTEXT`). null 解绑.",
    )


def _validate_plan_id(plan_id: str) -> Path:
    """Resolve plan_id to absolute dir, refusing path traversal / non-existent.

    Raises HTTPException on bad input.
    """
    from omnicompany.core.config import omni_workspace_root
    plans_root = omni_workspace_root() / "docs" / "plans"
    if "../" in plan_id or "..\\" in plan_id or plan_id.startswith("/") or plan_id.startswith("\\"):
        raise HTTPException(400, f"invalid plan_id (path traversal): {plan_id!r}")
    candidate = (plans_root / plan_id).resolve()
    try:
        candidate.relative_to(plans_root.resolve())
    except ValueError:
        raise HTTPException(400, f"plan_id escapes plans root: {plan_id!r}")
    if not candidate.is_dir():
        raise HTTPException(404, f"plan dir not found: {plan_id!r}")
    if not (candidate / "plan.md").is_file():
        raise HTTPException(404, f"plan.md missing in {plan_id!r}")
    return candidate


@cc_router.patch("/sessions/{sid}/active_plan")
async def patch_active_plan(sid: str, body: ActivePlanPatch) -> dict[str, Any]:
    """Switch (or unbind) the plan bound to this cc_session.

    Persists to `cc_sessions.json` and updates in-memory `PtySession.active_plan`
    if the session is alive. **Does not** force-reinject context into a running
    claude — that requires a SessionStart (i.e. /clear or /restart). See timing
    note in the response.
    """
    store = _read_meta_store()
    chat_sess = None
    chat_mgr = None
    try:
        from .chat import get_chat_manager
        chat_mgr = get_chat_manager()
        chat_sess = chat_mgr._sessions.get(sid)
    except Exception as exc:
        logger.warning("patch_active_plan: chat manager lookup failed: %s", exc)
    if sid not in store and sid not in get_manager()._sessions and chat_sess is None:
        raise HTTPException(404, f"session not found: {sid}")

    plan_id = body.plan_id
    if plan_id:
        _validate_plan_id(plan_id)  # raises HTTPException on bad

    # 1. persistent metadata + change marker (UserPromptSubmit hook reads this
    #    to re-inject plan_meta on the next user turn — alive 进程 b 方案)
    import time as _time
    now_ts = _time.time()
    if plan_id is None:
        # Explicit unbind is one of the few intentional null writes.
        _pty_update_meta(
            sid,
            allow_none=True,
            active_plan=None,
            active_plan_changed_ts=now_ts,
        )
    else:
        _pty_update_meta(sid, active_plan=plan_id, active_plan_changed_ts=now_ts)

    # 2. in-memory PtySession (if alive)
    sess = get_manager().get(sid)
    alive = False
    if sess is not None:
        sess.active_plan = plan_id
        alive = True

    # 2b. in-memory CcChatSession (if it's a chat session — SessionContextPanel
    #     always routes through ccApi regardless of session kind, so we must also
    #     update the chat manager's in-memory object for _maybe_inject_plan to
    #     pick up the change on the next user turn)
    if not alive:
        logger.info("patch_active_plan 2b: sid=%s chat_mgr_sessions=%s found=%s",
                    sid, list(chat_mgr._sessions.keys())[:5] if chat_mgr is not None else [],
                    chat_sess is not None)
        if chat_sess is not None:
            chat_sess.active_plan = plan_id
            alive = chat_sess.ended_at is None
            try:
                chat_mgr.schedule_context_event(
                    chat_sess,
                    trigger="plan_switch",
                    switched=True,
                )
            except AttributeError:
                pass

    # 3. CLI's cc_session_active.json — if this sid matches the currently active
    # trace_id (the one the user is "in"), mirror the change so omni plan current
    # picks it up next.
    try:
        from omnicompany.packages.services._core.identity import (
            current_session_meta,
            record_active_session,
        )
        meta = current_session_meta()
        if meta.get("pty_id") == sid or meta.get("trace_id") == sid:
            record_active_session(
                trace_id=meta.get("trace_id") or sid,
                claude_session_id=meta.get("claude_session_id"),
                pty_id=sid,
                active_plan=plan_id,
                cwd=meta.get("cwd"),
                source="web_patch_active_plan",
            )
    except Exception as e:
        logger.warning("active_plan PATCH: identity mirror failed: %s", e)

    # 4. return new state + timing note
    #    b 方案: alive 进程 UserPromptSubmit hook 会在下条用户输入触发时重注入 plan_meta
    return {
        "session_id": sid,
        "active_plan": plan_id,
        "alive": alive,
        "effective": "next_user_turn" if alive else "immediate",
        "note": (
            "Already-running claude code: the new plan_meta will be auto-injected "
            "via UserPromptSubmit hook on the NEXT user turn (no /clear needed). "
            "The current turn's already-cached system prompt is unchanged."
            if alive
            else "Session is not alive; new bindings apply when it's resumed."
        ),
    }


@cc_router.websocket("/sessions/{sid}/ws")
async def session_ws(ws: WebSocket, sid: str) -> None:
    await ws.accept()
    mgr = get_manager()
    try:
        sess, queue, snapshot = await mgr.attach(sid)
    except KeyError:
        await ws.send_text(json.dumps({"type": "exit", "reason": "session not found"}))
        await ws.close()
        return

    # Stream replay in bounded frames. A long-running session can hold tens of
    # megabytes and must not create one giant JSON/WebSocket frame or enqueue
    # tens of thousands of xterm write callbacks at once.
    await ws.send_text(json.dumps({
        "type": "snapshot_begin",
        "meta": {
            "provider": sess.provider,
            "cols": sess.cols,
            "rows": sess.rows,
            "buffered_bytes": sess.ring_bytes,
            "replay_truncated": sess.replay_truncated,
        },
    }))
    batch: list[str] = []
    batch_chars = 0
    for chunk in snapshot:
        if batch and batch_chars + len(chunk) > SNAPSHOT_WS_CHARS:
            await ws.send_text(json.dumps({"type": "snapshot_chunk", "chunks": batch}))
            batch = []
            batch_chars = 0
        batch.append(chunk)
        batch_chars += len(chunk)
    if batch:
        await ws.send_text(json.dumps({"type": "snapshot_chunk", "chunks": batch}))
    await ws.send_text(json.dumps({"type": "snapshot_end"}))

    import asyncio

    async def pump_out() -> None:
        # CC-PLAN-SESSION-CONTEXT 段五 (2026-05-05): drain queue 把连续 chunks 拼一
        # 帧再发. 当 reader 突 burst 时, 这里少发 N-1 个 WS 帧 + 少 N-1 次 JSON 编码.
        # asyncio.QueueEmpty 当 sentinel — 没东西就发当前 buf, 等下个 await get().
        try:
            pending: Any = None
            while True:
                item = pending if pending is not None else await queue.get()
                pending = None
                if isinstance(item, dict):
                    if item.get("type") == "proxy_reconnect":
                        await ws.close(code=1012, reason="PTY proxy reconnect")
                        return
                    if item.get("type") == "exit":
                        await ws.send_text(json.dumps(item))
                        await ws.close(code=1000, reason="PTY exited")
                        return
                    continue
                chunk = str(item)
                # try to drain extra without blocking
                while True:
                    try:
                        next_item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if isinstance(next_item, dict):
                        pending = next_item
                        break
                    chunk += str(next_item)
                await ws.send_text(json.dumps({"type": "output", "data": chunk}))
        except (WebSocketDisconnect, RuntimeError):
            pass

    out_task = asyncio.create_task(pump_out(), name=f"cc-ws-out-{sid}")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = msg.get("type")
            if t == "input":
                data = msg.get("data", "")
                if isinstance(data, str) and data:
                    try:
                        mgr.claim_control(sess, queue)
                        if "cols" in msg or "rows" in msg:
                            await mgr.resize(
                                sid,
                                msg.get("cols", DEFAULT_COLS),
                                msg.get("rows", DEFAULT_ROWS),
                                source=queue,
                            )
                        await mgr.write(sid, data, source=queue)
                    except KeyError:
                        break
            elif t == "resize":
                try:
                    await mgr.resize(
                        sid,
                        msg.get("cols", DEFAULT_COLS),
                        msg.get("rows", DEFAULT_ROWS),
                        source=queue,
                    )
                except KeyError:
                    break
            elif t == "redraw":
                try:
                    await mgr.redraw(
                        sid,
                        msg.get("cols", DEFAULT_COLS),
                        msg.get("rows", DEFAULT_ROWS),
                        source=queue,
                    )
                except KeyError:
                    break
            # Unknown types are silently ignored.
    except WebSocketDisconnect:
        pass
    finally:
        out_task.cancel()
        mgr.detach(sess, queue)
        try:
            await ws.close()
        except RuntimeError:
            pass
