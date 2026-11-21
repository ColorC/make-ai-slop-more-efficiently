# [OMNI] origin=claude-code domain=services/pipeline_ci ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:diagnosis.pipeline_ci.worker_registry.python"
"""pipeline_ci Team 的 Worker 集合（omnicompany 层命名）。

3 个 Worker (全部确定性，无 LLM):
  - DomainScannerWorker: 扫描所有含 routers.py + pipeline.py 的包
  - BatchAuditorWorker:  对每个包运行 ErrorRouteAuditor + TeamChecker
  - CIGateWorker:        critical_count > 0 → FAIL 阻断 CI
"""
from __future__ import annotations

from .domain_scanner_worker import DomainScannerWorker
from .batch_auditor_worker import BatchAuditorWorker
from .ci_gate_worker import CIGateWorker

ALL_WORKERS = [
    DomainScannerWorker,
    BatchAuditorWorker,
    CIGateWorker,
]

__all__ = [
    "DomainScannerWorker",
    "BatchAuditorWorker",
    "CIGateWorker",
    "ALL_WORKERS",
]
