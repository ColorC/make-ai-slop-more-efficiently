# [OMNI] origin=claude-code domain=services/absorption/workers/v2 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v2.synthesis.py"
"""SynthesisV2Worker — V2 Worker #6 (Clean Migration 2026-04-20).

职责: 将多个 question-answer 合成综合发现 (synthesis).
实现继承自 _archive/routers_v1v2_legacy.SynthesisV2Router.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v1v2_legacy import (
    SynthesisV2Router as _Legacy,
)


class SynthesisV2Worker(Worker, _Legacy):
    pass
