"""Lifecycle-safe process invokers for the language-neutral AgentRuntime port."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .client import AgentRuntimeProcessClient


RuntimeMessage = dict[str, Any]
RuntimeEventSink = Callable[[RuntimeMessage], None | Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AgentRuntimeProcessSpec:
    command: Sequence[str]
    cwd: str
    env: Mapping[str, str]
    close_timeout_seconds: float = 20.0


class OneShotAgentRuntimeProcessInvoker:
    """Own one child process per run until a daemon lifecycle exists.

    This is intentionally conservative for the first production canary: every
    success, failure, cancellation, or exception closes the JSON-RPC host and
    its DSH child tree.  Warm process ownership can move to a supervised daemon
    later without changing the Team-facing AgentRuntime contract.
    """

    def __init__(self, spec: AgentRuntimeProcessSpec) -> None:
        self._spec = spec

    async def run(
        self,
        request: RuntimeMessage,
        event_sink: RuntimeEventSink | None = None,
    ) -> RuntimeMessage:
        async with AgentRuntimeProcessClient(
            self._spec.command,
            cwd=self._spec.cwd,
            env=dict(self._spec.env),
            close_timeout_seconds=self._spec.close_timeout_seconds,
        ) as client:
            return await client.run(request, event_sink)


__all__ = ["AgentRuntimeProcessSpec", "OneShotAgentRuntimeProcessInvoker"]
