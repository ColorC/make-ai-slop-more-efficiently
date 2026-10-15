from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from omnicompany.runtime.agent_runtime import (
    AgentRuntimeApprovalCoordinator,
    AgentRuntimeApprovalError,
    AgentRuntimeProcessClient,
    AwaitingHumanRun,
)
from omnicompany.runtime.buses import HumanBus


ROOT = Path(__file__).resolve().parents[2]
RUN_FIXTURE = json.loads(
    (ROOT / "contracts/agent-runtime/v0/fixtures/valid/run-start-readonly.json").read_text(
        encoding="utf-8"
    )
)

FAKE_HOST = r"""
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message['kind'] != 'run.start':
        continue
    print(json.dumps({
        'kind': 'run.state',
        'protocol_version': '0.1.0',
        'run_id': message['run_id'],
        'sequence': 0,
        'timestamp': '2026-08-14T00:00:00Z',
        'state': 'running',
    }), flush=True)
    print(json.dumps({
        'kind': 'run.result',
        'protocol_version': '0.1.0',
        'request_id': message['request_id'],
        'run_id': message['run_id'],
        'session_id': message['session_id'],
        'status': 'succeeded',
        'final_output': {'text': 'approved fixture run'},
        'material_refs': [],
        'artifact_refs': [],
        'usage': {'duration_ms': 1},
    }), flush=True)
"""


def _request(tmp_path: Path) -> dict:
    request = json.loads(json.dumps(RUN_FIXTURE))
    request["workspace_cwd"] = str(tmp_path)
    request["capability_grant"]["allowed_read_roots"] = [str(tmp_path)]
    return request


def _bus(tmp_path: Path) -> HumanBus:
    return HumanBus(
        audit_log_path=tmp_path / "human-audit.jsonl",
        inbox_path=tmp_path / "human-inbox.db",
    )


@pytest.mark.asyncio
async def test_approval_survives_restart_and_runs_only_after_explicit_approval(
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "runtime-approvals.db"
    first_client = AgentRuntimeProcessClient(["missing-runtime-never-started"])
    first = AgentRuntimeApprovalCoordinator(
        first_client,
        human_bus=_bus(tmp_path),
        checkpoint_path=checkpoint,
    )
    awaiting = first.request_approval(
        _request(tmp_path),
        action="produce a review draft",
        reason="a human must approve this run before execution",
    )

    assert awaiting.event["state"] == "awaiting_human"
    assert awaiting.checkpoint_ref.endswith(awaiting.approval_id)
    await first_client.close()

    second_bus = _bus(tmp_path)
    second_client = AgentRuntimeProcessClient([sys.executable, "-u", "-c", FAKE_HOST])
    second = AgentRuntimeApprovalCoordinator(
        second_client,
        human_bus=second_bus,
        checkpoint_path=checkpoint,
    )
    still_waiting = await second.resume(awaiting.approval_id)
    assert isinstance(still_waiting, AwaitingHumanRun)

    second_bus.resolve(awaiting.approval_id, "approve", resolver="test-human")
    events: list[dict] = []
    result = await second.resume(awaiting.approval_id, events.append)
    repeated = await second.resume(awaiting.approval_id)
    await second_client.close()

    assert result["status"] == "succeeded"
    assert result["final_output"]["text"] == "approved fixture run"
    assert repeated == result
    assert [event["kind"] for event in events] == ["run.state"]


@pytest.mark.asyncio
async def test_rejected_approval_never_starts_runtime(tmp_path: Path) -> None:
    client = AgentRuntimeProcessClient(["missing-runtime-must-not-start"])
    bus = _bus(tmp_path)
    coordinator = AgentRuntimeApprovalCoordinator(
        client,
        human_bus=bus,
        checkpoint_path=tmp_path / "runtime-approvals.db",
    )
    awaiting = coordinator.request_approval(
        _request(tmp_path),
        action="attempt a gated run",
        reason="test explicit rejection",
    )
    bus.resolve(awaiting.approval_id, "reject", resolver="test-human")

    result = await coordinator.resume(awaiting.approval_id)
    await client.close()

    assert result["status"] == "permission_denied"
    assert result["error"]["code"] == "approval_rejected"


@pytest.mark.asyncio
async def test_ambiguous_approval_answer_fails_closed(tmp_path: Path) -> None:
    client = AgentRuntimeProcessClient(["missing-runtime-must-not-start"])
    bus = _bus(tmp_path)
    coordinator = AgentRuntimeApprovalCoordinator(
        client,
        human_bus=bus,
        checkpoint_path=tmp_path / "runtime-approvals.db",
    )
    awaiting = coordinator.request_approval(
        _request(tmp_path),
        action="attempt a gated run",
        reason="test ambiguous response",
    )
    bus.resolve(awaiting.approval_id, "maybe later", resolver="test-human")

    with pytest.raises(AgentRuntimeApprovalError, match="explicit approve/reject"):
        await coordinator.resume(awaiting.approval_id)

    await client.close()
