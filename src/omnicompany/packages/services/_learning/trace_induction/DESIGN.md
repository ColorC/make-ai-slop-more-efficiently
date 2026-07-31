<!-- [OMNI] origin=codex domain=services/trace_induction ts=2026-07-22T00:00:00+08:00 type=doc status=active -->
<!-- [OMNI] material_id="material:learning.trace_induction.pipeline_design_spec.md" -->

# trace-induction

## 定位

把真实工作中已经重复出现的执行轨迹，按需压缩为可审阅的 SOP 与需求候选。它不是后台模式发现器，也不是自动造 pipeline 的入口。

## 触发条件

- 已有真实重复操作或明确的复盘任务；
- 有可追溯的 trace / 外部会话轨迹；
- 预先说清预期消费者。

缺少上述条件时不运行，候选继续留在原始记录中。

## 拓扑

```text
ti.task
  -> TraceReader
  -> NoiseFilter
  -> SOPGenerator
  -> ReqWriter
  -> ti.requirement (emit)
```

管线只输出可审阅候选。是否把候选实现为 skill、脚本或工作流，由后续真实任务单独决定；不自动调用 `workflow-factory`，不自动写 pipeline index。

## 权威边界

- 原始运行事实：event / trace / ledger；
- 可证伪陈述：decisions；
- 本管线输出：可重建的 SOP / requirement 投影；
- 注册与部署：不属于本管线。

## 已知局限

- 三个 LLM 步骤仍有成本，短轨迹应人工判断是否值得运行；
- 历史 `wf_caller` / `registrar` 实现保留在源码 `_archive`，仅作历史证据；
- 没有第二次真实消费前，不应把候选升级成长期设施。
