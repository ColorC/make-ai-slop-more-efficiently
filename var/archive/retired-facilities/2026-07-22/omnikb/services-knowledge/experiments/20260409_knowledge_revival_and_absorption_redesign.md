# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:24Z
---
omnikb_type: kexp
id: kb.experiment.20260409_knowledge_revival_and_absorption_redesign
name: Knowledge Revival + Absorption Redesign
tags:
- topic.plan
- date.2026-04-09
maturity: draft
summary: '> 2026-04-09 · 复活 OmniKB 作为 Omnicompany 自知与跨管线知识沉淀的统一基座, 并在它之上重构 repo absorption
  的工作流。'
date_started: '2026-04-09'
method_summary: see docs/plans/[2026-04-09]KNOWLEDGE-REVIVAL-AND-ABSORPTION-REDESIGN/
status: documented
followups:
- docs/plans/[2026-04-09]KNOWLEDGE-REVIVAL-AND-ABSORPTION-REDESIGN/01-OMNIKB-REVIVAL-PLAN.md
- docs/plans/[2026-04-09]KNOWLEDGE-REVIVAL-AND-ABSORPTION-REDESIGN/02-ABSORPTION-V2-REDESIGN.md
- docs/plans/[2026-04-09]KNOWLEDGE-REVIVAL-AND-ABSORPTION-REDESIGN/03-STAGED-ROLLOUT.md
- docs/plans/[2026-04-09]KNOWLEDGE-REVIVAL-AND-ABSORPTION-REDESIGN/README.md
---

# Knowledge Revival + Absorption Redesign

> Plan directory: `docs/plans/[2026-04-09]KNOWLEDGE-REVIVAL-AND-ABSORPTION-REDESIGN/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
# Knowledge Revival + Absorption Redesign

> 2026-04-09 · 复活 OmniKB 作为 Omnicompany 自知与跨管线知识沉淀的统一基座, 并在它之上重构 repo absorption 的工作流。

## 背景

对话链路:

1. 2026-04-08 完成了 `packages/services/absorption/` 的 Stage 3d 升级,
   把 LandmarkPicker 做成 AgentNodeLoop, 引入 snapshot.py 扫本仓 + gh_tree_list /
   gh_file_read 工具集, 跑通了 codex 和 gemini-cli 两个真实样本, 产出了 markdown 报告。
2. 用户批评四点:
   (a) 陋习——置信度/标签/分数等 cargo-culted 评级必须清除,
   (b) 深度不够——关键文件 10% 阅读量都达不到,
   (c) 没有持久化的 "已经扫过什么" 记录, 学习深度缺旋钮,
   (d) **针对特定仓库的结构做了隐性优化**的嫌疑,
   (e) Omnicompany 自己的架构/历史/决策应该有 router-as-storage
      的明文 markdown wiki 作为全仓共享基座,
   (f) triage_gate / coverage_auditor 和 landmark_picker 是单向审判而非环,
      证据评估应该反哺 picker。
3. 我在上一轮提出"新建 omni_wiki 子系统"—— **这是错的**。
   因为 `src/omnicompany/_graveyard/knowledge/` 下有 **OmniKB**,
   它的 `_RETIRED.md` 写着 "designed and implemented but never wired into
   any live code path. Zero live callers", 于 2026-04-07 刚被移到 graveyard。
   用户的即时反应是 "不要生出旁支, 如果重合大, 请升级已有的"。
4. OmniKB **正是**用户说的那个基建, 而且 narrative plan
   `[2026-04-07]NARRATIVE-CREATION-ENGINE/05-omnicompany-gaps.md` 在 OmniKB 被
   retire 前一天刚写 "KFormat(OmniKB) 直接复用", 所以它的复活对 narrative 也有
   直接价值——这不是 absorption 独有的诉求。

本计划的任务是:

- **复活** OmniKB, 从 `_graveyard` 拉回 `src/omnicompany/packages/services/knowledge/`,
- **升级** 它的数据模型, 加上 `KDecision / KExperiment / KRepoArchitect` 三种新条目
  (设计 ADR / 实验记录 / 外部仓画像), 让它能承担"自修改所需的全部背景知识",
- **用 Router 暴露接口**, 让它正式成为 LAP 中的六元合规基建,
  不再是一堆孤立 Python 函数,
- **重构 absorption** 管线以依赖新的 knowledge 基建, 顺便解决 a/b/c
```

## Plan files

- `docs/plans/[2026-04-09]KNOWLEDGE-REVIVAL-AND-ABSORPTION-REDESIGN/01-OMNIKB-REVIVAL-PLAN.md`
- `docs/plans/[2026-04-09]KNOWLEDGE-REVIVAL-AND-ABSORPTION-REDESIGN/02-ABSORPTION-V2-REDESIGN.md`
- `docs/plans/[2026-04-09]KNOWLEDGE-REVIVAL-AND-ABSORPTION-REDESIGN/03-STAGED-ROLLOUT.md`
- `docs/plans/[2026-04-09]KNOWLEDGE-REVIVAL-AND-ABSORPTION-REDESIGN/README.md`

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
