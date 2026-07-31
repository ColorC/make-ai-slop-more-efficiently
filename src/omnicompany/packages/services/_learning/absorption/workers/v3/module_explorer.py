# [OMNI] origin=claude-code domain=services/absorption/workers/v3 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v3.module_explorer.py"
"""ModuleExplorerWorker (Clean Migration 2026-04-20).

职责: V3 主路径 agent 探索 — local_grep + local_read + submit_module.
内部含 _ExplorerLoop (AgentNodeLoop, 阶段 D 迁移).
实现继承自 _archive/routers_v3_legacy.module_explorer.ModuleExplorerRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.module_explorer import (
    ModuleExplorerRouter as _Legacy,
)


class ModuleExplorerWorker(Worker, _Legacy):
    pass
