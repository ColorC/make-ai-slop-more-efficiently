# [OMNI] origin=claude-code domain=services/absorption/workers/v3/knowledge_loaders ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.v3.knowledge_loaders.stage3_entry.bootstrap.worker.py"
"""Stage3EntryBootstrapWorker (Clean Migration 2026-04-20).

职责: V3 Stage 3 管线入口 bootstrap; 从 absorption report 引导进入 Stage 3.
实现继承自 _archive/routers_v3_legacy.knowledge_loaders.Stage3EntryBootstrapRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.knowledge_loaders import (
    Stage3EntryBootstrapRouter as _Legacy,
)


class Stage3EntryBootstrapWorker(Worker, _Legacy):
    pass
