# [OMNI] origin=claude-code domain=services/absorption/routers ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:learning.absorption.router_shim.knowledge_loaders.py"
"""compat shim (Clean Migration 2026-04-20).

旧 `from ...absorption.routers.knowledge_loaders import XxxRouter` 继续工作.
真实实现在 `workers/v3/knowledge_loaders/`; 本模块 re-export 旧 Router 名为 Worker alias.
"""
from __future__ import annotations

from ..workers.v3.knowledge_loaders import (
    Stage3EntryBootstrapWorker,
    CapabilityInventoryQueryBuilderWorker,
    CapabilityInventoryLoaderWorker,
    GapRegistryQueryBuilderWorker,
    GapRegistryLoaderWorker,
    ReceptionIntentsQueryBuilderWorker,
    ReceptionIntentsLoaderWorker,
)


Stage3EntryBootstrapRouter = Stage3EntryBootstrapWorker
CapabilityInventoryQueryBuilderRouter = CapabilityInventoryQueryBuilderWorker
CapabilityInventoryLoaderRouter = CapabilityInventoryLoaderWorker
GapRegistryQueryBuilderRouter = GapRegistryQueryBuilderWorker
GapRegistryLoaderRouter = GapRegistryLoaderWorker
ReceptionIntentsQueryBuilderRouter = ReceptionIntentsQueryBuilderWorker
ReceptionIntentsLoaderRouter = ReceptionIntentsLoaderWorker


__all__ = [
    "Stage3EntryBootstrapRouter",
    "CapabilityInventoryQueryBuilderRouter",
    "CapabilityInventoryLoaderRouter",
    "GapRegistryQueryBuilderRouter",
    "GapRegistryLoaderRouter",
    "ReceptionIntentsQueryBuilderRouter",
    "ReceptionIntentsLoaderRouter",
    "Stage3EntryBootstrapWorker",
    "CapabilityInventoryQueryBuilderWorker",
    "CapabilityInventoryLoaderWorker",
    "GapRegistryQueryBuilderWorker",
    "GapRegistryLoaderWorker",
    "ReceptionIntentsQueryBuilderWorker",
    "ReceptionIntentsLoaderWorker",
]
