# [OMNI] origin=claude-code domain=services/pipeline_ci ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:diagnosis.pipeline_ci.router_compat_shim.python"
"""pipeline_ci routers — 兼容垫片 (Clean Migration 2026-04-20 后).

业务实现已迁到 workers/ 子目录. 本文件保留旧名称以兼容任何仍引用 routers.py 的调用方.
"""
from __future__ import annotations

from .workers.domain_scanner_worker import DomainScannerWorker as DomainScannerRouter
from .workers.batch_auditor_worker import BatchAuditorWorker as BatchAuditorRouter
from .workers.ci_gate_worker import CIGateWorker as CIGateRouter

__all__ = [
    "DomainScannerRouter",
    "BatchAuditorRouter",
    "CIGateRouter",
]
