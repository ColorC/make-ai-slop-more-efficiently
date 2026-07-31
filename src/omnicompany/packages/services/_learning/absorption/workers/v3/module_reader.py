# [OMNI] origin=claude-code domain=services/absorption/workers/v3 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v3.module_reader.py"
"""ModuleReaderWorker (Clean Migration 2026-04-20).

职责: 读取 module.code (纯 IO, 喂给 LearningExtractor).
实现继承自 _archive/routers_v3_legacy.module_reader.ModuleReaderRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.module_reader import (
    ModuleReaderRouter as _Legacy,
)


class ModuleReaderWorker(Worker, _Legacy):
    pass
