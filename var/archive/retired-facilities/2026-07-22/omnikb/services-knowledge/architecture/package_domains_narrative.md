# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:25:20Z
---
omnikb_type: karch
id: kb.arch.package.domains_narrative
name: 'Package: packages/domains/narrative'
tags:
- topic.package
- layer.domains
- domain.narrative
- architecture
maturity: living
summary: omnicompany.packages.domains.narrative — Narrative Creation Engine domain.
scope: omnicompany
code_anchors:
- src/omnicompany/packages/domains/narrative/__init__.py
- src/omnicompany/packages/domains/narrative/pipeline.py
- src/omnicompany/packages/domains/narrative/run.py
- src/omnicompany/packages/domains/narrative/formats.py
---

# Package: packages/domains/narrative

> **id**: `kb.arch.package.domains_narrative` · **type**: karch · **maturity**: living

## Why this exists

narrative 域是 Omnicompany 中专门用于"创作引擎"实验的隔离域。根据 `__init__.py` 的模块文档，其当前阶段的核心任务是验证 **A5 假设**——即"意图驱动闭环自动化"——通过一个名为"Intent Compiler 玩具实验"的最小可执行场景来实施。

该域被明确设计为**物理隔离**结构，与现有的 demogame、local、voxelcraft 等域完全分离，具体隔离手段包括：
- YAML 节点定义放在 `config/domains/narrative/`（独立配置目录）
- Python 实现放在 `src/omnicompany/packages/domains/narrative/`
- source channel 使用专属标识 `private:narrative`

这种隔离设计意味着 narrative 域的实验性代码不会污染任何已稳定的业务域。该域的存在动机可以归纳为：在 Omnicompany 框架内验证"作者自然语言意图 → LLM 编译 → 场景生成 → 达成度反馈"这一闭环假设，是一个处于 `draft` 阶段的探索性实验域。

当前可见材料中无 plan 文档，因此无法引用更详细的产品背景描述或设计决策来源。

> _来源: `src/omnicompany/packages/domains/narrative/__init__.py` (seed_description + 代码注释)_

## How it works

narrative 域的核心是一条三节点线性管线，定义在 `pipeline.py` 的 `build_a5_loop_pipeline()` 函数中，返回一个 `PipelineSpec` 对象（id 为 `narrative.a5_loop`）。管线的数据流如下：

1. **`intent_compiler`** 节点：对应 `TransformerSpec(id="narrative-intent-compile", name="IntentCompiler")`，从格式 `narrative.author_intent` 转换到 `narrative.execution_bias`，方法为 `TransformMethod.LLM`。作用是把作者的自然语言意图编译为可执行的偏置参数集合（抓住戏剧机理而非关键词匹配）。

2. **`dialogue_generator`** 节点：对应 `TransformerSpec(id="narrative-dialogue-generate", name="DialogueGenerator")`，从 `narrative.execution_bias` 转换到 `narrative.generated_scene`，方法为 `TransformMethod.LLM`。根据 scene context 和 execution bias 生成完整 scene（narration_blocks + atmosphere_self_report）。

3. **`goal_achievement_evaluator`** 节点：对应 `TransformerSpec(id="narrative-goal-evaluate", name="GoalAchievementEvaluator")`，从 `narrative.generated_scene` 转换到 `narrative.achievement_report`，方法为 `TransformMethod.LLM`。评估生成场景对原作者意图的达成度，输出维度评分、批评、失败归因、改进建议等。

三个节点均标注 `maturity=NodeMaturity.HYPOTHETICAL`，表明均未实际实现，属于假设性声明。state dict 在节点间累加传递，最终输出包含全部四个字段。

运行时绑定由 `run.py` 中的 `build_a5_loop_bindings()` 函数提供，将节点 ID 映射到 `Router` 实例：`PipelineIntentCompiler`、`PipelineDialogueGenerator`、`PipelineEvaluator`（均来自 `routers/pipeline_wrappers`），并接受 `role` 参数（默认 `"vision_quality"`）。

基于当前代码片段只能看到管线声明层和 binding 构建层，完整的 LLM 调用机制需读 `routers/pipeline_wrappers.py` 及 `omnicompany/runtime/routing/router.py` 文件。

> _来源: `src/omnicompany/packages/domains/narrative/pipeline.py`, `src/omnicompany/packages/domains/narrative/run.py`_

## Public surface

`__init__.py` 的 `__all__` 明确声明了对外暴露的符号，分三类：

**Format 常量（来自 `formats.py`）：**
- `NARRATIVE_AUTHOR_INTENT` — Format id `narrative.author_intent`
- `NARRATIVE_EXECUTION_BIAS` — Format id `narrative.execution_bias`
- `NARRATIVE_SCENE_CONTEXT` — Format id `narrative.scene_context`
- `NARRATIVE_GENERATED_SCENE` — Format id `narrative.generated_scene`
- `NARRATIVE_ACHIEVEMENT_REPORT` — Format id `narrative.achievement_report`
- `ALL_FORMATS` — 所有 Format 的集合对象
- `register_formats` — 注册函数，将上述 Format 注册到 `FormatRegistry`

**管线相关（来自 `pipeline.py`）：**
- `build_a5_loop_pipeline` — 工厂函数，返回 `PipelineSpec`，id 为 `narrative.a5_loop`
- `get_pipeline(name: str) -> PipelineSpec` — 按名称获取管线，未知名称抛出 `KeyError`
- `PIPELINES` — dict，当前只含 `{"narrative.a5_loop": build_a5_loop_pipeline}`

**运行时绑定（来自 `run.py`）：**
- `build_a5_loop_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]` — 构建节点 ID 到 Router 实例的映射

> _来源: `src/omnicompany/packages/domains/narrative/__init__.py`, `src/omnicompany/packages/domains/narrative/pipeline.py`, `src/omnicompany/packages/domains/narrative/run.py`_

## Internal structure

从 `__init__.py` 的 import 结构和文件路径可以看出，narrative 域内部至少分为以下子模块：

- **`formats`**（`formats.py`）：语义类型定义层。定义所有 `Format` 对象及 `register_formats` 函数，使用 `omnicompany.protocol.format.Format` 和 `FormatRegistry`。

- **`pipeline`**（`pipeline.py`）：管线声明层。使用 `omnicompany.protocol.anchor.TransformerSpec`、`TransformMethod` 以及 `omnicompany.protocol.pipeline` 中的 `NodeKind`、`NodeMaturity`、`PipelineEdge`、`PipelineNode`、`PipelineSpec` 来声明 A5 闭环管线的结构。

- **`run`**（`run.py`）：运行时绑定层。使用 `omnicompany.runtime.routing.router.Router` 构建管线节点到 Router 实例的绑定映射。

- **`routers/pipeline_wrappers`**（从 `run.py` 的延迟导入推断）：包含 `PipelineIntentCompiler`、`PipelineDialogueGenerator`、`PipelineEvaluator` 三个 Router 子类。当前代码片段中未直接提供该文件内容，只能看到接口未看到实现。

从 `__init__.py` 注释还可知存在 `config/domains/narrative/` 下的 YAML 节点定义，但该目录内容不在当前代码片段中。

> _来源: `src/omnicompany/packages/domains/narrative/__init__.py`, `src/omnicompany/packages/domains/narrative/pipeline.py`, `src/omnicompany/packages/domains/narrative/run.py`, `src/omnicompany/packages/domains/narrative/formats.py`_

## Files

基于 code_anchors 提供的文件列表（与代码片段一致）：

- **`src/omnicompany/packages/domains/narrative/__init__.py`** — 域入口，声明物理隔离边界，重导出 formats、pipeline、run 三个子模块的公共符号，定义 `__all__`。

- **`src/omnicompany/packages/domains/narrative/pipeline.py`** — A5 闭环管线声明，用 `PipelineSpec`/`PipelineNode`/`PipelineEdge` 将三个 LLM Transformer 节点串成线性管线，并提供 `PIPELINES` 注册表和 `get_pipeline` 查询函数。

- **`src/omnicompany/packages/domains/narrative/run.py`** — 运行时绑定构建，`build_a5_loop_bindings()` 函数将节点 ID 映射到具体 `Router` 实例，支持通过 `role` 参数指定执行角色。

- **`src/omnicompany/packages/domains/narrative/formats.py`** — 语义类型定义，声明 A5 闭环所需的五个 `Format` 对象（`NARRATIVE_AUTHOR_INTENT`、`NARRATIVE_EXECUTION_BIAS`、`NARRATIVE_SCENE_CONTEXT`、`NARRATIVE_GENERATED_SCENE`、`NARRATIVE_ACHIEVEMENT_REPORT`）及其 JSON Schema。

> _来源: code_anchors 对应的四个代码片段文件_

## Related

从 KB 已有条目中，与 narrative 域最直接相关的条目为其他 domain 包条目，可作为结构类比参考：

- `kb.arch.package.domains_voxelcraft` — 同为 `packages/domains/` 下的业务域，可对比域隔离模式。
- `kb.arch.package.domains_demogame_produce` — 同层域，配表生产管线，展示了类似的 domain package 组织方式。
- `kb.arch.package.domains_software_engineering_equiv_test` — 同为实验性管线域（标注 EXPERIMENTAL），结构上与 narrative 的 `HYPOTHETICAL` 节点状态类似。

管线结构参考：
- `kb.arch.pipeline.voxelcraft.design` — 同样是 LLM 驱动的创意管线（vision → GDD → balance → review），与 narrative A5 闭环的意图→生成→评估结构有概念对应。

当前 KB 中无专门描述 `Protocol.Format`、`PipelineSpec`、`Router` 等底层机制的条目，这些是理解 narrative 域完整工作方式所需的关联知识，可能需要补写对应 kb 条目。

> _来源: kb_context 已有条目列表_

## Known limitations

从代码可见以下明确局限：

1. **所有管线节点均为 HYPOTHETICAL 状态**：`pipeline.py` 中三个 `PipelineNode` 均设置 `maturity=NodeMaturity.HYPOTHETICAL`，表明 `IntentCompiler`、`DialogueGenerator`、`GoalAchievementEvaluator` 的实际 LLM 调用逻辑尚未实现。

2. **formats.py 自述为"玩具实验"阶段**：`formats.py` 文档字符串明确写道"当前只定义 Intent Compiler 玩具实验所需的两个 Format"（实际上代码中已定义五个，但文档注释未更新），并列举了后续阶段会增加的 `Beat / Scene / CharacterSheet / FactLedger` 等 Format，当前均未实现。

3. **routers/pipeline_wrappers 内容不可见**：`run.py` 对 `PipelineIntentCompiler`、`PipelineDialogueGenerator`、`PipelineEvaluator` 使用延迟导入（函数内 import），当前代码片段中该文件未提供，无法确认这三个 Router 子类是否已有实质性实现。

4. **`NARRATIVE_SCENE_CONTEXT` 的 JSON Schema 在代码片段中被截断**（`characters_present` 字段的 items 定义不完整），完整 schema 需读完整文件。

5. **整体 maturity 为 `draft`**，结合 `__init__.py` 中"最小验证"的表述，说明该域处于探索阶段，不应视为稳定接口。

> _来源: `src/omnicompany/packages/domains/narrative/pipeline.py`, `src/omnicompany/packages/domains/narrative/formats.py`, `src/omnicompany/packages/domains/narrative/run.py`, seed_description_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 4 code anchors (13013 chars), 0 plan docs (0 chars), 25 kb refs
