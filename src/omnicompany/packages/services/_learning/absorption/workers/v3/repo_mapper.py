# [OMNI] origin=claude-code domain=services/absorption/workers/v3 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v3.repo_mapper.py"
"""RepoMapperWorker (Clean Migration 2026-04-20).

职责: 纯计算符号地图 (coarse_view + detail_views).
实现继承自 _archive/routers_v3_legacy.repo_mapper.RepoMapperRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.repo_mapper import (
    RepoMapperRouter as _Legacy,
)


class RepoMapperWorker(Worker, _Legacy):
    pass
