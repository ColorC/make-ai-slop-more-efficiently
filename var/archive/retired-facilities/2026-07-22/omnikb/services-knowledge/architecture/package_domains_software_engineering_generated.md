# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:28:29Z
---
omnikb_type: karch
id: kb.arch.package.domains_software_engineering_generated
name: 'Package: packages/domains/software_engineering/generated'
tags:
- topic.package
- layer.domains
- domain.generated
- architecture
maturity: living
summary: 'Auto-generated pipeline: generated'
scope: omnicompany
code_anchors:
- src/omnicompany/packages/domains/software_engineering/generated/__init__.py
- src/omnicompany/packages/domains/software_engineering/generated/pipeline.py
- src/omnicompany/packages/domains/software_engineering/generated/routers.py
- src/omnicompany/packages/domains/software_engineering/generated/run.py
- src/omnicompany/packages/domains/software_engineering/generated/formats.py
---

# Package: packages/domains/software_engineering/generated

> **id**: `kb.arch.package.domains_software_engineering_generated` · **type**: karch · **maturity**: living

## Why this exists

该包是 Omnicompany `software_engineering` 域下的一条自动生成管线，其 `__init__.py` 中的文件头注释明确标注 `origin=claude-code`，表明整个包由代码生成工具自动产出，而非手工编写。管线命名为 `generated`，对应一条文本统计 (text statistics) 工作流：接收用户提交的文本输入，先执行合法性验证，再对通过验证的文本进行字数、行数、字符数的确定性统计计算，最终输出统计指标。

该包存在的直接目的是提供一条完整的、可运行的管线示例，展示 Omnicompany 管线框架中 `AnchorSpec`、`PipelineSpec`、`Router` 等核心组件的组合方式。由于没有对应的 plan 文档，当前可见材料中无法进一步确认其是否作为功能性产品管线还是框架验证/示例管线被设计出来。

> _来源: seed_description, `src/omnicompany/packages/domains/software_engineering/generated/__init__.py`_

## How it works

管线由 `pipeline.py` 中的 `build_pipeline()` 函数构建，返回一个 `PipelineSpec` 对象，包含两个节点和一条边：

- **`validate_input_node`**：类型为 `NodeKind.ANCHOR`，封装 `AnchorSpec`（id `a_validate_input_node`），绑定一个 `ValidatorKind.HARD` 的 `ValidatorSpec`。接收格式 `sw.text-input`，输出格式 `sw.input-check-result`。路由规则：`VerdictKind.PASS` 时转向 `calculate_stats_node`，`VerdictKind.FAIL` 时执行 `RouteAction.HALT`。
- **`calculate_stats_node`**：同样为 `NodeKind.ANCHOR`，接收 `sw.input-check-result`，输出 `sw.stats-metrics`。路由规则：`VerdictKind.PASS` 时执行 `RouteAction.EMIT`（最终输出），`VerdictKind.FAIL` 时 `RouteAction.HALT`。

两节点之间有一条 `PipelineEdge`，`condition="PASS"`，`feedback=False`。

实际执行逻辑在 `routers.py` 中实现。`ValidateInputRouter.run()` 检查 `input_data` 中是否存在 `"text"` 字段、字段值是否为非空字符串，三种失败情形均返回 `VerdictKind.FAIL`。`CalculateStatsRouter.run()` 通过 `text.split()`、`text.splitlines()`、`len(text)` 计算字数、行数、字符数，全程不调用 LLM，属于纯确定性计算。

`run.py` 中的 `build_bindings()` 将节点 id 字符串映射到对应 Router 实例，供运行时将 `PipelineSpec` 与具体实现绑定。

> _来源: `src/omnicompany/packages/domains/software_engineering/generated/pipeline.py`, `src/omnicompany/packages/domains/software_engineering/generated/routers.py`, `src/omnicompany/packages/domains/software_engineering/generated/run.py`_

## Public surface

该模块通过 `run.py` 的 `__all__` 显式声明对外暴露以下三个符号：

| 符号 | 来源文件 | 说明 |
|---|---|---|
| `build_pipeline` | `pipeline.py` | 返回 `PipelineSpec`，描述管线拓扑结构 |
| `build_bindings` | `run.py` | 返回 `dict[str, Router]`，将节点 id 映射到 Router 实例 |
| `register_formats` | `formats.py` | 将三个 Format 对象注册到 `FormatRegistry` |

此外，`formats.py` 中定义了三个 Format id，作为数据格式契约对外可见：

- `sw.text-input`：管线入口格式，要求 `{"text": string}`
- `sw.input-check-result`：验证结果格式，包含 `status`（PASS/FAIL）及条件字段
- `sw.stats-metrics`：最终输出格式，包含 `word_count`、`line_count`、`char_count`

> _来源: `src/omnicompany/packages/domains/software_engineering/generated/run.py`, `src/omnicompany/packages/domains/software_engineering/generated/formats.py`_

## Internal structure

包内共五个文件，职责清晰分离：

- `__init__.py`：包标识文件，仅含文件头注释，无实质导出逻辑。
- `pipeline.py`：管线拓扑层，通过 `build_pipeline()` 用 `PipelineSpec` + `PipelineNode` + `PipelineEdge` + `AnchorSpec` 描述节点间连接关系与路由规则，不含执行逻辑。
- `routers.py`：执行层，定义 `ValidateInputRouter` 和 `CalculateStatsRouter` 两个 `Router` 子类，各自实现 `run()` 方法，处理实际数据。
- `formats.py`：格式注册层，定义 `TEXT_INPUT`、`INPUT_CHECK_RESULT`、`STATS_METRICS` 三个 `Format` 对象，并通过 `register_formats()` 注册至 `FormatRegistry`。Format 间通过 `semantic_preconditions` 字段显式声明依赖链：`sw.stats-metrics` → `sw.input-check-result` → `sw.text-input`。
- `run.py`：组装入口层，导入 `build_pipeline`、两个 Router 类及 `register_formats`，通过 `build_bindings()` 将节点 id 与 Router 实例绑定，并通过 `__all__` 统一声明模块公共接口。

`run.py` 是唯一跨文件 import 的聚合点，导入关系为 `run → pipeline`、`run → routers`、`run → formats`。

> _来源: `src/omnicompany/packages/domains/software_engineering/generated/run.py`, `src/omnicompany/packages/domains/software_engineering/generated/pipeline.py`, `src/omnicompany/packages/domains/software_engineering/generated/routers.py`, `src/omnicompany/packages/domains/software_engineering/generated/formats.py`_

## Files

- `src/omnicompany/packages/domains/software_engineering/generated/__init__.py`：包初始化文件，含自动生成标记注释，无实质代码。
- `src/omnicompany/packages/domains/software_engineering/generated/pipeline.py`：管线拓扑定义，通过 `build_pipeline()` 构建包含两节点一边的 `PipelineSpec`。
- `src/omnicompany/packages/domains/software_engineering/generated/routers.py`：Router 实现文件，定义 `ValidateInputRouter` 和 `CalculateStatsRouter` 两个执行类。
- `src/omnicompany/packages/domains/software_engineering/generated/run.py`：模块组装入口，定义 `build_bindings()` 并声明 `__all__`，供外部运行时调用。
- `src/omnicompany/packages/domains/software_engineering/generated/formats.py`：格式定义与注册文件，定义 `sw.text-input`、`sw.input-check-result`、`sw.stats-metrics` 三个 Format，并提供 `register_formats()` 注册函数。

> _来源: code_snippets 中出现的全部五个文件路径_

## Related

在 `software_engineering` 域下，以下同域管线包与本包结构最为相近（同样是自动生成或具有类似管线结构）：

- `kb.arch.package.domains_software_engineering_debugger`：同属 `software_engineering` 域，为调试工作流管线。
- `kb.arch.package.domains_software_engineering_equiv_test`：同属 `software_engineering` 域，跨语言语义等价性测试管线。
- `kb.arch.package.domains_software_engineering_lang_rewrite`：同属 `software_engineering` 域，跨语言改写管线。
- `kb.arch.package.domains_voxelcraft_mechanics_evolver`：同为标注 `Auto-generated pipeline` 的自动生成管线，结构上有参考价值。
- `kb.arch.package.vendors_mcp_builder`：同为标注 `Auto-generated` 的自动生成包。

> _来源: kb_context_

## Known limitations

从代码中可观察到以下明显局限或待注意点：

1. **异常吞没**：`CalculateStatsRouter.run()` 的 `except` 块捕获所有异常后返回全零统计值 `{"word_count": 0, "line_count": 0, "char_count": 0}`，并且返回 `VerdictKind.FAIL`，但错误信息未被记录或传递，调用方无法区分"输入为空"与"统计计算异常"两种失败情形。

2. **`input_dict` 未使用**：`build_bindings()` 接受 `input_dict` 参数，但函数体第一行即 `_ = input_dict`，注释说明"当前管线不使用输入配置"，意味着 Router 实例化暂不支持运行时参数传入。

3. **无 TODO/FIXME 注释**：代码中未出现任何 `TODO`、`FIXME`、`XXX` 标记，无法从注释层面识别已知待办事项。

4. **管线名称为 `generated`**：`PipelineSpec` 的 `name` 字段值为 `"generated"`，与目录名相同，未体现具体业务语义，可能是自动生成时的占位命名。

> _来源: `src/omnicompany/packages/domains/software_engineering/generated/routers.py`, `src/omnicompany/packages/domains/software_engineering/generated/run.py`, `src/omnicompany/packages/domains/software_engineering/generated/pipeline.py`_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 5 code anchors (11670 chars), 0 plan docs (0 chars), 25 kb refs
