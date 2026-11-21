# [OMNI] origin=ai-ide domain=slidecast ts=2026-06-20T00:00:00Z type=cli_entry status=active
# [OMNI] summary="slidecast.run 的 bindings 工厂: 节点 ID → Router 实例。供 dispatch 调度。"
# [OMNI] why="框架级统一: Team 节点绑到具体 Router。与 team.py 的节点 id 对齐(intake/outline/author_ir/validate_ir/render_slidev/build_deck)。"
# [OMNI] tags=slidecast,run,bindings

from __future__ import annotations

from typing import Any

from omnicompany.runtime.routing.router import Router


def build_slidecast_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]:
    """slidecast.run 的节点 ID → Router 绑定(6 节点 IR-first 管线)。"""
    from omnicompany.packages.domains.slidecast.routers.author import AuthorIR, Outline
    from omnicompany.packages.domains.slidecast.routers.pipeline import (
        BuildDeck,
        Intake,
        RenderSlidev,
        ValidateIR,
    )

    return {
        "intake": Intake(),
        "outline": Outline(),
        "author_ir": AuthorIR(),
        "validate_ir": ValidateIR(),
        "render_slidev": RenderSlidev(),
        "build_deck": BuildDeck(),
    }
