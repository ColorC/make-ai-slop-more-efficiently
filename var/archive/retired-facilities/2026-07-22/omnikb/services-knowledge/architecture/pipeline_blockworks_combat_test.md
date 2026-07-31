# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:05:57Z
---
omnikb_type: karch
id: kb.arch.pipeline.voxelcraft.combat_test
name: 'Pipeline: voxelcraft.combat_test'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.voxelcraft.combat_test
- domain.voxelcraft
- architecture
maturity: living
summary: voxelcraft 战斗测试管线 — config → build → server → RCON test → evolve
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L618-L643
---

# Pipeline: voxelcraft.combat_test

> **id**: `kb.arch.pipeline.voxelcraft.combat_test` · **type**: karch · **maturity**: living

## Why this exists

该管线面向 voxelcraft 游戏项目的战斗系统测试场景，涵盖从配置生成、构建、服务器启动、RCON 远程指令测试到演化迭代的完整闭环流程。Seed description 将其核心阶段描述为 `config → build → server → RCON test → evolve`，说明该管线是一条自动化战斗验证通道，目的是在不依赖手动操作的前提下完成战斗参数的端到端测试与迭代。

当前可见材料（仅 seed description 与注册代码）不足以进一步说明该管线产生的具体业务动机或与其他 voxelcraft 管线的分工逻辑。plan_docs 段为空，无 plan 文档支撑。

> _来源: seed_description, src/omnicompany/core/pipelines.py:L618-L626_

## How it works

在 `src/omnicompany/core/pipelines.py` 中，该管线以 `PipelineEntry` 形式注册，名称为 `"voxelcraft.combat_test"`，所属 domain 为 `"voxelcraft"`。注册时通过两个懒加载机制绑定实现：

- `build_pipeline` 字段使用 `_lazy(f"{_bw_pkg}.pipeline", "build_combat_test_pipeline")` 延迟加载管线构建函数 `build_combat_test_pipeline`，位于 `{_bw_pkg}.pipeline` 模块。
- `build_bindings` 字段使用 `_lazy_fn(f"{_bw_pkg}.run", "build_combat_test_bindings")` 延迟加载绑定构建函数 `build_combat_test_bindings`，位于 `{_bw_pkg}.run` 模块。
- `default_db_dir` 为 `"data/voxelcraft"`，`default_max_steps` 为 `30`（在当前可见的 voxelcraft 管线中步数上限最高）。

基于当前代码片段只能看到注册层的 `PipelineEntry` 声明；`build_combat_test_pipeline` 与 `build_combat_test_bindings` 的具体实现逻辑需读取 `{_bw_pkg}.pipeline` 和 `{_bw_pkg}.run` 两个模块才能确认。

> _来源: src/omnicompany/core/pipelines.py:L618-L626_

## Public surface

根据注册代码，该管线对外暴露的接口如下：

- **管线名 (pipeline name)**: `"voxelcraft.combat_test"` — 用于 CLI 或 EventBus 调用时定位该管线。
- **domain**: `"voxelcraft"` — 归属域标识。
- **build_pipeline 入口**: `build_combat_test_pipeline`（位于 `{_bw_pkg}.pipeline` 模块，通过 `_lazy` 懒加载）。
- **build_bindings 入口**: `build_combat_test_bindings`（位于 `{_bw_pkg}.run` 模块，通过 `_lazy_fn` 懒加载）。
- **默认参数**: `default_db_dir="data/voxelcraft"`，`default_max_steps=30`。

超出注册声明之外的 public 类、Router、Format id 等接口，当前代码片段不足以确认，需读取 `{_bw_pkg}.pipeline` 和 `{_bw_pkg}.run` 文件。

> _来源: src/omnicompany/core/pipelines.py:L618-L626_

## Internal structure

当前可见材料不足以回答这一段。仅从注册代码可知该管线的实现分布在两个模块：`{_bw_pkg}.pipeline`（包含 `build_combat_test_pipeline`）和 `{_bw_pkg}.run`（包含 `build_combat_test_bindings`），但两个模块的内部子模块划分、import 关系及阶段划分均无法从现有片段确认。补完此段需要阅读：`{_bw_pkg}/pipeline.py`（或同名目录）与 `{_bw_pkg}/run.py`，以及对应的 file_list 清单。

> _来源: src/omnicompany/core/pipelines.py:L618-L626_

## Files

当前 code_anchors 段未提供具体文件路径清单。从代码片段可推断存在以下相关文件，但路径中的 `{_bw_pkg}` 变量的实际值在可见材料中未展开，故无法给出完整路径：

- `src/omnicompany/core/pipelines.py` — 管线注册中心，包含 `voxelcraft.combat_test` 的 `PipelineEntry` 注册逻辑（L618-L626）。
- `{_bw_pkg}/pipeline.py`（路径待确认）— 应包含 `build_combat_test_pipeline` 函数实现。
- `{_bw_pkg}/run.py`（路径待确认）— 应包含 `build_combat_test_bindings` 函数实现。

补完此段需要阅读 code_anchors 完整列表或 `_bw_pkg` 变量定义处的源码。

> _来源: src/omnicompany/core/pipelines.py:L618-L626_

## Related

与本管线直接相关或同属 voxelcraft 域的 KB 条目：

- `kb.arch.pipeline.voxelcraft.art` — 同域美术管线，共享 `data/voxelcraft` 数据目录与相同注册模式。
- `kb.arch.pipeline.voxelcraft.design` — 同域策划管线，战斗测试依赖策划产出。
- `kb.arch.pipeline.voxelcraft.engineering` — 同域工程管线，包含编译/调试循环，与战斗测试的 build 阶段存在潜在上下游关系。
- `kb.arch.pipeline.voxelcraft.pm` — 同域 PM 管线，同文件相邻注册，共享默认配置。
- `kb.arch.pipeline.voxelcraft.structures` — 同域建筑管线，战斗场景可能依赖结构数据。
- `kb.arch.pipeline.voxelcraft.visual_assets` — 同域兵种外观管线，与战斗单位相关。
- `kb.arch.pipeline.debug` — 通用 debug 管线，与 `evolve` 阶段的迭代调试可能存在协作关系。

> _来源: kb.arch.pipeline.voxelcraft.art, kb.arch.pipeline.voxelcraft.design, kb.arch.pipeline.voxelcraft.engineering, kb.arch.pipeline.voxelcraft.pm, kb.arch.pipeline.voxelcraft.structures, kb.arch.pipeline.voxelcraft.visual_assets, kb.arch.pipeline.debug_

## Known limitations

基于当前可见的代码片段，无 TODO/FIXME/XXX 注释出现在 L618-L626 区间内。Seed description 本身标注 maturity 为 `draft`，表明该管线尚未成熟。

以下局限性基于代码缺失而非推断：

- `build_combat_test_pipeline` 和 `build_combat_test_bindings` 的具体实现在当前可见片段中完全缺失，无法确认 `config → build → server → RCON test → evolve` 各阶段是否已全部实现。
- `default_max_steps=30` 相比同域其他管线（如 `voxelcraft.pm` 的 15 步、`voxelcraft.art` 的 15 步）更高，原因不明，无 comment 说明。

> _来源: src/omnicompany/core/pipelines.py:L618-L626, seed_description_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 1 code anchors (1441 chars), 0 plan docs (0 chars), 25 kb refs
