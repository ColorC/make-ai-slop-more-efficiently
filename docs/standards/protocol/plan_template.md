<!-- [OMNI] origin=ai-ide domain=standards/protocol ts=2026-05-05T23:55:00Z type=doc status=active agent=ai-ide -->
<!-- [OMNI] summary="plan.md 标准模板 — 给 PlanDiagnosticAgent 读的'应满足什么'参考. 核心三块: 需求 / 产物 / 验收. 不达标处置走技术债不阻断" -->
<!-- [OMNI] why="诊断重制阶段 2 后续 2: PlanDiagnosticAgent 需要一份'plan.md 应长什么样'的标准, 不抽硬规则枚举, 让 agent 读模板 + 实际 plan.md 自然语言判完成度" -->
<!-- [OMNI] tags=standards,protocol,plan,template,doctor -->
<!-- [OMNI] material_id="material:standards.protocol.plan_template.md" -->

# plan.md 标准模板

> **权威**: [`concepts/plan.md`](../concepts/plan.md)（2026-06-13 起 plan 唯一权威规范, 用户裁决"冲突以新为准"）——本文件是其 protocol 层**模板细则**（plan.md 章节硬下限），冲突时以 concepts/plan.md 为准
> **头部字段**: 按 concepts/plan.md §三 — 文件顶部唯一 yaml frontmatter（平铺字段 + binding 块同居一块），之后才是 OmniMark 头注释. 改本文件前必同步看 concepts/plan.md 是否一致 (元规范第 1 条违例自查)
> **相关**: `distributed-docs.md` (位置), [`design_md_template.md`](design_md_template.md) (DESIGN 模板), [doctor 计划型诊断](../../../src/omnicompany/packages/services/_diagnosis/doctor/DESIGN.md), `standards_meta.md` (立规范的元规范)
> **位置**: `docs/plans/<topic>/[YYYY-MM-DD]<plan-name>/plan.md`
> **设施统一计划**: 若计划目标是消灭二重权威/统一核心设施, 必须补 `authority-confirmation.md` 与 `autonomous-execution-rules.md` 两个同目录文件; 当前样例见 authority-confirmation.md / autonomous-execution-rules.md。

---

## 一 · 为什么要有这份模板

两条理由:

1. **agent 跟人都能按固定章节扫**: 找产物清单? 直接跳"二 · 产物清单". 自由结构 = 每份计划都要重新摸结构.
2. **PlanDiagnosticAgent 能读出'计划做完没'**: 跟规范型/假设型/样例型同模式 — 拿这模板 + 实际 plan.md 让 LLM 自然语言判完成度. 没固定结构, 判定无锚.

不强抽硬规则, 这份模板是参考. 偏离的 plan.md PlanDiagnosticAgent 会标 finding 提示, 不阻断 (走技术债).

---

## 1.5 · 分文件规则

plan 不必是单个 plan.md 一体. 当计划内容较多时, 按以下拆分点分文件, 每个文件职责单一:

```
docs/plans/<topic>/[YYYY-MM-DD]<plan-name>/
  plan.md            # 主文件: 需求清单 + 产物清单 + 风险跟假设 (plan 是什么 + 要什么)
  brief.md           # 核心摘要: 退出条件 + 当前阶段 + 执行约束 (compact 后载入用, 必须短)
  roadmap.md         # 路线图: 阶段拆分 + 每阶段产物子集 (怎么做)
  decisions.md       # 决策日志: 拍板记录 + 路线变更 (为什么改)
  acceptance.md      # 验收标准: 静态 + 动态验收条件 (怎么算完)
  authority-confirmation.md      # 核心设施统一/唯一权威类计划必备: 一次性集中确认方向
  autonomous-execution-rules.md  # 核心设施统一/唯一权威类计划必备: 长程执行门禁 + guard 验证
  compact_summary_*.md  # compact 总结 (按已有协议)
```

**拆分原则**:

- **plan.md 始终是入口**, 包含需求清单和产物清单 — 这是 plan 的核心身份, 不可拆出
- **brief.md 必须存在** — 这是防遗忘机制的载体 (见 concepts/plan.md §4.3), compact 后首先载入这个文件
- **其余文件按需拆分** — 内容少时可以全写在 plan.md 里不拆, 内容多时按上面的职责边界拆
- 每个拆出的文件在 plan.md 中用相对链接引用: `路线图` / `决策日志`
- OmniMark 头只在 plan.md 上写, 拆出的子文件不重复写头

**不拆的情况**: 需求 ≤ 5 条 + 阶段 ≤ 3 个的小计划, 全写 plan.md 一个文件即可.

---

## 二 · 标准结构 (复制这份开始写)

```markdown
---
title: <计划标题>
date: '<YYYY-MM-DD>'
work_type: <refactor|infra-convergence|...>
status: active
exit_criteria:
  - <退出条件 1>
binding:
  workspace: <项目相对路径>
  packages: []
  targets: []
applicable_standards: []
expected_completion: <YYYY-MM-DD>
---

<!-- [OMNI] origin=<origin> domain=plans/<topic> ts=<ISO8601> type=plan status=<draft|active|done|archived> -->
<!-- [OMNI] summary="<本计划 1 句话总结>" -->
<!-- [OMNI] why="<立这计划为啥, 解决什么问题>" -->
<!-- [OMNI] tags=plan,<topic>,<其他相关>... -->
<!-- [OMNI] material_id="material:plans.<topic>.<plan-name>.plan.md" -->

# <计划主题> · <计划名> 计划书

> **立计划日**: <YYYY-MM-DD>
> **决策依据**: <用户明示 / 上游计划继承 / 经验沉淀>
> **范围**: <这计划改/动什么>
> **不在范围**: <明确写排除什么, 防 scope creep>

---

## 一 · 需求清单

> 这计划要满足的具体需求. 每条独立可对账, 不写"做好X" 写"X的具体表现是 Y".

1. **<需求 ID>**: <一句话需求描述>. 验收: <怎样算满足>
2. ...

需求来源: <用户明示 / 上游计划 / 规范要求>. 各条标来源.

## 二 · 产物清单

> 计划做完应有的代码 / 文档 / 数据产物. 每条含 path + 形态描述, agent 能查到存在性.

| ID | 类型 | 路径 (形态) | 完成判定 |
|---|---|---|---|
| P-1 | 代码 | `src/omnicompany/packages/services/<X>/<Y>.py` (含 class <Z>) | 文件存在 + 含指定 class + 有 docstring |
| P-2 | 文档 | `docs/<X>/<Y>.md` | 文件存在 + 含指定章节 |
| P-3 | Material | `<X>/formats.py` 含 `<MATERIAL_NAME>` 常量 | grep 找到该常量 |
| ... | | | |

**产物归属**: 每条产物属于 `<X>` service 或 `<Y>` plan 区. 不允许产物落到无主区.

## 三 · 验收标准

> 计划做完, 怎么证明做好了. 分静态 + 动态.

### 3.1 静态验收 (能查文件 / 找代码)

- [ ] 全部产物清单 (二节) 文件存在
- [ ] 关键 class / 函数 / Material 常量按描述存在
- [ ] OMNI 头齐 (按 `omni-header.md` 规范)
- [ ] 文档章节齐 (按 [`design_md_template.md`](design_md_template.md) 等模板)

### 3.2 动态验收 (能跑 / 能复现)

- [ ] 入口命令: `<具体命令, 例 'omni doctor run-plan-diagnosis'>`
- [ ] 跑通预期: <预期输出 / 状态变化, 例 '事件总线产 verdict 事件含 N findings'>
- [ ] 失败模式 (没跑过的话): <可能的失败 + 怎样判定>

## 四 · 路线图 (按阶段)

> 阶段化分解. 每阶段一句话目标 + 关键产物子集.

### 阶段 0 · 准备 (本 commit)
- 写本 plan.md
- 用户审过路线图

### 阶段 1 · <阶段名>
- <一句话>: <交付什么>
- 产物子集: P-1, P-2

### 阶段 N · ...

## 五 · 不达标处置

> 计划没全做完 / 某产物有债务时, 怎么处置.

- **优先级 A 产物缺失** (例 主功能没实现): 阻断, 不允许标 done
- **优先级 B 产物缺失** (例 测试没写): 走技术债 (`tech_debt/`), 计划仍可标 done 但需有 debt 登记
- **产物存在但有问题** (能跑但有性能/稳定性 bug): 走技术债 + finding, 不阻断

漂移概念取消: 不写"跟需求漂移", 写"跟需求/规范不一致" 或 "技术债登记".

## 六 · 风险跟假设

> 已识别的风险 + 计划成立的隐含假设.

- **R-1**: <风险描述>. 缓解: <怎么应对>
- **A-1** (假设): <隐含假设>. 验证: <怎么验>

## 七 · 决策日志

> 计划立时跟用户互动的拍板记录, 改路线时回写.
> 内容多时拆到 `decisions.md`, 此处保留链接: 决策日志

- <YYYY-MM-DD> · <决策内容> (用户拍板 / agent 自提议)

## 附录 · <如有>

> 词汇表 / 跨引用 / ad-hoc 备忘.
```

---

### brief.md 模板 (必须存在)

brief.md 是 plan 的核心摘要, compact 后作为恢复上下文的第一优先载入项. 必须足够短 (建议 ≤ 30 行), 信息完整.

```markdown
# <计划名> · 核心摘要

## 退出条件
> 从验收标准提炼, 这个计划做到什么程度算完.
- <条件 1>
- <条件 2>

## 当前阶段
> 路线图中正在执行哪个阶段, 该阶段的关键产物.
- 阶段: <阶段 N · 名称>
- 关键产物: <本阶段要交付什么>
- 进度: <一句话当前状态>

## 执行约束
> 不能做什么 / 必须遵守什么, 从风险和假设提炼.
- <约束 1>
- <约束 2>
```

> **维护规则**: 每次阶段切换时更新"当前阶段"部分. brief.md 的退出条件和执行约束在 plan 生命周期内应保持稳定, 频繁变更说明 plan 本身需要重新审视.

---

## 三 · 各节硬下限 (不达标 finding)

PlanDiagnosticAgent 看这些点判 finding:

| 项 | 必须有 |
|---|---|
| OMNI 头 | summary / why / tags / material_id (按 `omni-header.md`) |
| brief.md | 退出条件 + 当前阶段 + 执行约束 三节齐 (≤ 30 行) |
| 一 · 需求清单 | ≥1 条需求, 每条含 ID + 验收 |
| 二 · 产物清单 | ≥1 条产物, 每条含 path + 完成判定 |
| 三 · 验收标准 | 静态 + 动态各 ≥1 条 |
| 五 · 不达标处置 | 至少声明优先级 / 走债务 / 阻断 三档判定 |

少一节 → finding kind=plan, applied_standards=[本模板 path:节]. 但 agent 自然语言判, 不抽 ast 强校 (硬规则归 guardian).

---

## 四 · PlanDiagnosticAgent 怎么用本模板

1. 读 plan.md (待诊断 plan)
2. 读本模板 (作参考)
3. 自然语言判:
   - **结构合规**: plan.md 是否按本模板结构 (一-七节齐)
   - **静态产物存在性**: 二节产物清单的文件 path 是否真存在
   - **动态完成度**: 三节动态验收的入口能否跑 (V1 才接, V0 跳过)
   - **不达标项处置**: 五节是否声明了不达标项的处置 (技术债 vs 阻断)

每条不合规走 finding kind=plan + applied_standards=[本模板路径:节].

---

## 五 · 合规样本

合规样本: [`docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/samples/sample_compliant_plan_exemplar_library.md`](../../plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/samples/sample_compliant_plan_exemplar_library.md)

> 跟元规范 v1 (`standards_meta.md`) 第 2 条配套立的真合规样本. 本样本逐条符合本模板 (一-七节齐 + OMNI 头齐 + 产物清单 + 静态/动态验收 + 三档处置), **修本模板前必同步改样本**, 不一致是本模板出问题信号.

> 注: 本模板 2026-05-05 立, 历史 plan.md 可能不全合本模板 (如 `本计划自己 plan.md`). PlanDiagnosticAgent 看历史 plan 时按 finding 标差异, 不阻断不重写历史.

> **历史教训** (元规范触发): 本模板 v1 立时 §五 错指"本计划用的 plan.md" 作合格例, 实测它不合规 (PlanDiagnosticAgent dogfood 找 6 处 finding). 这促成元规范 v1 把"必有真合规样本" 上升为立规范者硬底线. 见 `standards_meta.md` §五 反例.

---

## 六 · 跟其他规范的关系

- `design_md_template.md` 是 DESIGN.md 模板 (服务/包级架构) — plan.md 是计划级
- `distributed-docs.md` 定 plan.md 位置: `docs/plans/<topic>/[YYYY-MM-DD]<plan-name>/`
- `l2_session_summary_protocol.md` 计划进行中 compact 时的总结协议 — 跟本模板互补不冲突 (compact summary 不替代 plan.md)
