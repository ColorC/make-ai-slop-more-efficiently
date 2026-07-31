# [OMNI] origin=claude-code ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:dashboard.ide_api.sse_rest_endpoint.py"
"""IDE API — SSE 事件流 + REST 交互端点

借鉴 OpenHands 的 oh_event/oh_user_action 模式，
使用 SSE (Server-Sent Events) 替代 WebSocket/Socket.IO，
直接映射 SQLiteBus.tail() → SSE 流。

端点:
  GET  /ide/events              — SSE 实时事件流
  POST /ide/send                — 提交用户指令
  GET  /ide/sessions            — 会话列表
  GET  /ide/trace/{id}/history  — 批量历史事件
  GET  /ide/trace/{id}/files    — 文件操作摘要
  POST /ide/cancel              — 取消 agent
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from ulid import ULID

logger = logging.getLogger(__name__)

ide_router = APIRouter(tags=["ide"])


# ── Request/Response Models ──


class SendActionRequest(BaseModel):
    trace_id: str | None = None
    instruction: str
    # session 上下文 — 跟 cc_session 对齐, 走 plan 联动 (而不是私有 user_context JSON)
    active_plan: str | None = None  # plan id, 例 "_infra/[2026-05-01]WEB-FOUNDATION"
    cwd: str | None = None


class SendActionResponse(BaseModel):
    trace_id: str
    event_id: str


class CancelRequest(BaseModel):
    trace_id: str


class SessionInfo(BaseModel):
    trace_id: str
    status: str
    task_desc: str | None
    created_at: str
    last_active: str


class FileChange(BaseModel):
    path: str
    action: str  # "read" | "write" | "edit" | "create"
    old_text: str | None = None
    new_text: str | None = None


# ── Helpers ──


def _get_bus(request: Request) -> Any:
    bus = getattr(request.app.state, "ide_bus", None)
    if bus is None:
        raise HTTPException(503, "Event bus not initialized")
    return bus


def _get_session_manager(request: Request) -> Any:
    mgr = getattr(request.app.state, "ide_session_manager", None)
    if mgr is None:
        raise HTTPException(503, "Session manager not initialized")
    return mgr


# ── SSE Events ──


@ide_router.get("/ide/events")
async def sse_events(
    request: Request,
    trace_id: str | None = Query(None, description="Filter by trace ID"),
    event_types: str | None = Query(None, description="Comma-separated event type filter"),
):
    """SSE 实时事件流。

    每个事件以 `data: {json}\n\n` 格式发送。
    前端使用 EventSource API 接收。
    """
    bus = _get_bus(request)

    type_filter: set[str] | None = None
    if event_types:
        type_filter = {t.strip() for t in event_types.split(",")}

    async def event_generator():
        async for event in bus.tail(trace_id=trace_id):
            # 检查客户端是否断开
            if await request.is_disconnected():
                break

            # 类型过滤
            if type_filter and event.event_type not in type_filter:
                continue

            data = event.model_dump_json()
            yield f"data: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── REST Endpoints ──


@ide_router.post("/ide/send", response_model=SendActionResponse)
async def send_action(body: SendActionRequest, request: Request):
    """提交用户指令，启动或继续 agent 会话。"""
    mgr = _get_session_manager(request)

    trace_id = body.trace_id or str(ULID())
    trace_id, event_id = await mgr.submit_and_run(
        trace_id, body.instruction,
        active_plan=body.active_plan, cwd=body.cwd,
    )

    return SendActionResponse(trace_id=trace_id, event_id=event_id)


@ide_router.get("/ide/sessions", response_model=list[SessionInfo])
def list_sessions(request: Request):
    """返回所有活跃会话。"""
    mgr = _get_session_manager(request)
    return mgr.list_sessions()


@ide_router.get("/ide/trace/{trace_id}/history")
async def trace_history(trace_id: str, request: Request):
    """批量返回 trace 的全部事件（用于 SSE 重连后加载历史）。"""
    bus = _get_bus(request)
    events = await bus.read_trace(trace_id)
    return [json.loads(e.model_dump_json()) for e in events]


@ide_router.get("/ide/trace/{trace_id}/files", response_model=list[FileChange])
async def trace_files(trace_id: str, request: Request):
    """提取 trace 中的文件操作。"""
    bus = _get_bus(request)
    events = await bus.read_trace(trace_id)

    # 建立 tool_call → tool_result 的关联
    tool_calls: dict[str, dict] = {}  # event_id -> payload
    file_changes: list[FileChange] = []

    for ev in events:
        if ev.event_type == "agent.tool.call":
            tool_calls[ev.id] = ev.payload
        elif ev.event_type == "agent.tool.result" and ev.parent_id:
            call = tool_calls.get(ev.parent_id, {})
            tool = call.get("tool", "")
            args = call.get("args", {})

            if tool in ("read_file", "view"):
                file_changes.append(FileChange(
                    path=args.get("path", args.get("file_path", "")),
                    action="read",
                ))
            elif tool in ("write_file", "create"):
                file_changes.append(FileChange(
                    path=args.get("path", args.get("file_path", "")),
                    action="create",
                    new_text=args.get("content", args.get("file_text", "")),
                ))
            elif tool in ("str_replace_editor", "edit"):
                cmd = args.get("command", "str_replace")
                if cmd == "create":
                    file_changes.append(FileChange(
                        path=args.get("path", ""),
                        action="create",
                        new_text=args.get("file_text", ""),
                    ))
                else:
                    file_changes.append(FileChange(
                        path=args.get("path", ""),
                        action="edit",
                        old_text=args.get("old_str"),
                        new_text=args.get("new_str"),
                    ))

    return file_changes


@ide_router.get("/ide/metrics")
def get_metrics(
    caller_prefix: str | None = Query(None, description="Filter by caller prefix (e.g. pipeline.demogame_qa)"),
):
    """LLM 计量数据：token 用量、成本、按节点分组。"""
    from omnicompany.runtime.llm.llm import LLMMeter
    meter = LLMMeter.get_instance()
    return {
        "summary": meter.summary(caller_prefix=caller_prefix),
        "breakdown": meter.breakdown(caller_prefix=caller_prefix or ""),
    }


@ide_router.post("/ide/cancel")
async def cancel_session(body: CancelRequest, request: Request):
    """取消运行中的 agent。"""
    mgr = _get_session_manager(request)
    success = await mgr.cancel(body.trace_id)
    if not success:
        raise HTTPException(404, f"Session {body.trace_id} not found")
    return {"ok": True, "trace_id": body.trace_id}


# ── 统一会话上下文端点 (跟 cc /sessions/{sid}/context 形态对齐) ──────────────
#
# 核心: session 上下文 = active plan (含 frontmatter) + cwd + agent state
#       + 修改文件 / 工具调用 / 新增 worker/material (从 router 事件聚合)
# 不再有 "session 私有 user_context" — work_type / standards 走 plan.md frontmatter

import re as _re

_NATIVE_BASH_REDIRECT = _re.compile(r"(?:>\s*|>>\s*|tee\s+(?:-a\s+)?)([^\s'\"|&;]+)")
_NATIVE_WORKER_PAT = _re.compile(r"packages[/\\][^/\\]+[/\\]workers[/\\][^/\\]+\.py$")
_NATIVE_TEAM_PAT = _re.compile(r"packages[/\\][^/\\]+[/\\]team[^/\\]*\.py$", _re.IGNORECASE)
_NATIVE_MATERIAL_PAT = _re.compile(r"packages[/\\][^/\\]+[/\\](?:materials|formats)\.py$")


def _aggregate_native_io(events: list) -> dict:
    """从 native NativeIdeAgent 的 router 事件聚合 modified_files / bash_writes / added_workers/materials.

    跟 cc_wrapper/api._aggregate_session_io 同语义, 但 event 形态不同:
        - cc:    event_type = "agent.tool.call",          payload.tool / payload.args
        - native: event_type = "router.tool_dispatch.input", payload.data.tool_name / payload.data.args
    """
    modified: dict[str, dict] = {}
    bash_writes: list[dict] = []
    added_workers: list[str] = []
    added_materials: list[str] = []
    seen: set[str] = set()
    tool_calls: list[dict] = []  # 简短调用清单 (tool_name + 关键参数)
    total_input_tokens = 0
    total_output_tokens = 0
    turn_count = 0
    model: str | None = None

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
        ts = ev.timestamp.isoformat() if hasattr(ev.timestamp, "isoformat") else str(ev.timestamp)
        et = ev.event_type
        p = ev.payload or {}

        if et == "router.tool_dispatch.input":
            d = p.get("data") or {}
            tool = d.get("tool_name") or ""
            args = d.get("args") or {}
            tool_calls.append({"tool": tool, "ts": ts})

            if tool == "write_file":
                fp = args.get("file_path") or args.get("path") or ""
                if fp:
                    _bump(fp, ts, tool)
                    if fp not in seen:
                        seen.add(fp)
                        if _NATIVE_WORKER_PAT.search(fp): added_workers.append(fp)
                        elif _NATIVE_TEAM_PAT.search(fp): added_workers.append(fp)
                        elif _NATIVE_MATERIAL_PAT.search(fp): added_materials.append(fp)
            elif tool == "bash":
                cmd = args.get("command") or ""
                for m in _NATIVE_BASH_REDIRECT.finditer(cmd):
                    target = m.group(1)
                    bash_writes.append({"path": target, "snippet": cmd[:120], "ts": ts})
                    _bump(target, ts, "bash")

        elif et == "router.llm_call.output":
            d = p.get("data") or {}
            usage = d.get("usage") or {}
            total_input_tokens += int(usage.get("input_tokens") or 0)
            total_output_tokens += int(usage.get("output_tokens") or 0)
            if not model:
                model = usage.get("model")

        elif et == "agent.turn.start":
            turn_count += 1

    mod_list = sorted(modified.values(), key=lambda x: x["last_ts"], reverse=True)
    return {
        "modified_files": mod_list,
        "bash_writes": bash_writes,
        "added_workers": added_workers,
        "added_materials": added_materials,
        "tool_calls": tool_calls,
        "stats": {
            "model": model,
            "turn_count": turn_count,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
        },
    }


@ide_router.get("/ide/trace/{trace_id}/context")
async def get_trace_context(trace_id: str, request: Request) -> dict:
    """统一会话上下文 — native 版, 跟 cc /sessions/{sid}/context 形态对齐.

    返回字段:
        session_id, kind="native"
        context: { active_plan, plan_meta, cwd, agent_state, started_at }
        modified_files / bash_writes / added_workers / added_materials / tool_calls
        event_count
    """
    bus = _get_bus(request)
    mgr = _get_session_manager(request)
    sess = mgr.get(trace_id)

    events = await bus.read_trace(trace_id)
    io = _aggregate_native_io(events)

    # active_plan / cwd 来自 IDESession in-memory (重启后丢, 不持久化 — 跟 cc 保留 cc_sessions.json 不同)
    active_plan = sess.active_plan if sess else None
    cwd = sess.cwd if sess else None
    state = sess.status if sess else "ended"
    started_at = sess.created_at.isoformat() if sess else None

    # plan.md frontmatter + 所属 project 的 project.md frontmatter
    plan_meta: dict = {}
    project_meta: dict = {}
    if active_plan:
        try:
            from omnicompany.dashboard.controlplane.plans import parse_plan_frontmatter, parse_project_meta, _plans_root
            plan_meta = parse_plan_frontmatter(_plans_root() / active_plan / "plan.md")
            project_meta = parse_project_meta(active_plan)
        except Exception:
            plan_meta = {}
            project_meta = {}

    return {
        "session_id": trace_id,
        "kind": "native",
        "context": {
            "active_plan": active_plan,
            "plan_meta": plan_meta,
            "project_meta": project_meta,
            "cwd": cwd,
            "agent_state": state,
            "started_at": started_at,
        },
        **io,
        "event_count": len(events),
    }
