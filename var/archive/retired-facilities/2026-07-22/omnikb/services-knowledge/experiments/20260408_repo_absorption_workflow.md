# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:24Z
---
omnikb_type: kexp
id: kb.experiment.20260408_repo_absorption_workflow
name: Repo Absorption Workflow — 模仿 / 抄写 / 反省 / 利用 / 吸纳
tags:
- topic.plan
- date.2026-04-08
maturity: draft
summary: '> 用一条 Omnicompany 管线自动化"读外部 GitHub 仓库 → 把里面好的东西变成本地 SOTA"的过程。 > 第一批吸纳目标:
  **codex / gemini-cli / openclaw** 等 Agent 框架。'
date_started: '2026-04-08'
method_summary: see docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/
status: documented
followups:
- docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/01_PRIOR_ART_AND_LANDSCAPE.md
- docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/02_FIVE_PHASE_PHILOSOPHY.md
- docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/03_NODE_BREAKDOWN.md
- docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/04_RISKS_AND_GUARDRAILS.md
- docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/05_PILOT_TARGETS.md
- docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/06_AGENT_LOOP_PLACEMENT.md
- docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/07_STAGED_ROADMAP.md
- docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/README.md
---

# Repo Absorption Workflow — 模仿 / 抄写 / 反省 / 利用 / 吸纳

> Plan directory: `docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
# Repo Absorption Workflow — 模仿 / 抄写 / 反省 / 利用 / 吸纳

> 用一条 Omnicompany 管线自动化"读外部 GitHub 仓库 → 把里面好的东西变成本地 SOTA"的过程。
> 第一批吸纳目标: **codex / gemini-cli / openclaw** 等 Agent 框架。

---

## 1. 一句话目标

把"人手抄 Claude Code"那种工作（见 `docs/plans/claude code学习/`）做成一条**可复用、可观测、可审计的 LAP 管线**，让 Omnicompany 能持续地从外部项目中吸纳能力，但**不污染**自己的纯六元架构。

## 2. 双 Profile — 同一管线两种用法

| Profile | 目标 | 退出标准 |
|---|---|---|
| **A. AI-Framework Absorption** (本批 codex / gemini-cli / openclaw) | 补齐 Omnicompany 框架短板 | 新能力以 Hook/Tool/Node/Format/Signal/Intent 之一进入 `src/omnicompany/` 或 `packages/`，PipelineChecker + LAP Auditor + Guardian 三审通过 |
| **B. Domain-Knowledge Absorption** (后续: 游戏 AI、配表、叙事…) | 在该领域先到达"别人的最好水平" | 该领域至少一条 baseline 管线能跑通 + 历史 GT 复现 PASS |

两种 Profile **共享 Phase A~D 节点**，在 Phase E（吸纳）才走不同的目标分支。

## 3. 五阶段哲学

1. **模仿 (Survey)** — 选仓库、扫架构、找地标
2. **抄写 (Transcribe)** — 在隔离区原样落地，记血统、查 license
3. **反省 (Reflect)** — 抽概念、对比已有、判 Hook/Tool/Node/Format/Signal/Intent 归属、判定补短板还是冲 SOTA
4. **利用 (Pilot)** — 写最薄的 adapter，在真实场景跑一次，确认价值
5. **吸纳 (Internalize)** — 用 lang_rewrite + workflow_factory 的方式重写为纯 LAP 代码，过 LAP Auditor，删隔离区，沉淀血统

详见 `02_FIVE_PHASE_PHILOSOPHY.md`。

## 4. 关键数字

| 维度 | 估算 |
|---|---|
| 单一职责节点总数 | **~22 个**（A:4 / B:3 / C:5 + 人工门 / D:3 / E:5 + 横切:2） |
| 主流程 happy-path 步数（管线 step） | **18~25 步**（含 1 个人工 gate + 2 类 retry 回路） |
| Format 数量 | **~16 个**（每相邻节点之间至少一个有调试价值的中间产物） |
| 反馈回路 | **4 条**（C 反推 A 重选；E 失败回 D 改 adapter；E 失败回 C 重判归属；Guardian 回归回 E 再纯化） |
```

## Plan files

- `docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/01_PRIOR_ART_AND_LANDSCAPE.md`
- `docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/02_FIVE_PHASE_PHILOSOPHY.md`
- `docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/03_NODE_BREAKDOWN.md`
- `docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/04_RISKS_AND_GUARDRAILS.md`
- `docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/05_PILOT_TARGETS.md`
- `docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/06_AGENT_LOOP_PLACEMENT.md`
- `docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/07_STAGED_ROADMAP.md`
- `docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/README.md`

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
