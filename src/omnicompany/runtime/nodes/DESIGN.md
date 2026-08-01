<!-- [OMNI] origin=claude-code domain=runtime/nodes ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:runtime.nodes.package_architecture.design.md" -->

# nodes · 设计文档

## 状态
- **版本**: V1 (2026-04-25 · Router化拆分落地，从单体 semantic/agent loop 拆分为独立模块)
- **成熟度**: active
- **下一步**: 完成旧 `agent_node_loop.py` 的 Phase D 彻底删除与全量切换至 Router 管线 (见 plan.md)

## 核心目的
本包提供运行时图中的所有节点（Nodes）的 Router 实现。每个节点对应一个明确的关注点（上下文注入、痛觉处理、路由分发、安全拦截、工具执行、元进化审计），通过标准 `Verdict` 协议接入底座调度器。

**解决什么问题**：
- 将单体 Agent 循环中的隐式阶段逻辑拆解为可测试、可替换、可进化的独立 `Router` 模块。
- 底座（`GraphRunner` / `AgentNodeLoop`）保持无状态与无业务语义，仅负责拓扑编排。
- 为元进化（Pain/Convergence）与运行时安全提供标准化观测与干预锚点。

**不解决什么问题**：
- 不定义调度顺序与重试策略（由管线/拓扑决定）。
- 不直接执行 LLM 调用或沙箱隔离（委托给 `runtime/llm` 与 `runtime/exec`）。
- 不管理具体业务领域的状态（由 `packages/domains` 负责）。

## 核心接口
所有节点均继承 `omnicompany.runtime.routing.router.Router`，统一签名 `run(self, input_data: Any) -> Verdict`。

### 上下文与追踪 ([context.py](context.py))
- `TruthInjectRouter` — 真相注入：拼接 Mirror 自我认知、语义类型指导、节点级进化指导语。
- `MirrorRouter` / `TaskIntentRouter` / `TraceAccumulateRouter` — 意图解析与 Trace 累积。

### 守护与收敛 ([guardian.py](guardian.py))
- `ConvergenceAuditRouter` — 收敛审计：基于 Fisher 单调性定理检查窗口内奖励趋势，触发元进化介入。
- `GuardianCheckRouter` — 基础健康守卫。

### 痛觉系统 ([pain.py](pain.py))
- `PainClassifyRouter` / `PainPropagateRouter` / `RewardComputeRouter` / `EscalationCheckRouter` — 痛觉事件分类、传播、奖励计算与升级拦截。

### 路由与分发 ([routing.py](routing.py))
- `RouteRetrieveRouter` / `BoltzmannSelectRouter` / `SemanticTypeClassifierRouter` / `SpecializedDispatchRouter` — 历史路径检索、玻尔兹曼选路、语义分类与专用分发。

### 安全与意图 ([safety.py](safety.py))
- `DeathZoneCheckRouter` — 禁区拦截：工具执行前检查不可变规则，命中则返回重定向提示。
- `IntentParseRouter` — 意图结构化解析。

### 工具节点 ([tools.py](tools.py))
- `BashRouter` / `EditorRouter` / `ThinkRouter` / `FinishRouter` — 物理工具执行超节点（bash 连接外部真相，其他为标准交互）。

### 兼容重导出 ([semantic.py](semantic.py))
- 聚合上述所有模块供旧版 `agent_node_loop` 平滑过渡，提供 `DeathZoneCheckRouter`, `PainClassifyRouter`, `RouteRetrieveRouter` 等旧路径别名。

## 架构决策
### D1 · 节点语义与调度底座解耦
**决策**: 节点不持有任何循环控制状态（如 step count, max turns），只处理 `input_data: dict -> Verdict`。底座负责将 Verdict 路由至下一节点。
**理由**: 允许底座替换调度策略（串行/并行/条件分支）而不必重写节点逻辑。符合 Routerization 铁律，使单个节点可独立进行单元测试与进化替换。

### D2 · 痛觉与奖励系统独立为专用模块
**决策**: 痛觉分类、传播、计算、升级拦截全部收归 `pain.py`，通过 `Verdict` 传递结构化信号。
**理由**: 痛觉是元进化的核心反馈信号。将其从业务路由中剥离，可独立替换痛觉阈值算法或传播拓扑，而不影响正常任务流转，降低系统耦合度。

### D3 · 安全拦截前置与不可变禁区 (Death Zones)
**决策**: `DeathZoneCheckRouter` 在工具实际执行前拦截违反不可变规则的调用，并直接返回 `VerdictKind.FAIL` 附带重定向提示。
**理由**: 防止 LLM 幻觉或恶意 Prompt 触发破坏性操作。独立节点便于安全策略随监管要求热更新，无需重编译底座执行器。

### D4 · 兼容性重导出而非立即废弃旧入口
**决策**: 创建 `semantic.py` 集中重导出拆分后的子模块，保留旧路径 import，Phase A-C 过渡期不直接删除。
**理由**: `AgentNodeLoop` 处于重构期，立即切断 import 会导致大规模回归。重导出提供缓冲期，符合“破坏性变更需分阶段释放”的运维纪律。

### D5 · Fisher 单调性收敛审计采用窗口+连续违反机制
**决策**: `ConvergenceAuditRouter` 配置 `WINDOW_SIZE=8` 与 `CONSECUTIVE_VIOLATIONS=3`，拒绝单次波动触发元进化。
**理由**: Fisher 定理在有限样本探索中存在统计噪声。提高触发门槛避免进化因正常任务难度震荡而误介入，确保收敛信号的高信噪比与行动权威性。

### D6 · 工具节点作为 Super Node 暴露通用接口
**决策**: `BashRouter` 接受原始字符串命令，直接透传至底层 `ToolExecutor`，不做应用层封装。
**理由**: bash 是连接外部真相的万能通道。过度封装会限制 Agent 探索能力；底座仅负责物理执行与沙箱隔离，语义解释权交还给 LLM。

## 数据流 / 拓扑
节点在图中按关注点分层协作，底座负责串联。单次 Step 数据流如下：
```
[Agent Loop / GraphRunner] 输入原始 dict (messages, tool_calls, system_prompt)
          ↓
┌─────────────────────────────────────────────────────────────────┐
│                        节点拓扑 (串行/条件)                       │
├─────────────────┬──────────────────────┬───────────────────────┤
│ 1. 安全与意图     │ 2. 上下文注入         │ 3. 路由与分发          │
│ IntentParseRouter│ TruthInjectRouter    │ RouteRetrieveRouter  │
│ DeathZoneCheck   │ MirrorRouter         │ BoltzmannSelectRouter│
│                  │ TaskIntentRouter     │ SpecializedDispatchR.│
└────────┬────────┴───────────┬──────────┴───────────┬───────────┘
         ↓                    ↓                      ↓
┌─────────────────────────────────────────────────────────────────┐
│                      执行与反馈层                                 │
├────────────────────────┬────────────────────────────────────────┤
│ 4. 工具执行 (Sink)      │ 5. 痛觉与审计 (Side-Effects)            │
│ Bash/Editor/ThinkRouter│ PainClassifyRouter                     │
│ FinishRouter           │ RewardComputeRouter                    │
│                        │ ConvergenceAuditRouter                 │
└───────────────┬────────┴──────────────────┬─────────────────────┘
                ↓                           ↓
         [Verdict PASS/FAIL]         [元进化信号 / 收敛评估]
                ↓                           ↓
        [回写上下文/推进管线]        [触发 Crystallize / 降级]
```

## 已知局限
- **`semantic.py` 重导出导致 import 路径不统一**：当前掩盖了节点实际物理分散在 5 个子文件的事实，新手易误以为节点逻辑集中。升级路径：在 `[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION` Phase D 中，配合旧 `AgentNodeLoop` 彻底删除，全局替换为直连具体子模块的 `import`，随后安全移除 `semantic.py`。
- **节点间隐式数据契约缺乏类型强校验**：`run()` 依赖运行时 `isinstance(input_data, dict)` 与 `input_data.get(key)` 防御，未在编译期暴露缺失字段风险。升级路径：在 `runtime/routing` 基类中引入 Pydantic `ContractValidator` 中间件，使各 Router 的 `INPUT_KEYS` 自动转化为 TypedDict/Schema 校验，缺失时提前抛出 `ContractViolation` Verdict。
- **`BashRouter` 权限过大且缺乏细粒度资源限制**：当前仅靠 `ToolExecutor` 沙箱隔离，无命令级白名单或 CPU/内存 cgroups 限制。升级路径：对接 `runtime/exec/sandbox.py` 实现资源配额，并在 `DeathZoneCheckRouter` 中增加命令模式匹配拦截；长期规划替换为权限收敛的 `RestrictedShellRouter`。

## 参考资料
- 重构计划: [docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md](../../../docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md)
- 重构规则: [docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/guardian_rules.md](../../../docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/guardian_rules.md)
- 路由协议: [src/omnicompany/protocol/anchor.py](../../../src/omnicompany/protocol/anchor.py)
- 兄弟包架构: [runtime/agent/DESIGN.md](../agent/DESIGN.md) · [runtime/exec/DESIGN.md](../exec/DESIGN.md) · [runtime/routing/DESIGN.md](../routing/DESIGN.md)
- 规范: docs/standards/distributed-docs.md (OMNI-034 结构合规)

## 接收意愿
- **接收**: 新增符合 `Router` 协议的领域节点（如特定格式的解析节点、新型审计节点）；对现有节点输入/输出 `Verdict` 格式的无损演进。
- **不接收**: 在节点内部硬编码调度循环逻辑（如 `while True`, `sleep`）；直接调用 LLM 客户端或原始数据库连接（应下沉至 `runtime/llm` / `runtime/storage`）；业务领域强耦合逻辑（应移至 `packages/domains`）。
- **边界信号**: 若某节点 `run()` 内部出现超过 50 行与 `Verdict` 构造无关的纯业务状态管理，或显式 `import` 非 `runtime`/`protocol`/`core` 的领域包，说明已越界成为业务服务，需拆离至 `packages/`。