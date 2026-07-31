# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-18
# [OMNI] material_id="material:core.agent.routers.llm_call.engine.py"
"""LLMCallRouter — agent.llm-request → agent.llm-response

职责：
1. 调用 LLMClient（qwen-3.6-plus / 指定模型）
2. 指数退避重试（429/529/overloaded/timeout/connection）
3. 解析 text / tool_use blocks
4. 输出标准 agent.llm-response Format（含 usage 审计）

设计说明：
- 复用 runtime/llm/llm.LLMClient — 不自建 LLMWrapper（plan §0.3 核心引擎铁律）
- 重试逻辑沿用旧 `_call_llm_with_retry` 的语义，配置走 RetryConfig
- tool_uses 解析时剥离 intent 等非工具字段（与既有行为一致，防幻觉不改）
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import random
import sys
from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router
from omnicompany.runtime.llm.llm import LLMClient
from omnicompany.runtime.agent.agent_loop_config import RetryConfig
from omnicompany.packages.services._core.agent._bus import (
    emit_agent_signal,
    emit_router_input,
    emit_router_output,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# tool_use / tool_result 配对验证 (CC normalizeMessagesForAPI 对齐, 2026-05-04)
# ═══════════════════════════════════════════════════════════════════════


def normalize_tool_pairs(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """规范化 messages 中 tool_use/tool_result 配对.

    Anthropic API 强制配对铁律:
      - 每个 assistant.tool_use(id=X) 必须**紧随** user 消息含 tool_result(tool_use_id=X)
      - 每个 user.tool_result(tool_use_id=X) 必须前置 assistant.tool_use(id=X)
      - **同一 assistant 的所有 tool_use 必须由紧跟的同一 user 全部应答**
      - 任一错配 → API 报 400 invalid_request_error

    策略:
      - orphan tool_result (无前置 tool_use) → drop 该 block (整 user msg 全 drop 则丢消息)
      - assistant 含 tool_use 但下个 user 不全应答 → 在该 user 的 content 列表加合成
        tool_result 块 (is_error=True, 提示 "auto-injected"). 若下个 message 不是 user
        list 则**先** insert 一条 synthetic user msg, 再继续.
      - 处理顺序: 先 drop orphan results, 再 inject synthetic results, 保持紧邻不变.

    返回 (normalized_messages, fix_events).
    """
    fix_events: list[dict] = []

    # 收集所有 tool_use_id (assistant 含的) 跟所有 tool_result_id (user 含的)
    tool_use_ids: set[str] = set()
    tool_result_ids: set[str] = set()
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for blk in content:
            if not isinstance(blk, dict):
                continue
            if role == "assistant" and blk.get("type") == "tool_use":
                tu_id = blk.get("id")
                if tu_id:
                    tool_use_ids.add(tu_id)
            elif role == "user" and blk.get("type") == "tool_result":
                tr_id = blk.get("tool_use_id")
                if tr_id:
                    tool_result_ids.add(tr_id)

    orphan_uses = tool_use_ids - tool_result_ids       # 需注入 synthetic
    orphan_results = tool_result_ids - tool_use_ids    # 需 drop

    if not orphan_uses and not orphan_results:
        return messages, fix_events

    # Pass 1: drop orphan tool_result blocks (但保留 user msg if 还有别的 block)
    cleaned: list[dict] = []
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "user" and isinstance(msg.get("content"), list):
            new_blocks = []
            for blk in msg["content"]:
                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                    tr_id = blk.get("tool_use_id")
                    if tr_id in orphan_results:
                        fix_events.append({
                            "action": "drop_orphan_tool_result",
                            "tool_use_id": tr_id,
                            "msg_index": i,
                        })
                        continue
                new_blocks.append(blk)
            if new_blocks:
                cleaned.append({**msg, "content": new_blocks})
            else:
                fix_events.append({
                    "action": "drop_empty_user_msg_after_filter",
                    "msg_index": i,
                })
            continue
        cleaned.append(msg)

    # Pass 2: 对每个 assistant 含 tool_use, 检查紧跟的 user 是否全应答; 不全则补 synthetic
    out: list[dict] = []
    n = len(cleaned)
    i = 0
    while i < n:
        msg = cleaned[i]
        out.append(msg)
        if isinstance(msg, dict) and msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
            this_uses = [
                blk.get("id")
                for blk in msg["content"]
                if isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("id") in orphan_uses
            ]
            if this_uses:
                # 看下一条是不是 user 含 tool_result list
                next_msg = cleaned[i + 1] if i + 1 < n else None
                if (
                    isinstance(next_msg, dict)
                    and next_msg.get("role") == "user"
                    and isinstance(next_msg.get("content"), list)
                ):
                    # 把 synthetic blocks 加到 next_msg.content
                    synth_blocks = [
                        {
                            "type": "tool_result",
                            "tool_use_id": tu_id,
                            "content": "[normalize_tool_pairs] tool_use 无对应 tool_result, 自动注入防 API 报错",
                            "is_error": True,
                        }
                        for tu_id in this_uses
                    ]
                    merged = {
                        **next_msg,
                        "content": list(next_msg["content"]) + synth_blocks,
                    }
                    out.append(merged)
                    i += 2  # 跳过 next_msg, 已合并
                    for tu_id in this_uses:
                        fix_events.append({
                            "action": "merge_synthetic_into_next_user",
                            "tool_use_id": tu_id,
                            "after_msg_index": i - 2,
                        })
                    continue
                else:
                    # next 不是 user list (可能是 assistant 或没下个) → 插独立 synthetic user
                    synth_msg = {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tu_id,
                                "content": "[normalize_tool_pairs] tool_use 无对应 tool_result, 自动注入防 API 报错",
                                "is_error": True,
                            }
                            for tu_id in this_uses
                        ],
                    }
                    out.append(synth_msg)
                    for tu_id in this_uses:
                        fix_events.append({
                            "action": "insert_standalone_synthetic_user",
                            "tool_use_id": tu_id,
                            "after_msg_index": i,
                        })
        i += 1

    return out, fix_events


# ═══════════════════════════════════════════════════════════════════════
# 专门异常类（BD.6c 对齐 CC withRetry.ts L144-168）
# ═══════════════════════════════════════════════════════════════════════

class CannotRetryError(Exception):
    """不可重试错误 — 调用方明确知道不应再 retry（401/403/background-529/max-retries）。
    对齐 CC `CannotRetryError`（src/services/api/withRetry.ts L144）"""
    def __init__(self, message: str, original_error: Exception | None = None, model: str | None = None):
        super().__init__(message)
        self.original_error = original_error
        self.model = model


class FallbackTriggeredError(Exception):
    """模型 fallback 触发 — 原模型不可用，切换到 fallback model。
    调用方应捕获并用 fallback_model 重新发起。
    对齐 CC `FallbackTriggeredError`（src/services/api/withRetry.ts L160）"""
    def __init__(self, original_model: str, fallback_model: str):
        super().__init__(f"Model fallback triggered: {original_model} -> {fallback_model}")
        self.original_model = original_model
        self.fallback_model = fallback_model


# ═══════════════════════════════════════════════════════════════════════
# 错误分类（对齐 CC is529Error / isStaleConnectionError / isTransientCapacityError）
# ═══════════════════════════════════════════════════════════════════════

def _is_529_error(err_str: str) -> bool:
    """CC is529Error 对齐：Anthropic 529 overloaded_error。
    the_company proxy 也可能返回 529 或 overloaded 关键字。"""
    return "529" in err_str or "overloaded_error" in err_str.lower() or "overloaded" in err_str.lower()


def _is_stale_connection_error(err_str: str) -> bool:
    """CC isStaleConnectionError 对齐：ECONNRESET / EPIPE / stale keep-alive socket。
    触发后应强制重建 LLMClient（丢弃连接池）。"""
    s = err_str.upper()
    return "ECONNRESET" in s or "EPIPE" in s or "CONNECTION RESET" in s or "CONNECTION ABORTED" in s


def _is_transient_capacity_error(err_str: str) -> bool:
    """CC isTransientCapacityError：429 / 529 临时容量问题，可重试。"""
    return "429" in err_str or _is_529_error(err_str)


def _is_non_retryable_auth_error(err_str: str) -> bool:
    """401 / 403 / invalid_api_key — 重试无意义，立刻 CannotRetryError。"""
    return any(code in err_str for code in ("401", "403", "invalid_api_key", "unauthorized"))


# 对齐 CC FOREGROUND_529_RETRY_SOURCES（src/services/api/withRetry.ts L62）：
# 旧设计 (foreground 白名单) 默认拒绝 — 实战 agent 类名 (LegacyAgnlMigrationAgent /
# GuardianAgent 等) 不在白名单 → 一旦 529 立刻失败. 但这些 agent 都是 user-waiting 的
# 前台调用, 应该重试.
#
# 2026-05-04 反转语义: 默认前台 (重试), 仅明确 background 的 caller 不重试.
# Background = "用户不在等" 的内部维护性调用 (compact summary / session_memory /
# title 生成 / 后台 cron 等).
_BACKGROUND_529_NORETRY_SOURCES = frozenset({
    "session_memory",        # CC SessionMemory 压缩 (内部维护)
    "title_generation",      # 给会话起标题 (用户不等)
    "background_cron",       # 任何标 cron 的后台任务
    "l4_compact",            # ContextCompactRouter L4 内部 LLM 摘要 (可丢, 失败仅退回 L1-L3)
})


def _should_retry_529(query_source: str | None) -> bool:
    """默认前台重试, 仅显式 background 的 query_source 不重试.

    避免 capacity cascade 下的 3-10× 放大: 已知 background 任务 (compact/title/cron)
    遇 529 立刻 fail, 让前台 user-waiting 调用优先用 capacity.
    """
    if not query_source:
        return True
    # 严格匹配 + 前缀匹配 (e.g. "l4_compact:abc" 也算 background)
    qs_lower = query_source.lower()
    for src in _BACKGROUND_529_NORETRY_SOURCES:
        if qs_lower == src or qs_lower.startswith(src + ":"):
            return False
    return True


class LLMCallRouter(Router):
    """一次完整 LLM 调用（含重试）。

    FORMAT_IN  = agent.llm-request
    FORMAT_OUT = agent.llm-response
    """

    DESCRIPTION: ClassVar[str] = "调用 LLM（带指数退避重试），解析 text / tool_uses / usage"
    FORMAT_IN: ClassVar[str] = "agent.llm-request"
    FORMAT_OUT: ClassVar[str] = "agent.llm-response"
    INPUT_KEYS: ClassVar[list[str]] = ["messages", "system_prompt"]
    OUTPUT_KEYS: ClassVar[list[str]] = ["assistant_message", "text", "tool_uses", "turn"]

    ROUTER_NAME: ClassVar[str] = "llm_call"

    def __init__(
        self,
        *,
        model: str | None = None,
        role: str | None = None,
        tools_spec: list[dict] | None = None,
        retry: RetryConfig | None = None,
        max_tokens: int = 16384,
        timeout_seconds: float | None = None,
        extra_body: dict[str, Any] | None = None,
        max_continuation_retries: int | None = None,
        prefix_tool_errors: bool = True,
        bus: Any | None = None,
        caller_prefix: str = "AgentNodeLoop",
    ):
        """
        max_tokens 默认 16384（2026-04-18 BD.6c 修）。
        原 LLMClient 默认 4096 对生成长 Markdown (findings_md 8-12K chars ≈ 5-8K tokens)
        的场景不足，导致 qwen 输出被截成残 JSON → OpenAI function.arguments 解析失败
        → 静默吞成 {} → 空 submit 死循环。业务子类可按 model 上限覆盖。
        """
        if bus is None:
            raise RuntimeError(
                "LLMCallRouter requires an EventBus (bus=...). "
                "Silent no-op emit loses LLM trail — can't triage hallucinations."
            )
        self._bus = bus
        self._retry = retry or RetryConfig()
        self._caller_prefix = caller_prefix
        self._tools_spec = tools_spec or []
        self._timeout_seconds = timeout_seconds
        self._extra_body = dict(extra_body or {})
        self._max_continuation_retries = max_continuation_retries
        self._prefix_tool_errors = bool(prefix_tool_errors)
        deadline_kwargs = {
            "request_timeout_sec": timeout_seconds,
            "stream_wall_clock_deadline_sec": timeout_seconds,
            "stream_idle_chunk_deadline_sec": timeout_seconds,
            # Retry ownership lives in this Router. A second hidden retry loop
            # inside LLMClient multiplies one timeout into minutes.
            "max_retry_attempts": 0,
            "max_continuation_retries": max_continuation_retries,
            "prefix_tool_errors": self._prefix_tool_errors,
        }

        # 带工具 / 不带工具两个 client
        client_kwargs = {**deadline_kwargs, "extra_body": self._extra_body or None}
        if role:
            self._llm = LLMClient(role=role, tools=self._tools_spec, max_tokens=max_tokens, **client_kwargs)
            self._llm_no_tools = LLMClient(role=role, tools=[], max_tokens=max_tokens, **client_kwargs)
        else:
            self._llm = LLMClient(model=model, tools=self._tools_spec, max_tokens=max_tokens, **client_kwargs)
            self._llm_no_tools = LLMClient(model=model, tools=[], max_tokens=max_tokens, **client_kwargs)

    # ── 纯文本调用辅助（供 ContextCompact L4 复用） ────────────────

    async def call_text(self, messages: list[dict], system: str, caller: str = "") -> str:
        """不带工具的纯文本调用（用于 compact/AI 分类等内部支撑调用）。"""
        resp = await self._call_with_retry(messages, system, use_tools=False, caller=caller)
        return self._extract_text(resp)

    # ── Router 入口 ────────────────────────────────────────────────

    async def run(self, input_data: Any) -> Verdict:
        pre = self.validate_input(input_data)
        if pre is not None:
            return pre

        trace_id = input_data.get("trace_id", "")
        turn = input_data.get("turn", 0)
        messages = input_data.get("messages", [])
        system = input_data.get("system_prompt", "")
        observer = input_data.get("agent_run_observer")

        # tool_use/tool_result 配对兜底 (CC normalizeMessagesForAPI 对齐, 2026-05-04)
        # L4 compact 切分 / extract_result fallback / partial compact 等路径都
        # 可能产畸形 messages, 喂 API 必报 400 invalid_request_error.
        # 这里跑一遍, 修动作落 fix_events 进 emit_router_input data.
        messages, _normalize_fixes = normalize_tool_pairs(messages)

        await emit_router_input(
            self._bus,
            trace_id=trace_id,
            router_name=self.ROUTER_NAME,
            format_id=self.FORMAT_IN,
            data={
                "turn": turn,
                "messages_count": len(messages),
                "system_prompt_len": len(system),
                "tools_count": len(self._tools_spec),
                "model": getattr(self._llm, "model", None),
                "normalize_fixes": _normalize_fixes,  # 一般空, 撞畸形时可见 audit
            },
        )
        if _normalize_fixes:
            logger.info(
                "[LLMCallRouter] turn %d: normalize_tool_pairs applied %d fix(es): %s",
                turn, len(_normalize_fixes),
                [e["action"] for e in _normalize_fixes],
            )

        caller = f"{self._caller_prefix}.turn_{turn}"
        try:
            response = await self._call_with_stream_progress(
                messages,
                system,
                use_tools=True,
                caller=caller,
                trace_id=trace_id,
                turn=turn,
                observer=observer,
            )
        except BaseException as exc:
            if observer is not None:
                observer.announce(
                    "failed",
                    turn=turn,
                    error_type=type(exc).__name__,
                    error=str(exc)[:1000],
                )
            raise

        stop_reason = getattr(response, "stop_reason", "end_turn")
        text_parts, tool_use_blocks = self._parse_response(response)

        # max_tokens 截断时工具调用可能畸形 — 丢弃
        # Keep truncated tool-call identities. AgentNodeLoop turns each one
        # into an explicit failed result and asks the model to re-issue it,
        # matching Pi's length-stop contract without executing partial args.

        text = "\n".join(text_parts)
        tool_uses = self._extract_tool_calls(tool_use_blocks)
        assistant_message = self._build_assistant_message(response)

        usage = getattr(response, "usage", None)
        usage_dict = {
            "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
            "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
            "cache_read_tokens": (
                getattr(usage, "cache_read_input_tokens", 0) if usage else 0
            ),
            "cache_write_tokens": (
                getattr(usage, "cache_creation_input_tokens", 0)
                if usage
                else 0
            ),
            "model": getattr(response, "model", getattr(self._llm, "model", None)),
        }

        output = {
            "assistant_message": assistant_message,
            "text": text,
            "tool_uses": tool_uses,
            "stop_reason": stop_reason,
            "usage": usage_dict,
            "turn": turn,
        }
        verdict = Verdict(kind=VerdictKind.PASS, output=output)

        await emit_router_output(
            self._bus,
            trace_id=trace_id,
            router_name=self.ROUTER_NAME,
            format_id=self.FORMAT_OUT,
            data={
                "stop_reason": stop_reason,
                "text_preview": text[:500],
                "tool_use_names": [tu["tool_name"] for tu in tool_uses],
                "usage": usage_dict,
                "turn": turn,
            },
            verdict_kind=verdict.kind.value,
        )
        return verdict

    # ── 内部实现 ──────────────────────────────────────────────────

    async def _call_with_stream_progress(
        self,
        messages: list[dict],
        system: str,
        *,
        use_tools: bool,
        caller: str,
        trace_id: str,
        turn: int,
        observer: Any | None = None,
    ) -> Any:
        """Bridge worker-thread stream telemetry into EventBus and the CLI."""
        updates: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()

        def on_progress(update: dict[str, Any]) -> None:
            updates.put(dict(update))

        task = asyncio.create_task(
            self._call_with_retry(
                messages,
                system,
                use_tools=use_tools,
                caller=caller,
                progress_callback=on_progress,
            )
        )

        async def drain() -> None:
            while True:
                try:
                    update = updates.get_nowait()
                except queue.Empty:
                    break
                payload = {"turn": turn, "caller": caller, **update}
                await emit_agent_signal(
                    self._bus,
                    trace_id=trace_id,
                    event_type="agent.llm.progress",
                    source="agent.llm_call",
                    payload=payload,
                )
                self._print_stream_progress(payload)
                if observer is not None:
                    observer.update("llm_streaming", **payload)

        heartbeat_at = 0.0
        while not task.done():
            await drain()
            now = asyncio.get_running_loop().time()
            if observer is not None and now - heartbeat_at >= 1.0:
                observer.update("llm_waiting", turn=turn, caller=caller)
                heartbeat_at = now
            await asyncio.wait({task}, timeout=0.25)
        await drain()
        return await task

    @staticmethod
    def _print_stream_progress(payload: dict[str, Any]) -> None:
        """Keep foreground Agent runs visibly alive without printing content."""
        event = str(payload.get("event") or "progress")
        model = str(payload.get("model") or "?")
        turn = payload.get("turn", "?")
        details: list[str] = []
        if "elapsed_ms" in payload:
            details.append(f"{float(payload['elapsed_ms']) / 1000:.1f}s")
        if "chunk_count" in payload:
            details.append(f"chunks={payload['chunk_count']}")
        if "reasoning_chars" in payload:
            details.append(f"reasoning={payload['reasoning_chars']}")
        if "text_chars" in payload:
            details.append(f"text={payload['text_chars']}")
        if "tool_arg_chars" in payload:
            details.append(f"tool_args={payload['tool_arg_chars']}")
        suffix = " " + " ".join(details) if details else ""
        print(f"[llm {model} turn={turn}] {event}{suffix}", file=sys.stderr, flush=True)

    async def _call_with_retry(
        self,
        messages: list[dict],
        system: str,
        *,
        use_tools: bool,
        caller: str,
        progress_callback: Any | None = None,
    ) -> Any:
        cfg = self._retry
        # env CLAUDE_CODE_MAX_RETRIES 覆盖（对齐 CC getMaxRetries）
        env_max = os.environ.get("CLAUDE_CODE_MAX_RETRIES")
        max_retries = int(env_max) if (env_max and env_max.isdigit()) else cfg.max_retries
        llm = self._llm if use_tools else self._llm_no_tools
        model_name = getattr(llm, "model", "?") or "?"
        consecutive_529 = 0   # CC MAX_529_RETRIES 单独计数
        # caller 形如 "PrefabSemanticLoop.turn_3"；提 query_source：background 标记由业务子类
        # 通过 caller_prefix 注入（未来：LoopConfig 可声明 query_source）；默认视为前台
        query_source = caller.split(".")[0] if caller else None
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 2):  # +1 循环，允许 max_retries+1 次
            # stale connection：上次是 ECONNRESET/EPIPE，下次重建 client 强制放弃连接池
            if last_error is not None and _is_stale_connection_error(str(last_error)):
                logger.warning("[LLMCallRouter] stale connection detected, rebuilding client")
                llm = LLMClient(
                    model=model_name,
                    tools=self._tools_spec if use_tools else [],
                    max_tokens=self._llm.max_tokens,
                    request_timeout_sec=self._timeout_seconds,
                    stream_wall_clock_deadline_sec=self._timeout_seconds,
                    stream_idle_chunk_deadline_sec=self._timeout_seconds,
                    max_retry_attempts=0,
                    max_continuation_retries=self._max_continuation_retries,
                    prefix_tool_errors=self._prefix_tool_errors,
                    extra_body=self._extra_body or None,
                )
            try:
                return await asyncio.to_thread(
                    llm.call,
                    messages=messages,
                    system=system,
                    caller=caller,
                    progress_callback=progress_callback,
                )
            except Exception as e:
                last_error = e
                err = str(e)
                # 1. 认证错误 — 立刻 CannotRetryError（重试无意义）
                if _is_non_retryable_auth_error(err):
                    raise CannotRetryError(
                        f"auth error (non-retryable): {err[:200]}", e, model_name,
                    ) from e
                # 2. 后台调用 + 529 — 立刻 CannotRetryError（防 capacity cascade 放大）
                if _is_529_error(err) and not _should_retry_529(query_source):
                    logger.warning(
                        "[LLMCallRouter] 529 for background source %s → drop without retry",
                        query_source,
                    )
                    raise CannotRetryError(
                        f"529 for background source {query_source}: {err[:200]}", e, model_name,
                    ) from e
                # 3. 其他可重试（429/529/timeout/connection 等）
                retryable = (
                    _is_transient_capacity_error(err)
                    or "timeout" in err.lower()
                    or "connection" in err.lower()
                    or _is_stale_connection_error(err)
                )
                if not retryable:
                    raise
                # 4. 529 单独计数 — 达 MAX_529_RETRIES=3 时 fallback model（对齐 CC MAX_529_RETRIES）
                if _is_529_error(err):
                    consecutive_529 += 1
                    if consecutive_529 >= 3 and cfg.fallback_model:
                        logger.warning(
                            "[LLMCallRouter] 529 x%d → fallback to %s",
                            consecutive_529, cfg.fallback_model,
                        )
                        raise FallbackTriggeredError(model_name, cfg.fallback_model) from e
                else:
                    consecutive_529 = 0  # non-529 重置
                # 5. 达到 max_retries → CannotRetryError
                if attempt > max_retries:
                    raise CannotRetryError(
                        f"exhausted {max_retries} retries: {err[:200]}", e, model_name,
                    ) from e
                # 6. 指数退避 + 抖动
                base = min(cfg.base_delay_ms * (2 ** (attempt - 1)), cfg.max_delay_ms)
                jitter = random.random() * cfg.jitter_factor * base
                delay = (base + jitter) / 1000
                logger.warning(
                    "[LLMCallRouter] retry %d/%d: %s (wait %.1fs)",
                    attempt, max_retries, err[:80], delay,
                )
                await asyncio.sleep(delay)
                # 7. fallback model（attempt 达阈值 + 非 529 场景）
                if attempt >= cfg.fallback_after_attempts and cfg.fallback_model and consecutive_529 == 0:
                    logger.info("[LLMCallRouter] switching to fallback model: %s", cfg.fallback_model)
                    llm = LLMClient(
                        model=cfg.fallback_model,
                        tools=self._tools_spec if use_tools else [],
                        max_tokens=self._llm.max_tokens,
                        request_timeout_sec=self._timeout_seconds,
                        stream_wall_clock_deadline_sec=self._timeout_seconds,
                        stream_idle_chunk_deadline_sec=self._timeout_seconds,
                        max_retry_attempts=0,
                        max_continuation_retries=self._max_continuation_retries,
                        prefix_tool_errors=self._prefix_tool_errors,
                        extra_body=self._extra_body or None,
                    )
                    model_name = cfg.fallback_model
        # 理论不可达（max_retries+1 次后前面会 raise CannotRetryError）
        raise CannotRetryError(
            f"LLM call failed after {max_retries} retries: {last_error}",
            last_error, model_name,
        )

    # ── response 解析（与既有行为一致，防幻觉不改） ────────────────────

    def _parse_response(self, response: Any) -> tuple[list[str], list[Any]]:
        text_parts: list[str] = []
        tool_use_blocks: list[Any] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_use_blocks.append(block)
        return text_parts, tool_use_blocks

    def _build_assistant_message(self, response: Any) -> dict:
        content = []
        for block in response.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
        return {"role": "assistant", "content": content}

    def _extract_tool_calls(self, tool_use_blocks: list[Any]) -> list[dict]:
        calls = []
        for tool in tool_use_blocks:
            args = dict(tool.input) if tool.input and isinstance(tool.input, dict) else {}
            # intent 是 LLM 的自述字段，不传给执行器
            args.pop("intent", None)
            calls.append({
                "tool_name": tool.name,
                "tool_args": args,
                "tool_use_id": tool.id,
            })
        return calls

    def _extract_text(self, response: Any) -> str:
        parts = []
        if hasattr(response, "content"):
            for block in response.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
        elif isinstance(response, str):
            return response
        return "\n".join(parts)
