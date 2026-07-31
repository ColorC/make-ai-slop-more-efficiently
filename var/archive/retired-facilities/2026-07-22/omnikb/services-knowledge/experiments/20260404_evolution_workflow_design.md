# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:22Z
---
omnikb_type: kexp
id: kb.experiment.20260404_evolution_workflow_design
name: 进化工作流设计
tags:
- topic.plan
- date.2026-04-04
maturity: draft
summary: '> **日期**: 2026-04-04 > **状态**: 核心思想确立，待细化设计 > **前置**: [Long-Term]LAP-EVOLUTION-ENGINE
  (已有理论框架)'
date_started: '2026-04-04'
method_summary: see docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/
status: documented
followups:
- docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/EVOLUTION_LIFECYCLE_ANALYSIS.md
- docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/EVOLVER_SURVEY.md
- docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/HYPOTHESIS_BLACKBOARD.md
- docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/PAIN_PATTERNS_AND_EVOLUTION.md
- docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/README.md
- docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/WORKFLOW_SPEC.md
---

# 进化工作流设计

> Plan directory: `docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
# 进化工作流设计

> **日期**: 2026-04-04
> **状态**: 核心思想确立，待细化设计
> **前置**: [Long-Term]LAP-EVOLUTION-ENGINE (已有理论框架)

## 背景

已有 Evolution Engine 理论设计（残差驱动、MCTS 策略搜索、validator-meta 变异注入），
但缺少**可执行的进化工作流**——即：日常运行中，管线如何实际发生进化。

本计划聚焦：把理论落地为具体的工作流管线。

## 核心认知

### 疼痛模型（不是分类模型）

之前试图把失败原因对齐到六元原语做分类。**方向错了。**

实际上：工作流出问题的模式非常多样，不应强行分类。我们能观测到的是**疼痛**——
知道哪里痛、痛的模式，但不一定知道原因。原因需要**诊断**，不是分类。

### 两大疼痛模式

1. **触犯红线（急性痛）** — 位置明确，位置即原因，本质是 debug
2. **完成质量不行（慢性痛）** — 需要深度诊断，这才是真正的进化

详见 PAIN_PATTERNS_AND_EVOLUTION.md。

### Omnicompany 的进化哲学

> 只要每个节点的 output 符合 Format 定义，管线就成功。
> 错了？沿管线语义追踪，定位到第一个偏离 Format 的节点，然后诊断。
> 诊断不强制分类——原因可能是复合的，要求的是结构化分析而非标签。

## 外部参考调研：EvoMap/evolver

调研了 EvoMap/evolver 项目（AI Agent 自进化引擎，MIT，Node.js，~50 个源文件）。

### 结论：参考价值有限

Evolver 产出 prompt，我们产出管线变更。Evolver 面对全能 Agent，我们面对受限管线。
我们已有的 Evolution Engine Spec 在理论深度上完全超过它。
唯一可参考的是 `扫描→信号→方案→执行→验证→固化` 的骨架节奏，具体实现完全不同。

详见 EVOLVER_SURVEY.md。

## 文档索引

| 文档 | 内容 | 状态 |
|---|---|---|
| EVOLVER_SURVEY.md | Evolver 项目调研详情 | Done |
| PAIN_PATTERNS_AND_EVOLUTION.md | 两大疼痛模式 + 进化工作流骨架 | Core |
| EVOLUTION_LIFECYCLE_ANALYSIS.md | 七阶段生命周期分析：Evolver 对照 + 验证/门控/回退设计 | Core |
| WORKFLOW_SPEC.md | 新进化工作流完整规格：类人学习 + 受控实验 + 因果理解 | **Done** |
| HYPOTHESIS_BLACKBOARD.md | 假设黑板机制：上下文预算管理 + 修改位置锁定 + 假设生命周期 | **Done** |

## 与已有计划的关系

- **[Long-Term]LAP-EVOLU
```

## Plan files

- `docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/EVOLUTION_LIFECYCLE_ANALYSIS.md`
- `docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/EVOLVER_SURVEY.md`
- `docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/HYPOTHESIS_BLACKBOARD.md`
- `docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/PAIN_PATTERNS_AND_EVOLUTION.md`
- `docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/README.md`
- `docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/WORKFLOW_SPEC.md`

## Hypothesis

_(待补充: 这个 plan 的核心假设, 为什么需要做)_

## Method

_(待补充: 实施方法, 关键步骤)_

## Samples

_(待补充: 跑过的样本, 各自结果)_

## Findings

_(待补充: 关键发现, 哪些假设被验证, 哪些被推翻)_

## Followups

_(待补充: 后续 TODO, 关联其他 plan / 计划目录中的文件已自动列在 frontmatter)_

## Change log

- 2026-04-08 — auto-seeded from plan README
