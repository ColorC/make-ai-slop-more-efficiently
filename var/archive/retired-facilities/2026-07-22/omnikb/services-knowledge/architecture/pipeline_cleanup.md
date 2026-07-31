# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:11:02Z
---
omnikb_type: karch
id: kb.arch.pipeline.cleanup
name: 'Pipeline: cleanup'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.cleanup
- domain.workflow
- architecture
maturity: living
summary: 环境稽查 — 扫描并清理 AI 误触产生的错位文件/目录
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L45-L70
---

# Pipeline: cleanup

> **id**: `kb.arch.pipeline.cleanup` · **type**: karch · **maturity**: living

## Why this exists

cleanup 管线的存在动机直接来自其 seed description：「环境稽查 — 扫描并清理 AI 误触产生的错位文件/目录」。在 AI 辅助开发场景中，LLM Agent 在执行文件操作时可能将文件写入错误路径、创建冗余目录或留下临时产物，这些「错位」产物若不及时清理，会污染工作区并干扰后续管线的文件扫描逻辑。cleanup 管线因此充当一个周期性或按需触发的环境稽查角色，通过关键词 + 根目录两个参数定位可疑文件，再执行清理动作。当前无 plan 文档对设计动机有进一步阐述，上述描述完全来自注册时的 description 字符串。

> _来源: seed_description, src/omnicompany/core/pipelines.py:L45-L70_

## How it works

从 `src/omnicompany/core/pipelines.py` 第 45–59 行可以看到，cleanup 管线通过 `register(PipelineEntry(...))` 完成注册，关键字段如下：

- `name`: `"cleanup"`
- `domain`: `"workflow"`
- `build_pipeline`: 通过 `_lazy("omnicompany.packages.services.cleanup_bot.pipeline", "build_pipeline")` 懒加载，实际构造逻辑位于 `omnicompany.packages.services.cleanup_bot.pipeline` 模块的 `build_pipeline` 函数。
- `build_bindings`: 通过 `_lazy_fn("omnicompany.packages.services.cleanup_bot.run", "build_bindings")` 懒加载，绑定逻辑位于 `omnicompany.packages.services.cleanup_bot.run` 模块的 `build_bindings` 函数。
- `default_db_dir`: `"data/workflow"`
- `default_max_steps`: `10`
- CLI 入参：`keyword`（必填，搜索关键词）和 `root_dir`（可选，默认 `"E:\\"`，即 Windows E 盘根目录）。

注册过程被 `try/except` 包裹，若加载失败会以 `logger.debug("skip cleanup: %s", e)` 静默跳过，不影响其他管线初始化。

基于当前代码片段只能看到注册层的元数据声明，`build_pipeline` 与 `build_bindings` 的实际节点图结构、扫描算法和清理策略需读 `omnicompany/packages/services/cleanup_bot/pipeline.py` 与 `omnicompany/packages/services/cleanup_bot/run.py` 才能确认。

> _来源: src/omnicompany/core/pipelines.py:L45-L70_

## Public surface

基于当前代码片段，对外暴露的接口如下：

- **管线名**: `"cleanup"`（通过 `PipelineEntry.name` 注册，可通过 CLI 或程序化方式按名称调用）
- **CLI 参数**:
  - `keyword`：搜索关键词，`required=True`
  - `root_dir`：扫描根目录，默认值 `"E:\\"`
- **懒加载入口**（模块路径层面可见）:
  - `omnicompany.packages.services.cleanup_bot.pipeline.build_pipeline`
  - `omnicompany.packages.services.cleanup_bot.run.build_bindings`

以上是代码中实际出现的标识符。`build_pipeline` 和 `build_bindings` 的函数签名、返回类型及具体参数在当前片段中未出现，需读对应模块文件确认。

> _来源: src/omnicompany/core/pipelines.py:L45-L70_

## Internal structure

当前可见材料 (code/plan/kb context) 不足以回答这一段的内部子模块划分。从注册信息推断，实现至少分布在两个模块：

- `omnicompany/packages/services/cleanup_bot/pipeline.py` — 负责 `build_pipeline`，应包含管线节点图定义
- `omnicompany/packages/services/cleanup_bot/run.py` — 负责 `build_bindings`，应包含运行时绑定逻辑

但上述路径仅从 `_lazy` / `_lazy_fn` 字符串参数推导，文件是否实际存在、内部是否还有更细的子模块划分，需要读取 file_list 和对应源文件才能确认。

补完此段需要阅读：`omnicompany/packages/services/cleanup_bot/pipeline.py`、`omnicompany/packages/services/cleanup_bot/run.py` 以及该包的 `__init__.py`（如存在）。

> _来源: src/omnicompany/core/pipelines.py:L45-L70_

## Files

当前 code_anchors 材料中仅提供了以下一个文件路径：

- `src/omnicompany/core/pipelines.py`：核心管线注册表，通过 `register(PipelineEntry(...))` 将 cleanup 管线的元数据（名称、domain、懒加载入口、默认参数、CLI 参数）登记到系统中。

cleanup 管线的实际实现文件（`omnicompany/packages/services/cleanup_bot/pipeline.py`、`omnicompany/packages/services/cleanup_bot/run.py`）未出现在 code_anchors 列表中，无法在此段列出。

> _来源: src/omnicompany/core/pipelines.py:L45-L70_

## Related

与 cleanup 管线最相关的现有 KB 条目：

- `kb.arch.pipeline.guardian`：同样以文件系统污染扫描为核心职责（「文件系统污染扫描 + 架构规范审计 + 健康报告」），与 cleanup 在问题域上高度重叠，可能存在分工或协作关系。
- `kb.arch.pipeline.omnikb_audit`：包含 code_anchor 漂移和孤儿检测，属于另一维度的环境稽查。
- `kb.arch.pipeline.pipeline_ci`：批量审计管线质量，属于稽查类管线的同族。
- `kb.arch.pipeline.selftest`：验证管线注册与 CLI 基础功能，可覆盖对 cleanup 注册是否正确的检查。

> _来源: kb_context_

## Known limitations

基于当前可见代码片段，可以观察到以下局限与潜在问题：

1. **平台硬编码**：`root_dir` 默认值为 `"E:\\"` （Windows 路径），暗示该管线在设计上或使用上主要面向 Windows 环境，跨平台兼容性未见处理。
2. **静默跳过**：注册逻辑被 `try/except` 包裹，失败时仅输出 `logger.debug("skip cleanup: %s", e)`，不会抛出异常。这意味着若 `cleanup_bot` 包缺失，系统不会有明显报错，可能造成调试困难。
3. **实现不可见**：扫描规则、「错位」判定逻辑、清理策略（删除/移动/仅报告）在当前代码片段中完全不可见，无法评估其完备性或安全性（例如误删风险）。
4. **步数上限偏低**：`default_max_steps=10` 相对保守，对于大规模目录扫描是否足够，无法从当前材料判断。

代码片段中未出现 TODO / FIXME / XXX 注释。

> _来源: src/omnicompany/core/pipelines.py:L45-L70, seed_description_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 1 code anchors (1390 chars), 0 plan docs (0 chars), 25 kb refs
