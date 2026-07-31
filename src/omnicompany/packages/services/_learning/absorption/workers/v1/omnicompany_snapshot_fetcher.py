# [OMNI] origin=claude-code domain=services/absorption/workers/v1 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v1.omni_snapshot_fetcher.py"
"""OmnicompanySnapshotFetcherWorker — V1 Survey Worker #3 (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = absorption.intake
  FORMAT_OUT = absorption.omnicompany_snapshot

职责: ANCHOR + HARD. 扫本仓自身能力 (packages/core/runtime), 产 snapshot.
实现继承自 _archive/routers_v1v2_legacy.OmnifactorySnapshotFetcherRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v1v2_legacy import (
    OmnifactorySnapshotFetcherRouter as _Legacy,
)


class OmnicompanySnapshotFetcherWorker(Worker, _Legacy):
    pass
