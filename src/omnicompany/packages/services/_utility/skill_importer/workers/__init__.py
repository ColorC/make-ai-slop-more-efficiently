# [OMNI] origin=claude-code domain=services/skill_importer ts=2026-04-22T00:00:00Z type=config
# [OMNI] material_id="material:utility.skill_importer.worker_exports.config.py"
"""skill_importer Team 的 Worker 集合 (Stage 3 Clean Migration 2026-04-22).

5 个独立 Worker 文件:
  - skill_parser.py          → SkillParserWorker          (HARD · 确定性 SKILL.md 解析)
  - structure_analysis.py    → StructureAnalysisWorker    (SOFT · LLM 归纳结构)
  - material_inference.py    → MaterialInferenceWorker      (HARD · 确定性 Material 命名推断)
  - requirement_draft.py     → RequirementDraftWorker     (SOFT · LLM 产出需求稿)
  - verify_against_skill.py  → VerifyAgainstSkillWorker   (SOFT · LLM 忠实度检验)

_archive/routers_legacy.py 仅保留作为历史参考 (OMNI-024 ALLOW), 不再被 workers/ 继承。
"""
from __future__ import annotations

from .material_inference import MaterialInferenceWorker
from .requirement_draft import RequirementDraftWorker
from .skill_parser import SkillParserWorker
from .structure_analysis import StructureAnalysisWorker
from .verify_against_skill import VerifyAgainstSkillWorker


ALL_WORKERS = [
    SkillParserWorker,
    StructureAnalysisWorker,
    MaterialInferenceWorker,
    RequirementDraftWorker,
    VerifyAgainstSkillWorker,
]

__all__ = [
    "SkillParserWorker",
    "StructureAnalysisWorker",
    "MaterialInferenceWorker",
    "RequirementDraftWorker",
    "VerifyAgainstSkillWorker",
    "ALL_WORKERS",
]
