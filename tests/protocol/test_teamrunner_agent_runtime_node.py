from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
from typing import Any

import pytest

from omnicompany.bus.memory import MemoryBus
from omnicompany.protocol.anchor import (
    AnchorSpec,
    Route,
    RouteAction,
    ValidatorKind,
    ValidatorSpec,
    VerdictKind,
)
from omnicompany.protocol.team import InfoAuditMode, NodeKind, TeamNode, TeamSpec
from omnicompany.runtime.exec.agent_runtime_node import (
    AgentRuntimeNodePolicy,
    AgentRuntimeNodeRouter,
)
from omnicompany.runtime.exec.runner import TeamRunner
from omnicompany.runtime.agent_runtime.client import AgentRuntimeProcessClient


class _Invoker:
    def __init__(self, label: str) -> None:
        self.label = label
        self.requests: list[dict[str, Any]] = []

    async def run(self, request: dict[str, Any], event_sink=None) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "kind": "run.result",
            "protocol_version": "0.1.0",
            "request_id": request["request_id"],
            "run_id": request["run_id"],
            "session_id": request["session_id"],
            "status": "succeeded",
            "final_output": {
                "text": f"finished by {self.label}",
                "structured": {"answer": self.label},
            },
            "material_refs": ["material:output"],
            "artifact_refs": [],
            "usage": {"input_tokens": 10, "output_tokens": 2},
            "metadata": {
                "tool_calls": {"count": 1, "names": ["omni_workspace_read"]}
            },
        }


def _team() -> TeamSpec:
    return TeamSpec(
        id="team:runtime-port",
        name="runtime-port",
        description="TeamSpec remains unchanged across runtime implementations",
        entry="agent",
        nodes=[
            TeamNode(
                id="agent",
                kind=NodeKind.ANCHOR,
                anchor=AnchorSpec(
                    id="agent-anchor",
                    name="agent",
                    format_in="task.request",
                    format_out="task.result",
                    validator=ValidatorSpec(
                        id="agent-validator",
                        kind=ValidatorKind.SOFT,
                        description="external agent runtime",
                    ),
                    routes={VerdictKind.PASS: Route(action=RouteAction.EMIT)},
                ),
            )
        ],
        edges=[],
    )


def _router(invoker: _Invoker) -> AgentRuntimeNodeRouter:
    return AgentRuntimeNodeRouter(
        invoker,
        AgentRuntimeNodePolicy(
            provider="provider:test",
            model="model:test",
            workspace_cwd=".",
            capability_grant=lambda _input: {
                "grant_id": "grant:team-node",
                "policy_ref": "policy:readonly",
                "permission_mode": "readonly",
                "allowed_tools": [],
                "allowed_read_roots": [str(Path(".").resolve())],
                "allowed_write_paths": [],
                "allowed_write_roots": [],
                "allowed_shell_roots": [],
                "allowed_shell_command_prefixes": [],
                "network_access": False,
            },
            budget={"max_steps": 3, "max_output_tokens": 1000},
            team_id="team:runtime-port",
            position_id="agent",
            instructions="complete the assigned team position",
            material_refs=lambda value: value.get("material_refs", []),
            task_id=lambda value: value.get("task_id"),
            parent_run_id=lambda value: value.get("parent_run_id"),
        ),
        description="complete the assigned team position",
        format_in="task.request",
        format_out="task.result",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ["fake", "alternate"])
async def test_same_teamspec_delegates_node_through_replaceable_runtime(implementation: str) -> None:
    invoker = _Invoker(implementation)
    runner = TeamRunner(
        _team(),
        {"agent": _router(invoker)},
        MemoryBus(),
        max_steps=3,
        info_audit_mode=InfoAuditMode.OFF,
    )

    result = await runner.run(
        {
            "task": "inspect",
            "task_id": "task:inspect",
            "material_refs": ["material:input"],
            "parent_run_id": "run-parent",
        }
    )

    assert result == {"answer": implementation}
    assert len(invoker.requests) == 1
    request = invoker.requests[0]
    AgentRuntimeProcessClient(["unused-runtime"]).validate(request)
    assert request["team_id"] == "team:runtime-port"
    assert request["position_id"] == "agent"
    assert request["parent_run_id"] == "run-parent"
    assert request["task_id"] == "task:inspect"
    assert request["input"]["material_refs"] == ["material:input"]
    assert request["input"]["instructions"] == "complete the assigned team position"
    assert "cordis" not in repr(request).lower()


def test_runtime_failure_maps_to_fail_without_leaking_child_context() -> None:
    verdict = AgentRuntimeNodeRouter._verdict(
        {
            "run_id": "run-failed",
            "session_id": "session-child",
            "status": "permission_denied",
            "error": {
                "code": "permission_denied",
                "message": "grant rejected",
                "retryable": False,
            },
            "usage": {},
            "material_refs": [],
            "artifact_refs": [],
            "metadata": {
                "tool_calls": {"count": 1, "names": ["omni_workspace_read"]}
            },
        }
    )

    assert verdict.kind == VerdictKind.FAIL
    assert verdict.output is None
    assert verdict.diagnosis == "grant rejected"
    assert verdict.details["agent_runtime"]["session_id"] == "session-child"
    assert verdict.details["agent_runtime"]["metadata"]["tool_calls"] == {
        "count": 1,
        "names": ["omni_workspace_read"],
    }


@pytest.mark.asyncio
async def test_teamrunner_node_delegates_to_real_dsh_process(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    service_root = repo_root / "services" / "agent-runtime-dsh"
    host = service_root / "dist" / "stdio-host.js"
    runtime_bin = (
        service_root
        / "node_modules"
        / "@deepseek-ai"
        / "dsh-sdk-jsonrpc-demo"
        / "lib"
        / "bin.js"
    )
    profile = service_root / "profiles" / "readonly-replay.cordis.yml"
    replay = service_root / "test" / "fixtures" / "dsh-replay-session.jsonl"
    node = shutil.which("node")
    if node is None or not all(path.is_file() for path in (host, runtime_bin, profile, replay)):
        pytest.skip("built DeepSeek Harness integration artifacts are unavailable")

    environment = {
        **os.environ,
        "OMNI_DSH_RUNTIME_ID": "dsh-teamrunner-integration",
        "OMNI_DSH_RUNTIME_COMMAND": node,
        "OMNI_DSH_RUNTIME_ARGS_JSON": json.dumps([str(runtime_bin), str(profile)]),
        "OMNI_DSH_ALLOWED_TOOLS_JSON": "[]",
        "OMNI_DSH_REQUEST_TIMEOUT_MS": "20000",
        "DSH_CORDIS_CONFIG": str(profile),
        "DSH_SESSION_ROOT": str(tmp_path / "sessions"),
        "DSH_SNAPSHOT_FILE": str(replay),
    }
    async with AgentRuntimeProcessClient(
        [node, str(host)],
        cwd=tmp_path,
        env=environment,
    ) as client:
        router = AgentRuntimeNodeRouter(
            client,
            AgentRuntimeNodePolicy(
                provider="deepseek-official",
                model="deepseek-v4-flash",
                workspace_cwd=tmp_path,
                capability_grant=lambda _input: {
                    "grant_id": "grant:teamrunner-real-dsh",
                    "policy_ref": "policy:readonly",
                    "permission_mode": "readonly",
                    "allowed_tools": [],
                    "allowed_read_roots": [str(tmp_path)],
                    "allowed_write_paths": [],
                    "allowed_write_roots": [],
                    "allowed_shell_roots": [],
                    "allowed_shell_command_prefixes": [],
                    "network_access": False,
                },
                budget={"max_steps": 3, "max_output_tokens": 1000},
                team_id="team:runtime-port",
                position_id="agent",
                instructions="complete the assigned team position",
            ),
            description="complete the assigned team position",
            format_in="task.request",
            format_out="task.result",
        )
        runner = TeamRunner(
            _team(),
            {"agent": router},
            MemoryBus(),
            max_steps=3,
            info_audit_mode=InfoAuditMode.OFF,
        )

        result = await runner.run({"task": "complete the replayed Team node"})

    assert result == {"text": "Omni DSH subprocess OK"}
