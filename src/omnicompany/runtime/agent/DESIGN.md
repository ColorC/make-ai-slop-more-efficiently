<!-- [OMNI] origin=claude-code domain=runtime/agent ts=2026-04-17T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:runtime.agent.subsystem.design_doc.md" -->

# runtime/agent · 设计文档

## 状态
- **版本**: V2
- **成熟度**: active
- **下一步**: 经验沉淀（crystallize）接入已就位，下一步是让 agent 直接调用沉淀出来的 skill（对标 hermes skill_manager）

## 核心目的

`runtime/agent/` 是**多轮对话 Agent 循环**的基础框架。提供 `AgentNodeLoop` 基类，业务域继承它构造具体的 agent Router（如 `ModuleExplorer` / `DisputeLoop` / 各种 workflow agent）。

核心能力：
- 多轮 LLM 对话（每轮 LLM 调 + 工具执行 + 下一轮）
- 四层上下文压缩（microcompact / truncation / sliding_window / auto_compact）
- 工具权限管控（readonly / permit / strict）
- 预算控制（max_turns / tool budget / token budget）
- 事件发射（LLM_CALL / TOOL_CALL / COMPACT / FINISH 等，供 bus 消费）

它不解决的问题：
- 不决定 agent 的**任务**（子类定义 `build_initial_messages` + `extract_result`）
- 不实现具体工具（工具在 `tools/` 或 `packages/*/routers/*` 下）
- 不管 agent 的经验沉淀（那是 `runtime/agent_crystallize/` 的事）

## 核心接口

> ⚠️ **2026-06-13 现状修正**：`AgentNodeLoop` 基类 + `LoopEventType` 事件枚举 + `ToolDefinition` / `FinishTool` / `ThinkTool` 已随 `agent_node_loop.py` / `agent_loop_tools.py` 的旧实现一起删除（AgentNodeLoop Phase D 清理）。基类现在唯一活体在 [`packages/services/_core/agent/loop.py`](../../packages/services/_core/agent/loop.py)（薄调度器版）。本目录留下的活体只有 **配置体系** 和 **四层压缩函数**（仍被新 loop.py import）。

- **`AgentNodeLoop(Router)`** — 薄调度器基类（已迁出本目录）— [packages/services/_core/agent/loop.py:49](../../packages/services/_core/agent/loop.py#L49)
- **`AgentNodeLoop.run(input_data)`** — 主循环（异步）— [packages/services/_core/agent/loop.py:217](../../packages/services/_core/agent/loop.py#L217)
- **子类接入方式**（新薄调度器）：
  - `NODE_PROMPT` — 类级常量（首轮 prompt 模板）
  - `LOOP_CONFIG: LoopConfig` — 预算 / 压缩 / 重试配置
  - `TOOL_ROUTERS: list[type[SingleToolRouter]]` — 工具 Router 清单（每个工具一个 Router）
  - `build_prompt_builder(...)` / `build_extract_result(...)` — 构造首轮消息与产出 Verdict 的子 Router
- **事件**：新版不再用 `LoopEventType` 枚举，改为字符串信号（`agent.loop.start` / `agent.turn.start` / `agent.turn.end` / `agent.budget_exhaust` / `agent.aborted` 等），经 `emit_agent_signal` 落 bus — [packages/services/_core/agent/_bus.py](../../packages/services/_core/agent/_bus.py)
- **`LoopConfig`** / **`CompactConfig`** / **`PermissionConfig`** — 配置（仍留本目录，新 loop.py import 之）— [agent_loop_config.py:167](agent_loop_config.py#L167) / [agent_loop_config.py:39](agent_loop_config.py#L39) / [agent_loop_config.py:145](agent_loop_config.py#L145)
- **工具声明** — `ToolDefinition`（声明式工具）已被 `SingleToolRouter` 子类取代 — [packages/services/_core/agent/routers/single_tool.py](../../packages/services/_core/agent/routers/single_tool.py)
- **内置工具** — `FinishTool` → `FinishRouter`（同上文件）；`ThinkTool` 已下线

## 架构决策

### D1 — AgentNodeLoop 继承 Router，融入管线

Agent 不是管线外的异类，而是管线里的一种 Router。继承自 `Router` 使：
- 有 `FORMAT_IN` / `FORMAT_OUT` / `DESCRIPTION`
- 能被 PipelineRunner 当普通节点调度（`await router.run(input_data)`）
- 产出 `Verdict`，走统一的路由
- 享受 post_hoc / crystallize 等统一能力

坏处：子类样板代码多（要定义 SYSTEM_PROMPT / LOOP_CONFIG / build_initial_messages 等）。Trade-off 接受。

_验证来源: [code] `src/omnicompany/packages/services/_core/agent/loop.py::class AgentNodeLoop(Router)`_

### D2 — 四层上下文压缩

单个 agent loop 可能跑几十轮，消息历史爆炸。四层压缩按 L1→L4 依次施加：

- **L1 microcompact** — 每轮同工具多次调用的结果内联合并
- **L2 truncation** — 过长工具输出原地截断（保留头尾 + 省略号）
- **L3 sliding_window** — 只保留最近 N 条消息 + 前几条系统消息
- **L4 auto_compact** — 超阈值时 LLM 调用生成整轮摘要代替

每层配置独立（threshold），按需启用。见 [agent_loop_compact.py](agent_loop_compact.py)。

**注意**：L2 的截断**只应用于 agent 内部工具结果的管理**，不违反铁律 A（不向 LLM 前喂截断）——agent 内部消息历史是 agent 自己的 context，不是对外的"资料"。但未来要审视：某些工具结果可能包含关键信息，L2 截断能否保留？

_验证来源: [code] `src/omnicompany/runtime/agent/agent_loop_compact.py` 四层实现 + LOOP_CONFIG 阈值_

### D3 — 工具权限三级

`PermissionConfig.mode`:

- **readonly** — 只允许 `is_readonly=True` 的工具（只读 / 内部状态更新类）。高危操作被拒，agent 看到错误反馈
- **permit** — 每次非只读操作都发事件等用户点确认（交互式 UI）
- **strict** — 每次工具都要审批（极保守）

默认 `readonly`（最保守）。子类可按业务调（如 workflow 生成类可能需要 permit）。

**坑过的**：2026-04-17 `ProposalDisputeLoop` 的 `submit_revised_proposals` 初版 `is_readonly=False` → readonly 模式拒绝 → agent 循环 14 轮但提交全 fail。修：tool 更新 session dict 不算"文件副作用"，仍可标 `is_readonly=True`。

_验证来源: [experiment] 2026-04-17 DisputeLoop 实测（14 轮 fail → 修 `is_readonly` 后正常）+ [code] `agent_loop_config.py::PermissionConfig`_

### D4 — 事件发射是默认行为，不依赖 debug mode

每轮 / 每工具调用 / 每压缩 / finish 都发信号（新版用字符串 `agent.loop.start` / `agent.turn.start` / `agent.turn.end` / `agent.budget_exhaust` 等，旧版用 `LoopEventType` 枚举）。这些事件经 `emit_agent_signal` 写入 SQLiteBus，dashboard 消费。

理由：agent loop 是最需要"看看它在干什么"的黑盒。没有事件 = 盲跑。

_验证来源: [code] `packages/services/_core/agent/loop.py::AgentNodeLoop._signal` + `_bus.py::emit_agent_signal` 各调用点_

### D5 — ~~messages 存在 self._messages~~（旧机制，已随 agent_node_loop.py 删除）

> ⚠️ **已失效**：此决策属旧单体 `agent_node_loop.py`，该文件已删。新薄调度器 `packages/services/_core/agent/loop.py` 里 `messages` 是 `run()` 的局部变量，逐 turn 经各子 Router Verdict 流转、全程落 bus，不再回写 `self._messages`。crystallize 改为从 bus 事件流（`agent.llm-response` / `agent.tool-response`）重建 trace，不再从 loop 实例读历史消息。下文保留作历史背景。

原设计 messages 是 run() 的局部变量，agent loop 结束后消失。但 crystallize 的 `build_agent_loop_trace(loop)` 需要从 loop 实例读历史消息。旧实现每轮同步 `self._messages = messages`，让 crystallize 从 finished loop 提取 trace。

_验证来源: [code] `packages/services/_core/agent/loop.py::run`（messages 为局部变量，无 self._messages 回写）+ bus 事件流取代 loop 实例读取_

### D6 — 内部辅助 LLM 调用豁免 piggyback

agent loop 内部有些 LLM 调用是"辅助"（压缩摘要 / ai_classify / budget_warning），它们不该被 piggyback 注入 info_audit tool（会混淆主调用）。

通过 caller 命名识别：`{prefix}.turn_internal` / `{prefix}.turn_N`（`\.turn_\w+$` 正则）。llm.py 看到这类 caller 跳过 piggyback 注入。

_验证来源: [code] `src/omnicompany/runtime/llm/llm.py:842-853` 三路豁免逻辑_

## 数据流 / 拓扑

```
Router 管线调度到 AgentNodeLoop 子类实例
    ↓ await agent.run(input_data)
       ↓
    build_initial_messages(input) → messages[]
       ↓
    ┌──────── Loop (turn in range(max_turns)) ────────┐
    │                                                  │
    │   should_force_finish(turn, messages)? → end    │
    │                                                  │
    │   apply L1/L2/L3 compress → messages             │
    │                                                  │
    │   若超阈值 → auto_compact → compacted messages  │
    │                                                  │
    │   _call_llm_with_retry(messages, system)         │
    │      → response (含 text + tool_use)             │
    │                                                  │
    │   若 no tool_calls → FINISH                      │
    │                                                  │
    │   _execute_tools_checked(tool_calls, ctx)         │
    │      ├─ check_permission (权限)                  │
    │      ├─ 并发 / 串行执行                          │
    │      └─ tool_results                             │
    │                                                  │
    │   messages.append(assistant)                     │
    │   messages.append(tool_results as user)          │
    │   self._messages = messages  ← crystallize 钩子  │
    │                                                  │
    │   on_turn_end(turn, messages)                    │
    │                                                  │
    └──────────────────────────────────────────────────┘
       ↓
    若 finish_tool 或 no_tool_calls → extract_result(final_text, messages)
    若 max_turns 到 → BUDGET_EXHAUST 事件 + extract_result(fallback)
       ↓
    返回 Verdict
```

## 已知局限

1. **子类样板代码多** — 每个 agent Router 子类要复制 20+ 行样板（`_build_loop` 内嵌类 / 重载方法）。升级路径：工厂函数 or 装饰器封装样板。

2. **L2 截断与铁律 A 的潜在张力** — 虽然 agent 内部消息管理不是"对外截断"，但极端情况下 L2 可能把关键工具结果裁掉。目前没有"重要信息保护"机制。升级路径：ToolDefinition 加 `priority: high|normal` 字段，L2 优先裁 normal。

3. **max_turns 当前默认 30/80（子类各写各的）** — 按新铁律应该升到 1000（触发即 bug）。现有子类要统一调整。

4. **权限系统无 per-tool override** — mode 是 loop 级，无法"readonly 模式但允许某个特定 write tool"。当前靠 `is_readonly` 声明绕开，但语义不够明确。

5. **L4 auto_compact 的摘要质量不稳定** — 摘要 LLM 调用偶尔产出"毫无信息量的总结"。目前有 fallback（失败则沿用原消息），但摘要质量本身没有评估。

## 参考资料

- 关联 runtime/exec：[runtime/exec/DESIGN.md](../exec/DESIGN.md)（PipelineRunner 怎么调 Agent）
- 关联 llm：[runtime/llm/DESIGN.md](../llm/DESIGN.md)（LLMClient 被 agent 用）
- 关联 crystallize：[runtime/agent_crystallize/DESIGN.md](../agent_crystallize/DESIGN.md)（从 agent trace 产 patch）
- 同类参考：`packages/services/absorption/routers/module_explorer.py`（AgentNodeLoop 子类典范）
- 同类参考：`packages/services/absorption/routers/proposal_dispute_loop.py`（较新的 agent，最佳实践）

## 接收意愿

- **welcome_themes**:
  - agent loop 上下文压缩新策略（替代或增强 L1-L4 四层）
  - 工具权限模型新范式（per-tool override / 动态权限升降）
  - 执行事件发射新维度（比 LOOP_EVENT_TYPE 更细粒度的观测）
  - 子任务 agent 原语（sub-agent / agent 间协作协议）
  - agent 自建 skill / 自取 skill（对标 hermes skill_manager 的可执行沉淀）
  - 工具重要性/优先级标注（priority: high|normal，用于 L2 截断保护关键信息）
  - agent 中止条件 / should_force_finish 新判据
- **hard_constraints**:
  - 必须融入管线 Router 抽象（继承 Router，有 FORMAT_IN/OUT，不另起 agent 世界观）
  - 必须挂 EventBus（2026-04-18 立档铁律，禁止 bus=None 静默运行）
  - 所有 Format 必须进 bus（LLM 调用 / 工具 / 压缩 / prompt 拼装全部落盘）
  - 不得引入"单体 while-true loop 绕过 Router"的实现
- **soft_preferences**:
  - 偏好声明式配置（LoopConfig / CompactConfig / PermissionConfig 扩展）而非 subclass 硬编码
  - 偏好 replay 可重放的 trace 结构（与 agent_crystallize 的 AgentLoopTrace 兼容）
- **maturity_preference**: `stable_only`（agent 基类改动影响所有子类，新范式需先实验再纳入）

---

## 过渡期迁移路径（2026-04-20 Stage 1 Team 8 新增）

### 当前状态: DEPRECATED 过渡期

本目录下所有 `AgentNodeLoop` 及子类**已全部标 DEPRECATED**（2026-04-18 立档, 阶段 D 删除）。

**新实现位置**: `src/omnicompany/packages/services/agent/`
- 阶段 A 骨架 2026-04-18 完成
- 重构完整计划: [`docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md`](../../../../docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md)

### Agent Worker 回映射（对齐 R-19 新标准）

新架构下, AgentNodeLoop 的 while 循环 = **Agent Worker 迷你 team**（router.md R-19）:

| 旧 AgentNodeLoop 内部职责 | 新 Agent Worker 子 Worker |
|---|---|
| `build_initial_messages()` + compact 压缩 | **Context Script Worker**（无 LLM, 组装/压缩上下文）|
| 主 LLM 循环（每轮 `LLMClient.call()`）| **LLM Worker**（单轮 LLM 调用）|
| tool_use / tool_executor 工具调度 | **Tool Script Worker (N)**（每种工具一个 Worker 或一组）|
| `TOOLS` 清单 | Worker 声明式订阅 tool_result material |
| 内部 messages 列表 | **迷你 stock**（Agent Worker 内部 material 流转, 不外泄）|

**对外表现**: 单个 Worker, FORMAT_IN/FORMAT_OUT 一套 — 订阅图上看是**一个节点**。

### LLM Worker → Agent Worker 升级规则

对齐 `router.md` R-20 — LLM Worker 不确定需要哪些 material 时（初始 material 难穷举）, **默认升级为 Agent Worker**, 开放相关 workspace 供其 Tool Script Worker 自由读取。

### Diagnosis Agent Worker 变体

对齐 `router.md` R-21 — 当 Agent Worker 产出异常或拿不到 material 时, **尽量少归因于 LLM 幻觉**。替换为 Diagnosis Agent Worker 重试, 使用 `trace_back_tool` / `material_assertion_tool` 沿 trace 上查。

### 过渡期规则

| 规则 | 说明 |
|---|---|
| 旧 AgentNodeLoop 子类 | **禁止新增**（必须继承 `packages.services.agent.AgentNodeLoop`）|
| 旧 ToolDefinition | **禁止新增**（用 SingleToolRouter 子类）|
| Guardian 计划新增规则 | 监控非 Router 的 LLM loop + 长流程绕开 bus |
| 命名铁律（2026-04-18 零容忍）| 类名 / 文件名不得挂版本后缀；新旧区分靠 import path |

### 质量目标

以 Claude Code（`参考项目/claude-code-analysis/`）为最终 agent 质量基准 — 工具结构 / Format 流 / 事件落盘 / replay 能力都要对齐该级别。

---

## 类型分类

**类 C · 元服务库（Agent 执行库）** · 属行政部核心基础设施。
- 无 Format 定义 / 无 Router 子类（基类 AgentNodeLoop 不算业务 Router）
- 业务 Team 继承使用
- Stage 1 Team 8 迁移（2026-04-20）= DESIGN.md 补阶段 D 过渡期路径 + R-19 回映射, 代码零改动（等阶段 D 整包删除）
