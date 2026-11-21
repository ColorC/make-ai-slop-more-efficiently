<!-- [OMNI] origin=claude-code domain=runtime/routing ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:routing.package_design.specification.md" -->

# routing · 设计文档

## 状态
- **版本**: V1 (2026-04-25 骨架升级为充实设计，固化 Router 化重构成果)
- **成熟度**: active
- **下一步**: 配合 `AGENT-NODE-LOOP-ROUTERIZATION` Phase C 将遗留 `AgentNodeLoop` 迁移至纯 Router 调度；补全 `RouteRetriever` 的 embedding 检索缓存策略与动态 β 控制器。

## 核心目的
本包是 OmniCompany 运行时**节点执行与路径寻址的绑定层**，解决“声明式图结构 (GraphSpec) 如何映射为实际执行逻辑”的问题。
提供 `Router` 抽象契约、全局注册发现、基于历史记忆的语义检索、以及玻尔兹曼退火选路引擎。
**不解决**：
- 不负责业务领域逻辑（如 demogame/Absorption 解析器，应下沉至 `packages/domains|services`）
- 不管理 LLM 底层连接/重试/限流（委托 `runtime/llm`）
- 不负责状态持久化与 DB 建表（委托 `runtime/storage`）
- 不替代 `AgentNodeLoop` 的循环控制与 Prompt 拼接主干（仅作为循环内的执行单元）

## 核心接口
- **`Router`** (ABC): [router.py](router.py) — 基类定义 `run(input_data: dict) -> Verdict`，声明 `FORMAT_IN`/`FORMAT_OUT`/`REQUIRED_CONTEXT`
- **内置 Routers**: 
  - `ContextRouter`: [router.py](router.py) — 确定性消息拼接，恒返 PASS
  - `LLMRouter`: [router.py](router.py) — 语义整流器，支持多工具分发与 `info_audit` 钩子注入
  - `ToolRouter`: [router.py](router.py) — 绑定 `ToolExecutor`，执行 bash/editor/think
- **`KnowledgeRouter`**: [knowledge.py](knowledge.py) — 知识注入透传基类，`run()` 仅附加 `_knowledge` 字段
- **`RouterRegistry`**: [registry.py](registry.py) — `scan_routers()`, `inspect_graph_bindings()`, `global_view()` (三层 DAG 绑定内省)
- **`BoltzmannRouter` + `RouteCandidate` + `ConvergenceAuditor`**: [boltzmann_router.py](boltzmann_router.py) — 玻尔兹曼分布选路 + Fisher 单调性审计 + 温度退火调度
- **`RouteRetriever`**: [route_retriever.py](route_retriever.py) — `retrieve_routes(query) -> list[RouteCandidate]`，基于 `route_graph.db` 贪心展开拓扑并格式化为 System Prompt
- **`SoftNodeExecutor`**: [soft_node_executor.py](soft_node_executor.py) — `execute(node_id, input_data) -> SoftNodeResult`，驱动 DB 存储的语义节点执行

## 架构决策
### D1 · Router 作为 Node 唯一运行时绑定接口
**决策**: 统一抽象 `run() -> Verdict` 契约，严格分离声明 (`GraphSpec`) 与执行 (`Router`)。
**理由**: 原 `AgentNodeLoop` 包含 686 行硬编码分支，违反单一职责与开闭原则。Router 化后新增节点只需继承并注册，调度器保持薄层，降低循环主干复杂度。
### D2 · `REQUIRED_CONTEXT` 显式声明机制
**决策**: Router 基类引入 `REQUIRED_CONTEXT: list[str] = []`，执行前由 `runner` 预检上下文完整性。
**理由**: 防止隐式上下文漂移导致 LLM 幻觉。配合 `info_audit` 模块，可在运行前拦截缺失关键上下文的调用（见 `INFO-SUFFICIENCY` M4 计划）。
### D3 · 注册表基于运行时反射而非静态配置
**决策**: `RouterRegistry` 使用 `pkgutil.walk_packages` + `inspect` 动态扫描所有加载模块中的 `Router` 子类。
**理由**: 避免 `router_registry.yaml` 与实际代码脱节；支持 AI 自省（IsomorphicScheduler）实时构建完整绑定视图，减少人工维护成本。
### D4 · 玻尔兹曼退火选路与硬消除分离
**决策**: `BoltzmannRouter` 使用温度参数 β 控制探索/利用权衡，`hard_eliminated` 标记直接跳过而不参与概率计算。
**理由**: 早期需广泛探索路由（高 β），积累痛觉信号后 β 退火趋向开发；硬消除用于处理明确失败的节点（如工具不可用），避免污染概率分布与 Fisher 收敛指标。
### D5 · 知识注入采用透传附加字段策略
**决策**: `KnowledgeRouter` 保持 `PASSTHROUGH=True`，输出仅追加 `_knowledge` 键，不修改输入结构。
**理由**: 保证数据流类型契约 (`FORMAT_IN/OUT`) 不被下游破坏；知识仅作为提示增强，不改变核心数据管道形状，符合数据流纯粹性原则。
### D6 · 路由检索基于 SQLite 贪心展开而非图数据库
**决策**: `RouteRetriever` 使用轻量 `route_graph.db`，按 embedding 相似度 + `hit_count` 排序后做贪心拓扑展开。
**理由**: 避免引入 Neo4j/NetworkX 等重型依赖；当前执行图规模在万级节点内，贪心展开延迟 <50ms，满足 Agent 启动期 prompt 注入的实时性要求。

## 数据流 / 拓扑
```
[GraphSpec / Node YAML] ─┐
                         ↓
RouterRegistry ───▶ (扫描加载) ───▶ 生成 Binding Map (NodeID -> RouterClass)
                                   │
                                   ▼
AgentNodeLoop / Scheduler ───▶ 实例化 Router ───▶ router.run(input_data)
                                   │
                     ┌─────────────┼─────────────┐
                     ▼             ▼             ▼
               ContextRouter    LLMRouter     ToolRouter
               (消息拼接)      (LLM 调用)     (Bash/Editor)
                     │             │             │
                     └─────────────┼─────────────┘
                                   ▼
                              Verdict (PASS/FAIL + output)
                                   │
RouteRetriever ◀─── (历史落盘) ─── DB (route_graph.db)
      │
      ▼ (Embedding + hit_count 加权)
Greedy Topology Expand ───▶ Format System Prompt ───▶ 注入 Agent Context
```

## 已知局限
- `RouteRetriever` 的贪心展开缺乏回溯机制，局部最优可能错过更优长路径 · 升级路径: 引入 Beam Search 或 A* 算法，结合下游节点历史 `success_rate` 作为启发式函数，替换当前纯贪心逻辑（预计 Phase D 配合 `SoftNodeExecutor` 升级）
- `BoltzmannRouter` 的 β 退火调度目前依赖硬编码时间窗口或手动干预，缺乏自适应性 · 升级路径: 实现基于 `ConvergenceAuditor` Fisher 单调性指标的动态 β 控制器，当连续 N 轮 pass_rate 无显著提升时自动降温，形成闭环反馈
- `RouterRegistry` 的反射扫描在超大规模 monorepo 下存在冷启动延迟（需遍历所有 `__init__.py`） · 升级路径: 引入编译期元数据生成脚本，在 CI 阶段产出静态映射缓存，运行时优先加载缓存文件，反射仅作为 fallback 触发条件为缓存版本落后于代码 commit sha

## 参考资料
- 关联源码: [src/omnicompany/runtime/routing/](router.py) 下所有模块实现
- 关联规范: `docs/standards/distributed-docs.md` (OMNI-034 七节结构与就近原则)
- 关联计划: `docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md` (Router 化重构与旧 Loop 废弃路径)
- 关联计划: `docs/plans/[2026-04-14]INFO-SUFFICIENCY/FOUR_TIER_PLAN.md` (上下文审计与 REQUIRED_CONTEXT 设计)
- 兄弟包边界: `src/omnicompany/runtime/agent/DESIGN.md` (调度循环), `src/omnicompany/runtime/exec/DESIGN.md` (工具底层执行)

## 接收意愿
- **接收**: 新 Router 子类实现（遵循 `run() -> Verdict` 契约 + 明确 `FORMAT_IN/OUT`）；针对特定协议的路由检索器扩展（需继承 `RouteRetriever` 基类）；选路算法变体（如 Thompson Sampling / UCB 替换玻尔兹曼，需附带 `ConvergenceAuditor` 适配）
- **不接收**: 业务领域特定逻辑（如 demogame 表格处理、Absorption 解析器应下沉至 `packages/domains|services`）；跨层干预（如直接修改 DB schema、在 Router 内部硬编码 LLM retry 策略）；破坏 `FORMAT_IN/OUT` 隐式契约的透传修改或 Side-effect 落盘行为（未走 `runtime/storage`）
- **边界信号**: 若新增模块需直接 import `omnicompany.runtime.llm.llm` 内部私有类或绕过 `ToolExecutor`，说明应归属 `exec/` 或 `agent/`；若路由逻辑强依赖特定业务 YAML/Config，说明应下沉至 domain 子包而非留在核心 routing 基建