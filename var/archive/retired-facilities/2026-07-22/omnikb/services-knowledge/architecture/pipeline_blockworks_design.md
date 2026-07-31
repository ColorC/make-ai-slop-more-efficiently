# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:06:36Z
---
omnikb_type: karch
id: kb.arch.pipeline.voxelcraft.design
name: 'Pipeline: voxelcraft.design'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.voxelcraft.design
- domain.voxelcraft
- architecture
maturity: living
summary: voxelcraft 策划管线 — vision → GDD → balance → review
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L600-L625
---

# Pipeline: voxelcraft.design

> **id**: `kb.arch.pipeline.voxelcraft.design` · **type**: karch · **maturity**: living

## Why this exists

voxelcraft 策划管线 (`voxelcraft.design`) 在 Omnicompany 的管线体系中负责覆盖游戏设计的前期策划流程，其 seed description 明确描述了四个阶段：vision → GDD → balance → review。这条管线属于 `voxelcraft` 域，是 voxelcraft 多管线族群的策划入口，与工程管线 (`voxelcraft.engineering`)、战斗测试管线 (`voxelcraft.combat_test`) 等配合，构成完整的 voxelcraft 游戏开发自动化闭环。策划管线的职责是将初始创意愿景转化为结构化的游戏设计文档 (GDD)，并在生成过程中进行平衡性分析和评审，形成可供后续工程管线消费的设计产物。当前可见材料未提供该管线的 plan 文档或 experiment 关联记录，动机描述仅来自 seed description 及注册时的 description 字段。

> _来源: seed_description, src/omnicompany/core/pipelines.py:L600-L608_

## How it works

从代码片段可见，`voxelcraft.design` 管线通过 `PipelineEntry` 对象注册到 Omnicompany 管线系统。注册时提供了两个懒加载构造器：

- `build_pipeline`：由 `_lazy(f"{_bw_pkg}.pipeline", "build_design_pipeline")` 延迟解析，指向 `{_bw_pkg}.pipeline` 模块中的 `build_design_pipeline` 函数。
- `build_bindings`：由 `_lazy_fn(f"{_bw_pkg}.run", "build_design_bindings")` 延迟解析，指向 `{_bw_pkg}.run` 模块中的 `build_design_bindings` 函数。

其余注册参数包括：`domain="voxelcraft"`、`default_db_dir="data/voxelcraft"`、`default_max_steps=20`（相比工程和战斗测试管线的 30 步，策划管线步数上限更低）。

基于当前代码片段只能看到管线的注册入口和懒加载指向，`build_design_pipeline` 与 `build_design_bindings` 的具体实现（即 vision→GDD→balance→review 各阶段的节点定义和数据流转）需要读取 `{_bw_pkg}.pipeline` 和 `{_bw_pkg}.run` 对应的源文件才能完整描述。

> _来源: src/omnicompany/core/pipelines.py:L600-L608_

## Public surface

当前代码片段中可见的对外接口如下：

- **管线名称**：`"voxelcraft.design"` — 用于 CLI 或 EventBus 调用时标识该管线的字符串 ID。
- **`build_design_pipeline`**：位于 `{_bw_pkg}.pipeline` 模块，通过 `_lazy` 懒加载暴露为 `PipelineEntry.build_pipeline` 字段的构造函数。
- **`build_design_bindings`**：位于 `{_bw_pkg}.run` 模块，通过 `_lazy_fn` 懒加载暴露为 `PipelineEntry.build_bindings` 字段的构造函数。

以上三项是代码片段中可以确认真实存在的公开接口。`_bw_pkg` 变量的具体包路径在当前片段中未展开，需读取 `pipelines.py` 更上文位置才能确认完整模块路径。

> _来源: src/omnicompany/core/pipelines.py:L600-L608_

## Internal structure

当前可见材料 (code/plan/kb context) 不足以回答这一段。补完此段需要阅读：`{_bw_pkg}.pipeline` 对应的完整源文件（包含 `build_design_pipeline` 的节点定义）以及 `{_bw_pkg}.run` 对应的源文件（包含 `build_design_bindings` 的绑定逻辑），同时需要确认 `_bw_pkg` 的实际包路径（需读取 `src/omnicompany/core/pipelines.py` L600 以上的变量定义段）。

## Files

当前可见材料中 code_anchors 仅提供了 `src/omnicompany/core/pipelines.py` 的片段（L600-L625），该文件负责集中注册所有管线，包括 `voxelcraft.design` 的 `PipelineEntry`。`{_bw_pkg}.pipeline` 和 `{_bw_pkg}.run` 作为实际实现文件被懒加载引用，但其完整路径未在当前 code_anchors 中出现，无法列出。

- `src/omnicompany/core/pipelines.py` — 管线注册中心，通过 `PipelineEntry` 和 `_lazy`/`_lazy_fn` 机制注册包括 `voxelcraft.design` 在内的所有管线。

> _来源: src/omnicompany/core/pipelines.py:L600-L608_

## Related

与本条目直接相关的 KB 条目如下：

- `kb.arch.pipeline.voxelcraft.engineering` — voxelcraft 工程管线，策划管线的下游，消费 GDD 产物进行代码生成。
- `kb.arch.pipeline.voxelcraft.combat_test` — voxelcraft 战斗测试管线，与策划管线共享 `voxelcraft` 域和 `data/voxelcraft` 数据目录。
- `kb.arch.pipeline.voxelcraft.pm` — voxelcraft PM 管线，负责 epic → sprint goals → schedule DAG，与策划管线在项目管理层面存在潜在协作关系。
- `kb.arch.pipeline.voxelcraft.art` — voxelcraft 美术管线，同属 `voxelcraft` 域。
- `kb.arch.pipeline.voxelcraft.structures` — voxelcraft 建筑管线，同属 `voxelcraft` 域。
- `kb.arch.pipeline.voxelcraft.visual_assets` — voxelcraft 兵种外观管线，同属 `voxelcraft` 域。

> _来源: kb.arch.pipeline.voxelcraft.engineering, kb.arch.pipeline.voxelcraft.combat_test, kb.arch.pipeline.voxelcraft.pm, kb.arch.pipeline.voxelcraft.art, kb.arch.pipeline.voxelcraft.structures, kb.arch.pipeline.voxelcraft.visual_assets_

## Known limitations

当前代码片段及 seed description 中未出现任何 TODO、FIXME、XXX 注释，也未明确标注已知局限。可以客观观察到的情况：

- `build_design_pipeline` 和 `build_design_bindings` 的具体实现未出现在当前可见片段中，无法判断 vision→GDD→balance→review 各阶段是否均已实现。
- `default_max_steps=20` 相比其他 voxelcraft 管线（30 步）更低，这是代码中可见的配置差异，但无材料说明这是否是一个已知约束或待调整项。
- `_bw_pkg` 的实际路径未在当前片段中展开，限制了对实现文件位置的确认。

> _来源: src/omnicompany/core/pipelines.py:L600-L608, seed_description_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 1 code anchors (1490 chars), 0 plan docs (0 chars), 25 kb refs
