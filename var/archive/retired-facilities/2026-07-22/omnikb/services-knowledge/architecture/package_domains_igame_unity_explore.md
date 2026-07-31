# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:24:15Z
---
omnikb_type: karch
id: kb.arch.package.domains_demogame_unity_explore
name: 'Package: packages/domains/demogame/unity_explore'
tags:
- topic.package
- layer.domains
- domain.unity_explore
- architecture
maturity: living
summary: Unity Exploration Environment — LAP Pipeline 驱动的 Unity 灰盒探索系统.
scope: omnicompany
code_anchors:
- src/omnicompany/packages/domains/demogame/unity_explore/__init__.py
- src/omnicompany/packages/domains/demogame/unity_explore/pipeline.py
---

# Package: packages/domains/demogame/unity_explore

> **id**: `kb.arch.package.domains_demogame_unity_explore` · **type**: karch · **maturity**: living

## Why this exists

Unity Exploration Environment (`unity_explore`) 是 `demogame` 域下的一个专用 LAP Pipeline 包，用于驱动 AI Agent 对 Unity 游戏运行环境进行灰盒自主探索。其 `__init__.py` 的模块文档说明，该包用 SQLiteBus 驱动的标准管线取代了旧有的硬编码 `play_agent.py` 循环——这一替换动机在 `pipeline.py` 的文件注释中有明确表述："It replaces the old hardcoded `play_agent.py` loops with a standard SQLiteBus-driven pipeline."

探索场景面向 Unity 客户端内部，Agent 可执行的动作类型包括 UI 点击、代码阅读、以及通过 GM（GameMaster）权限执行 Lua 脚本，探索结果以截图、UI 树、代码读取结果或 Lua 执行结果的形式返回。整体设计意图是将探索过程结构化为可复用的管线拓扑，使 Agent 的决策循环、工具调用和感知聚合均以 LAP 标准格式流转，从而具备可观测性和可扩展性。

当前可见材料中没有 plan 文档或相关实验条目，无法进一步还原设计动机的决策背景。

> _来源: src/omnicompany/packages/domains/demogame/unity_explore/\_\_init\_\_.py, src/omnicompany/packages/domains/demogame/unity_explore/pipeline.py (文件头注释)_

## How it works

管线通过 `build_unity_explore_pipeline()` 函数构建，返回一个 `PipelineSpec`，id 为 `"unity-explore-loop"`，名称为 `"Unity 自动探索管线"`，由三个节点构成一个循环拓扑：

- **`context` 节点**（`NodeKind.TRANSFORMER`）：使用 `TransformerSpec`（id `"unity-context-router"`，`TransformMethod.RULE`），将 `"unity-explore-obs"` 格式转换为 `"unity-joint-perception"` 格式，即把 Unity 工具的执行结果和错误信息聚合为包含代码上下文的联合感知状态。
- **`llm` 节点**（`NodeKind.ANCHOR`）：使用 `AnchorSpec`（id `"unity-llm-router"`），接收 `"unity-joint-perception"`，输出 `"unity-explore-intent"`，配备 `ValidatorKind.SOFT` 验证器。当 verdict 为 `PASS` 时执行 `RouteAction.EMIT`（探索结束）；为 `FAIL` 时路由至 `"tool"` 节点（继续执行工具）。
- **`tool` 节点**（`NodeKind.ANCHOR`）：使用 `AnchorSpec`（id `"unity-tool-router"`），接收 `"unity-explore-intent"`，输出 `"unity-explore-obs"`，配备 `ValidatorKind.HARD` 验证器（id `"unity-executor"`），负责分发执行 Unity Tools 并通过 RewardTracker 计算得分。无论成功或失败，均路由回 `"context"` 节点，失败时错误进入感知聚合并扣分。

三种 Format 通过 `register_unity_formats(registry)` 向注册表注册，注册前会调用 `registry.is_registered(fmt.id)` 检查去重。

基于当前代码片段只能看到管线声明（`PipelineSpec`）的拓扑定义，RewardTracker 的具体实现、`unity-executor` 验证器实体、以及各 Anchor 的实际 LLM/Tool 调用逻辑需读其他文件（当前代码片段未提供）。

> _来源: src/omnicompany/packages/domains/demogame/unity_explore/pipeline.py_

## Public surface

模块通过 `__all__` 对外暴露的公开接口只有一项：

| 名称 | 类型 | 说明 |
|------|------|------|
| `build_unity_explore_pipeline` | 函数 | 构建并返回 `PipelineSpec`（id: `"unity-explore-loop"`） |

此外，`pipeline.py` 中还定义了以下内容，但未列入 `__all__`，属于模块内部可调用但非正式导出的接口：

- `register_unity_formats(registry)` — 向 Format 注册表注册三个 Unity 专用格式

注册的 Format id：
- `"unity-explore-intent"`
- `"unity-explore-obs"`
- `"unity-joint-perception"`

`__init__.py` 的 docstring 还提及 `run_single_exploration` 和 `run_evolution_loop` 为"核心导出"，但当前代码片段中这两个函数**未出现在 `__all__`** 中，也未见其导入，当前可见材料不足以确认其实现位置。

> _来源: src/omnicompany/packages/domains/demogame/unity_explore/\_\_init\_\_.py, src/omnicompany/packages/domains/demogame/unity_explore/pipeline.py_

## Internal structure

从现有代码片段和文件清单可以确认的子模块划分如下：

- **`__init__.py`**：包入口，负责从 `pipeline` 子模块导入 `build_unity_explore_pipeline` 并声明 `__all__`。
- **`pipeline.py`**：核心模块，定义管线拓扑（`build_unity_explore_pipeline`）和 Format 注册逻辑（`register_unity_formats`）。

`pipeline.py` 依赖以下来自上层协议层的类型：
- `omnicompany.protocol.anchor`：`AnchorSpec`, `Route`, `RouteAction`, `TransformerSpec`, `TransformMethod`, `ValidatorKind`, `ValidatorSpec`, `VerdictKind`
- `omnicompany.protocol.format`：`Format`
- `omnicompany.protocol.pipeline`：`NodeKind`, `PipelineEdge`, `PipelineNode`, `PipelineSpec`

`__init__.py` 的 docstring 中提及 `run_single_exploration` 和 `run_evolution_loop`，暗示可能存在其他子模块（如 `runner.py` 或 `loop.py`），但当前可见文件清单仅包含上述两个文件，无法确认。

> _来源: src/omnicompany/packages/domains/demogame/unity_explore/\_\_init\_\_.py, src/omnicompany/packages/domains/demogame/unity_explore/pipeline.py_

## Files

- `src/omnicompany/packages/domains/demogame/unity_explore/__init__.py` — 包入口，声明公开导出（`build_unity_explore_pipeline`）并在 docstring 中列出模块级核心函数。
- `src/omnicompany/packages/domains/demogame/unity_explore/pipeline.py` — 核心实现文件，定义 Unity 探索专用 Format（`register_unity_formats`）和完整的 LAP `PipelineSpec` 拓扑（`build_unity_explore_pipeline`），包含 context、llm、tool 三个节点的路由声明。

> _来源: src/omnicompany/packages/domains/demogame/unity_explore/\_\_init\_\_.py, src/omnicompany/packages/domains/demogame/unity_explore/pipeline.py_

## Related

与本包最直接相关的已有 KB 条目：

- `kb.arch.package.domains_demogame_produce` — 同属 `demogame` 域的另一个管线包（配表生产管线），共享域上下文。
- `kb.arch.package.domains_voxelcraft_mechanics_evolver` — 同类型的自动生成/进化管线，结构上与 `unity_explore` 的进化循环设计有参考关联。
- `kb.arch.package.domains_voxelcraft` — Minecraft Game Studio Pipeline，作为游戏域管线的参照结构。
- `kb.arch.package.services_lap_auditor` — LAP 规范审计工作流，与本包使用的 LAP Pipeline 规范直接相关。
- `kb.arch.package.services_pipeline_ci` — 管线质量 CI 扫描器，适用于对本包管线声明进行合规扫描。

当前 KB 中没有专门描述 LAP Protocol 层（`omnicompany.protocol.anchor`、`omnicompany.protocol.pipeline`）的条目，补写这些条目将有助于完整理解本包的依赖关系。

> _来源: kb_context 列表_

## Known limitations

基于当前可见代码，可观察到以下明确的未实现或存疑区域：

1. **`run_single_exploration` 和 `run_evolution_loop` 未导出**：`__init__.py` 的 docstring 将这两个函数列为"核心导出"，但 `__all__` 中只有 `build_unity_explore_pipeline`，且代码中未见这两个函数的导入语句。它们的实现文件在当前可见材料中不存在。

2. **`pipeline.py` 代码片段截断**：`PipelineSpec` 的 `edges` 参数列表在第 132 行处被截断，`PipelineEdge(source="llm", target="tool", condition=VerdictKind.` 之后的内容不可见，无法确认完整的边定义。

3. **`register_unity_formats` 未在管线构建中调用**：`build_unity_explore_pipeline()` 函数体中未见对 `register_unity_formats` 的调用，Format 注册与管线构建是分离的，调用方需要自行负责注册时机，这可能是潜在的使用陷阱。

4. **RewardTracker 未见实现**：`tool` 节点的 validator 描述中提到"通过 RewardTracker 计算得分"，但当前代码片段中未见 `RewardTracker` 的任何定义或导入。

> _来源: src/omnicompany/packages/domains/demogame/unity_explore/\_\_init\_\_.py, src/omnicompany/packages/domains/demogame/unity_explore/pipeline.py_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 2 code anchors (5686 chars), 0 plan docs (0 chars), 25 kb refs
