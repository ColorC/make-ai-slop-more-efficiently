# [OMNI] origin=claude-code domain=services/absorption/routers ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:learning.absorption.router_shim.module_picker.py"
"""compat shim: redirect to workers/v3/module_picker.py."""
from __future__ import annotations

from ..workers.v3.module_picker import ModulePickerWorker


ModulePickerRouter = ModulePickerWorker


__all__ = ["ModulePickerRouter", "ModulePickerWorker"]
