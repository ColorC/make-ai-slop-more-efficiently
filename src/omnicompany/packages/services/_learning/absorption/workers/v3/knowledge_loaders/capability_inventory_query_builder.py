# [OMNI] origin=claude-code domain=services/absorption/workers/v3/knowledge_loaders ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.v3.knowledge_loaders.capability_inventory.query_builder.worker.py"
"""CapabilityInventoryQueryBuilderWorker (Clean Migration 2026-04-20).

职责: 构建 capability_inventory wiki 查询 (wiki fan-in 第一跳).
实现继承自 _archive/routers_v3_legacy.knowledge_loaders.CapabilityInventoryQueryBuilderRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.knowledge_loaders import (
    CapabilityInventoryQueryBuilderRouter as _Legacy,
)


class CapabilityInventoryQueryBuilderWorker(Worker, _Legacy):
    pass
