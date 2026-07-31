# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:07:12Z
---
omnikb_type: karch
id: kb.arch.pipeline.voxelcraft.engineering
name: 'Pipeline: voxelcraft.engineering'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.voxelcraft.engineering
- domain.voxelcraft
- architecture
maturity: living
summary: voxelcraft 工程管线 — GDD → code → compile → debug loop
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L609-L634
---

# Pipeline: voxelcraft.engineering

> **id**: `kb.arch.pipeline.voxelcraft.engineering` · **type**: karch · **maturity**: living

## Why this exists

voxelcraft.engineering 管线是 Omnicompany 中专门为 voxelcraft 项目提供的工程开发闭环管线。其 seed description 将其定位为 "GDD → code → compile → debug loop"，即从游戏设计文档（GDD）出发，经历编码、编译、调试的完整迭代循环。这与 voxelcraft 域下其他管线（如 `voxelcraft.design` 负责策划、`voxelcraft.pm` 负责项目管理）形成职责分工：工程管线专注于将设计产物落地为可运行代码并完成质量验证。当前可见材料中没有 plan 文档，设计动机仅能从 seed description 和注册代码的 `description` 字段推断。

> _来源: seed_description, src/omnicompany/core/pipelines.py:L609-L617_

## How it works

从注册代码可以看到，`voxelcraft.engineering` 管线通过 `PipelineEntry` 注册到系统中，关键字段如下：

- `build_pipeline` 通过 `_lazy(f"{_bw_pkg}.pipeline", "build_engineering_pipeline")` 懒加载，指向 voxelcraft 包下 `pipeline` 模块中的 `build_engineering_pipeline` 函数。
- `build_bindings` 通过 `_lazy_fn(f"{_bw_pkg}.run", "build_engineering_bindings")` 懒加载，指向同包 `run` 模块中的 `build_engineering_bindings` 函数。
- `default_db_dir` 为 `"data/voxelcraft"`，与 voxelcraft 域其他管线共享同一数据目录。
- `default_max_steps` 为 `30`，与 `voxelcraft.combat_test` 相同，高于 `voxelcraft.pm`（15步）。

基于当前代码片段只能看到注册层的元数据配置，`build_engineering_pipeline` 和 `build_engineering_bindings` 的具体实现逻辑需读取 `{_bw_pkg}.pipeline` 和 `{_bw_pkg}.run` 两个模块的源文件。

> _来源: src/omnicompany/core/pipelines.py:L609-L617_

## Public surface

当前代码片段仅暴露注册层信息，可确认以下对外接口：

- **管线名**: `"voxelcraft.engineering"`（用于 CLI 或 API 调用时的管线标识符）
- **构建函数（懒引用）**: `build_engineering_pipeline`（位于 `{_bw_pkg}.pipeline` 模块）
- **绑定函数（懒引用）**: `build_engineering_bindings`（位于 `{_bw_pkg}.run` 模块）

这两个函数的实际签名和返回类型在当前代码片段中不可见，完整接口描述需读取对应源文件。

> _来源: src/omnicompany/core/pipelines.py:L609-L617_

## Internal structure

当前可见材料 (code_snippets) 不足以回答这一段。注册代码通过 `_lazy` 和 `_lazy_fn` 引用了 voxelcraft 包下的 `pipeline` 模块和 `run` 模块，但两者的内部结构（阶段划分、节点组成、Router 等）均未在代码片段中展示。补完此段需要阅读 `{_bw_pkg}/pipeline.py` 和 `{_bw_pkg}/run.py` 的源文件内容。

> _来源: src/omnicompany/core/pipelines.py:L609-L617_

## Files

当前 code_anchors 中仅提供了以下文件：

- `src/omnicompany/core/pipelines.py`（L609-L634）: 管线注册中心，包含 `voxelcraft.engineering` 的 `PipelineEntry` 注册逻辑，通过 `_lazy` / `_lazy_fn` 指向具体实现模块。

voxelcraft 包下的 `pipeline` 模块和 `run` 模块路径（`{_bw_pkg}.pipeline`、`{_bw_pkg}.run`）在代码中被引用但未出现在 code_anchors 中，无法列出其确切路径。

> _来源: src/omnicompany/core/pipelines.py:L609-L617_

## Related

与本管线直接相关的 KB 条目：

- `kb.arch.pipeline.voxelcraft.design` — 策划管线，产出 GDD，是工程管线的上游输入来源
- `kb.arch.pipeline.voxelcraft.combat_test` — 战斗测试管线，可能是工程管线 compile/debug 产物的下游验证环节
- `kb.arch.pipeline.voxelcraft.pm` — PM 管线，提供 sprint 目标和排期，与工程管线在任务驱动层面关联
- `kb.arch.pipeline.debug` — 通用调试管线，工程管线的 debug loop 可能复用或参考此管线的机制
- `kb.arch.pipeline.sw_implement` — 通用独立实施管线，工程管线的 code 阶段在概念上与此类似
- `kb.arch.pipeline.sw_tdd` — TDD 执行管线，与工程管线的 compile/debug 阶段在职责上有重叠

> _来源: kb_context_

## Known limitations

从当前可见的注册代码和 seed description 中，能观察到以下局限：

- 代码片段中**没有出现任何 TODO / FIXME / XXX 注释**，无法从注释层面判断已知缺陷。
- `build_engineering_pipeline` 和 `build_engineering_bindings` 通过懒加载引用，意味着管线的实际阶段定义、错误路由、节点实现均不在当前可见范围内，存在大面积盲区。
- `default_max_steps=30` 是硬编码默认值，是否适合所有工程任务规模不可从当前代码判断。
- seed description 将管线描述为 "GDD → code → compile → debug loop"，但循环终止条件、最大迭代次数策略等细节在可见材料中均未体现。

> _来源: src/omnicompany/core/pipelines.py:L609-L617, seed_description_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 1 code anchors (1482 chars), 0 plan docs (0 chars), 25 kb refs
