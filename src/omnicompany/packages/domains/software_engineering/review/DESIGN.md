<!-- [OMNI] origin=claude-code domain=packages/domains/software_engineering/review ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:domains.software_engineering.review.design_spec.md" -->

# review · 设计文档

## 状态
- **版本**: V1 (2026-04-25 从 skeleton 升级，固化多 Agent 交叉审查管线与数据契约)
- **成熟度**: active
- **下一步**: 接入 `equiv_test` 域包的等价性验证作为 `finding_validator` 的前置校验钩子；优化 `sufficiency_judge` 的上下文完备性评分阈值，支持按语言/项目类型动态适配。

## 核心目的
负责代码变更的**多 Agent 交叉审查**，将原始 git diff 转化为结构化审查报告与裁决结论。
本包解决：
- 自动化收集 diff 及相关上下文（调用链、Imports、测试覆盖缺口）
- 通过“异步扫描→多 Agent 辩论链→冲突裁决”机制识别代码缺陷与改进建议
- 输出标准化审查产物（`findings` / `validated` / `report`），并严格隔离中间辩论态与终局结论
本包不解决：
- 不执行代码修改或自动修复（修复属 `implement` / `lang_rewrite` 域）
- 不管理长期架构演进决策或设计模式评审（属 `design` 域）
- 不运行测试用例或生成覆盖率数据（仅消费已有覆盖率摘要）

## 核心接口
- **数据契约/格式** ([formats.py](formats.py))
  - `FORMATS`: 定义 `sw_review.diff`, `sw_review.context`, `sw_review.test-coverage`, `sw_review.review-context`, `sw_review.findings`, `sw_review.validated-findings`, `sw_review.report` 七类 Material 格式。
- **路由与节点实现** ([routers.py](routers.py))
  - `DiffCollectorRouter`, `ContextGathererRouter`, `TestSearcherRouter`: 负责确定性信息收集。
  - `SufficiencyJudgeRouter`, `DeepReviewerRouter`, `FindingValidatorRouter`: 负责 LLM 驱动的语义判断与交叉验证。
  - `ReportFormatterRouter`: 负责最终报告聚合与格式化。
- **管线拓扑定义** ([team.py](team.py))
  - `build_team() -> TeamSpec`: 构建包含 7 个节点与 1 条上下文回路的 DAG。定义节点类型（ANCHOR / SOFT / DETERMINISTIC）与成熟度。
- **运行时绑定** ([run.py](run.py))
  - `build_bindings(input_dict) -> dict[str, Router]`: 将节点 ID 映射至具体 Router 实例，供 runtime 调度消费。
- *(注：原 `pipeline.py` 已标记 DEPRECATED，统一迁移至 `team.py` 的 TeamSpec 模式)*

## 架构决策
### D1 · 确定性收集与 LLM 判断解耦
**决策**: 将管线前 3 步（`diff_collector`, `context_gatherer`, `test_searcher`）实现为确定性（HARD）节点，后 3 步（`sufficiency_judge`, `deep_reviewer`, `finding_validator`）实现为软判断（SOFT/LLM）节点，最后 1 步为确定性格式化。
**理由**: 上下文收集依赖静态代码分析与文件系统读取，需保证 100% 准确且可缓存；LLM 仅用于消耗已构建的完备上下文进行推理，避免“垃圾进垃圾出”与上下文窗口浪费。明确边界便于独立优化收集器性能与调试 LLM Prompt。

### D2 · 上下文完备性反馈回路设计
**决策**: `sufficiency_judge` 判定上下文不足时，不直接失败，而是触发回路由至 `context_gatherer` 进行二次挖掘（`PARTIAL → 回路`），仅当连续失败或明确拒绝时流入下游。
**理由**: 代码变更常涉及深层调用或间接依赖，单次静态扫描易遗漏。回路机制以代价可控的方式提升审查质量，避免 Agent 因信息缺失产生幻觉审查。配合 `manifest.yaml` 中的 `debates/` 目录隔离中间态，防止无限循环。

### D3 · 终局产物与中间态严格物理隔离
**决策**: 审查过程中的原始分歧、未验证发现写入临时/辩论目录，仅 `validated-findings` 与最终 `report` 落盘至 `findings/` 目录，并受 `manifest.yaml` 的 `aging_policy` 与 `size_limits` 约束。
**理由**: 多 Agent 辩论会产生大量冗余中间数据。隔离策略确保下游消费者（如 CI 报告生成、技术债登记）只读取已裁决的稳定产物，同时通过自动老化策略控制存储膨胀，符合 OMNI-005/011 数据落盘规范。

## 数据流 / 拓扑
```
[输入] (git diff / 变更描述)
     ↓
diff_collector (HARD) → 产出 sw_review.diff
     ↓
context_gatherer (HARD) → 补充 Imports/调用链/相邻代码 → 产出 sw_review.context
     ↓
test_searcher (HARD) → 检索关联测试与覆盖缺口 → 产出 sw_review.test-coverage
     ↓
┌── sufficiency_judge (SOFT/LLM) ──────────────────────────────────────┐
│       ├─ (SUFFICIENT) → 流向下游                                     │
│       └─ (INSUFFICIENT / PARTIAL) → 触发回路 → context_gatherer ↑──┘
│                                                                       ↓
├─ deep_reviewer (SOFT/LLM) → 多维度静态审查 → 产出 sw_review.findings  ↓
│                                                                       ↓
├─ finding_validator (SOFT/LLM) → 交叉验证/去重/置信度加权 → 产出 sw_review.validated-findings
│                                                                       ↓
└─ report_formatter (DETERMINISTIC) → 聚合为 Markdown/JSON 审查报告 → 产出 sw_review.report → (EMIT/落盘)
```

## 已知局限
- **局限 1**: `sufficiency_judge` 的“充分性”阈值固定 — 当前依赖硬编码的 Prompt 判定上下文是否完备，对大型单仓或复杂跨模块变更易误判（过严导致死循环，过松导致审查浅层化）。
  **升级路径**: 引入项目级/语言级自适应评分矩阵，从历史审查成功的上下文规模中学习动态阈值；或在 `TeamSpec` 中为不同节点暴露 `max_retry` 与 `fallback_threshold` 配置项。
- **局限 2**: `test_searcher` 仅覆盖显式命名/路径关联 — 当前测试搜索策略依赖文件名匹配与基础导入分析，无法识别动态测试框架（如参数化测试、基于 Fixture 的间接调用）的覆盖缺口。
  **升级路径**: 对接 `tdd` 或 `verify` 域包的 AST/IR 级测试依赖分析器，替换当前基于文本的启发式搜索；或在 `formats.py` 中扩展 `test-coverage` 字段，支持接入外部覆盖率工具（如 `pytest-cov` XML）的解析器。
- **局限 3**: 多 Agent 辩论缺乏可解释的冲突溯源 — 当多个 Reviewer 给出矛盾发现时，`finding_validator` 仅做加权合并，未保留完整的辩论链供人工追溯。
  **升级路径**: 在 `sw_review.validated-findings` 格式中增加 `debate_trace` 字段（指向 `debates/` 下的 JSONL 记录）；后续在 `manifest.yaml` 中启用 `debates/` 子目录的自动持久化策略，并对外暴露轻量级冲突查看 CLI。

## 参考资料
- 关联格式定义: [formats.py](formats.py)
- 关联路由实现: [routers.py](routers.py)
- 关联拓扑构建: [team.py](team.py)
- 关联运行时绑定: [run.py](run.py)
- 关联数据清单与老化策略: .omni/manifest.yaml
- 兄弟包边界: [design/DESIGN.md](../design/DESIGN.md) (架构设计) / [implement/DESIGN.md](../implement/DESIGN.md) (自动修复) / [tdd/DESIGN.md](../tdd/DESIGN.md) (测试覆盖增强)
- 框架规范: `docs/standards/distributed-docs.md` (§三 内容类型矩阵, §四 放置规则)
- 协议层规范: `omnicompany/protocol/format.py`, `omnicompany/protocol/team.py`, `omnicompany/protocol/anchor.py`