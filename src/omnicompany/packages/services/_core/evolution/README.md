<!-- [OMNI] origin=ai-ide domain=services/evolution ts=2026-05-04T14:50:00Z type=doc status=design agent=ai-ide belongs_to_service=evolution -->
<!-- [OMNI] summary="evolution service 自我叙事 README — 假设驱动的管线/配置演化工作流. 捕获 Quality Pain Signal → LLM 产假设 + 受控实验 + 状态机更新. status=design 还在迭代" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §四 模板严格写. 状态 design 反映在远景段 (跟 self_repair 合并待 SELF-STABLE-CORE plan)" -->
<!-- [OMNI] tags=readme,evolution,core,self-narrative,design-stage -->
<!-- [OMNI] material_id="material:services._core.evolution.readme.self_narrative.md"-->

# evolution · 假设驱动演化工作流

> 捕获运行时质量痛点信号 (QualityPainSignal), 自动生成 / 验证修复假设, 逐步迭代优化管线逻辑或 Worker prompt. 5 阶段循环 (B.1 浅追踪 → B.2 诊断 → B.3 实验 → B.4 分析 → B.5 状态更新).
>
> ⚠ **状态 design**: V1 还在迭代, 跟 SELF-STABLE-CORE plan D2 决策待合并到 `self_repair` service.

---

## 这是什么

evolution 是 omnicompany 的**假设驱动管线 / 配置演化工作流 service**. 它针对**慢性质量退化** (例 LLM 产出质量逐步下降 / 管线某节点偶发 fail), 自动:

1. **捕获 Pain Signal** (B.1 ShallowTracer 浅层追踪 trace 片段)
2. **诊断** (B.2 DiagnosisAgent 调 LLM 产 DiagnosisReport)
3. **受控实验** (B.3 ExperimentRunner 动态加载补丁模块 + ReplayRunner 重放)
4. **结果分析** (B.4 ResultAnalyzer 对比 improved / unchanged / regression)
5. **状态更新** (B.5 BoardUpdater 更新假设 confidence + status)

形态: **HypothesisBoard 黑板** (独立 SQLite 存储) 作跨会话状态载体. EvolutionOrchestrator 串联 B.1~B.5 循环 (max 5 cycles).

跟其他诊断 / 修复 service 的边界:
- **doctor** 看单 Format/Worker/Team 健康 — **急性**痛 (有问题立即可见)
- **guardian** 看源码合规 — 静态规则
- **repair** 修 doctor 报的 B 类问题 — 已知问题立即修
- **evolution** 处理**慢性质量退化** — 不是急性, 是"渐渐变差", 需要假设 + 实验闭环
- **lap_auditor / semantic_auditor** 看协议 / 语义合规 — 静态深度审

evolution 的特殊在: **它处理"不知道哪里坏" 的问题**, 通过假设 + 实验找原因. 其他诊断 service 是"明确 critical/blocking".

## 解决什么 / 不解决什么

**解决**:
- 慢性质量退化 (Quality Pain Signal) 的自动化诊断 + 受控实验闭环
- 跨 Agent 会话的状态载体 (HypothesisBoard 长生命周期持久)
- 假设 confidence 驱动的状态机 (`ACTIVE` / `DORMANT` / `ELIMINATED` / `CONFIRMED`)
- 离线沙盒重放 (动态加载 + ReplayRunner 不污染主环境)

**不解决**:
- 红线急性痛 (交由 [guardian](../guardian/) / [repair](../repair/) 处理)
- 实时在线热更新 (所有实验都离线重放)
- 全自动代码提交 (仅产补丁 + 结论, 人工 confirm 后落)
- 业务正确性 (各 domain Team 自己负责)

## 设计目的与最终目标

**设计目的**: omnicompany 不只要快速发现 critical (那是 guardian / doctor 的职责), 还要能**慢慢迭代优化** — 当 LLM 产出质量在某些场景偶发下降, 不是立即 critical 但长期累积有问题时, 用假设 + 实验闭环逐步定位 + 修复. 这是"自演化" 的承载.

**理论锚点**: 体现 omnicompany 主轴第三件能力"自维护 / 自诊断 / 自认知" 的进阶 — 不只发现已知问题, 还能**主动猜测未知问题**并实验验证.

**最终目标** (当下能认知的):
- 跟 SELF-STABLE-CORE plan D2 决策合并到 `self_repair` service
- 统一迁移到 ServiceBus 通信
- B.3 集成 AST 重构工具 (libcst), 实现节点级拓扑自动变更
- HypothesisBoard 接入分布式锁 / Redis WAL, 支持跨进程并发
- DiagnosisReport 加 Pydantic 强类型校验

## 规划

- **当前 V1 design** (2026-04-25 从 skeleton 升级 design): 5 阶段类 + Orchestrator + HypothesisBoardStore + CLI 命令 (shallow-trace / evolve / list-boards / show-board)
- **下一步**: 跟 debugger 合并为 `self_repair` (按 SELF-STABLE-CORE D2 决策)
- **远景**: AST 重构 + 分布式黑板 + Pydantic 校验

## 构成

- 数据模型 → [workflow/hypothesis.py](workflow/hypothesis.py) + [workflow/pain_signal.py](workflow/pain_signal.py) + [workflow/diagnosis.py](workflow/diagnosis.py)
  - `QualityPainSignal` / `HypothesisBoard` / `Hypothesis` (含 status enum) / `DiagnosisReport` / `ProposedChange` / `AnalysisResult`
- 持久化 → [workflow/hypothesis_store.py](workflow/hypothesis_store.py) (`HypothesisBoardStore` 独立 SQLite)
- 工作流编排 → [workflow/orchestrator.py](workflow/orchestrator.py) (`EvolutionOrchestrator` B.1~B.5 串联)
- 5 阶段组件 → [workflow/](workflow/)
  - `ShallowTracer` (B.1 浅追踪)
  - `DiagnosisAgent` (B.2 LLM 诊断)
  - `ExperimentRunner` (B.3 受控实验, 动态加载 + 重放)
  - `ResultAnalyzer` (B.4 结果分析)
  - `BoardUpdater` (B.5 状态更新)
- CLI 入口 → [workflow/cli.py](workflow/cli.py)

技术架构详述见 [DESIGN.md](DESIGN.md), 操作手册见 [SKILL.md](SKILL.md).

## 想了解更多

- 架构 → [DESIGN.md](DESIGN.md)
- 操作手册 → [SKILL.md](SKILL.md)
- 关联计划 SELF-STABLE-CORE D2 → [docs/plans/_archive/[2026-04-23]SELF-STABLE-CORE/](../../../../../../docs/plans/_archive/)
- 关联计划 TEAM-BUILDER-V3-CONTINUE A4 (self_repair 路线) → docs/plans/_archive/
- HypothesisBoard 数据结构设计 → [docs/plans/_archive/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/HYPOTHESIS_BLACKBOARD.md](../../../../../../docs/plans/_archive/)
- 项目根叙事 → [../../../../../README.md](../../../../../../README.md)
