# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:08:51Z
---
omnikb_type: karch
id: kb.arch.pipeline.voxelcraft.pm
name: 'Pipeline: voxelcraft.pm'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.voxelcraft.pm
- domain.voxelcraft
- architecture
maturity: living
summary: voxelcraft PM 管线 — epic → sprint goals → schedule DAG
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L627-L652
---

# Pipeline: voxelcraft.pm

> **id**: `kb.arch.pipeline.voxelcraft.pm` · **type**: karch · **maturity**: living

## Why this exists

voxelcraft.pm 管线服务于 voxelcraft 项目的项目管理场景，其职责链为 epic → sprint goals → schedule DAG，即将高层 epic 目标逐步分解为可执行的冲刺目标，并生成带依赖关系的调度 DAG。这一管线是 voxelcraft 域下多条垂直管线之一，与策划（design）、工程（engineering）、美术（art）等管线并列，共同支撑 voxelcraft 游戏项目的全流程 AI 辅助生产。

当前可见材料（seed description + 注册代码）仅说明了该管线的顶层职责描述，没有附带 plan 文档，因此无法进一步展开其设计动机或业务背景。

> _来源: seed_description (`kb.arch.pipeline.voxelcraft.pm`), `src/omnicompany/core/pipelines.py:L627-L635`_

## How it works

基于当前代码片段，只能看到 `voxelcraft.pm` 管线通过 `PipelineEntry` 数据结构完成注册：

- `name`: `"voxelcraft.pm"` — 管线的唯一标识符
- `description`: `"voxelcraft PM 管线 — epic → sprint goals → schedule DAG"`
- `domain`: `"voxelcraft"`
- `build_pipeline`: 通过 `_lazy(f"{_bw_pkg}.pipeline", "build_pm_pipeline")` 延迟加载，指向 `{_bw_pkg}.pipeline` 模块中的 `build_pm_pipeline` 函数
- `build_bindings`: 通过 `_lazy_fn(f"{_bw_pkg}.run", "build_pm_bindings")` 延迟加载，指向 `{_bw_pkg}.run` 模块中的 `build_pm_bindings` 函数
- `default_db_dir`: `"data/voxelcraft"`
- `default_max_steps`: `15`

完整的管线执行逻辑（epic 分解步骤、DAG 构造方式、sprint goal 生成逻辑）位于 `{_bw_pkg}.pipeline` 和 `{_bw_pkg}.run` 两个模块中，当前代码片段未包含这两个文件，无法描述其内部机制。

> _来源: `src/omnicompany/core/pipelines.py:L627-L635`_

## Public surface

当前代码片段中可见的对外接口仅为管线注册层面的两个延迟加载符号：

- `build_pm_pipeline` — 位于 `{_bw_pkg}.pipeline` 模块，通过 `_lazy` 包装注册为管线构建函数
- `build_pm_bindings` — 位于 `{_bw_pkg}.run` 模块，通过 `_lazy_fn` 包装注册为 bindings 构建函数
- 管线名（pipeline name）: `"voxelcraft.pm"` — 供 CLI 或 EventBus 路由使用的字符串标识

以上三个是在注册代码中真实出现的对外符号。`build_pm_pipeline` 和 `build_pm_bindings` 的参数签名与返回类型在当前可见片段中未出现，需读取 `{_bw_pkg}.pipeline` 和 `{_bw_pkg}.run` 两个文件才能完整描述。

> _来源: `src/omnicompany/core/pipelines.py:L627-L635`_

## Internal structure

当前可见材料（代码片段 + file_list）对 `voxelcraft.pm` 管线的内部子模块划分不足以描述。从注册代码可知该管线至少涉及以下两个子模块文件：

- `{_bw_pkg}.pipeline`：包含 `build_pm_pipeline`，负责管线图结构定义
- `{_bw_pkg}.run`：包含 `build_pm_bindings`，负责运行时 bindings 配置

`{_bw_pkg}` 展开后的实际包路径在当前片段中未明确给出（`_bw_pkg` 为变量，其值定义在代码片段之外）。完整内部结构需读取该包目录下的 `pipeline.py` 和 `run.py` 文件。

> _来源: `src/omnicompany/core/pipelines.py:L627-L635`_

## Files

当前事实材料中 code_anchors 仅提供了以下一条文件路径：

- `src/omnicompany/core/pipelines.py` — 核心管线注册文件，包含 `voxelcraft.pm` 的 `PipelineEntry` 注册逻辑（L627–L635），使用 `_lazy` 和 `_lazy_fn` 延迟绑定实际实现模块

`{_bw_pkg}.pipeline` 和 `{_bw_pkg}.run` 对应的具体文件路径未在 code_anchors 中出现，无法列出。

> _来源: `src/omnicompany/core/pipelines.py:L627-L635`_

## Related

与 `kb.arch.pipeline.voxelcraft.pm` 直接相关的 KB 条目：

- `kb.arch.pipeline.voxelcraft.design` — voxelcraft 策划管线，与 PM 管线同属 voxelcraft 域，策划产物（GDD）可能是 PM 管线 epic 输入的上游来源
- `kb.arch.pipeline.voxelcraft.engineering` — voxelcraft 工程管线，sprint goals 的执行落地方向
- `kb.arch.pipeline.voxelcraft.art` — voxelcraft 美术管线，同域并列管线
- `kb.arch.pipeline.voxelcraft.combat_test` — voxelcraft 战斗测试管线，同域并列管线
- `kb.arch.pipeline.voxelcraft.structures` — voxelcraft 建筑管线，同域并列管线
- `kb.arch.pipeline.voxelcraft.visual_assets` — voxelcraft 兵种外观管线，同域并列管线

> _来源: kb_context 列表_

## Known limitations

当前可见材料（代码片段 + seed description）中未出现任何 TODO/FIXME/XXX 注释。代码片段仅覆盖管线注册层，实际管线执行逻辑文件（`{_bw_pkg}.pipeline`、`{_bw_pkg}.run`）未包含在片段中，因此无法判断其实现完整性。

唯一可观察到的结构性事实是：该管线与同域其他管线（art、visual_assets）共享完全相同的 `default_db_dir`（`"data/voxelcraft"`）和 `default_max_steps`（`15`），暂不清楚这是否对 PM 场景的调度深度有所限制，需读取实现文件方可判断。

> _来源: `src/omnicompany/core/pipelines.py:L627-L652`_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 1 code anchors (1448 chars), 0 plan docs (0 chars), 25 kb refs
