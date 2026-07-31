# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:20Z
---
omnikb_type: kexp
id: kb.experiment.20260323_semantic_network_redesign
name: 语义网络重新设计计划 v2
tags:
- topic.plan
- date.2026-03-23
maturity: draft
summary: '**日期**: 2026-03-23 **状态**: 架构草案（需实验验证后再实施） **背景**: Marathon 停止运行后，对现有实现做全面回顾，发现当前架构存在根本性设计偏差，需要重新规划后再重启。'
date_started: '2026-03-23'
method_summary: see docs/plans/[2026-03-23]SEMANTIC-NETWORK-REDESIGN/
status: documented
followups:
- docs/plans/[2026-03-23]SEMANTIC-NETWORK-REDESIGN/IMPLEMENTATION_PLAN.md
- docs/plans/[2026-03-23]SEMANTIC-NETWORK-REDESIGN/README.md
---

# 语义网络重新设计计划 v2

> Plan directory: `docs/plans/[2026-03-23]SEMANTIC-NETWORK-REDESIGN/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
# 语义网络重新设计计划 v2

**日期**: 2026-03-23
**状态**: 架构草案（需实验验证后再实施）
**背景**: Marathon 停止运行后，对现有实现做全面回顾，发现当前架构存在根本性设计偏差，需要重新规划后再重启。

---

## 目录

1. [架构修正：我们之前做错了什么](#一架构修正)
2. [统一节点模型](#二统一节点模型)
3. [语义类型系统设计（TA 框架）](#三语义类型系统设计)
4. [路由机制重新设计](#四路由机制重新设计)
5. [健康维护机制](#五健康维护机制)
6. [实验验证计划](#六实验验证计划)
7. [当前数据快照与问题清单](#七当前数据快照与问题清单)

---

## 一、架构修正

### 1.1 当前实现的根本错误

**错误1：节点类型双轨制**

当前代码存在两套并行且互不相通的节点体系：
- `route_nodes` — 从 agent_loop 工具调用自动提取的执行记录
- `semantic_nodes` — 手工/进化注册的"软节点"，有 processing_prompt

这个分法是错的。**工具调用节点本身也是语义节点**——它们是已经完全固化（crystallized）的硬节点。bash 执行文件，read file，write file，这些操作的内容不会出错，问题只在于输入是否正确（输入什么路径、什么命令）。

```
正确的统一模型：
所有节点 = 语义节点（SemanticNode）
            │
            ├── 软节点（soft）: 由 LLM 执行，有 processing_prompt
            │       └── maturity: hypothetical → growing → mature
            │
            └── 硬节点（hard/crystallized）: 确定性代码执行
                    ├── 内置硬节点: bash, read_file, write_file, think ...
                    └── 进化固化节点: 从 mature 软节点结晶而来
```

**错误2：路由必须精确匹配 desired_output_types**

当前逻辑：如果没有节点输出恰好是 `fs.path.python_file`，就返回 FAIL，交给 agent_loop。

这是错的。语义路由的价值在于**即使没有完全匹配的路径，也能找到信息完备度最高的近似路径**，并识别出信息缺口，让 agent 用最少的探索补全缺失部分。

**错误3：agent_loop 是 fallback（末端）**

当前逻辑：语义路由失败 → 进 agent_loop（LLM 自由探索）。

正确逻辑：**agent_loop 应该和语义路由并行启动**，不等语义路由结论就开始工作，除非整条语义通路都是 mature/crystallized（此时可以跳过 agent_loop 直达）。这样用户体验不受影响，语义路由只是在后台更新知识和优化未来路径。

**错误4：关键词匹配作为主分类手段**

807 种类型 × 关键词匹配 = 几乎完全随机。关键词没有语义，`"python file"` 会同时命中 `fs.path.python_file`、`python.code.source`、`bash.st
```

## Plan files

- `docs/plans/[2026-03-23]SEMANTIC-NETWORK-REDESIGN/IMPLEMENTATION_PLAN.md`
- `docs/plans/[2026-03-23]SEMANTIC-NETWORK-REDESIGN/README.md`

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
