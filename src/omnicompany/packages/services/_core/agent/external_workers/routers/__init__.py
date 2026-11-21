# [OMNI] origin=codex domain=services/agent ts=2026-05-11 type=infrastructure
"""External worker Router bindings for TeamRunner workflows."""

from omnicompany.packages.services._core.agent.external_workers.routers.workflow_node import (
    ExternalAgentWorkerNode,
)

__all__ = ["ExternalAgentWorkerNode"]
