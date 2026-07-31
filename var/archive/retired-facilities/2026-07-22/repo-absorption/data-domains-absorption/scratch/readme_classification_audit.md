# README 能力五分类独立审计报告

**时间**：2026-04-18 00:11
**审计员**：qwen3.6-plus (caller=ec5.readme_audit)
**LLM 耗时**：101.4s

## 总体判断

- **一致性评分**：`medium`
- **结论**：README 的五分类在核心意图与主干模块映射上逻辑自洽，概念划分具有指导意义。但与实际目录树比对后发现显著覆盖缺口：约半数 `packages/services/*` 子模块以及 `cli/`、`tools/`、`tracing/`、`runtime/signals/` 未被分类表收录，且业务域与分类体系呈平行关系而非包含关系。同时，`info_audit`、`routing` 等模块在诊断/执行/协议间存在天然双重属性。建议补充缺失模块归类与演进状态标注，并适度收敛分类边界，即可实现文档索引与代码结构的精准对齐。

## 覆盖性问题

- **cli/** — 命令行入口与Agent交互界面未被归入任何一类，作为触发管线与查询状态的直接通道，应归属 Execution 或独立为 Interface 类。
- **tracing/** — 链路追踪基础设施未分类。它是 Learning 下 `trace_induction/` 的原始数据源，也是 Persistence 下 `bus/` 的观测依赖，应明确归属。
- **tools/** — 内置工具库未分类。作为节点执行时的原子能力扩展，逻辑上属于 Execution 层的工具供给面。
- **packages/services/knowledge/** — 目录名与功能暗示知识管理/沉淀，与 Learning 分类高度吻合，但 README 分类表遗漏。
- **packages/services/evolution/** — 可能涉及管线/架构自演进机制，未被归类，可能属于 Learning 或 Execution。
- **packages/services/registry/** — 服务/管线注册中心是运行时发现与依赖解析的核心，未归类，应归属 Protocol 或 Execution 基础设施。
- **runtime/signals/** — 实际存在于目录树，但 README 将信号系统描述归入 `primitives/`，未在分类表中体现独立路径。

## 归属明确性（边界模糊）

- **runtime/info_audit/** — 当前归类 `Diagnosis`，也可归 `Persistence`：README 自身已指出 `audit_store.py` 归属 Persistence。该模块同时承担运行时探针（诊断）与审计落盘（持久化），天然跨两类能力。
- **packages/services/lap_auditor/** — 当前归类 `Diagnosis`，也可归 `Protocol`：审计 LAP 协议合规性，本质是契约守护与规则校验，既可视为系统健康诊断，也可视为 Protocol 层的强制执法组件。
- **runtime/routing/** — 当前归类 `Execution`，也可归 `Protocol`：路由决策强依赖 `protocol/` 定义的 Format/Anchor 契约与映射规则，属于契约驱动的调度逻辑，贴近 Protocol 范畴。
- **packages/services/repair/** — 当前归类 `Diagnosis`，也可归 `Execution`：若该模块负责自动修复缺陷，则属于闭环执行动作；若仅定位问题根因，则属 Diagnosis。名称未明确其主动性边界。

## 分类本身的逻辑问题

- 五分类采用‘能力视角’，但遗漏了‘业务应用层’（`packages/domains/`）和‘交互入口层’（`cli/`、`tools/`），导致能力地图与物理代码架构存在维度错位，顶层导航存在断层。
- Persistence 类别将底层数据存储与事件流（`bus/`、`storage/`）与上层可视化看板（`dashboard/`）合并，两者技术栈、生命周期与更新频率差异较大，建议拆分为‘数据/事件层’与‘可观测性/UI层’。
- Protocol 类别将严格契约定义（`protocol/`）与通用基础设施（`core/`）混为一谈。`core/` 包含 `config`、`dispatch`、`registry` 等运行时胶水代码，并非纯契约，分类粒度偏粗，易造成架构边界模糊。

## 建议修订

- 在 README 能力分类表末尾增加‘其他服务（待归类/演进中）’章节，明确列出 `packages/services/` 下未提及的 10+ 模块（如 `cleanup_bot/`, `evolution/`, `knowledge/`, `registry/`, `repair/`, `repo_architect/` 等）的当前定位或占位状态。
- 将 `cli/` 和 `tools/` 正式纳入分类表，建议挂靠于 Execution 类下，并标注为‘交互接口与原子能力’，以覆盖全链路入口。
- 在 `runtime/info_audit/` 和 `packages/services/lap_auditor/` 的表格行中增加‘交叉引用’备注，说明其跨分类属性（如：‘注：诊断逻辑归本类，落盘/协议校验归对应类’）。
- 将 `tracing/` 明确标注为 Persistence 层的底层依赖项，并在 `bus/DESIGN.md` 中说明事件采集与链路追踪的数据流向关系。

---

## 原始 LLM JSON 输出

```json
{
  "consistency_score": "medium",
  "coverage_issues": [
    {
      "module": "cli/",
      "reason": "命令行入口与Agent交互界面未被归入任何一类，作为触发管线与查询状态的直接通道，应归属 Execution 或独立为 Interface 类。"
    },
    {
      "module": "tracing/",
      "reason": "链路追踪基础设施未分类。它是 Learning 下 `trace_induction/` 的原始数据源，也是 Persistence 下 `bus/` 的观测依赖，应明确归属。"
    },
    {
      "module": "tools/",
      "reason": "内置工具库未分类。作为节点执行时的原子能力扩展，逻辑上属于 Execution 层的工具供给面。"
    },
    {
      "module": "packages/services/knowledge/",
      "reason": "目录名与功能暗示知识管理/沉淀，与 Learning 分类高度吻合，但 README 分类表遗漏。"
    },
    {
      "module": "packages/services/evolution/",
      "reason": "可能涉及管线/架构自演进机制，未被归类，可能属于 Learning 或 Execution。"
    },
    {
      "module": "packages/services/registry/",
      "reason": "服务/管线注册中心是运行时发现与依赖解析的核心，未归类，应归属 Protocol 或 Execution 基础设施。"
    },
    {
      "module": "runtime/signals/",
      "reason": "实际存在于目录树，但 README 将信号系统描述归入 `primitives/`，未在分类表中体现独立路径。"
    }
  ],
  "boundary_ambiguities": [
    {
      "module": "runtime/info_audit/",
      "current_class": "Diagnosis",
      "alternative_class": "Persistence",
      "reason": "README 自身已指出 `audit_store.py` 归属 Persistence。该模块同时承担运行时探针（诊断）与审计落盘（持久化），天然跨两类能力。"
    },
    {
      "module": "packages/services/lap_auditor/",
      "current_class": "Diagnosis",
      "alternative_class": "Protocol",
      "reason": "审计 LAP 协议合规性，本质是契约守护与规则校验，既可视为系统健康诊断，也可视为 Protocol 层的强制执法组件。"
    },
    {
      "module": "runtime/routing/",
      "current_class": "Execution",
      "alternative_class": "Protocol",
      "reason": "路由决策强依赖 `protocol/` 定义的 Format/Anchor 契约与映射规则，属于契约驱动的调度逻辑，贴近 Protocol 范畴。"
    },
    {
      "module": "packages/services/repair/",
      "current_class": "Diagnosis",
      "alternative_class": "Execution",
      "reason": "若该模块负责自动修复缺陷，则属于闭环执行动作；若仅定位问题根因，则属 Diagnosis。名称未明确其主动性边界。"
    }
  ],
  "logical_issues": [
    "五分类采用‘能力视角’，但遗漏了‘业务应用层’（`packages/domains/`）和‘交互入口层’（`cli/`、`tools/`），导致能力地图与物理代码架构存在维度错位，顶层导航存在断层。",
    "Persistence 类别将底层数据存储与事件流（`bus/`、`storage/`）与上层可视化看板（`dashboard/`）合并，两者技术栈、生命周期与更新频率差异较大，建议拆分为‘数据/事件层’与‘可观测性/UI层’。",
    "Protocol 类别将严格契约定义（`protocol/`）与通用基础设施（`core/`）混为一谈。`core/` 包含 `config`、`dispatch`、`registry` 等运行时胶水代码，并非纯契约，分类粒度偏粗，易造成架构边界模糊。"
  ],
  "suggested_revisions": [
    "在 README 能力分类表末尾增加‘其他服务（待归类/演进中）’章节，明确列出 `packages/services/` 下未提及的 10+ 模块（如 `cleanup_bot/`, `evolution/`, `knowledge/`, `registry/`, `repair/`, `repo_architect/` 等）的当前定位或占位状态。",
    "将 `cli/` 和 `tools/` 正式纳入分类表，建议挂靠于 Execution 类下，并标注为‘交互接口与原子能力’，以覆盖全链路入口。",
    "在 `runtime/info_audit/` 和 `packages/services/lap_auditor/` 的表格行中增加‘交叉引用’备注，说明其跨分类属性（如：‘注：诊断逻辑归本类，落盘/协议校验归对应类’）。",
    "将 `tracing/` 明确标注为 Persistence 层的底层依赖项，并在 `bus/DESIGN.md` 中说明事件采集与链路追踪的数据流向关系。"
  ],
  "overall_judgment": "README 的五分类在核心意图与主干模块映射上逻辑自洽，概念划分具有指导意义。但与实际目录树比对后发现显著覆盖缺口：约半数 `packages/services/*` 子模块以及 `cli/`、`tools/`、`tracing/`、`runtime/signals/` 未被分类表收录，且业务域与分类体系呈平行关系而非包含关系。同时，`info_audit`、`routing` 等模块在诊断/执行/协议间存在天然双重属性。建议补充缺失模块归类与演进状态标注，并适度收敛分类边界，即可实现文档索引与代码结构的精准对齐。"
}
```
