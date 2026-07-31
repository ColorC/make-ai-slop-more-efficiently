# [OMNI] origin=codex domain=services/agent ts=2026-07-29 type=infrastructure
# [OMNI] material_id="material:core.agent.routers.pi_context.py"
"""Pi 0.82.1 context transformation and session-boundary compaction.

The active agent loop does not micro-compact between tool turns. Pi checks
compaction after ``agent_end`` (or while recovering a context overflow), keeps
roughly 20k recent tokens, and summarizes the discarded prefix with the same
model. This module reproduces that behavior while all state remains on the
Omnicompany EventBus.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent._bus import (
    emit_router_input,
    emit_router_output,
)
from omnicompany.packages.services._core.agent.routers.llm_call import (
    LLMCallRouter,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent.agent_loop_config import RetryConfig
from omnicompany.runtime.routing.router import Router


PI_COMPACTION_RESERVE_TOKENS = 16_384
PI_COMPACTION_KEEP_RECENT_TOKENS = 20_000
PI_COMPACTION_SUMMARY_MAX_TOKENS = 13_107
PI_TURN_PREFIX_SUMMARY_MAX_TOKENS = 8_192

PI_COMPACTION_SUMMARY_PREFIX = (
    "The conversation history before this point was compacted into the "
    "following summary:\n\n<summary>\n"
)
PI_COMPACTION_SUMMARY_SUFFIX = "\n</summary>"

PI_SUMMARIZATION_SYSTEM_PROMPT = """You are a context summarization assistant. Your task is to read a conversation between a user and an AI assistant, then produce a structured summary following the exact format specified.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary."""

PI_SUMMARIZATION_PROMPT = """The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish? Can be multiple items if the session covers different tasks.]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned by user]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue]
- [Or "(none)" if not applicable]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

PI_UPDATE_SUMMARIZATION_PROMPT = """The messages above are NEW conversation messages to incorporate into the existing summary provided in <previous-summary> tags.

Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages
- If something is no longer relevant, you may remove it

Use this EXACT format:

## Goal
[Preserve existing goals, add new ones if the task expanded]

## Constraints & Preferences
- [Preserve existing, add new ones discovered]

## Progress
### Done
- [x] [Include previously done items AND newly completed items]

### In Progress
- [ ] [Current work - update based on progress]

### Blocked
- [Current blockers - remove if resolved]

## Key Decisions
- **[Decision]**: [Brief rationale] (preserve all previous, add new)

## Next Steps
1. [Update based on current state]

## Critical Context
- [Preserve important context, add new if needed]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

PI_TURN_PREFIX_SUMMARIZATION_PROMPT = """This is the PREFIX of a turn that was too large to keep. The SUFFIX (recent work) is retained.

Summarize the prefix to provide context for the retained suffix:

## Original Request
[What did the user ask for in this turn?]

## Early Progress
- [Key decisions and work done in the prefix]

## Context for Suffix
- [Information needed to understand the retained recent work]

Be concise. Focus on what's needed to understand the kept suffix."""


class PiContextTransformRouter(Router):
    """Identity transform used inside an active Pi-aligned agent run."""

    DESCRIPTION: ClassVar[str] = "Pi active-run context identity transform"
    FORMAT_IN: ClassVar[str] = "agent.context-request"
    FORMAT_OUT: ClassVar[str] = "agent.context-compacted"
    INPUT_KEYS: ClassVar[list[str]] = ["messages"]
    OUTPUT_KEYS: ClassVar[list[str]] = ["messages", "compact_events"]
    ROUTER_NAME: ClassVar[str] = "pi_context_transform"

    def __init__(self, *, bus: Any) -> None:
        if bus is None:
            raise RuntimeError("PiContextTransformRouter requires an EventBus")
        self._bus = bus

    async def run(self, input_data: Any) -> Verdict:
        pre = self.validate_input(input_data)
        if pre is not None:
            return pre
        trace_id = str(input_data.get("trace_id") or "")
        messages = list(input_data.get("messages") or [])
        await emit_router_input(
            self._bus,
            trace_id=trace_id,
            router_name=self.ROUTER_NAME,
            format_id=self.FORMAT_IN,
            data={
                "turn": input_data.get("turn", -1),
                "messages_count": len(messages),
            },
        )
        output = {
            "messages": messages,
            "compact_events": [
                {
                    "action": "identity",
                    "reason": "pi compacts only at session boundary or overflow recovery",
                }
            ],
        }
        verdict = Verdict(kind=VerdictKind.PASS, output=output)
        await emit_router_output(
            self._bus,
            trace_id=trace_id,
            router_name=self.ROUTER_NAME,
            format_id=self.FORMAT_OUT,
            data={
                "messages_count": len(messages),
                "action": "identity",
            },
            verdict_kind=verdict.kind.value,
        )
        return verdict


@dataclass(frozen=True)
class PiCompactionResult:
    messages: list[dict[str, Any]]
    summary: str
    tokens_before: int
    estimated_tokens_after: int
    first_kept_index: int
    is_split_turn: bool


@dataclass(frozen=True)
class _PiCompactionPreparation:
    messages_to_summarize: list[dict[str, Any]]
    turn_prefix_messages: list[dict[str, Any]]
    kept_messages: list[dict[str, Any]]
    previous_summary: str | None
    tokens_before: int
    first_kept_index: int
    is_split_turn: bool


class PiSessionCompactor:
    """Generate Pi-shaped compaction summaries with the active model."""

    def __init__(
        self,
        *,
        model: str | None,
        role: str | None,
        retry: RetryConfig,
        extra_body: dict[str, Any] | None,
        bus: Any,
    ) -> None:
        common = {
            "model": model,
            "role": role,
            "tools_spec": [],
            "retry": retry,
            "extra_body": extra_body,
            "max_continuation_retries": 0,
            "prefix_tool_errors": False,
            "bus": bus,
            "caller_prefix": "PiSessionCompactor",
        }
        self._summary_llm = LLMCallRouter(
            max_tokens=PI_COMPACTION_SUMMARY_MAX_TOKENS,
            **common,
        )
        self._turn_prefix_llm = LLMCallRouter(
            max_tokens=PI_TURN_PREFIX_SUMMARY_MAX_TOKENS,
            **common,
        )

    @property
    def policy(self) -> dict[str, Any]:
        return {
            "active_run_transform": "identity",
            "auto_compaction": {
                "enabled": True,
                "trigger": "agent_end_or_context_overflow",
                "reserve_tokens": PI_COMPACTION_RESERVE_TOKENS,
                "keep_recent_tokens": PI_COMPACTION_KEEP_RECENT_TOKENS,
            },
            "overflow_recovery": "compact_then_retry_once",
        }

    def bind_runtime_context(
        self,
        *,
        bus: Any,
        trace_id: str,
        parent_event_id: str,
    ) -> None:
        self._summary_llm.bind_runtime_context(
            bus=bus,
            trace_id=trace_id,
            parent_event_id=parent_event_id,
        )
        self._turn_prefix_llm.bind_runtime_context(
            bus=bus,
            trace_id=trace_id,
            parent_event_id=parent_event_id,
        )

    @staticmethod
    def should_compact(context_tokens: int, context_window: int) -> bool:
        return context_tokens > context_window - PI_COMPACTION_RESERVE_TOKENS

    def can_compact(self, messages: list[dict[str, Any]]) -> bool:
        return self._prepare(messages) is not None

    async def compact(
        self,
        messages: list[dict[str, Any]],
        *,
        trace_id: str,
    ) -> PiCompactionResult | None:
        preparation = self._prepare(messages)
        if preparation is None:
            return None

        history_text = ""
        history_summary = ""
        if preparation.messages_to_summarize:
            history_text = _serialize_conversation(
                preparation.messages_to_summarize
            )
            prompt = f"<conversation>\n{history_text}\n</conversation>\n\n"
            if preparation.previous_summary:
                prompt += (
                    "<previous-summary>\n"
                    f"{preparation.previous_summary}\n"
                    "</previous-summary>\n\n"
                )
            prompt += (
                PI_UPDATE_SUMMARIZATION_PROMPT
                if preparation.previous_summary
                else PI_SUMMARIZATION_PROMPT
            )
            history_summary = await self._summary_llm.call_text(
                [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                PI_SUMMARIZATION_SYSTEM_PROMPT,
                caller=f"pi_compaction:{trace_id[:16]}",
            )
        elif preparation.previous_summary:
            history_summary = preparation.previous_summary
        else:
            history_summary = "No prior history."

        summary = history_summary
        if preparation.is_split_turn and preparation.turn_prefix_messages:
            prefix_text = _serialize_conversation(
                preparation.turn_prefix_messages
            )
            prefix_prompt = (
                f"<conversation>\n{prefix_text}\n</conversation>\n\n"
                f"{PI_TURN_PREFIX_SUMMARIZATION_PROMPT}"
            )
            turn_prefix_summary = await self._turn_prefix_llm.call_text(
                [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": prefix_prompt}],
                    }
                ],
                PI_SUMMARIZATION_SYSTEM_PROMPT,
                caller=f"pi_turn_prefix:{trace_id[:16]}",
            )
            summary = (
                f"{history_summary}\n\n---\n\n"
                "**Turn Context (split turn):**\n\n"
                f"{turn_prefix_summary}"
            )

        read_files, modified_files = _extract_file_operations(
            preparation.messages_to_summarize
            + preparation.turn_prefix_messages,
            previous_summary=preparation.previous_summary,
        )
        summary += _format_file_operations(read_files, modified_files)
        compacted_messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            PI_COMPACTION_SUMMARY_PREFIX
                            + summary
                            + PI_COMPACTION_SUMMARY_SUFFIX
                        ),
                    }
                ],
            },
            *preparation.kept_messages,
        ]
        return PiCompactionResult(
            messages=compacted_messages,
            summary=summary,
            tokens_before=preparation.tokens_before,
            estimated_tokens_after=estimate_messages_tokens(compacted_messages),
            first_kept_index=preparation.first_kept_index,
            is_split_turn=preparation.is_split_turn,
        )

    def _prepare(
        self,
        messages: list[dict[str, Any]],
    ) -> _PiCompactionPreparation | None:
        if not messages:
            return None
        previous_summary = _extract_previous_summary(messages[0])
        boundary_start = 1 if previous_summary is not None else 0
        if boundary_start >= len(messages):
            return None

        cut_points = [
            index
            for index in range(boundary_start, len(messages))
            if _is_valid_cut_point(messages[index])
        ]
        if not cut_points:
            return None

        accumulated = 0
        cut_index = cut_points[0]
        for index in range(len(messages) - 1, boundary_start - 1, -1):
            accumulated += estimate_message_tokens(messages[index])
            if accumulated >= PI_COMPACTION_KEEP_RECENT_TOKENS:
                cut_index = next(
                    (point for point in cut_points if point >= index),
                    cut_points[-1],
                )
                break

        starts_turn = _is_turn_start(messages[cut_index])
        turn_start_index = (
            -1
            if starts_turn
            else _find_turn_start(messages, cut_index, boundary_start)
        )
        is_split_turn = not starts_turn and turn_start_index != -1
        history_end = turn_start_index if is_split_turn else cut_index
        messages_to_summarize = messages[boundary_start:history_end]
        turn_prefix_messages = (
            messages[turn_start_index:cut_index] if is_split_turn else []
        )
        if not messages_to_summarize and not turn_prefix_messages:
            return None
        return _PiCompactionPreparation(
            messages_to_summarize=list(messages_to_summarize),
            turn_prefix_messages=list(turn_prefix_messages),
            kept_messages=list(messages[cut_index:]),
            previous_summary=previous_summary,
            tokens_before=estimate_messages_tokens(messages),
            first_kept_index=cut_index,
            is_split_turn=is_split_turn,
        )


def is_context_overflow_error(error: BaseException) -> bool:
    text = str(error)
    patterns = (
        r"context length",
        r"context window",
        r"maximum context",
        r"max(?:imum)?[_ ]context",
        r"too many tokens",
        r"input length.*exceed",
        r"token count.*exceed",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    role = str(message.get("role") or "")
    content = message.get("content")
    chars = _content_js_chars(content)
    if role == "assistant" and isinstance(content, list):
        chars = 0
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                chars += _js_length(str(block.get("text") or ""))
            elif block_type in {"thinking"}:
                chars += _js_length(str(block.get("thinking") or ""))
            elif block_type in {"tool_use", "toolCall"}:
                name = str(block.get("name") or "")
                arguments = block.get("input", block.get("arguments", {}))
                chars += _js_length(name) + _js_length(
                    json.dumps(
                        arguments,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
    return (chars + 3) // 4


def _content_js_chars(content: Any) -> int:
    if isinstance(content, str):
        return _js_length(content)
    if not isinstance(content, list):
        return 0
    chars = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            chars += _js_length(str(block.get("text") or ""))
        elif block.get("type") in {"image", "image_url"}:
            chars += 4_800
        elif block.get("type") == "tool_result":
            chars += _js_length(str(block.get("content") or ""))
    return chars


def _js_length(text: str) -> int:
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _is_tool_result_message(message: dict[str, Any]) -> bool:
    if message.get("role") == "toolResult":
        return True
    content = message.get("content")
    return (
        message.get("role") == "user"
        and isinstance(content, list)
        and bool(content)
        and all(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        )
    )


def _is_turn_start(message: dict[str, Any]) -> bool:
    return message.get("role") == "user" and not _is_tool_result_message(message)


def _is_valid_cut_point(message: dict[str, Any]) -> bool:
    return _is_turn_start(message) or message.get("role") == "assistant"


def _find_turn_start(
    messages: list[dict[str, Any]],
    cut_index: int,
    boundary_start: int,
) -> int:
    for index in range(cut_index - 1, boundary_start - 1, -1):
        if _is_turn_start(messages[index]):
            return index
    return -1


def _extract_previous_summary(message: dict[str, Any]) -> str | None:
    text = _content_text(message.get("content"))
    if not (
        text.startswith(PI_COMPACTION_SUMMARY_PREFIX)
        and text.endswith(PI_COMPACTION_SUMMARY_SUFFIX)
    ):
        return None
    return text[
        len(PI_COMPACTION_SUMMARY_PREFIX) :
        -len(PI_COMPACTION_SUMMARY_SUFFIX)
    ]


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _serialize_conversation(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if _is_tool_result_message(message):
            result_parts: list[str] = []
            if role == "toolResult":
                result_parts.append(_content_text(content))
            elif isinstance(content, list):
                result_parts.extend(
                    str(block.get("content") or "")
                    for block in content
                    if isinstance(block, dict)
                    and block.get("type") == "tool_result"
                )
            for result in result_parts:
                if result:
                    parts.append(
                        f"[Tool result]: {_truncate_tool_result(result)}"
                    )
        elif role == "user":
            text = _content_text(content)
            if text:
                parts.append(f"[User]: {text}")
        elif role == "assistant" and isinstance(content, list):
            thinking: list[str] = []
            text_parts: list[str] = []
            tool_calls: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "thinking":
                    thinking.append(str(block.get("thinking") or ""))
                elif block_type == "text":
                    text_parts.append(str(block.get("text") or ""))
                elif block_type in {"tool_use", "toolCall"}:
                    arguments = block.get("input", block.get("arguments", {}))
                    args_text = ", ".join(
                        f"{key}={json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
                        for key, value in arguments.items()
                    )
                    tool_calls.append(
                        f"{block.get('name', '')}({args_text})"
                    )
            if thinking:
                thinking_text = "\n".join(thinking)
                parts.append(f"[Assistant thinking]: {thinking_text}")
            if text_parts:
                assistant_text = "\n".join(text_parts)
                parts.append(f"[Assistant]: {assistant_text}")
            if tool_calls:
                parts.append(
                    f"[Assistant tool calls]: {'; '.join(tool_calls)}"
                )
    return "\n\n".join(parts)


def _truncate_tool_result(text: str) -> str:
    if _js_length(text) <= 2_000:
        return text
    prefix = text.encode("utf-16-le", errors="surrogatepass")[: 2_000 * 2]
    kept = prefix.decode("utf-16-le", errors="ignore")
    return (
        f"{kept}\n\n"
        f"[... {_js_length(text) - 2_000} more characters truncated]"
    )


def _extract_file_operations(
    messages: list[dict[str, Any]],
    *,
    previous_summary: str | None,
) -> tuple[list[str], list[str]]:
    read: set[str] = set()
    modified: set[str] = set()
    if previous_summary:
        read.update(_extract_xml_lines(previous_summary, "read-files"))
        modified.update(_extract_xml_lines(previous_summary, "modified-files"))
    for message in messages:
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") not in {
                "tool_use",
                "toolCall",
            }:
                continue
            name = str(block.get("name") or "")
            arguments = block.get("input", block.get("arguments", {}))
            path = str(arguments.get("path") or "")
            if not path:
                continue
            if name == "read":
                read.add(path)
            elif name in {"edit", "write"}:
                modified.add(path)
    return sorted(read - modified), sorted(modified)


def _extract_xml_lines(text: str, tag: str) -> list[str]:
    match = re.search(
        rf"<{re.escape(tag)}>\n(.*?)\n</{re.escape(tag)}>",
        text,
        flags=re.DOTALL,
    )
    return match.group(1).splitlines() if match else []


def _format_file_operations(
    read_files: list[str],
    modified_files: list[str],
) -> str:
    sections: list[str] = []
    if read_files:
        sections.append(
            "<read-files>\n" + "\n".join(read_files) + "\n</read-files>"
        )
    if modified_files:
        sections.append(
            "<modified-files>\n"
            + "\n".join(modified_files)
            + "\n</modified-files>"
        )
    return "\n\n" + "\n\n".join(sections) if sections else ""


__all__ = [
    "PI_COMPACTION_KEEP_RECENT_TOKENS",
    "PI_COMPACTION_RESERVE_TOKENS",
    "PiCompactionResult",
    "PiContextTransformRouter",
    "PiSessionCompactor",
    "estimate_message_tokens",
    "estimate_messages_tokens",
    "is_context_overflow_error",
]
