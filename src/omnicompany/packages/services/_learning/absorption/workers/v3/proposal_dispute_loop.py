# [OMNI] origin=claude-code domain=services/absorption/workers/v3 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v3.proposal_dispute_loop.py"
"""ProposalDisputeLoopWorker (Clean Migration 2026-04-20).

职责: Stage 3 人审反驳 — agent_node_loop 异步方式接受 dispute, 产 revised_proposals.
内部含 _DisputeLoop (AgentNodeLoop, 阶段 D 迁移).
实现继承自 _archive/routers_v3_legacy.proposal_dispute_loop.ProposalDisputeLoopRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.proposal_dispute_loop import (
    ProposalDisputeLoopRouter as _Legacy,
)


class ProposalDisputeLoopWorker(Worker, _Legacy):
    pass
