# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:33:56Z
---
omnikb_type: karch
id: kb.arch.package.services_lap_auditor
name: 'Package: packages/services/lap_auditor'
tags:
- topic.package
- layer.services
- domain.lap_auditor
- architecture
maturity: living
summary: omnicompany.packages.services.lap_auditor — LAP 规范审计工作流
scope: omnicompany
code_anchors:
- src/omnicompany/packages/services/lap_auditor/__init__.py
- src/omnicompany/packages/services/lap_auditor/pipeline.py
- src/omnicompany/packages/services/lap_auditor/routers.py
- src/omnicompany/packages/services/lap_auditor/run.py
---

# Package: packages/services/lap_auditor

> **id**: `kb.arch.package.services_lap_auditor` · **type**: karch · **maturity**: living

## Why this exists

`lap_auditor` 包的存在目的是为 Omnicompany 项目提供一套针对 LAP（Logic-Anchor-Pipeline）六元规范的自动化代码审计工作流。其核心职能是：扫描指定目录下的 Python 源码，借助 LLM 评估这些代码对 LAP 六元规范的依从程度，并输出结构化的审计报告。

根据 `__init__.py` 的模块文档字符串，这一工作流面向的场景是：当团队需要检查某段代码是否真正符合 LAP 架构规范时，可以用本包发起一次审计。审计维度聚焦于四大不可妥协的红线（事件总线驱动、Format 真实性、接口规范遵循、Domain 隔离），并对代码按四个象限进行分类归判（规范实现、缺陷实现、非 LAP 代码、基础设施代码）。

当前材料中无 plan 文档，也无关联的 KExperiment 条目，故无法进一步追溯该包被创建的具体背景决策。

> _来源: seed_description, `src/omnicompany/packages/services/lap_auditor/__init__.py`_

## How it works

审计流程由三个节点串联构成，通过 `build_pipeline()` 函数声明为一个 `PipelineSpec` 对象（id 为 `"lap-audit"`），节点依次为：

- **`context_getter`**（`NodeKind.ANCHOR`，`NodeMaturity.CRYSTALLIZED`）：绑定到 `ContextGetterRouter`。其 `run` 方法接收包含 `target_path` 的 `input_data`，递归扫描指定路径下所有 `.py` 文件（或单个 `.py` 文件），将文件内容拼装成带文件名标题的字符串 `code_context`，以 `format_out="lap_auditor.context"` 向下游传递。路径不存在或无 Python 文件时返回 `VerdictKind.FAIL` 并 HALT。
- **`spec_auditor`**（`NodeKind.ANCHOR`，`NodeMaturity.GROWING`）：绑定到 `SpecAuditorRouter`（继承 `LLMRouter`）。从上下文中取出 `code_context`，截取前 80000 字符发给 LLM，系统提示为 `_AUDITOR_SYSTEM_PROMPT`（内嵌四大红线及输出格式要求）。该节点开启了 `REFLECTION_ENABLED = True`。验证器为 `ValidatorKind.SOFT`，失败时最多 RETRY 2 次。
- **`report_formatter`**（`NodeKind.ANCHOR`，`NodeMaturity.CRYSTALLIZED`）：绑定到 `ReportFormatterRouter`。接收 `format_in="lap_auditor.report"`，将审计报告输出到控制台，成功时 EMIT，失败时 HALT。

`build_bindings()` 函数（位于 `run.py`）负责将上述三个 Router 实例化并返回节点 id 到 Router 的映射字典，其中 `SpecAuditorRouter` 需要 `LLMClient`，通过 `LLMClient.for_role("runtime_main", tools=[])` 创建。

基于当前代码片段，`SpecAuditorRouter.run` 方法在 `except` 分支处被截断，完整的异常处理逻辑需读完整 `routers.py` 文件。`ReportFormatterRouter` 的实现在代码片段中未出现，只能从 `run.py` 的 import 确认其存在。

> _来源: `src/omnicompany/packages/services/lap_auditor/pipeline.py`, `src/omnicompany/packages/services/lap_auditor/routers.py`, `src/omnicompany/packages/services/lap_auditor/run.py`_

## Public surface

本包对外暴露的接口如下：

| 名称 | 类型 | 文件 | 说明 |
|---|---|---|---|
| `build_pipeline()` | 函数 | `pipeline.py` | 返回 `PipelineSpec`，id 为 `"lap-audit"` |
| `build_bindings()` | 函数 | `run.py` | 返回 `dict[str, Router]`，将节点 id 映射到 Router 实例 |
| `ContextGetterRouter` | 类 | `routers.py` | 继承 `Router`，处理 `lap_auditor.input` → `lap_auditor.context` |
| `SpecAuditorRouter` | 类 | `routers.py` | 继承 `LLMRouter`，处理 `lap_auditor.context` → `lap_auditor.report` |
| `ReportFormatterRouter` | 类 | `routers.py` | 从 `run.py` import 可知存在，处理 `lap_auditor.report` → `lap_auditor.done` |

Format id（数据格式标识符）：
- `lap_auditor.input` — 入口格式，含 `target_path`
- `lap_auditor.context` — 拉取后的代码上下文，含 `code_context`
- `lap_auditor.report` — LLM 生成的审计报告
- `lap_auditor.done` — 最终输出格式

> _来源: `src/omnicompany/packages/services/lap_auditor/pipeline.py`, `src/omnicompany/packages/services/lap_auditor/routers.py`, `src/omnicompany/packages/services/lap_auditor/run.py`_

## Internal structure

本包内部按职责拆分为四个文件：

- `__init__.py`：模块文档入口，不含实质逻辑。
- `pipeline.py`：拓扑声明层，仅通过 `build_pipeline()` 声明 `PipelineSpec`（节点、边、路由规则），不含任何业务逻辑。
- `routers.py`：业务实现层，包含 `ContextGetterRouter`（继承 `Router`）、`SpecAuditorRouter`（继承 `LLMRouter`）及 `ReportFormatterRouter`（从 import 确认存在）三个 Router 类，以及内嵌的 LLM 系统提示常量 `_AUDITOR_SYSTEM_PROMPT`。
- `run.py`：绑定组装层，`build_bindings()` 将节点 id 与 Router 实例关联，并完成 `LLMClient` 的初始化注入。

这种拆分结构与 LAP 规范本身的要求吻合：拓扑（`pipeline.py`）与实现（`routers.py`）严格分离，绑定（`run.py`）作为独立层负责依赖注入。

> _来源: `src/omnicompany/packages/services/lap_auditor/pipeline.py`, `src/omnicompany/packages/services/lap_auditor/routers.py`, `src/omnicompany/packages/services/lap_auditor/run.py`, `src/omnicompany/packages/services/lap_auditor/__init__.py`_

## Files

- `src/omnicompany/packages/services/lap_auditor/__init__.py` — 模块入口文件，包含包级文档字符串，说明本包职能为扫描 Python 代码并用 LLM 评估 LAP 六元规范依从度。
- `src/omnicompany/packages/services/lap_auditor/pipeline.py` — 管线拓扑声明文件，通过 `build_pipeline()` 函数构造并返回 id 为 `"lap-audit"` 的 `PipelineSpec`，定义三节点两边的有向图结构及各节点的路由规则。
- `src/omnicompany/packages/services/lap_auditor/routers.py` — Router 实现文件，包含 `ContextGetterRouter`（源码拉取）、`SpecAuditorRouter`（LLM 审计）及 `ReportFormatterRouter`（报告输出）的具体实现，以及审计系统提示常量 `_AUDITOR_SYSTEM_PROMPT`。
- `src/omnicompany/packages/services/lap_auditor/run.py` — 绑定组装文件，通过 `build_bindings()` 函数实例化所有 Router 并完成 `LLMClient` 注入，返回节点 id 到 Router 实例的映射字典。

> _来源: `src/omnicompany/packages/services/lap_auditor/__init__.py`, `src/omnicompany/packages/services/lap_auditor/pipeline.py`, `src/omnicompany/packages/services/lap_auditor/routers.py`, `src/omnicompany/packages/services/lap_auditor/run.py`_

## Related

与本包同属 `services` 层、结构最接近的条目：

- `kb.arch.package.services_pipeline_ci` — 同为面向代码质量/规范检查的 services 层管线，与 `lap_auditor` 在审计目标上相邻（一个做 CI 扫描，一个做 LAP 规范审计）。
- `kb.arch.package.services_guardian` — 同为 services 层的守护/检查类管线，职能上与审计工作流有相似的质量保障定位。
- `kb.arch.package.services_absorption` — 同为 services 层工作流，采用相同的 `PipelineSpec` + Router 绑定架构模式。
- `kb.arch.package.services_knowledge` — OmniKB 知识库管线，审计结果报告可能与知识沉淀流程存在关联。

> _来源: kb_context 列表中的真实 id_

## Known limitations

从代码片段中可以观察到以下明确的局限或未完成区域：

1. **`SpecAuditorRouter.run` 被截断**：代码片段在 `except Exception as e:` 之后被截断，`return Verdi` 明显是不完整的 `return Verdict(...)` 调用，说明异常处理逻辑在现有片段中不可见，无法确认其完整实现。

2. **`code_context` 截断上限为 80000 字符**：`SpecAuditorRouter.run` 中显式截取 `code_context[:80000]`，对于大型代码库而言会导致超出部分被静默丢弃，审计覆盖不完整。代码中未见任何对截断的警告或提示。

3. **`ReportFormatterRouter` 实现不可见**：代码片段中只有 `routers.py` 的部分内容，`ReportFormatterRouter` 的 `run` 方法实现未出现，仅能从 `run.py` 的 import 确认其存在。

4. **`spec_auditor` 节点处于 `NodeMaturity.GROWING`**：表明该节点尚未达到稳定状态，与 `context_getter` 和 `report_formatter` 的 `NodeMaturity.CRYSTALLIZED` 形成对比，说明 LLM 审计节点的实现仍在演进中。

5. **`ValidatorSpec` 描述与 `PipelineSpec` 描述不一致**：`spec_auditor` 的 `ValidatorSpec.description` 写的是"LLM 评估代码对 LAP 四大红线的依从度"，而 `PipelineSpec.description` 及 `__init__.py` 写的是"LAP 六元规范"，四大红线与六元规范的关系在当前材料中未被明确说明。

> _来源: `src/omnicompany/packages/services/lap_auditor/routers.py`, `src/omnicompany/packages/services/lap_auditor/pipeline.py`, `src/omnicompany/packages/services/lap_auditor/__init__.py`_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 4 code anchors (10850 chars), 0 plan docs (0 chars), 25 kb refs
