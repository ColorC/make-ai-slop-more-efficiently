# [OMNI] origin=claude-code domain=services/absorption/workers/v2 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v2.coverage_auditor.py"
"""CoverageAuditorV2Worker — V2 Worker #5 (Clean Migration 2026-04-20).

职责: V2 覆盖度审计 — 比对 DirectedReader 读过的文件 vs 应读清单.
实现继承自 _archive/routers_v1v2_legacy.CoverageAuditorV2Router.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v1v2_legacy import (
    CoverageAuditorV2Router as _Legacy,
)


class CoverageAuditorV2Worker(Worker, _Legacy):
    pass
