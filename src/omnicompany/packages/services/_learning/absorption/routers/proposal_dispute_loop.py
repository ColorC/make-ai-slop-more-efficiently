# [OMNI] origin=claude-code domain=services/absorption/routers ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:learning.absorption.router_shim.proposal_dispute_loop.py"
"""compat shim: redirect to workers/v3/proposal_dispute_loop.py."""
from __future__ import annotations

from ..workers.v3.proposal_dispute_loop import ProposalDisputeLoopWorker


ProposalDisputeLoopRouter = ProposalDisputeLoopWorker


__all__ = ["ProposalDisputeLoopRouter", "ProposalDisputeLoopWorker"]
