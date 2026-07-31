# [OMNI] origin=codex domain=services/agent ts=2026-05-09 type=infrastructure
"""Factory for explicit external agent worker registry."""

from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.agent.external_workers.base import (
    ExternalAgentWorkerRegistry,
)
from omnicompany.packages.services._core.agent.external_workers.claude_code import (
    ClaudeCodeSdkWorker,
)
from omnicompany.packages.services._core.agent.external_workers.codex import (
    CodexExecWorker,
)
from omnicompany.packages.services._core.agent.external_workers.kimi import (
    KimiExecWorker,
)
from omnicompany.packages.services._core.agent.external_workers.opencode import (
    OpenCodeRunWorker,
)
from omnicompany.packages.services._core.agent.external_workers.omni_native import (
    OmniNativeWorkspaceWorker,
)


def build_default_external_agent_worker_registry(
    *,
    bus: Any | None = None,
) -> ExternalAgentWorkerRegistry:
    """Build the explicit external worker registry.

    This registry is intentionally separate from AgentRouter's internal
    subagent registry. Callers opt into external local agents by selecting a
    provider from this registry.
    """

    registry = ExternalAgentWorkerRegistry()
    registry.register("codex", lambda **kw: CodexExecWorker(bus=bus, **kw))
    registry.register("claude-code", lambda **kw: ClaudeCodeSdkWorker(bus=bus, **kw))
    registry.register("opencode", lambda **kw: OpenCodeRunWorker(bus=bus, **kw))
    registry.register("kimi", lambda **kw: KimiExecWorker(bus=bus, **kw))
    registry.register("omni-native", lambda **kw: OmniNativeWorkspaceWorker(bus=bus, **kw))
    return registry


__all__ = ["build_default_external_agent_worker_registry"]
