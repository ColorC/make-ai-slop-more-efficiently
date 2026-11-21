# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:diagnosis.doctor.worker.blackboard.package_aggregate.py"
"""doctor 黑板诊断子域 — New World Diagnostics Phase B (2026-04-20).

6 Worker 诊断新世界订阅图合规性:

| Worker | 规则 | 产出 |
|---|---|---|
| MaterialKindLegalityWorker         | F-19 / F-16 Material kind 合法性 | kind_legality_report |
| FormatInModeCheckerWorker          | R-24 list[str] 必声明 MODE | mode_check_report |
| VerdictOutputFlatCheckerWorker     | R-23 output 不嵌套 | output_flat_report |
| OrphanWorkerScannerWorker          | Q4 孤儿 Worker | orphan_worker_report |
| UnconsumedMaterialScannerWorker    | Q4 未消费 Material | unconsumed_material_report |
| EmitAsNewJobCheckerWorker          | R-25 子 job 合规 | emit_check_report |

共同输入: doctor.blackboard.audit_request (kind.source)
各独立产出: 6 kind.sink 报告 (无 consumer, 供人/CI 读)

共享工具: _shared.py (动态 import Team + 订阅图构建).
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from .material_kind_legality import MaterialKindLegalityWorker
from .format_in_mode_checker import FormatInModeCheckerWorker
from .verdict_output_flat_checker import VerdictOutputFlatCheckerWorker
from .orphan_worker_scanner import OrphanWorkerScannerWorker
from .unconsumed_material_scanner import UnconsumedMaterialScannerWorker
from .emit_as_new_job_checker import EmitAsNewJobCheckerWorker


ALL_WORKERS_BLACKBOARD: list[type[Worker]] = [
    MaterialKindLegalityWorker,
    FormatInModeCheckerWorker,
    VerdictOutputFlatCheckerWorker,
    OrphanWorkerScannerWorker,
    UnconsumedMaterialScannerWorker,
    EmitAsNewJobCheckerWorker,
]


__all__ = [
    "MaterialKindLegalityWorker",
    "FormatInModeCheckerWorker",
    "VerdictOutputFlatCheckerWorker",
    "OrphanWorkerScannerWorker",
    "UnconsumedMaterialScannerWorker",
    "EmitAsNewJobCheckerWorker",
    "ALL_WORKERS_BLACKBOARD",
]
