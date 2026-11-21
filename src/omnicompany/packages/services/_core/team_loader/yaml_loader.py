# [OMNI] origin=ai-ide domain=services/_core/team_loader ts=2026-05-02T05:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="yaml loader - TeamSpec 从 yaml 加载 + dump 回 yaml"
# [OMNI] why="TeamSpec 是 pydantic BaseModel, 天然支持 dict <-> model 转换. 这层是 yaml <-> dict 桥"
# [OMNI] tags=team,loader,yaml
# [OMNI] material_id="material:core.team_loader.yaml_bridge.implementation.py"
"""TeamSpec yaml 加载 / 反向 dump.

yaml 格式约定:

```yaml
id: my_team
name: MyTeam
description: 干嘛的
entry: node_a
nodes:
  - id: node_a
    kind: ANCHOR        # ANCHOR / SOURCE / SINK
    maturity: GROWING   # NEWBORN / GROWING / MATURE / DECLINING
    anchor:
      id: a_node_a
      name: node_a
      format_in: ["my.input"]
      format_out: my.intermediate
      validator:
        id: v_node_a
        kind: HARD       # HARD / SOFT / AGENT
        description: 干嘛
      routes:
        - condition: PASS
          action: NEXT
          target: node_b
        - condition: FAIL
          action: HALT
edges:
  - from_node: node_a
    to_node: node_b
    condition: PASS
tags: [my_team, demo]
```

复杂场景 (动态节点 / 自定义 worker class) 仍需 pipeline.py + build_team(). yaml loader
适合简单线性串联的 demo / poc.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from omnicompany.protocol.team import TeamSpec


def load_team_from_yaml(path: str | Path) -> TeamSpec:
    """从 yaml 文件加载 TeamSpec.

    yaml 字段直接映射到 TeamSpec / TeamNode / AnchorSpec 等 pydantic model.
    yaml 解析后走 model_validate, 校验失败抛 ValidationError.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"team yaml 不存在: {p}")
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"team yaml 顶层必须是 dict, got {type(data).__name__}")
    return load_team_from_dict(data)


def load_team_from_dict(data: dict) -> TeamSpec:
    """从 dict 加载 TeamSpec (yaml.safe_load 后的中间形态)."""
    return TeamSpec.model_validate(data)


def dump_team_to_yaml(team: TeamSpec, path: str | Path | None = None) -> str:
    """TeamSpec 转 yaml 字符串. 给 path 时同时落盘."""
    data = team.model_dump(mode="json", exclude_none=True)
    yaml_str = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, indent=2)
    if path is not None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml_str, encoding="utf-8")
    return yaml_str
