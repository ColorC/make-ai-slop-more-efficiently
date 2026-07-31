# [OMNI] origin=claude-code domain=services/absorption/routers ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:learning.absorption.router_shim.learning_extractor.py"
"""compat shim: redirect to workers/v3/learning_extractor.py."""
from __future__ import annotations

from ..workers.v3.learning_extractor import LearningExtractorWorker


LearningExtractorRouter = LearningExtractorWorker


__all__ = ["LearningExtractorRouter", "LearningExtractorWorker"]
