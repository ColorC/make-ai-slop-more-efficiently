---
name: pattern_discovery
description: omnicompany 重复模式发现 - 三节点串行 (SummaryReader/PatternClusterer LLM/InductionDispatcher) 找重复操作模式 + 调 trace-induction 沉淀.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/pattern_discovery ts=2026-05-04T15:47:00Z type=doc status=active agent=ai-ide belongs_to_service=pattern_discovery -->
<!-- [OMNI] summary="pattern_discovery 操作手册 — 跑模式发现的步骤 + 入口清单 + 故障排查" -->
<!-- [OMNI] tags=skill,pattern_discovery,how-to,core -->
<!-- [OMNI] material_id="material:services._core.pattern_discovery.skill.operations_manual.md"-->

# pattern_discovery · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

## 适用范围

**用我**: 想找历史行为里的重复操作模式 + 自动沉淀.
**不用我**: 实时模式发现 / 手动定义模式 / 业务正确性测试.

## 前置条件

- omnicompany 已装 + `THE_COMPANY_API_KEY` 配
- `compression_summaries` 表有未处理摘要 (跑过 compression pipeline 留下)

## 操作步骤

### 场景 A · 跑一次模式发现

```bash
omni run pattern_discovery
```

跑完看 `pd.done` sink material 含 candidates 列表 + induction 触发结果.

### 场景 B · 库调用

```python
from omnicompany.packages.services._core.pattern_discovery.pipeline import build_pipeline
from omnicompany.runtime.exec import PipelineRunner

team = build_pipeline()
runner = PipelineRunner(team)
result = runner.run({})
```

## 入口清单

| 入口 | 用途 |
|---|---|
| `omni run pattern_discovery` | 跑模式发现 |
| `build_pipeline()` (Python) | 库调用 |

## 故障排查

| 现象 | 修 |
|---|---|
| InductionDispatcher 大量 status=skipped | session_id ↔ trace_id 关联问题 (局限 1), 改 compression pipeline 写 trace_id |
| 聚类质量差 | 当前只 LLM 直判, embedding 路径未实装 (局限 2), 调 prompt |
| 跑得慢 | InductionDispatcher 串行 (局限 3), 改子 job 并行需 R-25 |

## 想了解更多

- [README.md](README.md) / [DESIGN.md](DESIGN.md)
- 下游 → ../trace_induction/
