<!-- [OMNI] origin=claude-code domain=services/absorption ts=2026-04-17T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:learning.absorption.service_design.md" -->

# absorption · 设计文档

> **命名兼容注**（2026-04-20）：本文中 `Router` / `Format` / `PipelineSpec` 按 [`terminology.md`](../../../../../../docs/standards/_global/terminology.md) 对照读作 `Worker` / `Material` / `Team`。protocol 层类名保留原名（契约不变），新代码 import 请用 `from omnicompany.packages.services.omnicompany import Worker, Material, Team`。

## 状态
- **版本**: V3 + Stage 2 反馈循环 + Stage 3 提案解析（2026-04-15 C1/C3/C5 修复 + 2026-04-17 ProposalDisputeLoop 加入）
- **成熟度**: active（V3 主路径 E2E 跑通 + 反馈循环验证 + Stage 3 提案解析验证）
- **下一步**: 解决 "ModuleExplorer 被 self_portrait 的 G1-G7 限制视野" 根因（dual-research 或 SelfResearchLoop 取代硬编码 self_portrait）

## 核心目的

`services/absorption/` 是**外部仓库吸纳管线**。回答一个根本问题：

> **如何把外部开源仓库里值得学习的模式，系统性地提炼成 OmniCompany 可直接借鉴的改进提案？**

三代演化的迭代视角：

- **V1（Survey & Triage）** — 地标识别（facade→landmark_picker 迭代读→tier 评级→覆盖审计→markdown 报告）。7 节点 DAG。
- **V2（问题驱动深读）** — 带着 G1-G7 问题清单去 repo 里定向寻找答案。ReconScout → IntersectionPlanner → HumanApprovalGate → DirectedReader → CoverageAuditor → Synthesis。
- **V3（模块驱动学习）** — 当前主轴。先 RepoMapper 画全量符号地图，再 ModuleExplorer agent 主动选模块读码，LearningExtractor 提炼发现，ReportWriter 写综合报告。

V3 接入反馈循环（Stage 2）和提案解析（Stage 3）：
- **Stage 2 反馈循环**：HumanFeedbackGate → FeedbackRouter JUMP 回 supplement_explorer，形成多轮迭代。
- **Stage 3 提案解析**：SpecParser 从 report.v3 的 structured.proposals 解析 PRO-NNN 结构化提案 → HumanApprovalGate S3 审批。
- **Stage 3 人审反驳**：ProposalDisputeLoop（新）以 agent_node_loop 异步方式接受人工 dispute，产出 revised_proposals。

本目录**不**解决的问题：
- 不直接修改源码（Stage 3 只到 proposal 解析，不跑 workflow_generator）
- 不把 repo 整体 clone 到 src/（只做"学习发现"到"提案候选"的链路）
- 不自动执行 "写新节点"（下游 workflow_factory 才做）

## 核心接口

### V1 Survey 管线
- **`build_survey_pipeline()`** — 7 节点线性 DAG — [pipeline.py:59](pipeline.py#L59)
- **`LandmarkPickerRouter`** — AgentNodeLoop 读 facade + omnicompany_snapshot → 打 tier 评级 — routers.py

### V2 问题驱动管线
- **`build_v2_pipeline()`** — 8 节点含人审门 — [pipeline.py:334](pipeline.py#L334)

### V3 模块驱动主路径
- **`build_v3_pipeline()`** — 9 节点含反馈循环 — [pipeline.py:575](pipeline.py#L575)
- **`RepoMapperV3`** — 纯计算符号地图（coarse_view + detail_views）— [routers/repo_mapper.py](routers/repo_mapper.py)
- **`ModuleExplorerV3`** — AgentNodeLoop local_grep + local_read + submit_module — [routers/module_explorer.py](routers/module_explorer.py)
- **`LearningExtractorV3`** — LLM 分批按 gap_id 提炼 finding — [routers/learning_extractor.py](routers/learning_extractor.py)
- **`ReportWriterV3`** — LLM 综合报告 + 路径硬替换 — [routers/report_writer.py](routers/report_writer.py)

### Stage 2 反馈循环
- **`HumanFeedbackGateV3`** + **`FeedbackRouterV3`** — 读 feedback.md → PARTIAL JUMP 至 supplement_explorer — routers.py
- **`ReportUpdaterV3`** — LLM 增量融合补充发现到已有报告 — [routers/report_updater.py](routers/report_updater.py)

### Stage 3 提案解析
- **`build_v3_stage3_pipeline()`** — SpecParser + HumanApprovalGate S3 — [pipeline.py:868](pipeline.py#L868)
- **`SpecParserS3`** — 从 report.v3 解析结构化 PRO-NNN 提案 — [routers/spec_parser.py](routers/spec_parser.py)
- **`HumanApprovalGateS3`** — 读 approved_proposals.txt 做审批 — [routers/human_approval_gate_s3.py](routers/human_approval_gate_s3.py)
- **`ProposalDisputeLoopRouter`** — agent_node_loop 异步人审 dispute → revised_proposals — [routers/proposal_dispute_loop.py](routers/proposal_dispute_loop.py)

### Formats（见 [formats.py](formats.py)）
- V1: `absorption.user_request` → `intake` → `facade_card` → `omnicompany_snapshot` → `landmark_list` → `coverage_audit` → `triaged_landmarks` → `report`
- V2: `absorption.request` → `recon.map` → `question-list{,.approved}` → `question{,.answer}` → `audit` → `synthesis` → `report.v2`
- V3: `absorption.request` → `repomap` → `important-modules` → `module.code` → `learning` → `report.v3` + `feedback` + `supplement_request`
- Stage 3: `absorption.proposal.list` → `proposal.approved`（+ 未接入的 `workflow.diff/approved/result`）

## 架构决策

### D1 — 三代分工：V1 Survey 粗览 / V2 问题驱动定向 / V3 模块驱动深度

不是竞争关系而是认知深度的递进：
- V1 回答"这 repo 里有什么值得看的"（tier 评级）
- V2 回答"我有这些缺口（G1-G7），这 repo 能填吗"（带问题找答案）
- V3 回答"我应该读哪些模块的代码并从中学到什么"（模块→代码→发现）

V3 的进步：避免 V2 被问题清单限死（问题没覆盖到的正交基础设施读不到）。但 V3 仍受 self_portrait 的 G1-G7 框架影响（见 §已知局限 1）。

三套管线在代码里**同时存在**，`PIPELINES` dict 同时注册 `absorption.survey` / `absorption.v2` / `absorption.v3` / `absorption.v3-stage3`。Doctor 生态可并行评估三套哪个在什么场景表现更好。

### D2 — AgentNodeLoop 作为核心探索节点（非单次 LLM 调用）

LandmarkPickerV1 / ReconScoutV2 / ModuleExplorerV3 / SupplementExplorerV3 / ProposalDisputeLoop 五个关键探索节点都用 AgentNodeLoop 而非一次性 LLM。

理由：
- 外部 repo 信息量大（数百到数万文件），单次 prompt 塞不下，必须 agent 迭代式 local_grep/local_read 主动拉取
- Agent 有 4 层压缩（microcompact / truncation / sliding_window / auto_compact），长读码不炸上下文
- 单次 LLM 调用只能对"已喂进来的信息"做判断，无法"我再看一下那个文件"
- 实测：ModuleExplorer 单 loop 平均 30-50 turns，读 20-80 个文件后产出

预算 `max_turns=1000 / max_steps=1000`（符合铁律 B：预算宽松到触发即 bug）。

### D3 — 人审不是同步门，而是异步反馈回路

早期设计 HumanApprovalGate 是同步阻塞（runner 挂住等人工写文件）。这对 long-lived 管线不友好（人可能几天后才回复）。

V3 Stage 2 改为**异步反馈循环**：
- `report_writer` 写完 report.md 后 EMIT
- 用户事后（随时）写 `feedback.md`
- 下一次调 `human_feedback_gate` 时读入 → FeedbackRouter 判定 → JUMP 回 supplement_explorer

Stage 3 更激进：`ProposalDisputeLoop` 本身就是 agent_node_loop。人写 dispute message 后，agent 自己 local_read 相关源码、重新 propose。不走传统"人审批 = 勾选框"流程，而是"人提出质疑 = agent 再深度研究"。

### D4 — LearningExtractor 按 gap_id 分批，移除 hardcap 10（C1 修复，2026-04-15）

**问题**：原版一次性把 ≥30 个模块喂给 LLM 出 10 条 finding（hardcap），~55→10 的压缩导致 82% 信息丢失。

**修复**：
- 按 gap_id 分组，每个 gap 单独喂 LLM（默认 1 gap 含 ~3-10 个模块）
- 若某个 gap 模块数 >10，自动拆成多个批次（`_MAX_MODULES_PER_BATCH=10`）
- 移除 finding 上限；每个 gap 产出 2-5 条 finding 正常
- System prompt 加 **路径保真原则**："evidence file 必须原样保留，不得改写"

实证：E2E 跑 hermes-agent-real，findings 从 10 升到 22。参考 [docs/plans/[2026-04-15]PROPOSAL-QUALITY/pipeline_diagnosis.md](../../../../docs/plans/[2026-04-15]PROPOSAL-QUALITY/pipeline_diagnosis.md)。

### D5 — ReportWriter 路径硬替换防止 LLM 编造（C3 修复）

LLM 渲染 Markdown 报告时会"顺手"把路径改得更干净（如把真实 `src/agent/loop/trajectory.py` 写成 `agent/loop.py`）。下游 SpecParser 引用这些路径做 proposal 时就引用了不存在的文件。

修复：`_fix_fabricated_paths(text, real_paths)` 扫 LLM 产出的 `.py` 路径，若不在 `real_paths` 集合中，用最近基名匹配替换。先扫 report_md，再扫 detail_md。

实证：编造率 47% → 0%。

### D6 — SpecParser 注入 OmniCompany 代码摘要 + 禁截断（C5 修复）

原版 SpecParser 两个 bug：
- 给 LLM 传的 `report_md[:6000]` 硬截断（报告总长 ~10KB，finding #12-#17 在 bytes 6000-9927 被砍）
- LLM 不知道 OmniCompany 现有什么代码，容易提出"重复造轮子"的 proposal

修复：
- 去掉 `[:6000]` 截断（铁律 A：禁止预防性截断）。若 report 含 DETAIL 节则剥离 DETAIL，否则全量。
- `_build_omnicompany_summary()` 硬编码当前 OmniCompany 核心模块摘要，注入 system prompt
- `_SPEC_SYSTEM` 加"功能级纪律"：区分 enhance_existing / new_capability / architectural_pattern 三种提案类型

实证：proposal 从 5 条浅层 → 7 条精准分类 + 每条正确绑定类别。

### D7 — ProposalDisputeLoop 引入 agent 而非规则 reviser（2026-04-17）

Stage 3 原设计是 SpecParser → HumanApprovalGate 一次成型，不支持"我审过了但不满意"场景。

新节点 `ProposalDisputeLoopRouter`：
- FORMAT_IN: `absorption.proposal.dispute`（含 original_proposals + human_feedback + repo_path）
- FORMAT_OUT: `absorption.proposal.revised`
- 工具：local_list / local_read / local_grep / submit_revised_proposals / FinishTool / ThinkTool
- 所有工具 `is_readonly=True`（revised submission 写 session 字典，不改 FS）
- LoopConfig：`max_turns=1000`、permission=readonly、auto_compact_enabled=True

session 状态走模块级 `_sessions` dict，落盘到 `data/domains/absorption/<repo>/revised_proposals.md`。

实测：一次人工 dispute（14 turns，16 次 local_read）产出 3 条新 P0 proposal：PRO-008 learning closure / PRO-009 delegation / PRO-010 HRR memory。

## 数据流 / 拓扑

### V3 完整拓扑（主路径 + 反馈循环 + Stage 3）

```
[V3 主路径：模块驱动学习]
  user_request (repo_name + repo_local_path + self_portrait)
     ↓
  repo_mapper (RULE)                 → coarse_view + detail_views + files[]
     ↓
  module_explorer (AgentNodeLoop)    → module_readings[]（含 gap_id 绑定）
     ↓  ← loop 内 local_grep / local_read / submit_module
  learning_extractor (LLM 分批)      → findings[]（按 gap_id 分组，无上限）
     ↓  ← 每 gap 独立调 LLM，C1 修复后
  report_writer (LLM + 路径硬替换)   → report.v3 + structured.proposals
     ↓  ← C3 修复：_fix_fabricated_paths
  human_feedback_gate (RULE)         → feedback（读 feedback.md 或 auto-pass）
     ↓
  feedback_router (RULE)
     ├─ directions=[] → PASS → EMIT  (report 锁定)
     └─ directions≠[] → PARTIAL → JUMP to supplement_explorer

[补充探索路径（当 feedback 非空）]
  supplement_explorer (AgentNodeLoop) → module.code
     ↓  ← 基于 supplement_guidance + previous_findings 定向读
  supplement_extractor (LLM)          → learning（补充 findings）
     ↓
  report_updater (LLM 增量融合)       → report.v3（iteration+1）
     ↓
  → 回到 human_feedback_gate（继续下一轮反馈）

[Stage 3 提案解析（可独立跑）]
  absorption.report.v3 (from V3 EMIT)
     ↓
  spec_parser (RULE+LLM)             → proposal.list
     ↓  ← C5 修复：注入 omni 摘要，去截断
  human_approval_gate_s3 (RULE)      → proposal.approved → EMIT

[Stage 3 人审反驳（异步）]
  (人工对某 proposal 发起 dispute)
     ↓
  ProposalDisputeLoop (AgentNodeLoop) → proposal.revised
     ← 工具 local_list / local_read / local_grep / submit_revised_proposals
     ← 产物写 data/domains/absorption/<repo>/revised_proposals.md
```

数据落盘：
- `data/domains/absorption/<repo>/report.md` — 累积 iteration 的综合报告
- `data/domains/absorption/<repo>/feedback.md` → `feedback_N.md.done`（读后重命名）
- `data/domains/absorption/<repo>/pending_proposals.md` — Stage 3 产出
- `data/domains/absorption/<repo>/approved_proposals.txt` — 人审批结果
- `data/domains/absorption/<repo>/revised_proposals.md` — DisputeLoop 产出

## 已知局限

1. **ModuleExplorer 采样被 self_portrait 的 G1-G7 视野限制** — 核心未解问题。V3 虽号称"模块驱动"，但 self_portrait（硬编码 G1-G7 文字）作为 ModuleExplorer 的 system prompt 注入，agent 的 submit 判断倾向选"已被 G1-G7 cover 的模块"，正交模块（如 hermes 的 context_engine / mixture_of_agents / checkpoint_manager / smart_routing）读不到。独立研究 vs 管线输出对比：7 个关键领域中 4 个缺失。**升级路径**：SelfResearchLoop（动态从 README + git commits 产生 understanding）或 dual-research（先独立 agent 全量 scan 再交叉）。参考 [docs/plans/[2026-04-15]PROPOSAL-QUALITY/independent_vs_pipeline_gap.md](../../../../docs/plans/[2026-04-15]PROPOSAL-QUALITY/independent_vs_pipeline_gap.md)。

2. **Stage 3 workflow_generator / danger_gate / validator 未落地** — Stage 3 只到 proposal.approved，不跑 `absorption.workflow.diff/approved/result`。这些 Format 在 formats.py 里已定义但对应 Router 未实现。想自动从 proposal 走到 `generated/` 代码变更需要 workflow_factory（目前归 services/workflow_factory/，未接通）。

3. **LearningExtractor 单 gap 批量大 >10 模块时 LLM 容易跑偏** — 虽然 C1 分批缓解，但若某 gap 有 >15 模块，LLM 合批提炼时记忆衰减明显（有时 finding #8-#10 质量断崖）。缓解：分更细的子批（改 `_MAX_MODULES_PER_BATCH`），但 token 成本上升。根治：改 agent_node_loop 式提炼（让 LLM 主动说"我要再看下 module X 的细节"）。

4. **ProposalDisputeLoop 的 `_sessions` dict 是进程级状态** — 不跨进程持久化。server 重启 session 丢失。若需要分布式多工作者场景，要改成 bus 或 sqlite 存储。

5. **三代管线并存导致路由选择复杂** — 用户跑 `omnicompany dispatch absorption ...` 时选哪个（survey / v2 / v3 / v3-stage3）由 `PIPELINES` dict 注册顺序 + domain 默认决定，没有自动选择器。长期看应退役 V1/V2（保留 V3 + Stage 3），但目前 V1 的 LandmarkPicker 仍是独立研究场景的有效工具。

## 参考资料

- 关联管线：[pipeline.py](pipeline.py) 四个 build_*_pipeline() 函数
- 关联 workers：[workers/](workers/) 目录（34 Worker 分 v1/v2/v3 三子域, Clean Migration 2026-04-20）
- 关联 routers（compat shim）：[routers/](routers/) 目录（shim re-export 旧 Router 名 → 新 Worker alias）
- 关联 formats：[formats.py](formats.py) 四组 ALL_*_FORMATS
- 关联归档：_archive/ · routers_v1v2_legacy.py + routers_v3_legacy/ (Diamond 继承业务逻辑源)
- 关联 plan：`docs/plans/[2026-04-15]PROPOSAL-QUALITY/pipeline_diagnosis.md`（信息丢失链路全分析）
- 关联 plan：`docs/plans/[2026-04-15]PROPOSAL-QUALITY/hermes_independent_study.md`（独立研究基线）
- 关联 plan：`docs/plans/[2026-04-15]PROPOSAL-QUALITY/independent_vs_pipeline_gap.md`（4/7 领域缺失）
- 关联 plan：`docs/plans/[2026-04-13]REPO-ABSORPTION-V3/DESIGN.md`（V3 设计起源）
- 关联 plan：`docs/plans/[2026-04-14]STAGE3-WORKFLOW-MODIFIER/plan.md`（Stage 3 设计）
- 关联 plan：`docs/plans/[2026-04-17]OMNICOMPANY-SELF-KNOWLEDGE/HANDOFF.md`（本文所在 session 的交接）
- 关联 gap：docs/gaps/G2_learning_distill.md
- 关联 workflow_factory：[../workflow_factory/DESIGN.md](../../_core/workflow_factory/DESIGN.md)（Stage 3 未接入的下游）

## 十、Team 专属信息（Clean Migration V2, 2026-04-20）

> 迁移类型: **类 B+ · 原生 pipeline 形态 · 超大 (~7151 行 legacy)**
> 迁移策略: **Diamond shortcut**（业务代码保留在 `_archive/`, workers/ 只做 `class XxxWorker(Worker, _Legacy)` 继承）
> Stage 2 硬规则: 11/11 通过（见 migration_log.md · 完全迁移标准）

### 10.1 目录结构

```
absorption/
├── __init__.py          # re-export Workers + legacy Router alias + materials + pipelines
├── DESIGN.md            # 本文 (active)
├── formats.py           # 39 Material (含 2 composite), kind.* 全标
├── pipeline.py          # 4 条 build_*_pipeline() (不改)
├── run.py               # 4 条 build_*_bindings() (改 Worker 新名)
├── landmark_picker.py   # LandmarkPickerRouter(AgentNodeLoop) — 不迁, 原位保留
├── snapshot.py, tools.py, wiki_loader.py  # utilities, 不是 Worker
├── workers/             # NEW: 34 Worker 三子域
│   ├── __init__.py      # ALL_WORKERS 聚合
│   ├── v1/              # 6 Worker · Survey & Triage
│   ├── v2/              # 7 Worker · 问题驱动深读
│   └── v3/              # 21 Worker · 模块驱动学习 + Stage 2/3
│       └── knowledge_loaders/   # 7 Worker · wiki 三路 fan-in + Stage 3 entry
├── routers/             # compat shim package
│   ├── __init__.py      # re-export all 34 Worker + 34 Router alias
│   └── <name>.py        # 子模块 shim, 兼容 routers.module_explorer 等旧路径
└── _archive/
    ├── README.md
    ├── routers_v1v2_legacy.py   # 原 routers.py (2939 行 · 13 Router + 2 内嵌 AgentNodeLoop)
    └── routers_v3_legacy/       # 原 routers/ 目录 (12 文件 · 20 Router + 2 内嵌 AgentNodeLoop)
```

### 10.2 Worker 清单（34 Worker · 按子域）

**V1 Survey & Triage (6 Worker · `workers/v1/`)**:
| Worker | FORMAT_IN | FORMAT_OUT |
|---|---|---|
| TargetIntakeWorker | absorption.user_request | absorption.intake |
| RepoFacadeFetcherWorker | absorption.intake | absorption.facade_card |
| OmnicompanySnapshotFetcherWorker | absorption.intake | absorption.omnicompany_snapshot |
| CoverageAuditorWorker | absorption.landmark_list | absorption.coverage_audit |
| TriageGateWorker | absorption.coverage_audit | absorption.triaged_landmarks |
| ReportWriterWorker | absorption.triaged_landmarks | absorption.report |

**V2 问题驱动深读 (7 Worker · `workers/v2/`)**:
| Worker | 内部结构 |
|---|---|
| ReconScoutV2Worker | 含 `_ReconLoop` (AgentNodeLoop) |
| IntersectionPlannerV2Worker | 纯计算 |
| HumanApprovalGateV2Worker | 读 approved_questions.txt |
| DirectedReaderV2Worker | 含 `_DirectedReaderLoop` (AgentNodeLoop) |
| CoverageAuditorV2Worker | 纯计算 |
| SynthesisV2Worker | LLM 综合 |
| ReportWriterV2Worker | Markdown 产出 (sink) |

**V3 模块驱动学习 (21 Worker · `workers/v3/`)**:

- **knowledge_loaders/ (7)**: Stage3EntryBootstrap + 3 QueryBuilder (Capability/Gap/Reception) + 3 Loader
- **主路径 (5)**: RepoMapper / ModuleExplorer (含 `_ExplorerLoop`) / ModulePicker / ModuleReader / LearningExtractor
- **报告与反馈 (4)**: ReportWriterV3 / HumanFeedbackGateV3 / FeedbackRouterV3 / ReportUpdaterV3
- **Stage 3 (5)**: SpecParser / HumanApprovalGateS3 / ProposalFeedbackGate / ProposalFeedbackRouter / ProposalDisputeLoop (含 `_DisputeLoop`)

### 10.3 AgentNodeLoop 保留清单（5 处, 不迁）

| 类名 | 位置 | 理由 |
|---|---|---|
| `LandmarkPickerRouter` | `landmark_picker.py` 顶层类 | **整个类继承 AgentNodeLoop**, 不是 Router. 保留原位 |
| `_ReconLoop` | `_archive/routers_v1v2_legacy.py` 内嵌 | 内嵌于 ReconScoutV2Router.run() |
| `_DirectedReaderLoop` | `_archive/routers_v1v2_legacy.py` 内嵌 | 内嵌于 DirectedReaderV2Router.run() |
| `_ExplorerLoop` | `_archive/routers_v3_legacy/module_explorer.py` 内嵌 | 内嵌于 ModuleExplorerRouter.run() |
| `_DisputeLoop` | `_archive/routers_v3_legacy/proposal_dispute_loop.py` 内嵌 | 内嵌于 ProposalDisputeLoopRouter.run() |

阶段 D AGENT-NODE-LOOP-ROUTERIZATION 落地后会统一迁移到新 `packages/services/agent/AgentNodeLoop`.

### 10.4 Material kind 分配（39 Material, F-19 100% 覆盖）

**kind.source (2)**:
- `absorption.user_request` — V1 入口
- `absorption.request` — V2/V3 入口（repo_name + repo_local_path）

**kind.sink (4)**:
- `absorption.report` — V1 md 报告
- `absorption.report.v2` — V2 md 报告
- `absorption.proposal.approved` — Stage 3 审批门最终输出（下游 workflow_factory 消费但未接通）
- `absorption.workflow.result` — Stage 3 workflow 最终产出（未接通）

**kind.internal (33)**: 其余全部（含 2 composite + 6 omni.self.* 知识族 + 多代中间态）

**composite Material 处理**（2 个 · F-16 第 5 项）:
- `absorption.proposal.context` (3 路 fan-in: report.v3 + capability_inventory + gap_registry) · kind.internal
- `absorption.module_exploration.context` (4 路 fan-in: repomap + 3 omni.self.*) · kind.internal

### 10.5 Proposal 子概念在 V3 中的位置

"Proposal" 是 Stage 3 的**核心叙事单元**, 贯穿 5 Worker:

```
V3 主路径产 report.v3 (含 structured.proposals)
      ↓
  [composite absorption.proposal.context 3 路 fan-in]
      ↓
  SpecParserWorker → absorption.proposal.list (PRO-NNN 结构化)
      ↓
  ProposalFeedbackGateWorker → absorption.proposal.feedback
      ↓
  ProposalFeedbackRouterWorker → EMIT / JUMP
      ↓
  HumanApprovalGateS3Worker → absorption.proposal.approved (sink)
      ↓
  (外部): ProposalDisputeLoopWorker ← 人工 dispute → revised_proposals.md
```

两层 feedback 回路（Stage 2 report 级 + Stage 3 proposal 级）是 V3 与 V1/V2 的主要区别。

### 10.6 Diamond shortcut 理由

absorption 是 Stage 2 迁移中最大的 Team（~7151 行 legacy, 35 Router + 4 AgentNodeLoop），远超 `> 4000 行` 的 Diamond 门槛. 真迁 35 Worker 将:
1. 重写 35 个 `__init__` / `run` / 辅助函数 → 引入回归风险
2. 需复制 40+ 辅助函数 (如 `_parse_repo`, `_gh_api`, `_build_finding_with_code`) 到 workers/
3. 无法快速回滚

Diamond shortcut 用 `class XxxWorker(Worker, _Legacy)` 一行继承保证命名层合规, 业务代码零修改 (活代码仍在 `_archive/`). 这是 doctor Team (2026-04-20) 验证过的模式.

**Stage 3 清洁工作** (P3): 未来把 `_archive/` 业务代码搬到 `workers/*.py` 顶层, 优先级低于 Stage 2 全 Team 覆盖.

### 10.7 routers/ 子模块 shim 策略

原 `routers/` 目录是 V3 源码位置（12 文件）; 外部代码曾用路径:
- `from ...absorption.routers import FooRouter`
- `from ...absorption.routers.module_explorer import ModuleExplorerRouter` (子模块)
- `from ...absorption.routers.report_writer import _build_finding_with_code` (模块级工具)

新 `routers/` 变为 compat shim package:
- `routers/__init__.py` re-export 全部 34 Worker + 34 Router alias
- `routers/<name>.py` 11 文件 shim 保留旧子模块路径（含 report_writer shim 特殊 re-export 辅助函数）

旧 `_archive/routers_v3_legacy/report_updater.py` 内部的 `from absorption.routers.report_writer import ...` 已改为从 `_archive` 直接 import, 避免 shim 循环依赖.

### 10.8 验证清单

- [1] 类继承 `class X(Worker)` ✓ (Diamond: `class XWorker(Worker, _LegacyRouter)`)
- [2] Material 定义用 omnicompany import ✓
- [3] workers/ 子目录 + ALL_WORKERS ✓ (三子域 v1/v2/v3, 34 Worker)
- [4] __init__.py re-export ✓
- [5] kind.* 必填 (F-19) ✓ (39/39)
- [6] FORMAT_IN_MODE 显式 (R-24) — 本 Team 暂无 list[str] FORMAT_IN, 不适用
- [7] Verdict.output 平铺 (R-23) — 沿用原 Router 约定, 保持兼容
- [8] 声明即消费 (F-15 / P-13) — 原 Router 遵守, Diamond 无改动
- [9] MaterialDispatcher smoke — 本 Team 依赖 LLMClient + 真实 GitHub 调用, smoke 需外部环境, 待 Phase 1 pilot
- [10] DESIGN.md 七节 + §十 ✓
- [11] _archive/README.md ✓

