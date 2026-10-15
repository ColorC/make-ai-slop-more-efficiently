# [OMNI] origin=codex domain=services/agent ts=2026-05-09 type=infrastructure
"""Factory for explicit external agent worker registry."""

from __future__ import annotations

import os
from pathlib import Path
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
    # 第二个官方 Codex 账号：同一 CLI、独立 CODEX_HOME（额度/凭据/rollout 全独立）。
    # 主账号额度耗尽时的指定逃生口（2026-08-18：派 worker 用错账号浪费一轮的教训）。
    registry.register(
        "codex-2",
        lambda **kw: CodexExecWorker(
            bus=bus,
            provider_name="codex-2",
            codex_home=os.environ.get("OMNI_CODEX_2_HOME", "").strip()
            or str(Path.home() / ".codex-2"),
            **kw,
        ),
    )
    registry.register("claude-code", lambda **kw: ClaudeCodeSdkWorker(bus=bus, **kw))
    registry.register("opencode", lambda **kw: OpenCodeRunWorker(bus=bus, **kw))
    registry.register("kimi", lambda **kw: KimiExecWorker(bus=bus, **kw))
    registry.register("omni-native", lambda **kw: OmniNativeWorkspaceWorker(bus=bus, **kw))
    return registry


__all__ = ["build_default_external_agent_worker_registry"]
