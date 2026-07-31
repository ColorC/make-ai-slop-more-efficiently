# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:19:37Z
---
omnikb_type: karch
id: kb.arch.pipeline.demogame_learn
name: 'Pipeline: demogame-learn'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.demogame-learn
- domain.demogame
- architecture
maturity: living
summary: demogame 配表学习管线 — 多版本 CSV 差异驱动的规则发现
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L143-L168
---

# Pipeline: demogame-learn

> **id**: `kb.arch.pipeline.demogame_learn` · **type**: karch · **maturity**: living

## Why this exists

demogame 是一个游戏内容域，其配表（CSV 格式）随版本迭代持续演化。`demogame-learn` 管线的存在目的，是通过对比多个历史版本之间的 CSV 差异，自动发现其中隐含的配置规则——即"多版本 CSV 差异驱动的规则发现"。这一机制使得系统能够从已有版本对（如 `rel_1.5.4,rel_1.6.1`）中学习规律，并可选择性地用 out-of-sample 版本对进行泛化测试，从而评估规则的可靠性。

当前可见材料（`seed_description`、`code_snippets`）仅提供了管线注册层面的描述，未附带 plan 文档，亦无关联的 KExperiment 条目，因此无法进一步解释具体的设计动机或业务背景。

> _来源: seed_description, src/omnicompany/core/pipelines.py:L143-L168_

## How it works

从代码片段可知，`demogame-learn` 管线通过 `PipelineEntry` 注册至全局管线注册表。核心委托关系如下：

- **管线构建**：由 `_lazy("omnicompany.packages.domains.demogame.table_learning.table_learning_pipeline", "build_table_learning_pipeline")` 延迟加载，实际管线逻辑位于 `build_table_learning_pipeline` 函数。
- **绑定构建**：由 `_lazy_fn("omnicompany.packages.domains.demogame.table_learning.run_table_learning", "_build_default_bindings")` 提供默认绑定。
- **默认配置**：`default_db_dir="data/domains/demogame"`，`default_max_steps=50`。
- **CLI 参数**驱动运行时行为，包括表名（`table`）、训练版本对（`versions`，分号分隔的 `base,target` 对）、测试版本对（`test_versions`）、行过滤（`row_filter`，格式为 `field=val1,val2`）、跳过脚本生成标志（`skip_script`）、以及可选的 xlsm 文件路径（`xlsm`）和 sheet 名（`sheet`）。

注册操作被包裹在 `try/except` 块中，失败时以 `logger.debug` 记录并跳过，说明该管线属于可选加载的域扩展。

基于当前代码片段只能看到管线注册入口，完整机制（差异算法、规则发现逻辑、脚本生成流程）需读 `omnicompany/packages/domains/demogame/table_learning/table_learning_pipeline.py` 和 `omnicompany/packages/domains/demogame/table_learning/run_table_learning.py`。

> _来源: src/omnicompany/core/pipelines.py:L143-L168_

## Public surface

从注册代码中可确认以下对外暴露的接口：

- **管线名**：`"demogame-learn"`（通过 `PipelineEntry(name="demogame-learn", ...)` 注册，可由 CLI 按名调用）
- **CLI 参数**（均为 `CliArg` 实例）：
  - `table`：表名，默认值 `"TavernPool"`
  - `versions`：分号分隔的训练版本对，默认值 `"rel_1.5.4,rel_1.6.1;rel_1.6.1,rel_1.6.2;rel_1.6.2,rel_1.6.3"`
  - `test_versions`：out-of-sample 测试版本对（可选，无默认值）
  - `row_filter`：行过滤表达式，格式 `field=val1,val2`
  - `skip_script`：flag 类型，跳过脚本生成
  - `xlsm`：xlsm 文件路径，启用真实 benchmark
  - `sheet`：xlsm sheet 名

延迟加载的两个函数 `build_table_learning_pipeline` 和 `_build_default_bindings` 从模块路径可推断为公开接口，但其签名在当前代码片段中不可见。

> _来源: src/omnicompany/core/pipelines.py:L143-L168_

## Internal structure

从注册代码中可推断内部子模块划分如下：

- `omnicompany.packages.domains.demogame.table_learning.table_learning_pipeline`：承载管线主体逻辑，入口函数为 `build_table_learning_pipeline`。
- `omnicompany.packages.domains.demogame.table_learning.run_table_learning`：承载运行时绑定逻辑，入口函数为 `_build_default_bindings`（前缀 `_` 表明其为模块内约定的私有构建函数，但通过 `_lazy_fn` 被外部引用）。

两个模块均通过 `_lazy` / `_lazy_fn` 机制延迟导入，说明 demogame 域作为可选包存在，其缺失不影响核心管线注册系统的启动。

当前代码片段仅覆盖 `src/omnicompany/core/pipelines.py` 中的注册层，`table_learning` 子包内部的进一步文件划分（如是否存在 `diff.py`、`rule_extractor.py` 等子模块）无法从现有材料中确认。

> _来源: src/omnicompany/core/pipelines.py:L143-L168_

## Files

当前可见材料（code_anchors）仅提供以下一个文件：

- `src/omnicompany/core/pipelines.py`（第 143–168 行）：管线注册中心，包含 `demogame-learn` 的 `PipelineEntry` 定义，声明了 CLI 参数、延迟加载路径、默认数据库目录和最大步数。

demogame 域的实际实现文件（`omnicompany/packages/domains/demogame/table_learning/table_learning_pipeline.py`、`omnicompany/packages/domains/demogame/table_learning/run_table_learning.py`）在 code_anchors 中未出现，无法列出其作用描述。

> _来源: src/omnicompany/core/pipelines.py:L143-L168_

## Related

与本条目最直接相关的已有 KB 条目：

- `kb.arch.pipeline.demogame_produce`：demogame 配表生产管线，消费 `demogame-learn` 的学习产物，结合collab platform排期生成 CSV、Lua 及 P4 changelist，是本管线在生产端的下游配套。

其余 KB 条目均属于其他域（voxelcraft、sw-*、通用工具管线等），与 demogame 学习管线无直接关联。

> _来源: kb.arch.pipeline.demogame_produce_

## Known limitations

从注册代码可观察到以下已知局限或未实现区域：

1. **可选加载风险**：整个注册过程被 `try/except Exception` 包裹，失败时仅以 `logger.debug` 静默跳过。若 demogame 域包未安装，管线不可用，且不产生任何警告级别日志，调试时可能不易察觉。
2. **测试版本对为可选**：`test_versions` 参数无默认值，out-of-sample 评估并非强制流程，规则泛化能力的验证取决于调用方是否主动传入。
3. **xlsm benchmark 为可选**：`xlsm` 和 `sheet` 参数无默认值，意味着"真实 benchmark"路径在默认运行下不触发，实际效果存在两条代码路径，但具体分支逻辑无法从当前片段确认。
4. **内部实现不可见**：`build_table_learning_pipeline` 和 `_build_default_bindings` 的具体实现未出现在代码片段中，规则发现算法、CSV diff 机制、脚本生成逻辑均无法评估其完整性或局限。

seed description 及代码片段中未出现 TODO/FIXME/XXX 注释。

> _来源: src/omnicompany/core/pipelines.py:L143-L168, seed_description_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 1 code anchors (1585 chars), 0 plan docs (0 chars), 25 kb refs
