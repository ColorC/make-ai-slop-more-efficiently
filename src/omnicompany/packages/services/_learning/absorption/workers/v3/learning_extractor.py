# [OMNI] origin=claude-code domain=services/absorption/workers/v3 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v3.learning_extractor.py"
"""LearningExtractorWorker (Clean Migration 2026-04-20).

职责: LLM 分批按 gap_id 提炼 finding.
实现继承自 _archive/routers_v3_legacy.learning_extractor.LearningExtractorRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.learning_extractor import (
    LearningExtractorRouter as _Legacy,
)


class LearningExtractorWorker(Worker, _Legacy):
    pass
