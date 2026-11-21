# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=config
# [OMNI] material_id="material:diagnosis.doctor.workers.all_workers.registry.py"
"""doctor Team · 30 Worker 清单 (24 主 + 6 blackboard · Stage 3 命名规范化完成 2026-04-22).

四个子域:
  - material/   (9 Worker) · Material 诊断 build_team
  - worker/     (6 Worker) · Worker 诊断 build_router_pipeline
  - team/       (9 Worker) · Team 拓扑诊断 build_team_topology_pipeline
  - blackboard/ (6 Worker) · 订阅图诊断 (F-19 / R-23~R-25)

业务术语: Material / Worker / Team (不再出现 Format / Router / Pipeline 作业务对象).
Protocol 层契约名 (Router 基类 / Format 类 / FORMAT_IN 字段等) 保留作技术身份.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from .material import (
    ALL_WORKERS_MATERIAL,
    MaterialExtractorWorker,
    MaterialSignatureDiffWorker,
    MaterialFiveElementCheckWorker,
    MaterialTagCoverageWorker,
    MaterialParentChainWorker,
    MaterialCompositeCheckWorker,
    MaterialExamplePresenceWorker,
    MaterialContextualAuditWorker,
    MaterialHealthWriterWorker,
)
from .worker import (
    ALL_WORKERS_WORKER_DIAG,
    WorkerAnatomyExtractor,
    WorkerSignatureAnchor,
    WorkerContextCollector,
    WorkerRuleChecker,
    WorkerContextualAuditor,
    WorkerHealthWriter,
)
from .team import (
    ALL_WORKERS_TEAM,
    TeamSpecLoader,
    TeamStructuralCheck,
    TeamMaterialContractCheck,
    TeamMaturityCheck,
    TeamSoftHardCheck,
    TeamTopoHealthWriter,
    TeamNarrativeChecker,
    TeamTopologyCheck,
    TeamLineageExtractor,
)
from .blackboard import (
    ALL_WORKERS_BLACKBOARD,
    MaterialKindLegalityWorker,
    FormatInModeCheckerWorker,
    VerdictOutputFlatCheckerWorker,
    OrphanWorkerScannerWorker,
    UnconsumedMaterialScannerWorker,
    EmitAsNewJobCheckerWorker,
)


ALL_WORKERS: list[type[Worker]] = (
    ALL_WORKERS_MATERIAL + ALL_WORKERS_WORKER_DIAG + ALL_WORKERS_TEAM + ALL_WORKERS_BLACKBOARD
)


__all__ = [
    # Subdomain manifests
    "ALL_WORKERS_MATERIAL",
    "ALL_WORKERS_WORKER_DIAG",
    "ALL_WORKERS_TEAM",
    "ALL_WORKERS_BLACKBOARD",
    "ALL_WORKERS",
    # Material subdomain (9)
    "MaterialExtractorWorker",
    "MaterialSignatureDiffWorker",
    "MaterialFiveElementCheckWorker",
    "MaterialTagCoverageWorker",
    "MaterialParentChainWorker",
    "MaterialCompositeCheckWorker",
    "MaterialExamplePresenceWorker",
    "MaterialContextualAuditWorker",
    "MaterialHealthWriterWorker",
    # Worker subdomain (6)
    "WorkerAnatomyExtractor",
    "WorkerSignatureAnchor",
    "WorkerContextCollector",
    "WorkerRuleChecker",
    "WorkerContextualAuditor",
    "WorkerHealthWriter",
    # Team subdomain (9)
    "TeamSpecLoader",
    "TeamStructuralCheck",
    "TeamMaterialContractCheck",
    "TeamMaturityCheck",
    "TeamSoftHardCheck",
    "TeamTopoHealthWriter",
    "TeamNarrativeChecker",
    "TeamTopologyCheck",
    "TeamLineageExtractor",
    # Blackboard subdomain (6)
    "MaterialKindLegalityWorker",
    "FormatInModeCheckerWorker",
    "VerdictOutputFlatCheckerWorker",
    "OrphanWorkerScannerWorker",
    "UnconsumedMaterialScannerWorker",
    "EmitAsNewJobCheckerWorker",
]
