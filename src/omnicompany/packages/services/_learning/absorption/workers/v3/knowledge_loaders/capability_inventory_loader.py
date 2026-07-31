# [OMNI] origin=claude-code domain=services/absorption/workers/v3/knowledge_loaders ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.v3.knowledge_loaders.capability_inventory.loader.worker.py"
"""CapabilityInventoryLoaderWorker (Clean Migration 2026-04-20).

职责: 加载 OmniCompany capability inventory (wiki fan-in).
实现继承自 _archive/routers_v3_legacy.knowledge_loaders.CapabilityInventoryLoaderRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.knowledge_loaders import (
    CapabilityInventoryLoaderRouter as _Legacy,
)


class CapabilityInventoryLoaderWorker(Worker, _Legacy):
    pass
