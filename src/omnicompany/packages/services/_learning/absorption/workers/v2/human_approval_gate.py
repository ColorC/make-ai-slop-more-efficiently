# [OMNI] origin=claude-code domain=services/absorption/workers/v2 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v2.human_approval_gate.py"
"""HumanApprovalGateV2Worker — V2 Worker #3 (Clean Migration 2026-04-20).

职责: 读人审结果 (approved_questions.txt) 决定放行哪些问题.
实现继承自 _archive/routers_v1v2_legacy.HumanApprovalGateV2Router.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v1v2_legacy import (
    HumanApprovalGateV2Router as _Legacy,
)


class HumanApprovalGateV2Worker(Worker, _Legacy):
    pass
