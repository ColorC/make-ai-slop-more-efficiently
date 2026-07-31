# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:26:33Z
---
omnikb_type: karch
id: kb.arch.package.domains_software_engineering_debugger
name: 'Package: packages/domains/software_engineering/debugger'
tags:
- topic.package
- layer.domains
- domain.debugger
- architecture
maturity: living
summary: debugger — 通用假设驱动调试工作流
scope: omnicompany
code_anchors:
- src/omnicompany/packages/domains/software_engineering/debugger/__init__.py
- src/omnicompany/packages/domains/software_engineering/debugger/pipeline.py
- src/omnicompany/packages/domains/software_engineering/debugger/routers.py
- src/omnicompany/packages/domains/software_engineering/debugger/run.py
- src/omnicompany/packages/domains/software_engineering/debugger/formats.py
---

# Package: packages/domains/software_engineering/debugger

> **id**: `kb.arch.package.domains_software_engineering_debugger` · **type**: karch · **maturity**: living

## Why this exists

debugger 包的存在是为了提供一套跨语言、通用的假设驱动调试工作流。其核心思路在 `__init__.py` 的 docstring 中已明确阐述：管线覆盖"读错误 → 追踪根因 → 假设验证循环 → 修复复测"四个阶段，但整体结构不是线性的，而是以"假设-证据-修正"的累积循环为驱动。这与 Omnicompany 其他直线型管线（如 lang_rewrite、equiv_test）的结构有本质区别：调试过程中随时可能因证据否定假设而回到更早的节点重新提出新假设，因此整个工作流是一个带反馈回路的 DAG，而非一次性通过的流水线。

该包的适用范围被定义为"跨语言"，从 `ErrorAnalyzerRouter` 的实现中可以看到它对 `.ts`、`.rs`、`.py`、`.js`、`.go` 五种文件扩展名都做了解析处理，印证了这一设计意图。

> _来源: `src/omnicompany/packages/domains/software_engineering/debugger/__init__.py`, `src/omnicompany/packages/domains/software_engineering/debugger/routers.py`_

## How it works

管线拓扑定义在 `pipeline.py` 的 `build_pipeline()` 函数中，通过 `PipelineSpec`、`PipelineNode`、`PipelineEdge` 搭建 DAG。整体包含 10 个节点，在 `routers.py` 的文件头注释中分三类列出：

- **确定性 Transformer（3 个）**：`ContextInitRouter`（`context_init`）、`EvidenceCollectorRouter`（`evidence_collector`）、`RegressionToContextRouter`（`regression_to_context`），使用 `TransformMethod.RULE`，不调用 LLM。
- **SOFT/LLM 节点（5 个）**：`ErrorAnalyzerRouter`（`error_analyzer`）、`HypothesisGeneratorRouter`（`hypothesis_generator`）、`ProbeDesignerRouter`（`probe_designer`）、`FixerRouter`（`fixer`）、`RegressionAnalyzerRouter`（`regression_analyzer`），内部通过 `LLMClient` 调用模型，解析 JSON 输出，并据此发出 `Verdict`。
- **HARD/执行节点（2 个）**：`ProbeExecutorRouter`（`probe_executor`）、`TesterRouter`（`tester`），负责实际运行探针或测试。

核心循环结构如 `pipeline.py` DAG 注释所示：`evidence_collector` 是所有回路的归一点，`probe_executor` 证否假设时重新回到 `evidence_collector`，`tester` 失败时经 `regression_analyzer` 和 `regression_to_context` 再次流回 `evidence_collector`，从而驱动新一轮假设生成。整个调试状态由贯穿全程的 `debug-context`（数据结构定义在 `_empty_context()` 函数中）承载，包含 `errors`、`hypotheses`、`patches`、`excluded_files`、`current_hypothesis`、`iteration` 六个字段。

`ErrorAnalyzerRouter.run()` 展示了典型 LLM 节点的执行模式：解析错误输出中的文件路径、读取相关源文件、构造 prompt、调用 `LLMClient.call()`、提取 JSON、返回 `Verdict`。

基于当前代码片段只能看到 `error_analyzer` 节点和 `ContextInit` 的完整实现，以及管线 DAG 拓扑。其余七个 Router 的具体实现（`HypothesisGeneratorRouter`、`ProbeDesignerRouter` 等）在片段中未展示，完整机制需读 `routers.py` 的后续部分。

> _来源: `src/omnicompany/packages/domains/software_engineering/debugger/pipeline.py`, `src/omnicompany/packages/domains/software_engineering/debugger/routers.py`, `src/omnicompany/packages/domains/software_engineering/debugger/run.py`_

## Public surface

该模块对外暴露的接口如下：

**函数：**
- `build_pipeline() -> PipelineSpec`（`pipeline.py`）：构建并返回调试管线的完整 DAG 拓扑。
- `build_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]`（`run.py`）：实例化全部 10 个 Router 并以节点 id 为键返回绑定字典，支持通过 `input_dict` 传入 `model` 参数覆盖默认模型。
- `register_formats(registry: FormatRegistry) -> None`（`formats.py`）：将模块定义的所有 Format 注册到全局 `FormatRegistry`，注册前会检查 `registry.is_registered(fmt.id)` 避免重复。

**Format id（全部以 `debug.` 为前缀）：**
- `debug.error-report`、`debug.error-analysis`、`debug.trace-evidence`
- `debug.hypothesis`、`debug.probe-plan`、`debug.probe-result`
- `debug.fix-patch`、`debug.test-feedback`、`debug.regression-analysis`
- `debug.verified-fix`、`debug.debug-context`、`debug.enriched-context`

**Router 类（通过 `run.py` 的 `build_bindings` 聚合导出）：**
`ErrorAnalyzerRouter`、`ContextInitRouter`、`HypothesisGeneratorRouter`、`ProbeDesignerRouter`、`ProbeExecutorRouter`、`EvidenceCollectorRouter`、`FixerRouter`、`TesterRouter`、`RegressionAnalyzerRouter`、`RegressionToContextRouter`

> _来源: `src/omnicompany/packages/domains/software_engineering/debugger/pipeline.py`, `src/omnicompany/packages/domains/software_engineering/debugger/run.py`, `src/omnicompany/packages/domains/software_engineering/debugger/formats.py`_

## Internal structure

该包由四个文件组成，分工明确：

| 文件 | 职责 |
|---|---|
| `__init__.py` | 包入口，仅含 docstring，声明包的定位与核心设计原则 |
| `formats.py` | 数据契约层，定义全部 12 个 `Format` 对象并提供 `register_formats` 注册入口 |
| `pipeline.py` | 结构层，用 `PipelineNode`/`PipelineEdge`/`PipelineSpec` 声明 DAG 拓扑，引用 `omnicompany.protocol.pipeline` 和 `omnicompany.protocol.anchor` |
| `routers.py` | 执行层，实现全部 10 个 `Router` 子类，依赖 `omnicompany.runtime.routing.router.Router`、`omnicompany.runtime.llm.llm.LLMClient`，以及 Python 标准库 `json`、`subprocess`、`pathlib` |
| `run.py` | 组装层，`build_bindings` 将 10 个 Router 实例化并映射到节点 id，供运行时挂载 |

`formats.py` 定义了上游到下游的类型继承关系：例如 `debug.error-analysis` 以 `debug.error-report` 为 parent，`debug.verified-fix` 以 `debug.fix-patch` 为 parent，`debug.debug-context` 以 `agent-state` 为 parent，`debug.enriched-context` 以 `debug.debug-context` 为 parent，形成语义层级。

`pipeline.py` 与 `routers.py` 之间是声明与实现的分离关系：`pipeline.py` 只描述 DAG 拓扑和节点规格（`AnchorSpec`、`TransformerSpec`、`ValidatorSpec`、`Route`），`routers.py` 提供具体的运行时行为，`run.py` 的 `build_bindings` 将两者在运行时对接。

> _来源: `src/omnicompany/packages/domains/software_engineering/debugger/pipeline.py`, `src/omnicompany/packages/domains/software_engineering/debugger/routers.py`, `src/omnicompany/packages/domains/software_engineering/debugger/run.py`, `src/omnicompany/packages/domains/software_engineering/debugger/formats.py`, `src/omnicompany/packages/domains/software_engineering/debugger/__init__.py`_

## Files

- `src/omnicompany/packages/domains/software_engineering/debugger/__init__.py`：包入口，以 docstring 形式声明该包的定位（"假设-证据-修正"累积循环）和适用范围（跨语言调试）。
- `src/omnicompany/packages/domains/software_engineering/debugger/formats.py`：定义调试工作流涉及的全部 12 种语义数据格式（从 `debug.error-report` 到 `debug.enriched-context`），并提供 `register_formats` 函数供外部注册到 `FormatRegistry`。
- `src/omnicompany/packages/domains/software_engineering/debugger/pipeline.py`：以 `build_pipeline()` 函数声明包含 10 个节点的调试 DAG 拓扑，明确各节点的类型（ANCHOR/TRANSFORMER）、格式契约和路由规则。
- `src/omnicompany/packages/domains/software_engineering/debugger/routers.py`：实现全部 10 个 Router 类（3 个确定性 Transformer、5 个 LLM 节点、2 个执行节点），以及贯穿全程的 `_empty_context()` 辅助函数，是包内代码量最大的文件。
- `src/omnicompany/packages/domains/software_engineering/debugger/run.py`：提供 `build_bindings()` 函数，将全部 Router 实例以节点 id 为键组装成字典，供运行时挂载到管线。

> _来源: `src/omnicompany/packages/domains/software_engineering/debugger/__init__.py`, `src/omnicompany/packages/domains/software_engineering/debugger/formats.py`, `src/omnicompany/packages/domains/software_engineering/debugger/pipeline.py`, `src/omnicompany/packages/domains/software_engineering/debugger/routers.py`, `src/omnicompany/packages/domains/software_engineering/debugger/run.py`_

## Related

与当前条目最相关的 KB 条目：

- `kb.arch.package.domains_software_engineering_lang_rewrite`：同属 `software_engineering` 域，lang_rewrite 是跨语言改写管线，debugger 包的 `ErrorAnalyzerRouter` 同样支持多语言场景，两者共用语言场景。
- `kb.arch.package.domains_software_engineering_equiv_test`：等价性测试管线，与 debugger 的 `tester`/`regression_analyzer` 节点在测试反馈机制上存在概念关联。
- `kb.arch.pipeline.voxelcraft.engineering`：其管线描述为"GDD → code → compile → **debug loop**"，明确包含 debug loop 阶段，是 debugger 包潜在的调用方之一。
- `kb.arch.package.domains_software_engineering_generated`：同域包，可能与 debugger 共享部分基础设施。

当前 KB 中无直接描述 `Router` 基类或 `PipelineSpec` 协议层的条目，若需完整理解 debugger 包的运行机制，可能需要补写 `omnicompany.protocol.pipeline`、`omnicompany.protocol.anchor`、`omnicompany.runtime.routing.router` 相关的 karch 条目。

> _来源: kb_context 列表_

## Known limitations

基于当前可见代码，可以观察到以下明确存在的局限或未完成区域：

**节点成熟度标注的不对称性：**`pipeline.py` 中 `context_init` 节点标注为 `NodeMaturity.CRYSTALLIZED`（最成熟），`error_analyzer` 为 `NodeMaturity.GROWING`，而 `hypothesis_generator` 和 `probe_designer` 均为 `NodeMaturity.HYPOTHETICAL`（最不成熟）。这意味着假设生成和探针设计两个最核心的循环节点在代码作者看来仍属实验性阶段。

**代码片段截断：**`pipeline.py` 的代码片段在第 116 行 `probe_designer` 节点定义中途被截断，`probe_executor`、`evidence_collector`、`fixer`、`tester`、`regression_analyzer`、`regression_to_context` 六个节点的 `PipelineNode` 定义以及所有 `PipelineEdge` 的声明均未出现在可见片段中。`routers.py` 同样在 `ErrorAnalyzerRouter.run()` 内部被截断，`HypothesisGeneratorRouter` 等后续七个 Router 的实现代码未可见。

**`hypothesis_generator` 的失败处理：**从可见的 `pipeline.py` 片段中可看到，`HypothesisGenerator` 在 `VerdictKind.FAIL` 时路由到 `RouteAction.HALT` 并附注"无法提出新假设，需人工介入"——这意味着当 LLM 无法提出新假设时，整个管线会停止而非有其他自动恢复机制。

**`formats.py` 中的自引用：**`debug.hypothesis` Format 的 `parent` 字段被设为 `"debug.hypothesis"`（即自己引用自己），这在语义上可能是一个笔误或占位符，完整实现可能需要指向其他 parent。

> _来源: `src/omnicompany/packages/domains/software_engineering/debugger/pipeline.py`, `src/omnicompany/packages/domains/software_engineering/debugger/routers.py`, `src/omnicompany/packages/domains/software_engineering/debugger/formats.py`_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 5 code anchors (18337 chars), 0 plan docs (0 chars), 25 kb refs
