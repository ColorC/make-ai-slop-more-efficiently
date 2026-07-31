# [OMNI] origin=codex domain=services/agent ts=2026-07-24T00:00:00Z type=infrastructure
# [OMNI] material_id="material:core.agent.context_fork.contract_and_runtime.py"
"""True context-fork contract inside the existing Agent runtime.

The primitive launch surface remains the existing Agent tool. This module adds
checkpoint inheritance and a reference-only receipt; it is not another agent
launcher and it never creates another Team type.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from contextlib import suppress
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from omnicompany.packages.services._core.agent._bus import emit_agent_signal


CONTEXT_FORK_CHECKPOINT_KEY = "context_fork_checkpoint"
CONTEXT_FORK_SCHEMA_VERSION = "1"
_ALLOWED_MESSAGE_ROLES = {"user", "assistant"}


class ContextForkStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    ABORTED = "aborted"
    TIMED_OUT = "timed_out"


class AgentContextCheckpoint(BaseModel):
    """Immutable-by-convention snapshot taken before the parent's fork tool call.

    Parent system prompts, credentials, permissions, and mutable tool objects are
    deliberately absent. The child uses its own AgentSpec/system prompt.
    """

    schema_version: str = CONTEXT_FORK_SCHEMA_VERSION
    checkpoint_id: str
    parent_trace_id: str
    parent_turn: int = Field(ge=0)
    messages: list[dict[str, Any]]
    context_refs: list[str] = Field(default_factory=list)
    created_at: float

    @model_validator(mode="after")
    def _validate_messages(self) -> "AgentContextCheckpoint":
        for index, message in enumerate(self.messages):
            role = str(message.get("role") or "")
            if role not in _ALLOWED_MESSAGE_ROLES:
                raise ValueError(
                    f"checkpoint message {index} has unsupported role {role!r}; "
                    "system messages and credentials are not inheritable"
                )
        return self


class ContextForkBudget(BaseModel):
    """Enforced child limits, independent of the parent session's limits."""

    timeout_seconds: float = Field(default=300.0, gt=0, le=3600)
    max_turns: int = Field(default=50, ge=1, le=500)
    max_artifact_refs: int = Field(default=100, ge=1, le=1000)


class ContextForkRequest(BaseModel):
    """Validated request derived by the existing Agent tool."""

    description: str = Field(min_length=1)
    task: str = Field(min_length=1)
    subagent_type: str = Field(min_length=1)
    child_trace_id: str = Field(min_length=1)
    parent_trace_id: str = Field(min_length=1)
    checkpoint: AgentContextCheckpoint
    allocation_decision_ref: str = Field(min_length=1)
    budget: ContextForkBudget = Field(default_factory=ContextForkBudget)

    @model_validator(mode="after")
    def _trace_chain_matches_checkpoint(self) -> "ContextForkRequest":
        if self.checkpoint.parent_trace_id != self.parent_trace_id:
            raise ValueError("checkpoint.parent_trace_id does not match request.parent_trace_id")
        if self.child_trace_id == self.parent_trace_id:
            raise ValueError("child_trace_id must differ from parent_trace_id")
        return self


class ContextForkReceipt(BaseModel):
    """Reference-only result returned to the parent Agent.

    Deliberately no ``text``, ``summary``, transcript, or tool-output field. Full
    detail remains addressable by ``child_trace_id`` and artifact references.
    """

    schema_version: str = CONTEXT_FORK_SCHEMA_VERSION
    status: ContextForkStatus
    result_code: str
    parent_trace_id: str
    child_trace_id: str
    checkpoint_id: str
    allocation_decision_ref: str
    artifact_refs: list[str] = Field(default_factory=list)
    verification_ref: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    detail_available: bool = True
    budget: ContextForkBudget


def capture_context_checkpoint(
    *,
    parent_trace_id: str,
    parent_turn: int,
    messages: list[dict[str, Any]],
    context_refs: list[str] | tuple[str, ...] | None = None,
) -> AgentContextCheckpoint:
    """Deep-copy a parent conversation at a stable pre-tool boundary."""
    safe_messages = copy.deepcopy(list(messages))
    safe_refs = _dedupe_strings(context_refs or ())
    digest_payload = json.dumps(
        {
            "parent_trace_id": parent_trace_id,
            "parent_turn": parent_turn,
            "messages": safe_messages,
            "context_refs": safe_refs,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()[:20]
    return AgentContextCheckpoint(
        checkpoint_id=f"ctxfork:{digest}",
        parent_trace_id=parent_trace_id,
        parent_turn=parent_turn,
        messages=safe_messages,
        context_refs=safe_refs,
        created_at=time.time(),
    )


def inherit_context_fork_messages(
    input_data: dict[str, Any],
    initial_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prepend a validated checkpoint to a child's own initial task message."""
    raw_checkpoint = input_data.get(CONTEXT_FORK_CHECKPOINT_KEY)
    if raw_checkpoint is None:
        return initial_messages

    checkpoint = AgentContextCheckpoint.model_validate(raw_checkpoint)
    parent_trace_id = str(input_data.get("parent_trace_id") or "")
    if checkpoint.parent_trace_id != parent_trace_id:
        raise ValueError(
            "context fork checkpoint does not belong to the declared parent trace"
        )

    inherited = copy.deepcopy(checkpoint.messages)
    child_messages = copy.deepcopy(initial_messages)
    if child_messages:
        child_messages[0] = _prefix_child_task(
            child_messages[0],
            checkpoint.checkpoint_id,
        )
    return _merge_adjacent_roles([*inherited, *child_messages])


def validate_context_fork_allocation(
    decision: Any,
    *,
    decision_ref: str,
) -> None:
    """Require an auditable, unblocked Agent allocation decision."""
    if not decision_ref.strip():
        raise ValueError("context_fork requires agent_allocation_decision_ref")

    from omnicompany.packages.services._core.team_builder.agent_allocation import (
        AgentAllocationDecision,
        AgentAllocationMode,
    )

    validated = AgentAllocationDecision.model_validate(decision)
    if validated.mode != AgentAllocationMode.CONTEXT_FORK:
        raise ValueError(
            "context_fork requires AgentAllocationDecision.mode=context_fork"
        )
    if validated.blocked_gates:
        raise ValueError("context_fork allocation decision contains blocked gates")


async def run_context_fork(
    agent: Any,
    request: ContextForkRequest,
    *,
    parent_abort_event: Any | None = None,
) -> ContextForkReceipt:
    """Run a child Agent with timeout/abort limits and return only a receipt."""
    _apply_turn_budget(agent, request.budget.max_turns)
    bus = getattr(agent, "_bus", None)
    await _emit_fork_signal(
        bus,
        trace_id=request.parent_trace_id,
        event_type="agent.context_fork.start",
        payload={
            "child_trace_id": request.child_trace_id,
            "checkpoint_id": request.checkpoint.checkpoint_id,
            "allocation_decision_ref": request.allocation_decision_ref,
            "checkpoint_message_count": len(request.checkpoint.messages),
            "context_ref_count": len(request.checkpoint.context_refs),
            "timeout_seconds": request.budget.timeout_seconds,
            "max_turns": request.budget.max_turns,
        },
    )

    from omnicompany.packages.services._core.agent.spawn_surface import (
        ENTRY_CONTEXT_FORK,
        agent_spawn_metadata,
    )

    child_input = {
        **agent_spawn_metadata(ENTRY_CONTEXT_FORK),
        "task": request.task,
        "instruction": request.task,
        "description": request.description,
        "subagent_type": request.subagent_type,
        "trace_id": request.child_trace_id,
        "parent_trace_id": request.parent_trace_id,
        CONTEXT_FORK_CHECKPOINT_KEY: request.checkpoint.model_dump(mode="python"),
        "agent_execution_mode": "context_fork",
        "agent_allocation_decision_ref": request.allocation_decision_ref,
    }

    child_task = asyncio.create_task(agent.run(child_input))
    abort_task = (
        asyncio.create_task(_wait_for_parent_abort(parent_abort_event))
        if parent_abort_event is not None
        else None
    )
    wait_set = {child_task}
    if abort_task is not None:
        wait_set.add(abort_task)

    try:
        done, _pending = await asyncio.wait(
            wait_set,
            timeout=request.budget.timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if child_task in done:
            verdict = child_task.result()
            receipt = _receipt_from_verdict(verdict, request)
        elif abort_task is not None and abort_task in done:
            await _stop_child(agent, child_task)
            receipt = _terminal_receipt(
                request,
                status=ContextForkStatus.ABORTED,
                result_code="parent_aborted",
                risk_flags=["parent_abort_propagated"],
            )
        else:
            await _stop_child(agent, child_task)
            receipt = _terminal_receipt(
                request,
                status=ContextForkStatus.TIMED_OUT,
                result_code="timeout",
                risk_flags=["fork_timeout"],
            )
    except asyncio.CancelledError:
        await _stop_child(agent, child_task)
        raise
    except Exception as exc:
        await _stop_child(agent, child_task)
        receipt = _terminal_receipt(
            request,
            status=ContextForkStatus.FAILED,
            result_code=f"exception:{type(exc).__name__}",
            risk_flags=["child_exception"],
        )
    finally:
        if abort_task is not None:
            abort_task.cancel()
            with suppress(asyncio.CancelledError):
                await abort_task

    await _emit_fork_signal(
        bus,
        trace_id=request.parent_trace_id,
        event_type="agent.context_fork.finish",
        payload=receipt.model_dump(mode="json"),
    )
    return receipt


async def _wait_for_parent_abort(abort_event: Any) -> None:
    while not bool(abort_event.is_set()):
        await asyncio.sleep(0.05)


async def _stop_child(agent: Any, child_task: asyncio.Task[Any]) -> None:
    abort = getattr(agent, "abort", None)
    if callable(abort):
        abort()
    if not child_task.done():
        child_task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await child_task


def _apply_turn_budget(agent: Any, requested_max_turns: int) -> None:
    config = getattr(agent, "_config", None)
    if config is None or not hasattr(config, "max_turns"):
        return
    existing = config.max_turns
    if existing is None or requested_max_turns < existing:
        config.max_turns = requested_max_turns


def _receipt_from_verdict(
    verdict: Any,
    request: ContextForkRequest,
) -> ContextForkReceipt:
    kind = str(getattr(getattr(verdict, "kind", None), "value", "")).lower()
    status = {
        "pass": ContextForkStatus.SUCCEEDED,
        "partial": ContextForkStatus.PARTIAL,
        "fail": ContextForkStatus.FAILED,
    }.get(kind, ContextForkStatus.FAILED)
    output = getattr(verdict, "output", None)
    output = output if isinstance(output, dict) else {}
    refs = _extract_artifact_refs(output)[: request.budget.max_artifact_refs]
    risk_flags = _dedupe_strings(output.get("risk_flags") or ())
    return ContextForkReceipt(
        status=status,
        result_code=str(output.get("result_code") or kind or "unknown"),
        parent_trace_id=request.parent_trace_id,
        child_trace_id=request.child_trace_id,
        checkpoint_id=request.checkpoint.checkpoint_id,
        allocation_decision_ref=request.allocation_decision_ref,
        artifact_refs=refs,
        verification_ref=_optional_string(output.get("verification_ref")),
        risk_flags=risk_flags,
        budget=request.budget,
    )


def _terminal_receipt(
    request: ContextForkRequest,
    *,
    status: ContextForkStatus,
    result_code: str,
    risk_flags: list[str],
) -> ContextForkReceipt:
    return ContextForkReceipt(
        status=status,
        result_code=result_code,
        parent_trace_id=request.parent_trace_id,
        child_trace_id=request.child_trace_id,
        checkpoint_id=request.checkpoint.checkpoint_id,
        allocation_decision_ref=request.allocation_decision_ref,
        risk_flags=risk_flags,
        budget=request.budget,
    )


def _extract_artifact_refs(output: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for item in output.get("artifact_refs") or ():
        if isinstance(item, str):
            refs.append(item)
    for item in output.get("artifacts") or ():
        if isinstance(item, str):
            refs.append(item)
        elif isinstance(item, dict):
            value = item.get("ref") or item.get("artifact_ref")
            if isinstance(value, str):
                refs.append(value)
    return _dedupe_strings(refs)


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _dedupe_strings(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized and normalized not in seen:
            out.append(normalized)
            seen.add(normalized)
    return out


def _prefix_child_task(message: dict[str, Any], checkpoint_id: str) -> dict[str, Any]:
    prefixed = copy.deepcopy(message)
    marker = (
        f"[CONTEXT_FORK_TASK checkpoint={checkpoint_id}] "
        "The preceding messages are an inherited checkpoint, not a summary.\n"
    )
    content = prefixed.get("content")
    if isinstance(content, str):
        prefixed["content"] = marker + content
    elif isinstance(content, list):
        prefixed["content"] = [{"type": "text", "text": marker}, *content]
    else:
        prefixed["content"] = marker + str(content)
    return prefixed


def _merge_adjacent_roles(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for message in messages:
        current = copy.deepcopy(message)
        if merged and merged[-1].get("role") == current.get("role"):
            merged[-1]["content"] = _merge_content(
                merged[-1].get("content"),
                current.get("content"),
            )
        else:
            merged.append(current)
    return merged


def _merge_content(left: Any, right: Any) -> list[dict[str, Any]]:
    return [*_as_content_blocks(left), *_as_content_blocks(right)]


def _as_content_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        return copy.deepcopy(content)
    return [{"type": "text", "text": str(content or "")}]


async def _emit_fork_signal(
    bus: Any,
    *,
    trace_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    if bus is None:
        return
    await emit_agent_signal(
        bus,
        trace_id=trace_id,
        event_type=event_type,
        source="agent.context_fork",
        payload=payload,
    )


__all__ = [
    "CONTEXT_FORK_CHECKPOINT_KEY",
    "CONTEXT_FORK_SCHEMA_VERSION",
    "AgentContextCheckpoint",
    "ContextForkBudget",
    "ContextForkReceipt",
    "ContextForkRequest",
    "ContextForkStatus",
    "capture_context_checkpoint",
    "inherit_context_fork_messages",
    "run_context_fork",
    "validate_context_fork_allocation",
]
