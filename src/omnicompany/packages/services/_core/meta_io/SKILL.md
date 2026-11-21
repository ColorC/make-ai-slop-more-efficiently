---
name: meta_io
description: omnicompany 元 IO 工具操作 - tool 调用统一 audit + 注册 + 状态绑定. 用户 6.6 需求实施.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/meta_io ts=2026-05-04T17:14:00Z type=doc status=active agent=ai-ide belongs_to_service=meta_io -->

# meta_io · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

## 适用范围

**用我**: 想注册新工具 / 看 tool 调用 audit / 给某 tool 加状态校验.
**不用我**: LLM audit (找 omni llm 命令); 业务工具实现 (写 SingleToolRouter 子类).

## 操作步骤

### 场景 A · 看 tool 调用 audit

```bash
omni meta-io audit --tool=<tool_name>
```

### 场景 B · 注册新工具

```python
from omnicompany.packages.services._core.meta_io.registry import register_tool
register_tool(tool_def=...)
```

### 场景 C · 状态校验

```python
from omnicompany.packages.services._core.meta_io.state_check import check_before, check_after
```

## 入口清单

| 入口 | 用途 |
|---|---|
| `omni meta-io ...` | CLI (具体子命令见 `omni meta-io --help`) |
| `register_tool` / `audit` / `state_check` (Python) | 库调用 |

## 故障排查

| 现象 | 修 |
|---|---|
| tool 调用没 audit | 没走 ToolDispatch / 直调 SingleTool 跳过 audit, 走 ToolDispatch |

## 想了解更多

- [README.md](README.md) / [DESIGN.md](DESIGN.md)
- CLI → `omni meta-io --help`
- agent ToolDispatch → [../agent/README.md](../agent/README.md)
