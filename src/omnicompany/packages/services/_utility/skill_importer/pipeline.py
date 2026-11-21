# [OMNI] DEPRECATED 2026-04-21 -- migrated to team.py
# [OMNI] material_id="material:utility.skill_importer.pipeline_alias.config.py"
# 2026-04-21: 修复 Phase B rename 后的 import 别名 (team.py 里函数名是
# build_skill_importer_pipeline 和 build_verify_pipeline, 不是 build_team)
from .team import (  # noqa: F401
    build_skill_importer_pipeline,
    build_skill_importer_pipeline as build_pipeline,
    build_verify_pipeline,
)
