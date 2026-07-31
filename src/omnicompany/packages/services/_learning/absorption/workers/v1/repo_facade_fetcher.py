# [OMNI] origin=claude-code domain=services/absorption/workers/v1 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v1.repo_facade_fetcher.py"
"""RepoFacadeFetcherWorker — V1 Survey Worker #2 (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = absorption.intake
  FORMAT_OUT = absorption.facade_card

职责: ANCHOR + HARD. 用 gh CLI 抓 GitHub 门面元数据 + 递归 tree + 贡献者 + 近期 release.
实现继承自 _archive/routers_v1v2_legacy.RepoFacadeFetcherRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v1v2_legacy import (
    RepoFacadeFetcherRouter as _Legacy,
)


class RepoFacadeFetcherWorker(Worker, _Legacy):
    pass
