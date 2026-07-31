# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:31:55Z
---
omnikb_type: karch
id: kb.arch.package.services_cleanup_bot
name: 'Package: packages/services/cleanup_bot'
tags:
- topic.package
- layer.services
- domain.cleanup_bot
- architecture
maturity: living
summary: omnicompany.packages.services.cleanup_bot — 环境副作用清理工作流
scope: omnicompany
code_anchors:
- src/omnicompany/packages/services/cleanup_bot/__init__.py
- src/omnicompany/packages/services/cleanup_bot/pipeline.py
- src/omnicompany/packages/services/cleanup_bot/routers.py
- src/omnicompany/packages/services/cleanup_bot/run.py
---

# Package: packages/services/cleanup_bot

> **id**: `kb.arch.package.services_cleanup_bot` · **type**: karch · **maturity**: living

## Why this exists

`cleanup_bot` 是 Omnicompany 的一个服务层工作流包，专门用于识别并规划清理由 AI Agent 在宿主机上造成的文件系统副作用。根据模块文档字符串，AI Agent 在执行 bash 命令时，因相对路径写错或字符串拼接遗漏，会在操作系统中留下"错位、嵌套、重复"的垃圾文件夹。典型案例如：本意访问 `E:\WindowsWorkspace`，却意外执行了 `mkdir -p E:\e\WindowsWorkspace`，产生了一个单字母异常嵌套目录。

该包的设计原则是**只生成清理计划，不自动执行**——这一安全策略直接体现在包顶层文档字符串中："生成 PowerShell 清理脚本（不自动执行，仅生成计划）"。这意味着整个工作流的最终产物是供人工审核后手动运行的 PowerShell 脚本，而非自动触发删除操作。

当前可见材料中无 plan 文档，也无关联的 KExperiment 条目，无法判断该包是在何种具体事故背景下立项的。

> _来源: src/omnicompany/packages/services/cleanup_bot/\_\_init\_\_.py, src/omnicompany/packages/services/cleanup_bot/routers.py_

## How it works

工作流由 `build_pipeline()` 函数声明，返回一个 `PipelineSpec`（id 为 `"cleanup"`），包含三个串联的 `PipelineNode`，依次执行：

- **`evidence_gatherer`**（`NodeMaturity.CRYSTALLIZED`）：对应 `EvidenceGathererRouter`，接收 `cleanup.input` 格式（包含 `root_dir` 和 `keyword` 两个字段），使用 `os.walk` 递归扫描磁盘，最大深度限制为 5 层（`max_depth = 5`）。所有路径名或文件名中包含关键词的条目被收集到 `found_paths` 列表，输出 `cleanup.evidence` 格式。若关键词为空或未找到任何路径，返回 `VerdictKind.FAIL` 并终止（`RouteAction.HALT`）。

- **`anomaly_detector`**（`NodeMaturity.GROWING`）：对应 `AnomalyDetectorRouter`，继承自 `LLMRouter`。接收 `cleanup.evidence`，将路径名单和关键词拼入 user message，配合 `_CLEANUP_SYSTEM_PROMPT`（角色设定为"系统环境异常清理机器人"）调用 `self.client.call()`。LLM 被要求输出三部分 Markdown：异常判定结论、正常保留路径、Windows PowerShell 清理脚本。失败时最多重试 2 次（`max_retries=2`）。

- **`rollback_planner`**（`NodeMaturity.CRYSTALLIZED`）：对应 `RollbackPlannerRouter`，接收 `cleanup.plan`，将 LLM 输出的 Markdown 报告格式化打印到控制台，不执行任何文件删除操作，输出 `cleanup.done` 格式。

`build_bindings()` 函数负责将节点 id 与 Router 实例绑定，其中 `AnomalyDetectorRouter` 通过 `LLMClient.for_role("runtime_main", tools=[])` 获取 LLM 客户端。

基于当前代码片段，`RollbackPlannerRouter.run()` 的完整实现被截断，其控制台打印逻辑的细节需读 `routers.py` 完整文件。

> _来源: src/omnicompany/packages/services/cleanup_bot/pipeline.py, src/omnicompany/packages/services/cleanup_bot/routers.py, src/omnicompany/packages/services/cleanup_bot/run.py_

## Public surface

该模块对外暴露以下接口：

**函数**
- `build_pipeline() -> PipelineSpec`（定义于 `pipeline.py`）：构建并返回整个 cleanup 管线的声明式规格。
- `build_bindings(input_dict: dict | None = None) -> dict[str, Router]`（定义于 `run.py`）：返回节点 id 到 Router 实例的绑定字典，供运行时调度使用。

**Router 类**（定义于 `routers.py`）
- `EvidenceGathererRouter`：`FORMAT_IN = "cleanup.input"`，`FORMAT_OUT = "cleanup.evidence"`
- `AnomalyDetectorRouter`：`FORMAT_IN = "cleanup.evidence"`，`FORMAT_OUT = "cleanup.plan"`，`INPUT_KEYS = ["evidence_str"]`
- `RollbackPlannerRouter`：`FORMAT_IN = "cleanup.plan"`，`FORMAT_OUT = "cleanup.done"`

**管线 id**
- `"cleanup"`（`PipelineSpec.id`）：全局可引用的管线标识符。

**Format id 序列**（数据流格式标识）
- `cleanup.input` → `cleanup.evidence` → `cleanup.plan` → `cleanup.done`

> _来源: src/omnicompany/packages/services/cleanup_bot/pipeline.py, src/omnicompany/packages/services/cleanup_bot/routers.py, src/omnicompany/packages/services/cleanup_bot/run.py_

## Internal structure

该包按职责划分为四个文件，层次清晰：

- `__init__.py`：包入口，仅含模块级 docstring，声明包的用途，不导出任何符号。
- `pipeline.py`：管线声明层，使用 `PipelineSpec` / `PipelineNode` / `PipelineEdge` 等协议类型描述工作流拓扑。它只做结构声明，不包含任何业务逻辑或 IO 操作。
- `routers.py`：业务逻辑层，实现三个 Router 类。`EvidenceGathererRouter` 和 `RollbackPlannerRouter` 继承自 `Router`（纯 Python 逻辑），`AnomalyDetectorRouter` 继承自 `LLMRouter`（持有 LLM 客户端）。`routers.py` 还持有唯一的提示词常量 `_CLEANUP_SYSTEM_PROMPT`。
- `run.py`：绑定层，通过 `build_bindings()` 将 pipeline 节点 id 与 router 实例关联，并负责 `LLMClient` 的实例化。

`run.py` 从 `routers.py` 导入全部三个 Router 类，以及从 `omnicompany.runtime.llm.llm` 导入 `LLMClient`，从 `omnicompany.runtime.routing.router` 导入 `Router` 基类。`pipeline.py` 则完全独立于 `routers.py`，仅依赖 `omnicompany.protocol` 层。

> _来源: src/omnicompany/packages/services/cleanup_bot/\_\_init\_\_.py, src/omnicompany/packages/services/cleanup_bot/pipeline.py, src/omnicompany/packages/services/cleanup_bot/routers.py, src/omnicompany/packages/services/cleanup_bot/run.py_

## Files

- `src/omnicompany/packages/services/cleanup_bot/__init__.py`：包入口，含模块级 docstring 说明整体用途（磁盘扫描 + LLM 判定 + PowerShell 脚本生成）。
- `src/omnicompany/packages/services/cleanup_bot/pipeline.py`：使用 `PipelineSpec` 声明 `"cleanup"` 管线的节点拓扑与路由规则，由 `build_pipeline()` 函数返回。
- `src/omnicompany/packages/services/cleanup_bot/routers.py`：实现 `EvidenceGathererRouter`（磁盘爬虫）、`AnomalyDetectorRouter`（LLM 异常判定）、`RollbackPlannerRouter`（清理计划打印），并定义 LLM 系统提示词 `_CLEANUP_SYSTEM_PROMPT`。
- `src/omnicompany/packages/services/cleanup_bot/run.py`：绑定层，`build_bindings()` 实例化全部三个 Router 并与管线节点 id 对应，同时负责 `LLMClient` 的构建。

> _来源: seed_description, src/omnicompany/packages/services/cleanup_bot/\_\_init\_\_.py, src/omnicompany/packages/services/cleanup_bot/pipeline.py, src/omnicompany/packages/services/cleanup_bot/routers.py, src/omnicompany/packages/services/cleanup_bot/run.py_

## Related

与 `cleanup_bot` 在结构上同属 `services` 层、采用相同"管线声明 + Router 实现 + bindings 绑定"三文件模式的包：

- `kb.arch.package.services_guardian`：同为服务层守护/检查类管线，与 cleanup_bot 同属系统健康维护方向。
- `kb.arch.package.services_pipeline_ci`：管线质量 CI 扫描器，同样对系统产物做审查，与 cleanup_bot 的"扫描并报告"思路相近。
- `kb.arch.package.services_absorption`：服务层 Repo 吸纳工作流，管线结构与 cleanup_bot 相似，可作为对比参考。
- `kb.arch.package.services_lap_auditor`：LAP 规范审计工作流，同为"扫描 + LLM 判定 + 输出报告"模式的服务管线。

> _来源: kb_context_

## Known limitations

从代码中可以观察到以下明确的局限或潜在问题：

1. **`RollbackPlannerRouter.run()` 实现被截断**：代码片段在第 134 行处中断，`run()` 方法的完整实现不可见，只能确认其 docstring 中描述为"格式化打印到控制台，不自动删除任何文件"。

2. **`PermissionError` 被静默忽略**：`EvidenceGathererRouter.run()` 中 `os.walk` 遭遇权限错误时直接 `pass`，不记录日志也不向调用者反馈哪些目录被跳过，可能导致扫描结果不完整但无从察觉。

3. **Windows 路径硬编码**：`root_dir` 默认值为 `"E:\\"` ，`_CLEANUP_SYSTEM_PROMPT` 和 `AnomalyDetectorRouter` 的描述均明确以 Windows/PowerShell 为目标平台，跨平台可用性未作考虑。

4. **`anomaly_detector` 节点成熟度为 `NodeMaturity.GROWING`**：相比另外两个节点的 `CRYSTALLIZED` 状态，该节点标注为仍在演进中，说明 LLM 判定逻辑尚未稳定。

5. **LLM 输出无结构化解析**：`AnomalyDetectorRouter` 直接将 LLM 返回的原始 Markdown 字符串传入下游，没有对 PowerShell 脚本块做提取或验证，下游 `RollbackPlannerRouter` 依赖人工阅读整段输出。

> _来源: src/omnicompany/packages/services/cleanup_bot/pipeline.py, src/omnicompany/packages/services/cleanup_bot/routers.py_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 4 code anchors (10530 chars), 0 plan docs (0 chars), 25 kb refs
