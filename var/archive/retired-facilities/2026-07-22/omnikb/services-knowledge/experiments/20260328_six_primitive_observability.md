# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:21Z
---
omnikb_type: kexp
id: kb.experiment.20260328_six_primitive_observability
name: 六元可观测系统设计计划
tags:
- topic.plan
- date.2026-03-28
maturity: draft
summary: '> **创建时间**：2026-03-28 > **目标**：把"系统在跑什么"这个问题变成可以直接回答的问题，而不是看一堆指标猜 > **核心问题**：谁做了什么工作？结果如何？为什么做这个工作？做的好不好？
  > **技术路线**：CLI 数据层（查询/筛选/导出）+ React 前端（流程可视化 + 列表视图）'
date_started: '2026-03-28'
method_summary: see docs/plans/[2026-03-28]SIX-PRIMITIVE-OBSERVABILITY/
status: documented
followups:
- docs/plans/[2026-03-28]SIX-PRIMITIVE-OBSERVABILITY/[2026-03-28]ROUTING-RELIABILITY-BACKLOG.md
- docs/plans/[2026-03-28]SIX-PRIMITIVE-OBSERVABILITY/[2026-03-28]ROUTING-SYSTEM-DIAGNOSIS.md
- docs/plans/[2026-03-28]SIX-PRIMITIVE-OBSERVABILITY/README.md
---

# 六元可观测系统设计计划

> Plan directory: `docs/plans/[2026-03-28]SIX-PRIMITIVE-OBSERVABILITY/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
# 六元可观测系统设计计划

> **创建时间**：2026-03-28
> **目标**：把"系统在跑什么"这个问题变成可以直接回答的问题，而不是看一堆指标猜
> **核心问题**：谁做了什么工作？结果如何？为什么做这个工作？做的好不好？
> **技术路线**：CLI 数据层（查询/筛选/导出）+ React 前端（流程可视化 + 列表视图）

---

## 问题陈述

当前监控设施的问题不是"没有数据"，而是**数据以指标形式呈现，丢失了具体性**：

| 当前看到的 | 实际想知道的 |
|-----------|-------------|
| 路由命中率 73% | 第 47 轮，哪个 Intent 没找到节点？输入是什么？ |
| pain_score avg 0.42 | 哪个节点在哪个任务上报错了？错误文本是什么？ |
| 进化触发 3 次 | 每次进化因何触发？delta 是什么？有没有生效？ |
| repair_queue: 12 pending | 哪个节点在修复？是同一个节点反复失败吗？ |

**根本差异**：指标回答"多少"，具体可观测性回答"什么"和"为什么"。

---

## 核心回答框架：五个问题

系统的每次执行（一个 `task_id` / 一轮 `round_num`）应该能直接回答：

1. **什么触发了这次工作？** → Hook（PeriodicHook 定时 / EventHook 事件）
2. **系统打算做什么？** → Intent（input_format → output_format，由哪个 ConsciousnessNode 产生）
3. **实际经过了哪些步骤？** → Signal 流（每跳：节点、输入文本、输出文本）
4. **环闭合了吗？** → CompletionHook 是否观测到匹配的 output_format
5. **做的好不好？为什么？** → 痛觉、verdict、evolution delta、下一次是否改进

---

## 数据层设计（CLI）

### 六元作为查询维度

六元语义模型的每个原语对应一类可查询实体：

| 原语 | 查询目标 | 核心字段 |
|------|---------|---------|
| **Hook** | 什么触发了这轮 | hook_type, fired_at, signal_format |
| **Signal** | 每一跳传递了什么 | format, text_preview, node_id, task_id |
| **Format** | 语义类型是什么 | format_id, description, examples |
| **Node** | 哪个节点处理了它 | node_id, source_channel, maturity, processing_prompt |
| **Tool** | 调用了什么工具 | tool_name, input_summary, output_summary, success |
| **Intent** | 目标是什么，完成了吗 | input_format, output_format, completion_observed, verdict |

### CLI 命令设计

```bash
# ─── 执行历史 ────────────────────────────────────────────
omni trace <task
```

## Plan files

- `docs/plans/[2026-03-28]SIX-PRIMITIVE-OBSERVABILITY/[2026-03-28]ROUTING-RELIABILITY-BACKLOG.md`
- `docs/plans/[2026-03-28]SIX-PRIMITIVE-OBSERVABILITY/[2026-03-28]ROUTING-SYSTEM-DIAGNOSIS.md`
- `docs/plans/[2026-03-28]SIX-PRIMITIVE-OBSERVABILITY/README.md`

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
