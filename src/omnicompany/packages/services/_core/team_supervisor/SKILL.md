---
name: team_supervisor
description: omnicompany 通用 Team 健康监督 - 给定任意 Team 跑三问健康检查 (产物形式/设计目的/健康判据), 产假设+测试+报告. 不修复只发信号.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/team_supervisor ts=2026-05-04T16:37:00Z type=doc status=active agent=ai-ide belongs_to_service=team_supervisor -->
<!-- [OMNI] summary="team_supervisor 操作手册 — 跑 Team 健康监督的步骤 + 入口清单 + 故障排查" -->
<!-- [OMNI] tags=skill,team_supervisor,how-to,core,health-supervision -->
<!-- [OMNI] material_id="material:services._core.team_supervisor.skill.operations_manual.md"-->

# team_supervisor · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

## 适用范围

**用我**: 给任意 Team 跑健康检查 + 拿三问 (产物/目的/判据) 报告 + ≥10 条假设评估.
**不用我**: 修复代码 / 单 Format/Worker 健康 (找 doctor) / 业务正确性.

## 前置条件

- omnicompany 已装 + `THE_COMPANY_API_KEY` 配
- target_team_id 在协议层注册 (能 dispatch)
- 可选 sample_input (没给 supervisor 自己从 traces 找)

## 操作步骤

### 场景 A · 给某 Team 跑健康监督

```bash
omni run team_supervisor -i target_team_id="csv-to-md"
```

**可选**:
- `-i sample_input='{...}'` — 给 target 跑用的 input
- `-i run_count=3` — 跑几次取样
- `-i previous_ledger_path="..."` — 累积上次假设 ledger

### 场景 B · 看历史健康报告

```bash
ls data/services/team_supervisor/<target_team_id>/reports/
```

## 入口清单

| 入口 | 用途 |
|---|---|
| `omni run team_supervisor` | 跑健康监督 |
| `from .workers import ...` | 单 Worker 调用 |

## 故障排查

| 现象 | 修 |
|---|---|
| V0 还没真实现 | status=design, 6 Worker 真实现待做 |
| Q1/Q2 brief 输出空泛 | LLM 没 read DESIGN.md 全文, 调 prompt 让先 read 再答 |
| Q3 判据跟 Q1/Q2 对不上 | LLM fan-in 没真合并, 调 HealthCriteriaDesigner prompt |
| 假设评估 pass_count 0 | oracle hint 太严或 expectation 错, 看具体 failed_hypotheses |
| 报告含 score / tier / quality_rating 字段 | 反模式禁令违反, 改 prompt 强约束只用自然语言句子 |

## 想了解更多

- [README.md](README.md) / [DESIGN.md](DESIGN.md)
- 上游 plan → [docs/plans/[2026-04-26]TEAM-SUPERVISOR/plan.md](../../../../../docs/plans/%5B2026-04-26%5DTEAM-SUPERVISOR/plan.md)
- 反模式禁令 → feedback_semantic_sentences_not_classification memory
