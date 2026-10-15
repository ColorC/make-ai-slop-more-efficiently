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
  {"type":"attach", "last_seq": 1234}          # optional preamble, first message
  {"type":"input", "data":"<utf-8 keystrokes>"[, "cols":120, "rows":32]}  # grid = take over sizing
  {"type":"resize", "cols":120, "rows":32}
  {"type":"visibility", "visible": true|false}
server → client:
  {"type":"snapshot", "chunks":["...", ...]}   # sent once, on attach
  {"type":"snapshot_begin", "meta":{...}}      # framed full-ring replay
  {"type":"snapshot_chunk", "chunks":["...", ...]}
  {"type":"snapshot_end"}
  {"type":"resume_ok", "from_seq": 1235}       # last_seq still in the ring
  {"type":"output", "data":"<utf-8 chunk>", "seq": 1240}
  {"type":"geometry", "cols":120, "rows":32}   # PTY grid; non-controllers render at it
  {"type":"exit", "reason":"..."}              # session closed

PERF-OBSERVATORY P4.1 (续传/背压/可见性):
  A client that remembers the highest ``seq`` it has received may open a new
  WS and, as its very first message, send ``{"type":"attach","last_seq":N}``.
  The server waits up to ``ATTACH_PREAMBLE_TIMEOUT_S`` for this optional
  preamble (query params can't carry it — ``wsAutoReconnect`` reuses one
  fixed URL across reconnects) before falling back to the pre-P4.1 attach
  path unconditionally, so an old client that never sends it sees identical
  behavior, just a bounded extra wait. If the ring still covers ``last_seq``
  the server replies ``resume_ok`` + the incremental ``output`` frames since
  then (no ``snapshot_*`` at all — the client must not reset its terminal).
  Otherwise it falls back to the ordinary framed ``snapshot_begin/chunk/end``
  replay. A subscriber whose queue overflows is paused (not disconnected)
  and, once drained, gets one inline ``snapshot_begin/chunk/end`` resync
  instead of a forced reconnect; see ``pty.py``'s ``PtyManager._publish_output``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from omnicompany.dashboard.session_workdir import default_session_cwd, resolve_session_cwd

from .provider_identity import (
    AGENT_WORKER_PROVIDER_IDS,
    CLAUDE_FAMILY_PROVIDER_IDS,
    PTY_AGENT_PROVIDER_IDS,
    VISIBLE_AGENT_WORKER_PROVIDER_IDS,
    canonical_cli_provider_id,
    second_account_env,
)

from .latency_monitor import get_latency_monitor
from .pty import (
    DEFAULT_COLS, DEFAULT_ROWS, SESSION_ORIGIN_AGENT, SESSION_ORIGIN_HUMAN,
    get_manager, resolve_claude_cmd,
    list_recoverable_sessions, list_remembered_human_sessions,
    restore_recoverable_session,
    _resume_command, update_meta_field,
)
from . import codex_installer as ci
from . import installer as si
from .assistant import (
    ASSISTANT_BOOTSTRAP_TASK,
    ASSISTANT_TOPIC,
    assistant_dir,
    refresh_history_index,
)
# 助理专属路由与额度监视拆在 assistant_routes.py; _monitor_billing_issue 仍从这里
# 导出, 重接路径(pty.py)与测试都按 pty_routes._monitor_billing_issue 取。
from .assistant_routes import _monitor_billing_issue, assistant_router  # noqa: F401

logger = logging.getLogger(__name__)

cc_router = APIRouter(prefix="/cc", tags=["cc-wrapper"])
cc_router.include_router(assistant_router)
_TAB_STATE_CACHE_TTL_S = 0.35
_tab_state_cache: tuple[float, dict[str, Any]] | None = None
_tab_state_build_lock = asyncio.Lock()
SNAPSHOT_WS_CHARS = 256 * 1024
LIVE_WS_BATCH_CHARS = 64 * 1024
LIVE_WS_BATCH_ITEMS = 64
# PTY-RELOAD-CRAWL (2026-08-18): while a session is inside its post-nudge
# burst window (sess.output_burst_until, set by PtyManager.redraw/resize), the
# fanout waits out short gaps between the TUI's line-sized re-print chunks so
# the whole repaint lands in one (or few) WS frames instead of ~200 — the
# browser then applies it atomically instead of painting line by line.
REDRAW_BURST_QUIET_GAP_S = 0.12
REDRAW_BURST_MAX_CHARS = 256 * 1024
REDRAW_BURST_MAX_ITEMS = 2048
# PERF-OBSERVATORY P4.1: how long the WS endpoint waits for an optional
# {"type":"attach","last_seq":N} preamble before falling back to the
# unconditional pre-P4.1 attach (full snapshot). A client that sends it
# resolves this near-instantly; a client that never does (old build) pays
# this bounded wait once per connect and then behaves exactly as before.
ATTACH_PREAMBLE_TIMEOUT_S = float(
    os.environ.get("OMNI_PTY_ATTACH_PREAMBLE_TIMEOUT_S") or 0.2
)
SESSION_LIST_CACHE_TTL_S = max(
    2.0, float(os.environ.get("OMNI_SESSION_LIST_CACHE_TTL_S") or 5.0)
)
RECOVERABLE_SESSION_LIST_CACHE_TTL_S = max(
    SESSION_LIST_CACHE_TTL_S,
    float(os.environ.get("OMNI_RECOVERABLE_SESSION_LIST_CACHE_TTL_S") or 20.0),
)
_session_list_cache: dict[
    tuple[bool, str | None],
    tuple[float, dict[str, Any]],
] = {}
_session_list_tasks: dict[
    tuple[bool, str | None],
    asyncio.Task[dict[str, Any]],
] = {}
_session_list_generation = 0


def resolve_session_origin(
    request: Request | None,
    declared: str | None = None,
) -> str:
    """Decide whether a person or a program asked for this session.

    A caller may declare itself (``origin`` in the body) and that wins — an
    honest program saying "agent" is exactly what we want. Otherwise we look for
    the fetch metadata every browser sends and no CLI/HTTP client does. Anything
    we cannot positively identify as a browser is treated as a program, because
    the failure modes are not symmetric: mislabelling a program as human leaks a
    session forever, while mislabelling a human as a program only means the
    session is reclaimed after it has been detached and silent for the whole
    agent TTL.
    """
    if declared:
        normalized = declared.strip().lower()
        if normalized in {SESSION_ORIGIN_HUMAN, SESSION_ORIGIN_AGENT}:
            return normalized
    if request is None:
        return SESSION_ORIGIN_AGENT
    headers = request.headers
    # Fetch Metadata is set by the browser itself and cannot be spoofed by page
    # script, so a same-origin dashboard click always carries it.
    if headers.get("sec-fetch-site") or headers.get("sec-fetch-mode"):
        return SESSION_ORIGIN_HUMAN
    user_agent = headers.get("user-agent") or ""
    if user_agent.startswith("Mozilla/") and (
        headers.get("origin") or headers.get("referer")
    ):
        return SESSION_ORIGIN_HUMAN
    return SESSION_ORIGIN_AGENT


class CreateSessionBody(BaseModel):
    cmd: list[str] | None = Field(default=None, description="Override command; defaults to claude CLI on PATH.")
    cwd: str | None = Field(
        default=None,
        description="Working directory; defaults to C:/workspace/. Other directories are preserved in session metadata.",
    )
    cols: int = DEFAULT_COLS
    rows: int = DEFAULT_ROWS
    extra_env: dict[str, str] | None = Field(
        default=None,
        description="Extra environment variables merged into the child process "
                    "env (pty-cli contract §4; e.g. LARK_DELEGATE_BINDING).",
    )
    origin: str | None = Field(
        default=None,
        description="'human' (a person opened this) or 'agent' (a program did). "
                    "Omit and it is inferred from the request; anything not "
                    "identifiable as a browser counts as 'agent' and "
                    "auto-reclaimed once idle.",
    )
    safe_mode: bool = Field(
        default=False,
        description="If true, do not add the remote CLI's default permission-bypass flag. "
                    "Default false: Claude bypasses permissions, Codex bypasses approvals "
                    "and sandboxing, Kimi uses yolo, OpenCode auto-approves, and Qoder "
                    "skips permissions.",
    )


class ResumeProviderSessionBody(BaseModel):
    provider: str = Field(..., min_length=1, max_length=40)
    provider_session_id: str = Field(..., min_length=1, max_length=200)
    cwd: str | None = Field(default=None, max_length=4000)
    cols: int = DEFAULT_COLS
    rows: int = DEFAULT_ROWS


class SessionInputBody(BaseModel):
    data: str = Field(..., min_length=1, max_length=12000)
    submit: bool = True


class RestoreSessionBody(BaseModel):
    provider_session_id: str | None = Field(default=None, max_length=300)


class SessionOriginBody(BaseModel):
    origin: str = Field(..., pattern="^(human|agent)$")


class CreateAgentWorkerBody(BaseModel):
    """Product-level Agent Worker request; execution is always a real CLI PTY."""

    provider: str = Field(..., min_length=1, max_length=40)
    model: str | None = Field(default=None, max_length=200)
    effort: str | None = Field(default=None, max_length=20)
    task: str | None = Field(default=None, max_length=12000)
    cwd: str | None = Field(default=None, max_length=4000)
    project: str | None = Field(default=None, max_length=200)
    workstream: str | None = Field(default=None, max_length=300)
    active_plan: str | None = Field(default=None, max_length=500)
    topic: str | None = Field(default=None, max_length=500)
    parent_agent_id: str | None = Field(default=None, max_length=300)
    origin: str | None = Field(
        default=None,
        description="'human' or 'agent'; omit to infer from the request. "
                    "Unidentifiable callers are treated as 'agent'.",
    )
    resume_provider_session_id: str | None = Field(default=None, max_length=300)
    fork_from_provider_session_id: str | None = Field(default=None, max_length=300)
    visibility: str = Field(default="visible", pattern="^(visible|hidden|background)$")
    from_controller: bool = False
    safe_mode: bool = False
    pure_mode: bool = False


_agent_worker_monitors: set[asyncio.Task[Any]] = set()


# Agent Worker 目录拆为 exec 分片(R4 行数棘轮; 同 turn_confirm.py 惯例)。
_PTY_PARTS_DIR = Path(__file__).with_name("pty_parts")
_part_path = _PTY_PARTS_DIR / "agent_worker_catalog.py"
exec(compile(_part_path.read_text(encoding="utf-8"), str(_part_path), "exec"))
del _PTY_PARTS_DIR, _part_path



def _emit_agent_worker_event(
    sess: Any,
    event_type: str,
    payload: dict[str, Any],
    *,
    from_controller: bool,
) -> None:
    """Bridge PTY worker lifecycle into existing controller subscribers.

    The bridge is compatibility-only: it does not create a structured-chat
    session.  The facade exposes the minimal fields consumed by subscribers.
    """
    from omnicompany.core.caller_identity import CALLER_SUBAGENT
    from .chat import get_chat_manager
    from .hooks._shared import emit_event

    facade = SimpleNamespace(
        id=sess.id,
        provider=sess.provider,
        claude_session_id=sess.provider_session_id,
        cwd=sess.cwd,
        active_plan=payload.get("active_plan"),
        caller_identity=CALLER_SUBAGENT if from_controller else None,
        taken_over=False,
    )
    enriched = {
        "session_id": sess.id,
        "provider": sess.provider,
        "provider_session_id": sess.provider_session_id,
        "cwd": sess.cwd,
        **payload,
    }
    emit_event(
        sess.id,
        event_type,
        enriched,
        source=f"agent-worker:{sess.provider or 'unknown'}",
        tags=["agent_worker", "runtime:cli", str(sess.provider or "unknown")],
    )
    manager = get_chat_manager()
    for callback in list(manager._event_subscribers):  # noqa: SLF001 - existing subscriber bridge
        try:
            callback(facade, event_type, enriched, ["agent_worker", "runtime:cli"])
        except Exception:  # noqa: BLE001
            logger.exception("agent worker subscriber %r failed for %s", callback, event_type)
    if event_type in {"subagent.completed", "subagent.blocked"}:
        _wake_bound_parent(sess, event_type, enriched)


def _wake_bound_parent(sess: Any, event_type: str, payload: dict[str, Any]) -> None:
    """Deliver one durable terminal wake for an external PTY worker.

    ControllerWaker intentionally ignores user-created external workers.  The
    agent control plane still owns their explicit ``parent_agent_id`` binding,
    so terminal events must use that binding directly instead of pretending the
    worker is a structured controller subagent.
    """
    try:
        from omnicompany.packages.services._core.identity import get_session_binding
        from omnicompany.core.quiet_coordination import QuietCoordinationStore

        binding = get_session_binding(sess.id)
        parent = str(binding.get("parent_agent_id") or "").strip()
        task_id = str(binding.get("task_id") or binding.get("topic") or binding.get("active_plan") or "").strip()
        if not parent or not task_id:
            return
        run_id = f"{sess.id}:{event_type}:{payload.get('provider_session_id') or ''}"
        store = QuietCoordinationStore()
        result = store.record_child_terminal(
            child_session_id=str(sess.id),
            parent_session_id=parent,
            parent_task_id=task_id,
            run_id=run_id,
            outcome="blocked" if event_type == "subagent.blocked" else "completed",
            payload={"event_type": event_type, "active_plan": payload.get("active_plan"), "preview": payload.get("last_assistant_preview")},
        )
        message = result.get("message") if isinstance(result, dict) else None
        message_id = str((message or {}).get("message_id") or "")
        if not message_id or not store.claim_wake(message_id):
            return
        from omnicompany.packages.services._core.agent.control_plane import send_agent_input

        delivery = send_agent_input(
            parent,
            f"[OMNI CHILD TERMINAL WAKE] 子会话 {sess.id} 已{('阻断' if event_type == 'subagent.blocked' else '完成')}；请读取回执 {message_id} 并继续当前计划。",
            submit=True,
        )
        if isinstance(delivery, dict) and delivery.get("ok") is False:
            store.release_wake(message_id)
            raise RuntimeError(f"parent wake endpoint rejected delivery: {delivery}")
    except Exception:  # noqa: BLE001 - event emission must never kill the PTY monitor
        logger.exception("external agent parent wake failed for %s", getattr(sess, "id", "unknown"))


# ── 往 provider TUI 里投一条 prompt ────────────────────────────────────────────
#
# 2026-08-10 实测(codex 与 claude_code 各复现): 把「正文 + \r」一次写进 PTY 有两种
# 失败, 都表现为"会话活着但一直没人干活":
#   1. PTY 刚创建、TUI 还没就绪 → 整段被吞, 终端只剩 banner;
#   2. 字进了输入框但从未提交 —— TUI 的粘贴突发检测把这一次写当成粘贴, 换行成了
#      字面换行、结尾回车也被吃掉, 整段 prompt 停在 `›` 提示符里。
# 所以: 正文与回车必须分两次写, 中间留出突发窗口; 投完还要按语义信号确认真开了一轮
# (终端字节数不可信 —— 有 256KB 重放上限, 还会因 TUI 重绘乱长), 没开就逐档补救。
_PROMPT_ENTER_GAP_S = 1.2
_SEED_BOOT_WAIT_S = 4.0
_SEED_STAGE_WAIT_S = 12.0
_SEED_POLL_S = 1.0

# 投递确认(_turn_working/_wait_turn_working/_confirm_submitted_prompt)拆为
# exec 分片, 命名空间与拆分前等价(R4 行数棘轮; 同 pty.py 的 pty_parts 惯例)。
_PTY_PARTS_DIR = Path(__file__).with_name("pty_parts")
_part_path = _PTY_PARTS_DIR / "turn_confirm.py"
exec(compile(_part_path.read_text(encoding="utf-8"), str(_part_path), "exec"))
del _PTY_PARTS_DIR, _part_path


async def _submit_prompt(sid: str, text: str) -> None:
    """把一条 prompt 打进 TUI 并提交(正文与回车分两次写)。"""
    manager = get_manager()
    body = text.rstrip("\r\n")
    if body:
        await manager.write(sid, body)
        await asyncio.sleep(_PROMPT_ENTER_GAP_S)
    await manager.write(sid, "\r")


def _turn_started(sess: Any) -> bool:
    """这条会话真开了一轮吗 —— 只认 provider 自己的信号。"""
    if getattr(sess, "provider_session_id", None):
        return True
    try:
        return bool(sess.provider_runtime.working(now=time.time()))
    except Exception:  # noqa: BLE001
        return False


async def _wait_turn_started(sid: str, seconds: float) -> str:
    """("started"|"idle"|"gone")。"""
    manager = get_manager()
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        sess = manager.get(sid)
        if sess is None or sess.closed:
            return "gone"
        if _turn_started(sess):
            return "started"
        if time.monotonic() >= deadline:
            return "idle"
        await asyncio.sleep(_SEED_POLL_S)


async def _seed_agent_worker(
    sid: str,
    task: str,
    *,
    active_plan: str | None,
    from_controller: bool,
) -> str:
    """把开场任务投进新起的 Agent Worker, 并确认它真的开跑; 没开就逐档补救。

    返回 landed / submitted / reseeded / unconfirmed / gone。
    """
    manager = get_manager()
    await asyncio.sleep(_SEED_BOOT_WAIT_S)   # 给 provider TUI 一点启动时间
    await _submit_prompt(sid, task)
    status = await _wait_turn_started(sid, _SEED_STAGE_WAIT_S)
    if status != "idle":
        return "landed" if status == "started" else status

    await manager.write(sid, "\r")           # 字可能在输入框里没提交
    status = await _wait_turn_started(sid, _SEED_STAGE_WAIT_S)
    if status != "idle":
        return "submitted" if status == "started" else status

    await _submit_prompt(sid, task)          # 或者开场整段被吞了
    status = await _wait_turn_started(sid, _SEED_STAGE_WAIT_S)
    if status == "started":
        return "reseeded"
    if status == "gone":
        return "gone"

    sess = manager.get(sid)
    if sess is not None:
        # 静默失败最贵: 上游以为交出去了, 其实那条会话停在提示符前。喊出来。
        _emit_agent_worker_event(
            sess,
            "subagent.blocked",
            {
                "subagent_id": sid,
                "active_plan": active_plan,
                "reason": "opening prompt never started a turn (still sitting at the prompt)",
            },
            from_controller=from_controller,
        )
    return "unconfirmed"


async def _monitor_agent_worker_turn(
    sid: str,
    *,
    active_plan: str | None,
    from_controller: bool,
    seed_task: asyncio.Task[str] | None = None,
) -> None:
    """Observe one submitted PTY turn and emit completion without busy polling."""
    manager = get_manager()
    if seed_task is not None:
        try:
            seed_status = await seed_task
        except Exception as exc:  # noqa: BLE001 - seeder emits its own blocker where possible
            logger.exception("agent worker seed failed for %s", sid)
            seed_status = "unconfirmed"
        if seed_status not in {"landed", "submitted", "reseeded"}:
            return
    deadline = time.monotonic() + 12 * 60 * 60
    saw_working = False
    idle_samples = 0
    while time.monotonic() < deadline:
        await asyncio.sleep(1.0)
        sess = manager.get(sid)
        if sess is None or sess.closed:
            if sess is not None:
                _emit_agent_worker_event(
                    sess,
                    "subagent.blocked",
                    {"subagent_id": sid, "active_plan": active_plan, "reason": "PTY exited before a stable idle completion"},
                    from_controller=from_controller,
                )
            return
        working = bool(sess.provider_runtime.working(now=time.time()))
        if working:
            saw_working = True
            idle_samples = 0
            continue
        if not saw_working:
            # Provider startup may take a while; has_user_turn proves input was
            # accepted, but do not call it complete until busy was observed.
            continue
        idle_samples += 1
        if idle_samples < 2:
            continue
        preview = ""
        try:
            preview = "".join(str(chunk) for chunk in list(sess.ring)[-30:])[-1200:]
        except Exception:  # noqa: BLE001
            preview = ""
        _emit_agent_worker_event(
            sess,
            "subagent.completed",
            {
                "subagent_id": sid,
                "active_plan": active_plan,
                "runtime_kind": "cli",
                "worker_kind": "external_agent_worker",
                "last_assistant_preview": preview,
            },
            from_controller=from_controller,
        )
        return


def _track_agent_worker_monitor(task: asyncio.Task[Any]) -> None:
    _agent_worker_monitors.add(task)
    task.add_done_callback(_agent_worker_monitors.discard)


def _with_shared_agent_instructions(task: str) -> str:
    """Point every external CLI runtime at the same non-Skill instruction source."""
    root_agents = Path(default_session_cwd()) / "AGENTS.md"
    return (
        "OmniCompany 共享非 Skill 规范真源是 "
        f"{root_agents.as_posix()}。开始前读取并遵守它以及当前 cwd 适用的 "
        "AGENTS.md/CLAUDE.md；不要复述规范，直接完成下面任务。"
        "其中规格明确、可机械验收、体量摊得平交接的机械部分，"
        "按「集群派发」节默认派给执行档模型（omni task dispatch / omni worker run），"
        "不必亲手做。\n\n"
        f"任务：\n{task}"
    )


@cc_router.get("/health")
async def cc_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "claude_cli_found": resolve_claude_cmd() is not None,
        "session_count": get_manager().in_memory_session_count(),
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
    from .web_host import get_web_manager

    # web 会话只列活着的: 已结束的展示面会话不可续接, 不进会话列表
    # (打开着的页签仍能从 /cc/tab-states 看到 ended 状态)。
    from .litework_bridge import get_litework_bridge

    candidates = (
        get_manager().list_meta()
        + get_web_manager().list_meta(include_ended=False)
        + get_litework_bridge().list_meta()
    )
    # A runtime can be visible through more than one adapter during a refresh
    # (notably a resumed PTY and its compatibility alias).  Keep the canonical
    # session id once; aliases remain available through /cc/tab-states.
    alive: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in candidates:
        session_id = str(item.get("id") or item.get("session_id") or "").strip()
        if not session_id or session_id in seen_ids:
            continue
        seen_ids.add(session_id)
        alive.append(item)
    if active_plan:
        alive = [a for a in alive if a.get("active_plan") == active_plan]
    out: dict[str, Any] = {
        "items": alive,
        # ``alive`` is the runtime/connection catalog count kept for
        # compatibility (Omni PTYs + LiTeWork bridge).  ``process_alive`` is
        # intentionally separate and may be unknown for an external bridge.
        "alive_count": sum(1 for item in alive if item.get("alive") is True),
        "process_alive_count": sum(
            1 for item in alive if item.get("process_alive") is True
        ),
        "working_count": sum(1 for item in alive if item.get("working") is True),
        "unknown_count": sum(
            1 for item in alive
            if str(item.get("state") or "") == "unknown"
            or item.get("working_known") is False
        ),
        "observed_at": time.time(),
        "source": "ccdaemon.session_catalog",
    }
    if include_recoverable:
        # exclude any recoverable id that's also in `alive` (just in case the user
        # already resumed it this run)
        alive_ids = {a["id"] for a in alive}
        rec = [r for r in list_recoverable_sessions() if r["id"] not in alive_ids]
        if active_plan:
            rec = [r for r in rec if r.get("active_plan") == active_plan]
        out["recoverable"] = rec
        out["recoverable_count"] = len(rec)
        recoverable_ids = {item["id"] for item in rec}
        remembered = [
            item
            for item in list_remembered_human_sessions()
            if item["id"] not in alive_ids and item["id"] not in recoverable_ids
        ]
        if active_plan:
            remembered = [
                item for item in remembered if item.get("active_plan") == active_plan
            ]
        out["remembered"] = remembered
        out["remembered_count"] = len(remembered)
    return out


def _invalidate_session_list_cache() -> None:
    global _tab_state_cache, _session_list_generation
    _session_list_generation += 1
    _session_list_cache.clear()
    # Existing scans may still finish, but must not repopulate the new
    # generation with a pre-create/pre-migration catalogue.
    _session_list_tasks.clear()
    _tab_state_cache = None


async def _build_tab_state_payload() -> dict[str, Any]:
    from .litework_bridge import get_litework_bridge
    from .web_host import get_web_manager

    manager = get_manager()
    litework = get_litework_bridge()
    litework.schedule_refresh()
    await manager.prepare_tab_states()
    return {
        "pty": manager.tab_states() + litework.tab_states(),
        "web": get_web_manager().tab_states(),
        "chat": [],
        "product_kind": "agent_worker",
        "runtime_kind": "cli",
        "legacy_chat_archived": True,
    }


@cc_router.get("/sessions")
async def list_sessions(
    include_recoverable: bool = True,
    active_plan: str | None = None,
) -> dict[str, Any]:
    """Coalesce disk-heavy registry scans without blocking terminal IO."""
    from .litework_bridge import get_litework_bridge

    # LiTeWork is optional.  Its public CLI is sampled off-loop and cached;
    # list construction below remains a synchronous in-memory merge.
    get_litework_bridge().schedule_refresh()
    generation = _session_list_generation
    key = (include_recoverable, active_plan)
    now = time.monotonic()
    cached = _session_list_cache.get(key)
    cache_ttl = (
        RECOVERABLE_SESSION_LIST_CACHE_TTL_S
        if include_recoverable
        else SESSION_LIST_CACHE_TTL_S
    )
    if cached is not None and now - cached[0] <= cache_ttl:
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
    if generation != _session_list_generation:
        return await list_sessions(include_recoverable, active_plan)
    _session_list_cache[key] = (time.monotonic(), payload)
    return payload


@cc_router.get("/tab-states")
async def tab_states(response: Response) -> dict[str, Any]:
    """CLI Agent Worker 页签的高频状态投影。"""
    response.headers["Cache-Control"] = "no-store"
    global _tab_state_cache
    now = time.monotonic()
    cached = _tab_state_cache
    if cached is not None and now - cached[0] <= _TAB_STATE_CACHE_TTL_S:
        return cached[1]
    async with _tab_state_build_lock:
        now = time.monotonic()
        cached = _tab_state_cache
        if cached is not None and now - cached[0] <= _TAB_STATE_CACHE_TTL_S:
            return cached[1]
        payload = await _build_tab_state_payload()
        _tab_state_cache = (time.monotonic(), payload)
        return payload


@cc_router.post("/sessions")
async def create_session(body: CreateSessionBody, request: Request) -> dict[str, Any]:
    try:
        sess = await get_manager().create(
            cmd=body.cmd,
            cwd=body.cwd,
            cols=body.cols,
            rows=body.rows,
            safe_mode=body.safe_mode,
            extra_env=body.extra_env,
            origin=resolve_session_origin(request, body.origin),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _invalidate_session_list_cache()
    _log_created_session(sess, request)
    return sess.to_meta()


def _log_created_session(sess: Any, request: Request | None) -> None:
    """Record who opened every session, so a leak always has a name attached.

    Deliberately logs the human-origin case too. Frontend code calling this API
    from the dashboard carries the browser's fetch metadata and is therefore
    classified ``human``, so a panel that spawns workers on its own would be
    invisible here if only agent-origin creations were recorded — and "who keeps
    starting empty Codex sessions" is exactly the question this has to answer.
    """
    headers = request.headers if request else {}
    client = getattr(getattr(request, "client", None), "host", None) or "?"
    logger.info(
        "pty created id=%s provider=%s origin=%s client=%s ua=%r referer=%r%s",
        sess.id,
        sess.provider,
        sess.origin,
        client,
        headers.get("user-agent"),
        headers.get("referer"),
        (
            f" — creator owns reclaim (DELETE /cc/sessions/{sess.id});"
            " auto-reclaimed once idle"
            if sess.origin == SESSION_ORIGIN_AGENT
            else ""
        ),
    )


@cc_router.get("/reclaimable")
async def reclaimable_sessions() -> dict[str, Any]:
    """Agent-created sessions their creator still owes a reclaim for.

    Cheap in-memory projection so a supervising agent or an operator can find
    leaked workers without scanning the registry.
    """
    now = time.time()
    manager = get_manager()
    items = []
    for meta in manager.tab_states():
        reclaim = meta.get("reclaim") or {}
        if not reclaim.get("required"):
            continue
        items.append({
            "id": meta["id"],
            "provider": meta.get("provider"),
            "cwd": meta.get("cwd"),
            "working": meta.get("working"),
            "started_at": meta.get("started_at"),
            "last_output_at": meta.get("last_output_at"),
            **reclaim,
        })
    items.sort(key=lambda item: item.get("deadline") or 0)
    return {
        "now": now,
        "count": len(items),
        "overdue_count": sum(1 for item in items if item.get("overdue")),
        "items": items,
    }


@cc_router.post("/agent-workers")
async def create_agent_worker(
    body: CreateAgentWorkerBody,
    request: Request,
) -> dict[str, Any]:
    """Create the canonical external Agent Worker as a real CLI/PTY runtime."""
    try:
        from omnicompany.packages.services._core.identity import validate_session_binding_scope

        body.project, body.workstream = validate_session_binding_scope(
            body.project,
            body.workstream,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    provider = canonical_cli_provider_id(body.provider)
    if provider not in AGENT_WORKER_PROVIDER_IDS:
        raise HTTPException(status_code=400, detail=f"provider {body.provider!r} is not a CLI Agent Worker runtime")
    from omnicompany.packages.services._core.agent.control_plane import build_provider_command, safe_default_model
    model = safe_default_model(provider, body.model)

    # dsh_web 是 web 展示面会话: 独立 Python supervisor 进程跑官方 dsh web UI,
    # 无 PTY、无 xterm 转播, 展示面是 iframe。模型在 Web UI 内自选, 不支持 task 播种。
    if provider == "dsh_web":
        if body.task:
            raise HTTPException(
                status_code=400,
                detail="dsh_web does not accept a seeded task; drive the Web UI directly",
            )
        from .web_host import get_web_manager

        try:
            cwd = resolve_session_cwd(body.cwd)
            cmd = build_provider_command(provider, model, effort=body.effort)
            sess = await get_web_manager().create(cmd=cmd, cwd=cwd, origin=resolve_session_origin(request, body.origin))
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _invalidate_session_list_cache()
        from omnicompany.packages.services._core.identity import update_session_binding

        update_session_binding(
            sess.id,
            session_id=sess.id,
            pty_id=sess.id,
            provider=provider,
            cwd=sess.cwd,
            project=body.project,
            workstream=body.workstream,
            active_plan=body.active_plan,
            topic=body.topic or body.task,
            runtime_kind="web",
            worker_kind="external_agent_worker",
            product_kind="agent_worker",
            binding_kind="project" if any((body.project, body.workstream, body.active_plan)) else "unbound",
            parent_agent_id=body.parent_agent_id,
            session_origin=sess.origin,
            visibility=body.visibility,
            source="agent_control_plane",
        )
        from .pty import PtyManager

        meta = PtyManager._with_session_bindings([sess.to_meta()])[0]
        return {
            **meta,
            "worker_id": sess.id,
            "pty_id": sess.id,
            "runtime_kind": "web",
            "worker_kind": "external_agent_worker",
            "product_kind": "agent_worker",
            "binding_kind": "project" if any((body.project, body.workstream, body.active_plan)) else "unbound",
            "model": model,
            "parent_agent_id": body.parent_agent_id,
            "visibility": body.visibility,
        }
    # codex_the_company 的 PTY 运行时仍是 codex CLI(协议/标记/续接都按 codex 走),
    # the_company 接入只存在于命令行的 -c 覆盖里。绑定与续接用运行时口径。
    runtime_provider = "codex" if provider == "codex_the_company" else provider

    # assistant = 机主的助理: 规矩在它的落脚目录 AGENTS.md 里, 这里只负责三件事 ——
    # 把 cwd 定到落脚目录、起会话前刷新历任会话索引、无显式任务时播一句"开工"
    # (不叠 _with_shared_agent_instructions 的「集群派发」建议, 助理自己有规矩)。
    # 不再强制 safe_mode: 助理要替机主开会话、改待办文件, 权限由 AGENTS.md 约束。
    is_assistant = body.topic == ASSISTANT_TOPIC
    safe_mode = body.safe_mode
    task = body.task
    session_cwd = body.cwd
    if body.pure_mode:
        from .pure_runtime import prepare_pure_runtime

        session_cwd = str(prepare_pure_runtime(task=task, provider=provider))
    if is_assistant:
        if not (task or "").strip():
            task = ASSISTANT_BOOTSTRAP_TASK
        if not (session_cwd or "").strip():
            session_cwd = str(assistant_dir())
        try:
            refresh_history_index(Path(session_cwd))
        except Exception as exc:  # noqa: BLE001 - 索引只是给后来者看的, 刷不出来不拦起会话
            logger.warning("assistant history index refresh failed: %s", exc)

    try:
        if body.resume_provider_session_id and body.fork_from_provider_session_id:
            raise ValueError("resume_provider_session_id and fork_from_provider_session_id are mutually exclusive")
        cmd = build_provider_command(
            provider,
            model,
            effort=body.effort,
            pure_mode=body.pure_mode,
        )
        resume_provider_session_id = body.resume_provider_session_id
        if resume_provider_session_id:
            # provider 原样传: codex_the_company 的续接要保留 the_company 接入覆盖。
            cmd = _resume_command(provider, resume_provider_session_id)
            if body.pure_mode:
                from omnicompany.packages.services._core.agent.control_plane import apply_pure_mode_flags

                cmd = apply_pure_mode_flags(cmd, provider)
        if body.fork_from_provider_session_id:
            if runtime_provider not in CLAUDE_FAMILY_PROVIDER_IDS:
                raise ValueError("CLI session fork is currently supported only by claude_code")
            executable = cmd[0]
            model_args = cmd[1:]
            cmd = [
                executable,
                "--resume",
                body.fork_from_provider_session_id,
                "--fork-session",
                *model_args,
            ]
        # 第二账号(codex_2 / claude_2)的身份只在环境里, argv 与 official 1 完全
        # 相同 —— 不在这里给, create() 里的分类就会把它判成主号并用主号认证跑
        # (2026-09-01 实测: claude_2 漏了这段, 面板起出来的 official 2 直接就是
        # 已登录的 official 1)。
        provider_env = second_account_env(provider) or None
        sess = await get_manager().create(
            cmd=cmd,
            cwd=session_cwd,
            # Pure workers already carry Claude's --safe-mode and must not be
            # augmented with the dashboard's permission-bypass flag.
            safe_mode=(safe_mode or body.pure_mode),
            extra_env=provider_env,
            resume_claude_session_id=(
                resume_provider_session_id
                if runtime_provider in CLAUDE_FAMILY_PROVIDER_IDS
                else None
            ),
            resume_provider_session_id=resume_provider_session_id,
            origin=resolve_session_origin(request, body.origin),
            visibility=body.visibility,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    sess.topic = body.topic or body.task
    if sess.topic:
        # 持久进台账: daemon 重启后 hosted 会话重接时也要能按 topic 找回(assistant 单会话复用)。
        update_meta_field(sess.id, topic=sess.topic)
    if is_assistant:
        get_manager().mark_billing_watched(sess.id)
        watcher = asyncio.create_task(
            _monitor_billing_issue(sess.id),
            name=f"assistant-billing-watch-{sess.id}",
        )
        _track_agent_worker_monitor(watcher)
    _log_created_session(sess, request)

    binding_kind = "project" if any((body.project, body.workstream, body.active_plan)) else "unbound"
    from omnicompany.packages.services._core.identity import update_session_binding

    update_session_binding(
        sess.id,
        session_id=sess.id,
        pty_id=sess.id,
        provider=runtime_provider,
        cwd=sess.cwd,
        project=body.project,
        workstream=body.workstream,
        active_plan=body.active_plan,
        topic=body.topic or body.task,
        runtime_kind="cli",
        worker_kind="external_agent_worker",
        product_kind="agent_worker",
        binding_kind=binding_kind,
        parent_agent_id=body.parent_agent_id,
        session_origin=sess.origin,
        fork_from_provider_session_id=body.fork_from_provider_session_id,
        visibility=body.visibility,
        model=model,
        source="agent_control_plane",
    )
    _emit_agent_worker_event(
        sess,
        "subagent.spawned",
        {
            "subagent_id": sess.id,
            "active_plan": body.active_plan,
            "runtime_kind": "cli",
            "worker_kind": "external_agent_worker",
            "binding_kind": binding_kind,
        },
        from_controller=body.from_controller,
    )
    if task:
        seed = task if (is_assistant or body.pure_mode) else _with_shared_agent_instructions(task)
        # 投递走 _seed_agent_worker: 分两次写 + 确认真开跑 + 逐档补救(见其上方注释)。
        # 放后台跑, 不让 HTTP 响应等这段确认。
        seeder = asyncio.create_task(
            _seed_agent_worker(
                sess.id,
                seed,
                active_plan=body.active_plan,
                from_controller=body.from_controller,
            ),
            name=f"agent-worker-seed-{sess.id}",
        )
        _track_agent_worker_monitor(seeder)
        monitor = asyncio.create_task(
            _monitor_agent_worker_turn(
                sess.id,
                active_plan=body.active_plan,
                from_controller=body.from_controller,
                seed_task=seeder,
            ),
            name=f"agent-worker-turn-{sess.id}",
        )
        _track_agent_worker_monitor(monitor)
    _invalidate_session_list_cache()
    from .pty import PtyManager

    meta = PtyManager._with_session_bindings([sess.to_meta()])[0]
    return {
        **meta,
        "worker_id": sess.id,
        "pty_id": sess.id,
        "runtime_kind": "cli",
        "worker_kind": "external_agent_worker",
        "product_kind": "agent_worker",
        "binding_kind": binding_kind,
        "model": model,
        "parent_agent_id": body.parent_agent_id,
        "visibility": body.visibility,
    }


@cc_router.post("/sessions/resume-provider")
async def resume_provider_session(body: ResumeProviderSessionBody) -> dict[str, Any]:
    """Open a provider-native conversation in a real PTY-backed CLI."""
    provider = body.provider.strip().lower().replace("-", "_")
    if provider in {"claude", "claude_code"}:
        provider = "claude_code"
    if provider not in {
        "claude_code",
        "codex",
        "codex_the_company",
        "kimi",
        "opencode",
        "codebuddy",
        "kiro",
        "qoder",
    }:
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


@cc_router.post("/sessions/{recoverable_id}/migrate-codex-account")
async def migrate_codex_account(recoverable_id: str) -> dict[str, Any]:
    """Move an exact retired-account PTY onto the primary Codex account."""
    try:
        sess = await get_manager().migrate_codex_account(recoverable_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _invalidate_session_list_cache()
    from .pty import _read_meta_store
    entry = _read_meta_store().get(sess.id, {})
    return {**sess.to_meta(), "resumed_from": recoverable_id, "source_kept_open": True,
            "artifacts": [
                {"path": entry.get("migrated_rollout_source"), "role": "original native conversation (preserved)"},
                {"path": entry.get("migrated_rollout_target"), "role": "primary account native conversation"},
            ]}


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
async def kill_session(sid: str, request: Request) -> dict[str, Any]:
    from .litework_bridge import get_litework_bridge, is_litework_session_id
    from .web_host import get_web_manager

    # 杀掉谁干的要可追责: 每次 kill 都记 ip/UA/Referer 进墓碑 + daemon log,
    # 会话"莫名其妙变少"时不用再猜(2026-08-15 用户报告会话持续消失)。
    requester = {
        "ip": getattr(getattr(request, "client", None), "host", None) or None,
        "user_agent": (request.headers.get("user-agent") or "")[:200] or None,
        "referer": (request.headers.get("referer") or "")[:300] or None,
    }
    if is_litework_session_id(sid):
        if not await get_litework_bridge().close(sid):
            raise HTTPException(status_code=404, detail=f"session not found: {sid}")
        _invalidate_session_list_cache()
        return {"ok": True, "id": sid, "runtime_host": "litework"}
    if get_web_manager().get(sid) is not None:
        if not await get_web_manager().kill(sid):
            raise HTTPException(status_code=404, detail=f"session not found: {sid}")
        _invalidate_session_list_cache()
        return {"ok": True, "id": sid}
    ok = await get_manager().kill(sid, requester=requester)
    if not ok:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    _invalidate_session_list_cache()
    return {"ok": True, "id": sid}


@cc_router.post("/sessions/{sid}/restore")
async def restore_session(
    sid: str,
    body: RestoreSessionBody | None = None,
) -> dict[str, Any]:
    """Repair an ended tombstone only when durable provider history exists."""
    try:
        restored = await asyncio.to_thread(
            restore_recoverable_session,
            sid,
            provider_session_id=(body.provider_session_id if body else None),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _invalidate_session_list_cache()
    return restored


@cc_router.patch("/sessions/{sid}/origin")
async def patch_session_origin(sid: str, body: SessionOriginBody) -> dict[str, Any]:
    """Change the lifecycle owner of a live PTY and persist the same owner."""
    sess = get_manager().get(sid)
    if sess is None:
        raise HTTPException(404, f"session not found: {sid}")
    sess.origin = body.origin
    update_meta_field(sid, origin=body.origin)
    _invalidate_session_list_cache()
    return {
        "id": sid,
        "origin": body.origin,
        "creation_origin": sess.origin,
        "reclaim": sess.to_meta().get("reclaim"),
    }


@cc_router.get("/sessions/{sid}/output")
async def get_session_output(sid: str, limit: int = 4000) -> dict[str, Any]:
    """Return the recent PTY ring as plain text so a probe can read /usage."""
    from .litework_bridge import get_litework_bridge, is_litework_session_id

    if is_litework_session_id(sid):
        try:
            text = await get_litework_bridge().output(sid, limit=limit)
        except (KeyError, RuntimeError) as exc:
            raise HTTPException(status_code=404, detail=f"pty session not found: {sid}") from exc
        return {"id": sid, "text": text, "bytes": len(text.encode("utf-8")), "runtime_host": "litework"}
    sess = get_manager().get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"pty session not found: {sid}")
    try:
        text = "".join(str(chunk) for chunk in list(sess.ring))
    except Exception:  # noqa: BLE001
        text = ""
    cap = max(200, min(int(limit or 4000), 20_000))
    return {"id": sid, "text": text[-cap:], "bytes": len(text)}


@cc_router.post("/sessions/{sid}/input")
async def send_session_input(sid: str, body: SessionInputBody) -> dict[str, Any]:
    """Submit one agent-to-agent prompt to an exact PTY or structured chat."""
    from .litework_bridge import get_litework_bridge, is_litework_session_id

    if is_litework_session_id(sid):
        try:
            await get_litework_bridge().send(sid, body.data, submit=body.submit)
        except (KeyError, RuntimeError) as exc:
            raise HTTPException(status_code=404, detail=f"pty session not found: {sid}") from exc
        return {
            "ok": True,
            "id": sid,
            "kind": "pty",
            "submission": "submitted" if body.submit else "raw",
            "runtime_host": "litework",
        }
    if sid.startswith("chat-"):
        from .chat import get_chat_manager

        manager = get_chat_manager()
        session = manager.get(sid)
        if session is None or session.ended_at is not None:
            raise HTTPException(status_code=404, detail=f"chat session not found: {sid}")
        asyncio.create_task(manager.submit_user_prompt(session, body.data))
        return {"ok": True, "id": sid, "kind": "chat"}
    manager = get_manager()
    if manager.get(sid) is None:
        raise HTTPException(status_code=404, detail=f"pty session not found: {sid}")
    if body.submit:
        # 正文与回车分两次写 —— 一次写会被 TUI 当成粘贴, 整段停在输入框里不提交。
        sess = manager.get(sid)
        await _submit_prompt(sid, body.data)
        submission = await _confirm_submitted_prompt(sid, sess)
        if submission == "unconfirmed":
            return {
                "ok": False,
                "id": sid,
                "kind": "pty",
                "submission": submission,
                "detail": "prompt did not start after an isolated Enter retry",
            }
    else:
        await manager.write(sid, body.data)
        submission = "raw"
    return {"ok": True, "id": sid, "kind": "pty", "submission": submission}


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
            "SELECT data FROM events WHERE trace_id=? AND event_type=? ORDER BY timestamp",
            (sid, "agent.tool.call"),
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
    provider = str(entry.get("provider") or entry.get("kind") or "claude_code")
    provider_session_id = str(
        entry.get("provider_session_id")
        or entry.get("claude_session_id")
        or sid
    )
    session_binding: dict[str, Any] = {}
    try:
        from omnicompany.packages.services._core.identity import binding_for_session

        # Session binding is conversation identity. Provider is transport
        # metadata only, so a resumed native session must resolve to the same
        # ledger row even when its provider/account spelling changes.
        session_binding = dict(
            binding_for_session(
                trace_id=sid,
                provider=provider,
                session_id=provider_session_id,
                pty_id=sid,
            )
            or {}
        )
    except Exception:
        session_binding = {}
    session_summary = ""
    try:
        from omnicompany.packages.services.boss_sight.services.agent_digest import get_session_summary

        provider_aliases = [provider]
        if provider == "claude":
            provider_aliases.append("claude_code")
        elif provider == "claude_code":
            provider_aliases.append("claude")
        for provider_alias in provider_aliases:
            session_summary = get_session_summary(provider_alias, provider_session_id)
            if session_summary:
                break
    except Exception:
        session_summary = ""
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
            "provider": provider,
            "provider_session_id": provider_session_id,
            "session_summary": session_summary,
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


# Context is assembled from the metadata registry, SQLite events, plan files,
# project files, and the progressive context resolver.  The dashboard has two
# viewers that refresh the same session (the companion every 15s and the
# context panel every 5s), and multiple browser surfaces can do that at once.
# Without coalescing, every refresh occupies a shared executor worker and a
# slow filesystem/SQLite read can queue PTY input and output behind it.  Keep a
# short cache for the read-mostly projection and share one in-flight task per
# session.  Ten seconds is below the UI's visible refresh horizon while
# removing duplicate work during a slow calculation.
_SESSION_CONTEXT_CACHE_TTL_S = max(
    2.0, float(os.environ.get("OMNI_CC_CONTEXT_CACHE_TTL_S") or 10.0)
)
_SESSION_CONTEXT_CACHE_MAX = max(
    32, int(os.environ.get("OMNI_CC_CONTEXT_CACHE_MAX") or 256)
)
# Keep slow registry/SQLite/context resolution out of asyncio's shared default
# executor.  PTY input writes, host reads, and context panels otherwise compete
# for the same workers; a burst of context refreshes can make every terminal
# appear frozen even while the event loop itself is healthy.
_SESSION_CONTEXT_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, int(os.environ.get("OMNI_CC_CONTEXT_EXECUTOR_WORKERS") or 2)),
    thread_name_prefix="cc-context",
)
_session_context_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_session_context_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}


def _trim_session_context_cache() -> None:
    overflow = len(_session_context_cache) - _SESSION_CONTEXT_CACHE_MAX
    if overflow <= 0:
        return
    for sid, _ in sorted(
        _session_context_cache.items(), key=lambda item: item[1][0]
    )[:overflow]:
        _session_context_cache.pop(sid, None)


def _invalidate_session_context_cache(sid: str | None = None) -> None:
    if sid is None:
        _session_context_cache.clear()
        return
    _session_context_cache.pop(sid, None)


async def _run_session_context_sync(sid: str) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _SESSION_CONTEXT_EXECUTOR,
        _get_session_context_sync,
        sid,
    )


async def _get_session_context_cached(sid: str) -> dict[str, Any]:
    now = time.monotonic()
    cached = _session_context_cache.get(sid)
    if cached is not None and now - cached[0] < _SESSION_CONTEXT_CACHE_TTL_S:
        return cached[1]

    task = _session_context_tasks.get(sid)
    if task is None or task.done():
        task = asyncio.create_task(
            _run_session_context_sync(sid),
            name=f"cc-session-context-{sid}",
        )
        _session_context_tasks[sid] = task

    try:
        # A disconnected HTTP caller must not cancel the shared computation for
        # the other viewers waiting on the same session.
        payload = await asyncio.shield(task)
        _session_context_cache[sid] = (time.monotonic(), payload)
        _trim_session_context_cache()
        return payload
    finally:
        if _session_context_tasks.get(sid) is task and task.done():
            _session_context_tasks.pop(sid, None)


@cc_router.get("/sessions/{sid}/context")
async def get_session_context(sid: str) -> dict[str, Any]:
    # Registry parsing, SQLite reads, plan resolution and filesystem identity
    # lookup are control-plane work. Keep them off the terminal WebSocket loop.
    return await _get_session_context_cached(sid)


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
    _invalidate_session_context_cache(sid)

    # Keep the authority ledger and the prompt-hook's tiny derived trigger in
    # sync for every target PTY, not only whichever session happens to own the
    # process-global active pointer.
    try:
        from omnicompany.core.plan_context_trigger import publish_plan_change
        from omnicompany.packages.services._core.identity import (
            bindings_by_session_key,
            update_session_binding,
        )

        existing_binding = bindings_by_session_key().get(f"pty:{sid}") or {}
        binding_trace_id = str(existing_binding.get("trace_id") or sid)
        update_session_binding(
            binding_trace_id,
            pty_id=sid,
            active_plan=plan_id or "",
            active_plan_changed_ts=now_ts,
            source="web_patch_active_plan",
        )
        publish_plan_change(sid, plan_id)
    except Exception as exc:  # noqa: BLE001 - cc_sessions remains compatible authority
        logger.warning("active_plan PATCH: trigger projection failed: %s", exc)

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


async def apply_output_visibility(
    *,
    provider: str,
    sid: str,
    cols: int,
    rows: int,
    queue: asyncio.Queue[Any],
    output_enabled: asyncio.Event,
    visible: bool,
    redraw: Any,
) -> dict[str, Any]:
    """Pause hidden agent TUIs and request a self-contained frame on reveal.

    Discarding incremental frames without a redraw leaves xterm on a torn
    alternate screen. The first visibility=true of a new socket is not a
    reveal: output starts enabled, so this is a no-op and cannot SIGWINCH
    OpenCode during startup.
    """
    if not visible:
        output_enabled.clear()
        return {"paused": True, "dropped": 0, "redraw": False}
    was_paused = not output_enabled.is_set()
    dropped = _discard_stale_tui_output(queue, provider) if was_paused else 0
    output_enabled.set()
    should_redraw = was_paused and provider in PTY_AGENT_PROVIDER_IDS
    if should_redraw:
        # ``ensure``: the frames just discarded may include another viewer's
        # repaint from moments ago; a floor-throttled nudge must be deferred,
        # not dropped, or this viewer stays on its pre-hide screen.
        await redraw(sid, cols, rows, source=queue, ensure=True)
    return {"paused": False, "dropped": dropped, "redraw": should_redraw}


def _discard_stale_tui_output(queue: asyncio.Queue[Any], provider: str) -> int:
    """Drop incremental TUI frames queued while the viewer was hidden.

    Full-screen agent TUIs paint by cursor movement. Replaying those frames
    after a gap tears the screen; the client follows reveal with a redraw
    so the still-running app can emit a self-contained frame. Control
    messages (exit / reconnect) stay. Shells keep every queued chunk —
    those bytes are new scrollback, not a torn alternate screen.
    """
    if provider not in PTY_AGENT_PROVIDER_IDS:
        return 0
    kept: list[Any] = []
    dropped = 0
    while True:
        try:
            item = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if isinstance(item, dict):
            kept.append(item)
        else:
            dropped += 1
    for item in kept:
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            break
    return dropped


async def _send_snapshot_frames(ws: WebSocket, chunks: list[str]) -> None:
    """Send a bounded ``snapshot_chunk`` sequence (shared by attach + resync).

    A long-running session can hold tens of megabytes and must not create one
    giant JSON/WebSocket frame or enqueue tens of thousands of xterm write
    callbacks at once.
    """
    batch: list[str] = []
    batch_chars = 0
    for chunk in chunks:
        if batch and batch_chars + len(chunk) > SNAPSHOT_WS_CHARS:
            await ws.send_text(json.dumps({"type": "snapshot_chunk", "chunks": batch}))
            batch = []
            batch_chars = 0
        batch.append(chunk)
        batch_chars += len(chunk)
    if batch:
        await ws.send_text(json.dumps({"type": "snapshot_chunk", "chunks": batch}))


async def _send_resume_output(ws: WebSocket, chunks: list[str], from_seq: int) -> None:
    """Send the incremental replay after a successful resume as ``output``
    frames (never a snapshot — the client must not reset its terminal).

    Batched the same way ``pump_out`` batches live chunks; each frame's
    ``seq`` is the seq of the last chunk folded into it.
    """
    batch: list[str] = []
    batch_chars = 0
    batch_seq = from_seq - 1
    for offset, chunk in enumerate(chunks):
        if batch and batch_chars + len(chunk) > LIVE_WS_BATCH_CHARS:
            await ws.send_text(json.dumps({"type": "output", "data": "".join(batch), "seq": batch_seq}))
            batch = []
            batch_chars = 0
        batch.append(chunk)
        batch_chars += len(chunk)
        batch_seq = from_seq + offset
    if batch:
        await ws.send_text(json.dumps({"type": "output", "data": "".join(batch), "seq": batch_seq}))


async def _dispatch_ws_message(
    msg: dict[str, Any],
    *,
    mgr: Any,
    sess: Any,
    sid: str,
    queue: asyncio.Queue[Any],
    output_enabled: asyncio.Event,
) -> bool:
    """Handle one already-parsed client message. Returns False to disconnect."""
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
                return False
    elif t == "resize":
        try:
            await mgr.resize(
                sid,
                msg.get("cols", DEFAULT_COLS),
                msg.get("rows", DEFAULT_ROWS),
                source=queue,
            )
        except KeyError:
            return False
    elif t == "redraw":
        try:
            await mgr.redraw(
                sid,
                msg.get("cols", DEFAULT_COLS),
                msg.get("rows", DEFAULT_ROWS),
                source=queue,
            )
        except KeyError:
            return False
    elif t == "visibility":
        visible = bool(msg.get("visible"))
        await apply_output_visibility(
            provider=sess.provider,
            sid=sid,
            cols=sess.cols,
            rows=sess.rows,
            queue=queue,
            output_enabled=output_enabled,
            visible=visible,
            redraw=mgr.redraw,
        )
        if visible:
            # Catch a paused/overflowed subscriber up immediately on reveal
            # instead of waiting for the next chunk's opportunistic check.
            await mgr.resync_if_overflowed(sess, queue)
    # "attach" (only meaningful as the pre-loop preamble) and unknown types
    # are silently ignored here.
    return True


@cc_router.websocket("/sessions/{sid}/ws")
async def session_ws(ws: WebSocket, sid: str) -> None:
    from .litework_bridge import get_litework_bridge, is_litework_session_id

    if is_litework_session_id(sid):
        await get_litework_bridge().websocket(ws, sid)
        return
    await ws.accept()
    mgr = get_manager()
    latency_monitor = get_latency_monitor()

    # PERF-OBSERVATORY P4.1: give a client up to ATTACH_PREAMBLE_TIMEOUT_S to
    # send {"type":"attach","last_seq":N} as its first message. wsAutoReconnect
    # reuses one fixed URL across reconnects, so last_seq can't ride the query
    # string — this is the "existing handshake" fit instead. An old client
    # that sends nothing (or sends its usual first message, e.g. visibility)
    # within the timeout falls back to the pre-P4.1 attach path unconditionally;
    # a non-attach message it did send is dispatched, not dropped, once attached.
    last_seq: int | None = None
    deferred_msg: dict[str, Any] | None = None
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=ATTACH_PREAMBLE_TIMEOUT_S)
        try:
            preamble = json.loads(raw)
        except json.JSONDecodeError:
            preamble = None
        if isinstance(preamble, dict) and preamble.get("type") == "attach":
            candidate = preamble.get("last_seq")
            if isinstance(candidate, int) and candidate >= 0:
                last_seq = candidate
        elif isinstance(preamble, dict):
            deferred_msg = preamble
    except asyncio.TimeoutError:
        pass
    except WebSocketDisconnect:
        return

    try:
        if last_seq is not None:
            sess, queue, snapshot, resume = await mgr.attach(sid, last_seq=last_seq)
        else:
            sess, queue, snapshot = await mgr.attach(sid)
            resume = {"resumed": False}
    except KeyError:
        await ws.send_text(json.dumps({"type": "exit", "reason": "session not found"}))
        await ws.close()
        return

    if resume.get("resumed"):
        from_seq = int(resume.get("from_seq") or 0)
        await ws.send_text(json.dumps({"type": "resume_ok", "from_seq": from_seq}))
        await _send_resume_output(ws, resume.get("chunks") or [], from_seq)
    else:
        base_seq = sess.next_seq - len(snapshot)
        await ws.send_text(json.dumps({
            "type": "snapshot_begin",
            "meta": {
                "provider": sess.provider,
                "cols": sess.cols,
                "rows": sess.rows,
                "buffered_bytes": sess.ring_bytes,
                "replay_truncated": sess.replay_truncated,
                "base_seq": base_seq,
                "last_seq": sess.next_seq - 1,
                "screen_model": bool(sess.host_screen_model),
            },
        }))
        await _send_snapshot_frames(ws, snapshot)
        await ws.send_text(json.dumps({"type": "snapshot_end"}))

    output_enabled = asyncio.Event()
    output_enabled.set()

    async def pump_out() -> None:
        # CC-PLAN-SESSION-CONTEXT 段五 (2026-05-05): drain queue 把连续 chunks 拼一
        # 帧再发. 当 reader 突 burst 时, 这里少发 N-1 个 WS 帧 + 少 N-1 次 JSON 编码.
        # asyncio.QueueEmpty 当 sentinel — 没东西就发当前 buf, 等下个 await get().
        try:
            pending: Any = None
            while True:
                await output_enabled.wait()
                item = pending if pending is not None else await queue.get()
                pending = None
                if isinstance(item, dict):
                    kind = item.get("type")
                    if kind == "proxy_reconnect":
                        await ws.close(code=1012, reason="PTY proxy reconnect")
                        return
                    if kind == "exit":
                        await ws.send_text(json.dumps(item))
                        await ws.close(code=1000, reason="PTY exited")
                        return
                    if kind == "geometry":
                        # Server-authoritative PTY grid for a viewer that does
                        # not control sizing; the browser adopts it.
                        await ws.send_text(json.dumps(item))
                        continue
                    if kind == "resync":
                        # PERF-OBSERVATORY P4.1 backpressure: this subscriber
                        # overflowed and paused; it just drained (or was
                        # explicitly revealed) and gets one inline ring
                        # resync — the same framing as the initial attach —
                        # instead of every chunk it missed while paused.
                        chunks = [str(c) for c in item.get("chunks") or []]
                        screen_frame = False
                        if item.get("prefer_screen") and sess.host_screen_model:
                            # Screen-model convergence: substitute one atomic
                            # host-rendered frame for the raw ring replay.
                            # Any failure keeps the ring fallback byte-exact.
                            frame = await mgr._request_host_screen(sess)
                            if frame:
                                chunks = [frame]
                                screen_frame = True
                        await ws.send_text(json.dumps({
                            "type": "snapshot_begin",
                            "meta": {
                                "provider": sess.provider,
                                "cols": sess.cols,
                                "rows": sess.rows,
                                "buffered_bytes": sum(
                                    len(c.encode("utf-8", errors="replace")) for c in chunks
                                ),
                                "replay_truncated": sess.replay_truncated,
                                "base_seq": item.get("base_seq"),
                                "last_seq": item.get("last_seq"),
                                "screen_model": bool(sess.host_screen_model),
                                "screen_frame": screen_frame,
                            },
                        }))
                        await _send_snapshot_frames(ws, chunks)
                        await ws.send_text(json.dumps({"type": "snapshot_end"}))
                    continue
                seq, first_chunk = item
                chunks = [str(first_chunk)]
                last_seq_in_batch = seq
                batch_chars = len(chunks[0])
                # PTY-RELOAD-CRAWL: inside the post-nudge burst window a TUI
                # re-print trickles in as line-sized chunks with 20-250ms gaps;
                # wait those gaps out so the repaint ships as one frame.
                in_burst = time.monotonic() < sess.output_burst_until
                max_items = REDRAW_BURST_MAX_ITEMS if in_burst else LIVE_WS_BATCH_ITEMS
                max_chars = REDRAW_BURST_MAX_CHARS if in_burst else LIVE_WS_BATCH_CHARS
                # Bound each drain so a continuously producing terminal cannot
                # monopolize the event loop or trigger quadratic string copies.
                while len(chunks) < max_items and batch_chars < max_chars:
                    try:
                        next_item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        if not in_burst or time.monotonic() >= sess.output_burst_until:
                            break
                        try:
                            next_item = await asyncio.wait_for(
                                queue.get(), timeout=REDRAW_BURST_QUIET_GAP_S
                            )
                        except asyncio.TimeoutError:
                            break
                    if isinstance(next_item, dict):
                        pending = next_item
                        break
                    next_seq, next_chunk = next_item
                    next_chunk = str(next_chunk)
                    chunks.append(next_chunk)
                    batch_chars += len(next_chunk)
                    last_seq_in_batch = next_seq
                chunk = "".join(chunks)
                started_ns = time.perf_counter_ns()
                try:
                    await ws.send_text(json.dumps({
                        "type": "output",
                        "data": chunk,
                        "seq": last_seq_in_batch,
                    }))
                finally:
                    latency_monitor.observe_websocket_send(
                        sid,
                        time.perf_counter_ns() - started_ns,
                    )
                await asyncio.sleep(0)
        except (WebSocketDisconnect, RuntimeError):
            pass

    out_task = asyncio.create_task(pump_out(), name=f"cc-ws-out-{sid}")
    try:
        if deferred_msg is not None:
            if not await _dispatch_ws_message(
                deferred_msg, mgr=mgr, sess=sess, sid=sid, queue=queue,
                output_enabled=output_enabled,
            ):
                raise WebSocketDisconnect
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not await _dispatch_ws_message(
                msg, mgr=mgr, sess=sess, sid=sid, queue=queue,
                output_enabled=output_enabled,
            ):
                break
    except WebSocketDisconnect:
        pass
    finally:
        out_task.cancel()
        mgr.detach(sess, queue)
        try:
            await ws.close()
        except RuntimeError:
            pass
