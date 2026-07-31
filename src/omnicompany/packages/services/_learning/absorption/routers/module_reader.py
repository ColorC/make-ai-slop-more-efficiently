# [OMNI] origin=claude-code domain=services/absorption/routers ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:learning.absorption.router_shim.module_reader.py"
"""compat shim: redirect to workers/v3/module_reader.py."""
from __future__ import annotations

from ..workers.v3.module_reader import ModuleReaderWorker


ModuleReaderRouter = ModuleReaderWorker


__all__ = ["ModuleReaderRouter", "ModuleReaderWorker"]
