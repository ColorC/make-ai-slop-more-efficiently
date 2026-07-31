# [OMNI] origin=claude-code domain=services/absorption/routers ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:learning.absorption.router_shim.spec_parser.py"
"""compat shim: redirect to workers/v3/spec_parser.py."""
from __future__ import annotations

from ..workers.v3.spec_parser import SpecParserWorker


SpecParserRouter = SpecParserWorker


__all__ = ["SpecParserRouter", "SpecParserWorker"]
