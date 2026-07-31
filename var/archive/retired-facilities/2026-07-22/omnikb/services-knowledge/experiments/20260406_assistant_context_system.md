# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:23Z
---
omnikb_type: kexp
id: kb.experiment.20260406_assistant_context_system
name: Assistant 上下文管理系统
tags:
- topic.plan
- date.2026-04-06
maturity: draft
summary: '> **日期**: 2026-04-06 > **状态**: 设计稿 > **核心**: IDEAgentLoop（已完成）的上下文与工作模式管理层
  > **设计哲学**: 完全对齐 Claude Code — 简洁、高效、可靠'
date_started: '2026-04-06'
method_summary: see docs/plans/[2026-04-06]ASSISTANT-CONTEXT-SYSTEM/
status: documented
followups:
- docs/plans/[2026-04-06]ASSISTANT-CONTEXT-SYSTEM/02_CORRECTIONS.md
- docs/plans/[2026-04-06]ASSISTANT-CONTEXT-SYSTEM/progress.md
- docs/plans/[2026-04-06]ASSISTANT-CONTEXT-SYSTEM/README.md
---

# Assistant 上下文管理系统

> Plan directory: `docs/plans/[2026-04-06]ASSISTANT-CONTEXT-SYSTEM/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
# Assistant 上下文管理系统

> **日期**: 2026-04-06
> **状态**: 设计稿
> **核心**: IDEAgentLoop（已完成）的上下文与工作模式管理层
> **设计哲学**: 完全对齐 Claude Code — 简洁、高效、可靠

---

## 0. 背景与定位

IDEAgentLoop（claude-sonnet-4-6）已经完成，它是 assistant 的执行引擎。

本系统要做的是"执行引擎周围的一切"——它需要知道什么、怎么工作、遇到什么情况做什么。

**与普通管线节点的根本区别**：

| 维度 | 普通管线节点 | Assistant |
|------|------------|-----------|
| 输入 | 严格 Format，语义明确 | 用户的任意自然语言，超高不确定性 |
| 约束 | 职责单一，不可越界 | 几乎无限制，用户说什么就做什么 |
| 上下文 | 当前 Format data | 历史、目标、计划、规则、工具全集 |
| 终止条件 | Verdict 明确 | 用户满意 |

因此 assistant 的"管理层"不能是一套僵硬的流程，而是一套**按需加载的动态上下文系统**。

---

## 1. 总体结构

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Assistant System Prompt                         │
│                                                                     │
│  §0 Core Identity & Capabilities                                    │
│  §1 Workspace Index     ← [W] WorkspaceRouter 展开                  │
│  §2 Active Goals        ← [G] 当前 active goals 摘要                │
│  §3 Active Plan         ← [P] 当前计划标题+阶段（不展开全文）         │
│  §4 Open Todos          ← [T] TodoWrite 状态                        │
│  §5 Context Rules       ← [E3] 适用于当前意图的 Rules               │
│  §6 Available Help      ← [E1][E2] 可用 Knowledge/Skill 列表        │
│                                                                     │
│  ↓ compact 时追加                                                   │
│  §7 Session Summary     ← [H] 上次 compact 的归档摘要               │
└───────────────────────────────────────
```

## Plan files

- `docs/plans/[2026-04-06]ASSISTANT-CONTEXT-SYSTEM/02_CORRECTIONS.md`
- `docs/plans/[2026-04-06]ASSISTANT-CONTEXT-SYSTEM/progress.md`
- `docs/plans/[2026-04-06]ASSISTANT-CONTEXT-SYSTEM/README.md`

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
