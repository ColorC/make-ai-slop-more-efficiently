---
name: configurable
description: omnicompany 配置驱动 spec 库 - ConfigurableHook + HookSpec / ConfigurableTool + ToolSpec 替代写硬编码子类.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/configurable ts=2026-05-04T17:10:00Z type=doc status=active agent=ai-ide belongs_to_service=configurable -->

# configurable · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

## 适用范围

**用我**: 想给 hook / tool 加新变体 (写 SPEC 不写子类).
**不用我**: 复杂业务子类需要重写多 hook (写子类直接); Agent SPEC (找 [agent](../agent/)).

## 操作步骤

### 场景 A · 写新 hook spec

```python
from omnicompany.packages.services._core.configurable import HookSpec, ConfigurableHook

my_hook = ConfigurableHook(spec=HookSpec(
    name="my_hook",
    description="...",
    trigger="on_team_complete",
    executor=lambda ctx: ...,
))
```

### 场景 B · 写新 tool spec

```python
from omnicompany.packages.services._core.configurable import ToolSpec, ConfigurableTool

my_tool = ConfigurableTool(spec=ToolSpec(
    name="my_tool",
    description="...",
    parameters={"foo": "string"},
    executor=lambda params, ctx: ...,
))
```

## 入口清单

| 入口 (Python) | 用途 |
|---|---|
| `HookSpec / ConfigurableHook` | 配置驱动 hook |
| `ToolSpec / ConfigurableTool` | 配置驱动 tool |

## 故障排查

| 现象 | 修 |
|---|---|
| spec 不够用 (复杂行为) | 写子类直接, 不勉强用 spec |

## 想了解更多

- [README.md](README.md) / [DESIGN.md](DESIGN.md)
- 兄弟 → [../agent/](../agent/) (ConfigurableAgent)
