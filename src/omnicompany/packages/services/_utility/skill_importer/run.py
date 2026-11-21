# [OMNI] origin=claude-code domain=skill_importer/run.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:utility.skill_importer.pipeline_composer.config.py"
"""skill_importer run — 2026-04-09 重构版.

主管线 skill-import: parse → analyze → infer → draft_requirement
独立管线 skill-import-verify: 单节点 verify_against_skill
"""

from __future__ import annotations

from typing import Any

from omnicompany.runtime.routing.router import Router

# Re-export pipeline builders
from omnicompany.packages.services._utility.skill_importer.pipeline import (
    build_skill_importer_pipeline,
    build_verify_pipeline,
)


__all__ = [
    "build_skill_importer_pipeline",
    "build_verify_pipeline",
    "build_skill_importer_bindings",
    "build_verify_bindings",
]


def build_skill_importer_bindings(
    input_dict: dict[str, Any] | None = None,
) -> dict[str, Router]:
    """主管线 bindings: 4 个节点."""
    from omnicompany.packages.services._utility.skill_importer.routers import (
        MaterialInferenceRouter,
        RequirementDraftRouter,
        SkillParserRouter,
        StructureAnalysisRouter,
    )

    return {
        "parse": SkillParserRouter(),
        "analyze": StructureAnalysisRouter(),
        "infer": MaterialInferenceRouter(),
        "draft_requirement": RequirementDraftRouter(),
    }


def build_verify_bindings(
    input_dict: dict[str, Any] | None = None,
) -> dict[str, Router]:
    """验证管线 bindings: 1 个节点."""
    from omnicompany.packages.services._utility.skill_importer.routers import (
        VerifyAgainstSkillRouter,
    )

    return {
        "verify": VerifyAgainstSkillRouter(),
    }
