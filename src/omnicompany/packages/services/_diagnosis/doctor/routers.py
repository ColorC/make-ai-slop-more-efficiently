# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=shim
# [OMNI] material_id="material:diagnosis.doctor.router.compatibility_shim.py"
"""doctor/routers.py — 向后兼容 shim (Stage 3 Clean Migration · 命名规范化 2026-04-22).

真实 Worker 实现已拆到 `workers/material/`, `workers/worker/`, `workers/team/`.
本文件仅为旧 FooRouter 名称保留最小兼容:
  - 旧名 FooRouter → 新 class (MaterialXxxWorker / WorkerXxx / TeamXxx)
  - 模块级 AST 辅助函数从 `workers/{material,worker,team}/_shared.py` re-export
    (下划线旧名 alias, 仍有测试引用; 2026-07-26 OMNI-040 Stage 3 改指正式位置)

不要往本文件加新逻辑; 新代码直接 import 新 class 名从 doctor/workers.
归档: `_archive/routers_legacy.py` 保留旧实现供历史追溯 (活代码不再 import).
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
# 2026-07-26 OMNI-040 Stage 3: 改从 workers/ 各子域 _shared (Stage 3 正式位置)
# import, 下划线旧名 alias 保持向后兼容; `_archive/routers_legacy.py` 纯历史参考.
from .workers.material._shared import (  # noqa: E402
    is_format_call as _is_format_call,
    extract_kwargs as _extract_kwargs,
    iter_format_calls as _iter_format_calls,
    find_constant_name as _find_constant_name,
)
from .workers.worker._shared import (  # noqa: E402
    get_source_lines as _get_source_lines,
    classify_self_assignment as _classify_self_assignment,
    extract_router_ast as _extract_router_ast,
    count_run_lines as _count_run_lines,
    get_call_repr as _get_call_repr,
    get_line_context as _get_line_context,
    extract_vk_from_expr as _extract_vk_from_expr,
    extract_verdict_pattern as _extract_verdict_pattern,
    classify_except_handling as _classify_except_handling,
    is_router_class as _is_router_class,
)
from .workers.team._shared import (  # noqa: E402
    load_specs_from_input as _load_specs_from_input,
    serialize_findings as _serialize_findings,
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
