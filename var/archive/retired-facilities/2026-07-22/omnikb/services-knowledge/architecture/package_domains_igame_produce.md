# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:23:19Z
---
omnikb_type: karch
id: kb.arch.package.domains_demogame_produce
name: 'Package: packages/domains/demogame/produce'
tags:
- topic.package
- layer.domains
- domain.produce
- architecture
maturity: living
summary: 'demogame-produce: 配表生产管线 — 从学习产物到 P4 changelist + Lua 导出。'
scope: omnicompany
code_anchors:
- src/omnicompany/packages/domains/demogame/produce/__init__.py
- src/omnicompany/packages/domains/demogame/produce/pipeline.py
- src/omnicompany/packages/domains/demogame/produce/routers.py
- src/omnicompany/packages/domains/demogame/produce/run.py
- src/omnicompany/packages/domains/demogame/produce/formats.py
---

# Package: packages/domains/demogame/produce

> **id**: `kb.arch.package.domains_demogame_produce` · **type**: karch · **maturity**: living

## Why this exists

demogame-produce 包承担从"学习产物"到游戏客户端可用配表的完整生产责任。其核心场景是：策划在collab platform排期表中确定某版本需要上线的英雄列表后，需要将这些信息写入 Excel 配表（XLSM 格式）、触发 Excel 公式自动计算、再通过 DLL 工具导出 CSV 与 Lua 文件，最终提交至 P4（Perforce）版本库形成 changelist。这一流程此前依赖人工操作多个工具，demogame-produce 将其串联成可追踪的自动化管线。

seed description 明确概括了该包的定位："配表生产管线 — 从学习产物到 P4 changelist + Lua 导出"。"学习产物"指 demogame-learn 阶段产出的字段语义与信息源描述文件（`data/domains/demogame/learned/<table>.json`），produce 管线以这些文件为输入依据，而非在代码中硬编码字段语义，从而实现配置与逻辑分离。

> _来源: `src/omnicompany/packages/domains/demogame/produce/__init__.py`, seed_description_

## How it works

管线由 `pipeline.py` 中的 `build_pipeline()` 函数定义拓扑，共 6 个节点依次串联：

1. **`mi_resolver`** (`MIResolverRouter`)：接收 `demogame.produce.request`（含 `table_name` + `target_version`），从collab platform排期表解析目标版本的英雄列表，输出 `demogame.produce.minimum_inputs`。验证器类型为 `ValidatorKind.SOFT`，失败时 `RouteAction.HALT`。
2. **`learned_loader`** (`LearnedArtifactLoaderRouter`)：读取 `data/domains/demogame/learned/<table>.json`，加载 MI 字段清单、语义与发现的外部信息源，输出 `demogame.produce.learned_loaded`。验证器为 `ValidatorKind.HARD`。
3. **`mi_source_resolver`** (`MiSourceResolverRouter`)：按字段的 `source_hint` 描述为每个英雄拉取实际值，不符合条件的英雄被标记 skip（`__skip_reasons__`），输出 `demogame.produce.mi_resolved`。验证器为 `ValidatorKind.HARD`。
4. **`xlsm_writer`** (`XlsmWriterRouter`)：将 minimum inputs 写入 XLSM，让 Excel 自身计算公式，再读回所有字段的实际行值，输出 `demogame.produce.xlsm_written`。节点成熟度为 `NodeMaturity.CRYSTALLIZED`，是管线中唯一已结晶的节点。
5. **`dll_exporter`** (`DllExporterRouter`)：调用 `xlsx2csv.dll` 将 XLSM 导出为 CSV 文件并执行 P4 checkout，输出 `demogame.produce.csv_exported`。
6. **`lua_exporter`** (`LuaExporterRouter`)：在 CSV 导出完成后进一步生成 Lua 文件，输出最终的 `demogame.produce.done`。

核心设计原则写在 `routers.py` 文件头注释中：永远基于 XLSM 修改而非直接生成 CSV 字符串，Excel 公式逻辑由 Excel 自身执行而不在代码里复现。

`routers.py` 中硬编码了若干路径常量：`_P4_TOOLS`、`_P4_ROOT`、`_EXCEL_DIR`、`_CSV_DIR`、`_LUA_DIR`、`_DLL_PATH`、`_demogame_LEARN`，均指向 `D:/P4/main` 或 `e:/WindowsWorkspace/demogame-learn` 等本地绝对路径。`_XLSM_TABLE_REGISTRY` 字典注册了各表的 XLSM 文件路径、sheet 名及对应的 CSV 文件名，目前包含 `CostGroup`、`Tavern` 系列（含 `TavernPool`/`TavernUpLimit`/`TavernProbDes`/`TavernHeroTimeline`/`TavernHeroLines`/`TavernStarGazer`）、`UnitTime` 系列、`Schedule` 系列以及 `ImpulseGift`（含 family 导出）等条目。

基于当前代码片段，`MIResolverRouter.run()` 的方法体在摘录处被截断，`DllExporterRouter` 与 `LuaExporterRouter` 的完整实现未见于代码片段，完整机制需读 `routers.py` 全文。

> _来源: `src/omnicompany/packages/domains/demogame/produce/pipeline.py`, `src/omnicompany/packages/domains/demogame/produce/routers.py`, `src/omnicompany/packages/domains/demogame/produce/run.py`_

## Public surface

该包对外暴露的接口分为三类：

**Format id（数据契约）**，定义于 `formats.py`：
- `demogame.produce.request` — 生产请求入口
- `demogame.produce.minimum_inputs` — MI 解析结果
- `demogame.produce.learned_loaded` — 已加载学习产物
- `demogame.produce.mi_resolved` — MI 字段解析结果（含 `__skip_reasons__`）
- `demogame.produce.xlsm_written` — XLSM 写入 + Excel 公式计算结果
- `demogame.produce.csv_exported` — DLL 导出 CSV 结果
- `demogame.produce.done` — 生产完成报告（含 changelist 号、CSV/Lua 文件、行数统计）

**Router 类**，定义于 `routers.py`，由 `run.py` 的 `build_bindings()` 对外暴露：
- `MIResolverRouter`
- `LearnedArtifactLoaderRouter`
- `MiSourceResolverRouter`
- `XlsmWriterRouter`
- `DllExporterRouter`
- `LuaExporterRouter`

**绑定函数**，定义于 `run.py`：
- `build_bindings(input_dict)` — 返回节点 id 到 Router 实例的映射字典，供管线运行时使用

**格式注册函数**，定义于 `formats.py`：
- `register_formats(registry: FormatRegistry)` — 将所有 Format 注册到传入的 `FormatRegistry` 实例

> _来源: `src/omnicompany/packages/domains/demogame/produce/formats.py`, `src/omnicompany/packages/domains/demogame/produce/run.py`, `src/omnicompany/packages/domains/demogame/produce/routers.py`_

## Internal structure

该包由 5 个源文件构成，职责划分清晰：

| 文件 | 职责 |
|------|------|
| `__init__.py` | 包入口，仅含模块 docstring |
| `pipeline.py` | 管线拓扑定义，使用 `PipelineSpec`/`PipelineNode`/`PipelineEdge`/`AnchorSpec` 等协议类型构建 6 节点 DAG |
| `routers.py` | 6 个 Router 类的实现，含路径常量与 `_XLSM_TABLE_REGISTRY` 表级配置注册表 |
| `formats.py` | 7 个 `Format` 对象的定义与 `register_formats()` 注册接口 |
| `run.py` | 绑定层，`build_bindings()` 将节点 id 映射到 Router 实例 |

`pipeline.py` 依赖 `omnicompany.protocol.pipeline`（`PipelineSpec`、`PipelineNode`、`PipelineEdge`、`NodeKind`、`NodeMaturity`）与 `omnicompany.protocol.anchor`（`AnchorSpec`、`ValidatorSpec`、`ValidatorKind`、`Route`、`RouteAction`、`VerdictKind`）。`routers.py` 依赖 `omnicompany.protocol.anchor`（`Verdict`、`VerdictKind`）与 `omnicompany.runtime.routing.router`（`Router`）。`formats.py` 依赖 `omnicompany.protocol.format`（`Format`、`FormatRegistry`）。

> _来源: `src/omnicompany/packages/domains/demogame/produce/pipeline.py`, `src/omnicompany/packages/domains/demogame/produce/routers.py`, `src/omnicompany/packages/domains/demogame/produce/formats.py`, `src/omnicompany/packages/domains/demogame/produce/run.py`, `src/omnicompany/packages/domains/demogame/produce/__init__.py`_

## Files

- `src/omnicompany/packages/domains/demogame/produce/__init__.py` — 包入口文件，仅含模块级 docstring，声明该包为"配表生产管线"。
- `src/omnicompany/packages/domains/demogame/produce/pipeline.py` — 管线拓扑定义，`build_pipeline()` 函数构建从 `mi_resolver` 到 `lua_exporter` 的 6 节点有向管线，指定每个节点的格式契约、验证器类型与路由规则。
- `src/omnicompany/packages/domains/demogame/produce/routers.py` — 所有 Router 类的实现主体，包含硬编码路径常量、`_XLSM_TABLE_REGISTRY` 配置注册表以及 `MIResolverRouter` 等 6 个 Router 类。
- `src/omnicompany/packages/domains/demogame/produce/formats.py` — 定义管线全部 7 个中间/终止数据格式（`Format` 对象），并提供 `register_formats()` 将其注册到 `FormatRegistry`。
- `src/omnicompany/packages/domains/demogame/produce/run.py` — 绑定层，`build_bindings()` 实例化所有 Router 并以节点 id 为键返回映射字典，供运行时调用。

> _来源: code_anchors（即上述各文件路径），`src/omnicompany/packages/domains/demogame/produce/__init__.py`, `src/omnicompany/packages/domains/demogame/produce/pipeline.py`, `src/omnicompany/packages/domains/demogame/produce/routers.py`, `src/omnicompany/packages/domains/demogame/produce/formats.py`, `src/omnicompany/packages/domains/demogame/produce/run.py`_

## Related

与本包最直接相关的 KB 条目是同属 demogame domain 的另一个包：

- `kb.arch.package.domains_demogame_unity_explore` — 同为 demogame domain 下的领域包，采用 LAP Pipeline 驱动，与 produce 包同处 `packages/domains/demogame/` 层级，代表该 domain 的另一条管线方向。

produce 管线在架构模式上与其他 domain 包具有相似性，可参考：

- `kb.arch.package.domains_voxelcraft` — 同为 domain 层包，包含多条子管线，展示了 domain 包的典型组织方式。
- `kb.arch.package.domains_software_engineering_lang_rewrite` — 同为 domain 层包，具有明确的输入格式 → 输出格式管线结构，可对比理解 Format 链的设计。

当前 KB 中没有专门描述 demogame-learn（produce 的上游依赖）或 P4/Perforce 集成层的条目，若需补写可考虑新增类型为 `karch` 的 demogame-learn 包条目。

> _来源: kb_context_

## Known limitations

基于当前可见代码，存在以下明确可观察的局限：

1. **路径硬编码**：`routers.py` 中 `_P4_ROOT`、`_EXCEL_DIR`、`_CSV_DIR`、`_LUA_DIR`、`_DLL_PATH`、`_demogame_LEARN` 均为 Windows 本地绝对路径（`D:/P4/main`、`e:/WindowsWorkspace/demogame-learn`），无配置化机制，无法在不同机器或 CI 环境直接运行。

2. **节点成熟度不均**：`pipeline.py` 中 `xlsm_writer` 节点成熟度为 `NodeMaturity.CRYSTALLIZED`，而 `mi_resolver`、`learned_loader`、`mi_source_resolver` 均为 `NodeMaturity.GROWING`，说明管线前段仍处于开发中。`dll_exporter` 与 `lua_exporter` 节点的成熟度在当前摘录中被截断，未见声明。

3. **Router 实现不完整**：`MIResolverRouter.run()` 方法体在代码摘录处被截断，`DllExporterRouter` 与 `LuaExporterRouter` 的完整实现未出现在可见代码片段中，无法判断其是否已实现。

4. **_XLSM_TABLE_REGISTRY 与语义分离不彻底**：注释说明"mi_fields / type_field / pk_fields 等语义信息全部来自 learned artifact"，注册表只保留拓扑级配置，但当前可见代码中这一分离边界的实际执行情况只能通过 `routers.py` 全文确认。

5. **`pipeline.py` 节点编号注释错误**：代码注释中第 5 个节点（DLL 导出）被标注为"# 3."，存在明显的注释编号错误（与实际节点顺序不符）。

> _来源: `src/omnicompany/packages/domains/demogame/produce/routers.py`, `src/omnicompany/packages/domains/demogame/produce/pipeline.py`_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 5 code anchors (15316 chars), 0 plan docs (0 chars), 25 kb refs
