# 探索路径可视化 / 决策树自动构建 — 调研

> 调研日期：2026-06-27
> 对应概念笔记（poof note）：`note-edsspn3`
> 说明：这是调研产出，从 poof note 迁出。poof note 只保留用户提出的概念本身。
> 落地计划：`docs/plans/[2026-06-27]EXPLORATION-PATH-VIZ/plan.md`（2026-06-27 立，用户拍板做版本化 + 保持散落根；图=真本体投影）。

## 概念回顾

用户在和 AI 反复迭代时，会发出设计理念、设计思路、指正；AI 会产出多个版本的产物。
目标是把这些放进同一张可视化的网络图里，能看到：不同方向上的不同探索分支、每个方向上不同版本的产物记录、以及"理念 → 指正 → 新版本"的因果链。用途：方便用户回看自己的探索过程做决策，并为"决策树自动构建"提供数据基础。

---

## 一、Prior art：视觉部分早被解决，真正的空白在"带理由的因果边"

### 最接近的现成工具

**LLM 对话树 / 分支工具（视觉上最接近）**
- **Loom**（socketteer/loom；generative.ink 有概念文；商业版 Exoloom）：经典"多元宇宙树"。从一个提示生成多个续写，挑选要继续的分支，长成一棵分支树。节点 = 一次生成 + 元数据（prompt、response、model、logprobs）。视图 = 侧栏导航树 + 可缩放的整树视图。是"不同方向 + 每方向多版本"最纯粹的先例，但它是为文本续写设计的，不是"带指正的产物版本"。
- **tldraw branching-chat 模板**（tldraw/branching-chat-template）：无限画布、节点式对话，任何消息都能 fork，分支按空间排布。是目前最可复用的开源底座（画布、平移缩放、节点渲染、分支 fork 都现成）。
- **Sensecape**（Suh et al., UIST 2023, arXiv 2305.11483）：多层级 LLM 探索与意义建构的学术系统，支持把 LLM 输出做层级组织和空间排布，便于比较候选、回看旧想法。最贴近"回看自己探索过程"这个用途。
- **Nodea / GitChat / BranchGPT**：消费级，都把对话建成一棵消息 DAG，编辑或重新生成就 fork。GitChat 直接借用 git 语义（branch/merge）。证明"消息树"交互已经主流，但它们在对话轮次上分支，不是在产物版本上带指正边。
- **Tree of Thoughts**（Yao et al., NeurIPS 2023, arXiv 2305.10601）：推理搜索版本。每个节点是一个"想法"，模型每步生成多个候选并自评，做 BFS/DFS 搜索带回溯。是"自动决策树构建"的算法骨架——它本质就是在生成的候选上做带价值函数的树搜索。

**提示版本 / 实验追踪工具（数据上最接近）**
- **LangSmith**：Prompt Hub 对提示做类 git 版本管理；trace 捕获 run-tree；数据集驱动评测可比较版本、检测回归。强在"版本 + run-tree"，弱在"探索方向 + 人类指正因果"。
- **PromptLayer**：明确定位"Git for prompts"，可视化提示注册表 + 版本 + 版本间 A/B。
- **Humanloop**：2025 年 9 月已关停，且只做线性版本（无分支/合并）——它的线性模型 + 退场说明市场没服务好真正的"分支式探索"。
- **Langfuse / Braintrust / W&B（weave）**：同一族，版本化提示、实验比较、评分。都不把探索画成网络图，给的是表格、diff、run-tree。

**节点图创作工具**
- **ComfyUI**：算子节点的 DAG，节点间是带类型的"连线"（边 = 数据依赖），序列化成 JSON。它是节点图的"创作"模型不是"历史"模型，但其干净的 JSON-DAG schema 和拓扑排序执行是很好的序列化参考。

**设计迭代历史工具**
- **Figma 分支 + 版本史**：分支 = 平行探索的"另一个现实"，合并回主线生成版本史检查点；分支与主线之间有可视化 diff。设计界证明了"平行的产物版本分支 + 可视 diff + 合并"是有价值且非工程师也能用的。局限：记了"改了什么"但没记"为什么改"。

**设计理由 / 论证（别人都漏掉的"为什么"层）**
- **IBIS**（Kunz & Rittel, 1960s）与 gIBIS / Compendium 工具。三类节点：Issue（问题）、Position（候选答案/方案）、Argument（支持/反对某方案）。这是把决策理由记成图的经典 schema——正是"想法 → 指正 → 决策"的因果模型，只是从没和 LLM 产物版本接起来过。近期有从 issue log 自动挖设计理由的工作（arXiv 2405.19623）。

### 收敛到的数据模型

所有工具共享的抽象是有向无环图（DAG）：
- **节点 = 产物版本**（某时刻生成的文本/设计/提示）。
- **边 = 派生事件**（父 → 子 = "这个版本由那个版本派生"）。

几乎都很薄或缺失的：边只是"derived-from"，没人（除了 IBIS 式理由工具，但它们没接 LLM 产物）把边做成带类型的因果——为什么产生这个子节点。

用户这个想法需要的是 **DAG + IBIS 混合**：

```
Node（产物版本）:
  id, branch_id, parent_ids[], created_at,
  type: {idea | artifact_version | critique | decision},
  content / artifact_ref, model, params, eval_scores{}

Edge（带类型的因果事件）:
  from, to,
  rel: {derived_from | critiques | responds_to_critique |
        refines | rejects | merges | alternative_of},
  rationale: <导致这次改动的那条指正 / 设计理念>
```

这让"想法 → 指正 → 新版本"的因果链成为图里可查询的路径，这是做"自动决策树构建"的前提。

### 可视化方案与取舍

| 方案 | 优势 | 劣势 | 适用 |
|---|---|---|---|
| git-graph / 提交树（分列分支） | 分支身份清晰；像"带 fork 的历史"；熟悉 | 多合并、宽扇出时乱；基本是一维时间轴 | 默认选——数据本身就是"版本随时间分支" |
| 分层 DAG（Dagre / Sugiyama） | 父→子分层清晰；廉价减少交叉 | 一旦加合并就变真 DAG 不再是树 | 以树为主、偶有合并 |
| 力导向 | 密集簇展开好看；看"探索集中在哪" | 布局不确定、无稳定阅读顺序、丢失因果方向 | 概览/聚类视图，不做主因果视图 |
| 带分支的时间轴（泳道） | 加真实钟表时间；看"何时探索哪个方向" | 纵向铺张；多平行分支难 | 时间维度复盘重要时 |
| Sankey | 显示流向/收敛到选中结果 | 暗示了你没有的流量大小；不擅长回溯/环 | 做"哪条分支胜出"的汇总视图 |

**推荐**：git-graph / 分层 DAG 作主视图（因果 + 分支身份是用户要复盘的），可选时间轴叠加，加 Loom 式祖先侧栏（当前节点到根的线性路径）。力导向只做次级的"探索热度"视图。

### 真正难、没人解决好的地方

1. **自动捕获因果边**：工具都记"有了新版本"，不可靠地记"导致它的那条指正"。真实迭代里理由藏在对话散文里（"做暖一点"、"这违反了 X 的设计理念"）。把这些散文变成带结构理由的 `critiques`/`refines` 边，是核心未解的抽取问题。
2. **粒度 / 产物身份**：AI 吐了个半改的草稿、或一次改了三处时，什么算"一个产物""一个版本"？对话树工具用"每条消息一个节点"（粗）回避，Figma 用可视 diff 回避。能 diff 产物并归因"哪条指正导致哪处子改动"的工具会是新东西。
3. **规模 / 可读性**：真实探索铺成上百节点，需要激进的折叠/展开、语义聚类（"这整棵子树是'极简方向'"）、死分支剪枝。
4. **"自动决策树构建"——最强差异点**：一旦边带类型理由，探索 DAG 就成了决策树的结构化数据。Tree-of-Thoughts 给算法框架（节点=状态、分支=选择、价值函数打分），而捕获的人类指正 + 评分正是 ToT 平时要自己生成的价值信号。IBIS 给输出 schema（把杂乱探索图坍缩成 Issue → Positions → Arguments → Decision）。没有现成产品闭合"带理由的分支探索 → 蒸馏出 IBIS 式决策树"这个环——这是空白。

### 最小可行版本建议

- **数据模型**：先一种节点 `artifact_version { id, parents[], content/ref, created_at, eval_score? }`；一种边带 `rel` 枚举 + 自由文本 `rationale`，至少 `{derived_from, critiques, refines, alternative_of}`。`critiques`/`refines` 边上的 `rationale` 是关键，设为必填可人工编辑。序列化成 JSON（参考 ComfyUI 的干净节点/边 JSON）。
- **可视化**：主视图用分层 DAG / git-graph，用 React Flow + Dagre，或想后面做图算法就用 Cytoscape.js；加 Loom 式祖先侧栏；边标签显示指正理由，点边展开全文。
- **可复用 OSS**：tldraw branching-chat-template（最快出 MVP）、React Flow + Dagre、Cytoscape.js（要做决策树挖掘的图查询时）、IBIS/Compendium 作输出 schema 概念、对话转录跑一遍 LLM 抽取带类型的边。
- **落地顺序**：① 把一次真实的人↔AI 迭代灌进 DAG，半自动打 critique 边；② 渲染 git-graph + 祖先侧栏；③ 加 LLM 抽取器从对话散文提出带类型的边；④ 加 IBIS 蒸馏把图坍缩成决策树。①② 是周末级，③④ 是研究级差异点。

### 来源

- Loom：generative.ink/posts/loom-interface-to-the-multiverse/；github socketteer/loom；exoloom.io
- tldraw branching chat：github tldraw/branching-chat-template；tldraw.dev/starter-kits/branching-chat
- Sensecape：arXiv 2305.11483
- Tree of Thoughts：arXiv 2305.10601；github princeton-nlp/tree-of-thought-llm
- 提示版本/评测：LangSmith、PromptLayer、Langfuse、Braintrust、Humanloop（已关停）
- ComfyUI DAG/JSON：docs.comfy.org
- Figma 分支/版本史：help.figma.com
- 设计理由/IBIS：Wikipedia（Design rationale、IBIS）；gIBIS（Conklin, ACM）；arXiv 2405.19623

---

## 二、本仓现成设施盘点（重大发现：大半已有）

在 omnicompany 仓里搜了一遍，可复用的设施如下。

### 1. 决策树系统 — 高度成熟，可直接用（可信度 9/10）

- DESIGN：`src/omnicompany/packages/domains/decisions/DESIGN.md`
- Schema：`.../decisions/formats.py`
- 实现：`.../decisions/library.py`、`catalog.py`
- CLI：`src/omnicompany/cli/commands/decisions.py`

数据模型：决策树靠 `links` 边而非目录层级。边类型：`rests_on`（决策→信念/依赖）、`supersedes`（决策→决策/演进替换）、`parent`（子→父/层级嵌套）、`related`（一般关联）、`anchor`（记录锚到富媒体 doc/code/AI 产物/消息）。三种记录：`decision`（必须列被否决的备选）、`belief`（可证伪假设，带 challenge_log + verification_status）、`comment`（讨论，可升级为 decision）。
存储：`data/domains/decisions/library/{records.jsonl, index.json}`，append-only、按 id upsert（保留完整谱系）。信念证伪会沿 `rests_on` 反向边传播到依赖它的决策。

### 2. 版本 / 产物追踪 — 成熟多层（8.5/10）

- Material Registry：`src/omnicompany/dashboard/boss_sight/material_registry.py`
- Captures：`.../boss_sight/captures/routes.py`
- Authored Notes：`.../boss_sight/authored/store.py`

每个物料有 uri/id/kind/role/layer、relations（belongs_to_plan、produced_by 等）、status、open_ref、event_id/trace_id/event_source 审计链。所有产物经 `material_events.publish_material_event` 发布，形成可查询的带时间戳事件链。Authored Note 有反馈生命周期 saved→delivered→read→to_todo→todo_done。需要新增的：把"产物派生"显式建成 `supersedes` 边（版本 A→B）。

### 3. 计划 / 探索追踪 — 有脚手架，需整合（6/10）

- Work Report 聚合器：`.../services/_core/lifecycle/work_report.py`（聚合五章：执行概览/拆分思路/逐 task 结果/审阅回流/结论）
- 札记→结构化决策抽取：`.../boss_sight/authored/extract.py`
- ARCH-CHANGES.jsonl：`docs/ARCH-CHANGES.jsonl`（不可变变更日志）

现有的是事后聚合，缺的是探索过程中的实时面包屑捕获（候选提出、否决、转向、施加指正）。但 authored 抽取已经演示了"札记 → 结构化决策"的模式，扩展即可。

### 4. 图 / 网络可视化 — 已有可跑系统（9/10）

- GraphEditor.tsx：`.../dashboard/frontend/src/entities/graph/GraphEditor.tsx`
- 前端已装：Cytoscape.js (3.33.3)、React Flow (11.x)、Mermaid (11.x)、ELKjs (0.11.1)、Dagre (3.0.0)。
- 现状：GraphEditor 读 `/api/notes/_links` 的 FullLinkGraph，用 Cytoscape cose 布局渲染 wiki 链接的反/正向链接，点节点开 note。
- 需要新增：节点类型（decision/belief/comment/artifact）、边标签（rests_on/supersedes/parent/anchor）、状态着色、按 project/track 聚类。

### 5. poof notes BlockSuite 画布 — 生产系统，外部（7/10）

- 无限画布（edgeless）+ Markdown，经 notebridge 文件队列协议跨进程操作。可作"协作探索画布"：把决策树显示在画布上、人拖拽标注、批注作为画布块、自动同步回决策库。注意：从 omnicompany 侧是只读，所有编辑走 notebridge 文件协议。

### 综合：最小可复用组合

```
decisions.library（现成）
  + 把 material 版本建成 supersedes 边（schema 现成，缺 UI）
  + 探索事件捕获 → observation → library upsert（约 1 周）
  + 图可视化前端（约 1 周）
  = 决策树里可见探索路径 + 可视化佐证
```

估算：约 2 周做到"探索路径在决策树里可见"；再加 1–2 周做"从对话/captures 自动构建决策树"。
