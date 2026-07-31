# [OMNI] origin=claude-code domain=services/absorption/workers/v3/knowledge_loaders ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.v3.knowledge_loaders.gap_registry.query_builder.worker.py"
"""GapRegistryQueryBuilderWorker (Clean Migration 2026-04-20).

职责: 构建 gap_registry wiki 查询.
实现继承自 _archive/routers_v3_legacy.knowledge_loaders.GapRegistryQueryBuilderRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.knowledge_loaders import (
    GapRegistryQueryBuilderRouter as _Legacy,
)


class GapRegistryQueryBuilderWorker(Worker, _Legacy):
    pass
