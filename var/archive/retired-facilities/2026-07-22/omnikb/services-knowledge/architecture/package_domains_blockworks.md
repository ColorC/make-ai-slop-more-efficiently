# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:21:08Z
---
omnikb_type: karch
id: kb.arch.package.domains_voxelcraft
name: 'Package: packages/domains/voxelcraft'
tags:
- topic.package
- layer.domains
- domain.voxelcraft
- architecture
maturity: living
summary: voxelcraft — Minecraft Game Studio Pipeline
scope: omnicompany
code_anchors:
- src/omnicompany/packages/domains/voxelcraft/__init__.py
- src/omnicompany/packages/domains/voxelcraft/pipeline.py
- src/omnicompany/packages/domains/voxelcraft/run.py
- src/omnicompany/packages/domains/voxelcraft/formats.py
---

# Package: packages/domains/voxelcraft

> **id**: `kb.arch.package.domains_voxelcraft` · **type**: karch · **maturity**: living

## Why this exists

`voxelcraft` 是 Omnicompany 在 `packages/domains` 层专门为 Minecraft 游戏工作室开发流程而构建的领域包。其核心目标是将游戏公司的部门职能（策划 Design、工程 Engineering、美术 Art、QA）映射到 Omnicompany 的 LAP 节点体系上，形成一套端到端的游戏开发自动化管线。

从 `__init__.py` 的模块文档可见，该包自我定义为"a self-contained game development pipeline built on Omnicompany"，强调独立性与完整性。当前文档声明了两条主管线：`voxelcraft.design`（vision → GDD）和 `voxelcraft.engineering`（GDD → Java Fabric mod 代码），其余管线（Art、PM、QA）也在代码中已有雏形。该包的存在使 Omnicompany 能够以结构化、可审计的方式处理从游戏创意到可编译 mod 代码的完整生产链路，而不是依赖非结构化的 LLM 调用。

当前可见材料中无单独的 plan 文档，设计动机主要来自代码注释中对 `DESIGN.md` 的引用（如 `pipeline.py` 第 4 行），但该文件本身未包含在事实材料中，无法进一步引用其内容。

> _来源: `src/omnicompany/packages/domains/voxelcraft/__init__.py`, `src/omnicompany/packages/domains/voxelcraft/pipeline.py` (头部注释)_

## How it works

`voxelcraft` 的工作机制分为两个层次：格式定义层和管线执行层。

`formats.py` 定义了全部 20 个语义类型，使用 `Format` 和 `FormatRegistry` 构建四层类型系统（L0 Board → L1 PM → L2 Design/Eng/Art → L3 QA）。每个 `Format` 实例携带 `id`、`parent`、`tags`、`json_schema` 和 `semantic_preconditions` 字段，确保数据在管线间流转时有明确的 schema 约束。

`pipeline.py` 使用 `PipelineNode`、`PipelineEdge` 和 `PipelineSpec` 构建管线拓扑。节点分为两种 `NodeKind`：`ANCHOR`（绑定 `AnchorSpec`，含验证逻辑）和 `TRANSFORMER`（绑定 `TransformerSpec`，含转换方法）。已实现的 Design 管线拓扑为：`vision_validator`（`ValidatorKind.SOFT`）→ `design_drafter`（`TransformMethod.LLM`）→ `gdd_validator`（`ValidatorKind.HARD`）→ `balance_extractor` → `design_reviewer`，其中评审失败可通过 `feedback` 路径回流至 `design_drafter`。

`run.py` 负责将管线节点 ID 绑定到具体的 `Router` 实例，通过各子目录下的 router 类（如 `VisionValidatorRouter`、`DesignDrafterRouter`、`EngineerRouter` 等）实现。`PipelineRunner` 在运行时调用这些 binding 函数。当前代码可见 Design、Engineering、Art、PM、visual_assets、structures 六套 binding 函数的骨架。

基于当前代码片段只能看到 Design 管线的节点定义前半段（`pipeline.py` 截至第 119 行），`balance_extractor`、`design_reviewer` 节点及 Engineering/Art/PM 管线的完整拓扑需读完整的 `pipeline.py` 文件。

> _来源: `src/omnicompany/packages/domains/voxelcraft/formats.py`, `src/omnicompany/packages/domains/voxelcraft/pipeline.py`, `src/omnicompany/packages/domains/voxelcraft/run.py`_

## Public surface

以下是 `__init__.py` 的 `__all__` 中明确对外暴露的公共接口：

**Format 常量（共 20 个）：**

| 层级 | 标识符 | Format ID |
|------|--------|-----------|
| L0 Board | `BW_VISION` | `bw.vision` |
| L0 Board | `BW_EPIC` | `bw.epic` |
| L1 PM | `BW_SPRINT_GOAL` | — |
| L1 PM | `BW_SCHEDULE_DAG` | — |
| L2 Design | `BW_GDD`, `BW_BALANCE_SHEET`, `BW_UX_FLOW` | — |
| L2 Engineering | `BW_CODE_SPEC`, `BW_SOURCE_JAVA`, `BW_COMPILE_RESULT`, `BW_DEBUG_TRACE` | — |
| L2 Art | `BW_ASSET_REQUEST`, `BW_RAW_ASSET`, `BW_REFINED_ASSET` | — |
| L3 QA | `BW_PAPER_MODEL_REPORT`, `BW_VOYAGER_REPORT`, `BW_VLM_REPORT`, `BW_CRITIQUE`, `BW_RELEASE_VERDICT` | — |

**Format 注册接口：**
- `ALL_FORMATS` — 全量 Format 对象集合
- `register_formats` — 向 `FormatRegistry` 注册所有格式的函数

**管线构建接口：**
- `build_design_pipeline()` → `PipelineSpec`
- `build_engineering_pipeline()` → `PipelineSpec`
- `get_pipeline(name)` — 按名称获取管线
- `PIPELINES` — 已注册管线字典

`run.py` 中的 `build_design_bindings`、`build_engineering_bindings`、`build_art_bindings`、`build_pm_bindings`、`build_visual_assets_bindings`、`build_structures_bindings` 函数也是对外接口，但未出现在 `__all__` 中，其对外可见性只通过模块导入确定。

> _来源: `src/omnicompany/packages/domains/voxelcraft/__init__.py`, `src/omnicompany/packages/domains/voxelcraft/formats.py`, `src/omnicompany/packages/domains/voxelcraft/run.py`_

## Internal structure

从 `__init__.py` 的 import 结构和 `run.py` 中可推断出以下子模块划分：

- **`formats.py`** — 语义类型系统，定义所有 `Format` 实例和 `ALL_FORMATS`、`register_formats`
- **`pipeline.py`** — 管线拓扑构建，提供 `build_design_pipeline`、`build_engineering_pipeline`、`get_pipeline`、`PIPELINES`
- **`run.py`** — Router binding 层，将管线节点 ID 映射到具体 `Router` 实现类
- **`routers/`** — Router 实现子目录，根据 `run.py` 的延迟导入可识别以下子模块：
  - `routers/design.py` — 含 `VisionValidatorRouter`、`DesignDrafterRouter`、`GDDValidatorRouter`、`BalanceExtractorRouter`、`DesignReviewerRouter`
  - `routers/engineering.py` — 含 `CodeSpecTranslatorRouter`、`EngineerRouter`、`CompilerQARouter`、`DebugRouter`
  - `routers/art.py` — 含 `AssetSourcerRouter`、`AssetAnalyzerRouter`、`AssetValidatorRouter`
  - `routers/pm.py` — 含 `EpicDecomposerRouter`、`DependencyAnalyzerRouter`、`ScheduleValidatorRouter`
  - `routers/visual_assets.py` — 含 `VanillaEntitySourceRouter`、`TextureEvaluatorRouter`、`TextureFilterRouter`、`TextureMapperRouter`、`VisualValidatorRouter`
  - `routers/structures.py` — 含 `SchematicScoutRouter`、`SchematicParserRouter`、`StructureValidatorRouter`、`FillOpConverterRouter`
  - `routers/structure_understander.py` — 含 `StructureEvaluatorRouter`、`StructureFilterRouter`
  - `routers/block_substituter.py` — 含 `BlockSubstituterRouter`

这些文件路径均为从 `run.py` 的 import 语句中推断，实际文件清单未在 code_anchors 中完整列出。

> _来源: `src/omnicompany/packages/domains/voxelcraft/__init__.py`, `src/omnicompany/packages/domains/voxelcraft/run.py`_

## Files

基于代码片段中出现的文件路径：

- `src/omnicompany/packages/domains/voxelcraft/__init__.py` — 包入口，声明 `__all__`，聚合 formats 和 pipeline 的公共导出
- `src/omnicompany/packages/domains/voxelcraft/formats.py` — 定义全部 20 个语义 `Format` 实例及四层类型层级（L0–L3），包含 JSON Schema 约束与 `semantic_preconditions`
- `src/omnicompany/packages/domains/voxelcraft/pipeline.py` — 使用 `PipelineNode`/`PipelineSpec` 构建 Design 和 Engineering 管线拓扑，声明节点间路由规则
- `src/omnicompany/packages/domains/voxelcraft/run.py` — 为所有管线（design / engineering / art / pm / visual_assets / structures）提供 Router binding 函数，将节点 ID 映射到 `Router` 实例

code_anchors 中未提供 `routers/` 子目录下各文件的独立路径条目，其存在仅从 `run.py` 的延迟导入中推断。

> _来源: `src/omnicompany/packages/domains/voxelcraft/__init__.py`, `src/omnicompany/packages/domains/voxelcraft/formats.py`, `src/omnicompany/packages/domains/voxelcraft/pipeline.py`, `src/omnicompany/packages/domains/voxelcraft/run.py`_

## Related

与本条目直接相关的 KB 条目：

**同域管线条目（voxelcraft 管线的各子管线）：**
- `kb.arch.pipeline.voxelcraft.design` — voxelcraft 策划管线，对应 `build_design_pipeline()`
- `kb.arch.pipeline.voxelcraft.engineering` — voxelcraft 工程管线，对应 `build_engineering_pipeline()`
- `kb.arch.pipeline.voxelcraft.art` — voxelcraft 美术管线，对应 `build_art_bindings()`
- `kb.arch.pipeline.voxelcraft.pm` — voxelcraft PM 管线，对应 `build_pm_bindings()`
- `kb.arch.pipeline.voxelcraft.structures` — voxelcraft 建筑管线，对应 `build_structures_bindings()`
- `kb.arch.pipeline.voxelcraft.combat_test` — voxelcraft 战斗测试管线

**自动生成的衍生包：**
- `kb.arch.package.domains_voxelcraft_mechanics_evolver` — voxelcraft 域内的 mechanics-evolver 自动生成管线

**可参考的平行 domain 包（结构相似）：**
- `kb.arch.package.domains_narrative` — 同为 domains 层的创作类领域包
- `kb.arch.package.domains_demogame_produce` — 同为 domains 层的游戏生产管线

> _来源: `kb.arch.pipeline.voxelcraft.design`, `kb.arch.pipeline.voxelcraft.engineering`, `kb.arch.pipeline.voxelcraft.art`, `kb.arch.pipeline.voxelcraft.pm`, `kb.arch.pipeline.voxelcraft.structures`, `kb.arch.pipeline.voxelcraft.combat_test`, `kb.arch.package.domains_voxelcraft_mechanics_evolver`, `kb.arch.package.domains_narrative`, `kb.arch.package.domains_demogame_produce`_

## Known limitations

从代码片段中可观察到以下明确的局限与未完成区域：

1. **节点成熟度均为 `HYPOTHETICAL`**：`pipeline.py` 中 `vision_validator`（`NodeMaturity.HYPOTHETICAL`）和 `design_drafter`（`NodeMaturity.HYPOTHETICAL`）均标注为假设态，表明 Design 管线尚未经过真实验证。

2. **Engineering/Art/PM 管线拓扑缺失**：`pipeline.py` 文档字符串明确写道 "Future phases will add Engineering, Art, and QA pipelines"，但 `run.py` 中已有 `build_engineering_bindings`、`build_art_bindings`、`build_pm_bindings` 的 binding 骨架，两者之间存在不一致——pipeline 拓扑定义缺失而 router binding 已存在。

3. **`pipeline.py` 代码片段截断**：当前可见内容仅到第 119 行，`gdd_validator` 节点定义未完整呈现，`balance_extractor`、`design_reviewer` 节点以及完整的边定义（`PipelineEdge`）均不可见。

4. **`run.py` 中 `build_structures_bindings` 截断**：片段在第 119 行注释处结束（"ModExplorer: prefer the AgentNodeLoop multi-turn version"），说明 structures 管线的 9 个节点未全部列出。

5. **`formats.py` 中 L1–L3 大部分 Format 的 schema 未在代码片段中展示**：仅 `BW_VISION`、`BW_EPIC`、`BW_SPRINT_GOAL` 的 schema 头部可见，其余 17 个 Format 的完整约束无法从当前片段核实。

> _来源: `src/omnicompany/packages/domains/voxelcraft/pipeline.py`, `src/omnicompany/packages/domains/voxelcraft/run.py`, `src/omnicompany/packages/domains/voxelcraft/formats.py`_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 4 code anchors (20149 chars), 0 plan docs (0 chars), 25 kb refs
