<!-- [OMNI] origin=claude-code domain=services/team_supervisor ts=2026-05-04T16:35:00Z type=design status=active belongs_to_service=team_supervisor -->
<!-- [OMNI] material_id="material:core.team_supervisor.design_documentation.md" -->

# team_supervisor · 通用 Team 健康监督设施

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部**.
>
> 注: 本 DESIGN 用**非标结构** (1.名称定位 / 2.职责 / 3.IO / 4.拓扑), 是该 service 自选, 不强改.

## 1 · 名称与定位

`team_supervisor` 是一个**通用 Team 健康监督**服务. 给定任意 OmniCompany Team (target_team_id), 自动回答三个基本问题、产生假设、设计并跑测试, 输出健康报告 — 不修复, 只发信号.

立项: 2026-04-26 · L1 在 P3.B v4 真达成后提出. 完整计划见 plan.md.

## 2 · 职责

接收 `team_supervisor.target_spec` (target_team_id + 可选 sample_input + 累积 ledger 路径), 通过 6 worker 协作:

1. 装入 target metadata (代码路径 / DESIGN.md / FORMAT_OUT schema)
2. 回答 **Q1 产物形式** — 这个 team 产物长什么样
3. 回答 **Q2 设计目的** — 它为什么存在
4. 综合 Q1+Q2 设计 **Q3 健康判据**
5. 产生 ≥10 条 (条件→预期) 假设, 每条带 oracle hint
6. 真 dispatch 跑 target team · 跑 oracle 评估假设
7. 装配 `team_supervisor.health_report` (PASS/PARTIAL/FAIL + 三问 brief + 假设评估 + 诊断段落)

## 3 · 输入与输出

### 3.1 输入

`team_supervisor.target_spec`:
- `target_team_id` (str, required): 协议层 team id (如 `repo-absorption` / `csv-to-md`)
- `sample_input` (dict, optional): 给 target 跑用的 input_data; 不给则 supervisor 尝试从 traces 历史中找
- `run_count` (int, default=1): 跑几次 target 取样
- `previous_ledger_path` (str, optional): 上次假设 ledger 路径, 累积新假设用

### 3.2 输出

`team_supervisor.health_report`:
- `verdict`: `PASS` / `PARTIAL` / `FAIL` (协议层硬枚举)
- `target_team_id`: 透传
- `three_questions.q1`: product_form_brief (含 essence / minimal_passing_evidence / failure_signals · 全自然语言句子)
- `three_questions.q2`: design_purpose_brief (含 essence / replaces / non_goals / stakeholder_use · 全句子)
- `three_questions.q3`: health_criteria (含 key_observations / red_flags / oracle_strategies · 全句子)
- `hypotheses_evaluated_count`: 数 (物理度量, 允许)
- `passed_count`: 数
- `failed_hypotheses[]`: 每条 {id, condition, expectation, observed, evidence}
- `diagnosis`: 总体诊断自然语言段落
- `ledger_increment[]`: 这次新增假设, 用于下次累积

**反模式禁令** (按 feedback_semantic_sentences_not_classification):
- 禁字段 `complexity_score` / `quality_rating` / `maturity_level` / `tier` / `tags` / `kind` (语义 vibe 评分)
- 仅允许物理度量 (count) 与协议层硬枚举 (VerdictKind)

## 4 · 拓扑

```
TargetIngressWorker (entry · HARD)
  ↓
  ├──→ ProductFormAnalyzerWorker (Q1 · AGENT)
  └──→ PurposeInterpreterWorker  (Q2 · AGENT)
        (Q1 + Q2 → fan-in)
        ↓
       HealthCriteriaDesignerWorker (Q3 · SOFT, fan-in 2/2)
        ↓
       HypothesisGeneratorWorker (AGENT, fan-in 3 上游)
        ↓
       TestExecutorWorker (AGENT, fan-in 4 上游 · 用 DispatchTeam + EvaluateOracle 工具)
        ↓
       HealthReportAssemblerWorker (HARD, fan-in 全部)
```

7 个节点, 6 个主流 + 1 个入口装载. 全 fan-in 由 `FORMAT_IN_MODE = "and"` 与 composite list FORMAT_IN 表达.

## 5 · Material (8 个)

| ID | producer | consumer |
|---|---|---|
| `team_supervisor.target_spec` | 外部触发器 | TargetIngressWorker |
| `team_supervisor.target_metadata` | TargetIngressWorker | ProductForm + Purpose + Hypothesis + Test |
| `team_supervisor.product_form_brief` | ProductFormAnalyzer | Health + Hypothesis + Report |
| `team_supervisor.design_purpose_brief` | PurposeInterpreter | Health + Hypothesis + Report |
| `team_supervisor.health_criteria` | HealthCriteriaDesigner | Hypothesis + Report |
| `team_supervisor.hypothesis_set` | HypothesisGenerator | Test + Report |
| `team_supervisor.test_results` | TestExecutor | Report |
| `team_supervisor.health_report` | HealthReportAssembler | sink (外部消费) |

## 6 · 边界与约束

### 6.1 信号模式 (用户明示)

- supervisor **绝不**改 target team 代码
- 失败假设落 health_report 让 L1+L2 决定怎么修
- 不接 `retry-with-feedback` 机制

### 6.2 写法泛起步, 实践窄起步

- 代码上 target_team_id 是参数, 不为 absorption 写 if-else
- 首批喂 repo_absorption, 验通用性后再扩 csv_to_md

### 6.3 workspace 动态引用

worker prompt 注入 target 资源时**引用路径**(让 agent 用 ReadFile 读), 不**预拷贝内容**. 涉及路径:
- `src/omnicompany/packages/services/{target_team_id}/`
- `src/omnicompany/packages/services/{target_team_id}/DESIGN.md`
- `src/omnicompany/packages/services/{target_team_id}/team.py`
- `src/omnicompany/packages/services/{target_team_id}/workers/`
- `data/services/{target_team_id}/` (历史 traces)

### 6.4 验证器递归终止

不验 supervisor 自己, 验它产的 health_report 是否合理 — 由 L1 抽样 + L2 交叉对照. 不嵌套 supervise-supervisor.

### 6.5 LLM 输出全 function call

所有 SOFT/AGENT worker 用 `SubmitXxxRouter(SingleToolRouter)` 自定义工具产结构化输出. 禁 `FinishRouter` + `json.loads`.

### 6.6 禁预防性截断

喂 LLM 资源全量 / 让 agent 主动 ReadFile 分片. 禁 `[:N]` / `truncate`.

### 6.7 错误处理边界 (实操)

- `TargetIngressWorker` 校 target_team_id 在 registry. 否则 FAIL diagnosis "target 未注册"
- `TestExecutorWorker` 真 dispatch 内部失败 → 假设评估记 evidence "target 跑失败" 但不 abort 整个 supervisor
- 嵌套 dispatch 使用同进程 (因 dispatch 是 sync) · 用 max_steps=1000 避免预算撞死

## 7 · 未决与未来

- **csv_to_md 通用性验证**: Phase E 把 supervisor 喂 csv_to_md, 不改 supervisor 代码看是否能产合理 health_report
- **hypothesis ledger 跨 run 累积**: 一周观察期看假设是否真累积成稳定知识库 · 是否需要 dedup / generalize
- **L1 抽样裁决机制**: 暂走人审, 未来可自动化收集"L1 是否同意 supervisor 的判定"做正负样本
- **接其他 team 的具体怎么做** (规模上限): 一旦累积 ≥3 target 跑通, 看是否要做 supervisor-of-many (批量跑多 team)
- **接 retry-with-feedback** (如果未来开口子): 当前关闭, 留接口位但不实现
