# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-18
# [OMNI] material_id="material:core.agent.routers.context_compressor.router.py"
"""ContextCompactRouter — agent.context-request → agent.context-compacted

四层压缩 (2026-05-04 L4 实施完成):
- L1 微压缩 (老化, 确定性)
- L2 单条截断 (tool_result 截 max_tool_output, 确定性)
- L3 滑窗 (保留最近 max_messages, 确定性)
- L4 LLM 自动摘要 (条件触发: tokens 超 context_window * threshold)
  - 触发阈值: cfg.auto_compact_threshold (默认 0.90)
  - 保留最近 cfg.auto_compact_keep_recent 条 (默认 10) 不参与摘要
  - 早期消息 → LLM 调用产 <summary> 文本 → 替换为 1 条 user summary message
  - 失败熔断: 连续 cfg.auto_compact_max_failures (默认 3) 次失败后, 本 session 停 L4
  - 调用方式: 直接构造 LLMClient (no tools, no LLMCallRouter), 避免 compact ↔ LLMCall 递归

参考: 参考项目/claude-code-analysis/build-src/src/commands/compact/compact.ts
+ prompt.ts (CRITICAL: text-only summary prompt + 9-section summary template).
"""

from __future__ import annotations

import logging
import re
import asyncio
from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router
from omnicompany.runtime.agent.agent_loop_compact import (
    apply_microcompact,
    apply_sliding_window,
    apply_truncation,
    estimate_tokens,
)
from omnicompany.runtime.agent.agent_loop_config import CompactConfig, PRESET_STANDARD
from omnicompany.packages.services._core.agent._bus import (
    emit_router_input,
    emit_router_output,
)

logger = logging.getLogger(__name__)


# ─── L4 prompts (verbatim from CC build-src/src/commands/compact/prompt.ts) ─────

_L4_SYSTEM_PROMPT = """\
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.
"""

_L4_USER_PROMPT = """\
Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions. This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like file names, full code snippets, function signatures, file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly.

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first. If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.

REMINDER: Do NOT call any tools. Respond with plain text only — an <analysis> block followed by a <summary> block. Tool calls will be rejected and you will fail the task.
"""

_L4_USER_SUMMARY_TEMPLATE = """\
This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.

Summary:
{summary_text}
"""


def _extract_summary(text: str) -> str:
    """从 LLM 响应里剥 <analysis>...</analysis>, 抽 <summary>...</summary>. 失败兜底返回原文."""
    if not text:
        return ""
    # 删除 <analysis>...</analysis> 块 (可能多个)
    cleaned = re.sub(r"<analysis>[\s\S]*?</analysis>", "", text, flags=re.IGNORECASE)
    # 抽 <summary>...</summary> 内容
    m = re.search(r"<summary>([\s\S]*?)</summary>", cleaned, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # 没匹配 summary tag — 返回 cleaned (可能 LLM 没按格式吐, 但内容可用)
    stripped = cleaned.strip()
    return stripped or text.strip()


class ContextCompactRouter(Router):
    """上下文压缩 Router（L1-L3 同步）。

    FORMAT_IN  = agent.context-request
    FORMAT_OUT = agent.context-compacted
    """

    DESCRIPTION: ClassVar[str] = "对话上下文四层压缩（L1 老化 + L2 截断 + L3 滑窗，L4 由 LLM 回调）"
    FORMAT_IN: ClassVar[str] = "agent.context-request"
    FORMAT_OUT: ClassVar[str] = "agent.context-compacted"
    INPUT_KEYS: ClassVar[list[str]] = ["messages"]
    OUTPUT_KEYS: ClassVar[list[str]] = ["messages", "compact_events"]

    ROUTER_NAME: ClassVar[str] = "context_compact"

    def __init__(
        self,
        *,
        compact_cfg: CompactConfig | None = None,
        bus: Any | None = None,
        context_window: int = 200_000,
        l4_model_role: str = "runtime_main",
        l4_keep_recent: int = 10,
    ):
        if bus is None:
            raise RuntimeError(
                "ContextCompactRouter requires an EventBus (bus=...). "
                "Silent no-op emit loses Format trail."
            )
        self._cfg = compact_cfg or PRESET_STANDARD.compact
        self._bus = bus
        # L4 配置: context_window 用于阈值判断 (cfg.auto_compact_threshold * context_window)
        self._context_window = context_window
        self._l4_model_role = l4_model_role
        self._l4_keep_recent = l4_keep_recent  # 末尾保留几条不参与摘要
        self._l4_consecutive_failures = 0  # 熔断计数
        self._l4_disabled_by_breaker = False  # 触发熔断后本 session 永久 disable

    async def run(self, input_data: Any) -> Verdict:
        pre = self.validate_input(input_data)
        if pre is not None:
            return pre

        trace_id = input_data.get("trace_id", "")
        await emit_router_input(
            self._bus,
            trace_id=trace_id,
            router_name=self.ROUTER_NAME,
            format_id=self.FORMAT_IN,
            data={
                "turn": input_data.get("turn", -1),
                "messages_count": len(input_data.get("messages", [])),
                "tokens_before": estimate_tokens(input_data.get("messages", [])),
            },
        )

        messages: list[dict] = list(input_data.get("messages", []))
        cfg = self._cfg  # 本阶段不接受每轮覆盖

        compact_events: list[dict] = []
        tokens_before = estimate_tokens(messages)

        # L1 微压缩（老化）
        before = len(messages)
        messages = apply_microcompact(messages, cfg)
        compact_events.append({
            "layer": "L1",
            "action": "microcompact",
            "messages_before": before,
            "messages_after": len(messages),
        })

        # L2 单条截断（截 tool_result 内容）
        messages = apply_truncation(messages, cfg)
        compact_events.append({
            "layer": "L2",
            "action": "truncate_tool_output",
            "max_tool_output": cfg.max_tool_output,
        })

        # L3 滑窗
        before = len(messages)
        messages = apply_sliding_window(messages, cfg)
        compact_events.append({
            "layer": "L3",
            "action": "sliding_window",
            "messages_before": before,
            "messages_after": len(messages),
            "max_messages": cfg.max_messages,
        })

        # L4 LLM 自动摘要 (2026-05-04 实施完成)
        tokens_after_l3 = estimate_tokens(messages)
        l4_event = await self._maybe_l4_summarize(
            messages=messages,
            cfg=cfg,
            trace_id=trace_id,
            tokens_after_l3=tokens_after_l3,
        )
        compact_events.append(l4_event)
        if l4_event.get("messages_replaced"):
            messages = l4_event.pop("new_messages")  # pop 防 verdict.output 携 large payload

        tokens_after = estimate_tokens(messages)
        output = {
            "messages": messages,
            "compact_events": compact_events,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
        }
        verdict = Verdict(kind=VerdictKind.PASS, output=output)

        await emit_router_output(
            self._bus,
            trace_id=trace_id,
            router_name=self.ROUTER_NAME,
            format_id=self.FORMAT_OUT,
            data={
                "compact_events": compact_events,
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "messages_count": len(messages),
            },
            verdict_kind=verdict.kind.value,
        )
        return verdict

    # ── L4 LLM auto-summarize ──────────────────────────────────────────────

    async def _maybe_l4_summarize(
        self,
        *,
        messages: list[dict],
        cfg: CompactConfig,
        trace_id: str,
        tokens_after_l3: int,
    ) -> dict:
        """触发条件检查 + LLM 调用 + 摘要生成 + messages 替换.

        触发: cfg.auto_compact_enabled 且 tokens_after_l3 >= context_window * threshold.
        替换: head 部分 (除最近 keep_recent 条) → 1 条 user summary message + tail 保留.
        熔断: 连续 cfg.auto_compact_max_failures 次失败后, 本 session 永久跳过.
        """
        if not cfg.auto_compact_enabled:
            return {"layer": "L4", "action": "skipped_disabled"}
        if self._l4_disabled_by_breaker:
            return {
                "layer": "L4",
                "action": "skipped_circuit_breaker",
                "consecutive_failures": self._l4_consecutive_failures,
            }
        threshold_tokens = int(self._context_window * cfg.auto_compact_threshold)
        if tokens_after_l3 < threshold_tokens:
            return {
                "layer": "L4",
                "action": "skipped_below_threshold",
                "tokens": tokens_after_l3,
                "threshold": threshold_tokens,
            }
        # 至少留 keep_recent + 2 条才有压缩意义 (head 至少 2 条, 否则不值得调 LLM)
        keep_recent = max(2, self._l4_keep_recent)
        if len(messages) <= keep_recent + 2:
            return {
                "layer": "L4",
                "action": "skipped_too_few_messages",
                "messages_count": len(messages),
                "keep_recent": keep_recent,
            }
        head = messages[:-keep_recent]
        tail = messages[-keep_recent:]

        # ToolUse pairing safety: head 末尾不能切断 tool_use/tool_result 配对
        # (assistant 含 tool_use 但下一条 tool_result 在 tail → API 报错)
        # 简化策略: 若 head 最后一条是 assistant 含 tool_use, 把它移到 tail
        head, tail = _ensure_tool_use_pairing(head, tail)
        if len(head) < 2:
            return {
                "layer": "L4",
                "action": "skipped_pairing_no_head",
                "messages_count": len(messages),
            }

        # 调 LLM 生成摘要
        try:
            summary_text = await self._call_llm_summarize(head, trace_id)
        except Exception as e:
            self._l4_consecutive_failures += 1
            if self._l4_consecutive_failures >= cfg.auto_compact_max_failures:
                self._l4_disabled_by_breaker = True
                logger.warning(
                    "[L4 compact] tripped circuit breaker after %d failures, disabling for session",
                    self._l4_consecutive_failures,
                )
            logger.warning("[L4 compact] LLM call failed: %s", e)
            return {
                "layer": "L4",
                "action": "failed",
                "error": str(e)[:300],
                "consecutive_failures": self._l4_consecutive_failures,
                "circuit_broken": self._l4_disabled_by_breaker,
            }

        # 成功 → 重置计数
        self._l4_consecutive_failures = 0

        # 构造替换 messages: 1 条 user summary message + tail
        summary_msg = {
            "role": "user",
            "content": _L4_USER_SUMMARY_TEMPLATE.format(summary_text=summary_text),
        }
        new_messages = [summary_msg] + tail
        return {
            "layer": "L4",
            "action": "summarized",
            "head_count": len(head),
            "tail_count": len(tail),
            "summary_chars": len(summary_text),
            "new_messages_count": len(new_messages),
            "messages_replaced": True,
            "new_messages": new_messages,
        }

    async def _call_llm_summarize(self, head_messages: list[dict], trace_id: str) -> str:
        """直接构造 LLMClient 调用, 不走 LLMCallRouter (避免 LLMCall ↔ ContextCompact 递归)."""
        from omnicompany.runtime.llm.llm import LLMClient

        # 复用 head_messages 作上下文 + 末尾追加 _L4_USER_PROMPT 作摘要请求
        msgs_for_summary = list(head_messages) + [
            {"role": "user", "content": _L4_USER_PROMPT}
        ]
        # 摘要不走 tools, 不走 retry router; max_tokens 给充足 (CC 用 maxOutputTokensOverride
        # 约 8K, 我们 8000 跟齐).
        client = LLMClient(role=self._l4_model_role, max_tokens=8000, tools=[])
        # LLMClient.call 是 sync, 包到 asyncio.to_thread 不阻塞 event loop
        result = await asyncio.to_thread(
            client.call,
            msgs_for_summary,
            _L4_SYSTEM_PROMPT,
            None,  # tool_choice
            None,  # response_format
            f"l4_compact:{trace_id[:16]}",  # caller
        )
        # LLMClient.call 返回的对象有 .content (Anthropic 风格 list of blocks) 或 .text
        text = _extract_text_from_llm_result(result)
        if not text:
            raise RuntimeError("L4 LLM returned empty text")
        summary = _extract_summary(text)
        if not summary:
            raise RuntimeError("L4 summary extraction yielded empty")
        return summary


def _extract_text_from_llm_result(result: Any) -> str:
    """LLMClient.call 返回的对象形态多变 (Anthropic Message vs OpenAI ChatCompletion). 抽 plain text."""
    if result is None:
        return ""
    # Anthropic Message: result.content = [TextBlock(type='text', text='...'), ...]
    content = getattr(result, "content", None)
    if isinstance(content, list):
        parts = []
        for blk in content:
            t = getattr(blk, "text", None)
            if t:
                parts.append(t)
            elif isinstance(blk, dict) and blk.get("type") == "text":
                parts.append(blk.get("text", ""))
        if parts:
            return "\n".join(parts)
    # OpenAI ChatCompletion: result.choices[0].message.content
    choices = getattr(result, "choices", None)
    if choices:
        msg = getattr(choices[0], "message", None)
        if msg:
            c = getattr(msg, "content", None)
            if isinstance(c, str):
                return c
    # Fallback: 直接 str
    if isinstance(result, str):
        return result
    return str(result)


def _ensure_tool_use_pairing(head: list[dict], tail: list[dict]) -> tuple[list[dict], list[dict]]:
    """防 head 末尾留下未配对的 tool_use (下一 tool_result 在 tail 会让 API 报错).

    简化规则: 若 head 末尾消息含 tool_use block, 把它移到 tail 头.
    若移完 head 还含 tool_use 末尾, 继续移. 直到 head 末尾干净或 head < 2.
    """
    head = list(head)
    tail = list(tail)
    while head and _msg_contains_tool_use(head[-1]):
        moved = head.pop()
        tail.insert(0, moved)
    return head, tail


def _msg_contains_tool_use(msg: dict) -> bool:
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if isinstance(content, list):
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "tool_use":
                return True
    return False
