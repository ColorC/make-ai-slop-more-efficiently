---
name: team_loader
description: omnicompany yaml Team 加载 - 简单 Team yaml 写不写 Python.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/team_loader ts=2026-05-04T17:18:00Z type=doc status=active agent=ai-ide belongs_to_service=team_loader -->

# team_loader · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

## 适用范围

**用我**: 简单 Team 用 yaml 定义.
**不用我**: 复杂 Team (含动态逻辑) — 写 Python pipeline.py.

## 操作步骤

### 场景 A · 加载 yaml Team

```python
from omnicompany.packages.services._core.team_loader.yaml_loader import load_team_from_yaml

team = load_team_from_yaml("path/to/team.yaml")
```

### 场景 B · 写 yaml Team

```yaml
team_id: my-simple-team
workers:
  - id: my_worker_1
  - id: my_worker_2
edges:
  - from: my_worker_1
    to: my_worker_2
```

## 入口清单

| 入口 | 用途 |
|---|---|
| `load_team_from_yaml(path)` (Python) | 加载 yaml |
| `omni team validate / show / load` | CLI 命令 |

## 故障排查

| 现象 | 修 |
|---|---|
| yaml 加载报 Worker 找不到 | Worker id 在 registry 没注册 |
| 复杂拓扑 yaml 写不出来 | 改 Python pipeline.py |

## 想了解更多

- [README.md](README.md) / [DESIGN.md](DESIGN.md)
- omnicompany TeamSpec → [../omnicompany/SKILL.md](../omnicompany/SKILL.md)
