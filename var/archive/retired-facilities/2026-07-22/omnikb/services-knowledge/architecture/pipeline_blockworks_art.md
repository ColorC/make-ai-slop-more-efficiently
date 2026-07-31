# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:05:17Z
---
omnikb_type: karch
id: kb.arch.pipeline.voxelcraft.art
name: 'Pipeline: voxelcraft.art'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.voxelcraft.art
- domain.voxelcraft
- architecture
maturity: living
summary: voxelcraft 美术管线 — 通用资产搜索 + 分析 + 验证
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L636-L661
---

# Pipeline: voxelcraft.art

> **id**: `kb.arch.pipeline.voxelcraft.art` · **type**: karch · **maturity**: living

## Why this exists

voxelcraft 美术管线（`voxelcraft.art`）在 Omnicompany 管线注册表中作为 voxelcraft 域专属管线之一被注册，其 seed description 定义其职责为「通用资产搜索 + 分析 + 验证」。该管线与同域的 `voxelcraft.visual_assets`（兵种外观）、`voxelcraft.structures`（建筑/schematic）等管线并列，共同覆盖 voxelcraft 项目的不同美术资产维度。`voxelcraft.art` 定位为「通用」，推测承担其他专项管线未覆盖的美术资产类别，但当前可见材料（无 plan 文档、无 KExperiment 条目）不足以进一步说明其具体设计动机。

> _来源: seed_description, src/omnicompany/core/pipelines.py:L636-L644_

## How it works

从 `src/omnicompany/core/pipelines.py` 第 636–644 行可见，`voxelcraft.art` 管线通过 `PipelineEntry` 注册，关键字段如下：

- `build_pipeline`：通过 `_lazy(f"{_bw_pkg}.pipeline", "build_art_pipeline")` 延迟加载，指向 voxelcraft 子包的 `pipeline` 模块中的 `build_art_pipeline` 函数。
- `build_bindings`：通过 `_lazy_fn(f"{_bw_pkg}.run", "build_art_bindings")` 延迟加载，指向 voxelcraft 子包的 `run` 模块中的 `build_art_bindings` 函数。
- `default_db_dir`：`"data/voxelcraft"`，与同域其他管线共享同一数据目录。
- `default_max_steps`：`15`，与同域其他管线一致。

具体管线节点的串联逻辑（搜索→分析→验证各步骤如何衔接）在 `build_art_pipeline` 内部实现，基于当前代码片段只能看到注册入口，完整机制需读 `{_bw_pkg}/pipeline.py` 和 `{_bw_pkg}/run.py` 文件。

> _来源: src/omnicompany/core/pipelines.py:L636-L644_

## Public surface

基于当前代码片段，该管线对外暴露的接口为：

- **管线名**：`"voxelcraft.art"`（注册于 `PipelineEntry.name`，可通过 Omnicompany CLI 或管线注册表按名索引）
- **构建函数**（延迟加载引用）：
  - `build_art_pipeline`（位于 `{_bw_pkg}.pipeline` 模块）
  - `build_art_bindings`（位于 `{_bw_pkg}.run` 模块）
- **默认配置字段**：`default_db_dir="data/voxelcraft"`，`default_max_steps=15`

`build_art_pipeline` 和 `build_art_bindings` 的具体签名与返回类型在当前代码片段中不可见，需读对应源文件确认。

> _来源: src/omnicompany/core/pipelines.py:L636-L644_

## Internal structure

当前可见材料（code_snippets）仅展示了管线注册层的 `PipelineEntry` 声明，内部子模块的划分只能从延迟加载的模块路径推断：

- **`{_bw_pkg}.pipeline`**：包含 `build_art_pipeline`，负责定义管线节点图（DAG）。
- **`{_bw_pkg}.run`**：包含 `build_art_bindings`，负责将管线节点与具体工具/LLM 调用绑定。

`_bw_pkg` 变量的实际值在当前代码片段中未展开（需查阅 `pipelines.py` 上下文），因此无法给出确切的包路径。file_list 中也未单独列出 voxelcraft 子包的文件清单，完整内部结构需读 `_bw_pkg` 所指包目录下的 `pipeline.py` 和 `run.py`。

> _来源: src/omnicompany/core/pipelines.py:L636-L644_

## Files

当前 code_anchors 仅提供了管线注册所在文件：

- `src/omnicompany/core/pipelines.py`（L636–L644）：Omnicompany 全局管线注册表，`voxelcraft.art` 在此通过 `PipelineEntry` 注册，记录管线名、domain、延迟加载的构建函数引用及默认参数。

voxelcraft 子包内的 `pipeline.py`（含 `build_art_pipeline`）和 `run.py`（含 `build_art_bindings`）未出现在 code_anchors 中，无法列出其路径或作用描述。

> _来源: src/omnicompany/core/pipelines.py:L636-L644_

## Related

与本条目直接相关的 KB 已有条目：

- `kb.arch.pipeline.voxelcraft.visual_assets` — 同域专项管线，覆盖兵种外观资产，与 `voxelcraft.art` 并列注册。
- `kb.arch.pipeline.voxelcraft.structures` — 同域专项管线，覆盖建筑/schematic 资产，与 `voxelcraft.art` 并列注册。
- `kb.arch.pipeline.voxelcraft.design` — 同域策划管线，上游产物可能成为美术管线输入。
- `kb.arch.pipeline.voxelcraft.engineering` — 同域工程管线，与美术资产验证结果可能存在依赖关系。
- `kb.arch.pipeline.voxelcraft.combat_test` — 同域战斗测试管线，美术资产验证后可能进入测试流程。
- `kb.arch.pipeline.voxelcraft.pm` — 同域 PM 管线，统筹调度包括美术管线在内的各域工作。

> _来源: kb_context_

## Known limitations

从当前可见代码片段中可以观察到以下局限或不确定区域：

1. **实现不可见**：`build_art_pipeline` 和 `build_art_bindings` 均通过 `_lazy` / `_lazy_fn` 延迟加载，当前代码片段未展示其实现内容，无法确认「通用资产搜索 + 分析 + 验证」各步骤是否已实际实现。
2. **无 plan 文档**：本条目无关联 plan 文档，管线的验收标准、资产类型范围等均无文档依据。
3. **代码片段中无 TODO/FIXME 注释**：当前可见范围内未发现明确的 TODO 或 FIXME 标注，无法从代码注释层面得知已知缺陷。

完整的局限性评估需读 `{_bw_pkg}/pipeline.py` 和 `{_bw_pkg}/run.py` 中的实现代码。

> _来源: src/omnicompany/core/pipelines.py:L636-L644, seed_description_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 1 code anchors (1480 chars), 0 plan docs (0 chars), 25 kb refs
