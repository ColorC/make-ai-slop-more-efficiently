# [OMNI] origin=ai-ide ts=2026-05-07 type=infra
# [OMNI] material_id="material:dashboard.cc_wrapper.cc_chat_bridge.endpoints.py"
"""Claude Code 网页 chat 后端 — 用 claude-agent-sdk 包装本地 claude 二进制.

跟 pty_service.py 互补:
  pty_service.py     PTY 路线 — xterm + 原始字节流, 适合需要终端 UI 的场景
  cc_chat_bridge.py  Chat 路线 — claude-agent-sdk 走结构化消息, 适合网页 chat UI

**核心原则** (避免再犯段四走偏): claude-agent-sdk **不是** 直连 anthropic API
的 SDK. 它是**本地 claude 二进制的高级 spawn 包装** — 内部 spawn 用户机器上
装的 `claude` CLI (跟 pty_service.py spawn 同一个 binary), 走 stream-json
模式通信. 认证走 `claude login` 订阅, 不要 ANTHROPIC_API_KEY. 工具
(Edit/Bash/Read/Write/Grep/Glob/Task 等)由本地 claude binary 自带, 不重写.

----------------------------------------------------------------
WebSocket 帧契约
----------------------------------------------------------------
client → server (JSON):
  {"type":"user.message","content":"..."}             # 字符串提示词
  {"type":"user.message","content":[...content blocks...]}  # 富内容 (图片等)
  {"type":"user.interrupt"}

server → client (JSON, 来自 claude-agent-sdk 的 5 种 message 类型直转):
  {"kind":"system","subtype":"init","session_id":"...", ...}     # SystemMessage
  {"kind":"assistant","content":[{"type":"text","text":"..."},   # AssistantMessage
                                  {"type":"thinking","thinking":"..."},
                                  {"type":"tool_use","id":"...","name":"...","input":{...}}],
   "model":"sonnet","message_id":"...","stop_reason":null,...}
  {"kind":"user","content":[{"type":"tool_result","tool_use_id":"...","content":"..."}]}
  {"kind":"result","duration_ms":1234,"total_cost_usd":0.05,...}  # ResultMessage
  {"kind":"stream_event", ...}                                    # StreamEvent (partial)
  {"kind":"rate_limit", ...}                                      # RateLimitEvent

----------------------------------------------------------------
Schema 跟 PtySession 共写 cc_sessions.json (data/cc_sessions.json)
----------------------------------------------------------------
新加 `kind` 字段区分: 'pty' 缺省 (旧) | 'chat' (本路线).
其余字段 id / cwd / started_at / ended_at / active_plan / claude_session_id
保持兼容 [2026-05-03]CC-PLAN-SESSION-CONTEXT plan-session 协议两路线共用.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import claude_agent_sdk as casdk
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from omnicompany.core.caller_identity import CALLER_ENV, CALLER_SUBAGENT
from omnicompany.dashboard.session_workdir import default_session_cwd, resolve_session_cwd
from .pty import _read_meta_store, _sanitize_nonfinite, _write_meta_store
from .providers.base import BaseProvider, ProviderOptions
from .hooks._shared import emit_event as _emit_ide_event
from .write_scope import planned_write_denial_with_scope

logger = logging.getLogger(__name__)

# meta store 持久化专用单 worker 执行器。
# cc_sessions.json 是"全量读-改-写" (9MB+), 若用 asyncio.to_thread (共享多 worker 池)
# 两个 session 的 persist 会交错: 各读旧 store 再整量写回, 后写者覆盖先写者丢更新。
# 单 worker 队列保证 提交序=执行序, read-modify-write 不会交错; 同时把 145ms 级的
# 磁盘 IO 挪出事件循环 (原是"任意请求随机慢到秒级"的主因之一, 2026-07 P0 批)。
_PERSIST_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cc-meta-persist")


def _emit_chat_event(
    *,
    trace_id: str,
    event_type: str,
    payload: dict[str, Any],
    source: str,
    tags: list[str] | None = None,
    parent_id: str | None = None,
) -> str:
    return _emit_ide_event(
        trace_id,
        event_type,
        payload,
        parent_id=parent_id,
        source=source,
        tags=tags,
    )


# ── 默认参数 ────────────────────────────────────────────────────────────────
# model=None → 不传 model 参数给 SDK, 让本地 claude binary 用 ~/.claude/settings.json
# 里用户自己的 model 设置 (Claude Max 订阅时通常是 opus). 上游 Node 写死默认 'sonnet'
# 反而覆盖用户本地配置 — 我们不学这点. 用户要显式覆盖时通过 OMNI_CC_CHAT_MODEL env
# 或 create body.model 字段传 ('sonnet'/'opus'/'haiku'/'opusplan'/'sonnet[1m]').
DEFAULT_MODEL = os.environ.get("OMNI_CC_CHAT_MODEL")  # None unless explicitly set
DEFAULT_PERMISSION_MODE = "bypassPermissions"  # 跟 pty_service "--dangerously-skip-permissions" 同效, 在线 dashboard 不弹窗
VALID_PERMISSION_MODES = {"default", "acceptEdits", "auto", "bypassPermissions", "plan"}
# P0-a 防递归 spawn: 不依赖 env 的第二道防线 — 同时存活的 subagent 硬上限。
# 主防线是给 subagent 子进程注入 OMNI_CLI_CALLER=subagent (cli/_access.py 据此拒绝
# subagent 调 `omni worker spawn`); 本上限封顶递归 spawn 万一失控的规模。
MAX_LIVE_SUBAGENTS = int(os.environ.get("OMNI_MAX_LIVE_SUBAGENTS", "16") or "16")
LOCAL_MODEL_SENTINELS = {"", "(default)", "(local)", "(local default)"}
_UNSET = object()
CLAUDE_WORKSPACE_GUARD_PROMPT = """
OmniChat workspace guard:
- Your primary workspace is the current working directory. Prefer reading and writing files inside it.
- External writes are allowed only when they are explicitly planned. Use the active plan/project frontmatter to declare `allowed_write_roots` or `allowed_write_paths`; project-level declarations apply to every plan underneath that project.
- Before declaring a new external write path, read the active plan, project.md when present, and relevant standards/specs. Record what each external directory is for in the plan.
- If a tool call is denied or blocked by a hook, treat that denial as the tool result, update the write plan if appropriate, and then retry once the path is declared. Do not loop on the same blocked call.
""".strip()


def _normalize_session_model(value: Any) -> str | None:
    if value is None:
        return None
    model = str(value).strip()
    if model in LOCAL_MODEL_SENTINELS:
        return None
    return model


def _normalize_permission_mode(value: Any) -> str:
    mode = str(value or "").strip()
    if mode not in VALID_PERMISSION_MODES:
        raise ValueError(f"invalid permission_mode: {mode!r}")
    return mode


# N2b 推理强度档 (effort): claude-agent-sdk EffortLevel = low/medium/high/xhigh/max.
# None = 不传 effort, 用模型/CLI 默认。空串 / "default" / "auto" 都归一成 None。
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
CODEX_EFFORT_LEVELS = ("minimal", "low", "medium", "high")
CODEBUDDY_EFFORT_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")


def _normalize_effort(value: Any, provider: str | None = None) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in ("", "default", "auto", "none"):
        return None
    if provider in ("kimi", "opencode"):
        # 两家 CLI 的非交互模式都不消费 effort 档; 已知档位接受并存档 (展示用),
        # 未知档位回落默认 (None), 不报错.
        return v if v in EFFORT_LEVELS else None
    levels = (
        CODEX_EFFORT_LEVELS
        if provider == "codex"
        else CODEBUDDY_EFFORT_LEVELS
        if provider == "codebuddy"
        else EFFORT_LEVELS
    )
    if v not in levels:
        raise ValueError(f"invalid effort for {provider or 'provider'}: {value!r} (want one of {levels} or default)")
    return v


# N2c 持续目标 (/goal): 自建"续发循环"——设目标后每轮结束自动续作, 直到 agent 声明
# 达成 (输出含 GOAL_DONE_SENTINEL) 或达到轮数上限。无 Claude Code 原生 Stop 钩子,
# 故 SDK 直连侧自建 (落在 goal_state)。
GOAL_DONE_SENTINEL = "GOAL_DONE"
GOAL_MAX_ITERATIONS_DEFAULT = 30
GOAL_CONTINUE_DELAY_SEC = 0.4  # 续发前小停, 让上一轮 complete 帧先落, UI 不挤


def _resolve_context_window(model: str) -> int:
    """按 model 名查 context window — 给前端 TokenUsagePie 算 % 用. **不收紧实际 API 调用,
    只影响显示**.

    用户 2026-05-13 立: "所有 opus 和 sonnet 4-6+ 都有 1M 模式" — 不要把 1M 当成 [1m]
    后缀独享, 标准变体也能跑 1M (订阅决定). 默认给 1M 让 % 显示不虚高: 如果实际是 200K
    我们写 1M 顶多 % 偏低 (不报警), 反过来会假警报"满了".
    haiku 例外 — 这模型确实 200K 上限.
    未知 model 也按 1M 走 (claude code 用户绝大多数是 4.6+).
    """
    if not model:
        return 1_000_000
    m = model.lower()
    if "haiku" in m:
        return 200_000
    # opus / sonnet 4.x (含 [1m] 显式标注) 一律按 1M 给
    return 1_000_000


# ── 会话数据模型 ────────────────────────────────────────────────────────────

_PROVIDER_CN_NAMES = {
    "claude": "Claude 编程",
    "claude_code": "Claude 编程",
    "codex": "Codex 编程",
    "codebuddy": "CodeBuddy 编程",
    "kimi": "Kimi",
    "opencode": "OpenCode 编程",
    "controller": "总控协作",
    "omni_agent": "Omni 智能体",
}


def _default_session_name(provider: str, cwd: str, started_at: float | None = None) -> str:
    """Generate a stable, meaningful Chinese title before the first prompt exists."""
    provider_name = _PROVIDER_CN_NAMES.get((provider or "").lower(), "智能体对话")
    workspace = Path(cwd or os.getcwd()).name.strip() or "工作区"
    stamp = time.localtime(started_at or time.time())
    return f"{provider_name} · {workspace} · {stamp.tm_mon:02d}月{stamp.tm_mday:02d}日 {stamp.tm_hour:02d}:{stamp.tm_min:02d}"


@dataclass
class CcChatSession:
    """In-memory chat session — 内含一个常驻 ClaudeSDKClient (双向交互).

    history 缓存"喂给前端 snapshot 用"的纯文本视图; 真消息流由 claude-agent-sdk
    内部维护, history 只是给重连快照展示历史轮次的便利字段.
    """

    id: str
    cwd: str
    started_at: float
    provider: str = "claude_code"  # claude_code (默认, 走 SDK 直连) / omni_agent / codex
    name: str = ""  # 后端保证非空中文显示名; 用户仍可编辑
    archived: bool = False
    favorite: bool = False
    model: str | None = DEFAULT_MODEL  # None = 用本地 ~/.claude/settings.json 配置
    history_summary: list[dict[str, str]] = field(default_factory=list)  # [{"role":..,"text":..}]
    event_history: list[dict[str, Any]] = field(default_factory=list)
    # event_history 的 id→index 旁路索引: _append_event_history 的按 id 查重从 O(N) 全扫
    # 降为 O(1) 查表 (原实现每条消息扫一遍全历史, 长会话下是 O(N²), 2026-07 P0 批)。
    # 只在 _append_event_history / 会话恢复两处维护, event_history 没有其它写入点。
    _eh_id_index: dict[str, int] = field(default_factory=dict)
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    active_plan: str | None = None
    goal_state: dict[str, Any] = field(default_factory=dict)
    claude_session_id: str | None = None  # 来自 SystemMessage(subtype="init") 的 session_id
    ended_at: float | None = None
    exit_reason: str | None = None
    # 路径 A (claude_code): 直接持 ClaudeSDKClient (现有路径, 不动)
    client: casdk.ClaudeSDKClient | None = None
    current_receive_task: asyncio.Task | None = None
    submit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending_interrupt_prompts: list[str] = field(default_factory=list)
    pending_interrupt_record_history: list[bool] = field(default_factory=list)
    in_flight_turn: bool = False
    # 路径 B (omni_agent / codex): 持 BaseProvider 实例 + 后台 consume task
    provider_impl: BaseProvider | None = None
    provider_consume_task: asyncio.Task | None = None
    # 权限 flow: SDK can_use_tool callback 触发时, 用 request_id 挂个 Future, 等前端
    # 'claude-permission-response' ws 帧来 resolve. 然后 callback 返 Allow/Deny.
    pending_permissions: dict[str, asyncio.Future] = field(default_factory=dict)
    # 用户在 ChatComposer 顶栏切 permission 模式 (default/acceptEdits/auto/bypassPermissions/plan).
    # 每条 user.message 可带 permissionMode 字段覆盖. can_use_tool callback 看这个字段
    # 决定弹横幅还是自动 allow.
    current_permission_mode: str = DEFAULT_PERMISSION_MODE
    # N2b 推理强度档 (low/medium/high/xhigh/max), None=用模型默认。连接时通过
    # ClaudeAgentOptions(effort=) 透传给 claude binary; 改档若会话空闲则断开 client,
    # 下轮 resume 重连应用新档 (见 set_effort)。codex 路径暂不消费 (留作后续)。
    effort: str | None = None
    # P0-a cli caller 身份: None=用户直开会话(=external,可 spawn); "subagent"=总控 spawn
    # 的子 agent。claude_code/codex 子进程 env 注入 OMNI_CLI_CALLER=该值, 防 subagent 递归 spawn。
    caller_identity: str | None = None
    # #2 接管式采纳: adopted=这个 chat 是 resume 别处已有 claude/codex 会话采纳来的(当 subagent);
    # taken_over=用户已"接管"该对话(此时总控对它不自动 hook, 见 controller_waker)。用户可随时接管/交还。
    adopted: bool = False
    taken_over: bool = False
    # **累计 token 用量** — Python claude-agent-sdk (0.1.x) 的 ResultMessage.usage 只给"本轮"
    # 的 input/output/cache_*, 不像 Node SDK (claudecodeui 用的 ^0.2.116) 那样有
    # modelUsage.cumulative*. 同源对齐 — 这里 session 级累加, 在 result 帧里塞 cumulative*
    # 字段, 让 ccSessionAdapter 沿用 claudecodeui server/claude-sdk.js extractTokenBudget
    # 同一计算公式.
    cumulative_input_tokens: int = 0
    cumulative_output_tokens: int = 0
    cumulative_cache_creation_input_tokens: int = 0
    cumulative_cache_read_input_tokens: int = 0
    last_token_budget: dict[str, int] | None = None
    # 最近一条 assistant.model — claude binary 实际跑的模型. session.model 可能是 None
    # ("(默认)") 让 binary 决, 这字段是真值. 给 context_window 查询用.
    _last_seen_model: str = ""
    # plan 注入追踪: 记住上次注入的 plan_id, 跟 active_plan 比对决定是否需要重新注入.
    # _NEVER_INJECTED 哨兵 = 新会话首条消息需注入; None = 上次注入时无 plan; 其他 = plan_id.
    _last_injected_plan: str | None = "__NEVER_INJECTED__"
    # N2b: effort 改档时会话在途 → 没法即时断开重连, 挂个标记, 下一轮 submit 前先断开
    # 旧 client, 让 _ensure_runtime 用新 effort resume 重连。
    _pending_effort_reconnect: bool = False
    # N2c: 本轮最后一段 assistant 文本 — 给持续目标续发循环检测 GOAL_DONE 完成信号用。
    _last_turn_text: str = ""

    def to_meta(self) -> dict[str, Any]:
        reported_tokens = (
            self.cumulative_input_tokens
            + self.cumulative_output_tokens
            + self.cumulative_cache_creation_input_tokens
            + self.cumulative_cache_read_input_tokens
        )
        last_message = next((
            str(item.get("text") or "").strip()
            for item in reversed(self.history_summary)
            if str(item.get("text") or "").strip()
        ), "")
        return {
            "id": self.id,
            "kind": "chat",
            "provider": self.provider,
            "name": self.name,
            "archived": self.archived,
            "favorite": self.favorite,
            "cwd": self.cwd,
            "started_at": self.started_at,
            "alive": self.ended_at is None,
            # alive=逻辑会话仍可续接；runtime_alive=当前 daemon 仍持有真实 provider
            # runtime。Multiagent/恢复全部只认后者，避免重启后把全部历史会话当活进程。
            "runtime_alive": self.client is not None or self.provider_impl is not None,
            "running": self.in_flight_turn,
            "subscribers": len(self.subscribers),
            "buffered_chunks": len(self.history_summary),
            "message_count": len(self.history_summary),
            "last_message": last_message or None,
            "claude_session_id": self.claude_session_id,
            "provider_session_id": self.claude_session_id,
            "active_plan": self.active_plan,
            "goal_state": self.goal_state or None,
            "caller_identity": self.caller_identity,
            "adopted": self.adopted,
            "taken_over": self.taken_over,
            "model": self.model or "(local default)",
            "permission_mode": self.current_permission_mode,
            "effort": self.effort,
            # 只投影 provider 实际回传后累计的用量。PTY/未回传 usage 的 adapter
            # 不制造估算值, 前端据此决定是否展示 Token。
            "token_usage": ({
                "total": reported_tokens,
                "input": self.cumulative_input_tokens,
                "output": self.cumulative_output_tokens,
                "cache_creation_input": self.cumulative_cache_creation_input_tokens,
                "cache_read_input": self.cumulative_cache_read_input_tokens,
                "source": "provider_reported",
            } if reported_tokens > 0 else None),
            "last_token_budget": self.last_token_budget,
            "ended_at": self.ended_at,
            "exit_reason": self.exit_reason,
            "status": "alive" if self.ended_at is None else "ended",
            "cmd": [self.provider, "(chat)", self.model or "(local)"],
            "cols": 0,
            "rows": 0,
        }


# ── 会话管理 ────────────────────────────────────────────────────────────────


class CcChatSessionManager:
    """Singleton — 管 CcChatSession + 写盘 cc_sessions.json (kind=chat)."""

    def __init__(self) -> None:
        self._sessions: dict[str, CcChatSession] = {}
        self._lock = asyncio.Lock()
        # 块 3: 进程内 event subscriber. ControllerWaker / SubagentStatusAggregator
        # 通过 subscribe_events() 挂上, 每次 _emit_session_event 调一遍 (best-effort).
        # 故意不走 EventBus 跨进程 — boss_sight 跟 ccdaemon 同进程, 直接回调最简.
        self._event_subscribers: list[Any] = []
        self._load_persisted_sessions()

    def subscribe_events(self, callback: Any) -> None:
        """注册一个进程内事件回调. callback(sess, event_type, payload, tags) — 同步调.
        在 _emit_session_event 写完 sqlite 后触发. 抛异常会被 swallow + log, 不阻断 emit."""
        self._event_subscribers.append(callback)

    def _load_persisted_sessions(self) -> None:
        store = _read_meta_store()
        for sid, entry in store.items():
            if entry.get("kind") != "chat" or entry.get("ended_at") is not None:
                continue
            # 脏数据防线: 历史 store 文件可能混着 NaN/Infinity (json 默认 allow_nan 会
            # 原样往返毒化), 读回时递归洗成 None, 保证脏数据进过一次也不会再循环。
            entry = _sanitize_nonfinite(entry)
            has_resume_anchor = bool(entry.get("claude_session_id") or entry.get("provider_session_id"))
            has_visible_state = bool(entry.get("name") or entry.get("history_summary") or entry.get("event_history"))
            if not has_resume_anchor and not has_visible_state:
                continue
            model = _normalize_session_model(entry.get("model"))
            permission_mode = entry.get("permission_mode") or entry.get("current_permission_mode") or DEFAULT_PERMISSION_MODE
            try:
                permission_mode = _normalize_permission_mode(permission_mode)
            except ValueError:
                permission_mode = DEFAULT_PERMISSION_MODE
            try:
                effort = _normalize_effort(entry.get("effort"), str(entry.get("provider") or "claude_code"))
            except ValueError:
                effort = None
            try:
                sess = CcChatSession(
                    id=str(entry.get("id") or sid),
                    cwd=str(entry.get("cwd") or os.getcwd()),
                    started_at=float(entry.get("started_at") or time.time()),
                    provider=str(entry.get("provider") or "claude_code"),
                    name=str(entry.get("name") or _default_session_name(
                        str(entry.get("provider") or "claude_code"),
                        str(entry.get("cwd") or os.getcwd()),
                        float(entry.get("started_at") or time.time()),
                    )),
                    archived=bool(entry.get("archived") or False),
                    favorite=bool(entry.get("favorite") or False),
                    model=model,
                    history_summary=list(entry.get("history_summary") or []),
                    event_history=list(entry.get("event_history") or []),
                    active_plan=entry.get("active_plan"),
                    goal_state=dict(entry.get("goal_state") or entry.get("goal") or {}),
                    claude_session_id=entry.get("claude_session_id") or entry.get("provider_session_id"),
                    ended_at=entry.get("ended_at"),
                    exit_reason=entry.get("exit_reason"),
                    cumulative_input_tokens=int((entry.get("token_usage") or {}).get("input", 0) or 0),
                    cumulative_output_tokens=int((entry.get("token_usage") or {}).get("output", 0) or 0),
                    cumulative_cache_creation_input_tokens=int((entry.get("token_usage") or {}).get("cache_creation_input", 0) or 0),
                    cumulative_cache_read_input_tokens=int((entry.get("token_usage") or {}).get("cache_read_input", 0) or 0),
                    last_token_budget=entry.get("last_token_budget"),
                    current_permission_mode=permission_mode,
                    effort=effort,
                    caller_identity=(
                        CALLER_SUBAGENT if entry.get("caller_identity") == CALLER_SUBAGENT else None
                    ),
                    adopted=bool(entry.get("adopted")),
                    taken_over=bool(entry.get("taken_over")),
                )
            except Exception:
                logger.exception("cc_chat_bridge: failed to restore chat session %s", sid)
                continue
            # 旁路索引随恢复一并建好 (恢复自盘的历史在 persist 时已去重, setdefault 即首见位置)
            for _i, _m in enumerate(sess.event_history):
                _mid = str(_m.get("id") or "")
                if _mid:
                    sess._eh_id_index.setdefault(_mid, _i)
            self._sessions[sess.id] = sess

    def get(self, sid: str) -> CcChatSession | None:
        return self._sessions.get(sid)

    def list_meta(self) -> list[dict[str, Any]]:
        return [s.to_meta() for s in self._sessions.values() if s.ended_at is None]

    def list_meta_page(
        self,
        *,
        q: str = "",
        full_text: bool = False,
        limit: int = 60,
        offset: int = 0,
        pinned_id: str | None = None,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        query = " ".join((q or "").lower().split())
        limit = max(1, min(int(limit or 60), 200))
        offset = max(0, int(offset or 0))
        rows: list[tuple[int, float, dict[str, Any]]] = []

        for sess in self._sessions.values():
            if sess.ended_at is not None:
                continue
            if sess.archived and not include_archived and sess.id != pinned_id:
                continue
            first_msg, last_msg = self._first_last_message(sess)
            meta = {
                **sess.to_meta(),
                "first_message": first_msg,
                "last_message": last_msg,
                "message_count": len(sess.event_history) or len(sess.history_summary),
            }
            score = self._session_search_score(sess, query, full_text)
            if query and score <= 0:
                continue
            rows.append((score, float(sess.started_at or 0), meta))

        if query:
            rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
        else:
            rows.sort(key=lambda item: item[1], reverse=True)

        total = len(rows)
        page_rows = rows[offset: offset + limit]
        items = [item[2] for item in page_rows]

        if pinned_id and not any(item.get("id") == pinned_id for item in items):
            pinned = self._sessions.get(pinned_id)
            if pinned and pinned.ended_at is None and (include_archived or not pinned.archived):
                pinned_first, pinned_last = self._first_last_message(pinned)
                pinned_meta = {
                    **pinned.to_meta(),
                    "first_message": pinned_first,
                    "last_message": pinned_last,
                    "message_count": len(pinned.event_history) or len(pinned.history_summary),
                    "pinned": True,
                }
                items = [pinned_meta, *items]

        return {
            "items": items,
            "alive_count": len([s for s in self._sessions.values() if s.ended_at is None]),
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
            "query": q,
            "full_text": full_text,
        }

    def _first_last_message(self, sess: CcChatSession) -> tuple[str, str]:
        texts: list[str] = []
        source = sess.event_history
        if source:
            for msg in source:
                if msg.get("kind") != "text":
                    continue
                content = str(msg.get("content") or "").strip()
                if content:
                    texts.append(content)
        else:
            for msg in sess.history_summary:
                content = str(msg.get("text") or "").strip()
                if content:
                    texts.append(content)
        if not texts:
            return "", ""
        return texts[0][:240], texts[-1][:240]

    def _session_search_score(self, sess: CcChatSession, query: str, full_text: bool) -> int:
        if not query:
            return 1
        terms = query.split()
        first, last = self._first_last_message(sess)
        title = (sess.name or "").lower()
        identity = " ".join([
            sess.id,
            sess.provider or "",
            sess.cwd or "",
            sess.claude_session_id or "",
            sess.model or "",
        ]).lower()
        first_last = f"{first}\n{last}".lower()
        full = ""
        if full_text:
            parts: list[str] = []
            for msg in sess.event_history:
                if msg.get("kind") in {"text", "thinking", "tool_result", "error"}:
                    parts.append(str(msg.get("content") or ""))
                elif msg.get("kind") == "tool_use":
                    parts.append(str(msg.get("toolName") or ""))
                    parts.append(json.dumps(msg.get("toolInput") or {}, ensure_ascii=False))
            full = "\n".join(parts).lower()

        score = 0
        for term in terms:
            if term in title:
                score += 120
            if term in first_last:
                score += 60
            if term in identity:
                score += 20
            if full_text and term in full:
                score += 10
            if score == 0:
                return 0
        return score

    def _make_can_use_tool(self, sess: CcChatSession):
        async def _can_use_tool(tool_name: str, tool_input: dict, ctx: Any) -> Any:
            # 块 3 R8: 拿 (denial, scope), 区分软硬 guard
            result = planned_write_denial_with_scope(
                cwd=sess.cwd,
                active_plan=sess.active_plan,
                tool_name=tool_name,
                tool_input=tool_input,
            )
            if result is not None and sess.current_permission_mode != "bypassPermissions":
                denial, scope = result
                # 硬 guard (plan/project frontmatter hard_block_on_denial: true):
                # 阻断 + emit subagent.blocked 唤起总控. 排除 controller 自身 (防自唤).
                if scope.hard_block_on_denial and sess.provider != "controller":
                    self._emit_session_event(
                        sess,
                        "subagent.blocked",
                        {
                            "subagent_id": sess.id,
                            "provider": sess.provider,
                            "active_plan": sess.active_plan,
                            "tool_name": tool_name,
                            "tool_input": tool_input,
                            "denial_message": denial,
                            "guard_mode": "hard",
                        },
                        tags=["subagent", "lifecycle", "guard"],
                    )
                else:
                    # 块 5 R2: 软 guard — 不唤起总控, 但累计到 SoftViolationStore,
                    # subagent.completed 时 ControllerWaker 集中附给总控 (§6.3 集中审视).
                    if sess.provider != "controller":
                        try:
                            from omnicompany.dashboard.boss_sight.services.soft_violation_store import (
                                get_soft_violation_store,
                            )
                            input_summary = json.dumps(tool_input, ensure_ascii=False)[:200] \
                                if isinstance(tool_input, dict) else str(tool_input)[:200]
                            get_soft_violation_store().record(
                                subagent_id=sess.id,
                                tool_name=tool_name,
                                tool_input_summary=input_summary,
                                denial_message=denial,
                            )
                            # 也 emit 一个 soft event 给 events.db (用于 audit + e2e 断言)
                            self._emit_session_event(
                                sess,
                                "subagent.soft_violation",
                                {
                                    "subagent_id": sess.id,
                                    "provider": sess.provider,
                                    "tool_name": tool_name,
                                    "denial_message": denial[:400],
                                    "guard_mode": "soft",
                                },
                                tags=["subagent", "guard", "soft"],
                            )
                        except Exception:  # noqa: BLE001
                            logger.exception("soft violation record failed")
                # 软或硬都返回 deny (PermissionResultDeny 给 LLM 看见). 软 = deny + record,
                # 硬 = deny + emit blocked + 唤起总控. controller 收到 blocked 后可 emit_event
                # (subagent.unblock) 让总控**指挥**subagent 改路径再试.
                return casdk.PermissionResultDeny(message=denial)
            if sess.current_permission_mode == "bypassPermissions":
                return casdk.PermissionResultAllow(updated_input=None)
            # 总控(controller)是被事件 headless 唤起的可信编排者(用户明示 2026-06-03: 总控=主入口,
            # 自举依赖其可靠驱动闭环)。它被 subagent.* / reviewstage.* 事件唤起时通常**没有** UI 在监听
            # permission_request WS 广播 → 走 WS 询问会让每个 Bash(它调 omni cli 的唯一途径)/ 只读工具
            # 卡到 1800s 超时, 总控彻底无法干活。所以: 通过了上面写范围 guard 的工具对总控**直接放行**,
            # 不走 WS 询问。注意写范围 guard 仍在上方对 controller 生效 —— 总控直接用 Write/Edit 写
            # 白名单外路径(.py / 核心层 / 自己的 prompt)依然在 line 437~490 被 Deny, 这里只放行已过 guard 的。
            if sess.provider == "controller":
                return casdk.PermissionResultAllow(updated_input=None)
            request_id = f"perm_{uuid.uuid4().hex[:12]}"
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            sess.pending_permissions[request_id] = fut
            await self._broadcast(sess, {
                "kind": "permission_request",
                "requestId": request_id,
                "toolName": tool_name,
                "input": tool_input,
                "context": {
                    "tool_use_id": getattr(ctx, "tool_use_id", None),
                    "session_id": sess.id,
                },
            })
            try:
                decision = await asyncio.wait_for(fut, timeout=1800)
            except asyncio.TimeoutError:
                decision = {"allow": False, "message": "permission timeout"}
            finally:
                sess.pending_permissions.pop(request_id, None)
            if decision.get("allow"):
                return casdk.PermissionResultAllow(updated_input=decision.get("updatedInput"))
            return casdk.PermissionResultDeny(message=decision.get("message") or "user denied")

        return _can_use_tool

    async def _connect_controller_claude(self, sess: CcChatSession) -> None:
        """总控以 Claude Code(本地 claude binary + claude login 订阅 opus)运行。

        用户明示 2026-06-03: 用 codex / claude code, **不要**用 the_company key 访问 opus
        (那个 dev key 仅限 models=['limited'], 请求 opus 直接 403)。所以总控不再走
        OmniAgentProvider → the_company LLMClient, 而是复用 claude_code 运行时(路径 A):
        claude_code preset 工具(含 Bash, 总控操作早已 CLI 化 → 用 Bash 调 omni cli),
        系统提示 = 总控 system.md + claude_code 适配说明(自然语言回复, 不用 submit_response)。
        """
        if sess.client is not None:
            return
        from omnicompany.dashboard.boss_sight.controller.worker import (
            BossSightControllerWorker,
        )
        controller_prompt = BossSightControllerWorker.NODE_PROMPT or ""
        # system.md 自 2026-06-03 起已是 claude_code 原生版(用 Bash 调 omni cli, 自然语言回复,
        # 无 submit_response)。这里只留一句末尾强化, 不再重复正文。
        adapter = (
            "\n\n---\n"
            "提醒: 你是 Claude Code 会话(本地 opus)。调度/审阅/提议一律用 `Bash` 运行 `omni` CLI; "
            "处理完直接用自然语言回复, 不要找 submit_response 这种工具。\n"
        )
        def _opts(resume: str | None) -> "casdk.ClaudeAgentOptions":
            return casdk.ClaudeAgentOptions(
                system_prompt={
                    "type": "preset",
                    "preset": "claude_code",
                    "append": controller_prompt + adapter,
                },
                tools={"type": "preset", "preset": "claude_code"},
                setting_sources=["user", "project", "local"],
                permission_mode=sess.current_permission_mode,
                can_use_tool=self._make_can_use_tool(sess),
                resume=resume,
                fork_session=False,
                cwd=sess.cwd,
                model="opus",  # 本地 claude 别名, claude login 订阅有 opus; 不传 the_company 模型名
                effort=sess.effort,  # N2b 推理强度档 (None=默认)
                include_partial_messages=True,
                env={"OMNI_CLI_CALLER": "controller"},  # 其 Bash 调 omni cli 以总控身份
            )

        async def _try_connect(resume: str | None) -> None:
            sess.client = casdk.ClaudeSDKClient(options=_opts(resume))
            await sess.client.connect()

        try:
            await _try_connect(sess.claude_session_id)
        except (casdk.CLINotFoundError, casdk.CLIConnectionError, casdk.ProcessError) as e:
            # resume 失败(常见: 旧 omni_agent 总控会话的 session_id 对 claude 无效)→ 退回全新 claude 会话。
            sess.client = None
            if sess.claude_session_id:
                logger.warning("controller %s resume failed (%s); starting fresh claude session", sess.id, type(e).__name__)
                try:
                    await _try_connect(None)
                except (casdk.CLINotFoundError, casdk.CLIConnectionError, casdk.ProcessError) as e2:
                    sess.client = None
                    raise RuntimeError(f"总控 (Claude Code) 启动失败 ({type(e2).__name__}): {e2}") from e2
            else:
                raise RuntimeError(f"总控 (Claude Code) 启动失败 ({type(e).__name__}): {e}") from e

    async def _ensure_runtime(self, sess: CcChatSession) -> None:
        if sess.provider == "controller":
            # 总控走 Claude Code(本地 opus), 不走 the_company。见 _connect_controller_claude。
            await self._connect_controller_claude(sess)
            return

        if sess.provider == "claude_code":
            if sess.client is not None:
                return

            opts = casdk.ClaudeAgentOptions(
                system_prompt={
                    "type": "preset",
                    "preset": "claude_code",
                    "append": CLAUDE_WORKSPACE_GUARD_PROMPT,
                },
                tools={"type": "preset", "preset": "claude_code"},
                setting_sources=["user", "project", "local"],
                permission_mode=sess.current_permission_mode,
                can_use_tool=self._make_can_use_tool(sess),
                resume=sess.claude_session_id,
                fork_session=False,
                cwd=sess.cwd,
                model=sess.model,
                effort=sess.effort,  # N2b 推理强度档 (None=默认)
                include_partial_messages=True,
                env=({CALLER_ENV: sess.caller_identity} if sess.caller_identity else {}),
            )
            sess.client = casdk.ClaudeSDKClient(options=opts)
            try:
                await sess.client.connect()
            except (casdk.CLINotFoundError, casdk.CLIConnectionError, casdk.ProcessError) as e:
                sess.client = None
                raise RuntimeError(f"claude-agent-sdk startup failed ({type(e).__name__}): {e}") from e
            return

        if sess.provider == "codex":
            if sess.provider_impl is not None:
                return
            from .providers.codex import CodexProvider
            provider_opts: ProviderOptions = {
                "cwd": sess.cwd,
                "active_plan": sess.active_plan,
                "permission_mode": sess.current_permission_mode,
            }
            if sess.model:
                provider_opts["model"] = sess.model
            if sess.effort:
                provider_opts["effort"] = sess.effort
            if sess.claude_session_id:
                provider_opts["provider_session_id"] = sess.claude_session_id
            if sess.caller_identity:
                provider_opts["env"] = {**os.environ, CALLER_ENV: sess.caller_identity}
            sess.provider_impl = CodexProvider(provider_opts)
            try:
                await sess.provider_impl.connect()
            except RuntimeError as e:
                sess.provider_impl = None
                raise RuntimeError(f"CodexProvider startup failed: {e}") from e
            sess.provider_consume_task = asyncio.create_task(self._consume_provider(sess))
            return

        if sess.provider in ("kimi", "opencode", "codebuddy"):
            if sess.provider_impl is not None:
                return
            from .providers.codebuddy import CodeBuddyProvider
            from .providers.kimi import KimiProvider
            from .providers.opencode import OpenCodeProvider
            provider_opts = ProviderOptions(cwd=sess.cwd)
            if sess.model:
                provider_opts["model"] = sess.model
            if sess.claude_session_id:
                provider_opts["provider_session_id"] = sess.claude_session_id  # type: ignore[typeddict-unknown-key]
            if sess.caller_identity:
                provider_opts["env"] = {**os.environ, CALLER_ENV: sess.caller_identity}
            provider_classes = {
                "kimi": KimiProvider,
                "opencode": OpenCodeProvider,
                "codebuddy": CodeBuddyProvider,
            }
            sess.provider_impl = provider_classes[sess.provider](provider_opts)
            try:
                await sess.provider_impl.connect()
            except RuntimeError as e:
                sess.provider_impl = None
                raise RuntimeError(f"{sess.provider} provider startup failed: {e}") from e
            sess.provider_consume_task = asyncio.create_task(self._consume_provider(sess))
            return

        if sess.provider == "omni_agent":
            if sess.provider_impl is not None:
                return
            raise RuntimeError("Restored omni_agent chat sessions cannot be resumed yet")

    def _history_provider(self, sess: CcChatSession) -> str:
        return "claude" if sess.provider == "claude_code" else (sess.provider or "claude")

    def _event_source(self, sess: CcChatSession) -> str:
        return f"cc-chat:{sess.provider or 'unknown'}"

    def _emit_session_event(
        self,
        sess: CcChatSession,
        event_type: str,
        payload: dict[str, Any],
        *,
        tags: list[str] | None = None,
        parent_id: str | None = None,
    ) -> str:
        enriched = {
            "session_id": sess.id,
            "provider": sess.provider,
            "provider_session_id": sess.claude_session_id,
            "cwd": sess.cwd,
            "active_plan": sess.active_plan,
            **payload,
        }
        eid = _emit_chat_event(
            trace_id=sess.id,
            event_type=event_type,
            payload=enriched,
            source=self._event_source(sess),
            tags=["cc_chat", sess.provider, *(tags or [])],
            parent_id=parent_id,
        )
        # 块 3: in-process subscribers (controller_waker / status_aggregator)
        for cb in self._event_subscribers:
            try:
                cb(sess, event_type, enriched, list(tags or []))
            except Exception:  # noqa: BLE001
                logger.exception("cc_chat_bridge: subscriber %r failed for %s", cb, event_type)
        return eid

    def _emit_normalized_message(self, sess: CcChatSession, nm: dict[str, Any]) -> None:
        kind = str(nm.get("kind") or "unknown")
        self._emit_session_event(
            sess,
            f"chat.normalized.{kind}",
            {"message": nm},
            tags=[f"kind:{kind}"],
        )

    def _emit_raw_frame(self, sess: CcChatSession, frame: dict[str, Any]) -> None:
        kind = str(frame.get("kind") or "unknown")
        self._emit_session_event(
            sess,
            f"chat.raw.{kind}",
            {"frame": frame},
            tags=[f"kind:{kind}", "raw"],
        )

    async def _append_event_history(self, sess: CcChatSession, nm: dict[str, Any]) -> None:
        kind = nm.get("kind")
        if kind not in {"text", "thinking", "tool_use", "tool_result", "error", "context_event"}:
            return
        self._emit_normalized_message(sess, nm)
        idx = len(sess.event_history)
        msg: dict[str, Any] = {
            "id": f"hist_{sess.id}_{idx}",
            "sessionId": sess.id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "provider": self._history_provider(sess),
            "kind": kind,
        }
        if kind == "text":
            content = str(nm.get("content", "") or "")
            if not content.strip():
                return
            msg["role"] = nm.get("role") or "assistant"
            msg["content"] = content
        elif kind == "thinking":
            content = str(nm.get("content", "") or "")
            if not content.strip():
                return
            msg["content"] = content
        elif kind == "tool_use":
            tool_id = str(nm.get("toolId", "") or "")
            if tool_id:
                msg["id"] = f"tool_{sess.id}_{tool_id}_use"
            msg["toolId"] = tool_id
            msg["toolName"] = str(nm.get("toolName", "") or "")
            msg["toolInput"] = nm.get("input", nm.get("toolInput", {}))
        elif kind == "tool_result":
            tool_id = str(nm.get("toolId", "") or "")
            if tool_id:
                msg["id"] = f"tool_{sess.id}_{tool_id}_result"
            msg["toolId"] = tool_id
            msg["content"] = str(nm.get("result", nm.get("content", "")) or "")
            msg["isError"] = bool(nm.get("isError", False))
        elif kind == "error":
            msg["content"] = str(nm.get("error", nm.get("content", "unknown error")) or "unknown error")
        elif kind == "context_event":
            trigger = str(nm.get("status") or nm.get("trigger") or "context")
            msg["id"] = str(nm.get("id") or f"context_{sess.id}_{idx}")
            msg["status"] = trigger
            msg["summary"] = str(nm.get("summary") or "Context resolved")
            msg["content"] = msg["summary"]
            msg["context"] = nm.get("context") or {}
            msg["planId"] = nm.get("planId") or sess.active_plan
        # 按 id 查重走 _eh_id_index 旁路索引 O(1) (原实现每条消息线性扫全历史 O(N),
        # 工具调用高频更新同一 tool_id 时整体 O(N²), 长会话实测拖慢每条消息)。
        hit = sess._eh_id_index.get(msg["id"])
        if (
            hit is not None
            and hit < len(sess.event_history)
            and sess.event_history[hit].get("id") == msg["id"]  # 防御索引失稳: 错槽不当命中
        ):
            sess.event_history[hit] = {**sess.event_history[hit], **msg}
            await self._persist(sess)
            return
        sess._eh_id_index[msg["id"]] = len(sess.event_history)
        sess.event_history.append(msg)
        await self._persist(sess)

    def _dedupe_event_history(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        positions: dict[str, int] = {}
        for msg in messages:
            mid = str(msg.get("id", ""))
            if mid and mid in positions:
                out[positions[mid]] = {**out[positions[mid]], **msg}
                continue
            if mid:
                positions[mid] = len(out)
            out.append(msg)
        return out

    def _token_budget_from_usage(self, sess: CcChatSession, usage: dict[str, Any]) -> dict[str, int]:
        cached = int(usage.get("cache_read_input_tokens", usage.get("cached_input_tokens", 0)) or 0)
        raw_input = int(usage.get("input_tokens", 0) or 0)
        output = int(usage.get("output_tokens", 0) or 0)
        cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
        used = max(raw_input - cached, 0) + output + cache_creation
        total = int(usage.get("context_window") or (
            400_000 if sess.provider == "codex"
            else 262_144 if sess.provider == "kimi"  # Kimi K2 系列 256K
            else 200_000
        ))
        return {"used": used, "total": total}

    async def create(
        self,
        cwd: str | None = None,
        model: str | None = None,
        provider: str = "claude_code",
        fork_from_provider_session_id: str | None = None,
        caller_identity: str | None = None,
        adopt_session_id: str | None = None,
        effort: str | None = None,
    ) -> CcChatSession:
        """创建新 chat session.

        块 3 R7 fork: 当 `fork_from_provider_session_id` 传入时, claude_code 路径会
        以 fork_session=True 启 SDK, 新会话继承源对话历史但写入新 session_id.
        源会话**不受影响**继续跑 (用户原话 §6.2: '不进行打断, 而是对其进行 fork').
        """
        resolved_cwd = resolve_session_cwd(cwd)
        async with self._lock:
            # P0-a 不依赖 env 的第二道防线: subagent 并发硬上限, 封顶递归 spawn 失控规模
            if caller_identity == CALLER_SUBAGENT:
                live_subagents = sum(
                    1 for s in self._sessions.values()
                    if s.caller_identity == CALLER_SUBAGENT and s.ended_at is None
                )
                if live_subagents >= MAX_LIVE_SUBAGENTS:
                    raise RuntimeError(
                        f"subagent 并发上限 {MAX_LIVE_SUBAGENTS} 已满 (当前存活 {live_subagents}); "
                        f"拒绝新 spawn 以防递归失控 (可调 OMNI_MAX_LIVE_SUBAGENTS)"
                    )
            sid = f"chat-{uuid.uuid4().hex[:12]}"
            # 2026-05-26 (用户原话 U-034 / 默认 claude code 先):
            # controller provider 缺省 model = claude-opus-4-7 (the_company 旗舰, 调度判断要好智能).
            # 其他 provider 沿用旧逻辑 (model or DEFAULT_MODEL = None → 走 provider 自己默认).
            effective_model = model or DEFAULT_MODEL
            if not effective_model and provider == "controller":
                effective_model = "claude-opus-4-7"
            if not effective_model and provider == "opencode":
                from .providers.opencode import DEFAULT_OPENCODE_MODEL

                effective_model = DEFAULT_OPENCODE_MODEL
            sess = CcChatSession(
                id=sid,
                cwd=resolved_cwd,
                started_at=time.time(),
                provider=provider,
                name=_default_session_name(provider, resolved_cwd),
                model=effective_model,
                caller_identity=caller_identity,
                effort=_normalize_effort(effort, provider),
            )
            # #2 接管式采纳: resume 别处已有会话(同 session_id, fork_session=False)→ 接管它继续跑。
            # claude: 设 claude_session_id 即走 resume(下方 resume_id 用之); codex: 走 provider_session_id 续接。
            if adopt_session_id:
                sess.claude_session_id = adopt_session_id
                sess.adopted = True

            if provider == "claude_code":
                # 路径 A: 现有 SDK 直连. 跟旧代码 1:1, 不破 e2e
                # can_use_tool callback: SDK 调工具前来问, 我们广播 permission_request
                # 帧到 ws 等前端用户 grant/deny. 用 sess.pending_permissions 挂 Future.
                # R7 fork: 当 fork_from_provider_session_id 传入时, resume=<源> + fork=True
                resume_id = fork_from_provider_session_id or sess.claude_session_id
                opts = casdk.ClaudeAgentOptions(
                    system_prompt={
                        "type": "preset",
                        "preset": "claude_code",
                        "append": CLAUDE_WORKSPACE_GUARD_PROMPT,
                    },
                    tools={"type": "preset", "preset": "claude_code"},
                    setting_sources=["user", "project", "local"],
                    # 所有 Dashboard 原生会话默认完全授权；用户仍可在顶栏显式切回确认模式。
                    # 这里读取 session 状态，保证新会话、恢复会话和运行时切换语义一致。
                    permission_mode=sess.current_permission_mode,
                    can_use_tool=self._make_can_use_tool(sess),
                    resume=resume_id,
                    fork_session=bool(fork_from_provider_session_id),
                    cwd=sess.cwd,
                    model=sess.model,
                    effort=sess.effort,  # N2b 推理强度档 (None=默认)
                    include_partial_messages=True,
                    # P0-a: subagent 子进程注入身份, 其 bash 调 `omni worker spawn` 被 cli 拒绝。
                    # SDK 做 {**os.environ, **env}, 传增量即可 (用户直开会话 caller_identity=None → {})。
                    env=({CALLER_ENV: caller_identity} if caller_identity else {}),
                )
                sess.client = casdk.ClaudeSDKClient(options=opts)
                try:
                    await sess.client.connect()
                except (casdk.CLINotFoundError, casdk.CLIConnectionError, casdk.ProcessError) as e:
                    raise RuntimeError(f"claude-agent-sdk 启动失败 ({type(e).__name__}): {e}") from e
            elif provider == "codex":
                # 路径 B-codex: 走 CodexProvider 抽象, 转 NormalizedMessage 流
                from .providers.codex import CodexProvider
                provider_opts: ProviderOptions = {
                    "cwd": sess.cwd,
                    "active_plan": sess.active_plan,
                    "permission_mode": sess.current_permission_mode,
                }
                if sess.model:
                    provider_opts["model"] = sess.model
                # #2 载入/采纳已有 codex 会话: 把源 thread id 传给 CodexProvider, connect 时走
                # resume_thread 续接同一线程(codex 无 fork, resume 即"载入续接"/接管)。
                if fork_from_provider_session_id or adopt_session_id:
                    resume_thread_id = fork_from_provider_session_id or adopt_session_id
                    sess.claude_session_id = resume_thread_id
                    provider_opts["provider_session_id"] = resume_thread_id
                # P0-a: codex 子进程注入身份 (provider 已支持 env 透传, 见 codex.py)。
                # 传全量 env 避免 SDK 若整体替换时丢失 PATH 等。
                if caller_identity:
                    provider_opts["env"] = {**os.environ, CALLER_ENV: caller_identity}
                sess.provider_impl = CodexProvider(provider_opts)
                try:
                    await sess.provider_impl.connect()
                except RuntimeError as e:
                    raise RuntimeError(f"CodexProvider 启动失败: {e}") from e
                # spawn consume task — provider streams NormalizedMessage wire frames.
                sess.provider_consume_task = asyncio.create_task(self._consume_provider(sess))
            elif provider in ("kimi", "opencode", "codebuddy"):
                # 路径 B-kimi/opencode: 子进程包装本地 CLI (spawn-per-prompt),
                # 转 NormalizedMessage 流. 两家 resume 都靠 provider_session_id 续接.
                from .providers.codebuddy import CodeBuddyProvider
                from .providers.kimi import KimiProvider
                from .providers.opencode import OpenCodeProvider
                provider_opts = ProviderOptions(cwd=sess.cwd)
                if sess.model:
                    provider_opts["model"] = sess.model
                if fork_from_provider_session_id or adopt_session_id:
                    resume_session_id = fork_from_provider_session_id or adopt_session_id
                    sess.claude_session_id = resume_session_id
                    provider_opts["provider_session_id"] = resume_session_id  # type: ignore[typeddict-unknown-key]
                # P0-a: 子进程注入身份 (provider 支持 env 透传, 同 codex 路径)。
                if caller_identity:
                    provider_opts["env"] = {**os.environ, CALLER_ENV: caller_identity}
                provider_classes = {
                    "kimi": KimiProvider,
                    "opencode": OpenCodeProvider,
                    "codebuddy": CodeBuddyProvider,
                }
                sess.provider_impl = provider_classes[provider](provider_opts)
                try:
                    await sess.provider_impl.connect()
                except RuntimeError as e:
                    raise RuntimeError(f"{provider} provider 启动失败: {e}") from e
                sess.provider_consume_task = asyncio.create_task(self._consume_provider(sess))
            elif provider == "omni_agent":
                # 路径 B-omni: 走 OmniAgentProvider. 要 bus + agent_class, 暂用默认 ChatAgent
                from .providers.omni_agent import OmniAgentProvider
                from omnicompany.bus.memory import MemoryBus
                # 简化版: 共享单个 MemoryBus per session (隔离)
                bus = MemoryBus()
                await bus.connect()
                # 默认 agent class: ConfigurableAgent 系列暂未指定, 用最简 AgentNodeLoop 子类
                # 临时占位 — 实际 production 应允许配置. TODO: ProviderOptions 加 agent_class
                from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
                from omnicompany.packages.services._core.agent.routers.single_tool import FinishRouter

                class _DefaultChatAgent(AgentNodeLoop):
                    NODE_PROMPT = "你是聊天助手. 用户问你, 你直接调 finish 工具返回回复. 不要调其他工具."
                    TOOL_ROUTERS = [FinishRouter]

                provider_opts = {
                    "cwd": sess.cwd,
                    "model": sess.model,
                    "agent_class": _DefaultChatAgent,
                    "agent_bus": bus,
                }
                sess.provider_impl = OmniAgentProvider(provider_opts)
                try:
                    await sess.provider_impl.connect()
                except RuntimeError as e:
                    raise RuntimeError(f"OmniAgentProvider 启动失败: {e}") from e
                sess.provider_consume_task = asyncio.create_task(self._consume_provider(sess))
            elif provider == "controller":
                # 总控走 Claude Code(本地 claude opus, claude login 订阅), **不走 the_company key**
                # (用户明示 2026-06-03: dev key 仅限 'limited', 请求 opus 会 403)。
                # 复用路径 A 的 SDK 直连, 系统提示 = 总控 system.md + claude_code 适配。
                # 见 _connect_controller_claude。
                await self._connect_controller_claude(sess)
            else:
                raise RuntimeError(
                    f"未知 provider: {provider!r} "
                    "(支持: claude_code / omni_agent / codex / codebuddy / kimi / opencode / controller)"
                )

            self._sessions[sid] = sess
            await self._persist(sess)
            self._emit_session_event(sess, "chat.session.created", {
                "model": sess.model,
                "name": sess.name,
            }, tags=["session"])
            logger.info(
                "cc_chat_bridge: created session %s (provider=%s, cwd=%s, model=%s)",
                sid, provider, sess.cwd, sess.model,
            )
            return sess

    async def _consume_provider(self, sess: CcChatSession) -> None:
        """路径 B 用: 后台 task, 把 provider.consume_messages() 推出的 NormalizedMessage
        规整为上游 wire frame, broadcast 给 ws 订阅者. 一个 session 一个 task,
        整 session 生命周期内跑."""
        if sess.provider_impl is None:
            return
        try:
            async for nm in sess.provider_impl.consume_messages():
                try:
                    if nm.get("kind") in {"text", "thinking", "tool_use", "tool_result", "error"}:
                        await self._append_event_history(sess, nm)
                    else:
                        self._emit_normalized_message(sess, nm)
                    # 路径 B 去返回: 直发上游 wire NM.
                    if nm.get("kind") == "complete":
                        usage = nm.get("usage")
                        if isinstance(usage, dict):
                            usage.setdefault(
                                "context_window",
                                _resolve_context_window(getattr(sess, "_last_seen_model", "") or sess.model or ""),
                            )
                            sess.last_token_budget = self._token_budget_from_usage(sess, usage)
                            await self._broadcast(sess, _finalize_nm(
                                {"kind": "status", "text": "token_budget", "tokenBudget": sess.last_token_budget}, sess))
                        await self._broadcast(sess, _finalize_nm({
                            "kind": "complete",
                            "exitCode": int(nm.get("exitCode", 0) or 0),
                            "isNewSession": bool(nm.get("isNewSession", False)),
                            "actualSessionId": sess.id,
                            "aborted": bool(nm.get("aborted", False)),
                        }, sess))
                    else:
                        await self._broadcast(sess, _finalize_nm(_provider_nm_to_wire(dict(nm)), sess))
                except Exception as e:
                    logger.exception("provider frame mapping failed for %s", sess.id)
                    await self._broadcast_turn_error(sess, type(e).__name__, str(e))
                    continue
                # 抓 session_created 装回 claude_session_id 字段 (供 dashboard 显示)
                if nm.get("kind") == "session_created":
                    new_sid = nm.get("newSessionId")
                    if new_sid and not sess.claude_session_id:
                        sess.claude_session_id = new_sid
                        await self._persist(sess)
                        self._emit_session_event(sess, "chat.provider_session.bound", {
                            "provider_session_id": new_sid,
                        }, tags=["session", "provider_session"])
                # AssistantMessage 末轮抓 text 进 history (供 snapshot)
                if nm.get("kind") == "text":
                    sess.history_summary.append({"role": "assistant", "text": str(nm.get("content", ""))})
                # 块 3: subagent.completed 事件 (一个 turn 跑完, 总控可以来看)
                # 排除 controller session 自身 (避免总控自己完成 turn 时唤起自己)
                if nm.get("kind") == "complete" and sess.provider != "controller":
                    last_assistant = ""
                    for h in reversed(sess.history_summary or []):
                        if isinstance(h, dict) and h.get("role") == "assistant":
                            last_assistant = h.get("text", "")[:500]
                            break
                    self._emit_session_event(
                        sess,
                        "subagent.completed",
                        {
                            "subagent_id": sess.id,
                            "provider": sess.provider,
                            "model": sess.model,
                            "active_plan": sess.active_plan,
                            "verdict": "PASS" if not nm.get("aborted") else "ABORTED",
                            "last_assistant_preview": last_assistant,
                        },
                        tags=["subagent", "lifecycle"],
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("provider consume task crashed for %s", sess.id)
            await self._broadcast_turn_error(sess, type(e).__name__, str(e))

    async def kill(self, sid: str) -> bool:
        async with self._lock:
            sess = self._sessions.get(sid)
            if sess is None:
                return False
            # 路径 A: claude_code SDK
            try:
                if sess.client is not None:
                    await sess.client.disconnect()
            except Exception as e:
                logger.warning("cc_chat_bridge: SDK disconnect failed for %s: %s", sid, e)
            # 路径 B: BaseProvider
            try:
                if sess.provider_impl is not None:
                    await sess.provider_impl.disconnect()
            except Exception as e:
                logger.warning("cc_chat_bridge: provider disconnect failed for %s: %s", sid, e)
            if sess.provider_consume_task and not sess.provider_consume_task.done():
                sess.provider_consume_task.cancel()
            sess.ended_at = time.time()
            sess.exit_reason = "killed"
            await self._broadcast(sess, {"kind": "exit", "reason": "killed"})
            await self._persist(sess)
            return True

    def patch_active_plan(self, sid: str, plan_id: str | None) -> dict[str, Any]:
        sess = self._sessions.get(sid)
        if sess is None:
            raise KeyError(sid)
        sess.active_plan = plan_id
        if sess.provider_impl is not None:
            sess.provider_impl.options["active_plan"] = plan_id
        self._persist_queued(sess)
        self._emit_session_event(
            sess,
            "chat.session.context.updated",
            {"active_plan": plan_id},
            tags=["session", "context"],
        )
        self.schedule_context_event(sess, trigger="plan_switch", switched=True)
        return {
            "session_id": sid,
            "active_plan": plan_id,
            "alive": sess.ended_at is None,
            "effective": "next_user_turn" if sess.ended_at is None else "immediate",
            "note": "Chat route: applies to next user prompt (claude reads CLAUDE.md / setting sources)",
        }

    def set_taken_over(self, sid: str, on: bool) -> dict[str, Any]:
        """#2 接管/交还: on=用户接管该(采纳来的)对话 → 总控不自动 hook(见 controller_waker);
        off=交还给总控当 subagent 继续驱动。用户可随时切换。"""
        sess = self._sessions.get(sid)
        if sess is None:
            raise KeyError(sid)
        sess.taken_over = bool(on)
        self._persist_queued(sess)
        self._emit_session_event(
            sess, "chat.session.takeover", {"taken_over": sess.taken_over}, tags=["session", "takeover"],
        )
        return {"session_id": sid, "taken_over": sess.taken_over, "adopted": sess.adopted}

    def rename(self, sid: str, name: str) -> dict[str, Any]:
        """重命名 session；显示名是产品不变量，不允许回退到无意义 id。"""
        sess = self._sessions.get(sid)
        if sess is None:
            raise KeyError(sid)
        normalized = (name or "").strip()[:120]
        if not normalized:
            raise ValueError("会话名称不能为空")
        sess.name = normalized
        self._persist_queued(sess)
        self._emit_session_event(
            sess,
            "chat.session.renamed",
            {"name": sess.name},
            tags=["session", "metadata"],
        )
        return {"session_id": sid, "name": sess.name}

    def patch_metadata(
        self,
        sid: str,
        *,
        archived: bool | None = None,
        favorite: bool | None = None,
        model: str | None | object = _UNSET,
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        sess = self._sessions.get(sid)
        if sess is None:
            raise KeyError(sid)
        if archived is not None:
            sess.archived = bool(archived)
        if favorite is not None:
            sess.favorite = bool(favorite)
        if model is not _UNSET:
            sess.model = _normalize_session_model(model)
            if sess.provider_impl is not None:
                if sess.model:
                    sess.provider_impl.options["model"] = sess.model
                else:
                    sess.provider_impl.options.pop("model", None)
        if permission_mode is not None:
            sess.current_permission_mode = _normalize_permission_mode(permission_mode)
            if sess.provider_impl is not None:
                sess.provider_impl.options["permission_mode"] = sess.current_permission_mode
        self._persist_queued(sess)
        payload = {
            "archived": sess.archived,
            "favorite": sess.favorite,
            "model": sess.model or "(local default)",
            "permission_mode": sess.current_permission_mode,
        }
        self._emit_session_event(
            sess,
            "chat.session.metadata.updated",
            payload,
            tags=["session", "metadata"],
        )
        return {"session_id": sid, **payload, "effective": "next_user_turn"}

    async def set_effort(self, sid: str, effort: str | None) -> dict[str, Any]:
        """N2b 设推理强度档 (low/medium/high/xhigh/max, None=默认)。

        effort 是 claude binary 的连接时选项 (SDK 无运行时 set_effort), 所以:
          - 会话还没连 client → 存档, 下次连接 (_ensure_runtime / _connect_controller_claude) 生效。
          - claude client 在跑且空闲 → 当场断开 client (resume 保历史), 下轮自动重连应用新档。
          - claude client 在途 (in_flight) → 挂 _pending_effort_reconnect, 下轮 submit 前重连。
          - codex → 重建 CodexProvider, 新 thread/resume 时把档位传给
            ThreadOptions.model_reasoning_effort。
        """
        sess = self._sessions.get(sid)
        if sess is None:
            raise KeyError(sid)
        norm = _normalize_effort(effort, sess.provider)  # 非法值抛 ValueError
        changed = norm != sess.effort
        sess.effort = norm
        applied = "unchanged"
        if changed:
            if sess.provider == "codex" and sess.provider_impl is not None and not sess.in_flight_turn:
                async with sess.submit_lock:
                    await sess.provider_impl.disconnect()
                    if sess.provider_consume_task is not None:
                        sess.provider_consume_task.cancel()
                        await asyncio.gather(sess.provider_consume_task, return_exceptions=True)
                    sess.provider_impl = None
                    sess.provider_consume_task = None
                applied = "reconnected"
            elif sess.provider == "codex" and sess.provider_impl is not None:
                sess._pending_effort_reconnect = True
                applied = "after_current_turn"
            elif sess.client is not None and not sess.in_flight_turn:
                async with sess.submit_lock:
                    try:
                        await sess.client.disconnect()
                    except Exception:  # noqa: BLE001
                        logger.debug("set_effort disconnect ignored", exc_info=True)
                    sess.client = None
                applied = "reconnected"
            elif sess.client is not None:
                sess._pending_effort_reconnect = True
                applied = "after_current_turn"
            else:
                applied = "next_turn"
        await self._persist(sess)
        self._emit_session_event(
            sess,
            "chat.session.effort.updated",
            {"effort": sess.effort, "applied": applied},
            tags=["session", "metadata", "effort"],
        )
        return {"session_id": sid, "effort": sess.effort, "applied": applied}

    def _persist_snapshot(self, sess: CcChatSession) -> tuple:
        """在事件循环上取会话持久化快照 (纯内存读, 微秒级)。

        必须先快照再下线程: 线程里直接读 sess.event_history 的话, 循环上并发的
        append/replace 会让落盘内容不确定; list/dict 浅拷贝即可 — 元素 dict 从不原地改
        (_append_event_history 用 {**old, **new} 替换式更新)。
        """
        return (
            sess.id,
            sess.to_meta(),
            list(sess.history_summary),
            list(sess.event_history),
            dict(sess.goal_state) if sess.goal_state else None,
            dict(sess.last_token_budget) if sess.last_token_budget else None,
        )

    def _persist_write(self, snap: tuple) -> None:
        """全量读-改-写 cc_sessions.json (~145ms 磁盘 IO) — 只在持久化线程里跑。

        persist 失败不抛: 内存态是真源, 落盘只是恢复锚点, 不能反过来拖死业务。"""
        sid, meta, history_summary, event_history, goal_state, last_token_budget = snap
        try:
            _write_meta_store({sid: {
                **meta,
                "kind": "chat",
                "history_summary": history_summary,
                "event_history": self._dedupe_event_history(event_history),
                "goal_state": goal_state or None,
                "last_token_budget": last_token_budget,
            }})
        except Exception:  # noqa: BLE001
            logger.exception("cc_chat_bridge: persist session %s failed", sid)

    async def _persist(self, sess: CcChatSession) -> None:
        """持久化会话元数据。重 IO 挪 _PERSIST_EXECUTOR (单 worker, 提交序=执行序);
        逐次 await 调用即保持逐条顺序语义, 不并发。"""
        snap = self._persist_snapshot(sess)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_PERSIST_EXECUTOR, self._persist_write, snap)

    def _persist_queued(self, sess: CcChatSession) -> None:
        """sync 上下文 (rename/patch_metadata 等低频元数据方法) 的持久化出口:
        有运行中的 loop → 投持久化线程不堵循环; 无 loop (单测/脚本) → 就地同步写,
        保持旧的"调用完即可见"语义。"""
        snap = self._persist_snapshot(sess)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._persist_write(snap)
            return
        loop.run_in_executor(_PERSIST_EXECUTOR, self._persist_write, snap)

    def _goal_snapshot(self, sess: CcChatSession) -> dict[str, Any] | None:
        goal = sess.goal_state or {}
        objective = str(goal.get("objective") or "").strip()
        if not objective:
            return None
        return {
            "objective": objective,
            "status": str(goal.get("status") or "active"),
            "token_budget": goal.get("token_budget"),
            "auto": bool(goal.get("auto")),
            "iterations": int(goal.get("iterations") or 0),
            "max_iterations": int(goal.get("max_iterations") or GOAL_MAX_ITERATIONS_DEFAULT),
            "created_at": goal.get("created_at"),
            "updated_at": goal.get("updated_at"),
        }

    def _goal_context_lines(self, sess: CcChatSession) -> list[str]:
        goal = self._goal_snapshot(sess)
        if not goal:
            return ["", "## Session goal", "- No active session goal is set."]
        lines = ["", "## Session goal"]
        lines.append(f"- **objective**: {goal['objective']}")
        lines.append(f"- **status**: {goal['status']}")
        if goal.get("token_budget"):
            lines.append(f"- **token_budget**: {goal['token_budget']}")
        if goal.get("auto"):
            # N2c 续发循环: 告诉 agent 完成时如何收尾 (输出 GOAL_DONE 停循环)。
            lines.append(
                f"- **持续目标(自动续发 {goal.get('iterations', 0)}/{goal.get('max_iterations')})**: "
                f"每轮结束我会自动让你继续推进, 不要停下来等用户。"
                f"目标**完整**达成时, 回复以 `{GOAL_DONE_SENTINEL}` 开头并简述, 这会停止自动续发。"
            )
        lines.append("- Treat this as OmniChat session state; it applies to every provider in this chat.")
        return lines

    async def _broadcast_goal_event(self, sess: CcChatSession, *, action: str) -> None:
        goal = self._goal_snapshot(sess)
        if goal:
            summary = f"Goal {action}: {goal['status']} · {goal['objective']}"
        else:
            summary = "Goal cleared: no active session goal"
        frame = {
            "kind": "context_event",
            "status": f"goal_{action}",
            "summary": summary,
            "context": {
                "kind": "goal",
                "goal": goal,
                "total": 0,
                "contexts": [],
                "missing_total": 0,
            },
            "planId": sess.active_plan,
        }
        await self._append_event_history(sess, frame)
        await self._broadcast(sess, frame)

    async def set_goal(
        self,
        sid: str,
        *,
        objective: str,
        status: str = "active",
        token_budget: int | None = None,
        auto: bool = True,
        max_iterations: int | None = None,
    ) -> dict[str, Any]:
        sess = self._sessions.get(sid)
        if not sess:
            raise KeyError(sid)
        now = time.time()
        sess.goal_state = {
            "objective": objective.strip(),
            "status": status,
            "token_budget": token_budget,
            # N2c: auto=自动续发循环(默认开); iterations=已续发轮数; max_iterations=封顶防失控。
            "auto": bool(auto),
            "iterations": 0,
            "max_iterations": int(max_iterations) if max_iterations else GOAL_MAX_ITERATIONS_DEFAULT,
            "created_at": sess.goal_state.get("created_at") or now,
            "updated_at": now,
        }
        sess._last_injected_plan = self._NEVER_INJECTED
        await self._persist(sess)
        await self._broadcast_goal_event(sess, action="set")
        # N2c: auto 模式 + active → 立即开跑 (idle 直接 kick 第一轮; 在途则当前轮完成时接管)。
        if auto and status == "active":
            asyncio.create_task(self._kickoff_goal(sess))
        return {"session_id": sid, "goal_state": sess.goal_state}

    async def _kickoff_goal(self, sess: CcChatSession) -> None:
        """/goal set(auto) 后启动持续目标: 会话空闲就 kick 第一轮; 在途则不动
        (当前轮 finish_receive 会接管续发)。"""
        if sess.in_flight_turn:
            return
        try:
            await self._continue_goal_if_active(sess, "")
        except Exception:  # noqa: BLE001
            logger.exception("goal kickoff failed for %s", sess.id)

    def _goal_is_auto_active(self, sess: CcChatSession) -> bool:
        g = sess.goal_state or {}
        return (
            bool(g.get("auto"))
            and str(g.get("status") or "") == "active"
            and bool(str(g.get("objective") or "").strip())
        )

    async def _pause_goal_on_interrupt(self, sess: CcChatSession) -> None:
        if not self._goal_is_auto_active(sess):
            return
        sess.goal_state["status"] = "paused"
        sess.goal_state["updated_at"] = time.time()
        await self._persist(sess)
        await self._broadcast_goal_event(sess, action="paused")

    async def _continue_goal_if_active(self, sess: CcChatSession, last_text: str) -> bool:
        """一轮结束后判断持续目标: 检测完成信号→complete; 超轮→paused; 否则自动续发一轮。

        返回是否真的续发了。续发用 submit_user_prompt(record_history=False) — 不进可见历史,
        但广播一条状态行让用户看到"自动续发 N/max"。循环靠每轮 finish_receive 再次回调本方法,
        直到 agent 文本含 GOAL_DONE 或达到 max_iterations。
        """
        if not self._goal_is_auto_active(sess):
            return False
        if sess.ended_at is not None:
            return False
        g = sess.goal_state
        # 1) 完成信号: agent 在末段文本明确声明 GOAL_DONE。
        if GOAL_DONE_SENTINEL in (last_text or ""):
            g["status"] = "complete"
            g["updated_at"] = time.time()
            await self._persist(sess)
            await self._broadcast_goal_event(sess, action="complete")
            return False
        # 2) 轮数上限。
        iters = int(g.get("iterations") or 0) + 1
        maxi = int(g.get("max_iterations") or GOAL_MAX_ITERATIONS_DEFAULT)
        g["iterations"] = iters
        g["updated_at"] = time.time()
        if iters > maxi:
            g["status"] = "paused"
            await self._persist(sess)
            await self._broadcast_goal_event(sess, action="paused")
            await self._broadcast(sess, {
                "kind": "status",
                "text": "goal_paused",
                "summary": f"[持续目标] 已达最大续发轮数 {maxi}, 自动暂停。可 /goal complete 或重新 /goal set。",
                "canInterrupt": False,  # 终态通知, 非在途 turn — 别让前端卡 loading
            })
            return False
        await self._persist(sess)
        # 3) 续发一轮。
        await self._broadcast(sess, {
            "kind": "status",
            "text": "goal_continue",
            "summary": f"[持续目标] 自动续发 第 {iters}/{maxi} 轮 · {str(g.get('objective') or '')[:60]}",
        })
        await asyncio.sleep(GOAL_CONTINUE_DELAY_SEC)
        prompt = (
            f"[持续目标 · 自动续发 {iters}/{maxi}] 当前持续目标:\n{g.get('objective')}\n\n"
            f"请继续推进下一步, 不要停下来等我。\n"
            f"- 若目标已**完整**达成, 回复以 `{GOAL_DONE_SENTINEL}` 开头并简述完成情况(这会停止自动续发)。\n"
            f"- 否则直接动手做下一步具体工作。"
        )
        await self.submit_user_prompt(sess, prompt, record_history=False)
        return True

    async def update_goal_status(self, sid: str, *, status: str) -> dict[str, Any]:
        sess = self._sessions.get(sid)
        if not sess:
            raise KeyError(sid)
        if not self._goal_snapshot(sess):
            raise ValueError("no active session goal")
        sess.goal_state["status"] = status
        sess.goal_state["updated_at"] = time.time()
        sess._last_injected_plan = self._NEVER_INJECTED
        await self._persist(sess)
        await self._broadcast_goal_event(sess, action=status)
        return {"session_id": sid, "goal_state": sess.goal_state}

    async def clear_goal(self, sid: str) -> dict[str, Any]:
        sess = self._sessions.get(sid)
        if not sess:
            raise KeyError(sid)
        sess.goal_state = {}
        sess._last_injected_plan = self._NEVER_INJECTED
        await self._persist(sess)
        await self._broadcast_goal_event(sess, action="clear")
        return {"session_id": sid, "goal_state": None}

    async def compact_controller(self, sid: str) -> dict[str, Any]:
        """压缩上下文(用户明示 2026-06-03): 把旧总控会话的历史**折叠成一条种子**, 新开一个干净
        总控会话并以该种子起步, 归档旧会话。

        原理: 旧 claude_code 会话背着全部多轮历史(含所有工具调用/思考), token 越积越多。新会话只带
        一条折叠后的对话记录作为上下文 —— claude 侧上下文真减小, 又不丢要点。确定性, 不依赖 LLM 往返。
        """
        old = self._sessions.get(sid)
        if not old:
            raise KeyError(sid)
        # 折叠旧历史为一条文本(截近 200 条 / 每条 1500 字 / 总 40000 字封顶)
        lines: list[str] = []
        for h in (old.history_summary or [])[-200:]:
            role = h.get("role", "?")
            text = (h.get("text") or "").strip()
            if not text:
                continue
            lines.append(f"[{role}] {text[:1500]}")
        transcript = "\n".join(lines)[:40000] or "(旧会话无可折叠历史)"
        # 新开干净总控
        new = await self.create(provider="controller", cwd=old.cwd)
        # 归档旧会话(保留落盘, 不删)
        old.archived = True
        await self._persist(old)
        self._emit_session_event(old, "chat.session.compacted", {"new_session": new.id}, tags=["session"])
        # 用折叠记录给新会话起步(controller 读后简短确认, 一轮)
        seed = (
            "[上下文压缩 — 上一总控会话折叠记录]\n"
            "以下是上一个总控会话的对话记录(细节已折叠以节省上下文)。请快速读一遍、简短确认你已掌握当前进展与未完成事项, "
            "之后我们基于此继续。\n\n" + transcript
        )
        await self.submit_user_prompt(new, seed, record_history=True)
        return new.to_meta()

    def _build_context_frame(
        self,
        sess: CcChatSession,
        *,
        trigger: str,
        switched: bool = False,
    ) -> dict[str, Any]:
        from .context_progressive import build_context_frame

        return build_context_frame(
            session_id=sess.id,
            active_plan=sess.active_plan,
            cwd=sess.cwd,
            trigger=trigger,
            switched=switched,
        )

    async def _broadcast_context_event(
        self,
        sess: CcChatSession,
        *,
        trigger: str,
        switched: bool = False,
        frame: dict[str, Any] | None = None,
    ) -> None:
        event_frame = frame or self._build_context_frame(sess, trigger=trigger, switched=switched)
        bundle = event_frame.get("context") or {}
        total = int(bundle.get("total") or 0)
        missing_total = int(bundle.get("missing_total") or 0)
        plan_id = event_frame.get("plan_id") or sess.active_plan
        status = str(event_frame.get("trigger") or trigger)
        label = (
            "上下文已切换"
            if status == "plan_switch"
            else "上下文已注入"
            if status == "turn_injection"
            else "上下文已解析"
        )
        event_id = str(event_frame.get("id") or f"context_{sess.id}_{len(sess.event_history)}")
        summary = (
            f"{label}: {total} 项"
            + (f", 缺失 {missing_total} 项" if missing_total else "")
            + (f" · {plan_id}" if plan_id else " · 未绑定 plan")
        )
        event_frame["id"] = event_id
        event_frame["summary"] = summary
        await self._append_event_history(sess, {
            "id": event_id,
            "kind": "context_event",
            "status": status,
            "summary": summary,
            "context": bundle,
            "planId": plan_id,
        })
        await self._broadcast(sess, event_frame)

    def schedule_context_event(
        self,
        sess: CcChatSession,
        *,
        trigger: str,
        switched: bool = False,
    ) -> None:
        if sess.ended_at is not None:
            return
        try:
            asyncio.create_task(
                self._broadcast_context_event(sess, trigger=trigger, switched=switched)
            )
        except RuntimeError:
            pass

    def _build_plan_context(
        self,
        sess: CcChatSession,
        *,
        switched: bool,
    ) -> tuple[str, dict[str, Any]]:
        """构建注入到 user prompt 前的 plan 上下文文本.

        PTY 路线靠 Claude Code 二进制内置 hook 注入 (session_start.py /
        user_prompt_submit.py). Chat 路线不经 hook, 在 submit_user_prompt
        里直接前缀注入到 prompt, 等效实现.
        """
        from pathlib import Path
        plan_id = sess.active_plan
        context_frame = self._build_context_frame(
            sess,
            trigger="turn_injection",
            switched=switched,
        )
        context_bundle = context_frame.get("context") or {}
        if not plan_id:
            parts = [
                "<system-reminder>",
                "# omnicompany context\n",
                "No active plan is bound to this session.",
                "Pick one via the dashboard SessionContextPanel plan picker, "
                "or CLI: `omni plan list` -> `omni plan use <id>`.",
            ]
            parts.extend(self._goal_context_lines(sess))
            parts.append("</system-reminder>\n")
            return "\n".join(parts) + "\n", context_frame
        # chat.py 在 src/omnicompany/dashboard/ccdaemon/ → 5 层到项目根
        root = Path(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))
        plan_md = root / "docs" / "plans" / plan_id / "plan.md"
        plan_meta: dict[str, Any] = {}
        plan_head = ""
        if plan_md.is_file():
            try:
                from ..controlplane.plans import parse_plan_frontmatter
                plan_meta = parse_plan_frontmatter(plan_md) or {}
            except Exception:
                pass
            try:
                full = plan_md.read_text(encoding="utf-8", errors="replace")
                lines = full.splitlines()
                head_cut = 80
                for i, ln in enumerate(lines[:200]):
                    if i > 5 and ln.startswith("## "):
                        head_cut = min(head_cut, i)
                        break
                plan_head = "\n".join(lines[:head_cut])
            except OSError:
                pass

        tag = "plan switched" if switched else "active plan"
        parts = [
            "<system-reminder>",
            f"# omnicompany context ({tag})\n",
            f"active plan: `{plan_id}`",
        ]
        if plan_meta:
            parts.append("")
            for k in ("work_type", "project", "status", "phase", "expected_completion"):
                v = plan_meta.get(k)
                if v:
                    parts.append(f"- **{k}**: {v}")
            standards = plan_meta.get("standards") or []
            if standards:
                parts.append(f"- **standards**: {', '.join(standards)}")
            exit_criteria = plan_meta.get("exit_criteria") or []
            if exit_criteria:
                parts.append("- **exit_criteria**:")
                for ec in exit_criteria:
                    parts.append(f"  - {ec}")
            title = plan_meta.get("title")
            if title:
                parts.insert(3, f"title: {title}")
        if plan_head and not switched:
            parts.append(f"\n## plan.md (head)\n\n{plan_head}")
        contexts = context_bundle.get("contexts") or []
        if contexts:
            parts.append("\n## Progressive context bundle")
            parts.append(
                "Resolved by `omni context resolve`; read these paths when the task touches their category."
            )
            for item in contexts[:40]:
                path = item.get("path")
                category = item.get("category")
                source = item.get("source")
                reason = item.get("reason")
                parts.append(f"- `{path}` [{category}; {source}; {reason}]")
            if len(contexts) > 40:
                parts.append(f"- ... {len(contexts) - 40} more context paths")
        missing = context_bundle.get("missing") or []
        if missing:
            parts.append("\n## Missing context paths")
            for item in missing[:10]:
                parts.append(f"- `{item.get('path')}` [{item.get('source')}]")
        parts.extend(self._goal_context_lines(sess))
        parts.append(
            "\n_Use `omni plan show <id>` for full frontmatter "
            "or open `docs/plans/<id>/plan.md` for the prose._"
        )
        parts.append("</system-reminder>\n")
        return "\n".join(parts) + "\n", context_frame

    _NEVER_INJECTED = "__NEVER_INJECTED__"

    def _maybe_inject_plan(self, sess: CcChatSession, prompt: str) -> tuple[str, dict[str, Any] | None, bool]:
        """如果 plan 需要注入 (首条消息或 plan 切换), 前缀注入到 prompt 并更新追踪."""
        current = sess.active_plan
        last = sess._last_injected_plan
        if last != self._NEVER_INJECTED and current == last:
            return prompt, None, False  # 无变化, 不注入
        switched = last != self._NEVER_INJECTED  # 哨兵=首条消息, 其他=切换
        ctx, context_frame = self._build_plan_context(sess, switched=switched)
        sess._last_injected_plan = current
        logger.info("cc_chat: injected plan context for %s (plan=%s, switched=%s)",
                     sess.id, current, switched)
        return ctx + prompt, context_frame, switched

    async def _broadcast(self, sess: CcChatSession, frame: dict[str, Any]) -> None:
        self._emit_raw_frame(sess, frame)
        for q in list(sess.subscribers):
            q.put_nowait(frame)

    # ── 主对话流程 ──

    async def _broadcast_turn_error(self, sess: CcChatSession, code: str, message: str) -> None:
        await self._append_event_history(sess, {
            "kind": "error",
            "error": message,
            "sessionId": sess.id,
        })
        await self._broadcast(sess, {
            "kind": "error",
            "code": code,
            "message": message,
        })
        await self._broadcast(sess, {
            "kind": "result",
            "is_error": True,
            "session_id": sess.claude_session_id or sess.id,
            "duration_ms": 0,
            "duration_api_ms": 0,
            "num_turns": 0,
            "total_cost_usd": 0,
        })

    async def submit_user_prompt(self, sess: CcChatSession, prompt: str, *, record_history: bool = True) -> None:
        """提交一条 user prompt. 按 provider 字段路由到路径 A (SDK 直连) 或 B (BaseProvider).

        路径 A (claude_code): 把 claude-agent-sdk yield 的消息直转 WS 帧 (老路径).
        路径 B (omni_agent / codex): 调 provider.send_prompt(), consume task 已经在跑.
        """
        # 记 history (用原始 prompt, 不含注入前缀)
        self._emit_session_event(sess, "chat.input.user.requested", {
            "content": prompt,
            "record_history": record_history,
        }, tags=["input", "user"])

        # Chat 路线 plan 注入: PTY 路线靠 claude binary hook 自动注入,
        # Chat 路线不经 hook, 在此处前缀注入到 prompt.
        effective_prompt, context_frame, context_switched = self._maybe_inject_plan(sess, prompt)
        injected_context = effective_prompt[: max(len(effective_prompt) - len(prompt), 0)] if effective_prompt != prompt else ""

        # N2b: 有挂起的 effort 改档 (改档时会话在途) → 本轮开始前断开旧 claude client,
        # 让下面 _ensure_runtime 用新 effort resume 重连 (codex client 为 None, 自动跳过)。
        if sess._pending_effort_reconnect and sess.client is not None and not sess.in_flight_turn:
            sess._pending_effort_reconnect = False
            try:
                await sess.client.disconnect()
            except Exception:  # noqa: BLE001
                logger.debug("pending effort reconnect disconnect ignored", exc_info=True)
            sess.client = None
        if sess._pending_effort_reconnect and sess.provider_impl is not None and not sess.in_flight_turn:
            sess._pending_effort_reconnect = False
            await sess.provider_impl.disconnect()
            if sess.provider_consume_task is not None:
                sess.provider_consume_task.cancel()
                await asyncio.gather(sess.provider_consume_task, return_exceptions=True)
            sess.provider_impl = None
            sess.provider_consume_task = None

        try:
            await self._ensure_runtime(sess)
        except Exception as e:
            logger.exception("cc_chat_bridge: runtime restore failed for %s", sess.id)
            await self._broadcast_turn_error(sess, type(e).__name__, str(e))
            return

        # 路径 B: provider abstraction
        if sess.provider_impl is not None:
            try:
                turn_options: dict[str, Any] = {
                    "permission_mode": sess.current_permission_mode,
                }
                if sess.model:
                    turn_options["model"] = sess.model
                if sess.effort:
                    turn_options["effort"] = sess.effort
                await sess.provider_impl.send_prompt(effective_prompt, turn_options)
                sess.history_summary.append({"role": "user", "text": prompt})
                self._emit_session_event(sess, "chat.input.user.accepted", {
                    "content": prompt,
                    "record_history": record_history,
                }, tags=["input", "user", "accepted"])
                if injected_context:
                    self._emit_session_event(sess, "chat.input.context.accepted", {
                        "content": injected_context,
                    }, tags=["input", "context", "accepted"])
                    await self._broadcast_context_event(
                        sess,
                        trigger="turn_injection",
                        switched=context_switched,
                        frame=context_frame,
                    )
                if record_history:
                    await self._append_event_history(sess, {
                        "kind": "text",
                        "role": "user",
                        "content": prompt,
                        "sessionId": sess.id,
                    })
            except Exception as e:
                logger.exception("provider send_prompt failed for %s", sess.id)
                await self._broadcast_turn_error(sess, type(e).__name__, str(e))
            return

        # 路径 A: 老 SDK 直连
        if sess.client is None:
            # 重启/掉线后惰性重连(resume via claude_session_id), 与路径 B 的 _ensure_runtime 对齐,
            # 而不是直接抛 "client is not connected" 把会话变成死的。
            try:
                await self._ensure_runtime(sess)
            except Exception as e:  # noqa: BLE001
                logger.exception("claude_code lazy reconnect failed for %s", sess.id)
                await self._broadcast_turn_error(sess, "client_reconnect_failed", f"重连失败: {e}")
                return
        if sess.client is None:
            await self._broadcast_turn_error(sess, "client_not_connected", "client is not connected")
            return

        async with sess.submit_lock:
            try:
                # Continue with Claude's real session id after the first init frame.
                # Using our wrapper id here creates a parallel Claude session and can
                # make later turns replay or attach to the wrong transcript.
                target_session_id = sess.claude_session_id or sess.id
                running = bool(sess.current_receive_task and not sess.current_receive_task.done())
                if sess.in_flight_turn and not running:
                    sess.in_flight_turn = False

                if running:
                    sess.pending_interrupt_prompts.append(prompt)
                    sess.pending_interrupt_record_history.append(record_history)
                    try:
                        await sess.client.interrupt()
                    except Exception:
                        logger.exception("claude interrupt failed for %s", sess.id)
                        await self._broadcast(sess, {
                            "kind": "error",
                            "code": "interrupt_failed",
                            "message": "Failed to interrupt the active Claude turn; queued prompt will run after the current turn.",
                        })
                    return

                sess.in_flight_turn = True
                await sess.client.query(effective_prompt, session_id=target_session_id)
                sess.history_summary.append({"role": "user", "text": prompt})
                self._emit_session_event(sess, "chat.input.user.accepted", {
                    "content": prompt,
                    "record_history": record_history,
                    "target_provider_session_id": target_session_id,
                }, tags=["input", "user", "accepted"])
                if injected_context:
                    self._emit_session_event(sess, "chat.input.context.accepted", {
                        "content": injected_context,
                        "target_provider_session_id": target_session_id,
                    }, tags=["input", "context", "accepted"])
                    await self._broadcast_context_event(
                        sess,
                        trigger="turn_injection",
                        switched=context_switched,
                        frame=context_frame,
                    )
                if record_history:
                    await self._append_event_history(sess, {
                        "kind": "text",
                        "role": "user",
                        "content": prompt,
                        "sessionId": sess.id,
                    })

                async def consume() -> None:
                    assert sess.client is not None
                    last_assistant_text = ""
                    async for msg in sess.client.receive_response():
                        # 跟踪最近 assistant.model (token 百分比要除以本模型 context_window)
                        if isinstance(msg, casdk.AssistantMessage):
                            m = msg.model
                            if isinstance(m, str) and m:
                                sess._last_seen_model = m

                        # 路径 A 去返回: SDK 消息 → 上游 wire NormalizedMessage 直发
                        # (取代 _message_to_frame + 前端 ccSessionAdapter 翻译往返)
                        nms = _message_to_normalized(msg, sess)

                        if isinstance(msg, casdk.ResultMessage):
                            sess.in_flight_turn = False
                            d = dataclasses.asdict(msg)
                            u = d.get("usage") or {}
                            # 注 context_window 到 usage; per-request token (用户 2026-05-13 实测语义, 不换 cumulative)
                            model = getattr(sess, "_last_seen_model", None) or sess.model or ""
                            if isinstance(u, dict):
                                u["context_window"] = _resolve_context_window(model)
                            sess.cumulative_input_tokens += int(u.get("input_tokens", 0) or 0)
                            sess.cumulative_output_tokens += int(u.get("output_tokens", 0) or 0)
                            sess.cumulative_cache_creation_input_tokens += int(u.get("cache_creation_input_tokens", 0) or 0)
                            sess.cumulative_cache_read_input_tokens += int(u.get("cache_read_input_tokens", 0) or 0)
                            if isinstance(u, dict):
                                u["cumulative_input_tokens"] = sess.cumulative_input_tokens
                                u["cumulative_output_tokens"] = sess.cumulative_output_tokens
                                u["cumulative_cache_creation_input_tokens"] = sess.cumulative_cache_creation_input_tokens
                                u["cumulative_cache_read_input_tokens"] = sess.cumulative_cache_read_input_tokens
                                sess.last_token_budget = self._token_budget_from_usage(sess, u)
                            # 上游序: 先 token_budget status, 再 complete
                            if sess.last_token_budget:
                                await self._broadcast(sess, _finalize_nm(
                                    {"kind": "status", "text": "token_budget", "tokenBudget": sess.last_token_budget}, sess))
                            await self._broadcast(sess, _finalize_nm({
                                "kind": "complete",
                                "exitCode": 1 if d.get("is_error") else 0,
                                "isNewSession": False,
                                "actualSessionId": sess.id,
                            }, sess))
                            # N2c: 记本轮末段文本, 供持续目标续发循环检测 GOAL_DONE。
                            sess._last_turn_text = last_assistant_text
                            # 末轮 assistant 文本进 history_summary (供 snapshot)
                            if last_assistant_text.strip():
                                if (
                                    not sess.history_summary
                                    or sess.history_summary[-1].get("role") != "assistant"
                                    or sess.history_summary[-1].get("text") != last_assistant_text
                                ):
                                    sess.history_summary.append({"role": "assistant", "text": last_assistant_text})
                                    await self._append_event_history(sess, {
                                        "kind": "text", "role": "assistant", "content": last_assistant_text,
                                    })
                            # 块 3: 路径 A 完成 turn → emit subagent.completed (排除 controller 自唤)
                            if sess.provider != "controller":
                                self._emit_session_event(
                                    sess,
                                    "subagent.completed",
                                    {
                                        "subagent_id": sess.id,
                                        "provider": sess.provider,
                                        "model": sess.model,
                                        "active_plan": sess.active_plan,
                                        "verdict": "PASS",
                                        "last_assistant_preview": (last_assistant_text or "")[:500],
                                        "duration_ms": d.get("duration_ms"),
                                        "total_cost_usd": d.get("total_cost_usd"),
                                    },
                                    tags=["subagent", "lifecycle"],
                                )

                        # 直发本条 SDK 消息转出的 NM (session_created/text/thinking/tool_use/tool_result)
                        for nm in nms:
                            await self._broadcast(sess, _finalize_nm(nm, sess))

                        # SystemMessage(init) 绑定 claude session_id (SDK 0.1.x 装在 data dict)
                        if isinstance(msg, casdk.SystemMessage) and getattr(msg, "subtype", None) == "init":
                            d = dataclasses.asdict(msg)
                            sid_from_claude = (d.get("data") or {}).get("session_id")
                            if sid_from_claude and not sess.claude_session_id:
                                sess.claude_session_id = sid_from_claude
                                await self._persist(sess)
                                self._emit_session_event(sess, "chat.provider_session.bound", {
                                    "provider_session_id": sid_from_claude,
                                }, tags=["session", "provider_session"])

                        # AssistantMessage: 末轮 text 摘要 + thinking/tool_use 进 history (供 snapshot)
                        if isinstance(msg, casdk.AssistantMessage):
                            blocks = [_content_block_to_dict(b) for b in msg.content]
                            text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
                            thinking_parts = [b.get("thinking", "") for b in blocks if b.get("type") == "thinking"]
                            tool_use_parts = [b for b in blocks if b.get("type") in ("tool_use", "server_tool_use")]
                            if text_parts:
                                last_assistant_text = "".join(text_parts)
                            for thinking in thinking_parts:
                                await self._append_event_history(sess, {"kind": "thinking", "content": thinking})
                            for tu in tool_use_parts:
                                await self._append_event_history(sess, {
                                    "kind": "tool_use",
                                    "toolId": tu.get("id", ""),
                                    "toolName": tu.get("name", ""),
                                    "input": tu.get("input", {}),
                                })

                        # UserMessage: tool_result 进 history
                        if isinstance(msg, casdk.UserMessage):
                            content = msg.content
                            if isinstance(content, list):
                                for b in content:
                                    bd = _content_block_to_dict(b)
                                    if bd.get("type") in ("tool_result", "server_tool_result"):
                                        await self._append_event_history(sess, {
                                            "kind": "tool_result",
                                            "toolId": bd.get("tool_use_id", ""),
                                            "content": bd.get("content", ""),
                                            "isError": bd.get("is_error", False),
                                        })

                task = asyncio.create_task(consume())
                sess.current_receive_task = task

                def _clear_current_receive(done_task: asyncio.Task) -> None:
                    if sess.current_receive_task is done_task:
                        sess.current_receive_task = None
                        sess.in_flight_turn = False
                    interrupted = done_task.cancelled()
                    exc: BaseException | None = None
                    if not interrupted:
                        exc = done_task.exception()

                    next_prompt: str | None = None
                    next_record_history = True
                    if sess.pending_interrupt_prompts:
                        next_prompt = sess.pending_interrupt_prompts.pop(0)
                        if sess.pending_interrupt_record_history:
                            next_record_history = sess.pending_interrupt_record_history.pop(0)

                    async def finish_receive() -> None:
                        try:
                            if interrupted:
                                await self._broadcast_turn_error(sess, "interrupted", "user interrupted")
                                # N2c: 用户中断 = 停掉自动续发, 把持续目标置 paused (可再 /goal set 重启)。
                                await self._pause_goal_on_interrupt(sess)
                            elif exc is not None:
                                logger.exception("cc_chat_bridge: receive task failed for %s", sess.id, exc_info=exc)
                                await self._broadcast_turn_error(sess, type(exc).__name__, str(exc))
                            if next_prompt is not None:
                                await self.submit_user_prompt(
                                    sess,
                                    next_prompt,
                                    record_history=next_record_history,
                                )
                            elif not interrupted and exc is None:
                                # N2c 持续目标: 本轮无排队 prompt + 未中断/未出错 → 判断是否自动续发。
                                try:
                                    await self._continue_goal_if_active(sess, sess._last_turn_text)
                                except Exception:  # noqa: BLE001
                                    logger.exception("goal auto-continue failed for %s", sess.id)
                        finally:
                            await self._persist(sess)

                    asyncio.create_task(finish_receive())

                task.add_done_callback(_clear_current_receive)

            except asyncio.CancelledError:
                sess.in_flight_turn = False
                await self._broadcast_turn_error(sess, "interrupted", "user interrupted")
                raise
            except Exception as e:
                logger.exception("cc_chat_bridge: query/receive failed for %s", sess.id)
                sess.in_flight_turn = False
                await self._broadcast_turn_error(sess, type(e).__name__, str(e))
                await self._persist(sess)

    async def interrupt(self, sess: CcChatSession) -> None:
        # 路径 A
        if sess.client is not None:
            try:
                await sess.client.interrupt()
            except Exception as e:
                logger.warning("cc_chat_bridge: SDK interrupt failed for %s: %s", sess.id, e)
        # 路径 B
        if sess.provider_impl is not None:
            try:
                await sess.provider_impl.interrupt()
            except Exception as e:
                logger.warning("cc_chat_bridge: provider interrupt failed for %s: %s", sess.id, e)


# ── 把 SDK 消息对象转成 WS 帧 (JSON 可序列化 dict) ────────────────────────────


def _content_block_to_dict(block: Any) -> dict[str, Any]:
    """TextBlock / ThinkingBlock / ToolUseBlock / ToolResultBlock /
    ServerToolUseBlock / ServerToolResultBlock — 各类带 type 标签的 dict."""
    if isinstance(block, casdk.TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, casdk.ThinkingBlock):
        return {"type": "thinking", "thinking": block.thinking, "signature": getattr(block, "signature", "")}
    if isinstance(block, casdk.ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, casdk.ToolResultBlock):
        # content 可能是 str 或 list of content blocks; 都直转
        content = block.content
        if isinstance(content, list):
            content = [c if isinstance(c, (str, dict)) else _content_block_to_dict(c) for c in content]
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": content,
            "is_error": getattr(block, "is_error", None),
        }
    if isinstance(block, casdk.ServerToolUseBlock):
        return {"type": "server_tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, casdk.ServerToolResultBlock):
        return {"type": "server_tool_result", "tool_use_id": block.tool_use_id,
                "content": block.content, "is_error": getattr(block, "is_error", None)}
    # 兜底 fallback (新 block 类型时降级显示)
    return {"type": type(block).__name__, "raw": str(block)}


def _message_to_frame(msg: Any) -> dict[str, Any] | None:
    """claude-agent-sdk 5 种 message 类型 → JSON 可序列化 dict 帧."""
    if isinstance(msg, casdk.SystemMessage):
        # SystemMessage 有 subtype 字段 (e.g. 'init'); 用 dataclasses asdict 抓全字段
        d = dataclasses.asdict(msg)
        return {"kind": "system", **d}
    if isinstance(msg, casdk.AssistantMessage):
        # SDK 0.1.x AssistantMessage 仅 5 字段: content / model / parent_tool_use_id / error / usage
        # (历史代码引用 message_id / session_id / stop_reason / uuid 是基于 anthropic API 想象的字段,
        # 真 SDK 没有 — 2026-05-09 跟 SDK 0.1.50 对齐)
        return {
            "kind": "assistant",
            "content": [_content_block_to_dict(b) for b in msg.content],
            "model": msg.model,
            "parent_tool_use_id": msg.parent_tool_use_id,
            "error": msg.error,
            "usage": msg.usage,
        }
    if isinstance(msg, casdk.UserMessage):
        content = msg.content
        if isinstance(content, list):
            content = [_content_block_to_dict(b) for b in content]
        return {
            "kind": "user",
            "content": content,
            "uuid": msg.uuid,
            "parent_tool_use_id": msg.parent_tool_use_id,
            "tool_use_result": msg.tool_use_result,
        }
    if isinstance(msg, casdk.ResultMessage):
        return {"kind": "result", **dataclasses.asdict(msg)}
    if isinstance(msg, casdk.StreamEvent):
        # StreamEvent 可能 partial 内容; 整体 dump
        try:
            return {"kind": "stream_event", **dataclasses.asdict(msg)}
        except TypeError:
            return {"kind": "stream_event", "raw": str(msg)}
    if isinstance(msg, casdk.RateLimitEvent):
        try:
            return {"kind": "rate_limit", **dataclasses.asdict(msg)}
        except TypeError:
            return {"kind": "rate_limit", "raw": str(msg)}
    return None


# ── 直发上游 NormalizedMessage (聊天去返回: 取代 legacy 帧 + 前端翻译层) ──────
# 目标 = claudecodeui 上游前端期望的 wire NormalizedMessage。前端 useSessionStore /
# useChatRealtimeHandlers 本就是上游消费器, 后端只要直发这套形状即可, 不再经
# former frontend legacy-frame adapter 翻译。规格见
# docs/plans/dashboard/[2026-05-23]BOSS-SIGHT/聊天重建_特性保留与清理清单.md。


def _nm_provider(sess: CcChatSession) -> str:
    """对外 provider 名: claude_code → 'claude' (对齐上游), 其余透传。"""
    return "claude" if sess.provider == "claude_code" else sess.provider


def _finalize_nm(nm: dict[str, Any], sess: CcChatSession) -> dict[str, Any]:
    """补全 NormalizedMessage 信封 (对齐上游 createNormalizedMessage 缺省)。

    直发前过一遍: 补 id/sessionId/timestamp/provider。
    - stream_delta 用随机 id (绝不 __streaming_, 那是前端 store 私有 key)。
    - tool_use/tool_result 若已带稳定 id 则保留 (供 snapshot 去重)。
    - 对外 sessionId 用 wrapper id, 不暴露 claude UUID。
    """
    kind = nm.get("kind", "msg")
    nm.setdefault("id", f"{kind}_{uuid.uuid4().hex}")
    nm.setdefault("sessionId", sess.id)
    nm.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    nm.setdefault("provider", _nm_provider(sess))
    return nm


def _provider_nm_to_wire(nm: dict[str, Any]) -> dict[str, Any]:
    """路径 B: provider 产的 NormalizedMessage 字段名规整成上游 wire 态 (in-place)。

    provider 内部用 input/result/error, 上游前端要 toolInput/content/content;
    text 须带 role; session_created 须带 sessionId。
    """
    kind = nm.get("kind")
    if kind == "text":
        nm.setdefault("role", "assistant")
    elif kind == "tool_use":
        if "input" in nm and "toolInput" not in nm:
            nm["toolInput"] = nm.pop("input")
    elif kind == "tool_result":
        if "result" in nm and "content" not in nm:
            r = nm.pop("result")
            nm["content"] = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
    elif kind == "error":
        if "error" in nm and "content" not in nm:
            nm["content"] = nm.pop("error")
    elif kind == "session_created":
        nm.setdefault("sessionId", nm.get("newSessionId", ""))
    return nm


def _message_to_normalized(msg: Any, sess: CcChatSession) -> list[dict[str, Any]]:
    """claude-agent-sdk message → 上游 wire NormalizedMessage 列表 (路径 A)。

    取代 _message_to_frame 的"直发"用途。claude 路径不发 stream_delta (与原适配器一致:
    provider=='claude' 时 stream_event 丢弃, 文本由 AssistantMessage text block 整段带出,
    单气泡)。ResultMessage 的 token 注入 + complete 在 consume 循环里处理 (需 sess 累计)。
    """
    out: list[dict[str, Any]] = []
    if isinstance(msg, casdk.SystemMessage):
        if getattr(msg, "subtype", None) == "init":
            d = dataclasses.asdict(msg)
            new_sid = (d.get("data") or {}).get("session_id") or ""
            out.append({"kind": "session_created", "newSessionId": new_sid, "sessionId": new_sid or sess.id})
        return out
    if isinstance(msg, casdk.AssistantMessage):
        for b in msg.content:
            bd = _content_block_to_dict(b)
            t = bd.get("type")
            if t == "text":
                txt = bd.get("text", "")
                if txt:
                    out.append({"kind": "text", "role": "assistant", "content": txt})
            elif t == "thinking":
                out.append({"kind": "thinking", "content": bd.get("thinking", "")})
            elif t in ("tool_use", "server_tool_use"):
                tid = bd.get("id", "")
                nm: dict[str, Any] = {
                    "kind": "tool_use", "toolId": tid,
                    "toolName": bd.get("name", ""), "toolInput": bd.get("input", {}),
                }
                pid = getattr(msg, "parent_tool_use_id", None)
                if pid:
                    nm["parentToolUseId"] = pid
                if tid:
                    nm["id"] = f"tool_{sess.id}_{tid}_use"
                out.append(nm)
        return out
    if isinstance(msg, casdk.UserMessage):
        content = msg.content
        if isinstance(content, list):
            for b in content:
                bd = _content_block_to_dict(b)
                if bd.get("type") in ("tool_result", "server_tool_result"):
                    c = bd.get("content", "")
                    if not isinstance(c, str):
                        c = json.dumps(c, ensure_ascii=False)
                    tid = bd.get("tool_use_id", "")
                    nm = {"kind": "tool_result", "toolId": tid, "content": c,
                          "isError": bool(bd.get("is_error") or False)}
                    if tid:
                        nm["id"] = f"tool_{sess.id}_{tid}_result"
                    out.append(nm)
        return out
    if isinstance(msg, casdk.RateLimitEvent):
        try:
            d = dataclasses.asdict(msg)
        except TypeError:
            d = {"raw": str(msg)}
        out.append({"kind": "status", "text": "rate_limited", "tokenBudget": d})
        return out
    # ResultMessage / StreamEvent: consume 循环特殊处理 (token / 不发 stream_delta)
    return out


_manager: CcChatSessionManager | None = None


def get_chat_manager() -> CcChatSessionManager:
    global _manager
    if _manager is None:
        _manager = CcChatSessionManager()
    return _manager


# ── FastAPI 路由 ─────────────────────────────────────────────────────────────


cc_chat_router = APIRouter(prefix="/cc/chat", tags=["cc-chat"])


class CreateChatSessionBody(BaseModel):
    cwd: str | None = Field(
        default=None,
        description="工作目录，默认 E:\\WindowsWorkspace；其他目录会记录在会话元数据中",
    )
    model: str | None = Field(default=None, description=f"模型短名, 默认 {DEFAULT_MODEL}")
    provider: str | None = Field(
        default="claude_code",
        description="LLM provider: claude_code / omni_agent / codex / codebuddy (CodeBuddy CLI) / kimi / opencode / controller",
    )
    # BOSS SIGHT 块 3: spawn subagent 用 — 创 session 后 fire-and-forget 发第一条 prompt
    initial_prompt: str | None = Field(
        default=None,
        description="可选: 创建 session 后立即发的第一条 user message (fire-and-forget, 异步处理). 用于 BOSS SIGHT 总控 spawn subagent 一次性 sync HTTP 启动."
    )
    # BOSS SIGHT 块 3: 总控 spawn 时标记自己是 controller 不是用户 (§5.3)
    from_controller: bool = Field(
        default=False,
        description="True 表示这是 BOSS SIGHT 总控调度的, 不是用户直接命令. initial_prompt 会被加前缀 [from: BOSS-SIGHT controller, not_user: true]"
    )
    active_plan: str | None = Field(
        default=None,
        description="可选: 关联 plan id (会写入 sess.active_plan, 后续工具调用受该 plan frontmatter guard 约束)"
    )
    # BOSS SIGHT 块 3 R7: fork 不打断汇报 (用户原话 §6.2)
    fork_from_provider_session_id: str | None = Field(
        default=None,
        description=(
            "可选: 源 claude/codex provider session id. 传入时新 session 走 fork_session=True, "
            "继承源对话历史但写新 session_id. 源 session 不受影响继续跑."
        ),
    )
    # #2 接管式采纳: resume 别处已有会话(同 session_id)接管它当 subagent。与 fork 区别: fork 新开不动源, adopt 接管同一会话。
    adopt_session_id: str | None = Field(
        default=None,
        description="可选: 采纳别处已有 claude/codex 会话的 session_id —— resume 同一会话接管它当 subagent(自动 caller_identity=subagent)。",
    )
    # N2b 推理强度档: low/medium/high/xhigh/max, 缺省/None=用模型默认。
    effort: str | None = Field(
        default=None,
        description="可选: 推理强度档 (low/medium/high/xhigh/max), None=用模型默认。透传给 claude binary 的 effort。",
    )


@cc_chat_router.get("/health")
async def chat_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "default_model": DEFAULT_MODEL or "(local claude default)",
        "session_count": len(get_chat_manager().list_meta()),
        "default_cwd": default_session_cwd(),
        "claude_agent_sdk_version": getattr(casdk, "__version__", "?"),
        "note": "走本地 claude binary, 认证用 claude login 订阅, 不要 ANTHROPIC_API_KEY",
    }


@cc_chat_router.post("/sessions")
async def create_session(body: CreateChatSessionBody | None = None) -> dict[str, Any]:
    body = body or CreateChatSessionBody()
    try:
        sess = await get_chat_manager().create(
            cwd=body.cwd,
            model=body.model,
            provider=body.provider or "claude_code",
            fork_from_provider_session_id=body.fork_from_provider_session_id,
            adopt_session_id=body.adopt_session_id,
            # P0-a: 总控 spawn(from_controller)或采纳(adopt)的会话标记为 subagent → 子进程注入身份, 防递归 spawn
            caller_identity=(CALLER_SUBAGENT if (body.from_controller or body.adopt_session_id) else None),
            effort=body.effort,
        )
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))

    # 块 3: 关联 active_plan (frontmatter guard 自动生效)
    if body.active_plan:
        try:
            get_chat_manager().patch_active_plan(sess.id, body.active_plan)
        except Exception:  # noqa: BLE001
            logger.exception("cc_chat_bridge: patch_active_plan failed for %s", sess.id)

    # 块 3: subagent.spawned 事件 (string event_type, 不动 EventType enum)
    get_chat_manager()._emit_session_event(
        sess,
        "subagent.spawned",
        {
            "subagent_id": sess.id,
            "provider": sess.provider,
            "model": sess.model,
            "cwd": sess.cwd,
            "active_plan": sess.active_plan,
            "from_controller": bool(body.from_controller),
        },
        tags=["subagent", "lifecycle"],
    )

    # 块 3: fire-and-forget 发第一条 prompt (spawn 工具用 sync HTTP 调本端点即可)
    if body.initial_prompt:
        prompt_text = body.initial_prompt
        if body.from_controller:
            prompt_text = (
                "[from: BOSS-SIGHT controller, not_user: true]\n\n"
                + prompt_text
            )
        asyncio.create_task(
            get_chat_manager().submit_user_prompt(sess, prompt_text)
        )

    return sess.to_meta()


def _list_sessions_sync(
    q: str,
    full_text: bool,
    limit: int,
    offset: int,
    pinned_id: str | None,
    include_archived: bool,
) -> dict[str, Any]:
    """list_sessions 的同步实现体: list_meta_page 遍历全部会话 + (full_text 时)读历史消息,
    阻塞 I/O, 抽出来给 asyncio.to_thread 用, 不堵事件循环。"""
    return get_chat_manager().list_meta_page(
        q=q,
        full_text=full_text,
        limit=limit,
        offset=offset,
        pinned_id=pinned_id,
        include_archived=include_archived,
    )


@cc_chat_router.get("/sessions")
async def list_sessions(
    q: str = "",
    full_text: bool = False,
    limit: int = 60,
    offset: int = 0,
    pinned_id: str | None = None,
    include_archived: bool = False,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _list_sessions_sync, q, full_text, limit, offset, pinned_id, include_archived
    )


@cc_chat_router.delete("/sessions/{sid}")
async def kill_session(sid: str) -> dict[str, Any]:
    if not await get_chat_manager().kill(sid):
        raise HTTPException(404, f"session {sid} not found")
    return {"ok": True}


class TakeoverBody(BaseModel):
    on: bool = Field(default=True, description="True=用户接管(总控不自动 hook); False=交还给总控当 subagent")


@cc_chat_router.post("/sessions/{sid}/takeover")
async def set_takeover(sid: str, body: TakeoverBody) -> dict[str, Any]:
    """#2 接管/交还一个采纳来的会话。接管后总控对它不自动 hook(见 controller_waker)。"""
    try:
        return get_chat_manager().set_taken_over(sid, body.on)
    except KeyError:
        raise HTTPException(404, f"session {sid} not found") from None


class PatchActivePlanBody(BaseModel):
    plan_id: str | None = Field(default=None)


@cc_chat_router.patch("/sessions/{sid}/active_plan")
async def patch_active_plan(sid: str, body: PatchActivePlanBody) -> dict[str, Any]:
    try:
        return get_chat_manager().patch_active_plan(sid, body.plan_id)
    except KeyError:
        raise HTTPException(404, f"session {sid} not found")


class PatchGoalBody(BaseModel):
    action: str = Field(default="status", description="status | set | clear | complete | cancel | pause")
    objective: str | None = Field(default=None)
    status: str | None = Field(default=None)
    token_budget: int | None = Field(default=None)
    # N2c 持续目标: auto=自动续发循环(默认开); max_iterations=续发轮数上限。
    auto: bool = Field(default=True)
    max_iterations: int | None = Field(default=None)


@cc_chat_router.get("/sessions/{sid}/goal")
async def get_session_goal(sid: str) -> dict[str, Any]:
    sess = get_chat_manager().get(sid)
    if sess is None:
        raise HTTPException(404, f"session {sid} not found")
    return {"session_id": sid, "goal_state": sess.goal_state or None}


@cc_chat_router.post("/sessions/{sid}/goal")
async def patch_session_goal(sid: str, body: PatchGoalBody) -> dict[str, Any]:
    mgr = get_chat_manager()
    action = (body.action or "status").strip().lower()
    try:
        if action in {"set", "create"}:
            objective = (body.objective or "").strip()
            if not objective:
                raise HTTPException(400, "goal objective is required")
            return await mgr.set_goal(
                sid,
                objective=objective,
                status=body.status or "active",
                token_budget=body.token_budget,
                auto=body.auto,
                max_iterations=body.max_iterations,
            )
        if action == "clear":
            return await mgr.clear_goal(sid)
        if action in {"complete", "done"}:
            return await mgr.update_goal_status(sid, status="complete")
        if action in {"cancel", "pause"}:
            return await mgr.update_goal_status(sid, status=action)
        if action == "status":
            sess = mgr.get(sid)
            if sess is None:
                raise KeyError(sid)
            return {"session_id": sid, "goal_state": sess.goal_state or None}
    except KeyError:
        raise HTTPException(404, f"session {sid} not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    raise HTTPException(400, f"unsupported goal action: {action}")


class RenameSessionBody(BaseModel):
    name: str = Field(min_length=1, description="始终非空的中文优先会话显示名")


@cc_chat_router.patch("/sessions/{sid}/name")
async def rename_session(sid: str, body: RenameSessionBody) -> dict[str, Any]:
    try:
        return get_chat_manager().rename(sid, body.name)
    except KeyError:
        raise HTTPException(404, f"session {sid} not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class PatchSessionMetadataBody(BaseModel):
    archived: bool | None = None
    favorite: bool | None = None
    model: str | None = None
    permission_mode: str | None = None
    effort: str | None = None  # N2b 推理强度档 (low/medium/high/xhigh/max / "default"=清)


@cc_chat_router.post("/sessions/{sid}/compact")
async def compact_session(sid: str) -> dict[str, Any]:
    """压缩总控上下文: 折叠旧会话历史 → 新开干净总控 → 归档旧会话。返回新会话 meta。"""
    try:
        return await get_chat_manager().compact_controller(sid)
    except KeyError:
        raise HTTPException(404, f"session {sid} not found")


@cc_chat_router.patch("/sessions/{sid}/metadata")
async def patch_session_metadata(sid: str, body: PatchSessionMetadataBody) -> dict[str, Any]:
    try:
        model = body.model if "model" in body.model_fields_set else _UNSET
        res = get_chat_manager().patch_metadata(
            sid,
            archived=body.archived,
            favorite=body.favorite,
            model=model,
            permission_mode=body.permission_mode,
        )
        # N2b: effort 是连接时选项, 单独走 async set_effort (可能要断开重连)。
        if "effort" in body.model_fields_set:
            eff = await get_chat_manager().set_effort(sid, body.effort)
            res = {**res, "effort": eff.get("effort"), "effort_applied": eff.get("applied")}
        return res
    except KeyError:
        raise HTTPException(404, f"session {sid} not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@cc_chat_router.get("/sessions/{sid}/history")
async def get_session_history(sid: str) -> dict[str, Any]:
    """返 session history_summary 给前端 SessionStore.fetchFromServer 用.

    history_summary 是 [{role: 'user'|'assistant', text: '...'}] 简化记录, 由 ws
    snapshot 帧的源数据演化而来. ChatInterface (claudecodeui upstream) 需要
    NormalizedMessage 形态, 这里转换.
    """
    sess = get_chat_manager().get(sid)
    if sess is None:
        raise HTTPException(404, f"session {sid} not found")
    if sess.event_history:
        # 大会话的去重是 CPU 重活, 必须下线程 —— 同步跑会堵死整个 ccdaemon 事件循环
        # (本仓已知病类, 治法=to_thread; 移动端批量取标题时实测把 8201 卡到分钟级)。
        import asyncio as _asyncio

        messages = await _asyncio.to_thread(
            get_chat_manager()._dedupe_event_history, list(sess.event_history)
        )
        return {
            "messages": messages,
            "total": len(messages),
            "hasMore": False,
            "tokenUsage": sess.last_token_budget,
        }
    # history_summary → NormalizedMessage list
    messages: list[dict[str, Any]] = []
    import time as _time
    for i, h in enumerate(sess.history_summary):
        role = h.get("role", "assistant")
        text = h.get("text", "")
        if not text.strip():
            continue
        messages.append({
            "id": f"hist_{sid}_{i}",
            "sessionId": sid,
            "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(sess.started_at)),
            "provider": "claude" if sess.provider == "claude_code" else (sess.provider or "claude"),
            "kind": "text",
            "role": "user" if role == "user" else "assistant",
            "content": text,
        })
    return {
        "messages": messages,
        "total": len(messages),
        "hasMore": False,
        "tokenUsage": sess.last_token_budget,
    }


@cc_chat_router.websocket("/sessions/{sid}/ws")
async def chat_ws(ws: WebSocket, sid: str) -> None:
    await ws.accept()
    mgr = get_chat_manager()
    sess = mgr.get(sid)
    if sess is None:
        await ws.send_json({"kind": "error", "code": "not_found", "message": f"session {sid} not found"})
        await ws.close()
        return
    if sess.ended_at is not None:
        await ws.send_json({"kind": "exit", "reason": sess.exit_reason or "ended"})
        await ws.close()
        return

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    sess.subscribers.add(queue)

    # snapshot: 把 history_summary 喂给新订阅者
    try:
        # 大会话的去重是 CPU 重活, 照搬 get_session_history 的 to_thread 模式,
        # 同步跑会堵死整个 ccdaemon 事件循环 (2026-07 P0 批)。
        messages = await asyncio.to_thread(
            mgr._dedupe_event_history, list(sess.event_history)
        )
        await ws.send_json({
            "kind": "snapshot",
            "history": sess.history_summary,
            "messages": messages,
            "tokenUsage": sess.last_token_budget,
        })
    except Exception:
        logger.exception("cc_chat_bridge: snapshot send failed")

    async def producer() -> None:
        while True:
            frame = await queue.get()
            try:
                await ws.send_json(frame)
            except Exception:
                return
            if frame.get("kind") == "exit":
                return

    async def consumer() -> None:
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                return
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"kind": "error", "code": "bad_json", "message": "invalid JSON frame"})
                continue
            t = frame.get("type")
            if t == "user.message":
                content = frame.get("content")
                # 接受 string 或 content blocks list — MVP 先 string
                if isinstance(content, list):
                    text_parts = [b.get("text", "") for b in content
                                  if isinstance(b, dict) and b.get("type") == "text"]
                    text = "".join(text_parts)
                else:
                    text = str(content or "")
                if not text.strip():
                    continue
                # 用户切了 permission mode 这条消息要带新值, 更新 session 状态
                # (跟 ChatComposer 顶栏 cyclePermissionMode 同步)
                pm = frame.get("permissionMode")
                if pm:
                    try:
                        sess.current_permission_mode = _normalize_permission_mode(pm)
                    except ValueError as exc:
                        await ws.send_json({"kind": "error", "code": "bad_permission_mode", "message": str(exc)})
                        continue
                    if sess.provider_impl is not None:
                        sess.provider_impl.options["permission_mode"] = sess.current_permission_mode
                    await mgr._persist(sess)
                elif frame.get("skipPermissions"):
                    sess.current_permission_mode = "bypassPermissions"
                    if sess.provider_impl is not None:
                        sess.provider_impl.options["permission_mode"] = sess.current_permission_mode
                    await mgr._persist(sess)
                # fire-and-forget — submit_user_prompt 内部跑 receive_response, 不阻塞 ws 接收
                asyncio.create_task(mgr.submit_user_prompt(sess, text))
            elif t == "user.interrupt":
                await mgr.interrupt(sess)
            elif t == "session.permission_mode":
                try:
                    sess.current_permission_mode = _normalize_permission_mode(frame.get("permissionMode"))
                except ValueError as exc:
                    await ws.send_json({"kind": "error", "code": "bad_permission_mode", "message": str(exc)})
                    continue
                if sess.provider_impl is not None:
                    sess.provider_impl.options["permission_mode"] = sess.current_permission_mode
                await mgr._persist(sess)
                mgr._emit_session_event(
                    sess,
                    "chat.session.permission_mode.updated",
                    {"permission_mode": sess.current_permission_mode},
                    tags=["session", "metadata"],
                )
                # 不广播 status 帧: 这只是元数据 ack, composer 已本地反映新模式;
                # 发 kind:status 会被前端当成"正在响应"并卡住 loading(无 turn → 无 complete 清)。
            elif t == "session.model":
                sess.model = _normalize_session_model(frame.get("model"))
                if sess.provider_impl is not None:
                    if sess.model:
                        sess.provider_impl.options["model"] = sess.model
                    else:
                        sess.provider_impl.options.pop("model", None)
                await mgr._persist(sess)
                mgr._emit_session_event(
                    sess,
                    "chat.session.model.updated",
                    {"model": sess.model or "(local default)"},
                    tags=["session", "metadata"],
                )
                # 同 permission_mode: 元数据 ack 不广播 status 帧, 免卡 loading。
            elif t == "claude-permission-response":
                # 前端 grant/deny 工具调用 — resolve pending future, SDK 端继续/中断
                req_id = frame.get("requestId")
                fut = sess.pending_permissions.get(req_id) if req_id else None
                if fut and not fut.done():
                    fut.set_result({
                        "allow": bool(frame.get("allow")),
                        "updatedInput": frame.get("updatedInput"),
                        "message": frame.get("message"),
                    })
                    # 给前端广播 cancelled, 让横幅消失
                    await mgr._broadcast(sess, {
                        "kind": "permission_cancelled",
                        "requestId": req_id,
                    })
            else:
                # 静默丢未识别帧 (ChatComposer 内部状态查询类: check-session-status 等)
                # 不再回 error frame 避免污染前端 chat 列表
                logger.debug("cc_chat_bridge: unhandled ws frame type %r ignored", t)

    try:
        await asyncio.gather(producer(), consumer())
    except Exception:
        logger.exception("cc_chat_bridge: ws gather failed for %s", sid)
    finally:
        sess.subscribers.discard(queue)
        try:
            await ws.close()
        except Exception:
            pass
