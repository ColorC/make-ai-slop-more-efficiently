<!-- [OMNI] origin=claude-code domain=services/pattern_discovery ts=2026-05-04T15:45:00Z type=doc status=active belongs_to_service=pattern_discovery -->
<!-- [OMNI] material_id="material:core.pattern_discovery.service_architecture.design.md" -->

# pattern_discovery · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部**.

## 状态
- **版本**: V1 (Phase D Diamond shortcut 2026-04-20)
- **成熟度**: active
- **下一步**: 改进 InductionDispatcher 中 trace_id 关联逻辑（当前 session_id ≠ trace_id 导致跳过）

## 核心接口

- [workers/__init__.py](workers/__init__.py) — `ALL_WORKERS` (Diamond shortcut)
- [formats.py](formats.py) — 4 个 Material 定义
- [pipeline.py](pipeline.py) — `build_pipeline()`
- [_archive/routers_legacy.py](_archive/routers_legacy.py) — 原 Router 实现（Phase D 前）

## 架构决策

### D1 — Diamond Shortcut 迁移模式

`PatternClustererWorker` 和 `InductionDispatcherWorker` 因复杂性（async run + SubPipelineRouter 继承）采用 Diamond 模式：`class XxxWorker(Worker, _LegacyRouter)`. 业务逻辑保留在 `_archive/routers_legacy.py`，Workers 是轻量声明层。

### D2 — LLM 聚类降级策略

`PatternClustererWorker`（原 Router）有 embedding 优先、LLM 降级的设计意图，但当前只实现了 LLM 直判路径。embedding 路径留待后续实现。

### D3 — session_id ≠ trace_id 关联问题

`compression_summaries.session_id` 与 `intent_steps.trace_id` 不同，InductionDispatcher 尝试通过关键词搜索匹配，但实际命中率低。大多数候选因此跳过（status=skipped）。这是已知局限，不是 Bug。

### D4 — SubPipelineRouter 继承

`InductionDispatcherRouter` 继承 `SubPipelineRouter` 但实际重写了全部 `run()` 逻辑，直接调 `dispatch("trace-induction", ...)` 而非 super().run()。SubPipelineRouter 的框架逻辑未被使用。

### D5 — async run() 在 Diamond 中的 MRO

runner.py 会检测 `inspect.iscoroutinefunction(router.run)` 并正确 `await` 异步 Worker。Diamond 继承保持了原 async run() 语义。

## 数据流 / 拓扑

```
pd.trigger (source)
  → SummaryReaderWorker
  → pd.activities (internal)
  → PatternClustererWorker (LLM)
  → pd.candidates (internal)
  → InductionDispatcherWorker
  → pd.done (sink)
```

## 已知局限

1. **session_id ↔ trace_id 断链** — D3 描述。**升级路径**: compression pipeline 写 summaries 时一并记录 trace_id。

2. **PatternClusterer 无 embedding 路径** — D2 描述。**升级路径**: 接入 embedding 服务后按余弦相似度聚类。

3. **InductionDispatcher 仅串行处理** — 候选逐个调用 trace-induction，耗时长。**升级路径**: 改为 R-25 子 job 并行触发。

4. **Diamond 体未真迁移** — 业务逻辑仍在 _archive/。**升级路径**: Stage 3 真代码搬家（低优先级）。

## 新哲学对齐（Phase D · 2026-04-20）

> 对照 13 条新世界条款逐项评估。

### Material 层（F-16/17/18/19）

| 条款 | 状态 | 说明 |
|---|---|---|
| F-16 kind 三分 | ✅ | trigger=source; activities+candidates=internal; done=sink |
| F-17 Workspace 大明文 | N/A | 无大 payload，activities 为内存 dict 列表 |
| F-18 Job × Material 绑定 | N/A | 传统 pipeline，MaterialDispatcher 链路待新 Runtime |
| F-19 kind.* tag 必填 | ✅ | Phase D 修正：4 条 Material 全部补 kind.* |

### Worker 层（R-18~R-25）

| 条款 | 状态 | 说明 |
|---|---|---|
| R-18 粒度 | ✅ | 3 Worker 各有完整职责 + FORMAT 边界 |
| R-19 Agent Worker 升级 | N/A | PatternClusterer 为单次 LLM 调用，非动态 Material 需求 |
| R-20 Agent Worker 三件套 | N/A | 同上 |
| R-21 Diagnosis Agent Worker | N/A | 无需对上游质疑 |
| R-22 WorkspaceWriterWorker | N/A | 无 workspace 文件写入 |
| R-23 Verdict.output 平铺 | ✅ | 3 Worker 输出均无嵌套 format_id 包裹 |
| R-24 FORMAT_IN_MODE | N/A | 所有 Worker FORMAT_IN 为单 str |
| R-25 子 job | N/A | InductionDispatcher 串行不用 _emit_as_new_job（见局限 3） |

### Team 层（P-13~P-17）

| 条款 | 状态 | 说明 |
|---|---|---|
| P-13 声明即消费 | ✅ | 各 Worker 只消费 FORMAT_IN 声明的 Material |
| P-14~17 Workspace 目录 | N/A | 无 workspace 目录约定 |

**结论**: F-19 缺口已修正。Diamond shortcut 完成（workers/ + kind.* + _archive/）。

## 参考资料

- [workers/](workers/) — 3 个 Worker (Diamond shortcut)
- [formats.py](formats.py) — 4 个 Material
- [_archive/routers_legacy.py](_archive/routers_legacy.py) — 原 Router 实现
- ../trace_induction/ — 下游被调管线
