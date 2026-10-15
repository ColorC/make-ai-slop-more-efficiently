from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from omnicompany.runtime.agent_runtime import AgentRuntimeProcessClient
from omnicompany.runtime.agent_runtime.structured_runner import (
    GIT_EVIDENCE_TOOL,
    READONLY_WORKSPACE_TOOLS,
    run_readonly_structured_agent,
)


class _Invoker:
    def __init__(self, *, status: str = "succeeded") -> None:
        self.status = status
        self.requests: list[dict[str, Any]] = []

    async def run(self, request: dict[str, Any], event_sink=None) -> dict[str, Any]:
        self.requests.append(request)
        if self.status != "succeeded":
            return {
                "kind": "run.result",
                "protocol_version": "0.1.0",
                "request_id": request["request_id"],
                "run_id": request["run_id"],
                "session_id": request["session_id"],
                "status": "failed",
                "final_output": {"text": ""},
                "material_refs": [],
                "artifact_refs": [],
                "usage": {},
                "error": {
                    "code": "invalid_output",
                    "message": "schema mismatch",
                    "retryable": False,
                },
            }
        return {
            "kind": "run.result",
            "protocol_version": "0.1.0",
            "request_id": request["request_id"],
            "run_id": request["run_id"],
            "session_id": request["session_id"],
            "status": "succeeded",
            "final_output": {"text": '{"answer":"ok"}', "structured": {"answer": "ok"}},
            "material_refs": [],
            "artifact_refs": [],
            "usage": {"input_tokens": 10, "output_tokens": 4},
            "metadata": {"tool_calls": {"count": 1, "names": ["omni_workspace_read"]}},
        }


@pytest.mark.asyncio
async def test_structured_runner_builds_a_bounded_readonly_contract(tmp_path: Path) -> None:
    invoker = _Invoker()
    result = await run_readonly_structured_agent(
        task="Inspect one file and answer.",
        instructions="Return the requested object.",
        result_schema={
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        },
        workspace_cwd=tmp_path,
        caller="testmap.review",
        invoker=invoker,
    )

    assert result["ok"] is True
    assert result["final"] == {"answer": "ok"}
    assert result["runtime"]["metadata"]["tool_calls"]["count"] == 1
    request = invoker.requests[0]
    AgentRuntimeProcessClient(["unused"]).validate(request)
    assert request["position_id"] == "testmap.review"
    assert request["capability_grant"]["allowed_tools"] == list(
        READONLY_WORKSPACE_TOOLS
    )
    assert request["capability_grant"]["allowed_read_roots"] == [str(tmp_path)]
    assert request["capability_grant"]["network_access"] is False
    assert request["budget"]["deadline_at"] == request["capability_grant"]["expires_at"]


@pytest.mark.asyncio
async def test_structured_runner_surfaces_runtime_failure_without_fallback(
    tmp_path: Path,
) -> None:
    invoker = _Invoker(status="failed")

    result = await run_readonly_structured_agent(
        task="Inspect.",
        instructions="Return JSON.",
        result_schema={"type": "object"},
        workspace_cwd=tmp_path,
        caller="testmap.review",
        invoker=invoker,
    )

    assert result["ok"] is False
    assert result["error"] == "schema mismatch"
    assert result["runtime"]["error_code"] == "invalid_output"


@pytest.mark.asyncio
async def test_structured_runner_grants_explicit_git_tool_and_extra_read_root(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    transcript_root = tmp_path / "transcripts"
    workspace.mkdir()
    transcript_root.mkdir()
    invoker = _Invoker()

    await run_readonly_structured_agent(
        task="Audit evidence.",
        instructions="Return JSON.",
        result_schema={"type": "object"},
        workspace_cwd=workspace,
        caller="plan-audit",
        allowed_tools=(*READONLY_WORKSPACE_TOOLS, GIT_EVIDENCE_TOOL),
        allowed_read_roots=(transcript_root,),
        invoker=invoker,
    )

    grant = invoker.requests[0]["capability_grant"]
    assert grant["allowed_tools"] == [*READONLY_WORKSPACE_TOOLS, GIT_EVIDENCE_TOOL]
    assert grant["allowed_read_roots"] == [str(workspace), str(transcript_root)]


@pytest.mark.asyncio
async def test_structured_runner_rejects_tools_outside_readonly_profile(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="not read-only profile tools"):
        await run_readonly_structured_agent(
            task="Do not run.",
            instructions="Return JSON.",
            result_schema={"type": "object"},
            workspace_cwd=tmp_path,
            caller="unsafe",
            allowed_tools=("bash",),
            invoker=_Invoker(),
        )
