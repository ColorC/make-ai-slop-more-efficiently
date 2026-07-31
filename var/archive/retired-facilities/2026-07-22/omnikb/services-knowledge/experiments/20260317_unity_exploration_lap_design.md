# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:20Z
---
omnikb_type: kexp
id: kb.experiment.20260317_unity_exploration_lap_design
name: 开放式Unity游戏探索 LAP 节点与语义类型设计计划
tags:
- topic.plan
- date.2026-03-17
maturity: draft
summary: '> 项目背景：demogame (AFK Journey) Unity 工程，基于完全体灰盒权限（视窗+代码+控制台）的自主探索QA Agent。 >
  核心挑战：在没有具体外部人工数据集的情况下，通过内生奖励函数驱动，利用 LAP 架构让 Agent 从“盲目 UI 漫游”进化为“具备开发者视角的高级深度测试”。'
date_started: '2026-03-17'
method_summary: see docs/plans/[2026-03-17]UNITY-EXPLORATION-LAP-DESIGN/
status: documented
followups:
- docs/plans/[2026-03-17]UNITY-EXPLORATION-LAP-DESIGN/EVOLUTION_LOGIC_PROOF.md
- docs/plans/[2026-03-17]UNITY-EXPLORATION-LAP-DESIGN/README.md
---

# 开放式Unity游戏探索 LAP 节点与语义类型设计计划

> Plan directory: `docs/plans/[2026-03-17]UNITY-EXPLORATION-LAP-DESIGN/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
# 开放式Unity游戏探索 LAP 节点与语义类型设计计划

> 项目背景：demogame (AFK Journey) Unity 工程，基于完全体灰盒权限（视窗+代码+控制台）的自主探索QA Agent。
> 核心挑战：在没有具体外部人工数据集的情况下，通过内生奖励函数驱动，利用 LAP 架构让 Agent 从“盲目 UI 漫游”进化为“具备开发者视角的高级深度测试”。

## 一、 顶层领域定义 (Parent Domain ℱ)

所有的子类型和节点都应继承自一个共同的上下文定义：**`ℱ_demogame_Context`**。
这是一个全局的语义背景板，包含了游戏的全局约束和物理法则，所有在这个领域内的 Agent 必须默认理解这些法则：
- **架构约束**：Lua 端 MVC 架构（View -> Model -> Proxy）。
- **文件法则**：Lua 源码路径 (`Client/Binary/src`)，C# 源码路径 (`Client/Assets/Script`)。
- **交互法则**：ED 网络框架（`ed.Rpc:Send`），UI 入口生命周期 (`ed.EntranceManager` 和 `ed.ViewManager`)。

## 二、 语义类型精确定义 (Semantic Formats ℱ)

为了让系统从大段不可控的文本交互，转化为结构化的、可进化的 LAP 管线，我们需要精确定义流水线流转的数据类型。

### 1. ℱ_Joint_Perception (联合感知状态)
单纯的截图和 UI 树不足以作为灰盒 QA 的输入，必须是**视界 + 代码界**的联合。
- **`ℱ_UI_State`**: 视觉特征。包含当前界面的名、层级结构(Top-roots)、可见文本、截图。
- **`ℱ_Code_Insight`**: 代码特征。与当前 UI 名匹配的 Lua View 脚本摘要、相关配置表（Excel/CSV）键值对。
- **`ℱ_State_Delta`**: 状态增量。与上一步行动相比，新增了什么（比如抛出了一个 Error，弹出了一个浮层，或者货币/等级发生了变化）。

### 2. ℱ_Exploration_Intent (探索意图)
Agent 内部推演出来的下一步目标。它不再是底层的“点击(x,y)”，而是高语义的目标。
- **`Intent_Wander` (广度漫游)**: 寻找、记录未知的 UI 界面和状态机连线。
- **`Intent_Breakthrough` (深度突破)**: 当遇到阻碍（如按钮置灰、未达条件）时，目标转为“寻找代码约束并用 GM 命令突破”。
- **`Intent_Verify_Loop` (机制验证)**: 尝试跑通一个完整的玩法闭环（如完整打一次关卡，完成一次抽卡结算）。

### 3. ℱ_Action_Primitive (动作原语)
Agent 输出的结构化、可被环境精确执行的动作。
- **视觉动作**: `ui_click`, `navigate_to`, `go_back`
- **代码探针**: `search_source`, `read_lua_source`, `list_source_files`
- **神级干预**: `execute_lua` (GM命令、数值修改、强行改变客户端状态)

### 4. ℱ_Exploration_Reward (内驱力奖励评估)
定义探索好坏的绝对标尺（**这是无标注数据集时的
```

## Plan files

- `docs/plans/[2026-03-17]UNITY-EXPLORATION-LAP-DESIGN/EVOLUTION_LOGIC_PROOF.md`
- `docs/plans/[2026-03-17]UNITY-EXPLORATION-LAP-DESIGN/README.md`

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
