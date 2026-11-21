# [OMNI] DEPRECATED 2026-04-21 -- migrated to team.py
# [OMNI] material_id="material:diagnosis.doctor.pipeline.compatibility_shim.py"
# 2026-04-22: 修复 run.py 依赖的 build_router_pipeline / build_team_topology_pipeline shim
from .team import (  # noqa: F401
    build_team,
    build_team as build_pipeline,
    build_team_topology_pipeline,
    build_team_topology_pipeline as build_topology_pipeline,
    build_team_topology_pipeline as build_pipeline_topology_pipeline,
    build_router_pipeline,
)
