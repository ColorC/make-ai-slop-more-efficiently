from __future__ import annotations

import pytest

from omnicompany.bus.memory import MemoryBus
from omnicompany.runtime.agent_runtime import AgentRuntimeDomainEventProjector


@pytest.mark.asyncio
async def test_projector_keeps_only_domain_milestones() -> None:
    bus = MemoryBus()
    projector = AgentRuntimeDomainEventProjector(bus)
    messages = [
        {
            "kind": "run.state",
            "run_id": "run-projection",
            "state": "starting",
        },
        {
            "kind": "run.state",
            "run_id": "run-projection",
            "state": "running",
        },
        {
            "kind": "run.output.delta",
            "run_id": "run-projection",
            "text": "private streamed content",
        },
        {
            "kind": "run.material.proposed",
            "run_id": "run-projection",
            "material_ref": "material:proposal-1",
        },
        {
            "kind": "run.result",
            "run_id": "run-projection",
            "session_id": "session-projection",
            "status": "succeeded",
            "final_output": {"text": "private final content"},
            "material_refs": ["material:proposal-1"],
            "artifact_refs": [],
            "usage": {"duration_ms": 12},
        },
    ]

    for message in messages:
        await projector.observe(message)

    assert [event.event_type for event in bus._events] == [
        "agent_runtime.run.started",
        "agent_runtime.run.completed",
    ]
    serialized = "\n".join(event.model_dump_json() for event in bus._events)
    assert "private streamed content" not in serialized
    assert "private final content" not in serialized
    assert "material:proposal-1" in serialized


@pytest.mark.asyncio
async def test_projector_records_approval_and_failure_without_private_trace() -> None:
    bus = MemoryBus()
    projector = AgentRuntimeDomainEventProjector(bus)

    await projector.observe(
        {
            "kind": "run.state",
            "run_id": "run-failed",
            "state": "awaiting_human",
            "checkpoint_ref": "humanbus://question/hq-1",
        }
    )
    await projector.observe(
        {
            "kind": "run.approval.requested",
            "run_id": "run-failed",
            "approval_id": "hq-1",
            "action": "publish material",
            "reason": "human authority required",
        }
    )
    await projector.observe(
        {
            "kind": "run.result",
            "run_id": "run-failed",
            "session_id": "session-failed",
            "status": "permission_denied",
            "final_output": {"text": "must not leak"},
            "material_refs": [],
            "artifact_refs": [],
            "usage": {},
            "error": {
                "code": "approval_rejected",
                "message": "private explanation",
                "retryable": False,
            },
        }
    )

    assert [event.event_type for event in bus._events] == [
        "agent_runtime.run.awaiting_human",
        "agent_runtime.approval.requested",
        "agent_runtime.run.failed",
    ]
    serialized = "\n".join(event.model_dump_json() for event in bus._events)
    assert "must not leak" not in serialized
    assert "private explanation" not in serialized
    assert "approval_rejected" in serialized
