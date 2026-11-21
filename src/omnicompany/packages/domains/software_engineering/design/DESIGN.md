<!-- [OMNI] origin=claude-code domain=packages/domains/software_engineering/design ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:domains.software_engineering.design.design_specification.md" -->

# design · 设计文档

## 状态
- **版本**: V1 (2026-04-25 · 管线 DAG 定型 / Format 协议落地 / Router-Team 映射完整实现)
- **成熟度**: active
- **下一步**: 将 `context_judge` 的充分性阈值从启发式计数迁移至可配置的 LLM 语义评估；在 `data/domains/software_engineering/design/artifacts/` 中沉淀首个完整设计审查用例，打通 E2E 数据落盘验证。

## 核心目的
`software_engineering/design` 负责**代码仓库架构与设计提案的自动化审查**。它通过静态扫描与 LLM 语义分析结合，评估目标项目的架构模式是否与设计意图对齐，并输出结构化审查报告。
- **解决**：设计提案落地前的架构一致性验证、关键文件上下文自动累积、多轮迭代审查回路控制、确定性格式报告生成。
- **不解决**：运行时行为验证（属 `debugger`/`equiv_test`）、代码等价性测试（属 `tdd`/`verify`）、具体代码重写实施（属 `implement`/`lang_rewrite`）。

## 核心接口
- **`build_team() -> TeamSpec`** — 审查管线 DAG 构建入口 [team.py](team.py)
- **`build_bindings(input_dict) -> dict[str, Router]`** — 节点到 Router 实例的映射工厂 [run.py](run.py)
- **`formats.FORMATS: list[Format]`** — 域内协议族 (`sw_design.task`, `sw_design.snapshot`, `sw_design.context-state`, `sw_design.patterns`, `sw_design.llm-review`, `sw_design.report`) [formats.py](formats.py)
- **`routers.SpecParserRouter`** — 解析设计任务与目标路径 [routers.py](routers.py)
- **`routers.ArchScannerRouter` / `routers.FileReaderRouter`** — 目录树扫描与关键文件读取 [routers.py](routers.py)
- **`routers.ContextJudgeRouter`** — 上下文充分性判定 (决定回路或放行) [routers.py](routers.py)
- **`routers.PatternAnalyzerRouter` / `routers.DesignReviewerRouter`** — 模式提取与 LLM 审查 [routers.py](routers.py)
- **`routers.ReportFormatterRouter`** — 终态报告格式化与 EMIT [routers.py](routers.py)

## 架构决策
### D1 · 采用 TeamSpec DAG 替代旧版 Pipeline 硬连线
**决策**: 废弃 `pipeline.py` 中的隐式调用链，全面迁移至 `team.py` 基于 `TeamSpec`/`TeamNode` 的声明式拓扑。
**理由**: 旧管线难以支持动态回路 (`context_judge` 的 PARTIAL 分支回滚至 `arch_scanner`)。声明式 DAG 明确节点状态 (`Anchor`/`LLM`/`Deterministic`)、输入输出 Format 绑定及 Validator 规则，使管线可观测、可测试，并与 runtime 调度器无缝对接。

### D2 · 上下文累积采用有状态 ContextState 而非全量传递
**决策**: 引入 `sw_design.context-state` Format，在回路中维护 `file_batch`、`iteration` 计数与 `sufficient` 布尔标志，而非将海量文件内容塞入单次请求。
**理由**: 架构审查常需多轮扫描以覆盖分散的模块。有状态累积避免 Context 窗口溢出，同时 `context_judge` 可基于累积元数据快速决策是否满足 `SUFFICIENT` 阈值，显著降低推理成本与内存占用。

### D3 · 审查节点严格区分 HARD/LLM 职责边界
**决策**: `spec_parser`/`arch_scanner`/`file_reader`/`pattern_analyzer` 标记为 `HARD` (确定性/规则驱动)；`context_judge` 与 `design_reviewer` 标记为 `SOFT/LLM`。
**理由**: 静态结构解析与模式提取 100% 可自动化，无需 LLM 介入。仅当涉及“上下文是否足够支撑审查”及“设计意图与代码实现的语义对齐”时才调用 LLM。此分工保障管线基线性能与 Token 成本可控。

### D4 · 输出协议收敛为单一 `sw_design.report` Sink Format
**决策**: 无论中间经过多少轮回路或模式匹配，最终均由 `report_formatter` 产出标准化的 `sw_design.report` 格式，并触发 `EMIT` 终止管线。
**理由**: 上游下游模块（如 `plan` 域的审批流或 `review` 域的记录）仅消费终态报告。统一 Sink Format 避免下游需适配多态中间结果，简化集成契约并保证 `.omni/manifest.yaml` 中数据落盘边界清晰。

## 数据流 / 拓扑
```
[输入: sw.task-input]
      ↓
spec_parser (HARD) → sw_design.snapshot (目录树/语言分布)
      ↓
arch_scanner (HARD) ───────────────────────────────┐
      ↓                                            │
file_reader (HARD) → 读关键源码/配置文件 → 写入 context-state ┤
      ↓                                            │
context_judge (LLM)                                │ (PARTIAL)
      ├─ IF SUFFICIENT → 放行                     └─────↑ (回路)
      └─ IF PARTIAL    → 触发下一轮扫描 ─────────────────┘
            ↓ (PASS)
pattern_analyzer (HARD) → sw_design.patterns
            ↓
design_reviewer (LLM) → 结合 snapshot/patterns/原 task 生成审查结论
            ↓
report_formatter (DET) → sw_design.report (终态) → [EMIT]
```

## 已知局限
- **回路终止条件依赖启发式阈值** — 当前 `context_judge` 判定“充分”主要依赖已读取文件数与路径覆盖率启发规则，缺乏对“关键架构点是否已覆盖”的深层语义理解。升级路径: 在 `context_judge` 中集成轻量级 AST 摘要匹配，或接入 LLM 动态评估清单 (Checklist)，将 `sufficient` 判定从计数驱动升级为语义覆盖度驱动。
- **`design_reviewer` 缺乏多版本对比基线** — 当前审查针对静态代码快照，无法自动对比“设计提案修改前/后”的架构差异，难以量化改进效果。升级路径: 扩展 `sw_design.task` Format 增加 `baseline_snapshot_id` 字段；在 `pattern_analyzer` 后增加 `DiffAnalyzer` 节点，输出架构演进 Delta 报告，供 `design_reviewer` 消费。
- **跨包导入边界存在历史债务** — `routers.py` 曾隐式引用 `_graveyard` 模块，虽已清理但缺乏自动防护机制。升级路径: 在 CI/CD 中接入 Guardian 的 OMNI-003 边界检查规则，确保 `design` 包仅依赖 `protocol`/`runtime` 及 `_shared` 域，阻断向废弃模块的依赖回潮。

## 参考资料
- 关联管线定义: [team.py](team.py) / [run.py](run.py)
- 路由实现与上下文结构: [routers.py](routers.py)
- 域协议族定义: [formats.py](formats.py)
- 共享格式工具: `../_shared/common_formats.py` (`truncate_file_content`, `MAX_TREE_BYTES`)
- 协议基类: `../../../protocol/format.py` / `../../../protocol/team.py` / `../../../protocol/anchor.py`
- 关联归档计划: `../../../../docs/plans/_archive/[2026-04-07]ARCH-TIDY/tidy_proposal.md`
- 分布式文档规范: `../../../../docs/standards/distributed-docs.md` (§四 src/ 结构白名单 / §五 DESIGN.md 七节)
- 兄弟包边界: `../plan/DESIGN.md` (上游任务) / `../review/DESIGN.md` (下游人工复核) / `../implement/DESIGN.md` (下游执行)
- 数据布局策略: `.omni/manifest.yaml`