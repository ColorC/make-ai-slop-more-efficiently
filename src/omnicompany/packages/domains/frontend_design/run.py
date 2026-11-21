# [OMNI] origin=claude-code domain=frontend_design ts=2026-07-01T00:00:00Z type=cli_entry status=design
# [OMNI] summary="frontend_design 两分支的 bindings 工厂: 节点 ID → Router。两分支共用同一套 Router(靠数据分流)。"
# [OMNI] why="框架级统一: Team 的节点要绑到具体 Router。与 team.py 的节点 id 对齐。"
# [OMNI] tags=frontend_design,run,bindings

from __future__ import annotations

from typing import Any

from omnicompany.runtime.routing.router import Router


def _bindings() -> dict[str, Router]:
    """四节点 ID → Router。dashboard/webgame 共用同一套 Router, 分支身份走数据(intake 从请求读 archetype)。"""
    from omnicompany.packages.domains.frontend_design.routers.pipeline import (
        DeterministicGate,
        ReviewIntake,
        Synthesize,
        VlmRelativeReview,
    )

    return {
        "intake": ReviewIntake(),
        "gate": DeterministicGate(),
        "vlm_review": VlmRelativeReview(),
        "synthesize": Synthesize(),
    }


def build_dashboard_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]:
    """frontend_design.dashboard 的节点绑定。"""
    return _bindings()


def build_webgame_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]:
    """frontend_design.webgame 的节点绑定。"""
    return _bindings()
