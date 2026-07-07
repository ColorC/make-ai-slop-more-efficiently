# [OMNI] origin=claude-code domain=project_atlas ts=2026-06-21 type=cli_entry status=active
# [OMNI] summary="project_atlas 的 bindings 工厂:节点 ID → Router 实例(4 节点)。"
# [OMNI] why="框架级统一:Team 的节点要绑到具体 Router。键与 team.py 的节点 id 对齐。"
# [OMNI] tags=project_atlas,run,bindings

from __future__ import annotations

from typing import Any

from omnicompany.runtime.routing.router import Router


def build_project_atlas_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]:
    """project_atlas.run 的节点 ID → Router 绑定(4 节点)。"""
    from omnicompany.packages.domains.project_atlas.routers.pipeline import Finalize, Intake, Survey
    from omnicompany.packages.domains.project_atlas.routers.worker import Collect

    return {
        "intake": Intake(),
        "survey": Survey(),
        "collect": Collect(),
        "finalize": Finalize(),
    }
