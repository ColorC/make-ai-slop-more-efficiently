# [OMNI] origin=claude-code domain=services/absorption/workers/v3 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v3.module_picker.py"
"""ModulePickerWorker (Clean Migration 2026-04-20).

职责: 模块选择器 (非 agent) — 从 repomap 中按规则挑选 important-modules.
实现继承自 _archive/routers_v3_legacy.module_picker.ModulePickerRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.module_picker import (
    ModulePickerRouter as _Legacy,
)


class ModulePickerWorker(Worker, _Legacy):
    pass
