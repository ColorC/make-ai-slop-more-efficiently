<!-- [OMNI] origin=ai-ide domain=services/repair ts=2026-05-04T13:55:00Z type=doc status=active agent=ai-ide belongs_to_service=repair -->
<!-- [OMNI] summary="repair service 自我叙事 README — omnicompany 修理员. 消费 doctor Finding 迭代调 LLM 产 Format/Router 修复补丁, 跟 doctor 形成诊断→修复闭环" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §四 模板严格写. 抽核心目的到 README, DESIGN 留 12 Worker 架构详述" -->
<!-- [OMNI] tags=readme,repair,core,self-narrative -->
<!-- [OMNI] material_id="material:services._core.repair.readme.self_narrative.md"-->

# repair · omnicompany 修理员

> 消费 doctor 产出的 Finding, 迭代调 LLM 产 Format / Worker 修复补丁并验证, 直到诊断为 A 级或达迭代上限. 跟 doctor 形成"诊断→修复"闭环, 12 Worker 分两子管线 (Format 修复 3 / Worker 修复 9).

---

## 这是什么

repair 是 omnicompany 的**修理员 service**. 它**不自己诊断**, 只消费 [doctor](../../_diagnosis/doctor/) 产出的 Finding (通过 `format_id` + `source_root` 间接引用), 然后**迭代调 LLM** 生成修复补丁 (delta / diff), 应用到源码, **重新调 doctor 诊断**, 直到 grade 升 A 或达 max_iterations.

形态: 12 Worker 分**两个子管线**:
- **Format 修复子管线** (3 Worker, `repair.fmt.*`) — 1 节点 AgentLoop 包迭代 (单体, Phase 1 重构目标拆为 R-19 Agent Worker 三件套)
- **Worker 修复子管线** (9 Worker, `diag.repair.*`) — 线性 9 节点 (DescPlanner / FailPathPlanner / GrantedTagsPlanner / PatchMerger / PatchValidator / PatchApplier / etc)

跟其他修复 / 诊断 service 的边界:
- **doctor** 看 — 诊断 Format/Worker/Team 健康, 产 Finding (但不动代码)
- **repair** 修 — 消费 doctor Finding, 产修复补丁 + 应用 + 重诊断
- **guardian** 跟 repair **不同**: guardian 主要 warn, 修是 [tow_truck.py](../guardian/tow_truck.py) (轻量改名 / 挪位 / quarantine), 不调 LLM 修代码内容
- **lap_auditor / semantic_auditor** 产 Finding (语义级), 后续接 repair 形成更广闭环 (远景)

## 解决什么 / 不解决什么

**解决**:
- Format description 不合格 / tags 缺失 / parent 断链 等结构问题的**自动修复** (Format 修复)
- Worker DESCRIPTION 太短 (R-01) / FAIL 路径缺失 (R-05) / granted_tags 缺失 (R-07) 等 B 类问题**辅助补全** (Worker 修复)
- 消费 doctor Finding 不自己诊断 (职责分离)
- 迭代改进 (一次 LLM 修不好 → 重诊断 → 再修 → 直到 A 或上限)

**不解决**:
- 异步 Worker 改造 / `FORMAT_IN: list[str]` (A/C 类问题) — Phase 2 backlog
- 业务代码修复 (由 domain Team 各自负责)
- 无 doctor 情况下的修复 (强依赖 doctor health record)
- Pipeline edges 修改 (Phase 2 backlog)

## 设计目的与最终目标

**设计目的**: 让 omnicompany 的"诊断 → 修复" 形成自动闭环. 没 repair, doctor 产的 Finding 只能堆 REGISTRY, 真正修要靠人手. 加 repair, 大量 B 类机械问题能 LLM 自动修, 人只审最后 patch.

**理论锚点**: omnicompany 主轴第二件能力"诊断修复" 的"修复" 那一半 (诊断那一半是 doctor). 没 repair, "自维护" 缺一条腿.

**最终目标** (当下能认知的):
- Phase 1: `FormatRepairAgentLoopWorker` 拆 R-19 Agent Worker 三件套 (Context Script + LLM + Tool Script), 不再单体 while
- Worker 修复子管线进 build_pipeline 主 Team (当前是 `run_router_repair()` 辅助函数)
- 扩到 A / C 类问题 + Pipeline edges 修改 (Phase 2)
- 升级 Diagnosis Agent Worker (R-21) 先质疑 Finding 再修, 防"修了更糟"
- 接 lap_auditor / semantic_auditor Finding (远景, 让语义 Finding 也能自动修)

## 规划

- **当前 V2** (active, 2026-04-20 Stage 2 Clean Migration, 12 Worker)
- **下一步**: Phase 1 新 runtime 到位后 `FormatRepairAgentLoopWorker` 重构为 R-19 三件套
- **远景**: 扩 A/C 类 + 接语义 Finding + Diagnosis Agent Worker 质疑机制

## 构成

- 入口与 Team → [pipeline.py](pipeline.py) (`build_pipeline()` 1 节点极简, 内部包 Format 修复迭代) + [run.py](run.py) (`build_bindings()`)
- Materials → [formats.py](formats.py)
- 共享工具 → [workers/_shared.py](workers/_shared.py) (AST / diff 解析应用 / 路径常量)

### Format 修复子管线 (3 Worker, `repair.fmt.*`)

- `FormatRepairAgentLoopWorker` ([workers/format_repair_agent_loop.py](workers/format_repair_agent_loop.py)) — 单体 AgentLoop 包迭代 (Phase 1 拆三件套)
- `RepairPlannerWorker` ([workers/repair_planner.py](workers/repair_planner.py)) — LLM 产 delta
- `FormatPatcherWorker` ([workers/format_patcher.py](workers/format_patcher.py)) — 应用 delta

### Worker 修复子管线 (9 Worker, `diag.repair.*`)

按 R-01 (DescriptionPlanner) / R-05 (FailPathPlanner) / R-07 (GrantedTagsPlanner) 三类 B 问题拆独立规划器 + 后续 PatchMerger / PatchValidator / PatchApplier + IssueLoader / RouterSourceLoader / Rediagnose. 详见 [DESIGN §核心接口](DESIGN.md#核心接口).

- 旧名 compat shim → [routers.py](routers.py) (`*Router` 别名 + `run_router_repair()` 驱动函数)
- 归档 → [_archive/](_archive/) (Diamond 实现归档)

技术架构详述见 [DESIGN.md](DESIGN.md), 操作手册见 [SKILL.md](SKILL.md).

## 想了解更多

- 架构 → [DESIGN.md](DESIGN.md)
- 操作手册 → [SKILL.md](SKILL.md)
- 上游 doctor → [../../_diagnosis/doctor/README.md](../../_diagnosis/doctor/README.md)
- 跟 guardian tow_truck 对比 → [../guardian/tow_truck.py](../guardian/tow_truck.py)
- Worker 设计单 R-01/R-05/R-07/R-18/R-19 → [docs/standards/worker.md](../../../../../../docs/standards/concepts/worker.md)
- 项目根叙事 → [../../../../../README.md](../../../../../../README.md)
