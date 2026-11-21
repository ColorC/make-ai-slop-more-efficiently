# [OMNI] origin=codex domain=services/agent ts=2026-05-09 type=infrastructure
"""External agent worker adapters.

This package keeps full external agents such as Codex and Claude Code out of
the bare LLM provider path. They run as audited workers with explicit cwd and
permission mode.
"""

from omnicompany.packages.services._core.agent.external_workers.base import (
    ExternalAgentEvent,
    ExternalAgentPermissionMode,
    ExternalAgentResult,
    ExternalAgentRunSpec,
    ExternalAgentStatus,
    ExternalAgentWorker,
    ExternalAgentWorkerRegistry,
    FakeExternalAgentWorker,
)
from omnicompany.packages.services._core.agent.external_workers.codex import (
    CodexExecWorker,
)
from omnicompany.packages.services._core.agent.external_workers.claude_code import (
    ClaudeCodeSdkWorker,
)
from omnicompany.packages.services._core.agent.external_workers.factory import (
    build_default_external_agent_worker_registry,
)
from omnicompany.packages.services._core.agent.external_workers.subagent import (
    ExternalAgentSubAgent,
    build_external_agent_subagent_registry,
)
from omnicompany.packages.services._core.agent.external_workers.routers.workflow_node import (
    ExternalAgentWorkerNode,
)
from omnicompany.packages.services._core.agent.external_workers.runner import (
    ExternalAgentRunRequest,
    resolve_external_agent_model,
    run_external_agent_request,
)
from omnicompany.packages.services._core.agent.external_workers.runtime_contract import (
    AgentCapabilityGrant,
    AgentContextEnvelope,
    AgentRuntimeProfile,
    HarnessCapabilities,
    get_harness_capabilities,
)
from omnicompany.packages.services._core.agent.external_workers.runtime_profiles import (
    AgentRuntimeRequest,
    AgentRuntimeSelection,
    run_agent_runtime_request,
    select_agent_runtime,
)

__all__ = [
    "CodexExecWorker",
    "ClaudeCodeSdkWorker",
    "AgentCapabilityGrant",
    "AgentContextEnvelope",
    "AgentRuntimeProfile",
    "AgentRuntimeRequest",
    "AgentRuntimeSelection",
    "ExternalAgentSubAgent",
    "ExternalAgentEvent",
    "ExternalAgentPermissionMode",
    "ExternalAgentResult",
    "ExternalAgentRunRequest",
    "ExternalAgentRunSpec",
    "ExternalAgentStatus",
    "ExternalAgentWorkerNode",
    "ExternalAgentWorker",
    "ExternalAgentWorkerRegistry",
    "FakeExternalAgentWorker",
    "HarnessCapabilities",
    "build_default_external_agent_worker_registry",
    "build_external_agent_subagent_registry",
    "get_harness_capabilities",
    "resolve_external_agent_model",
    "run_agent_runtime_request",
    "run_external_agent_request",
    "select_agent_runtime",
]
