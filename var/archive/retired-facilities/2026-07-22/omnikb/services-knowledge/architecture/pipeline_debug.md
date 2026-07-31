# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:11:50Z
---
omnikb_type: karch
id: kb.arch.pipeline.debug
name: 'Pipeline: debug'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.debug
- domain.debug
- architecture
maturity: living
summary: 假设驱动调试工作流 — 通用跨语言 debug 管线
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L453-L478
---

# Pipeline: debug

> **id**: `kb.arch.pipeline.debug` · **type**: karch · **maturity**: living

## Why this exists

debug 管线的存在动机来自其 seed description：「假设驱动调试工作流 — 通用跨语言 debug 管线」。其核心价值在于提供一条领域专属的、可重复执行的调试路径，覆盖编译错误、测试失败等场景，并以「假设驱动」的方式引导 LLM 系统性地推进修复，而非随机尝试。「通用跨语言」意味着该管线不绑定特定语言，目标语言通过参数传入（默认值为 `typescript`）。

该管线与 `kb.arch.pipeline.voxelcraft.engineering` 所描述的工程管线中的 debug loop 存在关联——后者在 GDD → code → compile 阶段会触发调试循环，而 debug 管线则是该循环的独立可调用入口，可在任意上下文中单独启动。

当前可见材料中无 plan 文档，设计意图仅能从 seed description 和注册代码中读取，更深层的「假设驱动」具体实现逻辑需阅读 `omnicompany/packages/domains/software_engineering/debugger/pipeline.py`。

> _来源: seed_description, src/omnicompany/core/pipelines.py:L453-L478_

## How it works

从 `src/omnicompany/core/pipelines.py` 的 L453–L469 可以看到，debug 管线通过 `register(PipelineEntry(...))` 完成注册，`PipelineEntry` 的字段如下：

- `name="debug"`：管线唯一标识符
- `domain="debug"`：所属域
- `build_pipeline`：通过 `_lazy("omnicompany.packages.domains.software_engineering.debugger.pipeline", "build_pipeline")` 懒加载，指向 `debugger/pipeline.py` 中的 `build_pipeline` 函数
- `build_bindings`：通过 `_lazy_fn("omnicompany.packages.domains.software_engineering.debugger.run", "build_bindings")` 懒加载，指向 `debugger/run.py` 中的 `build_bindings` 函数
- `default_db_dir="data/debug"`：默认数据库存储目录
- `default_max_steps=50`：默认最大执行步数
- `cli_args`：四个 CLI 参数（详见 Public surface 段）

注册块被包裹在 `try/except` 中，加载失败时会以 `logger.debug("skip debug: %s", e)` 静默跳过，不中断其他管线的注册流程。

基于当前代码片段只能看到管线的注册元数据，完整的管线执行机制（步骤序列、假设生成逻辑、错误分析流程）需读 `omnicompany/packages/domains/software_engineering/debugger/pipeline.py` 和 `omnicompany/packages/domains/software_engineering/debugger/run.py`。

> _来源: src/omnicompany/core/pipelines.py:L453-L478_

## Public surface

当前代码片段中可见的对外接口如下：

**管线名（Pipeline name）**
- `"debug"` — 通过 CLI 或 API 调用时使用的管线标识符

**CLI 参数（`CliArg`）**
- `error_output`（必填）：编译/测试错误输出，作为调试的原始输入
- `language`（可选，默认 `"typescript"`）：目标语言
- `compile_command`（可选）：编译/测试命令
- `work_dir`（可选）：工作目录

**懒加载入口函数（通过 `_lazy` / `_lazy_fn` 指向）**
- `build_pipeline`（位于 `omnicompany.packages.domains.software_engineering.debugger.pipeline`）
- `build_bindings`（位于 `omnicompany.packages.domains.software_engineering.debugger.run`）

上述两个函数名来自 `_lazy`/`_lazy_fn` 的字符串参数，只看到接口声明未看到实现，具体签名需读对应模块文件。

> _来源: src/omnicompany/core/pipelines.py:L453-L478_

## Internal structure

从注册代码可推断 debug 管线的内部模块分布在以下路径下：

- `omnicompany/packages/domains/software_engineering/debugger/pipeline.py`：包含 `build_pipeline` 函数，负责构建管线步骤序列
- `omnicompany/packages/domains/software_engineering/debugger/run.py`：包含 `build_bindings` 函数，负责绑定运行时上下文或工具

两者均通过 `_lazy` / `_lazy_fn` 延迟导入，说明 debugger 包是可选依赖，缺失时管线注册会被静默跳过（见 `logger.debug("skip debug: %s", e)`）。

当前 file_list 中未提供 debugger 子包的完整文件清单，内部是否还有其他子模块（如 hypothesis 生成器、错误解析器等）无法从现有材料确认，需直接列举 `omnicompany/packages/domains/software_engineering/debugger/` 目录。

> _来源: src/omnicompany/core/pipelines.py:L453-L478_

## Files

当前 code_anchors 中仅提供了以下一处锚点：

- `src/omnicompany/core/pipelines.py`（L453–L478）：核心管线注册文件，包含 debug 管线的 `PipelineEntry` 注册块，定义管线名、域、CLI 参数、默认配置以及懒加载的 `build_pipeline` 和 `build_bindings` 入口。

以下文件在代码中被引用但不在 code_anchors 列表内，无法直接描述其内容：
- `omnicompany/packages/domains/software_engineering/debugger/pipeline.py`
- `omnicompany/packages/domains/software_engineering/debugger/run.py`

> _来源: src/omnicompany/core/pipelines.py:L453-L478_

## Related

与 debug 管线直接或间接相关的已有 KB 条目：

- `kb.arch.pipeline.voxelcraft.engineering`：voxelcraft 工程管线，其 GDD → code → compile → **debug loop** 阶段与本管线形成上下游关系，debug 管线可作为该 loop 的独立调用入口
- `kb.arch.pipeline.sw_tdd`：TDD 执行管线，包含写测试 + 跑测试 + 写实现 + **修复回路**，与 debug 管线在错误修复场景上存在功能重叠或协作关系
- `kb.arch.pipeline.sw_implement`：独立实施管线，实施过程中可能触发 debug 管线处理编译错误
- `kb.arch.pipeline.lang_rewrite`：跨语言改写管线，改写过程涉及多语言编译验证，与 debug 管线的 `language` 参数场景一致
- `kb.arch.pipeline.equiv_test`：跨语言语义等价性测试管线，测试失败时可能需要 debug 管线介入

> _来源: kb_context_

## Known limitations

从当前可见代码片段中可以观察到以下局限和未明确区域：

1. **可选依赖静默跳过**：注册块被 `try/except` 包裹，debugger 包缺失时以 `logger.debug("skip debug: %s", e)` 静默跳过。这意味着在未安装完整依赖的环境中，debug 管线不可用，且不会有任何用户可见的警告。

2. **`compile_command` 和 `work_dir` 均为可选**：代码中这两个 `CliArg` 无 `required=True`，但实际调试场景通常需要指定编译命令和工作目录，缺省时的行为（fallback 逻辑）无法从当前片段确认。

3. **「假设驱动」机制不可见**：seed description 标榜「假设驱动调试工作流」，但 `build_pipeline` 的实现位于 `debugger/pipeline.py`，当前片段中不可见，无法确认假设生成、验证、迭代的具体机制是否已实现。

4. **`default_max_steps=50` 的合理性**：步数上限为固定值 50，对于复杂 bug 是否足够无法从现有材料判断，代码中也未见相关 TODO/FIXME。

> _来源: src/omnicompany/core/pipelines.py:L453-L478, seed_description_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 1 code anchors (1251 chars), 0 plan docs (0 chars), 25 kb refs
