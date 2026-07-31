# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:29:36Z
---
omnikb_type: karch
id: kb.arch.package.domains_software_engineering_lang_rewrite
name: 'Package: packages/domains/software_engineering/lang_rewrite'
tags:
- topic.package
- layer.domains
- domain.lang_rewrite
- architecture
maturity: living
summary: lang_rewrite — 跨语言改写管线
scope: omnicompany
code_anchors:
- src/omnicompany/packages/domains/software_engineering/lang_rewrite/__init__.py
- src/omnicompany/packages/domains/software_engineering/lang_rewrite/pipeline.py
- src/omnicompany/packages/domains/software_engineering/lang_rewrite/routers.py
- src/omnicompany/packages/domains/software_engineering/lang_rewrite/run.py
- src/omnicompany/packages/domains/software_engineering/lang_rewrite/formats.py
---

# Package: packages/domains/software_engineering/lang_rewrite

> **id**: `kb.arch.package.domains_software_engineering_lang_rewrite` · **type**: karch · **maturity**: living

## Why this exists

`lang_rewrite` 包的目标是将 Omnicompany 自身的 Python 引擎层模块系统性地改写为 TypeScript 或 Rust，在整个过程中保持六元语义等价，并通过编译与等价性验证形成闭环。模块文档字符串明确描述了这一定位："将 Python 引擎层模块改写为 TypeScript / Rust，保持六元语义等价，通过编译 + 等价性验证闭环。"这是 Omnicompany 自举能力的组成部分——用自身管线处理自身代码库的跨语言迁移。

当前可见材料 (code/plan/kb context) 中没有专项 plan 文档描述更深层的设计动机，也没有相关 KExperiment 条目被关联到本包。上述解释完全来自 `__init__.py` 的 docstring。

> _来源: src/omnicompany/packages/domains/software_engineering/lang_rewrite/__init__.py (docstring)_

## How it works

管线是一个 14 节点 DAG，在 `build_pipeline()` 函数中以 `PipelineSpec` / `PipelineNode` / `PipelineEdge` 等协议类型构建，并分为五个逻辑阶段：

1. **分析阶段（确定性）**: `SourceAnalyzerRouter` 读取 Python 源文件，用 `ast.parse()` 解析 AST，提取公开接口（`ClassDef` / `FunctionDef` / `AsyncFunctionDef`）、内部与外部依赖；`DependencyMapperRouter` 再将其转为依赖图（`rewrite.dependency-graph`），并以 `ENGINE_TOPO_ORDER` 常量给出拓扑排序顺序。
2. **上下文扫描（fan-out）**: `DemandExtractorRouter` 扫描下游调用需求（`rewrite.demand-set`）；`SupplyScannerRouter` 扫描供给侧真实签名（`rewrite.supply-map`），两者从 `dependency-graph` 并发分出。
3. **翻译（LLM）**: `IdiomTranslatorRouter` 接受翻译上下文，生成目标语言代码（`rewrite.generated-code`）；翻译失败时进入 RETRY。
4. **L1–L2 确定性验证 + 自动修复**: `TypeCheckerRouter` 执行 `tsc --strict`（HARD，0 错误）；失败则进 `AgentFixerRouter`（LLM 修复）；通过后 `StyleCheckerRouter` 执行 biome lint（HARD）；失败则进 `StyleFixerRouter`。
5. **L3 接口对比 + L4 语义裁判**: `InterfaceExtractorRouter` 用 AST 提取双语接口规格；`SignatureComparatorRouter` 做接口名比对（HARD）；`BehavioralTesterRouter` 执行 import 验证脚本（HARD）；两路 fan-in 后 `EquivalenceJudgeRouter` 做 LLM 语义裁判（SOFT），失败则 `FeedbackDemoteRouter` 将反馈送回 `idiom_translator`。

Rust 构建路径通过 `_rust_env()` 函数注入 `~/.cargo/bin` 和 `~/mingw64/mingw64/bin` 到 `PATH`，保证 `cargo check` 可执行。

基于当前代码片段只能看到 `SourceAnalyzerRouter` 的完整实现；其余 Router（`DependencyMapperRouter`、`IdiomTranslatorRouter`、`TypeCheckerRouter` 等）的内部逻辑只看到接口定义，完整机制需读 `routers.py` 的其余部分。

> _来源: src/omnicompany/packages/domains/software_engineering/lang_rewrite/pipeline.py, src/omnicompany/packages/domains/software_engineering/lang_rewrite/routers.py, src/omnicompany/packages/domains/software_engineering/lang_rewrite/run.py_

## Public surface

以下接口在 `__init__.py` 的 `__all__` 中声明，并有代码实现支撑：

| 名称 | 类型 | 说明 |
|---|---|---|
| `build_pipeline` | 函数 | 返回 `PipelineSpec`（14 节点 DAG），在 `pipeline.py` 中定义 |
| `build_bindings` | 函数 | 返回 `dict[str, Router]`，在 `__init__.py` 中以延迟 import 封装，实际委托 `run.build_bindings` |
| `DOMAIN` | 常量 | 字符串 `"rewrite"`，在 `formats.py` 和 `pipeline.py` 中均有定义 |
| `FORMATS` | 常量 | 所有 `Format` 定义列表，在 `formats.py` 中定义 |
| `register_formats` | 函数 | 将本域 Format 注册到 `FormatRegistry`，在 `formats.py` 中定义 |

Format id 列表（均以 `rewrite.` 为前缀）：`rewrite.source-module`、`rewrite.dependency-graph`、`rewrite.demand-set`、`rewrite.supply-map`、`rewrite.translation-context`、`rewrite.generated-code`、`rewrite.checked-code`、`rewrite.style-checked`、`rewrite.interface-spec`、`rewrite.signature-compared`、`rewrite.behavioral-tested`、`rewrite.verified-code`。

> _来源: src/omnicompany/packages/domains/software_engineering/lang_rewrite/__init__.py, src/omnicompany/packages/domains/software_engineering/lang_rewrite/formats.py_

## Internal structure

包由四个 Python 文件组成，职责分工清晰：

- **`formats.py`**: 数据类型层。定义 `DOMAIN`、`FORMATS` 列表和 `register_formats`。12 个 `Format` 对象以继承关系（`parent` 字段）组成格式谱系，`required_tags` 字段在运行时用于格式校验。
- **`pipeline.py`**: 拓扑定义层。`build_pipeline()` 函数用 `PipelineNode` / `PipelineEdge` / `AnchorSpec` 等协议类型组装 DAG，并在模块顶层以注释 ASCII 图描述完整拓扑。`ENGINE_TOPO_ORDER` 常量也定义于 `routers.py`（此处重复出现表明两处均有定义）。
- **`routers.py`**: 执行层。每个节点对应一个 `Router` 子类，统一继承 `omnicompany.runtime.routing.router.Router`，`run(input_data: dict) -> Verdict` 为统一执行接口。`_PYTHON_TO_TS` / `_PYTHON_TO_RUST` 和 `DEP_MAPS` 字典提供外部依赖映射。`_rust_env()` 工具函数负责构建 Rust 工具链环境。
- **`run.py`**: 绑定构建层。`build_bindings()` 函数将 node id 字符串映射到 Router 实例，使用延迟 import 避免在 CLI 启动时拉入重依赖（如 `anthropic`）。`model`、`work_dir`、`ts_dir`、`rs_dir` 四个参数通过 `input_dict` 向各 Router 传递配置。
- **`__init__.py`**: 公开 API 层。统一 re-export，`build_bindings` 以薄封装委托 `run.py`。

> _来源: src/omnicompany/packages/domains/software_engineering/lang_rewrite/__init__.py, src/omnicompany/packages/domains/software_engineering/lang_rewrite/pipeline.py, src/omnicompany/packages/domains/software_engineering/lang_rewrite/routers.py, src/omnicompany/packages/domains/software_engineering/lang_rewrite/run.py, src/omnicompany/packages/domains/software_engineering/lang_rewrite/formats.py_

## Files

- `src/omnicompany/packages/domains/software_engineering/lang_rewrite/__init__.py` — 包入口，声明 `__all__`，re-export `DOMAIN`、`FORMATS`、`register_formats`、`build_pipeline`，并以延迟 import 封装 `build_bindings`。
- `src/omnicompany/packages/domains/software_engineering/lang_rewrite/pipeline.py` — DAG 拓扑定义，`build_pipeline()` 返回完整 `PipelineSpec`，包含 14 个节点的分层验证架构。
- `src/omnicompany/packages/domains/software_engineering/lang_rewrite/routers.py` — 所有 Router 实现，包含 `SourceAnalyzerRouter` 等 14 个节点对应类，以及 `_PYTHON_TO_TS`、`_PYTHON_TO_RUST`、`DEP_MAPS`、`ENGINE_TOPO_ORDER`、`_rust_env()` 等辅助定义。
- `src/omnicompany/packages/domains/software_engineering/lang_rewrite/run.py` — `build_bindings()` 函数，将 node id 映射到 Router 实例，并处理 `model`、`work_dir`、`ts_dir`、`rs_dir` 参数传递。
- `src/omnicompany/packages/domains/software_engineering/lang_rewrite/formats.py` — 12 个 `Format` 定义 + `register_formats()` 函数，构成本域完整数据类型谱系。

> _来源: src/omnicompany/packages/domains/software_engineering/lang_rewrite/__init__.py, src/omnicompany/packages/domains/software_engineering/lang_rewrite/pipeline.py, src/omnicompany/packages/domains/software_engineering/lang_rewrite/routers.py, src/omnicompany/packages/domains/software_engineering/lang_rewrite/run.py, src/omnicompany/packages/domains/software_engineering/lang_rewrite/formats.py_

## Related

与本包最相关的 KB 已有条目：

- `kb.arch.package.domains_software_engineering_equiv_test` — 跨语言语义等价性测试管线，与 `lang_rewrite` 的 L3b `BehavioralTesterRouter` 和 L4 `EquivalenceJudgeRouter` 功能高度相关，可能存在共享或继承关系。
- `kb.arch.package.domains_software_engineering_debugger` — 通用假设驱动调试工作流，与 `lang_rewrite` 中 `AgentFixerRouter`（L1 失败修复）和 `StyleFixerRouter`（L2 失败修复）的修复回路在概念上接近。
- `kb.arch.package.domains_software_engineering_generated` — 同属 `software_engineering` domain 下的 auto-generated 管线包，结构上并列。
- `kb.arch.package.services_pipeline_ci` — 管线质量 CI 扫描器，可能对 `lang_rewrite` 产出代码进行质量扫描。

> _来源: kb_context_

## Known limitations

从代码中可以观察到以下明确的局限或未完成区域：

- **Rust 路径硬编码**: `_rust_env()` 中 `cargo` 路径硬编码为 `~/.cargo/bin`，`mingw64` 路径硬编码为 `~/mingw64/mingw64/bin`，仅适用于特定 Windows + MinGW 环境，在 Linux/macOS CI 环境中可能失效。
- **Router 实现可见度不足**: `routers.py` 代码片段在 `SourceAnalyzerRouter.run()` 提取公开接口的循环处截断，`DependencyMapperRouter`、`IdiomTranslatorRouter`、`TypeCheckerRouter` 等 13 个 Router 的内部实现均未在当前可见片段中出现，无法确认其完成状态。
- **`ts_dir` / `rs_dir` 为可选参数且无默认值**: `build_bindings()` 中 `ts_dir` 和 `rs_dir` 仅在 `input_dict` 提供时才有值，传入 `SupplyScannerRouter` 和 `BehavioralTesterRouter`；若调用方未提供，这两个 Router 的行为依赖其内部对 `None` 的处理，当前片段中未见处理逻辑。
- **`pipeline.py` 中 `PipelineEdge` 未在代码片段中出现**: `build_pipeline()` 的完整 DAG 边定义（fan-out / fan-in / feedback 回路）在截断处尚未出现，只能从 ASCII 注释图推断拓扑，无法从代码层面验证边是否完整定义。

> _来源: src/omnicompany/packages/domains/software_engineering/lang_rewrite/routers.py, src/omnicompany/packages/domains/software_engineering/lang_rewrite/run.py, src/omnicompany/packages/domains/software_engineering/lang_rewrite/pipeline.py_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 5 code anchors (20654 chars), 0 plan docs (0 chars), 25 kb refs
