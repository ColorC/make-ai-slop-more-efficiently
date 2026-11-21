<!-- [OMNI] origin=claude-code domain=services/trace_induction ts=2026-04-20T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:learning.trace_induction.pipeline_design_spec.md" -->

# trace_induction · 设计文档

## 状态
- **版本**: V1 (Phase D Diamond shortcut 2026-04-20)
- **成熟度**: active
- **下一步**: 改进 noise_filter LLM 标注精度；接入 embedding 加速聚类

## 核心目的
将历史执行 trace（intent_steps）转化为可复用的 SOP → 需求文档 → workflow-factory Pipeline。

六节点管线：TraceReader (DB) → NoiseFilter (LLM) → SOPGenerator (LLM) → ReqWriter (LLM) → WFCaller (SubPipeline) → Registrar (DB)。
由 `pattern_discovery` 触发（找到重复 pattern 后调用本管线），也可单独调用。

## 核心接口

- [workers/__init__.py](workers/__init__.py) — `ALL_WORKERS` (6 Worker, Diamond shortcut)
- [formats.py](formats.py) — 7 个 Material 定义
- [pipeline.py](pipeline.py) — `build_pipeline()`
- [sop_extractor.py](sop_extractor.py) — SOP 提取工具函数
- [requirement_writer.py](requirement_writer.py) — 需求文档生成工具
- [_archive/routers_legacy.py](_archive/routers_legacy.py) — 原 Router 实现

## 架构决策

### D1 — Diamond Shortcut 迁移

6 个 Router 中含 LLM 调用（NoiseFilter/SOPGenerator/ReqWriter）和 SubPipelineRouter（WFCaller），采用 Diamond shortcut: `class XxxWorker(Worker, _LegacyRouter)`. 业务逻辑保留在 `_archive/routers_legacy.py`。

### D2 — 3 个 LLM 节点串行

NoiseFilter → SOPGenerator → ReqWriter 三个 LLM 节点串行，每步基于上一步的结果。这个设计简单清晰，但三步 LLM 代价较高。

### D3 — WFCaller 调用 workflow-factory

WFCallerRouter（现 WFCallerWorker）继承 SubPipelineRouter，实际重写了 `run()` 调用 `dispatch("workflow-factory", ...)`。

### D4 — Registrar 写入 pipeline_index

RegistrarRouter（现 RegistrarWorker）确定性将 workflow-factory 产出的代码写入 pipeline_index 表，是整个归纳流程的最终落地点。

### D5 — 被 pattern_discovery 触发

pattern_discovery 的 InductionDispatcherWorker 通过 `dispatch("trace-induction", ...)` 调用本管线。两者通过 dispatch 解耦。

## 数据流 / 拓扑

```
ti.task (source)
  → TraceReaderWorker (DB读取)
  → ti.trace-data (internal)
  → NoiseFilterWorker (LLM标注)
  → ti.essential (internal)
  → SOPGeneratorWorker (LLM提炼)
  → ti.sop (internal)
  → ReqWriterWorker (LLM转化)
  → ti.requirement (internal)
  → WFCallerWorker (SubPipeline)
  → ti.wf-result (internal)
  → RegistrarWorker (DB写入)
  → ti.done (sink)
```

## 已知局限

1. **3 LLM 节点代价高** — 噪音过滤 + SOP 生成 + 需求写作三次 LLM 调用。**升级路径**: 合并为 Agent Worker 一次性提炼（R-19）。

2. **Diamond 体未真迁移** — 业务逻辑仍在 _archive/。**升级路径**: Stage 3 低优先级。

3. **WFCaller 依赖 workflow-factory 可用性** — 若 workflow-factory 不可用则整链 FAIL。**升级路径**: 加入 WFCaller 的容错/重试逻辑。

## 新哲学对齐（Phase D · 2026-04-20）

### Material 层（F-16/17/18/19）

| 条款 | 状态 | 说明 |
|---|---|---|
| F-16 kind 三分 | ✅ | task=source; trace-data/essential/sop/requirement/wf-result=internal; done=sink |
| F-17 Workspace 大明文 | N/A | 无大 payload 走 workspace |
| F-18 Job × Material 绑定 | N/A | 传统 pipeline，待新 Runtime |
| F-19 kind.* tag 必填 | ✅ | Phase D 修正：7 条 Material 全部补 kind.* |

### Worker 层（R-18~R-25）

| 条款 | 状态 | 说明 |
|---|---|---|
| R-18 粒度 | ✅ | 6 Worker 各有完整职责 + FORMAT 边界 |
| R-19 Agent Worker 升级 | ⚠️ 待评估 | 3 LLM 节点串行可合并为 Agent Worker；当前先 grandfathered |
| R-20 Agent Worker 三件套 | ⚠️ 待评估 | 同上 |
| R-21 Diagnosis Agent Worker | N/A | |
| R-22 WorkspaceWriterWorker | N/A | 无 workspace 文件写入 |
| R-23 Verdict.output 平铺 | ✅ | 所有 Worker 输出无嵌套 format_id |
| R-24 FORMAT_IN_MODE | N/A | 所有 Worker FORMAT_IN 为单 str |
| R-25 子 job | N/A | 无 _emit_as_new_job |

### Team 层（P-13~P-17）

| 条款 | 状态 | 说明 |
|---|---|---|
| P-13 声明即消费 | ✅ | 各 Worker 只消费 FORMAT_IN 声明的 Material |
| P-14~17 Workspace 目录 | N/A | |

**结论**: F-19 缺口已修正。Diamond shortcut 完成。R-19/R-20 LLM 节点合并升级为 grandfathered 记录。

## 参考资料

- [workers/](workers/) — 6 个 Worker (Diamond shortcut)
- [formats.py](formats.py) — 7 个 Material
- [_archive/routers_legacy.py](_archive/routers_legacy.py) — 原 Router 实现
- ../pattern_discovery/ — 上游触发者
- ../workflow_factory/ — WFCaller 调用目标
