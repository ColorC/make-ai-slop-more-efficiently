# [OMNI] origin=claude-code domain=services/absorption/workers/v1 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v1.triage_gate.py"
"""TriageGateWorker — V1 Survey Worker #5 (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = absorption.coverage_audit
  FORMAT_OUT = absorption.triaged_landmarks

职责: ANCHOR + HARD. tier-1 过滤 + 落盘 pool.
实现继承自 _archive/routers_v1v2_legacy.TriageGateRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v1v2_legacy import (
    TriageGateRouter as _Legacy,
)


class TriageGateWorker(Worker, _Legacy):
    pass
