# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:12:33Z
---
omnikb_type: karch
id: kb.arch.pipeline.equiv_test
name: 'Pipeline: equiv-test'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.equiv-test
- domain.equiv
- architecture
maturity: living
summary: '[EXPERIMENTAL] 跨语言语义等价性测试管线 — Golden File 模式验证 Python↔TS 行为一致性'
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L430-L455
---

# Pipeline: equiv-test

> **id**: `kb.arch.pipeline.equiv_test` · **type**: karch · **maturity**: living

## Why this exists

该管线标注为 `[EXPERIMENTAL]`，其存在目的直接来自注册时的 `description` 字段：以 Golden File 模式验证 Python 与 TypeScript 两种语言实现之间的语义等价性。这与 Omnicompany 中跨语言改写工作（见 `kb.arch.pipeline.lang_rewrite`）形成配套关系——改写完成后需要有机制验证 Python 原始行为与 TS 翻译结果在语义上保持一致，equiv-test 管线承担这一验证职责。当前可见材料中没有 plan 文档对此做进一步说明，设计动机仅能从 seed description 和注册代码推断到此层次。

> _来源: seed_description, src/omnicompany/core/pipelines.py:L430-L455_

## How it works

从注册代码来看，管线通过 `PipelineEntry` 注册，核心构建逻辑由两个懒加载函数提供：

- `build_pipeline`：由 `_lazy("omnicompany.packages.domains.software_engineering.equiv_test.pipeline", "build_pipeline")` 延迟加载，指向 `equiv_test.pipeline` 模块中的 `build_pipeline` 函数。
- `build_bindings`：由 `_lazy_fn("omnicompany.packages.domains.software_engineering.equiv_test.run", "build_bindings")` 延迟加载，指向 `equiv_test.run` 模块中的 `build_bindings` 函数。

注册时指定 `default_max_steps=20`，`default_db_dir="data/equiv"`，表明管线运行步数上限为 20，中间数据存储在 `data/equiv` 目录下。注册被包裹在 `try/except` 中，失败时仅以 `logger.debug` 记录并跳过，说明该管线为可选组件，不影响主流程启动。

基于当前代码片段只能看到注册层的入口声明，完整的步骤图、Agent 调用链、Golden File 比对逻辑需读 `omnicompany/packages/domains/software_engineering/equiv_test/pipeline.py` 和 `omnicompany/packages/domains/software_engineering/equiv_test/run.py` 文件。

> _来源: src/omnicompany/core/pipelines.py:L430-L455_

## Public surface

基于注册代码，该管线对外暴露的接口如下：

- **管线名**: `"equiv-test"`（通过 `PipelineEntry(name="equiv-test", ...)` 注册）
- **域**: `domain="equiv"`
- **CLI 参数**（`CliArg` 列表）:
  - `py_path`：Python 源文件路径，必填
  - `ts_path`：TypeScript 翻译文件路径，必填
  - `module_name`：模块名，可选，默认为空字符串
  - `ts_dir`：TS 工作目录，可选，默认 `"data/rewrite/ts_phase1"`
- **构建入口函数**（懒加载）:
  - `build_pipeline`（位于 `omnicompany.packages.domains.software_engineering.equiv_test.pipeline`）
  - `build_bindings`（位于 `omnicompany.packages.domains.software_engineering.equiv_test.run`）

当前代码片段中未见其他 public 类或 Router 声明，完整接口需读对应的 `pipeline.py` 和 `run.py`。

> _来源: src/omnicompany/core/pipelines.py:L430-L455_

## Internal structure

当前可见材料（code_snippets）只能看到注册层代码，从模块路径可以推断内部至少存在以下两个子模块：

- `omnicompany.packages.domains.software_engineering.equiv_test.pipeline`：负责构建管线步骤图（`build_pipeline`）
- `omnicompany.packages.domains.software_engineering.equiv_test.run`：负责构建 bindings（`build_bindings`）

此外，`domain="equiv"` 与 `default_db_dir="data/equiv"` 暗示存在独立的数据存储路径，但该路径下的具体文件结构在当前可见材料中无从确认。完整的内部模块划分（如 Golden File 读写模块、比对逻辑模块）需读 `file_list` 和上述两个源文件。

> _来源: src/omnicompany/core/pipelines.py:L430-L455_

## Files

当前 `code_anchors` 中仅提供了以下一个文件路径：

- `src/omnicompany/core/pipelines.py`（L430–L455）：管线注册中心，包含 `equiv-test` 管线的 `PipelineEntry` 注册声明，定义 CLI 参数、懒加载构建函数和默认配置。

注册代码引用的以下路径在当前 `code_anchors` 中未出现，但依据代码推断应当存在：
- `omnicompany/packages/domains/software_engineering/equiv_test/pipeline.py`
- `omnicompany/packages/domains/software_engineering/equiv_test/run.py`

> _来源: src/omnicompany/core/pipelines.py:L430-L455_

## Related

以下 KB 条目与 equiv-test 管线存在关联：

- `kb.arch.pipeline.lang_rewrite`：跨语言改写管线，是 equiv-test 的上游；改写产物（Python→TS）是 equiv-test 的验证对象，`ts_dir` 默认值 `"data/rewrite/ts_phase1"` 直接指向改写产物目录。
- `kb.arch.pipeline.debug`：通用调试管线，在 `pipelines.py` 中与 equiv-test 在相邻位置注册，可作为 equiv-test 失败后的后续处理管线。
- `kb.arch.pipeline.selftest`：Omnicompany e2e 功能自测管线，验证管线注册等基础功能，与 equiv-test 的注册机制相关。
- `kb.arch.pipeline.sw_tdd`：TDD 执行管线，在软件工程域中与 equiv-test 处于同一 domain 层级，可能有工作流上的衔接。

> _来源: kb_context_

## Known limitations

从当前可见材料可以确认以下局限：

- **实验性标注**：管线 `description` 字段明确标注 `[EXPERIMENTAL]`，表明该管线尚未成熟，不应作为稳定接口使用。
- **注册容错设计**：注册代码被包裹在 `try/except` 块中，异常时仅 `logger.debug("skip equiv-test: %s", e)` 后静默跳过，说明该管线在某些环境下可能加载失败且不会报错，存在可观测性盲点。
- **代码片段覆盖不足**：当前可见代码仅为注册层，`build_pipeline` 和 `build_bindings` 的实际内容、Golden File 比对的具体逻辑、步骤图结构均不可见，无法评估其实现完整性。
- **`module_name` 默认为空字符串**：CLI 参数 `module_name` 默认值为 `""`，空字符串作为模块名是否为有效输入，以及管线如何处理该边界情况，当前代码片段中无法确认。

代码片段中未见 TODO/FIXME/XXX 注释，更深入的局限分析需读 `equiv_test/pipeline.py` 和 `equiv_test/run.py`。

> _来源: src/omnicompany/core/pipelines.py:L430-L455, seed_description_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 1 code anchors (1326 chars), 0 plan docs (0 chars), 25 kb refs
