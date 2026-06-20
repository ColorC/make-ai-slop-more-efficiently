---
name: software_engineering
description: 软件工程各阶段的管线化——把"计划→设计→测试先行→实现→审查→验证→等价测试"每一段串成 OmniCompany Team,让 agent 代替人做每一阶段。按工程阶段聚类(非按语言/框架),各阶段语言无关;agent 产物先写 generated/ 再人审 cherry-pick 合入,不直接改 src。何时用:要 agent 从需求拆计划、产架构设计、生成测试用例、写实现、自动 code review、跑验证、或做新旧实现等价性验证(尤其跨语言翻译)时。触发关键词:计划拆分、架构设计、TDD、测试生成、代码实现、code review、代码审查、验证、等价测试、跨语言翻译、TS转Rust、lang rewrite、重构等价、plan/design/tdd/implement/review/verify。当前 lang_rewrite(TS→Rust)最成熟,七阶段主链尚未端到端贯通,各子包独立可用。
---

# software_engineering

software_engineering 是软件工程各阶段的 OmniCompany 化,回答"软件工程从计划到等价测试的每一段能不能用管线串起来、让 agent 代替人做每一阶段"。它按工程阶段聚类而非按技术栈,每阶段跨语言通用(语言差异在 tools/prompt 层解决)。所有 agent 代码产出先写到 `generated/`、人工 cherry-pick 合入 src,失败产物不污染主仓。

## 何时用

- 让 agent 接需求做某一阶段:拆计划、产架构设计、生成测试、写实现、自动审查、跑验证。
- 做新旧实现的等价性验证(重构/翻译场景),equiv_test 作为"事实判据"而非靠 LLM 自判对错。
- 跨语言翻译(当前最成熟,TS→Rust 已跑通):AST 解析→类型映射→生成候选→cargo check→等价验证。
- 各子包独立使用即有价值(只跑 lang_rewrite、只跑 tdd 生成),不必等整链贯通。

## 怎么跑

各阶段注册成独立管线(`sw-` 前缀),按需单跑:

```bash
omni run sw-plan      -i ...   # 计划:需求 → 任务拆分 + 风险评估
omni run sw-design    -i ...   # 设计:plan → 模块/接口/Format 定义
omni run sw-tdd       -i ...   # 测试先行:设计 → 测试用例 + 断言
omni run sw-implement -i ...   # 实现:测试+设计 → 源码(写到 generated/)
omni run sw-review    -i ...   # 审查:自动 code review
omni run sw-verify    -i ...   # 验证:跑测试 + 静态分析
```

产物落 `data/domains/software_engineering`;agent 代码产出落 `generated/`(按 task_id/timestamp 分子目录,不覆盖,人审合入)。

## 管线节点

七阶段映射到子包,加三个横切子包:

- **plan/** — 从需求产 plan(任务拆分/风险评估)。
- **design/** — 从 plan 产设计(模块/接口/Format)。
- **tdd/** — 按设计产测试用例 + 断言。
- **implement/** — 按测试+设计写代码(agent loop,写 generated/)。
- **review/** — 自动 code review(当前靠 pattern rule + LLM 一次调用,对标 human reviewer 差距大)。
- **verify/** — 跑测试 +(目标)静态分析。
- **equiv_test/** — 新旧实现行为等价性验证,被提升为跨阶段常驻"安全网"(重构前录旧行为/翻译中持续对比/审查中验候选 patch/最终作 gate)。
- 横切:**lang_rewrite/**(跨语言翻译,Rust Phase 2 进行中,TS tsc PASS,当前最成熟,作 domain 磨刀石)、**lang_rewrite_verifier/**(翻译等价验证)、**debugger/**(交互式调试,跨阶段)。
- 共享:**_shared/**(AST 解析/git diff/语言检测等跨阶段 primitive,不重复造)。

已知局限:七阶段主链尚未端到端贯通;plan/design/tdd 成熟度低;review 捕捉不到业务合理性类深度审查;equiv_test 语言覆盖限 Rust/TS。

## 配置

无需额外配置。各阶段工具语言无关;翻译验证依赖目标语言工具链(如 Rust 走 `cargo check`/rust-analyzer、TS 走 `tsc`),需本机装好对应工具链。
