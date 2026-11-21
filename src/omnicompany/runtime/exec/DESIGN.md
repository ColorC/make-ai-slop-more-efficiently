<!-- [OMNI] origin=claude-code domain=runtime/exec ts=2026-04-17T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:runtime.exec.pipeline_engine.design_documentation.md" -->

# runtime/exec · 设计文档

## 状态
- **版本**: V3
- **成熟度**: active
- **下一步**: pre/post hook 系统化（目前 post-run hook 堆在 run() 末尾硬编码，未来改成可注册协议）

## 核心目的

`runtime/exec/` 是管线执行引擎。输入一个 `PipelineSpec` + 一套 `Router` 实例绑定，产出执行结果 + 事件流。

主要职责：
- 按拓扑调度节点（包含 fan-out / fan-in / join / 条件路由）
- 管理执行预算（step counter / max_steps / hard_limit）
- 处理路由动作（NEXT / EMIT / HALT / JUMP / RETRY / BUDGET）
- 集成信息审计链（post_hoc 兜底 / node_audit_reports / pipeline_health 汇聚）
- 集成经验沉淀（crystallize trigger，N≥3 自动开启）
- 实现 M4 REQUIRED_CONTEXT 事前拦截

它**不解决**的问题：
- 不决定管线结构（那是 `PipelineSpec` 作者的事）
- 不做业务逻辑（那是 Router.run() 的事）
- 不直接调 LLM（通过 Router 间接）

## 核心接口

- **`PipelineRunner(pipeline, bindings, bus, ...)`** — 主类 — [runner.py:105](runner.py#L105)
- **`PipelineRunner.run(initial_input, parent_event_id=None)`** — 主入口（async）— [runner.py:1174](runner.py#L1174)
- **`_check_required_context(input_data, required_keys)`** — M4 事前拦截辅助 — [runner.py:30](runner.py#L30)
- **`NodeMetrics`** — 每节点执行度量（duration / input_tokens / output_tokens / failure_count）— [runner.py:73](runner.py#L73)
- **`sub_pipeline.py`** — 子管线执行支持（`SUB_PIPELINE` NodeKind）
- **`graph_builder.py`** — 从 PipelineSpec 构建 DAG 拓扑
- **`tool_executor.py`** — 工具调用执行层（Agent 用）
- **`bootstrap.py`** — 启动期初始化（如 `_ensure_guardian_running`）

## 架构决策

### D1 — 统一 DAG 执行器，线性管线自然退化

不分"线性管线"和"DAG 管线"两套 executor。DAG 是 superset：
- 单条边 = DAG 的退化情况
- fan-out = 多边出一个节点 → 并发执行
- fan-in = 多边进一个节点 → join barrier 等全部上游完成再合并

好处：一套代码，fan-out / fan-in / 线性都统一处理。

_验证来源: [code] `src/omnicompany/runtime/exec/runner.py::PipelineRunner.run`（fan-out `asyncio.gather` / fan-in join barrier 一体实现）_

### D2 — 双预算制（decision count + hard limit）

两种预算同时监控：
- **decision_count** — 只在 decision_nodes（默认 SOFT/LLM 节点）+1，受 `max_steps` 约束。这是"LLM 推理预算"。
- **hard_limit = max_steps × 20** — 不分节点类型，每步 +1。防止无限循环（某个 retry 路径反复走）。

参数默认 `max_steps=50`（单次 dispatch 可覆盖）。按新的 LLM 铁律（预算宽松到触发即 bug），未来默认应升到 1000。

_验证来源: [code] `runner.py` `decision_count` / `hard_limit` 双计数器 + [归纳] 铁律 B 要求宽松预算（`docs/standards/llm_first.md` §4）_

### D3 — execute_node 是闭包里的递归函数

`run()` 内定义 `async def execute_node(node_id, input_data, input_signal)`，闭包捕获 `step_counter` / `final_result` / `pipeline_terminated` / `budget_lock`。

理由：
- 递归调度天然适配 DAG（每个 edge 就是一次 `await execute_node(target_id, ...)`）
- 闭包共享状态免传递（相比传一堆 ref 参数）
- fan-out 时 `tasks = [execute_node(target, ...) for target in targets]` + `asyncio.gather` 一气呵成

_验证来源: [code] `runner.py::run()` 内嵌 `async def execute_node(...)` 闭包定义_

### D4 — 路由动作驱动控制流，不是拓扑驱动

节点执行完产出 Verdict，Route.action 决定下一步：

| action | 含义 |
|---|---|
| NEXT | 走拓扑里的下条边 |
| EMIT | 设 `final_result[0] = output`，触发 `pipeline_terminated` |
| HALT | 抛 RuntimeError，管线失败 |
| JUMP | 跳到指定 target（支持反向跳，如 feedback loop）|
| RETRY | 同节点重跑（有 `max_retries` 限）|
| BUDGET | decision_count 用尽的软退出（output 含 `budget_exhausted=True`）|

拓扑描述"能连"，路由描述"怎么连"。两者分离。

_验证来源: [code] `src/omnicompany/protocol/anchor.py::RouteAction` 枚举 + `runner.py` action 分派_

### D5 — Info Audit 集成点分两段

**段 1（节点执行完，Verdict 拿到）**：
- 读 `verdict.info_audit`（Router 主动填的）
- 若 None → 从 `get_last_info_audit()` contextvar 兜底（14 个老 Router 不读 result.info_audit）
- 若仍 None 且 `info_audit_mode != OFF` → 触发 **post_hoc**（独立 probe 审计实际 LLM 调用）
- 产出塞 `verdict.info_audit` + `self.node_audit_reports[node_id]`

**段 2（整次 run 结束前）**：
- 把 `self.node_audit_reports` 聚合成 `pipeline_health.jsonl` 记录
- 便于 Doctor / CLI 未来消费

_验证来源: [code] `runner.py:753-788` post_hoc 触发 + `append_pipeline_health` 聚合_

### D6 — Crystallize trigger 的 N≥3 默认

每次 AgentNodeLoop 节点完成后：
- 累加 `trace_count[pipeline_id:node_id]`
- count < 3 → 只跑 `TraceSummarizer`（记录不产 patch）
- count ≥ 3 → 跑全套 crystallizer（TraceSummarizer + FormatEdgeInferrer + DescriptionRefiner）
- env var `OMNICOMPANY_CRYSTALLIZE=...` 可显式覆盖

理由：单次运行噪音大（同一 repo 不同跑法可能提不同 patch）。≥3 次才自动开启，确保信号稳定。

_验证来源: [experiment] Exp B crystallize（`docs/plans/[2026-04-14]INFO-SUFFICIENCY/EXPERIMENT_REPORT.md` §B）_

## 数据流 / 拓扑

```
run(initial_input)
 │
 ├─ emit TASK_INTENT 事件
 │
 ├─ entry_signal = Signal(format=entry_format_in, ...)
 │
 ├─ await execute_node(pipeline.entry, initial_input, entry_signal)
 │    │
 │    ├─ 硬上限检查 (step_counter >= hard_limit)
 │    │
 │    ├─ _execute_single_node(node_id, input_data, signal, ...)
 │    │    ├─ M4 REQUIRED_CONTEXT 事前拦截
 │    │    ├─ use_audit_context({trace_id, pipeline_id, node_id})
 │    │    ├─ await router.run(input_data) → verdict
 │    │    ├─ 解析 verdict.info_audit (+ post_hoc 兜底)
 │    │    ├─ crystallize trigger (若是 agent loop + 开关打开)
 │    │    └─ 返回 (verdict, out_signal, action)
 │    │
 │    ├─ 根据 action:
 │    │    - EMIT  → final_result[0] = verdict.output; terminate
 │    │    - HALT  → raise RuntimeError
 │    │    - JUMP  → execute_node(target, ...) 递归
 │    │    - RETRY → retry_counter += 1; 重新 execute_node
 │    │    - NEXT  → 对每个下游 edge: execute_node(target_id, ...)
 │    │
 │    └─ fan-in (join barrier):
 │         join_received[target_id][source_id] = verdict
 │         当 len(join_received) == in_degree[target]:
 │             merged = _merge_inputs(target_id, join_received)
 │             execute_node(target_id, merged, ...)
 │
 ├─ pipeline 结束后:
 │    - 若有 self.node_audit_reports → append_pipeline_health(...)
 │
 └─ 返回 final_result[0] 或 self.last_output
```

## 已知局限

1. **pre/post hook 堆在 run() 末尾** — `append_pipeline_health` 等是硬编码在 run() 尾部。未来功能多了（比如 crystallize final trigger / insights 生成）要变成硬编码 10+ 条。升级路径：定义 `PostRunHook` 协议，dispatch 或 runner 注册时传入。

2. **decision_count 和 max_steps 的默认值偏保守** — 50 按新铁律应该升到 1000（触发即 bug，不是业务需求）。

3. **fan-in 合并时 `_merge_inputs` 的默认行为是 dict.update** — 多上游输出冲突时（同 key 不同值）只保留最后一个。若需严格的"按 format ID 命名空间"合并，需要 composite Format（已有实现但不是默认）。

4. **retry 没有"指数退避"** — 一个节点 RETRY 立即重跑，不等待。若因外部服务抖动失败，可能连续打穿。升级路径：`Route.retry_delay_ms` 字段 + runner 等待。

5. **子管线 SUB_PIPELINE 支持有限** — `_run_sub_pipeline` 存在但未在生产广泛使用，嵌套深度和事件关联关系未严格测试。

## 参考资料

- 关联 protocol：[protocol/DESIGN.md](../../protocol/DESIGN.md)（Verdict / Route / PipelineSpec）
- 关联 info_audit：[runtime/info_audit/DESIGN.md](../info_audit/DESIGN.md)（post_hoc / pipeline_health）
- 关联 agent_crystallize：[runtime/agent_crystallize/DESIGN.md](../agent_crystallize/DESIGN.md)（N≥3 trigger）
- 关联 plan：`docs/plans/[2026-04-14]INFO-SUFFICIENCY/FOUR_TIER_PLAN.md`（runner 的四层机制接入）
- 关联规范：docs/standards/team.md

## 接收意愿

- **welcome_themes**:
  - DAG 调度新原语（除 NEXT/EMIT/HALT/JUMP/RETRY/BUDGET 外的新路由动作）
  - fan-out / fan-in 优化（composite Format 合并之外的新合并策略）
  - 双预算新算法（超越 max_turns / max_steps + hard_limit 的细粒度预算）
  - 并行隔离 / partition 机制（同 fan-out 分支独立资源配额）
  - pre/post hook 协议化（从硬编码到可注册）
  - 子管线 SUB_PIPELINE 深度嵌套支持
  - retry 指数退避 / retry_delay_ms 细粒度控制
  - 拓扑动态重写（基于 crystallize / insights 的运行时管线调整）
- **hard_constraints**:
  - 单 runner 统一处理（不分两套执行器；agent loop / 确定性 Router 走同一 PipelineRunner）
  - 必须保证事件发射不破坏（所有节点执行必经 bus.publish）
  - 不得引入"绕过 Router 协议的快速路径"（Verdict / Route / FORMAT 协议不可退化）
  - 预算控制必须宽松到触发即 bug（铁律 B）
- **soft_preferences**:
  - 偏好声明式管线（PipelineSpec 描述）而非过程式编排
  - 偏好 composite Format 驱动的 fan-in 而非字典 merge
- **maturity_preference**: `production_validated`（执行引擎是底座，新范式需先 shadow 跑通再默认启用）
