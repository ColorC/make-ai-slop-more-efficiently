# [OMNI] origin=claude-code domain=services/skill_importer ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:utility.skill_importer.router_compatibility_shim.config.py"
"""skill_importer routers — 兼容垫片 (Phase D Diamond shortcut 2026-04-20).

业务实现已迁到 workers/ (Diamond shortcut 模式). 本文件保留旧名称以兼容调用方.
"""
from __future__ import annotations

from .workers import (
    SkillParserWorker as SkillParserRouter,
    StructureAnalysisWorker as StructureAnalysisRouter,
    MaterialInferenceWorker as MaterialInferenceRouter,
    RequirementDraftWorker as RequirementDraftRouter,
    VerifyAgainstSkillWorker as VerifyAgainstSkillRouter,
)

__all__ = [
    "SkillParserRouter",
    "StructureAnalysisRouter",
    "MaterialInferenceRouter",
    "RequirementDraftRouter",
    "VerifyAgainstSkillRouter",
]
