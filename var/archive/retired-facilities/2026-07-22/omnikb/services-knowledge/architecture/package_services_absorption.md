# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T10:30:51Z
---
omnikb_type: karch
id: kb.arch.package.services_absorption
name: 'Package: packages/services/absorption'
tags:
- topic.package
- layer.services
- domain.absorption
- architecture
maturity: living
summary: omnicompany.packages.services.absorption — Repo Absorption Workflow.
scope: omnicompany
code_anchors:
- src/omnicompany/packages/services/absorption/__init__.py
- src/omnicompany/packages/services/absorption/pipeline.py
- src/omnicompany/packages/services/absorption/routers.py
- src/omnicompany/packages/services/absorption/run.py
- src/omnicompany/packages/services/absorption/formats.py
---

# Package: packages/services/absorption

> **id**: `kb.arch.package.services_absorption` · **type**: karch · **maturity**: living

## Why this exists

`omnicompany.packages.services.absorption` 包的存在是为了把外部 GitHub agent/AI 框架仓库系统化地"模仿 → 抄写 → 反省 → 利用 → 吸纳"转化为纯六元 LAP 代码，使其进入 Omnicompany 而不污染框架的设计哲学。其核心动机是结构化地消化外部知识，而非直接复制粘贴代码或引入框架依赖。

包内 `__init__.py` 的模块文档明确指出，该工作流对照七份设计文档（`README.md`、`01_PRIOR_ART_AND_LANDSCAPE.md` 至 `07_STAGED_ROADMAP.md`）分阶段实施。当前代码处于 Stage 3d，已从最初 Stage 1 的 4 节点骨架扩展为 7 节点线性 DAG。早期 Stage 1 的目标仅是验证"包结构 + 注册 + Format 链 + 路由全链路通畅"，所有 Router 均为桩实现，不调 LLM、不联网、不写盘。Stage 3d 则解决了前一轮暴露出的七个具体问题，包括数据抓取太薄、缺乏 Omnicompany 自身能力对照、无迭代式文件读取、证据链薄弱、无完整性审计、结果不可读以及 confidence 不诚实。

> _来源: `src/omnicompany/packages/services/absorption/__init__.py`, `src/omnicompany/packages/services/absorption/routers.py`_

## How it works

管线以 7 节点线性 DAG 形式运转，由 `build_survey_pipeline()` 在 `pipeline.py` 中构造 `PipelineSpec`，每个节点用 `PipelineNode` 描述，路由逻辑用 `AnchorSpec` + `ValidatorSpec` + `Route` 表达。

各节点按顺序执行：

- **Node 01 `target_intake`**（`TargetIntakeRouter`）：接收 `absorption.user_request`，解析 owner/name、校验 profile 枚举，分配全局唯一 `absorption_id`，产出 `absorption.intake`。`ValidatorKind.HARD`，失败立即 `RouteAction.HALT`。
- **Node 02 `repo_facade_fetcher`**（`RepoFacadeFetcherRouter`）：调用 `_gh_api` / `_gh_api_json` 通过 `gh` CLI 抓取递归目录树、全量 README（`_decode_base64_readme`）、贡献者、近期 release、语言/commit 频率，产出 `absorption.facade_card`。任一 repo 拉取失败即 HALT。
- **Node 03 `omnicompany_snapshot_fetcher`**（`OmnicompanySnapshotFetcherRouter`）：调用 `build_snapshot` / `snapshot_stats`（来自 `absorption.snapshot` 子模块）扫描本仓 packages/core/runtime 自身能力，产出 `absorption.omnicompany_snapshot`。
- **Node 04 `landmark_picker`**（`LandmarkPickerRouter`，位于 `landmark_picker.py`）：基于 `AgentNodeLoop` 最多 50 轮迭代，LLM 阅读门面信息并挑选地标，产出含 `landscape_sketches`、`capability_gaps`、探索轨迹的 `absorption.landmark_list`。`ValidatorKind.SOFT`。
- **Node 05 `coverage_auditor`**（`CoverageAuditorRouter`）：比对总 tree 与已读文件列表，产出 `absorption.coverage_audit`。
- **Node 06 `triage_gate`**（`TriageGateRouter`）：至少 1 个 tier-1 地标才放行，落盘 pool，产出 `absorption.triaged_landmarks`。
- **Node 07 `report_writer`**（`ReportWriterRouter`）：`TransformerSpec` 节点（`RULE`），将结构化结果转为 Markdown 报告，产出 `absorption.report` 并 `EMIT`。

`routers.py` 还持有解析 GitHub repo URL 的 `_parse_repo` 辅助函数（支持 HTTP/SSH/短名三种模式）、`_absorption_artifact_dir`（调用 `resolve_db_dir("absorption")` 确定落盘路径）等通用工具。

基于当前代码片段只能看到 Node 03 的 `AnchorSpec` 开头部分，Node 04-07 的完整 `PipelineNode` 定义被截断，完整机制需读 `pipeline.py` 后续部分及 `landmark_picker.py`。

> _来源: `src/omnicompany/packages/services/absorption/pipeline.py`, `src/omnicompany/packages/services/absorption/routers.py`, `src/omnicompany/packages/services/absorption/run.py`_

## Public surface

该包通过 `__init__.py` 的 `__all__` 对外暴露以下接口：

**Format 对象（在 `formats.py` 中定义）：**
- `ABSORPTION_USER_REQUEST`（id: `absorption.user_request`）
- `ABSORPTION_INTAKE`（id: `absorption.intake`）
- `ABSORPTION_FACADE_CARD`（id: `absorption.facade_card`）
- `ABSORPTION_OMNICOMPANY_SNAPSHOT`（id: `absorption.omnicompany_snapshot`）
- `ABSORPTION_LANDMARK_LIST`（id: `absorption.landmark_list`）
- `ABSORPTION_COVERAGE_AUDIT`（id: `absorption.coverage_audit`）
- `ABSORPTION_TRIAGED_LANDMARKS`（id: `absorption.triaged_landmarks`）
- `ABSORPTION_REPORT`（id: `absorption.report`）
- `ALL_FORMATS`（全部 Format 的集合）
- `register_formats`（注册函数）

**管线构造函数：**
- `build_survey_pipeline() -> PipelineSpec`：构造 7 节点 DAG 的 `PipelineSpec`

**Router 绑定函数：**
- `build_survey_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]`：返回节点 id 到 Router 实例的映射字典

**管线注册表：**
- `PIPELINES`：管线注册对象（具体类型在当前片段中未完整展示）

> _来源: `src/omnicompany/packages/services/absorption/__init__.py`, `src/omnicompany/packages/services/absorption/run.py`_

## Internal structure

该包按职责拆分为以下子模块：

| 文件 | 职责 |
|------|------|
| `__init__.py` | 包入口，聚合并重新导出 Format、管线构造函数、绑定函数 |
| `formats.py` | 定义 8 个 `Format` 实例及 `register_formats`，描述各节点的输入/输出数据类型契约 |
| `pipeline.py` | 用 `PipelineNode` / `AnchorSpec` / `PipelineSpec` 描述 7 节点 DAG 拓扑，不持有实现逻辑 |
| `routers.py` | 持有 6 个同步 Router 实现：`TargetIntakeRouter`、`RepoFacadeFetcherRouter`、`OmnicompanySnapshotFetcherRouter`、`CoverageAuditorRouter`、`TriageGateRouter`、`ReportWriterRouter`，以及 `_gh_api`、`_gh_api_json`、`_parse_repo`、`_absorption_artifact_dir` 等辅助函数 |
| `landmark_picker.py` | 独立持有 `LandmarkPickerRouter`（`AgentNodeLoop` 实现），因拉 LLM 依赖而单独成文件 |
| `snapshot.py` | 持有 `build_snapshot` 和 `snapshot_stats`，扫描 Omnicompany 自身能力 |
| `run.py` | 绑定入口，`build_survey_bindings` 通过 lazy import 组装所有 Router，被 `core/pipelines.py` 通过 `_lazy()` 引用 |

`run.py` 的注释明确说明 lazy import 策略：Router 的导入延迟到 `build_survey_bindings()` 调用时，避免 CLI 启动时触发 `LLMClient` / `AgentNodeLoop` 的重依赖加载。

> _来源: `src/omnicompany/packages/services/absorption/__init__.py`, `src/omnicompany/packages/services/absorption/run.py`, `src/omnicompany/packages/services/absorption/routers.py`_

## Files

- `src/omnicompany/packages/services/absorption/__init__.py` — 包入口，聚合并 re-export Format 常量、`build_survey_pipeline`、`build_survey_bindings`、`PIPELINES`，定义 `__all__`
- `src/omnicompany/packages/services/absorption/pipeline.py` — 构造 Stage 3d 7 节点线性 DAG 的 `PipelineSpec`，用 `PipelineNode` / `AnchorSpec` / `ValidatorSpec` / `Route` 描述拓扑，不含实现逻辑
- `src/omnicompany/packages/services/absorption/routers.py` — 持有 6 个同步 Router 实现（Node 1/2/3/5/6/7）及 GitHub API 辅助工具函数
- `src/omnicompany/packages/services/absorption/run.py` — Router bindings 入口，`build_survey_bindings` 通过 lazy import 返回节点名到 Router 的映射，被 core 层调用
- `src/omnicompany/packages/services/absorption/formats.py` — 定义 8 个 `Format` 实例（`absorption.user_request` 至 `absorption.report`）及 `register_formats`，每个 Format 含 JSON Schema

以下文件在代码片段中被引用但未提供内容：
- `src/omnicompany/packages/services/absorption/landmark_picker.py` — 持有 `LandmarkPickerRouter`（AgentNodeLoop 迭代式 LLM 实现）
- `src/omnicompany/packages/services/absorption/snapshot.py` — 持有 `build_snapshot` 和 `snapshot_stats`，扫描 Omnicompany 自身能力快照

> _来源: `src/omnicompany/packages/services/absorption/__init__.py`, `src/omnicompany/packages/services/absorption/pipeline.py`, `src/omnicompany/packages/services/absorption/routers.py`, `src/omnicompany/packages/services/absorption/run.py`, `src/omnicompany/packages/services/absorption/formats.py`_

## Related

与本包直接相关的 KB 条目：

- `kb.arch.pipeline.absorption_survey` — 描述 absorption-survey 管线本身（Stage 1 Survey & Triage），与本包实现的管线对应
- `kb.arch.package.services_knowledge` — OmniKB 知识库服务包，是 Omnicompany 自身能力积累的对应层，`OmnicompanySnapshotFetcherRouter` 扫描的能力集与此相关
- `kb.arch.package.services_lap_auditor` — LAP 规范审计，absorption 产出的 LAP 代码需经过此类审计
- `kb.arch.package.services_pattern_discovery` — 模式发现管线，与 absorption 的"识别地标"动机有概念上的关联
- `kb.arch.package.services_workflow_factory` — 造工作流的工作流，absorption 工作流本身也是在此框架范式下构建的

以下 services 包在结构上与本包平行，属于同层次的服务型管线包，有参考价值：
- `kb.arch.package.services_guardian`
- `kb.arch.package.services_cleanup_bot`
- `kb.arch.package.services_pipeline_ci`

> _来源: KB 已有条目列表_

## Known limitations

基于当前可见代码，可观察到以下已知局限或未实现区域：

1. **所有节点 maturity 均为 `NodeMaturity.HYPOTHETICAL`**：`pipeline.py` 中 Node 01 和 Node 02 明确设置了 `maturity=NodeMaturity.HYPOTHETICAL`，说明节点定义尚未进入可运行/验证状态（Node 03 之后的代码被截断，但注释中也未提示有节点升级为更高 maturity）。

2. **`LandmarkPickerRouter` 独立文件但片段不可见**：`routers.py` 的模块文档明确说明"LandmarkPickerRouter 在 `landmark_picker.py`，AgentNodeLoop"，该文件内容在当前材料中不可见，其具体的 AgentNodeLoop 迭代实现、工具调用定义、停止条件均无法核实。

3. **`snapshot.py` 实现不可见**：`build_snapshot` 和 `snapshot_stats` 的具体扫描逻辑未出现在片段中，只能看到接口，无法确认其扫描 packages/core/runtime 的实际范围和深度。

4. **`formats.py` 片段被截断**：`ABSORPTION_FACADE_CARD` 的 `json_schema` 字段在代码片段末尾被截断，后续的 `ABSORPTION_OMNICOMPANY_SNAPSHOT`、`ABSORPTION_LANDMARK_LIST`、`ABSORPTION_COVERAGE_AUDIT`、`ABSORPTION_TRIAGED_LANDMARKS`、`ABSORPTION_REPORT` 以及 `ALL_FORMATS` 和 `register_formats` 的定义均不可见。

5. **Stage 3d 之后的路线未实现**：`__init__.py` 文档提及七份设计文档（包括 `07_STAGED_ROADMAP.md`），但当前代码仅实现了 Stage 3d（Survey 阶段），Phase B 之后的"抄写 → 反省 → 利用 → 吸纳"各阶段在本包中尚无对应代码。

> _来源: `src/omnicompany/packages/services/absorption/pipeline.py`, `src/omnicompany/packages/services/absorption/routers.py`, `src/omnicompany/packages/services/absorption/__init__.py`, `src/omnicompany/packages/services/absorption/formats.py`_

## Change log

- 2026-04-08 — deep-read by LLM deep_read.py
- source manifest: 5 code anchors (21986 chars), 0 plan docs (0 chars), 25 kb refs
