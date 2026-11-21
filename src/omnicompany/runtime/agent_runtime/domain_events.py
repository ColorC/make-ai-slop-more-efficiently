"""Whitelist projection from runtime messages to the Omni domain event log."""

from __future__ import annotations

from typing import Any

from omnicompany.bus.base import EventBus
from omnicompany.protocol.events import FactoryEvent

from .client import RuntimeMessage


class AgentRuntimeDomainEventProjector:
    """Publish only durable run milestones, never the harness event stream."""

    def __init__(self, bus: EventBus, *, source: str = "agent_runtime.external") -> None:
        self.bus = bus
        self.source = source

    async def observe(self, message: RuntimeMessage) -> str | None:
        event = self.project(message)
        if event is None:
            return None
        return await self.bus.publish(event)

    def project(self, message: RuntimeMessage) -> FactoryEvent | None:
        kind = message.get("kind")
        run_id = str(message.get("run_id") or "")
        if not run_id:
            return None
        if kind == "run.state":
            state = message.get("state")
            if state == "starting":
                return self._event(run_id, "agent_runtime.run.started", {"status": state})
            if state == "awaiting_human":
                return self._event(
                    run_id,
                    "agent_runtime.run.awaiting_human",
                    {
                        "status": state,
                        "checkpoint_ref": message.get("checkpoint_ref"),
                    },
                )
            return None
        if kind == "run.approval.requested":
            return self._event(
                run_id,
                "agent_runtime.approval.requested",
                {
                    "approval_id": message.get("approval_id"),
                    "action": message.get("action"),
                    "reason": message.get("reason"),
                    "checkpoint_ref": message.get("checkpoint_ref"),
                },
            )
        if kind != "run.result":
            return None
        status = str(message.get("status") or "failed")
        event_type = (
            "agent_runtime.run.completed"
            if status == "succeeded"
            else "agent_runtime.run.failed"
        )
        error = message.get("error") if isinstance(message.get("error"), dict) else {}
        return self._event(
            run_id,
            event_type,
            {
                "status": status,
                "session_id": message.get("session_id"),
                "material_refs": list(message.get("material_refs") or []),
                "artifact_refs": list(message.get("artifact_refs") or []),
                "usage": dict(message.get("usage") or {}),
                "error_code": error.get("code"),
                "retryable": error.get("retryable"),
            },
        )

    def _event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> FactoryEvent:
        return FactoryEvent(
            trace_id=run_id,
            event_type=event_type,
            source=self.source,
            payload={key: value for key, value in payload.items() if value is not None},
            tags=["agent_runtime.milestone", event_type],
        )
