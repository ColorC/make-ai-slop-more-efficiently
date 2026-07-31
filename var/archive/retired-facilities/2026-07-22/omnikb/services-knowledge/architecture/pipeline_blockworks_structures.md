# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:09:31Z
---
omnikb_type: karch
id: kb.arch.pipeline.voxelcraft.structures
name: 'Pipeline: voxelcraft.structures'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.voxelcraft.structures
- domain.voxelcraft
- architecture
maturity: living
summary: voxelcraft 建筑管线 — schematic search → parse → validate → FillOp
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L654-L679
---

# Pipeline: voxelcraft.structures

> **id**: `kb.arch.pipeline.voxelcraft.structures` · **type**: karch · **maturity**: living

## Why this exists

voxelcraft 项目需要将建筑结构（schematic）从数据库检索、解析、校验，最终产出可执行的填充操作（FillOp）。`voxelcraft.structures` 管线承担这一完整链路，使建筑结构的生产流程可以在 Omnicompany 的统一调度框架下运行，与同域的美术、工程、策划等管线并列注册、统一管理。

- seed description 明确描述该管线的四阶段流程：schematic search → parse → validate → FillOp
- 管线归属 `domain="voxelcraft"`，与其他 voxelcraft 子管线共享同一域
- 默认数据目录为 `data/voxelcraft`，最大步数为 15

> _来源: seed_description, src/omnicompany/core/pipelines.py:L654-L662_

## How it works

在 `src/omnicompany/core/pipelines.py` 中，`voxelcraft.structures` 通过 `register(PipelineEntry(...))` 注册。注册时采用懒加载模式：

- `build_pipeline` 字段指向 `_lazy(f"{_bw_pkg}.pipeline", "build_structure_pipeline")`，即在首次调用时才从 `{_bw_pkg}.pipeline` 模块导入 `build_structure_pipeline` 函数并执行。
- `build_bindings` 字段指向 `_lazy_fn(f"{_bw_pkg}.run", "build_structures_bindings")`，同样延迟导入 `{_bw_pkg}.run` 模块中的 `build_structures_bindings`。
- `_lazy` 函数（L671–L679）使用 `_cache` 字典缓存首次导入结果，后续调用直接从缓存取函数，避免重复 `importlib.import_module`。
- 注册过程包裹在 `try/except` 块中（L663–L664），若 voxelcraft 依赖缺失则以 `debug` 级别日志跳过，不中断其他管线注册。

基于当前代码片段只能看到注册层的懒加载机制，完整的四阶段流程（schematic search → parse → validate → FillOp）需读 `{_bw_pkg}.pipeline` 和 `{_bw_pkg}.run` 两个模块的实现。

> _来源: src/omnicompany/core/pipelines.py:L654-L679_

## Public surface

从当前代码片段可见的对外接口：

- **管线名**: `"voxelcraft.structures"`（注册 key，供 CLI 或调度器按名调用）
- **`build_structure_pipeline`**（位于 `{_bw_pkg}.pipeline` 模块，通过 `_lazy` 包装后作为 `build_pipeline` 暴露）
- **`build_structures_bindings`**（位于 `{_bw_pkg}.run` 模块，通过 `_lazy_fn` 包装后作为 `build_bindings` 暴露）
- **`PipelineEntry` 参数**：`default_db_dir="data/voxelcraft"`，`default_max_steps=15`

以上接口名均直接来自代码字面量。`{_bw_pkg}` 的实际包路径在当前代码片段中未展开，需读 `pipelines.py` 更早的变量定义段才能确认。

> _来源: src/omnicompany/core/pipelines.py:L654-L679_

## Internal structure

当前代码片段仅展示了 `pipelines.py` 中的注册层，内部子模块结构由两个懒加载目标隐含：

- `{_bw_pkg}.pipeline`：预期包含 `build_structure_pipeline`，对应管线构建逻辑（schematic search → parse → validate → FillOp 的步骤编排）
- `{_bw_pkg}.run`：预期包含 `build_structures_bindings`，对应运行时 bindings 配置

`{_bw_pkg}` 变量的实际值（即 voxelcraft 包的根路径）在当前片段中未展示，完整的文件层次结构需读 `pipelines.py` 顶部的变量声明，以及 file_list 中 voxelcraft 相关文件。当前 file_list 材料不足，无法进一步细化子模块划分。

> _来源: src/omnicompany/core/pipelines.py:L654-L679_

## Files

当前 code_anchors 中只提供了以下文件路径：

- `src/omnicompany/core/pipelines.py`：Omnicompany 核心管线注册中心，包含 `voxelcraft.structures` 的 `PipelineEntry` 注册逻辑及 `_lazy`/`_lazy_fn` 懒加载工具函数。

`{_bw_pkg}.pipeline` 和 `{_bw_pkg}.run` 对应的实际文件路径未出现在 code_anchors 中，无法在此列出。

> _来源: src/omnicompany/core/pipelines.py:L654-L679_

## Related

与本管线直接相关的 KB 条目：

- `kb.arch.pipeline.voxelcraft.art` — 同域美术管线，结构类似（资产搜索 + 分析 + 验证）
- `kb.arch.pipeline.voxelcraft.combat_test` — 同域战斗测试管线
- `kb.arch.pipeline.voxelcraft.design` — 同域策划管线
- `kb.arch.pipeline.voxelcraft.engineering` — 同域工程管线，与 structures 管线在构建产物上可能存在依赖
- `kb.arch.pipeline.voxelcraft.pm` — 同域 PM 管线
- `kb.arch.pipeline.voxelcraft.visual_assets` — 同域兵种外观管线

以上均为 `domain=voxelcraft` 下的平行子管线，共享同一数据目录 `data/voxelcraft` 的可能性较高，但具体依赖关系需读各管线实现文件确认。

> _来源: kb_context_

## Known limitations

从当前代码片段可观察到以下局限：

- `{_bw_pkg}` 变量的实际值在片段中未展开，若 voxelcraft 包路径变更则懒加载目标 `{_bw_pkg}.pipeline` 和 `{_bw_pkg}.run` 均会静默失败（被 `except Exception` 吃掉，仅留 debug 日志）。
- 注册失败的错误处理粒度粗糙：任何异常（包括语法错误、import 错误、配置错误）均被同一个 `except Exception as e` 捕获，只输出 `logger.debug("skip voxelcraft pipelines: %s", e)`，问题排查难度较高。
- 四阶段流程（schematic search → parse → validate → FillOp）的具体实现不在当前可见代码片段中，无法判断各阶段是否存在 TODO/FIXME。完整局限性评估需读 `{_bw_pkg}.pipeline` 和 `{_bw_pkg}.run`。

> _来源: src/omnicompany/core/pipelines.py:L654-L679, seed_description_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 1 code anchors (1167 chars), 0 plan docs (0 chars), 25 kb refs
