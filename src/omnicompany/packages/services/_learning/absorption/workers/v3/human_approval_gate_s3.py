# [OMNI] origin=claude-code domain=services/absorption/workers/v3 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v3.approval_feedback_gate.py"
"""Stage 3 Human Approval Gate 三 Worker (Clean Migration 2026-04-20).

Workers:
  - HumanApprovalGateS3Worker — 读 approved_proposals.txt 做审批 (最终 gate)
  - ProposalFeedbackGateWorker — 读 proposal feedback 文件
  - ProposalFeedbackRouterWorker — RULE 分流: EMIT 或 JUMP (补充探索)

实现继承自 _archive/routers_v3_legacy.human_approval_gate_s3.*.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.human_approval_gate_s3 import (
    HumanApprovalGateS3Router as _GateLegacy,
    ProposalFeedbackGateRouter as _FeedbackGateLegacy,
    ProposalFeedbackRouterRouter as _FeedbackRouterLegacy,
)


class HumanApprovalGateS3Worker(Worker, _GateLegacy):
    pass


class ProposalFeedbackGateWorker(Worker, _FeedbackGateLegacy):
    pass


class ProposalFeedbackRouterWorker(Worker, _FeedbackRouterLegacy):
    pass
