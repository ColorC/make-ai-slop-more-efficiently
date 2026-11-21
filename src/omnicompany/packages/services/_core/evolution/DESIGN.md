<!-- [OMNI] origin=claude-code domain=services/evolution ts=2026-05-04T14:50:00Z type=doc status=design belongs_to_service=evolution -->
<!-- [OMNI] material_id="material:core.evolution.hypothesis_pipeline.design_document.md" -->

# evolution · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部** (接口 / 决策 / 数据流 / 局限).

## 状态
- **版本**: V1 (2026-04-25 从 skeleton 升级为 design，填充核心工作流与接口定义)
- **成熟度**: design
- **下一步**: 按 `[2026-04-23]SELF-STABLE-CORE/plan.md` D2 决策与 debugger 合并为 `self_repair` 服务，并统一迁移至 ServiceBus 通信。

## 核心接口
- **数据模型**: `QualityPainSignal`, `HypothesisBoard`, `Hypothesis` (含 `HypothesisStatus` 枚举), `DiagnosisReport`, `ProposedChange`, `AnalysisResult` — [workflow/hypothesis.py](workflow/hypothesis.py), [workflow/pain_signal.py](workflow/pain_signal.py), [workflow/diagnosis.py](workflow/diagnosis.py)
- **持久化**: `HypothesisBoardStore` (独立 SQLite 存取) — [workflow/hypothesis_store.py](workflow/hypothesis_store.py)
- **工作流编排**: `EvolutionOrchestrator` (串联 B.1→B.5 循环) — [workflow/orchestrator.py](workflow/orchestrator.py)
- **阶段组件**:
  - `ShallowTracer` (B.1 浅层追踪) — [workflow/shallow_tracer.py](workflow/shallow_tracer.py)
  - `DiagnosisAgent` (B.2 深度诊断) — [workflow/diagnosis.py](workflow/diagnosis.py)
  - `ExperimentRunner` (B.3 受控实验) — [workflow/experiment_runner.py](workflow/experiment_runner.py)
  - `ResultAnalyzer` (B.4 结果分析) — [workflow/result_analyzer.py](workflow/result_analyzer.py)
  - `BoardUpdater` (B.5 状态更新) — [workflow/board_updater.py](workflow/board_updater.py)
- **CLI 入口**: `cli` 模块提供 `shallow-trace`, `evolve`, `list-boards`, `show-board` 命令 — [workflow/cli.py](workflow/cli.py)

## 架构决策
### D1 · 独立 SQLite 黑板存储 (HypothesisBoardStore)
**决策**: `HypothesisBoard` 不使用 EventBus 的事件流存储，而是维护独立的 `evolution_boards.db`。
**理由**: 进化会话具有长生命周期、高读写频率且状态需跨多次 Agent 调用持久化。混入 EventBus 会导致事件流膨胀且难以进行基于 board_id 的快照/回滚查询。独立存储实现读写隔离，符合单一职责。

### D2 · 假设置信度驱动的状态机流转
**决策**: `Hypothesis` 状态 (`ACTIVE`/`DORMANT`/`ELIMINATED`/`CONFIRMED`) 与 `confidence` 浮点数强绑定，`BoardUpdater` 根据实验结论执行乘性衰减/提升。
**理由**: LLM 诊断具有不确定性。纯布尔状态无法表达“部分有效”或“需观察”的中间态。置信度衰减机制允许系统自动淘汰低价值假设，并在实验成功时固化结论，减少人工干预阈值。

### D3 · B.3 受控实验采用动态模块加载 + 原地重放 (Replay)
**决策**: `ExperimentRunner` 将 `ProposedChange` 转化为代码补丁后，通过 `tempfile` + `importlib.util` 动态加载临时模块，并使用 `ReplayRunner` 复用原始 trace 输入进行沙盒重跑。
**理由**: 避免污染主运行环境或产生新 trace 污染历史数据。动态加载确保每次实验环境干净，原地重放保证对比基准（A/B 测试仅变量唯一），提升实验结果的因果可解释性。

### D4 · 工作流按 B.1~B.5 显式阶段拆分而非单文件 Agent
**决策**: 将浅层追踪、深度诊断、实验运行、结果分析、状态更新拆分为独立类，由 `EvolutionOrchestrator` 统一串联。
**理由**: 早期单文件 `auto_evolve.py` 逻辑耦合严重，难以针对单一阶段（如诊断 Prompt 调优或实验沙盒隔离）进行独立迭代与单元测试。显式阶段拆分提升可观测性，便于未来按需替换为不同能力的 Agent 节点。

## 数据流 / 拓扑
```
[Pain Signal 输入]
   │
   ▼
(B.1) ShallowTracer ──→ 提取关键 trace 片段与节点边界
   │
   ▼
(Init) HypothesisBoardStore.load() ──→ 创建/读取 HypothesisBoard (状态载体)
   │
   ▼
┌─────────────────────── 进化循环 (Max Cycles=5) ───────────────────────┐
│ (B.2) DiagnosisAgent ──→ 加载 focus 假设上下文 → 调用 LLM → 输出 DiagnosisReport 
│        │
│        ▼
│ (B.3) ExperimentRunner ──→ 生成补丁 → 动态加载 → ReplayRunner 重放 → 输出 ExperimentResult 
│        │
│        ▼
│ (B.4) ResultAnalyzer ──→ 对比重放输出与原输出 → 判定 improved/unchanged/regression 
│        │
│        ▼
│ (B.5) BoardUpdater ──→ 更新假设状态/置信度 → 持久化至 SQLite 
│        │
│        ▼
└── 退出判断: board.status ∈ {done, escalated} OR 达最大轮数 → 退出循环 ──┘
   │
   ▼
[CLI / 上游服务] 读取最终 Board 结论或生成变更工单
```

## 已知局限
- **仅支持 `prompt`/`logic` 类修改的自动实验**：`ExperimentRunner` 当前只能自动处理 Router 内部 prompt 或逻辑微调；`insert_node` / `split_node` 等结构性变更仅生成文本描述，需人工落地。 · **升级路径**: 在 B.3 阶段集成代码 AST 重构工具（如 `libcst`），实现节点级拓扑自动变更与验证。
- **黑板持久化依赖独立文件路径，未接入统一消息总线**：当前 `HypothesisBoardStore` 直连本地 SQLite，多 Worker/Agent 并发访问同一 board 时缺乏分布式锁与冲突解决机制。 · **升级路径**: 按 `self_repair` 合并计划，迁移至 `ServiceBus` 或引入基于 Redis/SQLite WAL 模式的乐观并发控制，支持跨进程会话同步。
- **LLM 诊断结果缺乏结构化约束强校验**：`DiagnosisReport` 依赖 LLM 自由文本填充，虽要求“不允许空泛描述”，但缺少自动化 schema 校验。 · **升级路径**: 在 `DiagnosisAgent` 输出层增加 Pydantic 强类型校验与重试机制，失败时降级至确定性规则诊断。

## 参考资料
- 关联计划: `docs/plans/[2026-04-23]SELF-STABLE-CORE/plan.md` (D2 决策: evolution 与 debugger 合并)
- 关联计划: `docs/plans/[2026-04-24]TEAM-BUILDER-V3-CONTINUE/plan.md` (A4 路线: self_repair 建立)
- 架构设计草案: `docs/plans/_archive/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/HYPOTHESIS_BLACKBOARD.md` (黑板数据结构设计)
- 关联规范: `docs/standards/distributed-docs.md` (OMNI-034 设计文档结构规范)
- 源码实现: `workflow/orchestrator.py` (B.1-B.5 串联逻辑) + `workflow/hypothesis_store.py` (持久化层)