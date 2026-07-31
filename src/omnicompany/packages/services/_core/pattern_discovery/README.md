<!-- [OMNI] origin=ai-ide domain=services/pattern_discovery ts=2026-05-04T15:45:00Z type=doc status=active agent=ai-ide belongs_to_service=pattern_discovery -->
<!-- [OMNI] summary="pattern_discovery service 自我叙事 README — 从历史行为压缩摘要发现重复操作模式, 触发 trace-induction 自动沉淀" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §四 模板严格写, 快速推进" -->
<!-- [OMNI] tags=readme,pattern_discovery,core,self-narrative -->
<!-- [OMNI] material_id="material:services._core.pattern_discovery.readme.self_narrative.md"-->

# pattern_discovery · 重复模式发现

> 从历史行为压缩摘要 (`compression_summaries`) 自动发现重复操作模式 (LLM 语义聚类), 触发 trace-induction 自动沉淀. 三节点串行: SummaryReader → PatternClusterer (LLM) → InductionDispatcher (async).

## 这是什么

pattern_discovery 是 omnicompany 的**重复模式发现 service**. 从历史 compression_summaries 表读未处理摘要, LLM 语义聚类找重复模式, 调 trace-induction 管线自动沉淀.

## 解决什么 / 不解决什么

**解决**: 历史行为重复模式自动发现 / 触发 trace-induction 沉淀.
**不解决**: 手动定义的模式 / 实时模式发现 / session_id ↔ trace_id 关联问题 (D3 局限, 当前低命中率)

## 设计目的与最终目标

**设计目的**: 让 omnicompany 自学习 — 历史行为里有重复操作 = 可固化为 SpecPatch, 不需要每次 LLM 重做.

**最终目标**: 改进 InductionDispatcher 的 trace_id 关联逻辑 (当前 session_id ≠ trace_id 跳过率高); 加 embedding 路径替代 LLM 直判聚类; 改子 job 并行 (R-25) 替串行.

## 规划

- **当前 V1** (active, Phase D Diamond shortcut 2026-04-20)
- **下一步**: 改进 trace_id 关联逻辑

## 构成

- 入口与 Team → [pipeline.py](pipeline.py) (`build_pipeline()`)
- Materials (4 条) → [formats.py](formats.py): `pd.trigger` (source) / `pd.activities` / `pd.candidates` (internal) / `pd.done` (sink)
- Workers (3 个) → [workers/](workers/) (Diamond shortcut, 业务逻辑在 _archive)
  - `SummaryReaderWorker` (HARD)
  - `PatternClustererWorker` (SOFT LLM)
  - `InductionDispatcherWorker` (SOFT async, 调 trace-induction)
- 归档 → [_archive/routers_legacy.py](_archive/)

## 想了解更多

- [DESIGN.md](DESIGN.md) / [SKILL.md](SKILL.md)
- 下游被调 → ../trace_induction/
- 项目根 → [../../../../../README.md](../../../../../../README.md)
