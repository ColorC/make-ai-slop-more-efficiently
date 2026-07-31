# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:21Z
---
omnikb_type: kexp
id: kb.experiment.20260327_trace_sedimentation_redesign
name: Trace Sedimentation Redesign
tags:
- topic.plan
- date.2026-03-27
maturity: draft
summary: '> 创建：2026-03-27'
date_started: '2026-03-27'
method_summary: see docs/plans/[2026-03-27]TRACE-SEDIMENTATION-REDESIGN/
status: documented
followups:
- docs/plans/[2026-03-27]TRACE-SEDIMENTATION-REDESIGN/01-diagnosis.md
- docs/plans/[2026-03-27]TRACE-SEDIMENTATION-REDESIGN/02-circuit-design.md
- docs/plans/[2026-03-27]TRACE-SEDIMENTATION-REDESIGN/03-extraction-nodes.md
- docs/plans/[2026-03-27]TRACE-SEDIMENTATION-REDESIGN/04-phases.md
- docs/plans/[2026-03-27]TRACE-SEDIMENTATION-REDESIGN/05-pipeline-integrity.md
- docs/plans/[2026-03-27]TRACE-SEDIMENTATION-REDESIGN/README.md
---

# Trace Sedimentation Redesign

> Plan directory: `docs/plans/[2026-03-27]TRACE-SEDIMENTATION-REDESIGN/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
# Trace Sedimentation Redesign
# 轨迹沉淀机制重设计

> 创建：2026-03-27

## 核心目标

将 agent 执行经验自动沉淀为可复用的语义节点（Node）和语义类型（Format），
使系统在多次运行后逐步形成可替代 agent_loop 单步推理的结构化工作流路径。

## 文件索引

| 文件 | 内容 |
|------|------|
| 01-diagnosis.md | 当前 CH3 机制的失效分析 |
| 02-circuit-design.md | 沉淀回路的六元模型设计（核心） |
| 03-extraction-nodes.md | 各提取节点的详细设计 |
| 04-phases.md | 分阶段实现计划 |
| 05-pipeline-integrity.md | 管线完整性验证与串联补足 |

## 一句话设计

```
[运行中] ShadowRoutingMonitor（每步并行检测类型可达性）
    ↓ shadow_routing_report（覆盖率、悬空引用）

AgentLoopCompletionHook
    → trace_completed Signal
    → SedimentationCircuit（六元管道，与 agent_loop 并行）
        → 读原始 trace（intent_steps + messages）
        → 按 action_class 分道提取 Format + Node（软/硬）
        → QualityValidatorNode（prompt 质量验证）
        → PipelineIntegrityNode（连续性检测 + 断层补足）
        → 写入 semantic_network.db
    → SedimentationCompletionHook（含等价性评分 equivalence_score）
```

## 关键约束

- 沉淀回路**不阻塞** agent_loop，异步并行启动
- agent_loop 本身**不需要预处理**信息给沉淀回路，由回路自行从原始 trace 提取
- 只提取 **Format + Node**，不提取 Hook 和 Intent（见 02-circuit-design.md §1）
- 质量是核心关注点，宁可少产出，不产出错误节点
```

## Plan files

- `docs/plans/[2026-03-27]TRACE-SEDIMENTATION-REDESIGN/01-diagnosis.md`
- `docs/plans/[2026-03-27]TRACE-SEDIMENTATION-REDESIGN/02-circuit-design.md`
- `docs/plans/[2026-03-27]TRACE-SEDIMENTATION-REDESIGN/03-extraction-nodes.md`
- `docs/plans/[2026-03-27]TRACE-SEDIMENTATION-REDESIGN/04-phases.md`
- `docs/plans/[2026-03-27]TRACE-SEDIMENTATION-REDESIGN/05-pipeline-integrity.md`
- `docs/plans/[2026-03-27]TRACE-SEDIMENTATION-REDESIGN/README.md`

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
