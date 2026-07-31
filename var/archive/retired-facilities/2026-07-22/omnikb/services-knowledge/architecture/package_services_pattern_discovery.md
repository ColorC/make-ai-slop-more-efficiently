# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:34:57Z
---
omnikb_type: karch
id: kb.arch.package.services_pattern_discovery
name: 'Package: packages/services/pattern_discovery'
tags:
- topic.package
- layer.services
- domain.pattern_discovery
- architecture
maturity: living
summary: pattern-discovery — 后台模式发现管线（路径 B）
scope: omnicompany
code_anchors:
- src/omnicompany/packages/services/pattern_discovery/__init__.py
- src/omnicompany/packages/services/pattern_discovery/pipeline.py
- src/omnicompany/packages/services/pattern_discovery/routers.py
- src/omnicompany/packages/services/pattern_discovery/run.py
- src/omnicompany/packages/services/pattern_discovery/formats.py
---

# Package: packages/services/pattern_discovery

> **id**: `kb.arch.package.services_pattern_discovery` · **type**: karch · **maturity**: living

## Why this exists

`pattern_discovery` 包实现了 Omnicompany 架构中所谓"路径 B"的后台模式发现管线。其核心职责是从 `compression_summaries` 表中聚类发现 agent 在多个会话中反复执行的操作模式，并对筛选出的候选模式自动触发 trace-induction 管线，将其沉淀为可复用的 pipeline。

这一机制属于系统的自进化通路之一：当 agent 积累了足够多的行为保全摘要之后，无需人工介入，后台管线即可识别出高频重复模式并主动完成归纳，形成新的可调用能力。与之对比的是 `kb.arch.package.services_trace_induction` 所描述的"路径 C"——那条路径由用户主动触发轨迹归纳，而路径 B（本包）则是完全后台自动驱动。

`__init__.py` 中的 docstring 明确说明：该包的工作流是从压缩摘要中聚类 → 对候选模式调用 trace-induction → 自动沉淀为可复用 pipeline，呈现出 meta 层面的"造工作流的工作流"特性。

> _来源: `src/omnicompany/packages/services/pattern_discovery/__init__.py` seed_description_

## How it works

管线由 `pipeline.py` 中的 `build_pipeline()` 函数构建，返回一个 `PipelineSpec` 对象，id 为 `"pattern-discovery"`。管线拓扑为线性 4 节点（含 EMIT）：

`summary_reader` → `pattern_clusterer` → `induction_dispatcher` → EMIT

各节点均以 `PipelineNode` 定义，`kind` 均为 `NodeKind.ANCHOR`，内嵌 `AnchorSpec`：

- **`summary_reader`**（`ValidatorKind.HARD`）：使用 `SummaryReaderRouter` 实现。通过 `open_db` 对 SQLite 数据库执行确定性 SQL 查询，读取 `compression_summaries` 表中 `checked = 0` 的未处理行，将各行的 `activities` JSON 字段展平为列表，并为每条 activity 附加 `_summary_id` 和 `_session_id`。若表不存在、数据库读取失败或 activities 全为空，则返回 `VerdictKind.FAIL` 令管线 HALT。
- **`pattern_clusterer`**（`ValidatorKind.SOFT`）：使用 `PatternClustererRouter` 实现，依赖注入 `LLMClient`。通过 `_CLUSTER_PROMPT` 模板向 LLM 提交 activities 列表，要求 LLM 将目的相同或高度相似的操作归组，只输出出现次数 `>= min_cluster_size` 的聚类，结果以严格 JSON 格式返回（字段 `clusters`，含 `purpose_summary`、`member_indices`、`count`、`domain`）。注释说明 embedding 方案降级为 LLM 直判。
- **`induction_dispatcher`**（`ValidatorKind.SOFT`）：使用 `InductionDispatcherRouter` 实现，对每个候选模式调用 trace-induction 子管线（引入了 `SubPipelineRouter`）。

边关系通过 `PipelineEdge` 列表定义，条件均为 `VerdictKind.PASS`，任一节点 FAIL 则 HALT 终止。

基于当前代码片段，`PatternClustererRouter` 和 `InductionDispatcherRouter` 的完整实现（含 LLM 调用逻辑和 trace-induction 子管线调用方式）在 `routers.py` 片段中截断，需阅读完整 `routers.py` 才能确认。

> _来源: `src/omnicompany/packages/services/pattern_discovery/pipeline.py`, `src/omnicompany/packages/services/pattern_discovery/routers.py`, `src/omnicompany/packages/services/pattern_discovery/run.py`_

## Public surface

该模块对外暴露的公开接口：

**函数**
- `build_pipeline() -> PipelineSpec`（`pipeline.py`）：构建并返回管线拓扑规格，id 为 `"pattern-discovery"`。
- `build_bindings(input_dict, *, model) -> dict[str, Router]`（`run.py`）：构建节点名到 Router 实例的绑定字典，可选注入 `model` 参数覆盖 LLM 模型。
- `register_formats(registry: FormatRegistry) -> None`（`formats.py`）：将本包所有 Format 注册到传入的 `FormatRegistry`。

**Router 类**（`routers.py`，由 `run.py` 显式导入使用）
- `SummaryReaderRouter`
- `PatternClustererRouter`
- `InductionDispatcherRouter`

**Format id**（`formats.py`）
- `pd.trigger`
- `pd.activities`
- `pd.candidates`
- `pd.done`

**常量**
- `ALL_FORMATS`（`formats.py`）：包含上述 4 个 `Format` 对象的列表。

**管线 id**
- `"pattern-discovery"`（`PipelineSpec.id`）

> _来源: `src/omnicompany/packages/services/pattern_discovery/pipeline.py`, `src/omnicompany/packages/services/pattern_discovery/routers.py`, `src/omnicompany/packages/services/pattern_discovery/run.py`, `src/omnicompany/packages/services/pattern_discovery/formats.py`_

## Internal structure

模块由 5 个文件构成，职责划分清晰：

| 文件 | 职责 |
|------|------|
| `__init__.py` | 包入口与 docstring，声明模块用途 |
| `pipeline.py` | 管线拓扑定义，`build_pipeline()` 返回 `PipelineSpec` |
| `routers.py` | 3 个节点的 Router 实现（`SummaryReaderRouter`、`PatternClustererRouter`、`InductionDispatcherRouter`） |
| `formats.py` | 4 个 Format 对象定义及 `register_formats()` 注册函数 |
| `run.py` | 组装入口，`build_bindings()` 将 Router 实例与 `LLMClient` 绑定，并调用 `register_formats` |

从 `run.py` 的 import 关系可以看到依赖方向：`run.py` 依赖 `formats.py`（注册）、`pipeline.py`（构建拓扑）、`routers.py`（实例化 Router）以及运行时层的 `omnicompany.runtime.routing.router.Router`、`omnicompany.runtime.llm.llm.LLMClient`、`omnicompany.protocol.format.create_builtin_registry`。

`routers.py` 则还引入了 `omnicompany.runtime.exec.sub_pipeline.SubPipelineRouter`，说明 `InductionDispatcherRouter` 通过子管线调用机制触发 trace-induction，而非直接调用函数。

> _来源: `src/omnicompany/packages/services/pattern_discovery/__init__.py`, `src/omnicompany/packages/services/pattern_discovery/pipeline.py`, `src/omnicompany/packages/services/pattern_discovery/routers.py`, `src/omnicompany/packages/services/pattern_discovery/formats.py`, `src/omnicompany/packages/services/pattern_discovery/run.py`_

## Files

- `src/omnicompany/packages/services/pattern_discovery/__init__.py` — 包入口，docstring 声明模块定位为"后台模式发现管线（路径 B）"，描述从 compression_summaries 聚类到 trace-induction 的完整目的。
- `src/omnicompany/packages/services/pattern_discovery/pipeline.py` — 定义 4 节点线性管线拓扑，`build_pipeline()` 返回 id 为 `"pattern-discovery"` 的 `PipelineSpec`，包含节点、边和路由规则。
- `src/omnicompany/packages/services/pattern_discovery/routers.py` — 实现 3 个节点对应的 Router 类：`SummaryReaderRouter`（确定性 DB 读取）、`PatternClustererRouter`（LLM 语义聚类）、`InductionDispatcherRouter`（调用 trace-induction 子管线）。
- `src/omnicompany/packages/services/pattern_discovery/formats.py` — 定义管线数据流的 4 个 Format：`PD_TRIGGER`、`PD_ACTIVITIES`、`PD_CANDIDATES`、`PD_DONE`，并提供 `register_formats()` 注册入口。
- `src/omnicompany/packages/services/pattern_discovery/run.py` — 组装模块，`build_bindings()` 初始化 `LLMClient`、注册 formats、实例化所有 Router 并返回节点绑定字典。

> _来源: `src/omnicompany/packages/services/pattern_discovery/__init__.py`, `src/omnicompany/packages/services/pattern_discovery/pipeline.py`, `src/omnicompany/packages/services/pattern_discovery/routers.py`, `src/omnicompany/packages/services/pattern_discovery/formats.py`, `src/omnicompany/packages/services/pattern_discovery/run.py`_

## Related

与本包直接相关的 KB 条目：

- `kb.arch.package.services_trace_induction` — 本包的 `induction_dispatcher` 节点在执行时调用 trace-induction 管线（路径 C），两者形成上下游关系：pattern_discovery 发现候选模式后将其交给 trace-induction 完成归纳沉淀。
- `kb.arch.package.services_workflow_factory` — 本包在语义层面属于"造工作流的工作流"范畴，与 workflow_factory 共享 meta 层定位。
- `kb.arch.package.services_absorption` — absorption 包也涉及从已有材料中提取并归纳知识的模式，与本包的聚类→归纳流程在架构目的上相近。
- `kb.arch.package.services_knowledge` — 本包生成的归纳结果最终可能流入 OmniKB 知识库，与 knowledge 包存在潜在下游关系。

> _来源: kb_context (`kb.arch.package.services_trace_induction`, `kb.arch.package.services_workflow_factory`, `kb.arch.package.services_absorption`, `kb.arch.package.services_knowledge`)_

## Known limitations

从可见代码中可以识别以下局限与未完全实现的区域：

1. **embedding 方案未实现**：`routers.py` 注释明确写明 `pattern_clusterer` 的语义聚类策略为"embedding 降级为 LLM 直判"，说明原计划使用向量 embedding 做相似度聚类，但当前实现退化为纯 LLM 文本判断，精度和可扩展性受限于 LLM 上下文窗口。

2. **`_CLUSTER_PROMPT` 片段截断**：代码片段在 `_CLUSTER_PROMPT` 模板的"规则"部分（第 132 行后）截断，`PatternClustererRouter.run()` 和 `InductionDispatcherRouter` 的完整实现均未可见，无法确认聚类结果解析逻辑和子管线调用方式是否存在边界问题。

3. **`checked` 字段更新未见于片段**：`SummaryReaderRouter` 查询 `checked = 0` 的未处理摘要，但可见代码中未出现将已处理摘要标记为 `checked = 1` 的写回逻辑，是否存在重复处理风险需阅读完整 `routers.py` 或其他存储操作文件确认。

4. **`min_cluster_size` 默认值硬编码**：`SummaryReaderRouter.run()` 中 `min_cluster_size` 默认值为 `3`，`similarity_threshold` 默认值为 `0.85`，这些参数以字面量形式分散于代码中，未见统一配置管理。

> _来源: `src/omnicompany/packages/services/pattern_discovery/routers.py`, `src/omnicompany/packages/services/pattern_discovery/pipeline.py`_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 5 code anchors (15741 chars), 0 plan docs (0 chars), 25 kb refs
