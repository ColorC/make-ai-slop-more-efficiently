# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.node_planner.router_designer.py"
"""NodePlannerWorker — workflow_factory Team Worker (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = wf.format_chain
  FORMAT_OUT = wf.node_plan

职责: LLM 根据 Format 链为每条转换设计 Router 节点规划 (HARD/SOFT 分明 · FAIL 路由完整).
内含完整性校验: 每个 format_designer 设计的 format 必须被至少一个节点引用.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from .._archive.routers_legacy import NodePlannerRouter as _Legacy


class NodePlannerWorker(Worker, _Legacy):
    """LLM 为 Format 链中每条转换设计 Router 节点, 含 format 覆盖率校验."""
    pass
