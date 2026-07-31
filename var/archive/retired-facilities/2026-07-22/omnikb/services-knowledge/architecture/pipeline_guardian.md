# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:13:17Z
---
omnikb_type: karch
id: kb.arch.pipeline.guardian
name: 'Pipeline: guardian'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.guardian
- domain.guardian
- architecture
maturity: living
summary: 守护检查管线 — 文件系统污染扫描 + 架构规范审计 + 健康报告
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L475-L500
---

# Pipeline: guardian

> **id**: `kb.arch.pipeline.guardian` · **type**: karch · **maturity**: living

## Why this exists

guardian 管线的存在目的直接由其 `description` 字段给出："守护检查管线 — 文件系统污染扫描 + 架构规范审计 + 健康报告"。三个子职责并列：

- **文件系统污染扫描**：识别并标记工作区内由 AI 或自动化工具误触产生的错位文件、脏目录等污染物。
- **架构规范审计**：对项目的架构符合性做周期性检查，确保代码结构与既定规范保持一致。
- **健康报告**：汇总扫描与审计结果，向使用者输出可读的健康状态摘要。

当前可见材料（seed description + 注册代码）只提供了三句话的高层描述，未见 plan 文档对动机做进一步阐述。没有关联的 KExperiment 条目。

> _来源: seed_description, src/omnicompany/core/pipelines.py:L475-L489_

## How it works

从注册代码可以看到 guardian 管线通过 `PipelineEntry` 机制接入系统。关键字段如下：

- `name="guardian"`，`domain="guardian"`：管线标识与域隔离。
- `build_pipeline`：通过 `_lazy("omnicompany.packages.services.guardian.pipeline", "build_pipeline")` 延迟加载，实际 pipeline 图由 `omnicompany.packages.services.guardian.pipeline` 模块的 `build_pipeline` 函数构建。
- `build_bindings`：通过 `_lazy_fn("omnicompany.packages.services.guardian.run", "build_bindings")` 延迟加载，bindings 由 `omnicompany.packages.services.guardian.run` 模块的 `build_bindings` 函数提供。
- `default_db_dir="data/guardian"`：运行状态与步骤记录持久化到此目录。
- `default_max_steps=10`：单次运行最多执行 10 步。
- `cli_args` 包含一个参数 `project_root`（默认值 `e:/WindowsWorkspace/omnicompany`），用于指定被扫描的项目根目录。

注册块被 `try/except` 包裹，加载失败时记录 debug 日志并跳过，不影响其他管线启动。

基于当前代码片段只能看到注册入口，`build_pipeline` 与 `build_bindings` 的具体节点图、路由逻辑、扫描算法需读 `omnicompany/packages/services/guardian/pipeline.py` 和 `omnicompany/packages/services/guardian/run.py`。

> _来源: src/omnicompany/core/pipelines.py:L475-L491_

## Public surface

基于当前代码片段，对外可见的接口仅有管线注册层面的名称与 CLI 参数：

- **管线名**: `"guardian"`（通过 `PipelineEntry.name` 注册，供 CLI 及调度器调用）
- **domain**: `"guardian"`
- **CLI 参数**:
  - `project_root`：字符串，项目根目录路径，默认 `"e:/WindowsWorkspace/omnicompany"`
- **延迟加载符号**（接口合约，实现在对应模块）:
  - `omnicompany.packages.services.guardian.pipeline.build_pipeline`
  - `omnicompany.packages.services.guardian.run.build_bindings`

`build_pipeline` 与 `build_bindings` 的签名、返回类型在当前代码片段中未暴露，需读对应源文件才能确认完整 public surface。

> _来源: src/omnicompany/core/pipelines.py:L475-L489_

## Internal structure

当前可见材料不足以回答这一段。补完此段需要阅读: `omnicompany/packages/services/guardian/pipeline.py`（管线图节点定义）、`omnicompany/packages/services/guardian/run.py`（bindings 与执行入口）以及 `omnicompany/packages/services/guardian/` 目录下的完整文件清单，以确认是否存在 scanner、auditor、reporter 等子模块划分。

## Files

当前 code_anchors 中仅包含一处引用，可确认的文件如下：

- `src/omnicompany/core/pipelines.py`（L475–L491）：核心管线注册表，包含 `guardian` 管线的 `PipelineEntry` 定义，通过 `_lazy` / `_lazy_fn` 引用实际实现模块。

实际实现文件（`omnicompany/packages/services/guardian/pipeline.py`、`omnicompany/packages/services/guardian/run.py`）在 seed 的 code_anchors 中未列出，无法在此确认其路径与职责细节。

> _来源: src/omnicompany/core/pipelines.py:L475-L491_

## Related

与 guardian 管线功能相近或存在协作关系的已有 KB 条目：

- `kb.arch.pipeline.cleanup`：同样负责环境稽查，扫描并清理 AI 误触产生的错位文件/目录，与 guardian 的「文件系统污染扫描」职责高度重叠，两者定位可能互补或存在分工。
- `kb.arch.pipeline.lap_audit`：LAP 规范审计，检查代码对六元接口的依从度，与 guardian 的「架构规范审计」子职责在目标上相近。
- `kb.arch.pipeline.omnikb_audit`：OmniKB 全量审计，校验知识库引用完整性，属于同类「审计/健康检查」模式管线。
- `kb.arch.pipeline.pipeline_ci`：管线质量 CI 扫描，在注册表中紧随 guardian 之后定义，同属守护/质量保障类管线族群。
- `kb.arch.pipeline.selftest`：Omnicompany e2e 功能自测，健康检查的另一层面，与 guardian 的「健康报告」功能可形成互补。

> _来源: kb_context_

## Known limitations

基于当前代码片段可以观察到以下局限或待确认事项：

- **`project_root` 默认值硬编码为本地路径** `"e:/WindowsWorkspace/omnicompany"`，在非 Windows 或不同工作区环境下需显式传参，存在环境耦合风险。
- **`default_max_steps=10`**：步骤上限设置较低，若扫描或审计任务步骤较多，可能在单次运行中无法完成全量检查，但当前代码未见相关注释说明此限制是否刻意设计。
- **注册块使用 `try/except` 静默跳过**：加载失败只记录 debug 日志，不会向用户报告缺失原因，可能导致 guardian 管线静默不可用而难以排查。
- 实际的扫描逻辑、审计规则、报告格式在当前可见代码片段中完全不可见，无法判断这三个子职责是否均已实现，或是否存在 TODO/FIXME。需读 `omnicompany/packages/services/guardian/` 下的具体实现文件才能确认。

> _来源: src/omnicompany/core/pipelines.py:L475-L491_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 1 code anchors (1235 chars), 0 plan docs (0 chars), 25 kb refs
