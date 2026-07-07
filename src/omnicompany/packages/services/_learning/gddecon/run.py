# [OMNI] origin=claude-code domain=services/_learning/gddecon ts=2026-06-27T00:00:00Z type=bindings status=active
"""gddecon.run —— 事件型引擎的 build_team (worker 清单)。

engine="event" 约定: build_team() 返回 worker 实例清单, MaterialDispatcher 驱动。
真实拆解逻辑在 gddecon.pipeline.run_deconstruction / DeconstructionWorker。
"""
from __future__ import annotations


def build_team_workers() -> list:
    """gddecon-aspect-tree 事件型 worker 清单。"""
    from omnicompany.packages.services._learning.gddecon.workers import ALL_WORKERS
    return [W() for W in ALL_WORKERS]


def build_gap_workers() -> list:
    """gddecon-gap-report 事件型 worker 清单。"""
    from omnicompany.packages.services._learning.gddecon.workers import GAP_WORKERS
    return [W() for W in GAP_WORKERS]


def build_ui_standard_workers() -> list:
    """gddecon-ui-standard 事件型 worker 清单。"""
    from omnicompany.packages.services._learning.gddecon.workers import UI_STANDARD_WORKERS
    return [W() for W in UI_STANDARD_WORKERS]


def build_ui_build_workers() -> list:
    """gddecon-ui-build 事件型 worker 清单。"""
    from omnicompany.packages.services._learning.gddecon.workers import UI_BUILD_WORKERS
    return [W() for W in UI_BUILD_WORKERS]


def build_info_hierarchy_workers() -> list:
    """gddecon-info-hierarchy 事件型 worker 清单。"""
    from omnicompany.packages.services._learning.gddecon.workers import INFO_HIER_WORKERS
    return [W() for W in INFO_HIER_WORKERS]


def build_interaction_model_workers() -> list:
    """gddecon-interaction-model 事件型 worker 清单。"""
    from omnicompany.packages.services._learning.gddecon.workers import INTERACTION_WORKERS
    return [W() for W in INTERACTION_WORKERS]
