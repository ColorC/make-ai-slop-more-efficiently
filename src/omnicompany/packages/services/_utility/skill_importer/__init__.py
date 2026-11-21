# [OMNI] origin=claude-code domain=skill_importer/__init__.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:utility.skill_importer.package_exports.config.py"
"""omnicompany.packages.services._utility.skill_importer.

2026-04-09 重构: 不再自己生成 Python 代码。职责收敛到两条:
  1. 解析一个 Claude Code Skill → 产 workflow-factory 可消费的 markdown 需求稿
  2. 在 workflow-factory 生成 package 后做忠实度检验

两条对应两条 pipeline: skill-import (主) + skill-import-verify (独立验证).
"""

from omnicompany.packages.services._utility.skill_importer.formats import (
    ALL_FORMATS,
    register_formats,
)
from omnicompany.packages.services._utility.skill_importer.pipeline import (
    build_skill_importer_pipeline,
    build_verify_pipeline,
)
from omnicompany.packages.services._utility.skill_importer.run import (
    build_skill_importer_bindings,
    build_verify_bindings,
)

__all__ = [
    "ALL_FORMATS",
    "register_formats",
    "build_skill_importer_pipeline",
    "build_verify_pipeline",
    "build_skill_importer_bindings",
    "build_verify_bindings",
]
