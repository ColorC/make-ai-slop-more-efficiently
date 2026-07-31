# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:04:17Z
---
omnikb_type: karch
id: kb.arch.pipeline.absorption_survey
name: 'Pipeline: absorption-survey'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.absorption-survey
- domain.absorption
- architecture
maturity: living
summary: Repo Absorption · Stage 1 Survey & Triage — 从 GitHub 仓库列表识别值得吸纳的地标，不下载源码
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L237-L262
---

# Pipeline: absorption-survey

> **id**: `kb.arch.pipeline.absorption_survey` · **type**: karch · **maturity**: living

## Why this exists

Repo Absorption 系列管线的第一阶段，目标是从 GitHub 仓库列表中识别值得吸纳的地标性内容，整个过程**不下载源码**。这一设计意味着该阶段是纯粹的调研与分级（Survey & Triage）工作：在消耗实际网络和存储资源之前，先通过轻量方式评估仓库是否值得进一步处理。seed description 明确将其定位为 Repo Absorption 的 Stage 1，暗示后续还存在更深入的吸纳阶段，但当前材料仅覆盖此阶段。

当前可见材料（seed description + 注册代码）不包含任何 plan 文档或 KExperiment 条目，因此无法从设计意图或实验验证角度进一步展开。

> _来源: seed_description, src/omnicompany/core/pipelines.py:L237-L262_

## How it works

从注册代码可以看到，该管线通过 `PipelineEntry` 进行声明式注册，关键字段如下：

- `name`: `"absorption-survey"`
- `domain`: `"absorption"`
- `default_db_dir`: `"data/absorption"`
- `default_max_steps`: `15`
- `build_pipeline`: 通过 `_lazy` 延迟加载，指向 `omnicompany.packages.services.absorption.run` 模块的 `build_survey_pipeline` 函数
- `build_bindings`: 通过 `_lazy_fn` 延迟加载，指向同模块的 `build_survey_bindings` 函数

`_lazy` / `_lazy_fn` 是延迟导入机制，仅在实际构建管线时才加载目标模块，避免启动时全量导入。CLI 入口接受 `repos` 和 `profile` 两个参数，分别控制目标仓库列表和吸纳配置文件类型。

基于当前代码片段只能看到注册入口和参数声明，`build_survey_pipeline` 与 `build_survey_bindings` 的具体实现逻辑需读 `omnicompany/packages/services/absorption/run.py` 文件才能获得。

> _来源: src/omnicompany/core/pipelines.py:L237-L262_

## Public surface

当前代码片段中可见的对外接口：

- **管线名**: `"absorption-survey"`（通过此名称从 CLI 或 EventBus 触发管线）
- **CLI 参数**:
  - `repos` — 目标仓库列表，接受 JSON 数组或逗号分隔字符串，如 `'openai/codex,google-gemini/gemini-cli'`，必填
  - `profile` — 吸纳 Profile，可选值包括 `framework_absorption` 和 `domain_absorption`（从 help 文本推断枚举值，但枚举定义的完整列表需读源文件确认）
- **构建函数**（通过 `_lazy` / `_lazy_fn` 暴露）:
  - `build_survey_pipeline`（位于 `omnicompany.packages.services.absorption.run`）
  - `build_survey_bindings`（同模块）

其余 public API（如返回类型、事件名、Format id）在当前片段中未出现，需读取 `run.py` 的完整实现。

> _来源: src/omnicompany/core/pipelines.py:L237-L262_

## Internal structure

当前可见材料（代码片段）仅展示了管线的注册层。根据注册信息可以推断模块划分：

- **注册层**: `omnicompany/core/pipelines.py` — 负责将 `absorption-survey` 声明为 `PipelineEntry` 并注入全局注册表
- **实现层**: `omnicompany/packages/services/absorption/run.py` — 存放 `build_survey_pipeline` 和 `build_survey_bindings` 的具体实现，是管线逻辑的主体
- **数据目录**: `data/absorption`（`default_db_dir` 配置值，运行时写入）

`run.py` 内部是否进一步拆分子模块（如网络请求、分级评分、结果序列化等）在当前片段中无法确认，需阅读该文件的完整内容。

> _来源: src/omnicompany/core/pipelines.py:L237-L262_

## Files

当前 code_anchors 仅提供了一个文件路径：

- `src/omnicompany/core/pipelines.py`（L237–L262）— 管线注册中心，包含 `absorption-survey` 的 `PipelineEntry` 声明，定义管线名、domain、CLI 参数、默认配置及指向实现层的延迟加载引用

实现层文件 `omnicompany/packages/services/absorption/run.py` 在注册代码中被引用，但未出现在 code_anchors 列表中，无法列出其作用描述。

> _来源: src/omnicompany/core/pipelines.py:L237-L262_

## Related

与本条目相关的 KB 已有条目：

- `kb.arch.pipeline.selftest` — selftest 管线验证管线注册与 bindings 的正确性，与 absorption-survey 共享同一注册机制
- `kb.arch.pipeline.pipeline_ci` — pipeline-ci 对所有域管线的错误路由完整性和类型安全进行批量审计，absorption-survey 作为 `absorption` 域管线也在其审计范围内
- `kb.arch.pipeline.omnikb_audit` — omnikb-audit 校验 code_anchor 漂移，与本管线的注册代码位置相关

其余 voxelcraft 系列及 demogame 系列管线与本管线属于并列关系，无直接依赖，不单独列出。

> _来源: kb_context_

## Known limitations

从注册代码和 seed description 中可观察到以下局限或待确认点：

- **不下载源码**：seed description 明确标注此阶段不拉取源码，意味着分析深度受限于 GitHub API 或元信息层，但具体分析维度在当前代码片段中未呈现
- **`profile` 参数的完整枚举未可见**：help 文本提及 `framework_absorption | domain_absorption`，但枚举的完整定义及校验逻辑需读 `run.py` 确认
- **`default_max_steps=15` 的含义不明**：当前片段只能看到该数值的声明，步骤的实际定义和终止条件在 `build_survey_pipeline` 内部实现中，当前无法确认
- 代码片段中未出现任何 TODO / FIXME / XXX 注释，无法从注释层面报告已知缺陷

> _来源: src/omnicompany/core/pipelines.py:L237-L262, seed_description_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 1 code anchors (1168 chars), 0 plan docs (0 chars), 25 kb refs
