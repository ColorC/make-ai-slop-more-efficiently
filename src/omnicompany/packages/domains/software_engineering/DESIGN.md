<!-- [OMNI] origin=claude-code domain=domains/software_engineering ts=2026-04-17T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:domains.software_engineering.domain_index.design_doc.md" -->

# software_engineering · 域索引

> **命名兼容注**（2026-04-20）：本文中 `Router` / `Format` / `PipelineSpec` 按 `terminology.md` 对照读作 `Worker` / `Material` / `Team`。protocol 层类名保留原名（契约不变），新代码 import 请用 `from omnicompany.packages.services.omnicompany import Worker, Material, Team`。

## 状态
- **版本**: V1（11 个子包按软件工程阶段聚类；本文件是顶层导航）
- **成熟度**: active（架构就位，各阶段成熟度不均）
- **下一步**: lang_rewrite / lang_rewrite_verifier 两个子包成熟度最高（Rust/TS 翻译已跑通）；design / plan / tdd 子包待 DESIGN.md 填充

## 核心目的

`packages/domains/software_engineering/` 是 **软件工程各阶段的 OmniCompany 化**。回答的问题：

> **软件工程从"计划→设计→测试→实现→审查→验证→等价测试"的每一段能不能用 OmniCompany 管线串起来，让 agent 代替人做每一阶段？**

七阶段映射到子包：

| 阶段 | 子包 | 核心职责 |
|---|---|---|
| 0. 需求计划 | `plan/` | 从需求产出 plan（任务拆分 / 风险评估） |
| 1. 架构设计 | `design/` | 从 plan 产出设计（模块 / 接口 / Format） |
| 2. 测试先行 | `tdd/` | 按设计产出测试用例 + 断言 |
| 3. 编码实现 | `implement/` | 按测试 + 设计写代码 |
| 4. 代码审查 | `review/` | 自动 code review（对标 human reviewer） |
| 5. 验证 | `verify/` | 运行测试 + 静态分析 |
| 6. 等价测试 | `equiv_test/` | 新旧实现等价性验证（重构 / 翻译场景） |

加三个横切子包：
- `debugger/` — 交互式调试（跨阶段）
- `lang_rewrite/` — 跨语言翻译（TS→Rust 等，当前最成熟）
- `lang_rewrite_verifier/` — 翻译结果的等价验证

本目录**不**解决的问题：
- 不是 IDE（不做实时编辑体验）
- 不是 CI（不做 pipeline 执行调度，`runtime/exec/` 负责）
- 不做特定语言专家（各阶段工具语言无关）

## 核心接口（子包清单）

### 七阶段主链
- **[plan/](plan/)** — 计划阶段（无 DESIGN.md，待填充）
- **[design/](design/)** — 设计阶段
- **[tdd/](tdd/)** — TDD 测试生成
- **[implement/](implement/)** — 实现阶段
- **[review/](review/)** — 审查阶段
- **[verify/](verify/)** — 验证阶段
- **[equiv_test/](equiv_test/)** — 等价测试

### 横切子包
- **[debugger/](debugger/)** — 交互式调试（跨阶段）
- **[lang_rewrite/](lang_rewrite/)** — 跨语言翻译（Phase 1 完成 + Phase 2 进行中：Rust 翻译 R-01~R-07 沉淀，TS tsc PASS）
- **[lang_rewrite_verifier/](lang_rewrite_verifier/)** — 翻译等价验证

### 共享子包
- **[_shared/](_shared/)** — 跨阶段共享工具 / Format / primitives

### 生成产物
- **[generated/](generated/)** — agent 产出的中间代码（不提交 src/，由用户 cherry-pick 合入）

## 架构决策

### D1 — 按软件工程阶段聚类，不按技术栈聚类

大多数"AI 写代码"项目按语言（python-gen / typescript-gen）或框架（react-gen / django-gen）组织。本 domain 反其道：

- 按**工程阶段**聚类（plan / design / tdd / implement / review / verify / equiv_test）
- 每阶段跨语言通用（design 阶段的 Format 设计逻辑与 Python / Rust / TS 无关）
- 语言差异在 tools / prompt 层解决，不在目录结构

好处：
- agent 在多语言项目中复用同一阶段的工具
- 新加语言（Go / Swift）不新增 7 套子包
- 阶段模型稳定，易积累经验

### D2 — lang_rewrite 是当前最成熟子包，作为"domain 磨刀石"

跨语言翻译（TS→Rust 是主要实验场景）是最接近端到端的实验：
- 有明确 GT（原 TS 代码的行为）
- 等价验证可自动化（lang_rewrite_verifier）
- 跨语言挑战涵盖"语义保持 / 类型映射 / 错误处理 / 并发模型"等核心工程问题

Rust 翻译 Phase 1 完成 + Phase 2 进行中（R-01~R-07 沉淀，types.rs 供给注入生效，TS Phase 3 tsc PASS）。这些经验反哺到其他阶段。

### D3 — generated/ 目录隔离：agent 不直接改 src/

所有 agent 代码产出先写到 `generated/` 子目录：
- 按 task_id / timestamp 分子目录，不覆盖
- 人工 cherry-pick 合入 src/（类似 Stage 3 proposal 手工合入机制）
- 失败产物不污染主仓，方便回滚

这是与 absorption Stage 3 / `services/workflow_factory/` 同源的设计：**agent 产物永远走生成目录 + 人审合入**，不直接改 src/。

### D4 — equiv_test 是跨阶段的"安全网"（不只是最后一步）

传统 SE 流程把 equiv_test 放最后。本 domain 把它提升为"跨阶段常驻机制"：
- 重构前：equiv_test 录制旧行为
- 翻译中：lang_rewrite_verifier 持续 equiv_test 对比
- 审查中：review 阶段调 equiv_test 跑"候选 patch 是否等价"
- 最终：equiv_test 作为 gate

这样 agent 在每个阶段都有"事实判据"，不靠 LLM 自己判对错。

### D5 — _shared/ 下放跨阶段 primitive，不重复造

常见跨阶段需求：
- AST 解析（tdd / implement / review 都要）
- Git diff 处理（review / verify / equiv_test 都要）
- 语言检测 / 依赖分析（plan / design / implement 都要）

统一放 `_shared/`，各阶段调用而不复制。对应 OmniCompany 框架级的 `primitives/` 设计（领域级 vs 框架级）。

### D6 — debugger 作为横切子包而非阶段

交互式调试不属于任何单阶段（每个阶段都可能需要调试 agent 状态）。作为横切子包：
- 供每阶段 Router 注入 breakpoint
- 产出 debug trace 供人审 / crystallize 消费

对标 IDE debugger（实际比 IDE 更侧重 agent 的"中间思考"而非运行时变量）。

### D7 — 本 domain 是 OmniCompany "agent 做完整软件工程"的答卷

最终目标：agent 能接收需求 → 完整走完 plan → design → tdd → implement → review → verify → equiv_test → PR。当前远未达到：
- plan / design / tdd 子包成熟度低
- review 只做简单 pattern check（对标不了 human reviewer）
- verify 只跑测试，不做静态分析
- 多阶段串联还没跑通

但各子包独立使用（只跑 lang_rewrite / 只跑 tdd gen）已有价值。

## 数据流 / 拓扑

### 完整七阶段链路（目标，尚未完全贯通）

```
[输入] 需求（自然语言 / issue）
   ↓
plan/ (LLM) → task_list + 风险评估
   ↓
design/ (LLM + tools) → 模块 / 接口 / Format 定义
   ↓
tdd/ (LLM) → 测试用例 + 断言
   ↓
implement/ (agent loop) → 源码（写到 generated/）
   ↓
review/ (LLM + rules) → review_report
   ↓
verify/ (RULE) → 跑测试 + 静态分析
   ↓
equiv_test/ (RULE) → 行为等价性验证
   ↓
[产物] PR-ready patch + review_report + equiv_report
```

### 跨语言翻译独立链路（已成熟）

```
[输入] TS 源码 + 翻译目标（Rust）
   ↓
lang_rewrite/ (agent loop + tools)
   ├─ AST 解析 TS
   ├─ 类型映射（TS → Rust trait）
   ├─ 生成 Rust 候选（写 generated/）
   └─ 调 cargo check / rust-analyzer
   ↓
lang_rewrite_verifier/ (RULE)
   ├─ 编译等价检查
   ├─ 行为等价（原 TS test → Rust test 映射）
   └─ 产出 equiv_report
   ↓
[产物] Rust 源码 + 等价报告
```

## 已知局限

1. **七阶段主链尚未完整跑通** — plan / design / tdd / implement / review / verify / equiv_test 每阶段都有子包，但"端到端走一遍"的集成实验没跑过。目前各阶段独立可用。**升级路径**：级别 B 或 C 任务，先把各子包 DESIGN.md 填齐，再做贯通实验。

2. **plan / design / tdd 子包成熟度低** — 缺 DESIGN.md，业务深度依赖 LLM 零样本能力。**升级路径**：每子包需要独立深度设计文档（级别 B 的 EC-8 任务）。

3. **review 对标 human reviewer 差距大** — 目前 review 靠 pattern rule + LLM 一次调用，无法捕捉"业务合理性"类深度 review。**升级路径**：crystallize + 历史 review 数据微调。

4. **equiv_test 语言覆盖有限** — Rust / TS 跑通，其他语言（Python / Go / Java）未适配。

5. **debugger 缺实战经验** — 横切设计合理但没大量使用。多数 agent 产出直接 pass/fail，不深度 debug。

## 参考资料

- 子包：[plan/](plan/) / [design/](design/) / [tdd/](tdd/) / [implement/](implement/) / [review/](review/) / [verify/](verify/) / [equiv_test/](equiv_test/)
- 横切：[debugger/](debugger/) / [lang_rewrite/](lang_rewrite/) / [lang_rewrite_verifier/](lang_rewrite_verifier/)
- 共享：[_shared/](_shared/)
- 中间产物：[generated/](generated/)
- 关联 memory：`project_rust_translation_progress.md`（Rust 翻译 Phase 2）
- 关联 memory：`project_ts_translation_status.md`（TS 翻译 Phase 3 tsc PASS）
- 关联 memory：`project_pain_evolution_workflow_status.md`（pattern_discovery / trace_induction 相关）
- 关联 domain：../demogame/DESIGN.md（demogame 的 produce 本质也是"代码生成"，可交叉参考）
