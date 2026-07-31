# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:16Z
---
omnikb_type: karch
id: kb.arch.pipeline.sw_plan
name: 'Pipeline: sw-plan'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.sw-plan
- domain.sw_plan
maturity: draft
summary: 实施计划 — 代码库探索 + TDD 计划生成 + 自检循环
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L90-L115
---

# Pipeline: sw-plan

> **已知概要**: 实施计划 — 代码库探索 + TDD 计划生成 + 自检循环

## Identity

| Field | Value |
|---|---|
| Pipeline name | `sw-plan` |
| Domain tag | `sw_plan` |
| Registered in | `src/omnicompany/core/pipelines.py` |
| Maturity stage (此 wiki 条目) | draft (auto-seeded) |

## Why this exists

_(待补充: 这条管线解决了什么问题, 为什么不能用其他已有管线?)_

## How it works

_(待补充: 主要节点流程, 关键 Format, 核心 Router, 输入输出契约)_

## Files

- `src/omnicompany/core/pipelines.py` — 注册条目所在
- _(待补充: build_pipeline / build_bindings 所在的 run.py)_
- _(待补充: pipeline.py / routers.py)_

## Related

- _(待补充: 关联的 KDecision, 例如设计选择 ADR)_
- _(待补充: 关联的 KArchitecture, 例如其依赖的核心抽象)_
- _(待补充: 关联的 KExperiment, 例如设计阶段的试验)_

## Known limitations

_(待补充: 当前已知 bug, 未实现部分, 不适用场景)_

## Change log

- 2026-04-08 — auto-seeded from `core/pipelines.py` register block
