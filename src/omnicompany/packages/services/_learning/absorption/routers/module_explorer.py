# [OMNI] origin=claude-code domain=services/absorption/routers ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:learning.absorption.router_shim.module_explorer.py"
"""compat shim (Clean Migration 2026-04-20).

旧 `from ...absorption.routers.module_explorer import ModuleExplorerRouter` 继续工作.
真实实现在 `workers/v3/module_explorer.py`.
"""
from __future__ import annotations

from ..workers.v3.module_explorer import ModuleExplorerWorker


ModuleExplorerRouter = ModuleExplorerWorker


__all__ = ["ModuleExplorerRouter", "ModuleExplorerWorker"]
