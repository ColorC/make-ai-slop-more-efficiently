# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:35:56Z
---
omnikb_type: karch
id: kb.arch.package.services_pipeline_ci
name: 'Package: packages/services/pipeline_ci'
tags:
- topic.package
- layer.services
- domain.pipeline_ci
- architecture
maturity: living
summary: pipeline_ci — 管线质量 CI 扫描器
scope: omnicompany
code_anchors:
- src/omnicompany/packages/services/pipeline_ci/__init__.py
- src/omnicompany/packages/services/pipeline_ci/pipeline.py
- src/omnicompany/packages/services/pipeline_ci/routers.py
- src/omnicompany/packages/services/pipeline_ci/run.py
- src/omnicompany/packages/services/pipeline_ci/formats.py
---

# Package: packages/services/pipeline_ci

> **id**: `kb.arch.package.services_pipeline_ci` · **type**: karch · **maturity**: living

## Why this exists

`pipeline_ci` 包作为 Omnicompany 的管线质量 CI 扫描器而存在。其核心职责在 `__init__.py` 的 docstring 中有明确描述：对 `packages/` 下所有域进行批量扫描，运行 `ErrorRouteAuditor` 和 `PipelineChecker` 两类检查器，聚合审计报告，并在发现 critical 级别问题时阻断 CI 流程。

这个包的存在回答了一个工程问题：随着 Omnicompany 中各业务域（domains）和服务（services）的管线数量不断增长，需要一个自动化的、纯确定性的质量门禁，以防止错误路由配置或类型不安全的管线被合入主干。它不依赖 LLM 推理，而是依靠静态规则审查，确保结果的可重复性。

当前可见材料中没有 plan 文档或关联的 KExperiment 条目，设计动机仅能从 seed description 和代码 docstring 推断至此。

> _来源: seed_description, `src/omnicompany/packages/services/pipeline_ci/__init__.py`_

## How it works

管线按三步线性流水线执行，每步对应一个 `PipelineNode`，均通过 `build_pipeline()` 函数在 `pipeline.py` 中声明：

1. **`domain_scanner`** 节点：类型为 `NodeKind.TRANSFORMER`，绑定 `TransformerSpec(id="pipeline-ci-domain-scanner")`，使用 `TransformMethod.RULE`。对应运行时实现是 `DomainScannerRouter`，它接收格式 `pipeline_ci.scan-request`，从 `project_root/src/omnicompany/packages/` 出发递归扫描，找出同时含有 `routers.py` 和 `pipeline.py` 的子目录（通过辅助函数 `_collect_domains` 递归处理嵌套包），读取文件内容后输出格式 `pipeline_ci.domains`。支持可选的 `domain_filter` 白名单过滤。

2. **`batch_auditor`** 节点：类型为 `NodeKind.TRANSFORMER`，绑定 `TransformerSpec(id="pipeline-ci-batch-auditor")`，同样是 `TransformMethod.RULE`。对应 `BatchAuditorRouter`，在 `run()` 中动态导入 `ErrorRouteAuditorRouter`（来自 `workflow_factory` 包）和 `PipelineChecker`，对每个域依次执行两项检查，聚合 issues 并统计 `critical_count` 和 `warning_count`，输出格式 `pipeline_ci.ci-report`。

3. **`ci_gate`** 节点：类型为 `NodeKind.ANCHOR`，绑定 `AnchorSpec(id="pipeline-ci-gate")`，内含 `ValidatorSpec(kind=ValidatorKind.HARD)`。`CIGateRouter` 判断 `critical_count == 0` 时输出 `VerdictKind.PASS`（`RouteAction.EMIT`），否则 `VerdictKind.FAIL`（`RouteAction.HALT`）阻断 CI。

三节点由两条 `PipelineEdge` 连接：`domain_scanner → batch_auditor → ci_gate`，入口为 `domain_scanner`。

基于当前代码片段，`BatchAuditorRouter.run()` 中对每个域执行检查的循环体在代码截断处结束，`ErrorRouteAuditorRouter` 和 `PipelineChecker` 的具体调用细节只能看到接口，完整机制需读 `src/omnicompany/packages/services/workflow_factory/routers.py` 和 `omnicompany/protocol/pipeline.py`。

> _来源: `src/omnicompany/packages/services/pipeline_ci/pipeline.py`, `src/omnicompany/packages/services/pipeline_ci/routers.py`, `src/omnicompany/packages/services/pipeline_ci/run.py`_

## Public surface

该模块对外暴露的接口如下：

**管线声明函数：**
- `build_pipeline() -> PipelineSpec`（`pipeline.py`）：返回 id 为 `"pipeline-ci"` 的 `PipelineSpec` 实例。

**Router 绑定函数：**
- `build_bindings(input_dict: dict | None = None) -> dict[str, Router]`（`run.py`）：返回三个 Router 的实例字典，键为节点 id 字符串。

**Router 类（`routers.py` 导出）：**
- `DomainScannerRouter`：`FORMAT_IN = "pipeline_ci.scan-request"`，`FORMAT_OUT = "pipeline_ci.domains"`
- `BatchAuditorRouter`：`FORMAT_IN = "pipeline_ci.domains"`，`FORMAT_OUT = "pipeline_ci.ci-report"`
- `CIGateRouter`（在 `run.py` 中被导入，但代码片段中 `routers.py` 未完整展示其类定义）

**Format id（`formats.py`）：**
- `"pipeline_ci.scan-request"`（`CIScanRequest`）
- `"pipeline_ci.domains"`（`CIDomains`）
- `"pipeline_ci.ci-report"`（`CIReport`）

**Format 注册函数：**
- `register_formats(registry: FormatRegistry) -> None`（`formats.py`）

> _来源: `src/omnicompany/packages/services/pipeline_ci/pipeline.py`, `src/omnicompany/packages/services/pipeline_ci/run.py`, `src/omnicompany/packages/services/pipeline_ci/routers.py`, `src/omnicompany/packages/services/pipeline_ci/formats.py`_

## Internal structure

该包内部划分为四个职责明确的模块，对应以下文件：

- **`pipeline.py`**：管线拓扑声明层，使用 `PipelineSpec` / `PipelineNode` / `PipelineEdge` 等协议类型构建 DAG，不含任何执行逻辑。
- **`routers.py`**：具体执行层，实现三个继承自 `Router` 的类（`DomainScannerRouter`、`BatchAuditorRouter`、`CIGateRouter`），以及辅助函数 `_collect_domains`。`BatchAuditorRouter` 在运行时动态导入外部依赖（`workflow_factory` 包的 `ErrorRouteAuditorRouter` 和协议层的 `PipelineChecker`），属于跨包调用。
- **`formats.py`**：数据格式注册层，定义 `FORMATS` 列表和 `register_formats()` 函数，三个 `Format` 对象之间存在 `parent` 继承关系（`scan-request → domains → ci-report`）。
- **`run.py`**：绑定层（bindings），通过 `build_bindings()` 将节点 id 字符串映射到 Router 实例，供运行时装配使用。

`__init__.py` 仅包含包级 docstring，不导出任何符号。各模块之间的导入方向为：`run.py` 导入 `routers.py`；`routers.py` 在运行时导入外部包；`pipeline.py` 和 `formats.py` 相互独立，只依赖协议层。

> _来源: `src/omnicompany/packages/services/pipeline_ci/pipeline.py`, `src/omnicompany/packages/services/pipeline_ci/routers.py`, `src/omnicompany/packages/services/pipeline_ci/run.py`, `src/omnicompany/packages/services/pipeline_ci/formats.py`, `src/omnicompany/packages/services/pipeline_ci/__init__.py`_

## Files

- `src/omnicompany/packages/services/pipeline_ci/__init__.py`：包入口，仅含包级 docstring，声明该包为"管线质量 CI 扫描器"，概述扫描流程和阻断行为。
- `src/omnicompany/packages/services/pipeline_ci/pipeline.py`：`PipelineSpec` 声明文件，通过 `build_pipeline()` 函数定义三节点 DAG（`domain_scanner → batch_auditor → ci_gate`），所有节点 maturity 均为 `NodeMaturity.CRYSTALLIZED`。
- `src/omnicompany/packages/services/pipeline_ci/routers.py`：Router 实现文件，包含 `DomainScannerRouter`、`BatchAuditorRouter`、`CIGateRouter` 三个类及辅助函数 `_collect_domains`，全部为纯确定性逻辑，不调用 LLM。
- `src/omnicompany/packages/services/pipeline_ci/run.py`：bindings 注册文件，通过 `build_bindings()` 将节点 id 映射至对应 Router 实例，供运行时框架装配管线。
- `src/omnicompany/packages/services/pipeline_ci/formats.py`：Format 定义与注册文件，声明 `pipeline_ci.scan-request`、`pipeline_ci.domains`、`pipeline_ci.ci-report` 三个格式对象，并提供 `register_formats()` 注册入口。

> _来源: `src/omnicompany/packages/services/pipeline_ci/__init__.py`, `src/omnicompany/packages/services/pipeline_ci/pipeline.py`, `src/omnicompany/packages/services/pipeline_ci/routers.py`, `src/omnicompany/packages/services/pipeline_ci/run.py`, `src/omnicompany/packages/services/pipeline_ci/formats.py`_

## Related

与 `pipeline_ci` 包直接相关的 KB 条目：

- `kb.arch.pipeline.pipeline_ci`：该条目描述的正是本包所声明的 `pipeline-ci` 管线本身，是对同一对象的管线视角描述。
- `kb.arch.package.services_workflow_factory`：`BatchAuditorRouter` 在运行时动态导入 `ErrorRouteAuditorRouter` 自该包，是直接的运行时依赖。

被本包扫描审计的目标域包（均为 `pipeline_ci` 的扫描对象）：
- `kb.arch.package.domains_voxelcraft`
- `kb.arch.package.domains_demogame_produce`
- `kb.arch.package.domains_demogame_unity_explore`
- `kb.arch.package.domains_narrative`
- `kb.arch.package.domains_software_engineering_debugger`
- `kb.arch.package.domains_software_engineering_equiv_test`
- `kb.arch.package.domains_software_engineering_lang_rewrite`
- `kb.arch.package.services_guardian`
- `kb.arch.package.services_lap_auditor`

> _来源: kb_context_

## Known limitations

基于当前可见代码片段，可以观察到以下已知局限或未完成区域：

1. **`BatchAuditorRouter.run()` 代码截断**：`routers.py` 的代码片段在第 123 行处截断，循环体内对 `ErrorRouteAuditorRouter` 和 `PipelineChecker` 的实际调用方式不可见，无法确认聚合逻辑的完整实现。

2. **`CIGateRouter` 类定义不可见**：`run.py` 中导入了 `CIGateRouter`，但 `routers.py` 的代码片段仅展示到 `BatchAuditorRouter` 的 `run()` 方法开头处，`CIGateRouter` 的完整类定义未出现在代码片段中。

3. **无并行化**：`BatchAuditorRouter` 的 `DESCRIPTION` 字段注明"并行执行"，但从 `run()` 中可见的循环结构是顺序的 `for domain in domains` 迭代，是否有实际并行实现无法从截断的代码确认。

4. **外部依赖的动态导入**：`BatchAuditorRouter.run()` 在方法体内才导入 `ErrorRouteAuditorRouter` 和 `PipelineChecker`，属于运行时延迟导入，若这些依赖在环境中不可用，错误只能在执行期发现，而非静态分析阶段。

代码片段中未发现 `TODO` / `FIXME` / `XXX` 等注释标记。

> _来源: `src/omnicompany/packages/services/pipeline_ci/routers.py`, `src/omnicompany/packages/services/pipeline_ci/run.py`_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 5 code anchors (12268 chars), 0 plan docs (0 chars), 25 kb refs
