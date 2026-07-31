# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:32:55Z
---
omnikb_type: karch
id: kb.arch.package.services_guardian
name: 'Package: packages/services/guardian'
tags:
- topic.package
- layer.services
- domain.guardian
- architecture
maturity: living
summary: guardian — 守护检查管线
scope: omnicompany
code_anchors:
- src/omnicompany/packages/services/guardian/__init__.py
- src/omnicompany/packages/services/guardian/pipeline.py
- src/omnicompany/packages/services/guardian/routers.py
- src/omnicompany/packages/services/guardian/run.py
- src/omnicompany/packages/services/guardian/formats.py
---

# Package: packages/services/guardian

> **id**: `kb.arch.package.services_guardian` · **type**: karch · **maturity**: living

## Why this exists

Guardian 的存在是为了对 Omnicompany 项目自身的工作区进行持续的"守护检查"。根据 `__init__.py` 中的模块文档，它承担三项职责：文件系统洁净度扫描、架构规范审计、以及健康评分报告。其设计动机是项目在长期 agent 运行过程中会产生散落文件（如类型命名临时文件、非法位置写入），同时 `src/` 下的架构规范（LAP 约定、Router 签名等）也可能随时间产生漂移，需要一个统一的审计入口。用法为命令行 `omni guardian [--fix]`，暗示其结果可能具有可修复性，但 `--fix` 分支的具体实现在当前可见片段中尚未出现。

> _来源: src/omnicompany/packages/services/guardian/\_\_init\_\_.py (seed_description)_

## How it works

管线由 `pipeline.py` 中的 `build_pipeline()` 函数构建，返回一个 `PipelineSpec`，id 为 `guardian-pipeline`，入口节点为 `fs_scanner`。拓扑为线性三节点串联：

- **`fs_scanner`**：`PipelineNode`，kind 为 `NodeKind.TRANSFORMER`，关联 `TransformerSpec`（id `guardian-fs-scan`，name `FsScannerRouter`），以 `TransformMethod.RULE` 方式将 `guardian.check-request` 转为 `guardian.fs-report`，maturity 为 `NodeMaturity.CRYSTALLIZED`。
- **`arch_auditor`**：同为 `TRANSFORMER`，id `guardian-arch-audit`，name `ArchAuditorRouter`，将 `guardian.fs-report` 转为 `guardian.arch-report`，重点检查 DEPRECATED 模块、空 `__init__`、以及 Router 是否符合 LAP 约定（`INPUT_KEYS` / `run` 签名 / docstring）。
- **`health_reporter`**：`PipelineNode`，kind 为 `NodeKind.ANCHOR`，关联 `AnchorSpec`（id `guardian-health-report`，name `HealthReporterRouter`），使用 `ValidatorKind.SOFT` 验证器（即 LLM 评估），将 `guardian.arch-report` 聚合为 `guardian.health-report`，无论 `VerdictKind.PASS` 还是 `VerdictKind.FAIL` 均执行 `RouteAction.EMIT`。

`FsScannerRouter.run()` 方法依次调用四个私有方法：`_check_root_entries`、`_check_data_dir`（片段被截断）、`_check_type_named_files`、`_check_drive_roots`，汇总 `issues` 列表后以 `VerdictKind.PASS` 返回 `Verdict`。`build_bindings()` 函数（在 `run.py`）将三个节点 id 绑定到对应 Router 实例，`HealthReporterRouter` 可接受 `model` 参数。

基于当前代码片段，`_check_data_dir`、`_check_type_named_files`、`_check_drive_roots` 的具体实现以及 `ArchAuditorRouter` 和 `HealthReporterRouter` 的完整实现均被截断，需读取 `routers.py` 完整内容。

> _来源: src/omnicompany/packages/services/guardian/pipeline.py, src/omnicompany/packages/services/guardian/routers.py, src/omnicompany/packages/services/guardian/run.py_

## Public surface

以下是代码中真实出现的对外接口：

**函数**
- `build_pipeline() -> PipelineSpec`（`pipeline.py`）：构建并返回完整的管线拓扑描述对象。
- `build_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]`（`run.py`）：构建节点 id 到 Router 实例的绑定字典。

**Router 类**（`routers.py`，均继承自 `Router`）
- `FsScannerRouter`：`INPUT_KEYS = ["project_root"]`，`FORMAT_IN = "guardian.check-request"`，`FORMAT_OUT = "guardian.fs-report"`
- `ArchAuditorRouter`：在 `build_bindings` 中被实例化，接口细节因代码截断未见全貌。
- `HealthReporterRouter`：在 `build_bindings` 中接受 `model` 参数，继承 `AgentNodeLoop`（据 `routers.py` 文档注释）。

**Format id**（`formats.py`）
- `guardian.check-request`（`FORMAT_CHECK_REQUEST`）
- `guardian.fs-report`（`FORMAT_FS_REPORT`）
- `guardian.arch-report`（`FORMAT_ARCH_REPORT`）
- `guardian.node-report`（`FORMAT_NODE_REPORT`）
- `guardian.health-report`（`FORMAT_HEALTH_REPORT`）

> _来源: src/omnicompany/packages/services/guardian/pipeline.py, src/omnicompany/packages/services/guardian/routers.py, src/omnicompany/packages/services/guardian/run.py, src/omnicompany/packages/services/guardian/formats.py_

## Internal structure

包内文件按职责划分为四个模块：

- **`__init__.py`**：包入口，提供模块文档与用法说明，无实质逻辑。
- **`pipeline.py`**：管线拓扑定义层，使用 `PipelineSpec` / `PipelineNode` / `PipelineEdge` 等协议对象声明三节点有向图，不含运行时逻辑。
- **`routers.py`**：运行时实现层，包含三个 Router 实现类。`FsScannerRouter` 和 `ArchAuditorRouter` 为 HARD 节点（纯规则），`HealthReporterRouter` 为 `AgentNodeLoop` 子类（LLM 驱动）。该文件 import 了 `omnicompany.runtime.agent.agent_loop_config`、`omnicompany.runtime.agent.agent_loop_tools`、`omnicompany.runtime.agent.agent_node_loop`、`omnicompany.runtime.routing.router` 等运行时模块，以及 `omnicompany.protocol.anchor` 中的 `Verdict` 和 `VerdictKind`。
- **`formats.py`**：格式常量层，集中定义五个 Format id 字符串常量，供管线和 Router 共同引用。
- **`run.py`**：绑定层，`build_bindings()` 将管线节点 id 与 Router 实例对应，作为管线执行的统一入口。

> _来源: src/omnicompany/packages/services/guardian/pipeline.py, src/omnicompany/packages/services/guardian/routers.py, src/omnicompany/packages/services/guardian/run.py, src/omnicompany/packages/services/guardian/formats.py, src/omnicompany/packages/services/guardian/\_\_init\_\_.py_

## Files

- `src/omnicompany/packages/services/guardian/__init__.py`：包声明文件，包含模块文档（三大职责说明）与命令行用法提示。
- `src/omnicompany/packages/services/guardian/pipeline.py`：管线拓扑定义，通过 `build_pipeline()` 构建 `guardian-pipeline` 的 `PipelineSpec`，声明三节点线性拓扑及边关系。
- `src/omnicompany/packages/services/guardian/routers.py`：三个 Router 的运行时实现：`FsScannerRouter`（文件系统扫描）、`ArchAuditorRouter`（架构规范审计）、`HealthReporterRouter`（LLM 健康评分），以及扫描白名单常量 `_ALLOWED_ROOT_ENTRIES`、`_ALLOWED_DATA_DIRS`、`_DRIVE_ROOTS_TO_CHECK` 等。
- `src/omnicompany/packages/services/guardian/run.py`：提供 `build_bindings()` 便捷函数，将节点 id 映射到对应 Router 实例，支持 `project_root` 和 `model` 参数注入。
- `src/omnicompany/packages/services/guardian/formats.py`：集中定义五个 Format id 常量（`FORMAT_CHECK_REQUEST`、`FORMAT_FS_REPORT`、`FORMAT_ARCH_REPORT`、`FORMAT_NODE_REPORT`、`FORMAT_HEALTH_REPORT`），作为管线各阶段数据格式的命名锚点。

> _来源: src/omnicompany/packages/services/guardian/\_\_init\_\_.py, src/omnicompany/packages/services/guardian/pipeline.py, src/omnicompany/packages/services/guardian/routers.py, src/omnicompany/packages/services/guardian/run.py, src/omnicompany/packages/services/guardian/formats.py_

## Related

以下 KB 条目与 guardian 有直接或间接关联：

- `kb.arch.pipeline.guardian`：guardian 管线的专属 pipeline 条目，与本 package 条目互为补充。
- `kb.arch.package.services_lap_auditor`：同为 services 层的规范审计类工作流，`arch_auditor` 节点的审计目标（Router 是否符合 LAP 约定）与此条目高度重叠。
- `kb.arch.package.services_pipeline_ci`：管线质量 CI 扫描器，与 guardian 的架构规范审计职能在目标上相近，均关注管线/代码质量。
- `kb.arch.package.services_cleanup_bot`：环境副作用清理工作流，与 guardian 的文件系统洁净度扫描在关注域（工作区污染）上相关，可能存在协作关系。

> _来源: kb.arch.pipeline.guardian, kb.arch.package.services_lap_auditor, kb.arch.package.services_pipeline_ci, kb.arch.package.services_cleanup_bot_

## Known limitations

根据当前可见代码，可以确认以下局限：

1. **`_check_data_dir` 实现截断**：`routers.py` 代码片段在 `_check_data_dir` 方法体处被截断（第 127 行后不可见），`_check_type_named_files` 和 `_check_drive_roots` 的实现同样不可见。
2. **`ArchAuditorRouter` 与 `HealthReporterRouter` 实现不可见**：当前代码片段仅包含 `FsScannerRouter` 的部分实现，另外两个 Router 的完整逻辑均未出现。
3. **`FORMAT_NODE_REPORT`（`guardian.node-report`）在管线中未使用**：`formats.py` 定义了 `FORMAT_NODE_REPORT`，但 `pipeline.py` 的三节点拓扑中无对应节点（注释中也提及 `lap_node_inspector` 但管线未包含该节点），该 format 可能是规划中但尚未接入的扩展点。
4. **`_DEFAULT_PROJECT_ROOT` 硬编码 Windows 路径**：`routers.py` 中 `_DEFAULT_PROJECT_ROOT = Path("e:/WindowsWorkspace/omnicompany")` 为硬编码本地路径，在其他环境下需通过 `input_dict` 显式传入 `project_root`。
5. **`--fix` 模式未在可见代码中实现**：`__init__.py` 的用法说明提及 `omni guardian [--fix]`，但当前代码片段中未见对应的修复逻辑。

> _来源: src/omnicompany/packages/services/guardian/routers.py, src/omnicompany/packages/services/guardian/formats.py, src/omnicompany/packages/services/guardian/\_\_init\_\_.py, src/omnicompany/packages/services/guardian/pipeline.py_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 5 code anchors (11432 chars), 0 plan docs (0 chars), 25 kb refs
