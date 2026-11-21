<!-- [OMNI] origin=claude-code domain=runtime/info_audit ts=2026-04-17T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:runtime.info_audit.module_spec.design.md" -->

# runtime/info_audit · 设计文档

## 状态
- **版本**: V2（四层机制全部接入）
- **成熟度**: active
- **下一步**: probe baseline 接入 CI（PR 前运行并 diff）；post_hoc 结果写回 audit_store 的 `info_audit` 字段（目前结果散在 response_text 里）

## 核心目的

`runtime/info_audit/` 是**信息充分性四层机制**的集中实现。回答一个根本问题：

> **LLM 节点产出不可靠，到底是输入信息不够，还是 LLM 能力问题？**

如果是前者（信息问题），LLM 改 prompt 也没用。如果是后者（能力问题），补信息也没用。四层机制从不同角度诊断：

- **probe** — 看 FORMAT 描述：规范层信息够吗？
- **piggyback** — LLM 在主任务中顺手自评：支撑信息够吗？
- **post_hoc** — 执行后独立审计：真实 context 充分吗？
- **crystallize**（拆在 `runtime/agent_crystallize/`）— Agent 救火后沉淀经验：说明原管线缺什么？

四层覆盖"事前 / 事中 / 事后 / 失败"四个时机，成本从低到高。

本目录**不**解决的问题：
- 不替 LLM 做决策（产出 InfoAuditReport，由上层规则路由）
- 不做 crystallize 自身（在 `runtime/agent_crystallize/`）
- 不决定什么时候开启（runner 读 env var / Router 显式指定）

## 核心接口

- **`run_info_audit_probe_strict(format_in, format_out, description, ...)`** — 独立 probe，返回 `InfoAuditReport` — [probe.py:60](probe.py#L60)
- **`run_pipeline_probe_baseline(pipeline, *, output_dir, include_kinds)`** — 启动期对全管线做 probe 基线 — [startup_baseline.py](startup_baseline.py)
- **`find_last_llm_call(*, trace_id, node_id)`** — 从 audit_store 查特定节点最近 LLM 调用 — [post_hoc.py:27](post_hoc.py#L27)
- **`run_post_hoc_audit(*, trace_id, node_id, format_in, format_out, description)`** — 节点执行后独立 probe — [post_hoc.py:73](post_hoc.py#L73)
- **`record_llm_call(rec: LLMAuditRecord)`** — 落盘一条 LLM 调用审计 — [audit_store.py:90](audit_store.py#L90)
- **`load_historical_llm_calls(*, pipeline_id, node_id, last_n)`** — 历史 LLM 调用查询 — [audit_store.py:164](audit_store.py#L164)
- **`parse_info_audit_from_tool_use(blocks)`** / **`parse_info_audit_from_text(text)`** — 从响应提取 InfoAuditReport — [parser.py](parser.py)
- **`maybe_probe_baseline(pipeline, *, domain)`** / **`append_pipeline_health(...)`** / **`read_pipeline_health(...)`** — dispatch / runner 钩子 — [pipeline_health.py](pipeline_health.py)

## 架构决策

### D1 — 四层分工严格，不混淆

| 层 | 触发时机 | 预算 | 对 Agent 节点 | 核心契约 |
|---|---|---|---|---|
| probe | 执行**前** | 极低（1 LLM 短调用）| 同样适用 | 无上下文，看 FORMAT 描述 |
| piggyback | 执行**中** | **0**（搭车主 LLM）| **明确排除** | tool_use 结构化输出 |
| post_hoc | 执行**后** | +1 LLM / 节点 | 同样适用 | 读真实 prompt/response 独立审 |
| crystallize | 失败或质量差 | 最高（agent loop）| 本身就是第 4 层 | 换活人 + 经验回流 |

决策是分工而非竞争：不同场景适用不同层。见 `docs/standards/llm_first.md` + `docs/plans/[2026-04-14]INFO-SUFFICIENCY/EXPERIMENT_REPORT.md`。

_验证来源: [experiment] `docs/plans/[2026-04-14]INFO-SUFFICIENCY/EXPERIMENT_REPORT.md` §七受控对比矩阵 — 四层各自擅长场景实证_

### D2 — probe 用"孤立 LLM 实例"

probe 函数每次调都 `LLMClient(role="runtime_main")` 新建实例，不共享对话历史。理由：孤立 LLM 不会因为"我刚自信答过"就虚报 sufficient（用户 2026-04-09 洞察）。

孤立 probe 比 piggyback 保守，也更诚实。

_验证来源: [code] `src/omnicompany/runtime/info_audit/probe.py` + 用户 2026-04-09 对话洞察_

### D3 — piggyback tool 注入 + 三路豁免

见 [runtime/llm/DESIGN.md §D4](../llm/DESIGN.md)。这里说的 info_audit tool schema 定义在 [protocol/info_audit.py](../../protocol/info_audit.py)。

为什么 tool 化：旧版文本追加 JSON 块的 0% 成功率问题。tool_use 结构化不污染主文本。

_验证来源: [experiment] H3 piggyback 实测（LearningExtractor 50% / ReportWriter 0% / AgentLoop 0%）+ [code] `runtime/llm/llm.py` `INFO_AUDIT_TOOL_SCHEMA`_

### D4 — post_hoc 是"真实任务 + 专门输出"的填白

四象限里：probe=（非真实任务，专门输出），piggyback=（真实任务，顺便输出），缺"真实任务，专门输出"。post_hoc 补这个：

- 节点执行完，ia 仍为 None（piggyback 没得到 / 被 opt-out）
- post_hoc 读 audit_store 里该节点 LLM 调用的真实 prompt/response
- 独立 probe 审（有真实 context，比纯 probe 准）

每节点 +1 LLM 调用开销，但是 absorption 实测 post_hoc 对 ReportWriter 等节点审计得比 piggyback 好。

_验证来源: [experiment] absorption-module-driven 实测 post_hoc 对 ReportWriter 产出有意义 concerns（docs/plans/[2026-04-14]INFO-SUFFICIENCY/EXPERIMENT_REPORT.md §十二主动实验）_

### D5 — audit_store 是 append-only jsonl + trace 分文件

落盘路径：`data/llm_audit/<date>/<trace_id>.jsonl`

- 按 trace_id 分文件 —— 天然并发安全
- append-only —— 永远不修改已写记录
- 每行一个 `LLMAuditRecord`（json serialize）
- 大字段 truncate（response_text / system_prompt 截 20KB）—— 仅对落盘，不影响喂 LLM 的原文

**注意**：这里的 truncate 符合铁律 A 的例外（审计日志落盘截断不影响 LLM 能力）。

_验证来源: [code] `src/omnicompany/runtime/info_audit/audit_store.py`（LLMAuditRecord 字段 + trace 分文件实现）_

### D6 — pipeline_health 接入 dispatch + runner

M2 接入点：
- `dispatch` 前：`maybe_probe_baseline(pipeline, domain)` — 首次跑或缓存过期跑 probe（节点级信息缺口 warning）
- `runner.run()` 末尾：`append_pipeline_health(pipeline_id, trace_id, node_reports)` — 每次运行节点级 sufficiency 写到 `data/domains/<domain>/pipeline_health.jsonl`

这些是"Doctor 和 dashboard 未来消费"的基座。

_验证来源: [code] `src/omnicompany/core/dispatch.py::maybe_probe_baseline` + `src/omnicompany/runtime/exec/runner.py::append_pipeline_health`_

## 数据流 / 拓扑

**四层协作图**：

```
[管线首次运行]
  dispatch(pipeline, input)
      ↓
  maybe_probe_baseline(pipeline) ← 读 data/domains/<domain>/probe_baseline.json
      ↓ 缓存过期（>7d）或缺失 → 重跑
  run_pipeline_probe_baseline(pipeline)
      ↓ 对每个节点调 run_info_audit_probe_strict()
      ↓ 落盘 probe_baseline.json + logger.warning 列 critical 缺口节点
      ↓
  runner.run(input)
      ↓
  per-node 执行:
    router.run(input_data) 内部若 info_audit=True:
      LLMClient.call(info_audit=True) 注入 piggyback tool
        → response 含 tool_use(info_audit) → parse_info_audit_from_tool_use
        → result.info_audit = InfoAuditReport
        → audit_store 落盘（含 info_audit JSON）
      
    runner 拿到 verdict:
      ia = verdict.info_audit or get_last_info_audit()
      若 ia is None 且 info_audit_mode != OFF:
        run_post_hoc_audit(trace_id, node_id, format_in, format_out, desc)
          → find_last_llm_call(trace_id, node_id)
          → run_info_audit_probe_strict(含真实 prompt/response preview)
          → InfoAuditReport
        ia = report
      
      self.node_audit_reports[node_id] = ia
  
  runner.run() 末尾:
    append_pipeline_health(pipeline_id, trace_id, node_reports)
      → data/domains/<domain>/pipeline_health.jsonl

[crystallize 单独一条流]:
  见 runtime/agent_crystallize/DESIGN.md
```

## 已知局限

1. **probe_baseline 缓存粒度是整管线，不是单节点** — 改一个节点的 DESCRIPTION 也要重跑全部节点的 probe。优化路径：按 node_id 分文件。

2. **post_hoc 没有并发控制** — 长管线（10+ LLM 节点）会顺序跑 10+ 次额外 probe。可并发加速。

3. **InfoAuditReport schema 不够统一** — probe 版、piggyback 版、post_hoc 版字段细节有差（如 piggyback 有 `should_exist_but_absent`）。下游消费要处理变体。

4. **pipeline_health.jsonl 没有自动清理** — 长时间运行累积大。

5. **piggyback 对 agent loop 的豁免粒度是"所有 turn"** — 不能"只允许 turn_N 带审计"。agent 内部工具调用无法被独立审计。crystallize 弥补了这个盲点。

## 参考资料

- 关联 llm：[runtime/llm/DESIGN.md](../llm/DESIGN.md)（piggyback 注入实现）
- 关联 exec：[runtime/exec/DESIGN.md](../exec/DESIGN.md)（runner 如何消费 info_audit）
- 关联 crystallize：[runtime/agent_crystallize/DESIGN.md](../agent_crystallize/DESIGN.md)（第四层）
- 关联 plan：`docs/plans/[2026-04-14]INFO-SUFFICIENCY/FOUR_TIER_PLAN.md`（全部机制的实施计划）
- 关联 plan：`docs/plans/[2026-04-14]INFO-SUFFICIENCY/EXPERIMENT_REPORT.md`（13 章实验报告 + 对比矩阵）
- 关联 standards：docs/standards/information_sufficiency.md

## 接收意愿

- **welcome_themes**:
  - 信息充分性诊断新机制（probe/piggyback/post_hoc 之外的第 N 种时机）
  - LLM 调用审计新维度（stop_reason / tool_use pattern / 异常响应结构识别）
  - 跨 trace 聚合视图（从"单次审计"到"这个 Router 最近 30 次审计趋势"）
  - InfoAuditReport schema 统一与演化
  - audit_store 查询 / 索引 / rotation 新方案
  - pipeline_health 的热力图 / 异常趋势识别
  - 基于审计历史的自动门禁（连续 N 次 insufficient → 触发 crystallize）
- **hard_constraints**:
  - 不阻塞主路径（info_audit 任何失败必须降级为"无审计"而非让管线 HALT）
  - 审计日志落盘允许 truncation，但**不喂回 LLM**（铁律 A 的磁盘例外）
  - 不绕开 audit_store（业务代码不能直接写另一套审计落盘）
- **soft_preferences**:
  - 偏好结构化 schema 而非自由文本审计
  - 偏好低额外 LLM 成本的机制（按四层分工，piggyback 0 成本优先）
- **maturity_preference**: `stable_only`（审计机制变动会影响全管线的诊断解释，需先确认不回归四层分工）
