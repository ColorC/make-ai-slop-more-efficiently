# [OMNI] origin=claude-code domain=services/absorption/workers/v3/knowledge_loaders ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.v3.knowledge_loaders.reception_intents.query_builder.worker.py"
"""ReceptionIntentsQueryBuilderWorker (Clean Migration 2026-04-20).

职责: 构建 reception_intents wiki 查询.
实现继承自 _archive/routers_v3_legacy.knowledge_loaders.ReceptionIntentsQueryBuilderRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.knowledge_loaders import (
    ReceptionIntentsQueryBuilderRouter as _Legacy,
)


class ReceptionIntentsQueryBuilderWorker(Worker, _Legacy):
    pass
