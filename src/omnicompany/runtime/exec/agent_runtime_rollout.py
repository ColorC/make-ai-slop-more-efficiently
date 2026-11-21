"""Explicit, fail-closed rollout selection for Team AgentRuntime bindings."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import inspect
import os
from typing import Any

from omnicompany.protocol.anchor import Verdict
from omnicompany.runtime.routing.router import Router


class AgentRuntimeRolloutMode(str, Enum):
    LEGACY = "legacy"
    CANARY = "canary"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class AgentRuntimeRolloutPolicy:
    mode: AgentRuntimeRolloutMode = AgentRuntimeRolloutMode.LEGACY
    canary_positions: frozenset[str] = frozenset()

    @classmethod
    def from_environment(cls) -> "AgentRuntimeRolloutPolicy":
        raw_mode = os.environ.get("OMNI_AGENT_RUNTIME_MODE", "legacy").strip().lower()
        try:
            mode = AgentRuntimeRolloutMode(raw_mode)
        except ValueError as error:
            raise ValueError(f"invalid OMNI_AGENT_RUNTIME_MODE: {raw_mode!r}") from error
        raw_positions = os.environ.get("OMNI_AGENT_RUNTIME_CANARY_POSITIONS", "")
        positions = frozenset(item.strip() for item in raw_positions.split(",") if item.strip())
        if mode == AgentRuntimeRolloutMode.CANARY and not positions:
            raise ValueError("canary mode requires OMNI_AGENT_RUNTIME_CANARY_POSITIONS")
        return cls(mode=mode, canary_positions=positions)

    def use_external(self, team_id: str, position_id: str) -> bool:
        if self.mode == AgentRuntimeRolloutMode.EXTERNAL:
            return True
        if self.mode == AgentRuntimeRolloutMode.LEGACY:
            return False
        return f"{team_id}/{position_id}" in self.canary_positions


class AgentRuntimeRolloutRouter(Router):
    """Temporary canary binding; an external failure never falls back to legacy."""

    def __init__(
        self,
        *,
        team_id: str,
        position_id: str,
        legacy: Router,
        external: Router,
        policy: AgentRuntimeRolloutPolicy,
    ) -> None:
        contracts = {
            (legacy.FORMAT_IN, legacy.FORMAT_OUT),
            (external.FORMAT_IN, external.FORMAT_OUT),
        }
        if len(contracts) != 1:
            raise ValueError("legacy and external runtime bindings must expose the same formats")
        self._selected = external if policy.use_external(team_id, position_id) else legacy
        self.selected_runtime = "external" if self._selected is external else "legacy"
        self.DESCRIPTION = self._selected.DESCRIPTION
        self.FORMAT_IN = self._selected.FORMAT_IN
        self.FORMAT_OUT = self._selected.FORMAT_OUT
        self.INPUT_KEYS = self._selected.INPUT_KEYS
        self.OUTPUT_KEYS = self._selected.OUTPUT_KEYS
        self.REQUIRED_CONTEXT = self._selected.REQUIRED_CONTEXT
        self.PASSTHROUGH = self._selected.PASSTHROUGH

    def bind_runtime_context(
        self,
        *,
        bus: Any,
        trace_id: str,
        parent_event_id: str,
    ) -> None:
        super().bind_runtime_context(
            bus=bus,
            trace_id=trace_id,
            parent_event_id=parent_event_id,
        )
        self._selected.bind_runtime_context(
            bus=bus,
            trace_id=trace_id,
            parent_event_id=parent_event_id,
        )

    async def run(self, input_data: Any) -> Verdict:
        result = self._selected.run(input_data)
        return await result if inspect.isawaitable(result) else result

    def summarize_input(self, input_data: Any) -> str:
        return self._selected.summarize_input(input_data)

    def summarize_output(self, verdict: Verdict) -> str:
        return self._selected.summarize_output(verdict)


__all__ = [
    "AgentRuntimeRolloutMode",
    "AgentRuntimeRolloutPolicy",
    "AgentRuntimeRolloutRouter",
]
