# [OMNI] origin=claude-code domain=services/absorption/routers ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:learning.absorption.router_shim.human_approval_gate_s3.py"
"""compat shim: redirect to workers/v3/human_approval_gate_s3.py."""
from __future__ import annotations

from ..workers.v3.human_approval_gate_s3 import (
    HumanApprovalGateS3Worker,
    ProposalFeedbackGateWorker,
    ProposalFeedbackRouterWorker,
)


HumanApprovalGateS3Router = HumanApprovalGateS3Worker
ProposalFeedbackGateRouter = ProposalFeedbackGateWorker
ProposalFeedbackRouterRouter = ProposalFeedbackRouterWorker


__all__ = [
    "HumanApprovalGateS3Router",
    "ProposalFeedbackGateRouter",
    "ProposalFeedbackRouterRouter",
    "HumanApprovalGateS3Worker",
    "ProposalFeedbackGateWorker",
    "ProposalFeedbackRouterWorker",
]
