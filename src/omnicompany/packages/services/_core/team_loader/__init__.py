# [OMNI] origin=ai-ide domain=services/_core/team_loader ts=2026-05-02T05:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="team yaml loader - 从纯 yaml 加载 TeamSpec, 让 team 可纯配置不写 Python"
# [OMNI] why="用户原始需求 6.3.1: team 暂时纯配置, 之后再看. yaml loader 让 team 立新实例不需要写 build_team 函数"
# [OMNI] tags=team,loader,yaml,configurable,no-code
# [OMNI] material_id="material:core.team_loader.service_aggregator.exports.py"
"""team yaml loader.

让 team 可纯 yaml 声明, 不写 Python. 适用场景:
- 简单线性 pipeline (worker 已存在, 只是拼链)
- 业务方不熟 Python
- 跑 demo / poc 阶段

复杂逻辑 (动态构造节点 / 条件路由) 仍需走 pipeline.py + build_team() 函数.

用法:
    from omnicompany.packages.services._core.team_loader import load_team_from_yaml

    team = load_team_from_yaml('templates/team/纯配置范本.yaml')
    # team 是合法 TeamSpec, 可丢给 TeamRunner 跑
"""
from __future__ import annotations

from omnicompany.packages.services._core.team_loader.yaml_loader import (
    load_team_from_yaml,
    load_team_from_dict,
    dump_team_to_yaml,
)

__all__ = [
    "load_team_from_yaml",
    "load_team_from_dict",
    "dump_team_to_yaml",
]
