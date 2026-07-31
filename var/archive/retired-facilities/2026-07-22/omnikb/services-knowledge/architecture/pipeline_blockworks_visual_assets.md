# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:18:42Z
---
omnikb_type: karch
id: kb.arch.pipeline.voxelcraft.visual_assets
name: 'Pipeline: voxelcraft.visual_assets'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.voxelcraft.visual_assets
- domain.voxelcraft
- architecture
maturity: living
summary: voxelcraft 兵种外观管线 — entity model search → style eval → texture map
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L645-L670
---

# Pipeline: voxelcraft.visual_assets

> **id**: `kb.arch.pipeline.voxelcraft.visual_assets` · **type**: karch · **maturity**: living

## Why this exists

`voxelcraft.visual_assets` 管线负责处理 voxelcraft 项目中兵种的外观资产生产流程，覆盖从实体模型搜索、风格评估到纹理贴图的完整链路（entity model search → style eval → texture map）。该管线与同域的 `voxelcraft.art`、`voxelcraft.structures` 等管线并列注册，共同支撑 voxelcraft 项目的不同专项产出。外观资产管线作为独立条目存在，说明兵种视觉相关的工作流在 voxelcraft 域内被视为独立关注点，与通用美术资产管线（`voxelcraft.art`）有所区分。

当前可见材料中无 plan 文档，设计动机只能从 seed description 与注册代码的字面描述推断，不进一步延伸。

> _来源: seed_description, src/omnicompany/core/pipelines.py:L645-L653_

## How it works

基于当前代码片段，只能看到该管线在 `pipelines.py` 中的注册逻辑：

- `PipelineEntry` 被构造并传入 `register()`，字段包括：
  - `name="voxelcraft.visual_assets"`
  - `description` 标注了三阶段流程：entity model search → style eval → texture map
  - `build_pipeline=_lazy(f"{_bw_pkg}.pipeline", "build_visual_asset_pipeline")` — 通过懒加载从 voxelcraft 子包的 `pipeline` 模块中延迟导入 `build_visual_asset_pipeline` 函数来构建管线
  - `build_bindings=_lazy_fn(f"{_bw_pkg}.run", "build_visual_assets_bindings")` — 同样懒加载，从 `run` 模块导入 `build_visual_assets_bindings` 来构建 bindings
  - `default_db_dir="data/voxelcraft"` 与同域其他管线共用同一数据目录
  - `default_max_steps=15`

完整的三阶段（entity model search / style eval / texture map）内部实现存在于 `_bw_pkg.pipeline` 与 `_bw_pkg.run` 模块中，当前代码片段未展示这两个文件，无法进一步描述各阶段的具体实现机制。

> _来源: src/omnicompany/core/pipelines.py:L645-L653_

## Public surface

从注册代码可以识别以下对外可见的接口标识：

| 标识 | 类型 | 说明 |
|---|---|---|
| `"voxelcraft.visual_assets"` | 管线名 (Pipeline name) | 用于 CLI 或 EventBus 按名称寻址该管线 |
| `build_visual_asset_pipeline` | 函数名 | 位于 `_bw_pkg.pipeline` 模块，负责构建管线对象，通过 `_lazy` 延迟导入 |
| `build_visual_assets_bindings` | 函数名 | 位于 `_bw_pkg.run` 模块，负责构建 bindings，通过 `_lazy_fn` 延迟导入 |

注意：`_bw_pkg` 的具体包路径在当前代码片段中未展开，上述两个函数的签名和返回类型也未可见，只能确认其名称与所在模块的相对位置。

> _来源: src/omnicompany/core/pipelines.py:L645-L653_

## Internal structure

从注册代码可以推断出该管线的内部结构分布在两个子模块中：

- **`<_bw_pkg>.pipeline`** — 包含 `build_visual_asset_pipeline`，推测是管线 DAG 的构造逻辑，对应 entity model search → style eval → texture map 的各步骤节点定义
- **`<_bw_pkg>.run`** — 包含 `build_visual_assets_bindings`，推测是管线运行时绑定（输入输出参数、工具绑定等）的构建逻辑

这一"pipeline 构造 + run bindings"的两模块结构与同域的 `voxelcraft.structures` 管线（`build_structure_pipeline` / `build_structures_bindings`）保持一致，是 voxelcraft 域内的统一分层惯例。

`_bw_pkg` 变量所指向的实际包路径在当前代码片段中未展示，完整的文件清单和 import 关系需阅读 `_bw_pkg` 定义处及对应的 `pipeline.py`、`run.py` 文件。

> _来源: src/omnicompany/core/pipelines.py:L645-L664_

## Files

基于 code_anchors 仅有以下一个文件可见：

- `src/omnicompany/core/pipelines.py` — 核心管线注册文件，负责通过 `register(PipelineEntry(...))` 将 `voxelcraft.visual_assets` 及其他管线登记到系统中；包含 `_lazy` / `_lazy_fn` 懒加载工具，将实际实现推迟到运行时导入。

`_bw_pkg.pipeline` 与 `_bw_pkg.run` 两个实现文件的具体路径未出现在当前 code_anchors 中，无法列出。

> _来源: src/omnicompany/core/pipelines.py:L645-L670_

## Related

以下 KB 条目与本管线直接或间接相关：

- `kb.arch.pipeline.voxelcraft.art` — 同域美术管线，与本管线同属 voxelcraft 视觉资产范畴，存在职责边界的关联
- `kb.arch.pipeline.voxelcraft.structures` — 同域建筑管线，在注册代码中与本管线紧邻注册，共用 `default_db_dir` 和 `default_max_steps` 配置
- `kb.arch.pipeline.voxelcraft.design` — 同域策划管线，视觉外观资产通常依赖策划方向输入
- `kb.arch.pipeline.voxelcraft.engineering` — 同域工程管线，纹理与模型资产最终需要工程侧集成
- `kb.arch.pipeline.voxelcraft.combat_test` — 同域战斗测试管线，兵种外观与战斗实体存在关联

> _来源: kb_context_

## Known limitations

当前可见的代码片段中没有 TODO、FIXME 或 XXX 注释。seed description 也未明确提及局限。

从代码结构可以观察到：

- `build_visual_asset_pipeline` 与 `build_visual_assets_bindings` 均通过懒加载引入，若 `_bw_pkg` 所在包不可用，注册会被 `except Exception` 静默跳过（见 L663-L664：`logger.debug("skip voxelcraft pipelines: %s", e)`），这意味着该管线在部分部署环境下可能静默缺失，且只有 debug 级别日志记录。
- 三阶段流程（entity model search → style eval → texture map）的具体实现文件当前不在 code_snippets 范围内，无法评估各阶段是否有未完成区域。

> _来源: src/omnicompany/core/pipelines.py:L645-L664, seed_description_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 1 code anchors (1282 chars), 0 plan docs (0 chars), 25 kb refs
