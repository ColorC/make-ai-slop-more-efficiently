# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=config
# [OMNI] material_id="material:diagnosis.doctor.worker.worker.package_aggregate.py"
"""doctor Worker 诊断子域 · 6 Worker (Stage 3 Clean Migration · 命名规范化完成).

订阅拓扑 (详见 ../../team.py build_router_pipeline):
  anatomy_extractor → signature_anchor ─(PASS)→ context_collector
      → rule_checker → contextual_auditor → health_writer
                    └─(FAIL EMIT)→ health_writer (最小档案)

术语说明: 本子域诊断 Worker (Router 类). Protocol 层 Router 基类保留作技术契约,
业务叙述层统一用 Worker. Class 去 "Worker" suffix 避免重复, 继承由 (Worker) 表达.

旧 class 名 (RouterXxxWorker) deprecation alias 已在 2026-04-22 清理, 外部代码请用新名.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from .anatomy_extractor import WorkerAnatomyExtractor
from .signature_anchor import WorkerSignatureAnchor
from .context_collector import WorkerContextCollector
from .rule_checker import WorkerRuleChecker
from .contextual_auditor import WorkerContextualAuditor
from .health_writer import WorkerHealthWriter


ALL_WORKERS_WORKER_DIAG: list[type[Worker]] = [
    WorkerAnatomyExtractor,
    WorkerSignatureAnchor,
    WorkerContextCollector,
    WorkerRuleChecker,
    WorkerContextualAuditor,
    WorkerHealthWriter,
]


__all__ = [
    "WorkerAnatomyExtractor",
    "WorkerSignatureAnchor",
    "WorkerContextCollector",
    "WorkerRuleChecker",
    "WorkerContextualAuditor",
    "WorkerHealthWriter",
    "ALL_WORKERS_WORKER_DIAG",
]
