# [OMNI] origin=claude-code domain=services/agent ts=2026-04-18
# [OMNI] material_id="material:core.agent.module_aggregate.exports.py"
"""services.agent — Agent Node Loop Router 化架构

本包是 2026-04-18 启动的 `Agent Node Loop Router 化重构` 的落地位置。
详见 `docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md`。

核心铁律（§0.1）：
1. AgentNodeLoop 必须挂 EventBus
2. 所有 Format 都要进 bus
3. AgentNodeLoop 是纯粹 Router 实现（薄调度器）
4. 所有子组件都是 Router
5. 禁止通过 bus 以外的地方传参

子 Router 清单：
- PromptBuilderRouter  : agent.prompt-request  → agent.prompt-built
- ContextCompactRouter : agent.context-request → agent.context-compacted
- LLMCallRouter        : agent.llm-request     → agent.llm-response
- ToolDispatchRouter   : agent.tool-request    → agent.tool-response
- SingleToolRouter     : tool.<name>-request   → tool.<name>-response  (抽象基类)
- ExtractResultRouter  : agent.result-request  → agent.result-final
- AgentNodeLoop        : 薄调度器，串起上述 Router

与即将删除的 `omnicompany.runtime.agent.agent_node_loop.AgentNodeLoop` 同名但不同
import path。阶段 C/D 会把旧位置的单体类迁净并删除，之后全项目唯一 AgentNodeLoop
就是本包这份。
"""

from omnicompany.packages.services._core.agent.formats import register_formats
from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.context_compact import ContextCompactRouter
from omnicompany.packages.services._core.agent.routers.llm_call import (
    LLMCallRouter,
    CannotRetryError,
    FallbackTriggeredError,
)
from omnicompany.packages.services._core.agent.routers.tool_dispatch import ToolDispatchRouter
from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolExecutionError,
    GlobRouter,
    GrepRouter,
    ReadFileRouter,
    EditRouter,
    ListDirRouter,
    FinishRouter,
)
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter
from omnicompany.packages.services._core.agent.configurable import (
    AgentSpec,
    ConfigurableAgent,
    TOOL_REGISTRY,
    register_tool,
    auto_register_singletool_subclasses,
)
from omnicompany.packages.services._core.agent.routers.web_fetch import WebFetchRouter
from omnicompany.packages.services._core.agent.routers.write_file import WriteFileRouter
from omnicompany.packages.services._core.agent.routers.dev_bash import DevBashRouter
from omnicompany.packages.services._core.agent.routers.powershell import PowerShellRouter
from omnicompany.packages.services._core.agent.routers.sub_agent import SubAgentRouter
from omnicompany.packages.services._core.agent.sub_agent_registry import SubAgentRegistry
from omnicompany.packages.services._core.agent.system_prompt_builder import SystemPromptBuilder
from omnicompany.packages.services._core.agent.slash_commands import SlashCommandRegistry
from omnicompany.packages.services._core.agent.routers.playwright_probe import PlaywrightProbeRouter
from omnicompany.packages.services._core.agent.routers.file_owner_query import FileOwnerQueryRouter
from omnicompany.packages.services._core.agent.routers.verify_test_red_green import VerifyTestRedGreenRouter
from omnicompany.packages.services._core.agent.spawn_surface import (
    AGENT_SPAWN_SURFACE_VERSION,
    ENTRY_AGENT_TOOL,
    ENTRY_CONTROLLER_SPAWN,
    ENTRY_CONTEXT_FORK,
    ENTRY_EXTERNAL_WORKER_AS_AGENT,
    ENTRY_EXTERNAL_WORKER_RUN,
    ENTRY_INTERNAL_LOOP,
    ENTRY_TEAMRUNNER_NODE,
    ENTRY_WORKFLOW_RUN,
    AgentSpawnEntry,
    agent_spawn_metadata,
    describe_agent_spawn_surface,
    ensure_agent_spawn_metadata,
    get_agent_spawn_entry,
    list_agent_spawn_entries,
)
from omnicompany.packages.services._core.agent.context_fork import (
    AgentContextCheckpoint,
    ContextForkBudget,
    ContextForkReceipt,
    ContextForkRequest,
    ContextForkStatus,
    capture_context_checkpoint,
    run_context_fork,
)

__all__ = [
    "register_formats",
    "AgentNodeLoop",
    "PromptBuilderRouter",
    "ContextCompactRouter",
    "LLMCallRouter",
    "CannotRetryError",
    "FallbackTriggeredError",
    "ToolDispatchRouter",
    "SingleToolRouter",
    "ToolExecutionError",
    "GlobRouter",
    "GrepRouter",
    "ReadFileRouter",
    "EditRouter",
    "ListDirRouter",
    "FinishRouter",
    "WebFetchRouter",
    "WriteFileRouter",
    "DevBashRouter",
    "PowerShellRouter",
    "SubAgentRouter",
    "SubAgentRegistry",
    "SystemPromptBuilder",
    "SlashCommandRegistry",
    "PlaywrightProbeRouter",
    "FileOwnerQueryRouter",
    "VerifyTestRedGreenRouter",
    "ExtractResultRouter",
    "AGENT_SPAWN_SURFACE_VERSION",
    "ENTRY_AGENT_TOOL",
    "ENTRY_CONTROLLER_SPAWN",
    "ENTRY_CONTEXT_FORK",
    "ENTRY_EXTERNAL_WORKER_AS_AGENT",
    "ENTRY_EXTERNAL_WORKER_RUN",
    "ENTRY_INTERNAL_LOOP",
    "ENTRY_TEAMRUNNER_NODE",
    "ENTRY_WORKFLOW_RUN",
    "AgentSpawnEntry",
    "agent_spawn_metadata",
    "describe_agent_spawn_surface",
    "ensure_agent_spawn_metadata",
    "get_agent_spawn_entry",
    "list_agent_spawn_entries",
    "AgentContextCheckpoint",
    "ContextForkBudget",
    "ContextForkReceipt",
    "ContextForkRequest",
    "ContextForkStatus",
    "capture_context_checkpoint",
    "run_context_fork",
]
