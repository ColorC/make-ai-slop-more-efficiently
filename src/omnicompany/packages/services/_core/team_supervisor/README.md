<!-- [OMNI] origin=ai-ide domain=services/team_supervisor ts=2026-05-04T16:35:00Z type=doc status=active agent=ai-ide belongs_to_service=team_supervisor -->
<!-- [OMNI] summary="team_supervisor service 自我叙事 README — 通用 Team 健康监督设施. 给定任意 Team 自动回答 3 问 (产物形式/设计目的/健康判据), 产假设设计测试输出健康报告. 不修复只发信号" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §四 模板严格写, 快速推进. DESIGN 用非标中文数字结构, 不强改" -->
<!-- [OMNI] tags=readme,team_supervisor,core,health-supervision,self-narrative -->
<!-- [OMNI] material_id="material:services._core.team_supervisor.readme.self_narrative.md"-->

# team_supervisor · 通用 Team 健康监督

> 给定任意 OmniCompany Team (target_team_id), 自动回答**三个基本问题** (Q1 产物形式 / Q2 设计目的 / Q3 健康判据), 产假设 + 设计测试 + 跑 + 产健康报告. **不修复, 只发信号**.

## 这是什么

team_supervisor 是 omnicompany 的**通用 Team 健康监督设施**. 6 Worker 协作, 给定任意 Team 自动跑健康检查:

1. 装 target metadata (代码 / DESIGN / FORMAT_OUT schema)
2. **Q1 产物形式** — 这 team 产物长什么样
3. **Q2 设计目的** — 它为什么存在
4. **Q3 健康判据** — Q1+Q2 综合
5. 产 ≥10 条假设 (条件 → 预期, 带 oracle hint)
6. 真 dispatch 跑 target team + oracle 评估
7. 装配 health_report (PASS/PARTIAL/FAIL)

L1 2026-04-26 P3.B v4 真达成后立项. 跟 [doctor](../../_diagnosis/doctor/) 互补 — doctor 看单 Format/Worker/Team 语义健康, team_supervisor 看 Team 整体三问健康判据.

## 解决什么 / 不解决什么

**解决**: 任意 Team 三问健康判据自动产出 + 假设级测试评估 + 健康报告 (PASS/PARTIAL/FAIL).
**不解决**: 修复 (只发信号, 不动代码) / 业务正确性 / 单 Format/Worker 健康 (那是 doctor).

## 设计目的与最终目标

**设计目的**: 让 omnicompany 给任意已存在的 Team 跑"我健康吗" 检查, 不需要 Team 作者手工设计 oracle. 三问机制 (产物 / 目的 / 判据) 是 L1 验收 P3.B 时立的方法论.

**反模式禁令**: 禁字段 `complexity_score` / `quality_rating` / `maturity_level` / `tier` / `tags` / `kind` (语义 vibe 评分). 仅允许物理度量 (count) + 协议层硬枚举.

**最终目标** (当下能认知的): 跑通几个真 Team (csv-to-md / repo-absorption / docauthor 等), 验证三问机制泛化能力.

## 规划

- **当前 V0 design** (2026-04-26 立项, P3.B v4 后)
- **下一步**: 6 Worker 真实现 + 跑通几个真 Team

## 构成

- Workers (7 个 = 6 主流 + 1 入口) → [workers/](workers/)
  - TargetIngressWorker (HARD 入口装载)
  - ProductFormAnalyzerWorker (AGENT, Q1)
  - PurposeInterpreterWorker (AGENT, Q2)
  - HealthCriteriaDesignerWorker (SOFT, Q3 fan-in 2/2)
  - HypothesisGeneratorWorker (AGENT, fan-in 3 上游)
  - TestExecutorWorker (AGENT, fan-in 4, 用 DispatchTeam + EvaluateOracle 工具)
  - HealthReportAssemblerWorker (HARD, fan-in 全部)
- Materials (8 个) → [formats.py](formats.py)
- 入口 → [run.py](run.py)

## 想了解更多

- [DESIGN.md](DESIGN.md) (非标结构 1.名称 / 2.职责 / 3.IO / 4.拓扑) / [SKILL.md](SKILL.md)
- 上游 plan → [docs/plans/[2026-04-26]TEAM-SUPERVISOR/plan.md](../../../../../docs/plans/%5B2026-04-26%5DTEAM-SUPERVISOR/plan.md)
- 跟 doctor 关系 → [../../_diagnosis/doctor/README.md](../../_diagnosis/doctor/README.md)
- 反模式禁令来源 → [feedback_semantic_sentences_not_classification](../../../../../) memory
