# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:27:38Z
---
omnikb_type: karch
id: kb.arch.package.domains_software_engineering_equiv_test
name: 'Package: packages/domains/software_engineering/equiv_test'
tags:
- topic.package
- layer.domains
- domain.equiv_test
- architecture
maturity: living
summary: equiv_test — 跨语言语义等价性测试管线 [EXPERIMENTAL]
scope: omnicompany
code_anchors:
- src/omnicompany/packages/domains/software_engineering/equiv_test/__init__.py
- src/omnicompany/packages/domains/software_engineering/equiv_test/pipeline.py
- src/omnicompany/packages/domains/software_engineering/equiv_test/routers.py
- src/omnicompany/packages/domains/software_engineering/equiv_test/run.py
- src/omnicompany/packages/domains/software_engineering/equiv_test/formats.py
---

# Package: packages/domains/software_engineering/equiv_test

> **id**: `kb.arch.package.domains_software_engineering_equiv_test` · **type**: karch · **maturity**: living

## Why this exists

`equiv_test` 管线的存在目的，在 `__init__.py` 的文档字符串中有明确说明：它是一套跨语言语义等价性测试管线，采用 Golden File 模式——先以 Python 实际执行录制输出，再用 TypeScript（或 Rust）跑一遍，最后逐 key 比对两侧结果。文档字符串还特别指出，这比 `lang_rewrite` L4 阶段（LLM 裁判）更为严格，但代价是被测模块必须能独立执行。

该模块当前标注为 `[EXPERIMENTAL]`，状态说明为"设计有效，但未接入 lang_rewrite 主流程"。这意味着它作为一个独立实验性管线存在，可通过以下两种方式手动触发：
- `omni run equiv-test --py-path <file> --ts-path <file>`
- `python scripts/run_equiv_test.py <py_path> <ts_path>`

从现有材料来看，该包的直接动机是弥补 LLM 裁判方式在等价性验证上的主观性不足——用确定性执行比对替代语义判断。没有 plan 文档提供额外背景，所有动机信息均来自代码内注释。

> _来源: `src/omnicompany/packages/domains/software_engineering/equiv_test/__init__.py`_

## How it works

`pipeline.py` 中的 `build_pipeline()` 函数构建了一条包含六个有序节点的管线，注释标注的完整流程为：

```
test_designer → golden_recorder → baseline_check → ts_test_gen → ts_executor → comparator
                                                                                      │
                                                              PASS(全匹配) → EMIT
                                                              有不匹配    ↓
                                                                 failure_analyzer (LLM)
                                                                        │
                                                                   PASS → EMIT(带诊断)
```

各节点类型和成熟度如下：

| 节点 id | 类型 | 成熟度 | 性质 |
|---|---|---|---|
| `test_designer` | `NodeKind.ANCHOR` | `GROWING` | LLM |
| `golden_recorder` | `NodeKind.ANCHOR` | `GROWING` | LLM + 实际运行 |
| `baseline_check` | `NodeKind.TRANSFORMER` | `CRYSTALLIZED` | 确定性规则 |
| `ts_test_gen` | `NodeKind.ANCHOR` | `GROWING` | LLM |
| `ts_executor` | `NodeKind.ANCHOR` | `GROWING` | 确定性（代码片段截断） |

`routers.py` 中实现了各节点的执行逻辑。辅助函数 `_run_python()` 通过 `subprocess.run` 以 30 秒超时调用 `python -c <code>`，将 stdout 解析为 JSON；`_run_typescript()` 则将 LLM 生成的代码写入 `_equiv_test.ts` 临时文件，通过 `npx tsx _equiv_test.ts` 执行，执行后删除该文件。`_extract_json()` 和 `_extract_code()` 负责从 LLM 返回文本中提取结构化内容。

LLM 客户端通过 `_make_llm_client()` 惰性实例化，使用 `omnicompany.runtime.llm.llm.LLMClient`，`role` 固定为 `"runtime_main"`，`max_tokens` 为 `16384`。

基于当前代码片段，`ts_executor` 节点之后的 `comparator` 和 `failure_analyzer` 节点在 `pipeline.py` 的代码片段中被截断，完整节点定义需读 `pipeline.py` 完整文件。`routers.py` 中 `TestDesignerRouter.run()` 方法的具体 prompt 构建逻辑也被截断，需读 `routers.py` 完整文件。

> _来源: `src/omnicompany/packages/domains/software_engineering/equiv_test/pipeline.py`, `src/omnicompany/packages/domains/software_engineering/equiv_test/routers.py`_

## Public surface

**Router 类**（来自 `routers.py`，通过 `run.py` 的 `build_bindings()` 确认存在）：

- `TestDesignerRouter` — 格式 `equiv.test-spec` → `equiv.test-spec`，LLM 节点
- `GoldenRecorderRouter` — 格式 `equiv.test-spec` → `equiv.test-suite`，LLM + 运行节点
- `BaselineCheckRouter` — 确定性验证节点，无 `model` 参数
- `TSTestGeneratorRouter` — 格式 `equiv.test-suite` → `equiv.test-suite`，LLM 节点
- `TSExecutorRouter` — 接受 `ts_dir` 参数，确定性执行节点
- `ResultComparatorRouter` — 确定性比对节点，无额外参数
- `FailureAnalyzerRouter` — LLM 节点，接受 `model` 参数

**Pipeline 构建函数**（来自 `pipeline.py`）：
- `build_pipeline() -> PipelineSpec`

**Bindings 构建函数**（来自 `run.py`）：
- `build_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]`

**Format id**（来自 `formats.py`）：
- `equiv.test-spec`
- `equiv.test-suite`
- `equiv.execution-result`
- `equiv.comparison-report`
- `equiv.diagnosed-report`

**格式注册函数**（来自 `formats.py`）：
- `register_formats(registry: FormatRegistry) -> None`

> _来源: `src/omnicompany/packages/domains/software_engineering/equiv_test/routers.py`, `src/omnicompany/packages/domains/software_engineering/equiv_test/run.py`, `src/omnicompany/packages/domains/software_engineering/equiv_test/pipeline.py`, `src/omnicompany/packages/domains/software_engineering/equiv_test/formats.py`_

## Internal structure

该包由四个主要模块构成，职责清晰分离：

- **`formats.py`**：定义数据格式类型体系，声明五级 `Format` 对象并提供注册入口，不依赖其他包内模块。
- **`pipeline.py`**：使用 `omnicompany.protocol.pipeline` 和 `omnicompany.protocol.anchor` 中的协议类型构建管线拓扑，声明节点顺序、格式契约和路由规则，不包含执行逻辑。
- **`routers.py`**：实现各节点的实际执行逻辑（`Router` 子类），包含 LLM 调用、子进程执行、JSON 解析等所有 I/O 操作。惰性导入 `omnicompany.runtime.llm.llm.LLMClient`。
- **`run.py`**：作为装配层，将 `routers.py` 中的 Router 类实例化并以字典形式返回，是运行时将 pipeline 拓扑与实现绑定的接口。

`pipeline.py` 描述"什么"（拓扑结构），`routers.py` 描述"如何"（执行细节），`run.py` 负责将两者对接——这与 Omnicompany 其他 domain package 的分层模式一致。`__init__.py` 仅包含文档字符串，不导出任何符号。

> _来源: `src/omnicompany/packages/domains/software_engineering/equiv_test/pipeline.py`, `src/omnicompany/packages/domains/software_engineering/equiv_test/routers.py`, `src/omnicompany/packages/domains/software_engineering/equiv_test/run.py`, `src/omnicompany/packages/domains/software_engineering/equiv_test/formats.py`_

## Files

- `src/omnicompany/packages/domains/software_engineering/equiv_test/__init__.py` — 包入口，仅含文档字符串，描述模块用途、Golden File 模式原理、实验状态和手动触发命令，不导出任何符号。
- `src/omnicompany/packages/domains/software_engineering/equiv_test/pipeline.py` — 使用协议层类型（`PipelineSpec`、`PipelineNode`、`AnchorSpec`、`TransformerSpec` 等）声明管线拓扑，通过 `build_pipeline()` 返回完整 `PipelineSpec`。
- `src/omnicompany/packages/domains/software_engineering/equiv_test/routers.py` — 实现全部七个 Router 子类（`TestDesignerRouter`、`GoldenRecorderRouter`、`BaselineCheckRouter`、`TSTestGeneratorRouter`、`TSExecutorRouter`、`ResultComparatorRouter`、`FailureAnalyzerRouter`），包含 Python 和 TypeScript 子进程执行逻辑。
- `src/omnicompany/packages/domains/software_engineering/equiv_test/run.py` — 装配层，`build_bindings()` 函数实例化所有 Router 并以节点 id 为键返回字典，供运行时绑定 pipeline 节点。
- `src/omnicompany/packages/domains/software_engineering/equiv_test/formats.py` — 定义 `equiv` 域的五个 `Format` 对象（`equiv.test-spec` 至 `equiv.diagnosed-report`），并提供 `register_formats()` 注册入口。

> _来源: `src/omnicompany/packages/domains/software_engineering/equiv_test/__init__.py`, `src/omnicompany/packages/domains/software_engineering/equiv_test/pipeline.py`, `src/omnicompany/packages/domains/software_engineering/equiv_test/routers.py`, `src/omnicompany/packages/domains/software_engineering/equiv_test/run.py`, `src/omnicompany/packages/domains/software_engineering/equiv_test/formats.py`_

## Related

与本包最直接相关的已有 KB 条目：

- `kb.arch.package.domains_software_engineering_lang_rewrite` — `__init__.py` 明确提到 equiv_test 比 `lang_rewrite` L4（LLM 裁判）更严格，且当前未接入 lang_rewrite 主流程，两者存在设计上的关联。
- `kb.arch.package.domains_software_engineering_debugger` — 同属 `software_engineering` 域，是同级 domain package，共享协议层基础设施。
- `kb.arch.package.domains_software_engineering_generated` — 同属 `software_engineering` 域的同级 package。
- `kb.arch.package.services_pipeline_ci` — `pipeline_ci` 负责管线质量 CI 扫描，与实验性管线的验证机制存在潜在交叉。

> _来源: kb_context 列表, `src/omnicompany/packages/domains/software_engineering/equiv_test/__init__.py`_

## Known limitations

根据代码中可见的明确信息：

1. **未接入主流程**：`__init__.py` 明确标注"状态：Experimental — 设计有效，但未接入 lang_rewrite 主流程"，只能手动触发。

2. **`[EXPERIMENTAL]` 标签**：`__init__.py` 和 `pipeline.py` 均在模块名后附带 `[EXPERIMENTAL]`，多个节点的 `maturity` 为 `NodeMaturity.GROWING`（仅 `baseline_check` 为 `CRYSTALLIZED`）。

3. **子进程执行限制**：`_run_python()` 和 `_run_typescript()` 均有硬编码 30 秒超时上限，超时直接返回错误字典，无重试机制。

4. **TypeScript 执行依赖 `npx tsx`**：`_run_typescript()` 通过 `shell=True` 执行 `npx tsx _equiv_test.ts`，依赖宿主环境已安装 Node.js 和 tsx，无环境检测。

5. **临时文件写入**：`_run_typescript()` 将测试代码直接写入 `_equiv_test.ts`，代码注释标注了一个待办项："follow-up: refactor to guarded_write"（审计标记 `OMNI-013`）。

6. **代码片段截断**：`pipeline.py` 中 `ts_executor` 节点定义以及之后的 `result_comparator`、`failure_analyzer` 节点在可见片段中被截断，`routers.py` 中 `TestDesignerRouter.run()` 的 prompt 构建逻辑同样被截断，完整实现细节无法从当前片段确认。

> _来源: `src/omnicompany/packages/domains/software_engineering/equiv_test/__init__.py`, `src/omnicompany/packages/domains/software_engineering/equiv_test/pipeline.py`, `src/omnicompany/packages/domains/software_engineering/equiv_test/routers.py`_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 5 code anchors (15811 chars), 0 plan docs (0 chars), 25 kb refs
