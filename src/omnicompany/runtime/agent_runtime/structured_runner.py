"""Bounded structured read-only jobs over the external AgentRuntime contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from typing import Any, Mapping
import uuid

from omnicompany.runtime.exec.agent_runtime_node import AgentRuntimeInvoker

from .profiles import build_the_company_dsh_readonly_invoker


WORKSPACE_READ_TOOL = "omni_workspace_read"
WORKSPACE_LIST_TOOL = "omni_workspace_list"
WORKSPACE_SEARCH_TOOL = "omni_workspace_search"
WORKSPACE_READ_LINES_TOOL = "omni_workspace_read_lines"
GIT_EVIDENCE_TOOL = "omni_git_evidence"
READONLY_WORKSPACE_TOOLS = (
    WORKSPACE_READ_TOOL,
    WORKSPACE_LIST_TOOL,
    WORKSPACE_SEARCH_TOOL,
)
READONLY_AGENT_TOOLS = (
    *READONLY_WORKSPACE_TOOLS,
    WORKSPACE_READ_LINES_TOOL,
    GIT_EVIDENCE_TOOL,
)


async def run_readonly_structured_agent(
    *,
    task: str,
    instructions: str,
    result_schema: Mapping[str, Any],
    workspace_cwd: str | os.PathLike[str],
    caller: str,
    provider: str = "the_company",
    model: str = "deepseek-v4.1-flash",
    max_steps: int = 16,
    max_output_tokens: int = 4096,
    timeout_seconds: int = 120,
    allowed_tools: tuple[str, ...] = READONLY_WORKSPACE_TOOLS,
    allowed_read_roots: tuple[str | os.PathLike[str], ...] = (),
    invoker: AgentRuntimeInvoker | None = None,
) -> dict[str, Any]:
    """Run one structured read-only job without importing the legacy loop."""
    if not task.strip() or not instructions.strip() or not caller.strip():
        raise ValueError("structured AgentRuntime task identity cannot be blank")
    if not result_schema:
        raise ValueError("structured AgentRuntime requires a result schema")
    if max_steps < 1 or max_output_tokens < 1 or timeout_seconds < 1:
        raise ValueError("structured AgentRuntime limits must be positive")

    workspace = Path(workspace_cwd).resolve()
    unknown_tools = sorted(set(allowed_tools) - set(READONLY_AGENT_TOOLS))
    if unknown_tools:
        raise ValueError(f"structured AgentRuntime tools are not read-only profile tools: {unknown_tools}")
    read_roots = [workspace]
    for root in allowed_read_roots:
        resolved = Path(root).resolve()
        if resolved not in read_roots:
            read_roots.append(resolved)
    runtime = invoker or build_the_company_dsh_readonly_invoker(
        workspace,
        allowed_tools=allowed_tools,
    )
    run_id = f"run-{uuid.uuid4().hex}"
    expires_at = datetime.now(UTC) + timedelta(seconds=timeout_seconds)
    request: dict[str, Any] = {
        "kind": "run.start",
        "protocol_version": "0.1.0",
        "request_id": f"req-{uuid.uuid4().hex}",
        "run_id": run_id,
        "session_id": f"structured-{caller}-{uuid.uuid4().hex}",
        "task_id": f"task:{caller}:{uuid.uuid4().hex}",
        "team_id": "omni.readonly-structured-agent",
        "position_id": caller,
        "provider": provider,
        "model": model,
        "workspace_cwd": str(workspace),
        "input": {
            "instructions": instructions,
            "text": task,
            "material_refs": [],
            "artifact_refs": [],
            "expected_output_schema": dict(result_schema),
        },
        "context": {
            "context_refs": [],
            "material_refs": [],
            "artifact_refs": [],
            "inline_context": [],
            "provenance": [
                "plan:_cross/[2026-08-14]AGENT-RUNTIME-EXTERNALIZATION"
            ],
        },
        "capability_grant": {
            "grant_id": f"grant:{caller}:{uuid.uuid4().hex}",
            "policy_ref": "policy:agent-runtime-readonly-workspace-query",
            "permission_mode": "readonly",
            "allowed_tools": list(allowed_tools),
            "allowed_material_refs": [],
            "allowed_read_roots": [str(root) for root in read_roots],
            "allowed_write_paths": [],
            "allowed_write_roots": [],
            "allowed_shell_roots": [],
            "allowed_shell_command_prefixes": [],
            "network_access": False,
            "expires_at": expires_at.isoformat(),
        },
        "budget": {
            "deadline_at": expires_at.isoformat(),
            "max_output_tokens": max_output_tokens,
            "max_steps": max_steps,
        },
        "metadata": {"source": "omni.readonly-structured-agent", "caller": caller},
    }
    result = await runtime.run(request)
    if result.get("status") != "succeeded":
        error = result.get("error")
        message = error.get("message") if isinstance(error, Mapping) else None
        return {
            "ok": False,
            "final": None,
            "text": str(result.get("final_output", {}).get("text", "")),
            "turn_count": 0,
            "error": str(message or f"AgentRuntime ended with {result.get('status')}"),
            "runtime": {
                "run_id": result.get("run_id", run_id),
                "status": result.get("status"),
                "error_code": error.get("code") if isinstance(error, Mapping) else None,
                "metadata": dict(result.get("metadata") or {}),
            },
        }
    final_output = result.get("final_output")
    structured = final_output.get("structured") if isinstance(final_output, Mapping) else None
    if not isinstance(structured, Mapping):
        return {
            "ok": False,
            "final": None,
            "text": "" if not isinstance(final_output, Mapping) else str(final_output.get("text", "")),
            "turn_count": 0,
            "error": "AgentRuntime succeeded without structured output",
            "runtime": {"run_id": result.get("run_id", run_id), "status": "failed"},
        }
    return {
        "ok": True,
        "final": dict(structured),
        "text": str(final_output.get("text", "")),
        "turn_count": 0,
        "error": "",
        "runtime": {
            "run_id": result.get("run_id", run_id),
            "status": "succeeded",
            "usage": dict(result.get("usage") or {}),
            "metadata": dict(result.get("metadata") or {}),
        },
    }


__all__ = [
    "GIT_EVIDENCE_TOOL",
    "READONLY_AGENT_TOOLS",
    "READONLY_WORKSPACE_TOOLS",
    "WORKSPACE_LIST_TOOL",
    "WORKSPACE_READ_TOOL",
    "WORKSPACE_READ_LINES_TOOL",
    "WORKSPACE_SEARCH_TOOL",
    "run_readonly_structured_agent",
]
