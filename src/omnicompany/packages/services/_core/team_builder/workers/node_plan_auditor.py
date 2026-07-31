# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.node_plan_quality_auditor.worker.py"
"""NodePlanAuditorWorker — workflow_factory Team Worker (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = wf.node_plan
  FORMAT_OUT = wf.node_plan

职责: HARD 审计 node_plan 的语义质量 (P7.8 meta-pipeline 自净):
  - 每个 SOFT 节点是否填了 context_sources / hallucination_risks / output_token_budget / FAIL 路由
  - 防止 workflow_factory 把自己的坏习惯复制到生成的管线 (GAP §2.3 + §2.5)
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from .._archive.routers_legacy import NodePlanAuditorRouter as _Legacy


class NodePlanAuditorWorker(Worker, _Legacy):
    """HARD 审计 node_plan 是否满足 SKILL §3.1 节点设计单表, 未通过返回 PARTIAL 触发 retry."""
    pass
