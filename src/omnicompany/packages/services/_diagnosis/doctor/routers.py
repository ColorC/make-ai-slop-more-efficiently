# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=shim
# [OMNI] material_id="material:diagnosis.doctor.router.compatibility_shim.py"
"""doctor/routers.py — 向后兼容 shim (Stage 3 Clean Migration · 命名规范化 2026-04-22).

真实 Worker 实现已拆到 `workers/material/`, `workers/worker/`, `workers/team/`.
本文件仅为旧 FooRouter 名称保留最小兼容:
  - 旧名 FooRouter → 新 class (MaterialXxxWorker / WorkerXxx / TeamXxx)
  - 模块级 AST 辅助函数从 _archive/routers_legacy.py re-export (仍有测试引用)

不要往本文件加新逻辑; 新代码直接 import 新 class 名从 doctor/workers.
归档: `_archive/routers_legacy.py` 保留旧实现供历史追溯.
"""
from __future__ import annotations

# ─── Worker 类 (新名) ────────────────────────────────────────────────────
from .workers.material import (
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
from .workers.worker import (
    WorkerAnatomyExtractor,
    WorkerSignatureAnchor,
    WorkerContextCollector,
    WorkerRuleChecker,
    WorkerContextualAuditor,
    WorkerHealthWriter,
)
from .workers.team import (
    TeamSpecLoader,
    TeamStructuralCheck,
    TeamMaterialContractCheck,
    TeamMaturityCheck,
    TeamSoftHardCheck,
    TeamTopoHealthWriter,
    TeamNarrativeChecker,
)

# ─── 旧 Router 名别名 (外部代码若用旧名 import, 保留指向新 class) ─────────
FormatExtractorRouter = MaterialExtractorWorker
SignatureDiffRouter = MaterialSignatureDiffWorker
FiveElementCheckRouter = MaterialFiveElementCheckWorker
TagCoverageRouter = MaterialTagCoverageWorker
ParentChainRouter = MaterialParentChainWorker
CompositeFormatCheckRouter = MaterialCompositeCheckWorker
ExamplePresenceCheckRouter = MaterialExamplePresenceWorker
FormatContextualAuditRouter = MaterialContextualAuditWorker
HealthWriterRouter = MaterialHealthWriterWorker

RouterExtractorRouter = WorkerAnatomyExtractor
RouterSignatureRouter = WorkerSignatureAnchor
RouterContextCollectorRouter = WorkerContextCollector
RouterDeterministicCheckRouter = WorkerRuleChecker
RouterContextualAuditRouter = WorkerContextualAuditor
RouterHealthWriterRouter = WorkerHealthWriter

PipelineSpecLoaderRouter = TeamSpecLoader
PipelineStructuralCheckRouter = TeamStructuralCheck
PipelineFormatContractCheckRouter = TeamMaterialContractCheck
PipelineMaturityCheckRouter = TeamMaturityCheck
PipelineSoftHardCheckRouter = TeamSoftHardCheck
PipelineTopoHealthWriterRouter = TeamTopoHealthWriter
PipelineNarrativeCheckerRouter = TeamNarrativeChecker

# ─── 模块级辅助函数 re-export (测试 / 内部使用) ──────────────────────────
from ._archive.routers_legacy import (  # noqa: E402
    _is_format_call,
    _extract_kwargs,
    _iter_format_calls,
    _find_constant_name,
    _get_source_lines,
    _classify_self_assignment,
    _extract_router_ast,
    _count_run_lines,
    _get_call_repr,
    _get_line_context,
    _extract_vk_from_expr,
    _extract_verdict_pattern,
    _classify_except_handling,
    _is_router_class,
    _load_specs_from_input,
    _serialize_findings,
)


__all__ = [
    # Material subdomain — new names
    "MaterialExtractorWorker",
    "MaterialSignatureDiffWorker",
    "MaterialFiveElementCheckWorker",
    "MaterialTagCoverageWorker",
    "MaterialParentChainWorker",
    "MaterialCompositeCheckWorker",
    "MaterialExamplePresenceWorker",
    "MaterialContextualAuditWorker",
    "MaterialHealthWriterWorker",
    # Worker subdomain — new names
    "WorkerAnatomyExtractor",
    "WorkerSignatureAnchor",
    "WorkerContextCollector",
    "WorkerRuleChecker",
    "WorkerContextualAuditor",
    "WorkerHealthWriter",
    # Team subdomain — new names
    "TeamSpecLoader",
    "TeamStructuralCheck",
    "TeamMaterialContractCheck",
    "TeamMaturityCheck",
    "TeamSoftHardCheck",
    "TeamTopoHealthWriter",
    "TeamNarrativeChecker",
    # Legacy Router alias (backward compat)
    "FormatExtractorRouter",
    "SignatureDiffRouter",
    "FiveElementCheckRouter",
    "TagCoverageRouter",
    "ParentChainRouter",
    "CompositeFormatCheckRouter",
    "ExamplePresenceCheckRouter",
    "FormatContextualAuditRouter",
    "HealthWriterRouter",
    "RouterExtractorRouter",
    "RouterSignatureRouter",
    "RouterContextCollectorRouter",
    "RouterDeterministicCheckRouter",
    "RouterContextualAuditRouter",
    "RouterHealthWriterRouter",
    "PipelineSpecLoaderRouter",
    "PipelineStructuralCheckRouter",
    "PipelineFormatContractCheckRouter",
    "PipelineMaturityCheckRouter",
    "PipelineSoftHardCheckRouter",
    "PipelineTopoHealthWriterRouter",
    "PipelineNarrativeCheckerRouter",
]
