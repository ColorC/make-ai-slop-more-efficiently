# [OMNI] origin=claude-code domain=services/absorption/workers/v3/knowledge_loaders ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:learning.absorption.v3.knowledge_loaders.worker_registry.aggregator.py"
"""absorption V3 知识加载子域 · 7 Worker 清单 (Clean Migration 2026-04-20).

wiki 三路 fan-in (capability_inventory / gap_registry / reception_intents) + Stage 3 entry bootstrap:
  stage3_entry_bootstrap (V3 Stage 3 管线 entry)
  capability_inventory_query_builder → capability_inventory_loader
  gap_registry_query_builder → gap_registry_loader
  reception_intents_query_builder → reception_intents_loader
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from .stage3_entry_bootstrap import Stage3EntryBootstrapWorker
from .capability_inventory_query_builder import CapabilityInventoryQueryBuilderWorker
from .capability_inventory_loader import CapabilityInventoryLoaderWorker
from .gap_registry_query_builder import GapRegistryQueryBuilderWorker
from .gap_registry_loader import GapRegistryLoaderWorker
from .reception_intents_query_builder import ReceptionIntentsQueryBuilderWorker
from .reception_intents_loader import ReceptionIntentsLoaderWorker


ALL_WORKERS_V3_KL: list[type[Worker]] = [
    Stage3EntryBootstrapWorker,
    CapabilityInventoryQueryBuilderWorker,
    CapabilityInventoryLoaderWorker,
    GapRegistryQueryBuilderWorker,
    GapRegistryLoaderWorker,
    ReceptionIntentsQueryBuilderWorker,
    ReceptionIntentsLoaderWorker,
]


__all__ = [
    "Stage3EntryBootstrapWorker",
    "CapabilityInventoryQueryBuilderWorker",
    "CapabilityInventoryLoaderWorker",
    "GapRegistryQueryBuilderWorker",
    "GapRegistryLoaderWorker",
    "ReceptionIntentsQueryBuilderWorker",
    "ReceptionIntentsLoaderWorker",
    "ALL_WORKERS_V3_KL",
]
