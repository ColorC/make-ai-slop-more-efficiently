<!-- [OMNI] origin=claude-code domain=services/repair ts=2026-05-04T13:55:00Z type=doc status=active belongs_to_service=repair -->
<!-- [OMNI] material_id="material:core.repair.design.document.md" -->

# repair · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部** (12 Worker 接口 / 决策 / 数据流 / 局限).
>
> 形态: 行政部 Team (核心基础设施 · Stage 2 Clean Migration 完成 2026-04-20).
> Clean Migration 完成 2026-04-20: `workers/` 子目录 (12 Worker) + Worker 基类 + Material kind + _archive 归档.

## 状态
- **版本**: V2 (2026-04-20 · Clean Migration / Stage 2 完全迁移)
- **成熟度**: active
- **下一步**: Phase 1 新 runtime 到位后, `FormatRepairAgentLoopWorker` 正式按 R-19 重构为 Context Script + LLM + Tool Script 三件套 Agent Worker

## 核心接口

- **`build_pipeline()`** → [pipeline.py](pipeline.py): 1 节点极简管线 (只有 `format_repair_loop`)
- **`build_bindings()`** → [run.py](run.py): 映射 `format_repair_loop` → `FormatRepairAgentLoopWorker` 实例
- **Worker 清单** ([workers/](workers/)): 共 **12 Worker**, 分两个子管线

### Format 修复子管线 (3 Worker · repair.fmt.*)

| Worker | FORMAT_IN | FORMAT_OUT | 文件 |
|---|---|---|---|
| `RepairPlannerWorker` | repair.fmt.attempt | repair.fmt.attempt | [workers/repair_planner.py](workers/repair_planner.py) |
| `FormatPatcherWorker` | repair.fmt.attempt | repair.fmt.attempt | [workers/format_patcher.py](workers/format_patcher.py) |
| `FormatRepairAgentLoopWorker` | repair.fmt.request | repair.fmt.report | [workers/format_repair_agent_loop.py](workers/format_repair_agent_loop.py) |

### Router 修复子管线 (9 Worker · diag.repair.*)

| # | Worker | FORMAT_IN | FORMAT_OUT | 文件 |
|---|---|---|---|---|
| 1 | `IssueLoaderWorker` | diag.repair.request | diag.repair.issue-list | [workers/issue_loader.py](workers/issue_loader.py) |
| 2 | `RouterSourceLoaderWorker` | diag.repair.issue-list | diag.repair.source-context | [workers/router_source_loader.py](workers/router_source_loader.py) |
| 3 | `DescriptionPlannerWorker` (R-01) | diag.repair.source-context | diag.repair.desc-patch | [workers/description_planner.py](workers/description_planner.py) |
| 4 | `FailPathPlannerWorker` (R-05) | diag.repair.desc-patch | diag.repair.fail-patch | [workers/fail_path_planner.py](workers/fail_path_planner.py) |
| 5 | `GrantedTagsPlannerWorker` (R-07) | diag.repair.fail-patch | diag.repair.tags-patch | [workers/granted_tags_planner.py](workers/granted_tags_planner.py) |
| 6 | `PatchMergerWorker` | diag.repair.tags-patch | diag.repair.patch-plan | [workers/patch_merger.py](workers/patch_merger.py) |
| 7 | `PatchValidatorWorker` | diag.repair.patch-plan | diag.repair.validated-patch | [workers/patch_validator.py](workers/patch_validator.py) |
| 8 | `PatchApplierWorker` | diag.repair.validated-patch | diag.repair.applied | [workers/patch_applier.py](workers/patch_applier.py) |
| 9 | `RediagnoseWorker` | diag.repair.pending | diag.repair.result | [workers/rediagnose.py](workers/rediagnose.py) |

- 共享工具: [workers/_shared.py](workers/_shared.py) (AST 分析 / diff 解析应用 / 路径常量)
- 兼容 shim: [routers.py](routers.py) 旧名 `*Router` / `FormatRepairAgentLoop` 作为 `*Worker` 别名保留 + 保留驱动函数 `run_router_repair()`
- 归档: [_archive/README.md](_archive/README.md) · [_archive/routers_legacy.py](_archive/routers_legacy.py) · [_archive/router_repair_legacy.py](_archive/router_repair_legacy.py)

## 架构决策

### D1 — Format 修复子管线: 1 节点 AgentLoop 包迭代 (当前)

`format_repair_loop` 对外是单个 pipeline node, 内部封装 "诊断 → LLM 规划 → Patch → 重新诊断" 循环。

**理由** (当前):
- 外部调用者只需 `repair.fmt.request` → `repair.fmt.report`, 不关心内部迭代
- 循环逻辑紧耦合 (诊断结果直接决定规划, 不适合拆成跨 pipeline edge)

**局限** (Phase 1 重构目标):
- 违反 R-19 Agent Worker 三件套原则 — 目前是**单体 while 循环** (对齐新设计会拆为 Context Script + LLM Worker + Tool Script Worker)
- 归为 R-19 反模式 RA-13 "Agent Worker 假单体" 过渡期豁免

### D2 — Router 修复子管线: 9 Worker 线性 (当前)

Router 修复 9 Worker 按 R-01 / R-05 / R-07 三类 B 问题拆成独立规划器 (DescPlanner / FailPathPlanner / GrantedTagsPlanner), 保持单一职责:

- **优点**: 每个 Planner 只看自己类型的 issue, prompt 极小, LLM 专注度高
- **缺点**: 当前通过 `run_router_repair()` 辅助函数串联 (非原生 pipeline 形态), 未进 `build_pipeline()` 主 Team

Phase 1 将考虑把 9 Worker 正式编为第二条 pipeline, 与 Format 修复 pipeline 并列。

### D3 — 只修 Format+Router B 类 (当前范围)

- Format 修复: description / tags / examples (改 metadata, 不影响行为, 相对安全)
- Router 修复: R-01 DESCRIPTION 补全 / R-05 FAIL 路径补充 / R-07 granted_tags 添加 (最小行为影响)
- **不在范围**: A 类 (FORMAT_IN list[str]) / C 类 (async run()) / Pipeline edges 修改 (Phase 2 backlog)

### D4 — 依赖 doctor 的 Finding (职责分离)

repair 不自己做诊断, 只消费 doctor 输出:
- `repair.fmt.request` 内部调 doctor 的 build_pipeline() 获取当前 health_record
- `diag.repair.request` 通过 `IssueLoaderWorker` 重跑 doctor 确定性诊断链

这样 repair 与 doctor 解耦 (repair 可独立升级, 不需要 doctor 协同)。

### D5 — 最大迭代次数默认 3 (硬上限 Q2 预算)

对齐 Q2 `max_workers_per_job` 基线(1000)但 repair 业务上设 3 足够 (LLM 多次修不好 → 说明问题结构性)。
超过上限 → 返回 `success=False` 报告, 手动介入。

### D6 — Agent Worker 迁移路径 (Phase 1 ready)

`FormatRepairAgentLoopWorker` 是未来 R-19 Agent Worker 的**典型原型**:
- Context Script Worker: 组装 "当前 Format 定义 + doctor Finding + 修复历史"
- LLM Worker: 基于上下文产 `delta` (description/tags/examples 修改建议)
- Tool Script Worker: 调 doctor 重诊断 (tool_call) + Patch 源码 (tool_call)

Phase 1 重构 = 把当前单体类拆成上述三件套 + 迷你 stock。

## 数据流 / 拓扑

### Format 修复子管线 (对外)

```
[输入] repair.fmt.request (kind.source · format_id + source_root + max_iterations)
   ↓
FormatRepairAgentLoopWorker (1 node · 内部迭代)
   ├── 内部迭代:
   │   ├── doctor.build_pipeline().run(format_id) → health_record
   │   ├── IF grade == "A" → break
   │   ├── RepairPlannerWorker (LLM) → delta (description/tags/examples)
   │   ├── FormatPatcherWorker → 应用 delta 到源码
   │   └── 重诊断, iter += 1
   └── 产出:
   ↓
[输出] repair.fmt.report (kind.sink · initial_grade/final_grade/iterations/success)
```

### Router 修复子管线 (内部 · run_router_repair 驱动)

```
diag.repair.request
   ↓ IssueLoaderWorker         (诊断链重跑, B 类问题提取)
diag.repair.issue-list
   ↓ RouterSourceLoaderWorker  (AST 深度上下文)
diag.repair.source-context
   ↓ DescriptionPlannerWorker  (R-01 LLM diff)
diag.repair.desc-patch
   ↓ FailPathPlannerWorker     (R-05 LLM diff)
diag.repair.fail-patch
   ↓ GrantedTagsPlannerWorker  (R-07 LLM diff)
diag.repair.tags-patch
   ↓ PatchMergerWorker         (合并三 diff)
diag.repair.patch-plan
   ↓ PatchValidatorWorker      (AST 验证安全性)
diag.repair.validated-patch
   ↓ PatchApplierWorker        (备份 + 写入 + 记录)
diag.repair.applied
```

`RediagnoseWorker` 独立辅助: 修复后重跑诊断对比 before/after grade, 当前未进 Router 修复子管线主线。

## 已知局限

1. **只修 Format B 类 + Router R-01/R-05/R-07** — A / C 类 + Pipeline edges 均 Phase 2 backlog。Router 行为变更风险高。
2. **FormatRepairAgentLoopWorker 单体** (违反 R-19) — Phase 1 重构目标, 拆为三件套。当前 RA-13 过渡期豁免。
3. **Router 修复子管线未进 build_pipeline()** — 当前通过 `run_router_repair()` 辅助函数驱动, 非原生 pipeline。Phase 1 可提升为第二条 pipeline。
4. **LLM 幻觉风险** — Planner 生成的 delta/diff 可能语义不对但 schema 合法, 导致 "修复成功"但新 Format/Router 更糟。Phase 1 升级为 Diagnosis Agent Worker (R-21) 先质疑 Finding 再修。

## 参考资料

- **代码**:
  - [pipeline.py](pipeline.py) · [run.py](run.py) · [formats.py](formats.py)
  - [routers.py](routers.py) (兼容 shim)
  - [workers/](workers/) · 12 Worker 独立文件 (Clean Migration 后结构)
  - [workers/_shared.py](workers/_shared.py) (AST / diff 共享工具)
- **新架构规范** (Stage 2 Clean Migration 依据):
  - [router.md R-18/R-19/R-20/R-21 / RA-13](../../../../../../docs/standards/concepts/worker.md)
  - [format.md F-19 Material kind](../../../../../../docs/standards/concepts/material.md)
  - [terminology.md §6 / §7 / §8](../../../../../../docs/standards/_global/terminology.md)
- **依赖 Team**: [doctor/DESIGN.md](../../_diagnosis/doctor/DESIGN.md) (消费 health_record · 诊断链复用)
- **迁移记录**: migration_log.md Team 5 · Stage 2 完全迁移
