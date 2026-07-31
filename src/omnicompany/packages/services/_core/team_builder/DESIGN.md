<!-- [OMNI] origin=claude-code domain=services/team_builder ts=2026-05-04T16:30:00Z type=doc status=active belongs_to_service=team_builder -->
<!-- [OMNI] material_id="material:core.team_builder.design_document.overview.md" -->

# team_builder · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部** (11 阶段工作流 / Material 清单 / workspace).
>
> 注: 本 DESIGN 用**非标结构** (中文数字"## 1/2/3/4/5/6/7"), 是该 service 自选 (Stage 2 时立). 不强改回标准七节. 后段还含旧 workflow_factory Clean Migration 归档参考内容.
>
> Team of Teams — 输入自然语言需求 → 输出通过全部验证的 L3.5 合规 Team 包 (agent-first 设计).
> 2026-04-23 A3 改造: workflow_factory → team_builder 改名 · agent-first 方向启动 · Diamond 归档作参考.

## 状态
- **版本**: V3 · agent-first 启动期 (2026-04-23)
- **成熟度**: active (骨架 · 待 agent 探针运行观测后精炼)
- **分型**: meta team — 产出其他 Team 包
- **下一步**: agent worker 阶段图设计 (几阶段未定 · 等探针跑起来观测)

## 1 · 核心职责

agent-first meta team — 输入自然语言需求, 产出合规 L3.5 Team 包 (DESIGN 骨架 + Worker 深化 + Material 深化 + Workspace 声明 + 契约审计 + 代码生成 + 注册). 详细工作流见 .omni/build_workflow.md.

## 2 · 输入 / 输出边界

- **入**: `team_builder.material.request_trigger` (CLI `--text` source material, 自然语言需求)
- **出**: `team_builder.material.team_design` (V1 · 草图深度; 后续 WorkerDesigner/MaterialDesigner/CodeGenerator 接续深化, 见 §3)
- **副作用**: 生成新 package 时**显式扩 Workspace** 写入 `src/omnicompany/packages/services/<new_pkg>/` (不自动)

## 3 · 数据流 · 11 阶段 agent-first 工作流

**核心哲学** (`docs/standards/agent_first.md`): 先搭完整 workspace (信息库, 宁滥毋缺) → agent 作探针 → 观测建档 → 按需提炼固化. **不预设理想管线**让 agent 照走.

| # | 阶段 | 入 → 出 material | 驱动 worker | HARD/SOFT/AGENT | 状态 |
|---|---|---|---|---|---|
| 0 | 需求承接 | request_trigger → origin_request | OriginRequestLoader | HARD | ✅ V1 |
| 1 | 意图研判 | origin_request → intent_analysis | IntentAnalyzer | SOFT LLM | ✅ V1 |
| 1' | 参考收集 (并行 1) | origin_request → team_references | ReferenceScout | SOFT v0 启发式 (可升 AGENT) | ✅ V1 |
| **2** | **规模研判 + 拆分** (大需求路径) | intent_analysis → scale_assessment / decomposition_plan | ScaleAssessor / DecompositionPlanner | SOFT LLM | ⏳ V2 |
| 3 | 草图架构 | [intent_analysis + team_references] → team_design | TeamArchitect | SOFT LLM (composite fan-in and) | ✅ V1 (骨架深度) |
| **4** | Worker 深化 × N | team_design + workspace_spec → worker_design_detailed × N | WorkerDesigner × N (独立上下文) | SOFT LLM | ⏳ V2 |
| **4'** | Material 深化 × M | team_design → material_design_detailed × M | MaterialDesigner × M (独立上下文) | SOFT LLM | ⏳ V2 |
| **5** | Workspace 规范化 | team_design → workspace_spec | WorkspaceDesigner | HARD | ⏳ V2 |
| **6** | 契约对齐 | workers + materials → contract_audit | ContractAuditor | HARD (P-13 + F-15 静态) | ⏳ V2 |
| **7** | 草图级完整验证 | 所有前阶段 material → design_validation_report | DesignValidator | HARD + SOFT 补判 | ⏳ V2 |
| 8 | 代码生成 | worker/material/workspace_detailed → code_package | CodeGeneratorLoop | AgentNodeLoop (参考 `routers_codegen_legacy.py`) | ⏳ V3 |
| 9 | 最终健康 (后验证) | code_package → doctor 三套自检 | Doctor (既有 L3 组件) | 既有 | ✅ |
| 10 | 注册上架 | code_package → pipelines registry | Registrar | HARD | ⏳ V3 |

### 3.1 小需求路径 (size=small, 直线)

`0 → 1 // 1' → 3 → (4 // 4' // 5) → 6 → 7 → 8 → 9 → 10`

### 3.2 大需求路径 (size=large, 递归拆分)

```
0 → 1 → 2 (ScaleAssessor 判 large)
      └→ 2' (DecompositionPlanner 产 decomposition_plan)
            └→ 子 team 1 (递归 team-builder)  ┐
            └→ 子 team 2 (递归 team-builder)  ├→ 契约 material 作子 team 间 FORMAT 接口
            └→ 子 team 3 (递归 team-builder)  ┘
            └→ 父组合层 (Phase 3 合成最终 team_design 链接子 team)
```

契约 material = 子 team 间 producer.FORMAT_OUT ≡ consumer.FORMAT_IN 的公共 Material id + schema. 由 DecompositionPlanner 声明, 子 team-builder 跑时作为约束注入.

## 4 · Material 清单 (V1 · 9 类 → V2 · 16 类)

**已有 V1 (9 类, formats.py 已注册)**:
- request_trigger (source) · origin_request · intent_analysis (中间) · team_references · team_design · worker_design · material_design · agent_worker_design · workspace_design

**新加 V2 (7 类 · 本次落)**:
- scale_assessment · decomposition_plan · worker_design_detailed · material_design_detailed · workspace_spec · contract_audit · design_validation_report

schema 详见 `formats.py::TB_A3_MATERIALS`.

## 5 · Workspace

见 .omni/workspace.yaml. 写紧 (自身 package + data/services/team_builder/) · 读宽 (READ_ANY) · bash_cwd 项目根.

**生成新 Team 时 workspace 扩展**: 每次递归启动子 team-builder, 构造临时 Workspace 含子 package 写入范围 (不继承扩展, 必须显式).

## 6 · 已知局限

- 现 V1 产出只到**草图深度** (DESIGN.md 只给七节标题 + worker/material 一行 brief, 无 FORMAT_IN/OUT schema / 无 routes / 无 prompt 模板), 离可运行 package 还差 Phase 4-8
- LLM 规范合规需后置 HARD 兜底 (V1 实跑暴露 workspace 路径错 · impl_type 自拟 等)
- ReferenceScout V0 是硬编码启发式清单 11 条, 未按 intent.domain 动态筛选 · 观测后升级 AGENT
- Diamond 归档作参考: `workers/*.py` 14 旧 Worker 继承 `routers_legacy.py` (3076 行, 2026-07-26 OMNI-040 迁出 _archive 到正式位置), 用户明示**不拆**成 per-worker 文件, 作回退路径 + 观测对照组

## 7 · 未来方向

- V2: 完成 Phase 2/4/5/6/7 (7 新 worker, ~7-8h)
- V3: 对接 Phase 8 CodeGenerator (参考旧 CodeGenLoop 实装) + Phase 10 Registrar
- intent.ambiguities → needs_user_input (把歧义直接交回当前 agent 对话裁定)
- 对接 self_repair (A4): design_validation_report FAIL 时 → core_diagnose 入 self_repair
- ReferenceScout v1 升级 AGENT worker (grep + read + LLM 判相关性)
- 多轮探针观测后, 把**稳定流程**固化成 HARD worker (按 agent-first 方法论 Step 4)

## 附 · ServiceBus 出口强制

所有 agent worker 的执行动作必须走 DiskBus / WebBus / BashBus, **禁直** subprocess / open('w') / requests; 人类澄清留在当前 agent 对话。当前 IntentAnalyzer + TeamArchitect LLM 调用已走 WebBus audit 回流 EventBus。

---

## (以下为 V2 旧内容 · workflow_factory Clean Migration 2026-04-20 · 归档作参考)
### 原状态记录

## 核心目的

**workflow_factory Team** 是 OmniCompany 的"造工作流的工作流" (meta pipeline):
- 输入: `wf.requirement_raw` (自然语言需求)
- 输出: `wf.done` (注册后的可运行 LAP 管线 · sink Material)

**解决的问题**:
- 用户描述一个新管线需求 → LLM 解析成结构化需求 → 设计 Format 链 → 规划节点 →
  注入框架真源码 → 用 agent-loop 逐文件生成代码 → 三层编译检查 →
  错误路由审计 → 集成测试 → 自动修复回路 → 注册到全局 registry.
  (2026-07-03 批4: 原验证链里的 LAP 九维合规审计节点已显式废止, 见下方废止说明。)

**不解决**:
- 管线业务正确性 (生成后由领域专家手工验收 + 业务测试负责)
- 生成代码的性能调优 (功能正确不等于最优执行计划)

## 核心接口

- `build_pipeline()` → [pipeline.py](pipeline.py) 构造 13 节点 + 1 AgentNodeLoop 的 Team spec (含设计链 / 生成链 / 验证链 / 修复链 4 条回路; 2026-07-03 批4 lap_verifier 废止后由 14 减为 13).
- `build_bindings(*, model=None)` → [run.py](run.py) 映射 node_id → Worker/AgentNodeLoop 实例, 返回 `dict[str, Worker]`.
- `register_formats(registry)` → [formats.py](formats.py) 注册 9 条 Material.

### Worker 清单 (Clean Migration 后全部继承自 omnicompany.Worker)

| Worker | MRO 来源 (Legacy Router) | 节点职责 | FORMAT_IN → FORMAT_OUT |
|---|---|---|---|
| `ReqAnalyzerWorker` | LLMRouter | 自然语言需求 → 结构化 | `wf.requirement_raw` → `wf.requirement` |
| `FormatDesignerWorker` | LLMRouter | 设计 Format 继承链 | `wf.requirement` → `wf.format_chain` |
| `NodePlannerWorker` | LLMRouter | 为每条转换规划 Router 节点 (含 format 覆盖率校验) | `wf.format_chain` → `wf.node_plan` |
| `NodePlanAuditorWorker` | Router (HARD) | P7.8 node_plan 语义质量审计 | `wf.node_plan` → `wf.node_plan` |
| `FrameworkContextLoaderWorker` | Router (HARD) | 注入框架真源码 + selftest 参考 | `wf.framework_context_loader.input` (composite) → `wf.node_plan_augmented` |
| `CodeGenFormatsWorker` | `_CodeGenBaseRouter` | 生成 formats.py (per-file fallback) | `wf.node_plan_augmented` → `wf.code_gen_state` |
| `CodeGenPipelineWorker` | `_CodeGenBaseRouter` | 生成 pipeline.py (per-file fallback) | `wf.code_gen_state` → `wf.code_gen_state` |
| `CodeGenRoutersWorker` | `_CodeGenBaseRouter` | 生成 routers.py (per-file fallback) | `wf.code_gen_state` → `wf.code_gen_state` |
| `CodeGenRunWorker` | `_CodeGenBaseRouter` | 生成 run.py + 收敛 skeleton | `wf.code_gen_state` → `wf.project_skeleton` |
| `SyntaxFixerWorker` | LLMRouter | Level 2 逐文件精准修复 | `wf.project_skeleton` → `wf.project_skeleton` |
| `DeterministicFixerWorker` | Router (HARD) | Level 1 确定性清理 | `wf.project_skeleton` → `wf.project_skeleton` |
| `AutoFixerWorker` | Router (LLM) | Level 3 LLM fallback 跨文件修复 | `wf.project_skeleton` → `wf.project_skeleton` |
| `CompileCheckerWorker` | Router (HARD) | 三层编译检查 (py_compile / import / PipelineChecker) | `wf.project_skeleton` → `wf.project_skeleton` |
| `ErrorRouteAuditorWorker` | Router (HARD) | 错误路由完整性五项检查 | `wf.project_skeleton` → `wf.project_skeleton` |
| `IntegrationTesterWorker` | Router (HARD) | 六项集成测试 (import + build + runner dry-run) | `wf.project_skeleton` → `wf.project_skeleton` |
| ~~`LAPVerifierWorker`~~ | ~~Router (HARD)~~ | **已废止 (2026-07-03 批4)**: D1-D9 九维打分与"不打分列证据"民约冲突; 实现体留归档 `_archive/routers_legacy.py::LAPVerifierRouter` 不删, 活代码引用已全摘除 | — |
| `FinalizerWorker` | Router (HARD) | 写盘 + 注册 + 生成 quality_summary | `wf.project_skeleton` → `wf.done` (sink) |

**附加**: `CodeGenLoop` (AgentNodeLoop, [routers_codegen.py](routers_codegen.py)) 实际上占据 pipeline.py 的 `code_gen_loop` 节点,
取代 4 个 per-file CodeGen*Worker 作为生产使用. per-file Workers 保留作为历史 fallback (Clean Migration 14 Worker 清单完整性).

### Material 清单 (9 条, F-19 全 kind.* 标注)

| Material | kind | 角色 |
|---|---|---|
| `wf.requirement_raw` | **source** | 外部触发 (CLI / Python API 输入自然语言需求) |
| `wf.requirement` | internal | 结构化需求 |
| `wf.format_chain` | internal | Format 继承链设计 |
| `wf.node_plan` | internal | 节点执行计划 |
| `wf.framework_context_loader.input` | internal | composite fan-in (node_plan + format_chain) |
| `wf.node_plan_augmented` | internal | 注入框架真源码后的 node_plan |
| `wf.code_gen_state` | internal | 增量代码生成中间态 (files 字典累加) |
| `wf.project_skeleton` | internal | P7.3 单主干 + reports 容器 (验证/修复节点共用) |
| `wf.done` | **sink** | 最终产物 (注册后可运行的管线 · 无 consumer Worker) |

### 兼容 shim

- [routers.py](routers.py) 旧 `*Router` 名 → `*Worker` 别名 + 模块级辅助 (`_wf_no_trunc` / `_extract_json_obj` / `check_format_in_consumption` 等) re-export
- [routers_codegen.py](routers_codegen.py) `CodeGenLoop` + 子 `*Router` (SingleToolRouter) re-export · 保留 AgentNodeLoop 继承不动
- [workers/_shared.py](workers/_shared.py) 内部 shared layer: `_CodeGenBaseRouter` / `_check_global_fix_iter` / 等

### 归档

- [routers_legacy.py](routers_legacy.py) · 原 `routers.py` 单文件实现 (2026-07-26 OMNI-040 Stage 3 从 `_archive/` 迁回正式位置)
- [routers_codegen_legacy.py](routers_codegen_legacy.py) · 原 `routers_codegen.py` 的 `CodeGenLoop` 实现 (同上迁回)
- [_archive/routers_legacy.py](_archive/routers_legacy.py) · 仅余批4 废止的 LAP 九维检查器实现体 (历史参考)
- [_archive/README.md](_archive/README.md) · 归档原因 / Diamond shortcut 历史说明

## 架构决策

### D1 — 14 Worker 粒度 (对齐 R-18)

按"完整职责 + FORMAT 边界 + 独立测试价值"划分:
- 设计链 4 Worker (req_analyzer / format_designer / node_planner / node_plan_auditor)
- 上下文注入 1 Worker (framework_context_loader, 确定性)
- 代码生成 1 AgentNodeLoop (code_gen_loop, 取代原 4 个 CodeGen*) + 4 per-file fallback (仍保留, 不在主拓扑)
- 验证链 4 Worker (compile / lap / error_route / integration)
- 修复链 3 Worker (deterministic / syntax / auto)
- 最终化 1 Worker (finalizer)

**粒度定论**: workflow_factory Team 最终 14 (或 17 含 per-file fallback) Worker.
主拓扑激活 14 个, 非主拓扑保留 3 个历史子类 (CodeGenFormats/Pipeline/Routers 3 个, 加 CodeGenRun 4 个合计 4).
Export 全量 17 使 ALL_WORKERS 保持完整性以备 rollback + 子 Worker 独立测试.

### D2 — Material kind 三分完整标注 (对齐 F-19)

- `wf.requirement_raw`: **source** — 外部 CLI 触发, 无 producer Worker
- 7 条 internal — Worker 间流转
- `wf.done`: **sink** — 最终产物, 无 consumer Worker

Q4 诊断下 ReqAnalyzerWorker 订阅 source Material 无上游 producer 合法.

### D3 — Diamond 继承 shortcut (对齐 migration_log "已知妥协"节)

workflow_factory routers.py 3053 行, 含紧耦合模块级辅助 (`_REQ_SYSTEM` / `_NODE_SYSTEM` / `_CODE_GEN_SYSTEM` 等大体量 LLM system prompts + `_CodeGenBaseRouter` 共享基类 + `_wf_no_trunc` 等 utility).
本次 Clean Migration 采用 **Diamond shortcut**:

```python
# workers/req_analyzer.py
from omnicompany.packages.services.omnicompany import Worker
from ..routers_legacy import ReqAnalyzerRouter as _Legacy

class ReqAnalyzerWorker(Worker, _Legacy):
    pass  # 业务代码在 routers_legacy.py (正式位置)
```

**合规**: Worker 继承链 + workers/ 结构 + kind.* 三分 + compat shim 全建立.
**Stage 3 (2026-07-26 OMNI-040 已落地)**: 业务代码从 `_archive/` 迁回顶层
`routers_legacy.py` / `routers_codegen_legacy.py` 正式位置, 活代码不再物理依赖归档;
`_archive/` 仅余批4 废止的 LAP 九维检查器实现体作静态历史参考.
(更彻底的"每 Worker 一个独立实现文件"拆分仍是可选后续, 非 OMNI-040 要求.)

### D4 — LLMRouter 子类的 Worker 继承路径

4 个 LLM Worker (ReqAnalyzer / FormatDesigner / NodePlanner / SyntaxFixer) 的 MRO:
  `[XxxWorker, Worker, XxxRouter (Legacy), LLMRouter, Router, ABC, object]`

Worker 基类本身 is-a Router (见 omnicompany.worker.Worker ← _Router),
LLMRouter 也 is-a Router. Diamond 继承 `(Worker, LLMRouter)` 通过 MRO C3 合并后:
- Worker 在前 — 满足 Clean Migration 硬规则 ("类继承 `class X(Worker)`")
- LLMRouter 在 Router 之前 — LLM 能力 (`self.client` / `INPUT_KEYS` / tool_use 支持) 保留

实测 MRO 合法 (无 TypeError), 所有 FORMAT_IN/OUT/DESCRIPTION/run() 通过 MRO 正常解析.

### D5 — CodeGenLoop 保留 AgentNodeLoop 继承 (本次 Clean Migration 不迁)

`CodeGenLoop` 是 AgentNodeLoop 不是 Worker (阶段 D `AGENT-NODE-LOOP-ROUTERIZATION` 在推进 router 化).
本次仅 re-export 维持 import 路径 (`from ...routers_codegen import CodeGenLoop`), 不动继承关系 + 业务逻辑.
阶段 D 完成后再按 Agent Worker (R-19) 规范重构.

## 数据流 / 拓扑

```
wf.requirement_raw (kind.source)
  ↓
ReqAnalyzerWorker (LLM)
  ↓ wf.requirement
FormatDesignerWorker (LLM)
  ↓ wf.format_chain ─────────────────┐ (fan-in 到 framework_context_loader)
  ↓                                   │
NodePlannerWorker (LLM)               │
  ↓ wf.node_plan                      │
NodePlanAuditorWorker (HARD)          │
  ↓ wf.node_plan (PASS) ──────────────┤
                                      ↓
                     FrameworkContextLoaderWorker (HARD, composite fan-in)
                      ↓ wf.node_plan_augmented
                                      ↓
                     CodeGenLoop (AgentNodeLoop · write/py_compile/read_written_file)
                      ↓ wf.project_skeleton
                                      ↓
         ┌────────────────────────────┼────────────────────────────┐
         ↓                            ↓                            ↓
   CompileCheckerWorker (HARD) ──────→ ErrorRouteAuditorWorker (HARD)
                    (2026-07-03 批4: LAPVerifier 节点已废止摘除)      ↓
                              IntegrationTesterWorker (HARD) ──────┘
                                      ↓ (FAIL → auto_fixer · PASS → finalizer)
                                      ↓
                                FinalizerWorker (HARD)
                                      ↓ wf.done (kind.sink)
                                      ↓
                                   EMIT

修复回路:
  CompileChecker FAIL → DeterministicFixer (L1) ─ PASS → CompileChecker (feedback)
                                                 └ PARTIAL → SyntaxFixer (L2)
  SyntaxFixer PASS → CompileChecker (feedback)
  SyntaxFixer FAIL/PARTIAL → AutoFixer (L3)
  LAP/ErrorRoute/Integration FAIL → AutoFixer (L3) → CompileChecker (feedback)
```

## 已知局限

1. ~~**Diamond shortcut 物理依赖**~~ (2026-07-26 OMNI-040 Stage 3 已解): 业务代码已迁回
   `routers_legacy.py` / `routers_codegen_legacy.py` 正式位置, `_archive/` 只剩静态历史参考.
   更彻底的"每 Worker 一个独立实现文件"拆分仍是可选后续.

2. **CodeGenLoop 未迁 Worker**: pipeline.py 用的 `code_gen_loop` 节点是 AgentNodeLoop,
   不是 Worker. 阶段 D 完成前, 本 Team 并非 100% Worker 化.
   升级路径: 阶段 D 完成后按 R-19 Agent Worker 规范重构为 Context/LLM/Tool/Finalizer 三件套.

3. **per-file CodeGen*Worker 未被主拓扑使用**: 4 个 per-file fallback Worker 当前在 bindings 里没 key,
   pipeline.py 只绑 `code_gen_loop`. 保留作为历史 fallback (P7.2 SCATTER 拆分产物).
   升级路径: 如果 CodeGenLoop 体系稳定, 清理这 4 个类.

4. **node_plan_auditor 的 maturity=HYPOTHETICAL**: P7.8 meta-pipeline 自净是实验性,
   检查项可能 critical/issue 分级不准确. 升级路径: 多跑几条管线验证 audit 规则.

5. **LAP verifier 有 9 维度确定性检查 (D1-D9) 但跑时间较长**: 大管线 skeleton 会拖慢整个
   workflow_factory 的验证链. 升级路径: 缓存 AST 解析结果 / 按 tags 跳过部分维度.

## 参考资料

- Team 代码:
  - [pipeline.py](pipeline.py) · [run.py](run.py) · [formats.py](formats.py)
  - [routers.py](routers.py) (shim) · [routers_codegen.py](routers_codegen.py) (shim)
  - [workers/](workers/) · 14 Worker 独立文件 + `_shared.py` 共享基类 re-export
  - [routers_legacy.py](routers_legacy.py) · [routers_codegen_legacy.py](routers_codegen_legacy.py) (legacy 业务实现, 正式位置)
  - [_archive/](_archive/) · 仅余批4 废止 LAP 检查器实现体 (历史参考)

- 规范引用:
  - [terminology.md §6/§7/§8](../../../../../../docs/standards/_global/terminology.md) 两层命名
  - [material.md F-16 / F-19 kind 三分](../../../../../../docs/standards/concepts/material.md)
  - [worker.md R-18 粒度 / R-19 Agent Worker / R-20 升级规则](../../../../../../docs/standards/concepts/worker.md)
  - [team.md P-14 Workspace](../../../../../../docs/standards/concepts/team.md)

- 迁移记录:
  - migration_log.md "完全迁移标准 · Stage 2 升级版"
  - 阶段 D AGENT-NODE-LOOP-ROUTERIZATION plan

- 历史计划 (已完成):
  - P7.2 SCATTER 4-step code gen 拆分
  - P7.3 单主干 + reports 容器 (替代 skeleton 克隆链)
  - P7.5 framework_context_loader (消 code_generator 幻觉)
  - P7.7 全局修复 iteration 上限
  - P7.8 node_plan_auditor (meta-pipeline 自净)
  - M1.2/1.4 IntegrationTester T5/T6 (build_bindings + runner dry-run)
  - M2.α/β F-15/P-13 声明即消费 + composite fan-in
