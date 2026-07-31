# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:21Z
---
omnikb_type: kexp
id: kb.experiment.20260331_progress_report_for_team
name: Omnicompany 项目进度分享
tags:
- topic.plan
- date.2026-03-31
maturity: draft
summary: '**日期**: 2026-03-31 **定位**: 跨部门分享 — 面向不了解本项目（甚至不了解 Agent 框架）的同事 **核心**: 过去三周，我尝试让
  AI 在没有人工干预的情况下自动变得更聪明。以下是我做了什么、遇到什么样的核心理论瓶颈、以及接下来我们在 AGI 前沿探索上打算怎么走。'
date_started: '2026-03-31'
method_summary: see docs/plans/[2026-03-31]PROGRESS-REPORT-FOR-TEAM/
status: documented
followups:
- docs/plans/[2026-03-31]PROGRESS-REPORT-FOR-TEAM/README.md
---

# Omnicompany 项目进度分享

> Plan directory: `docs/plans/[2026-03-31]PROGRESS-REPORT-FOR-TEAM/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
# Omnicompany 项目进度分享

**日期**: 2026-03-31  
**定位**: 跨部门分享 — 面向不了解本项目（甚至不了解 Agent 框架）的同事  
**核心**: 过去三周，我尝试让 AI 在没有人工干预的情况下自动变得更聪明。以下是我做了什么、遇到什么样的核心理论瓶颈、以及接下来我们在 AGI 前沿探索上打算怎么走。

---

## 〇、先从一个类比说起

想象你在管理一家工厂。工厂里有很多工人（AI Agent），每个工人擅长一件事——有人擅长读文件，有人擅长写代码，有人擅长跑测试。

当一个新任务进来（比如"修复登录页面的 bug"），你需要决定：
1. **让谁来干**（路由问题）
2. **用什么流程干**（编排问题）
3. **干完之后怎么知道干得好不好**（反馈问题）
4. **怎么让下次干得更好**（学习/进化问题）

目前市面上的 AI 代理框架（如 AutoGPT、LangChain、CrewAI）主要解决前两个问题，也就是"让 AI 按照人写好的流程走"。**Omnicompany 想解决全部四个问题**——尤其是第 3 和第 4 个：让系统自动发现自己哪里做得不好，然后自动变得更好。

最终极的目标是：系统做了足够多的任务之后，能自动发现"原来这类任务有一种通用模式"，把这个模式记下来，下次遇到类似的事情就直接用（这就是我们说的"概念涌现"，后面会详细讲）。

---

## 一、项目整体架构：走向统一的"六元语义"模型

在 3 月 28 日对系统底层的重新梳理中，我们总结出了一个基础理论模型。我们观察到，包括 Omnicompany 在内，目前市面上的主流 Agent 框架（如 MetaGPT、CrewAI、Letta 等），在剥离具体代码实现后，其核心运作逻辑可以被抽象为统一的 **六个基本原语（六元语义架构）**。这也是本项目的底层逻辑定义：

1. **Format（概念/类型）**：规定信息的数据结构与语义，例如"这是一段特定约束下的 Python 代码"。
2. **Signal（信号）**：节点间流通的真实数据载体，要求必须是自然语言可读的具体描述。
3. **Hook（感官）**：负责感知外部环境或内部状态，并在满足触发条件时主动发出 Signal。
4. **Node（处理节点）**：接收 Signal，调用大模型进行分析评估，输出新的 Signal 或行为指令。
5. **Tool（执行工具）**：执行具体的确定性操作（如读写文件、终端命令），自身不具备主动推理能力。
6. **Intent（意志/意图）**：一种特殊的抽象 Signal，仅声明"期望获得的输出 Format"，将具体实施路径交予路由网络。

基于这套六元模型，Agent 系统从紧耦合的过程式代码，转变为一个通过语义信号相互驱动的模块化体系。

在这个模型之上，我们具体的运行时设施如下：

### 1.1 基层：事件总线（信息高速公路）

所有东西的基础是一条"事件总线"。你可以把它想象成一条信息高速公路——AI Agent 做的每一步操作（打开文件、调用 LLM、写代码、跑测试……）都会在这条总线上发一条"事件消息"。

这些消息被持久化存储在一个 SQLite 数据库里（轻量级数据库，不需要额外安装任何服务），所以我们可以事后回放"这个任务当时到底是怎么执行的"。

**已实现的事件类型有 16 种**，覆盖 Agent 的完整生命周期：

```
任务开始 → LLM 请求 → LLM 回复 →
```

## Plan files

- `docs/plans/[2026-03-31]PROGRESS-REPORT-FOR-TEAM/README.md`

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
