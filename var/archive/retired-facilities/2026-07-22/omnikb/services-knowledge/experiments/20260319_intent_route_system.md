# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:20Z
---
omnikb_type: kexp
id: kb.experiment.20260319_intent_route_system
name: Intent Route System (IRS) — 文档索引
tags:
- topic.plan
- date.2026-03-19
maturity: draft
summary: '> 从 Agent 工具调用轨迹中自动生长语义路由图，实现跨任务知识沉淀与路径复用。 > 对应终极愿景：将大量 token 用于学习沉淀，承担长期稳定任务并内化知识图谱。'
date_started: '2026-03-19'
method_summary: see docs/plans/[2026-03-19]INTENT-ROUTE-SYSTEM/
status: documented
followups:
- docs/plans/[2026-03-19]INTENT-ROUTE-SYSTEM/DESIGN_SPEC.md
- docs/plans/[2026-03-19]INTENT-ROUTE-SYSTEM/README.md
- docs/plans/[2026-03-19]INTENT-ROUTE-SYSTEM/ROADMAP.md
---

# Intent Route System (IRS) — 文档索引

> Plan directory: `docs/plans/[2026-03-19]INTENT-ROUTE-SYSTEM/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
# Intent Route System (IRS) — 文档索引

> 从 Agent 工具调用轨迹中自动生长语义路由图，实现跨任务知识沉淀与路径复用。
> 对应终极愿景：将大量 token 用于学习沉淀，承担长期稳定任务并内化知识图谱。

## 文档目录

| 文档 | 内容 | 状态 |
|------|------|------|
| DESIGN_SPEC.md | 核心设计规格：意图字段、图结构、聚类机制、借鉴项目对位 | Draft |
| ROADMAP.md | 四阶段路线图：V1 轨迹采集验证 → V4 无监督探索 | Draft |

## 核心思想（一句话）

Agent 每次工具调用时**多输出一个意图字段**（input_type, output_type, desc），
运行结束后对轨迹做 embedding 聚类，相似意图合并成持久化路由节点，
下次同类任务直接走已知路由，越跑越快、越跑越稳。

## 与已有工程的关系

```
omnicompany/
  src/omnicompany/
    runtime/agent_loop.py    ← 注入意图字段的改造入口
    bus/sqlite.py            ← 轨迹持久化存储
    evolution/               ← V3+ 结晶合并逻辑复用
  scripts/
    feishu_oauth.py          ← V1 真相桩（Feishu API）
    evolution_lab/           ← 验证框架参考
  data/
    format_registry.json     ← 语义类型注册表
```

## 前置依赖

- LAP V0.2 Specification
- OmniCompany Architecture
- 相关调研：已有Agent学习架构调研
- 实验结论：信息完备环境的边界分析
```

## Plan files

- `docs/plans/[2026-03-19]INTENT-ROUTE-SYSTEM/DESIGN_SPEC.md`
- `docs/plans/[2026-03-19]INTENT-ROUTE-SYSTEM/README.md`
- `docs/plans/[2026-03-19]INTENT-ROUTE-SYSTEM/ROADMAP.md`

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
