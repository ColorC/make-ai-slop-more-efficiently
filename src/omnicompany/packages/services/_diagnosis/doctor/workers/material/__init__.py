# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=config
# [OMNI] material_id="material:diagnosis.doctor.worker.material.package_aggregate.py"
"""doctor Material 诊断子域 · 9 Worker (Stage 3 Clean Migration · 命名规范化完成).

订阅拓扑 (详见 ../../team.py build_team):
  material_extractor → signature_diff ─(PASS fan-out)→ five_element / tag_coverage / parent_chain /
                                                        composite_check / example_presence /
                                                        material_contextual_audit (LLM)
                                     └─(FAIL EMIT)→ health_writer (最小档案)
  → fan-in → health_writer

术语说明: 本子域诊断 Material (Format 对象). Protocol 层 Format 类 + FORMAT_IN/OUT
字段名保留作技术契约, 业务叙述层统一用 Material.

旧 class 名 (FormatXxxWorker) deprecation alias 已在 2026-04-22 清理, 外部代码请用新名.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from .material_extractor import MaterialExtractorWorker
from .signature_diff import MaterialSignatureDiffWorker
from .five_element_check import MaterialFiveElementCheckWorker
from .tag_coverage import MaterialTagCoverageWorker
from .parent_chain import MaterialParentChainWorker
from .composite_check import MaterialCompositeCheckWorker
from .example_presence_check import MaterialExamplePresenceWorker
from .material_contextual_audit import MaterialContextualAuditWorker
from .health_writer import MaterialHealthWriterWorker


ALL_WORKERS_MATERIAL: list[type[Worker]] = [
    MaterialExtractorWorker,
    MaterialSignatureDiffWorker,
    MaterialFiveElementCheckWorker,
    MaterialTagCoverageWorker,
    MaterialParentChainWorker,
    MaterialCompositeCheckWorker,
    MaterialExamplePresenceWorker,
    MaterialContextualAuditWorker,
    MaterialHealthWriterWorker,
]


__all__ = [
    "MaterialExtractorWorker",
    "MaterialSignatureDiffWorker",
    "MaterialFiveElementCheckWorker",
    "MaterialTagCoverageWorker",
    "MaterialParentChainWorker",
    "MaterialCompositeCheckWorker",
    "MaterialExamplePresenceWorker",
    "MaterialContextualAuditWorker",
    "MaterialHealthWriterWorker",
    "ALL_WORKERS_MATERIAL",
]
