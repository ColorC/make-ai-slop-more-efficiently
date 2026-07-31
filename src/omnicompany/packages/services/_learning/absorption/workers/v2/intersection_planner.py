# [OMNI] origin=claude-code domain=services/absorption/workers/v2 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v2.intersection_planner.py"
"""IntersectionPlannerV2Worker — V2 Worker #2 (Clean Migration 2026-04-20).

职责: 计算 OmniCompany 缺口 × repo 能力交集, 生成 question-list.
实现继承自 _archive/routers_v1v2_legacy.IntersectionPlannerV2Router.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v1v2_legacy import (
    IntersectionPlannerV2Router as _Legacy,
)


class IntersectionPlannerV2Worker(Worker, _Legacy):
    pass
