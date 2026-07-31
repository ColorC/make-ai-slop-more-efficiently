<!-- [OMNI] origin=claude-code domain=services/doctor ts=2026-05-04T11:00:00Z type=doc status=active belongs_to_service=doctor -->
<!-- [OMNI] material_id="material:diagnosis.doctor.service_design.document.md" -->

# doctor · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部** (接口 / 决策 / 数据流 / 局限 / Clean Migration 实施策略).
>
> **Clean Migration V2**（2026-04-20）：24 个主 Worker 按子域拆到 `workers/format/` / `workers/router/` / `workers/pipeline/`; legacy `routers.py` / `pipeline_topology.py` 归档到 [`_archive/`](_archive/), 根目录同名文件保留 **compat shim** (旧 `from ...doctor.routers import FooRouter` 继续工作). 新代码直接 `from ...doctor.workers import FooWorker`.
> **V3 New World Diagnostics**（2026-04-20 夜）：加 `workers/blackboard/` 第四子域 (6 Worker / 7 新 Material) 诊断新世界订阅图 (F-19 / R-23~R-25 / Q4). 总 Worker 数 24 → 30.

## 状态
- **版本**: V3 · New World Diagnostics + 诊断重制 Phase 2 起步 (30 Worker · 4 子域 + 1 agent 骨架)
- **成熟度**: active (V3 主体) + skeleton (诊断重制初期 5 Material + 1 agent 待跑通)
- **下一步**:
  - **诊断重制 (主线)**: 落 spec_diagnostic agent 真跑通 → 加 hypothesis/exemplar/plan 三种诊断 agent → 收编现 V3 30 worker 到方法层抽象 — 见 [docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/plan.md](../../../../../../docs/plans/diagnosis/%5B2026-05-05%5DDIAGNOSIS-RECONSOLIDATION/plan.md)
  - **V3 老分支 backlog**: `.omni/health/` 就近写盘 / 接 LAP `crystallize` 反馈回路 (低优先, 跟新方向并行)

## 诊断重制 (Phase 2 起步, 2026-05-05)

> 本服务从"按对象诊断的 30 worker 4 子域"升级为"按方法 × 对象诊断, 主诊断器 = 核心层 agent worker".
> 现 V3 4 子域 (Format/Router/Pipeline/Blackboard 各 workers/) 保留, 后续新增按"诊断方法"抽象的 agent.

### 诊断方法 vs 诊断对象 (二维)

**方法层** (新加, 走 ConfigurableAgent):
- **规范型**: 拿 `docs/standards/` 文档原文 + 对象 → LLM 自然语言判合规度. 不抽硬规则 (硬规则归 guardian)
- **样例型**: 跟 `data/services/doctor/exemplars/` 标杆比, 看差在哪
- **假设型**: 拿 `data/services/doctor/hypotheses/` 假设条 → 看对象违反哪条
- **计划型**: 跟 `docs/plans/<plan>/plan.md` 对账, 看产物在不在 / 跑得出来不

**对象层** (现 V3 已覆盖 + 待扩):
- 现有: Format / Router / Pipeline / Blackboard (workers/{format,router,team,blackboard}/)
- 待扩: Agent / Tool / Hook (新立)

### 诊断重制初期 Material (11, 见 [formats.py](formats.py))

> 用户铁律: 一切都是 material. 假设 / 样例 / 健康发现都走 Material 标准, 不自创 yaml schema.

- `doctor.hypothesis.statement` (kind.source) — 一条健康假设. 实例存 `data/services/doctor/hypotheses/`
- `doctor.exemplar` (kind.source) — 一份标杆样例. 实例存 `data/services/doctor/exemplars/`
- `doctor.health_finding` (kind.sink) — 诊断结果统一格式. 走 SQLiteBus → registry HealthArchive
- `doctor.spec_diagnosis.request/verdict` — 规范型 request/verdict
- `doctor.hypothesis_diagnosis.request/verdict` — 假设型 request/verdict
- `doctor.exemplar_diagnosis.request/verdict` — 样例型 request/verdict
- `doctor.plan_diagnosis.request/verdict` — 计划型 request/verdict
- `doctor.hypothesis_derivation.request/report` — 假设派生 request/report (派生 agent 专属)

### 已立 agent (4 诊断 + 1 派生, 全 dogfood 跑通)

- [`agents/spec_diagnostic.py`](agents/spec_diagnostic.py) `SpecDiagnosticAgent` (id: `doctor.spec_diagnostic`) — 规范型诊断
  - 跑路径: `doctor.spec_diagnosis.request → SpecDiagnosticAgent → doctor.spec_diagnosis.verdict + list[doctor.health_finding]`
  - dogfood 跑通 × 3 (step 7, 8.4, 9.1-9.2). 见 dogfood_step7_report.md + dogfood_step8_4_report.md
  - prompt 在 [`agents/spec_diagnostic_prompt.md`](agents/spec_diagnostic_prompt.md)
  - override hooks: `build_tool_context` 注入 task_id (= trace_id) + agent_id + scratch / `build_extract_result` verdict event payload 走 submit_verdict args (跨 turn 持久化, 不依赖 ctx 短生命)

- [`agents/hypothesis_diagnostic.py`](agents/hypothesis_diagnostic.py) `HypothesisDiagnosticAgent` (id: `doctor.hypothesis_diagnostic`) — 假设型诊断
  - 跑路径: `doctor.hypothesis_diagnosis.request → HypothesisDiagnosticAgent → doctor.hypothesis_diagnosis.verdict + list[doctor.health_finding (finding_kind=hypothesis)]`
  - dogfood 跑通 × 1 (step 9.4). 用 sample_hypothesis 诊断 doctor 自己 worker
  - 复用 SpecDiagnosticAgent 的 build_tool_context + build_extract_result hook (双 agent 共用 80% 代码, 模式可复制)

- [`agents/exemplar_diagnostic.py`](agents/exemplar_diagnostic.py) `ExemplarDiagnosticAgent` (id: `doctor.exemplar_diagnostic`) — 样例型诊断
  - 跑路径: `doctor.exemplar_diagnosis.request → ExemplarDiagnosticAgent → doctor.exemplar_diagnosis.verdict + list[doctor.health_finding (finding_kind=exemplar)]`
  - dogfood 跑通 × 2 (post-compact phase 2 后续 1). 用 sample_exemplar (csv_reader) 比对 doctor 的 format_in_mode_checker.py
  - prompt 在 [`agents/exemplar_diagnostic_prompt.md`](agents/exemplar_diagnostic_prompt.md) — 强调"差在哪 / 学到什么", 不是判合规
  - 复用 SpecDiagnosticAgent 的 build_tool_context + build_extract_result hook (三 agent 共用 80% 代码 — 模式跨 spec/hypothesis/exemplar 可复制确认)

- [`agents/plan_diagnostic.py`](agents/plan_diagnostic.py) `PlanDiagnosticAgent` (id: `doctor.plan_diagnostic`) — 计划型诊断
  - 跑路径: `doctor.plan_diagnosis.request → PlanDiagnosticAgent → doctor.plan_diagnosis.verdict + list[doctor.health_finding (finding_kind=plan)]`
  - dogfood 跑通 × 1 (post-compact phase 2 后续 2). 元 dogfood — 拿本计划自己 plan.md 跑诊断, LLM 准确指出本计划用"用户决策清单"等节名跟 plan_template 一-五节不对应, 但因是元计划不阻断 (走 advisory)
  - prompt 在 [`agents/plan_diagnostic_prompt.md`](agents/plan_diagnostic_prompt.md) — 强调"plan 完成度 + 结构合规 + 产物存在性"
  - V0 仅静态 (结构 + 产物 path 实在性), 动态验收 (跑入口) 留 V1
  - 配套规范: [docs/standards/protocol/plan_template.md](../../../../../../docs/standards/protocol/plan_template.md) — plan.md 应长什么样
  - 复用 SpecDiagnosticAgent 的 build_tool_context + build_extract_result hook (四 agent 共用 80% 代码 — 模式跨 spec/hypothesis/exemplar/plan 全四方法可复制确认)

- [`agents/hypothesis_deriver.py`](agents/hypothesis_deriver.py) `HypothesisDeriverAgent` (id: `doctor.hypothesis_deriver`) — 假设派生 (供给 HypothesisDiagnosticAgent)
  - 跑路径: `doctor.hypothesis_derivation.request → HypothesisDeriverAgent → doctor.hypothesis_derivation.report + N×doctor.hypothesis.statement (落 yaml)`
  - dogfood 跑通 × 1 (post-compact phase 2 后续 3). 拿 worker.md 派生 5 条 worker 类假设 (H-2026-05-06-001..005), 各引 R-02/R-24/etc 具体规范条款, evidence_query 注明 'ast 解析能查可转 guardian'
  - prompt 在 [`agents/hypothesis_deriver_prompt.md`](agents/hypothesis_deriver_prompt.md) — 强调'抽出应满足什么 + 为什么', hard rule vs 软语义自然分流
  - 工具集替: write_finding/submit_verdict → write_hypothesis/submit_derivation_report (派生专属业务工具)
  - 自定义 build_extract_result hook (扫 messages 找 last 成功 submit_derivation_report, 跟诊断 agent 思路一致)

### 业务工具 (4)

诊断 agent 用 (2):
- [`tools/write_finding.py`](tools/write_finding.py) `WriteFindingRouter` (TOOL_NAME=`write_finding`) — 落一条 doctor.health_finding yaml 到 `data/services/doctor/findings/<task_id>/<finding_id>.yaml`. 必填 entity_id/entity_kind/finding_kind/evidence/commentary/concern (无 severity).
- [`tools/submit_verdict.py`](tools/submit_verdict.py) `SubmitVerdictRouter` (TOOL_NAME=`submit_verdict`) — 诊断 agent 出口检查工具. 严格 schema 校验 + 显式拒 7 打分字段. 通过校验才合法结束 loop. 4 类诊断方法 (spec/hypothesis/exemplar/plan) 共用本工具.

派生 agent 用 (2):
- [`tools/write_hypothesis.py`](tools/write_hypothesis.py) `WriteHypothesisRouter` (TOOL_NAME=`write_hypothesis`) — 落一条 doctor.hypothesis.statement yaml 到 `data/services/doctor/hypotheses/<id>.yaml`. 必填 id/source_kind/source_path/source_excerpt/statement/motivation/applies_to/evidence_query (各字段长度门 + 拒 severity).
- [`tools/submit_derivation_report.py`](tools/submit_derivation_report.py) `SubmitDerivationReportRouter` (TOOL_NAME=`submit_derivation_report`) — 派生 agent 出口检查. 校验 source_paths/derived_hypothesis_ids/narrative + 拒 7 打分字段.

### 用户铁律落地

| 铁律 | 落地 |
|---|---|
| 不擅设施 | 沿用 service 标准结构 (workers/agents/tools/), 不立 6 子域目录 |
| 规范是引用不抽取, LLM 判 | SpecDiagnosticAgent prompt 让 LLM 读 docs/standards/ 原文判, 不抽硬规则 (硬规则归 guardian) |
| 自然语言判定 | finding 三字段 evidence/commentary/concern 必填自然语言, hypothesis statement+motivation 自然语言, narrative 自然语言整体评论 |
| 假设也是 material | doctor.hypothesis.statement Material 类型, 实例 yaml 走 `data/services/doctor/hypotheses/` |
| 一切都是 material | finding/exemplar/spec_request+verdict/hypothesis_request+verdict 全 Material 类型 |
| 骨架带 todo | 每文件含 ## 待做 列表 |
| 拒打分拥评论 | submit_verdict 7 banned 字段, finding/verdict schema 全去 severity/confidence |
| 堵不如疏出口检查 | submit_verdict 不阻 LLM finish 但 verdict 不传播 (FAIL 时 dispatcher 跳过 publish) = 等效"未调用通过不能退出" |

### 实例库目录 (运行时自动创)

诊断 agent 真跑时按需创下面三个目录 (`data/` 路径整体 gitignore, runtime 数据不进版本控):

- `data/services/doctor/hypotheses/` — 假设实例库 (`doctor.hypothesis.statement` Material 实例)
- `data/services/doctor/exemplars/` — 样例库 (`doctor.exemplar` Material 实例)
- `data/services/doctor/findings/` — 诊断发现归档 (`doctor.health_finding` Material 实例)

教学示例 (一份 hypothesis, 进版本控) — docs/plans/.../sample_hypothesis_H-2026-05-05-001.yaml

### Phase 2 已完 (本会话 2026-05-05)

- [x] **agent 框架真接通** (ConfigurableAgent FORMAT_IN/OUT 自动派生 patch + 双 agent 跑通)
- [x] **write_finding 业务工具** (落 yaml + ctx 注入 task_id/agent_id, registry HealthArchive 接通待 9.5)
- [x] **2 个诊断 agent**: SpecDiagnosticAgent + HypothesisDiagnosticAgent
- [x] **接入 dispatcher**: build_diagnostic_dispatcher 入口, 双 agent 都 dogfood 跑通
- [x] **真 dogfood** (3 次, 跨 spec/hypothesis 两种诊断方法)
- [x] **submit_verdict 出口检查** + **拒打分拥评论 schema** (用户两条新铁律)
- [x] **出口协议守卫** (没 submit_verdict 时返 FAIL, 等效"未调用通过不能退出")

### Phase 2 后续 (按推荐顺序, post-compact)

- [x] **ExemplarDiagnosticAgent** (2026-05-05 完, dogfood × 2 跑通, 样例库示例 csv_reader 进版本控)
- [x] **PlanDiagnosticAgent + plan_template 规范** (2026-05-05 完, dogfood × 1 元跑通, 模板进 docs/standards/protocol/plan_template.md)
- [x] **HypothesisDeriverAgent** (2026-05-05 完, dogfood × 1 跑通, 5 条新假设入 data/services/doctor/hypotheses/. 加 write_hypothesis + submit_derivation_report 2 个派生专属业务工具)
- [x] **registry HealthArchive 接通 doctor.health_finding** (2026-05-06 完, V0 走 FindingArchive JSONL 双轨: yaml 按 task_id 分桶 + JSONL 按 entity 分桶. write_finding 工具自动双落, registry 失败不阻断主路径. V1 走 bus 事件)
- [x] **finding 聚合 V3 snapshot** (2026-05-06 完, 立 FindingArchive.aggregate_to_snapshot 方法. schema v3 拒打分铁律: 不打 severity, 不打 verdict='healthy/unhealthy'. 跟 V2 HealthSnapshot 双轨独立 — V2 含 severity 跟铁律冲突, 不强行兼容. 字段: finding_count + by_finding_kind 分桶 + applied_* union + findings_summary 各 commentary 前 200 字. 健康判定靠人/agent 读 commentary+concern, 不靠数字)
- [x] **A 类 8 上下文准备 worker 评估** (2026-05-06 完, 结论: 不应订阅, 现状 read_file 自取最优. 详 v3_workers_inventory_and_classification.md §五附)
- [ ] **现 V3 30 worker 收编**: 评估哪些可归为"规范型/假设型"实例, 重写或保留 (大工作)
- [ ] **测试基线 red/green**: 四 agent SPEC.test_baseline 现 () 占位, 待真用更多场景后校准
- [ ] **prompt/context engineering 规范统一**: 用户 2026-05-05 提的元任务, 留单独议
- [ ] **PlanDiagnosticAgent V1 动态验收**: 让 agent 按 plan.md 三节描述的入口跑, 抓真产出. 需框架支持 sandboxed bash/python 跑入口

## 核心接口

### 管线工厂（见 [pipeline.py](pipeline.py)）
- **`build_pipeline() -> PipelineSpec`** — Format 诊断管线（9 节点）— [pipeline.py:43](pipeline.py#L43)
- **`build_pipeline_topology_pipeline() -> PipelineSpec`** — Pipeline 拓扑诊断管线（7 节点）— [pipeline.py:239](pipeline.py#L239)
- **`build_router_pipeline() -> PipelineSpec`** — Router 诊断管线 — [pipeline.py:444](pipeline.py#L444)

### Workers — Format 诊断子域（见 workers/format/）
- **`FormatExtractorWorker`** — 从源码提取 Format 定义 + 引用位置 — workers/format/format_extractor.py
- **`SignatureDiffWorker`** — HARD Anchor，无正式定义 → FAIL EMIT 短路 — workers/format/signature_diff.py
- **`FiveElementCheckWorker`** — 五要素：域前缀 / 常量名 / 行内描述 / 定义于 formats.py / ≥1 处引用 — workers/format/five_element_check.py
- **`TagCoverageWorker`** — 命名规范：全小写 / 含语义类型 tag — workers/format/tag_coverage.py
- **`ParentChainWorker`** — FORMAT_IN / FORMAT_OUT 使用者存在性 — workers/format/parent_chain.py
- **`CompositeFormatCheckWorker`** — composite 的 components 合法性 — workers/format/composite_format_check.py
- **`ExamplePresenceCheckWorker`** — Format.examples 非空且有意义 — workers/format/example_presence_check.py
- **`FormatContextualAuditWorker`** — LLM 全语境审计（含上下游 Router 源码 + F-01/F-06/F-08 标准）— workers/format/format_contextual_audit.py
- **`HealthWriterWorker`** — fan-in 汇聚 + 等级评定（A/B/C/D/F）— workers/format/health_writer.py

### Workers — Router 诊断子域（见 workers/router/）
- `RouterExtractorWorker` / `RouterSignatureWorker` / `RouterContextCollectorWorker` / `RouterDeterministicCheckWorker` / `RouterContextualAuditWorker` / `RouterHealthWriterWorker`

### Workers — Pipeline 诊断子域（见 workers/pipeline/）
- `PipelineSpecLoaderWorker` — 从 pipeline.py 调用 build_*() 加载 PipelineSpec
- `PipelineStructuralCheckWorker` — no_entry / isolated / dead_end / cycle / duplicate_edge
- `PipelineFormatContractCheckWorker` — format_break / composite_missing / granted_tag_chain
- `PipelineMaturityCheckWorker` — maturity_consistency（短板原则）
- `PipelineSoftHardCheckWorker` — soft_hard_pairing（P-07）
- `PipelineNarrativeCheckerWorker` — LLM 叙事连贯性 / 意图对齐
- `PipelineTopoHealthWriterWorker` — fan-in 汇聚
- `PipelineTopologyCheckWorker` — 拓扑诊断旧整合入口（兼容 `run_pipeline_checks` 调用）
- `PipelineLineageWorker` — B2 跨管线 format 产消图提取

### Workers — Blackboard 诊断子域（见 [workers/blackboard/](workers/blackboard/) · New World Diagnostics Phase B · 2026-04-20）

针对新 Material/Worker/Team 体系的**订阅图级**诊断, 区别于 Format/Router/Pipeline 三子域的**单对象**诊断. 6 Worker 共同订阅 `doctor.blackboard.audit_request` (kind.source), 各产独立 kind.sink 报告:

- **`MaterialKindLegalityWorker`** — F-19 / F-16 Material kind 合法性 (source 无 producer / internal 双向 / sink 无 consumer / 缺 kind 标) — [workers/blackboard/material_kind_legality.py](workers/blackboard/material_kind_legality.py)
- **`FormatInModeCheckerWorker`** — R-24 `FORMAT_IN = list[str]` 必显式声明 `FORMAT_IN_MODE = "and"|"or"` — [workers/blackboard/format_in_mode_checker.py](workers/blackboard/format_in_mode_checker.py)
- **`VerdictOutputFlatCheckerWorker`** — R-23 `return Verdict(output={"<format_id>": ...})` 嵌套反模式粗扫 — [workers/blackboard/verdict_output_flat_checker.py](workers/blackboard/verdict_output_flat_checker.py)
- **`OrphanWorkerScannerWorker`** — Q4 孤儿: Worker 订阅的 Material 无 producer 且非 `kind.source` — [workers/blackboard/orphan_worker_scanner.py](workers/blackboard/orphan_worker_scanner.py)
- **`UnconsumedMaterialScannerWorker`** — Q4 冗余: Material 有 producer 但无 consumer 且非 `kind.sink` — [workers/blackboard/unconsumed_material_scanner.py](workers/blackboard/unconsumed_material_scanner.py)
- **`EmitAsNewJobCheckerWorker`** — R-25 子 job 发射合规 (需 DESCRIPTION/docstring 说明用途, 防滥用) — [workers/blackboard/emit_as_new_job_checker.py](workers/blackboard/emit_as_new_job_checker.py)

**共享**: [`workers/blackboard/_shared.py`](workers/blackboard/_shared.py) · 动态 import Team + 订阅图构建工具 (`load_team_workers` / `load_team_materials` / `build_subscription_graph`). 当前静态 AST 未接入, runtime import 为主.

**验证**: tests/doctor/test_blackboard_workers.py · 19 smoke 覆盖 Worker 注册 / 金标 Team 0 违规 / 异常请求 / 真实违规捕获能力.

### Run.py 内 passthrough Worker（3 个 bindings 内部细节）
- `_FormatLLMPassthroughWorker` / `_PassthroughWorker` / `_NarrativePassthroughWorker` — hard 诊断模式 LLM 占位，与 bindings 构建紧耦合，保留在 run.py 内（OMNI-024 ALLOW）

### 旧名兼容 shim
- `routers.py` — 22 个 `*Router` 名的 alias re-export，外加 AST 工具函数（`_is_format_call` 等）re-export 自 `_archive/routers_legacy.py`
- `pipeline_topology.py` — 2 个 `*Router` alias + `Finding/CheckContext/run_pipeline_checks/PipelineLineage/...` re-export 自 `_archive/pipeline_topology_legacy.py`

### 检查引擎（原 `pipeline_topology.py`，现归档到 [_archive/pipeline_topology_legacy.py](_archive/pipeline_topology_legacy.py)）
- **`Finding`** dataclass — check_id / level / location / observation / implication / cross_refs
- **`CheckContext`** — 一次计算、所有检查共享的图结构
- **`run_pipeline_checks(spec, enabled=None, disabled=None) -> list[Finding]`** — 带注册表模式的 11 条检查
- 通过 shim `from ...doctor.pipeline_topology import Finding, run_pipeline_checks` 继续可用

## 架构决策

### D1 — 三条独立管线（Format / Router / Pipeline），而非一条"全诊断"

为什么分开：
- 三种对象诊断维度差异大（Format 看字段 / Router 看 class + method / Pipeline 看图结构）
- 用户调 Doctor 时通常已经知道要诊断什么（"我新写了一个 Format" vs "我加了一条 edge"）
- 独立管线方便 CI 按需触发（PR 只改 Router → 只跑 Router 管线）

三条管线共享同一套 Anchor 短路 + fan-out + fan-in 模板，保证架构一致。

### D2 — 诊断管线都是 fan-out 并行 + fan-in 汇聚

每条管线拓扑：
```
entry (Anchor HARD)
  ├─ PASS → 检查器A
  ├─ PASS → 检查器B         (并行)
  ├─ PASS → 检查器...
  └─ FAIL → EMIT 最小健康档案 (短路)
                ↓
            health_writer (fan-in 汇聚)
```

理由：
- 每个检查器产物独立（不共享 finding list），天然可并行
- 汇聚节点 health_writer 的 in_degree=6（Format 管线）/5（Pipeline 拓扑）/4（Router）— runner 的 fan-in 等待机制在此被实测
- Anchor 的 FAIL 路径走 EMIT 短路，避免"下游检查器空跑"

### D3 — Finding 不打分，只语义标签（blocking / degrading / advisory）

早期设计打 0-1 健康分，问题：
- 打分主观（5 个问题 vs 1 个严重问题哪个分更低？）
- 数字掩盖语义（0.7 的管线到底哪里不对？）
- 无法跨管线比较（不同管线检查项数量不同）

改为 `Finding.level` 四级语义：
- `blocking` — 结构性问题，阻止正确执行
- `degrading` — 质量问题，不阻止执行但降低可靠性
- `advisory` — 建议改进，不影响当前执行
- `info` — 纯统计/记录

`health_writer` 聚合时：若 blocking ≥1 → F 级；degrading ≥3 → D；advisory 只影响 B/C 区分。

`@property severity` 提供向后兼容（映射到 CRITICAL/HIGH/MEDIUM/INFO）。

### D4 — 检查器注册表：每条检查独立 `PipelineCheckSpec`，按 ID 开关

Pipeline 拓扑诊断有 11 条检查（见 pipeline_topology.py 文件头 docstring）。每条独立注册，`run_pipeline_checks(spec, enabled=None, disabled=None)` 可按 ID 显式开/关。

理由：
- 某些检查在特定场景是误报（如 `cycle` 对 feedback 管线要豁免）
- CI 不同阶段需要不同严格度
- 新检查加入不影响旧检查

一次 `_build_context` 预计算图结构（node_map / out_edges / in_edges / reachable / fan_in_nodes / feedback_pairs），所有检查共享，O(N) 而非 O(N·C)。

### D5 — LLM 检查器（desc_eval / narrative）读源码级上下文，不做纯字符串分析

`FormatContextualAuditRouter` / `PipelineNarrativeCheckerRouter` 都是 LLM 检查器。与 rule 检查器不同，它们喂给 LLM：
- format + 上下游 Router **完整源码**（不截断 — 符合铁律 A）
- 相关标准文档（F-01/F-06/F-08/FA）
- 语境（谁消费、谁产出）

产出定性报告（何处违反标准 / 可能的反模式 / git 存档留证据）。不给分数，给"明确可改进项"。

这是 Doctor 的"专家审判"层，rule 检查只抓结构，LLM 抓语义。

### D6 — SignatureDiff 的 FAIL 走 EMIT 短路，不污染 fan-in 的空闲检查器

当 Format ID 在源码找不到正式定义时：
- 传统设计：signature_diff FAIL → 六路检查器全部"空跑"（没有定义拿什么查？）→ health_writer 收到 6 条 blocking finding
- Doctor 设计：signature_diff FAIL → 直接 EMIT（走 RouteAction.EMIT，不走 fan-out 边）→ 产出"最小健康档案"（含 "Format 未找到定义" 一条）

理由：
- 节省 LLM 调用（desc_eval 很贵）
- 语义清晰：下游检查器不该收到"我都没找到的东西"
- EMIT 直接出边界，不经 health_writer（因为没有东西可汇聚）

这也是 runner `EMIT` 动作的典型应用场景。

### D7 — Router 诊断专有 `RouterContextCollector` 前置节点

Router 检查比 Format 检查复杂：Router 绑定 FORMAT_IN/OUT/TOOLS/DESCRIPTION 四要素，需要"看全上下文"才能判断确定性 / 完整性。

新增 `RouterContextCollectorRouter` 节点：
- 抓 Router class 源码 + `_DESCRIPTION` / `_SYSTEM` 常量 + tool schemas + 相关 Format 定义
- 产出"一口气喂给 LLM 的完整 dossier"，供下游 RouterContextualAudit 使用

这是 `runtime/agent_crystallize/` 的 `build_agent_loop_trace()` 思路的复刻版（一次收集、多次消费）。

## 数据流 / 拓扑

### Format 诊断管线（9 节点，Format 主干）

```
[输入] doctor.material.request (format_id + source_root)
   ↓
format_extractor (RULE)             → doctor.material.extracted
   ↓
signature_diff (HARD Anchor)
   ├─ PASS → fan-out 到 6 个检查器
   │       ├─ five_element_check (RULE)
   │       ├─ tag_coverage (RULE)
   │       ├─ parent_chain (RULE)
   │       ├─ composite_format_check (RULE)
   │       ├─ example_presence (RULE)
   │       └─ desc_eval (LLM)
   │           ↓ 六条 edge 入
   │       health_writer (fan-in + 等级评定)
   │           ↓
   │       [输出] doctor.material.health-record
   └─ FAIL → EMIT 最小健康档案（直接出边界）
```

### Pipeline 拓扑诊断管线（7 节点）

```
[输入] diag.team.request (pipeline_py_path)
   ↓
pipeline_spec_loader (HARD Anchor，调 build_*() 加载)
   ├─ PASS → fan-out 到 5 检查器
   │       ├─ pipeline_structural_check (RULE, no_entry/isolated/dead_end/cycle/dup)
   │       ├─ pipeline_format_contract (RULE, format_break/composite/granted_tag)
   │       ├─ pipeline_maturity_check (RULE, 短板原则)
   │       ├─ pipeline_soft_hard_check (RULE, P-07)
   │       └─ pipeline_narrative_check (LLM, 叙事连贯)
   │           ↓
   │       pipeline_topo_health_writer (fan-in)
   │           ↓
   │       [输出] doctor.pipeline.health-record
   └─ FAIL → EMIT 最小健康档案
```

### Router 诊断管线（类似结构）
```
router_extractor → router_signature → router_context_collector
   → [fan-out: deterministic_check / contextual_audit (LLM)]
   → router_health_writer
```

数据落盘（Phase 2 目标是就近到 `.omni/health/`，当前仍集中）：
- `data/health/formats/<fmt_id>.json` — Format 健康档案
- `data/health/routers/<router_id>.json`
- `data/health/pipelines/<pipe_id>.json`
- `data/health/_summary.json` — dashboard 消费

## 已知局限

1. **`.omni/health/` 就近写盘未完成** — Phase 2 设计是让 Format / Router / Pipeline 的健康档案就近写到所属包的 `.omni/health/` 子目录，dashboard 才能按包聚合。当前所有 health_record 写集中目录（`data/health/*`），背离分布式文档规范。**升级路径**：HealthWriter 根据 target 路径推导 `.omni/health/` 位置 + dashboard 支持两种读取。

2. **LLM 检查器（desc_eval / narrative_check）预算消耗高** — 每条管线诊断调 1-2 次 LLM，大管线（30+ 节点）`narrative_check` 要喂所有节点 description + edges，成本可观。**缓解**：LLM 检查器可按 ID 关闭（注册表设计留了口子）。**根治**：缓存（针对未改动的 Format/Pipeline，health_record 有效期 7 天不重跑）。

3. **Router 诊断管线成熟度落后 Format 管线** — Format 诊断走过数百次实战，Router 诊断只跑过少量基准。`RouterDeterministicCheckRouter` 目前只检查"是否有 LLM 调用"，不检查"deterministic Router 是否真的 deterministic"（需要 run 两次比较输出）。

4. **未接入 LAP `crystallize` 回路** — Doctor 产出的 Finding 理论上应该反哺 `runtime/agent_crystallize/` 的 SpecPatch 候选（如"此 Format description 不够清晰"直接转 crystallize patch）。目前 Finding 只落 health_record，没有自动生成 crystallize patch 的通道。

5. **Finding.level 的划分靠作者判断而非标准** — `blocking` vs `degrading` 的边界每个检查器自己判断，缺乏全局对齐。未来应定义"什么算 blocking"的判别表。

## 十 · Team 专属（Clean Migration V2）

### 目录结构

```
doctor/
├── __init__.py                  # workers/ 聚合 + routers.py / pipeline_topology.py compat shim re-export
├── DESIGN.md                    # 本文件
├── formats.py                   # 11 Material 定义（Material kind 100% 覆盖：source×2 / internal×7 / sink×2）
├── pipeline.py                  # 三个 build_*_pipeline 函数（零改动）
├── run.py                       # bindings 构建 + CLI 入口 + 3 passthrough Worker
├── routers.py                   # compat shim: 22 Router 名 alias + AST 工具 re-export
├── pipeline_topology.py         # compat shim: 2 Router 名 alias + Finding/run_pipeline_checks re-export
├── checks/                      # 纯函数 check library (format_in_consumption 等; 非 Worker, 保持)
├── workers/
│   ├── __init__.py              # ALL_WORKERS (24) = ALL_WORKERS_FORMAT + ROUTER + PIPELINE
│   ├── format/                  # 9 Worker (Format 诊断管线)
│   │   ├── __init__.py          # ALL_WORKERS_FORMAT
│   │   ├── format_extractor.py
│   │   ├── signature_diff.py
│   │   ├── five_element_check.py
│   │   ├── tag_coverage.py
│   │   ├── parent_chain.py
│   │   ├── composite_format_check.py
│   │   ├── example_presence_check.py
│   │   ├── format_contextual_audit.py
│   │   └── health_writer.py
│   ├── router/                  # 6 Worker (Router 诊断管线)
│   │   ├── __init__.py          # ALL_WORKERS_ROUTER
│   │   ├── router_extractor.py
│   │   ├── router_signature.py
│   │   ├── router_context_collector.py   # 覆盖 _find_neighbors 以扫 _archive/*_legacy.py
│   │   ├── router_deterministic_check.py
│   │   ├── router_contextual_audit.py
│   │   └── router_health_writer.py
│   └── pipeline/                # 9 Worker (Pipeline 拓扑诊断 + Lineage + 旧入口)
│       ├── __init__.py          # ALL_WORKERS_PIPELINE
│       ├── pipeline_spec_loader.py
│       ├── pipeline_structural_check.py
│       ├── pipeline_format_contract_check.py
│       ├── pipeline_maturity_check.py
│       ├── pipeline_soft_hard_check.py
│       ├── pipeline_topo_health_writer.py
│       ├── pipeline_narrative_checker.py
│       ├── pipeline_topology_check.py
│       └── pipeline_lineage.py
└── _archive/
    ├── README.md                # 归档说明
    ├── routers_legacy.py        # 原 routers.py，业务逻辑源，Worker 子类 diamond 继承
    └── pipeline_topology_legacy.py   # 原 pipeline_topology.py，Finding/CheckContext/run_pipeline_checks 源
```

### Worker 粒度（R-18 / Patch-1）

24 个主 Worker + 3 个 run.py 内 passthrough = 27 Router，全部按原粒度保留。每个 Worker 对应一条管线节点，FORMAT_IN/OUT 边界清晰，不合并不拆分。

### Material kind 分配（F-19，11/11 覆盖）

| Material | Kind |
|----------|------|
| `doctor.material.request` | kind.source |
| `doctor.material.extracted` | kind.internal |
| `doctor.material.acc` | kind.internal |
| `doctor.material.health-record` | kind.sink |
| `diag.worker.request` | kind.source |
| `diag.worker.extracted` | kind.internal |
| `diag.worker.sig-checked` | kind.internal |
| `diag.worker.context` | kind.internal |
| `diag.worker.det-checks` | kind.internal |
| `diag.worker.audit` | kind.internal |
| `diag.worker.health-record` | kind.sink |

**注**: `diag.team.*` 系列 Material 仅在 `pipeline.py` `TransformerSpec` 引用，未在 `formats.py` 声明为 Material 对象（已有局限，非本次迁移引入；待后续补齐）。

### Worker 实现策略（diamond 继承）

每个 Worker 文件通过 Python 多重继承组合 `Worker` + 归档 Legacy 类：

```python
from omnicompany.packages.services.omnicompany import Worker
from omnicompany.packages.services.doctor._archive.routers_legacy import (
    FormatExtractorRouter as _Legacy,
)

class FormatExtractorWorker(Worker, _Legacy):
    """FORMAT_IN=doctor.material.request → FORMAT_OUT=doctor.material.extracted。"""
    pass
```

MRO: `[FormatExtractorWorker, Worker, _Legacy, Router, ABC, object]` — `Worker` 和 `_Legacy` 共享 `Router` 祖先，`issubclass(FormatExtractorWorker, Worker) == True`。业务逻辑全部保留在 legacy 源码里，新 Worker 文件只是命名层。

### Passthrough Worker（3 个 bindings 内部细节）

`run.py` 内 `_FormatLLMPassthroughWorker` / `_PassthroughWorker` / `_NarrativePassthroughWorker` 继承 `Worker`，不拆文件，因为它们是 hard 诊断模式下与 bindings 构建紧耦合的 LLM 占位（OMNI-024 ALLOW 注释说明）。

## 参考资料

- 关联管线：[pipeline.py](pipeline.py)（三条 build_*_pipeline 函数）
- Worker 实现：[workers/](workers/)
- 归档 Legacy：[_archive/](_archive/)（`routers_legacy.py` + `pipeline_topology_legacy.py` 业务逻辑源）
- 关联 Material：[formats.py](formats.py)（doctor.material.* / diag.worker.*）
- 关联 standards：`docs/standards/material.md`（F-01/F-06/F-08 引用）
- 关联 standards：`docs/standards/pipeline-narrative.md`（叙事性检查标准）
- 关联 guardian：[../guardian/DESIGN.md](../../_core/guardian/DESIGN.md)（guardian 扫源码合规，doctor 扫运行时健康）
- 关联 repair：[../repair/DESIGN.md](../../_core/repair/DESIGN.md)（Doctor 下游的修复候选生成）
- 关联 runtime：[../../../runtime/exec/DESIGN.md](../../../../runtime/exec/DESIGN.md)（诊断管线本身也走 PipelineRunner）
- 关联 migration_log: [`docs/plans/[2026-04-19]BLACKBOARD-ARCHITECTURE/migration_log.md`](../../../../../docs/plans/%5B2026-04-19%5DBLACKBOARD-ARCHITECTURE/migration_log.md)（Clean Migration 11 项标准）
