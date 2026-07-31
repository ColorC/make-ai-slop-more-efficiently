# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:22:04Z
---
omnikb_type: karch
id: kb.arch.package.domains_voxelcraft_mechanics_evolver
name: 'Package: packages/domains/voxelcraft/mechanics_evolver'
tags:
- topic.package
- layer.domains
- domain.mechanics_evolver
- architecture
maturity: living
summary: 'Auto-generated pipeline: mechanics-evolver'
scope: omnicompany
code_anchors:
- src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/__init__.py
- src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/pipeline.py
- src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/routers.py
- src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/run.py
- src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/formats.py
---

# Package: packages/domains/voxelcraft/mechanics_evolver

> **id**: `kb.arch.package.domains_voxelcraft_mechanics_evolver` · **type**: karch · **maturity**: living

## Why this exists

`mechanics_evolver` 是 voxelcraft 域中专门负责"战斗机制演化"的自动化管线。其 `__init__.py` 的 header 注释说明它是 `claude-code` 自动生成的 (`origin=claude-code`)，seed description 为 "Auto-generated pipeline: mechanics-evolver"。从管线描述字段 `"Evolve Java combat mechanics"` 可以看出，该包的职责是在战斗测试失败后，自动分析根因、生成 Java 代码补丁，并通过编译验证形成闭环，从而推动战斗机制向更优状态演化。结合 `kb.arch.pipeline.voxelcraft.combat_test` 中描述的 `config → build → server → RCON test → evolve` 流程，`mechanics_evolver` 对应的正是末尾的 `evolve` 阶段——接收战斗测试结果，输出经过编译验证的修复代码。当前可见材料中无独立 plan 文档，设计动机只能从代码内嵌描述和 seed description 推断到此为止。

> _来源: seed_description, `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/__init__.py`, `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/pipeline.py`, kb.arch.pipeline.voxelcraft.combat_test_

## How it works

管线由 `build_pipeline()` 函数构建，返回一个 `PipelineSpec`，id 为 `"mechanics-evolver"`，entry 节点为 `"gap_analyzer"`。管线包含三个 `NodeKind.ANCHOR` 节点，按顺序串联：

- **`gap_analyzer`**（`AnchorSpec` id=`"a_gap"`）：接收格式 `bw.combat_test_result`，输出 `bw.gap_analysis`，使用 `ValidatorKind.SOFT` 验证。通过则路由到 `code_patcher`，失败则 `RouteAction.HALT`。
- **`code_patcher`**（`AnchorSpec` id=`"a_patch"`）：接收 `bw.gap_analysis`，输出 `bw.patched_java`，同样 `SOFT` 验证。通过则路由到 `compile_verify`，失败则 `HALT`。
- **`compile_verify`**（`AnchorSpec` id=`"a_compile"`）：接收并输出 `bw.patched_java`，使用 `ValidatorKind.HARD` 验证（运行 `gradlew build`）。通过则 `RouteAction.EMIT`；失败则 `RouteAction.JUMP` 回 `code_patcher`，携带 feedback `"Compile failed"`，最多重试 3 次（`max_retries=3`）。

`PipelineEdge` 中显式定义了 `compile_verify → code_patcher` 的反馈边（`condition=VerdictKind.FAIL, feedback=True`），形成修复-编译的重试回路。基于当前代码片段只能看到节点/边的声明结构，运行时如何实际执行这些节点需读 `omnicompany.runtime` 相关文件。

> _来源: `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/pipeline.py`, `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/routers.py`_

## Public surface

该模块通过 `run.py` 的 `__all__` 显式导出两个符号：

- **`build_pipeline`**：函数，返回 `PipelineSpec`，构建完整的 `"mechanics-evolver"` 管线定义。
- **`build_bindings`**：函数，签名 `build_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]`，返回节点 id 到 Router 实例的映射，包含 `"gap_analyzer" → GapAnalyzerRouter()`、`"code_patcher" → CodePatcherRouter()`、`"compile_verify" → CompileVerifyRouter()`。

`formats.py` 对外暴露两个 Format 常量及一个注册函数：

- **`FMT_GAP_ANALYSIS`**：`Format(id="bw.gap_analysis", ...)`，parent 为 `"spec"`。
- **`FMT_PATCHED_CODE`**：`Format(id="bw.patched_java", ...)`，parent 为 `"code"`。
- **`register_formats`**：函数，签名 `register_formats(registry: FormatRegistry = None) -> None`，当前实现为空（`pass`），注释说明 format 通过模块级常量注册。

> _来源: `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/run.py`, `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/formats.py`_

## Internal structure

该包由 5 个文件组成，职责分工清晰：

- **`__init__.py`**：包入口，仅含模块 docstring，无实质逻辑。
- **`pipeline.py`**：管线拓扑定义层，通过 `build_pipeline()` 声明节点、边及路由规则，依赖 `omnicompany.protocol.anchor` 和 `omnicompany.protocol.pipeline`。
- **`routers.py`**：Router 实现层，三个 Router 类分别继承自 `omnicompany.runtime.routing.router.Router`，各自实现 `run(self, input_data: Any) -> Verdict` 方法，承载实际处理逻辑（当前为桩实现）。
- **`run.py`**：组装层，将 `pipeline.py` 的拓扑与 `routers.py` 的实现通过 `build_bindings()` 绑定，是对外的统一入口模块。
- **`formats.py`**：Format 注册层，定义本域专用的两个数据格式常量，与 `omnicompany.protocol.format` 集成。

import 关系：`run.py` → `routers.py` + `pipeline.py`；`pipeline.py` → `omnicompany.protocol.*`；`routers.py` → `omnicompany.runtime.routing.router`；`formats.py` → `omnicompany.protocol.format`。

> _来源: `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/run.py`, `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/pipeline.py`, `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/routers.py`, `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/formats.py`, `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/__init__.py`_

## Files

- `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/__init__.py`：包入口文件，含自动生成标记和模块 docstring `"Auto-generated pipeline: mechanics-evolver"`。
- `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/pipeline.py`：定义 `build_pipeline()` 函数，构建包含三节点两反馈边的 `PipelineSpec`，描述 mechanics-evolver 的完整拓扑。
- `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/routers.py`：实现 `GapAnalyzerRouter`、`CodePatcherRouter`、`CompileVerifyRouter` 三个 Router 类，各含 `FORMAT_IN`、`FORMAT_OUT`、`DESCRIPTION` 类属性和 `run()` 方法。
- `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/run.py`：对外组装模块，导出 `build_pipeline` 和 `build_bindings`，后者返回节点 id 到 Router 实例的绑定字典。
- `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/formats.py`：定义 `FMT_GAP_ANALYSIS`（`bw.gap_analysis`）和 `FMT_PATCHED_CODE`（`bw.patched_java`）两个 Format 常量，及空实现的 `register_formats()` 函数。

> _来源: `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/__init__.py`, `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/pipeline.py`, `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/routers.py`, `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/run.py`, `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/formats.py`_

## Related

直接相关条目：

- `kb.arch.package.domains_voxelcraft`：mechanics_evolver 所在的 voxelcraft 域顶层包，描述整个 Minecraft Game Studio Pipeline 的整体结构。
- `kb.arch.pipeline.voxelcraft.combat_test`：管线描述为 `config → build → server → RCON test → evolve`，mechanics_evolver 对应其中的 `evolve` 阶段，接收 `bw.combat_test_result` 格式输入。
- `kb.arch.pipeline.voxelcraft.engineering`：描述 `GDD → code → compile → debug loop`，与 mechanics_evolver 的 code_patcher + compile_verify 回路在职责上有结构相似性，可参照理解编译反馈机制。

间接相关（同属 voxelcraft 域）：

- `kb.arch.pipeline.voxelcraft.design`
- `kb.arch.pipeline.voxelcraft.art`
- `kb.arch.pipeline.voxelcraft.pm`

> _来源: kb.arch.package.domains_voxelcraft, kb.arch.pipeline.voxelcraft.combat_test, kb.arch.pipeline.voxelcraft.engineering, kb.arch.pipeline.voxelcraft.design, kb.arch.pipeline.voxelcraft.art, kb.arch.pipeline.voxelcraft.pm_

## Known limitations

从代码中可观察到以下明显的未完成区域：

1. **Router 实现均为桩代码**：`GapAnalyzerRouter.run()` 直接返回硬编码的 `{"root_cause": "PARAMETER_LIMIT", "target_file": "BowWeaponBehavior.java", ...}`，`CodePatcherRouter.run()` 返回 `{"file": "BowWeaponBehavior.java", "content": "// patched"}`，`CompileVerifyRouter.run()` 无条件返回 `VerdictKind.PASS`。三者均未实现真实逻辑（未调用 LLM、未执行 `gradlew build`）。

2. **`register_formats()` 为空实现**：`formats.py` 中的 `register_formats` 函数体只有 `pass`，注释说明 format 通过模块级常量注册，但实际注册机制依赖外部 registry 的调用方，当前包内未见显式调用。

3. **输入格式 `bw.combat_test_result` 未在本包内定义**：`gap_analyzer` 节点的 `format_in` 为 `"bw.combat_test_result"`，但 `formats.py` 只定义了 `bw.gap_analysis` 和 `bw.patched_java`，该输入格式的 `Format` 定义需从其他地方（可能是 `domains_voxelcraft` 顶层包）获取，当前可见材料中未看到其定义。

4. **`compile_verify` 的 HARD 验证描述提到运行 `gradlew build`**，但 Router 实现未见任何 subprocess 调用，实际编译验证逻辑尚未实现。

> _来源: `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/routers.py`, `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/formats.py`, `src/omnicompany/packages/domains/voxelcraft/mechanics_evolver/pipeline.py`_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 5 code anchors (6901 chars), 0 plan docs (0 chars), 25 kb refs
