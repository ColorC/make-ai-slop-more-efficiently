# [OMNI] origin=claude-code domain=omnicompany/omnicompany ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:omnicompany.module_aggregate.exports.py"
"""Worker/Material/Team vocabulary and Material-ledger activation.

Material bodies and subscription activation belong to a direct Material
authority.  The compatibility MaterialDispatcher uses a run-scoped ledger;
EventBus receives only optional bodyless audit projections.  Cross-run
Material query/subscription is provided by the canonical Material authority
and BlackboardCoordinator, never by replaying EventBus as inventory.
"""
from .worker import Worker, Material, Team
from .material_dispatcher import MaterialDispatcher
from .material_events import publish_material_event, query_material_events
from .llm_client import call_llm_json
from .agent_team_demo import (
    AgentContextScriptWorker,
    AgentLLMWorker,
    AgentToolWorker,
    AgentFinalizerWorker,
    AGENT_TEAM_WORKERS,
)


__all__ = [
    # Core shapes (2026-04-20 用户洞察: 让 import 直接看到 omnicompany 词汇)
    "Worker",       # protocol 层 Router 的 omnicompany 层基类
    "Material",     # protocol 层 Format 的 alias
    "Team",         # protocol 层 TeamSpec 的 alias
    # Runtime
    "MaterialDispatcher",
    "publish_material_event",
    "query_material_events",
    # Shared LLM helper (2026-04-23 提到共享层)
    "call_llm_json",
    # Agent Team demo workers (金标样本)
    "AgentContextScriptWorker",
    "AgentLLMWorker",
    "AgentToolWorker",
    "AgentFinalizerWorker",
    "AGENT_TEAM_WORKERS",
]
