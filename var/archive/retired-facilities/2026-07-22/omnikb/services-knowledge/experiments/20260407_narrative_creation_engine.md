# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:24Z
---
omnikb_type: kexp
id: kb.experiment.20260407_narrative_creation_engine
name: 创作引擎方向:叙事创作能力的架构探索
tags:
- topic.plan
- date.2026-04-07
maturity: draft
summary: '**日期**:2026-04-07 **状态**:愿景期 / 架构草拟 **性质**:对 Omnicompany 跨界进入"虚拟设定集创作"领域的可行性与架构草图'
date_started: '2026-04-07'
method_summary: see docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/
status: documented
followups:
- docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/00-context-and-scope.md
- docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/01-basic-assumptions.md
- docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/02-industry-landscape.md
- docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/03-architecture-three-layer.md
- docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/04-north-star-projects.md
- docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/05-omnicompany-gaps.md
- docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/06-phased-roadmap.md
- docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/README.md
- docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/REQUIREMENTS-AND-INITIAL-ANALYSIS.md
- docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/WHAT-WE-BUILT.md
---

# 创作引擎方向:叙事创作能力的架构探索

> Plan directory: `docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
# 创作引擎方向:叙事创作能力的架构探索

**日期**:2026-04-07
**状态**:愿景期 / 架构草拟
**性质**:对 Omnicompany 跨界进入"虚拟设定集创作"领域的可行性与架构草图

## 文件索引

| 文件 | 内容 | 成熟度 |
|---|---|---|
| 00-context-and-scope.md | 触发背景、讨论范围、对话来源 | 定稿 |
| 01-basic-assumptions.md | **基本假设**(需要验证) | **待验证** |
| 02-industry-landscape.md | 业界 AI 创作技术地貌(Smallville/Story2Game/WhatIF/Inworld/Façade 等) | 定稿 |
| 03-architecture-three-layer.md | 三层架构(Simulator / Narrative Driver / Knowledge Base)+ LOD 旋钮 | 草稿 |
| 04-north-star-projects.md | 两颗北极星:交互 VN + MC 全宇宙文明模拟 | 草稿 |
| 05-omnicompany-gaps.md | Omnicompany 需要新增的能力清单 | 草稿 |
| 06-phased-roadmap.md | 三阶段推进路线 | 草稿 |

## 核心判断

1. **业界 AI 创作的共同范式**:结构化记忆(Codex/Story Bible) + 分层大纲 + 自动事实抽取 + 一致性校验闭环 + 参考文本驱动风格 + 代理式动态记忆。没有神秘技术,只是工程化程度差异。

2. **Omnicompany 的架构天然契合度高**:Format + StateAnchor + PipelineSpec + SQLiteBus + OmniKB 这套骨架,正好就是"活设定集 + 一致性守护 + 渐进固化知识"所需要的基础设施。

3. **两颗北极星构成同一架构的两个极端**:
   - 项目 1(交互 VN)- **情节驱动 × 深度冰山 × 密集语义**
   - 项目 2(MC 文明)- **规则驱动 × LOD 尺度 × 涌现叙事**
   - 中间的"尺度滑杆"才是真正的通用性所在

4. **核心缺失能力**:Disclosure Control(真相访问控制)、Focus Cursor + Hydration(LOD 补水/脱水)、Playtest Loop(自动评测)。详见 05-omnicompany-gaps.md。

5. **关键架构原则**:**LLM 负责语言外壳和局部推理,客观引擎负责真相的唯一副本**。绝不让 LLM 作为"唯一真相源"。任何需要跨 24 章仍要记住的事实,都必须落地到外部存储。

## 待确认(下一步)

- [ ] 基本假设(01 文件)逐条验证
- [ ] 最小切片设计:VN 项目的 "1 NPC ×
```

## Plan files

- `docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/00-context-and-scope.md`
- `docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/01-basic-assumptions.md`
- `docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/02-industry-landscape.md`
- `docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/03-architecture-three-layer.md`
- `docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/04-north-star-projects.md`
- `docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/05-omnicompany-gaps.md`
- `docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/06-phased-roadmap.md`
- `docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/README.md`
- `docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/REQUIREMENTS-AND-INITIAL-ANALYSIS.md`
- `docs/plans/[2026-04-07]NARRATIVE-CREATION-ENGINE/WHAT-WE-BUILT.md`

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
