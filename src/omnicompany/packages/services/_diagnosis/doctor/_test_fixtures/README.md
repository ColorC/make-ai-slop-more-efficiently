<!-- [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/_test_fixtures ts=2026-05-06T18:55:00Z type=doc status=active agent=ai-ide -->
<!-- [OMNI] summary="doctor 红绿基线测试 fixture 库. SPEC.test_baseline 引这里的样本验证诊断 agent 真有判别力, 不是单方向跑通" -->
<!-- [OMNI] why="self_audit_2026-05-06 §B-2 修复: 用户铁律 feedback_connected_is_not_discriminating 要求接通声明前必走红绿对比. doctor agent SPEC.test_baseline 字段填这里的 fixture 路径" -->
<!-- [OMNI] tags=test-fixtures,red-green,doctor,baseline -->
<!-- [OMNI] material_id="material:diagnosis.doctor.test_fixtures.readme.md" -->

# doctor 红绿基线 test_fixtures 库

> 修复 `self_audit_2026-05-06.md` §B-2 — 诊断 agent 真接通的红绿基线证明.

## 一 · 红 vs 绿 vs 灰度

按 [`feedback_validation_calibration_red_green_gradient`](../../../../../../../docs/standards/_global/) 铁律:

- **绿样本** (`green_*/`): 已知合规对象, 跑诊断器应**少 finding 或仅 advisory finding**, 无阻断
- **红样本** (`red_*/`): 故意违反某条规范的 fixture, 跑诊断器应**真产 finding, 引规范条款**
- **梯度样本** (`gradient_*/`): 介于红绿之间, 不同档质量 (好 / 很好 / 特别好 + 坏 / 多坏)

## 二 · 当前 fixture (V0)

### red_workers/

| fixture | 故意违反 | 期望 finding |
|---|---|---|
| [`red_minimal_worker.py`](red_workers/red_minimal_worker.py) | R-01 (DESCRIPTION 太短) + R-02 (无 FORMAT_OUT) + R-04 (直接 import LLM 库) + R-14 (Verdict.diagnosis 空话) | ≥4 finding 引 4 条 R-XX |

### red_plans/

| fixture | 故意违反 | 期望 finding |
|---|---|---|
| [`red_minimal_plan.md`](red_plans/red_minimal_plan.md) | plan_template 七节硬下限 (缺一·需求清单 / 二·产物清单 / 三·验收标准 / 五·不达标处置) | narrative 主调缺失/阻断 |

### red_sources/

| fixture | 故意特征 | 期望 |
|---|---|---|
| [`random_readme.md`](red_sources/random_readme.md) | 0 个"必须 / 应 / 不得" 强制词的软介绍 (锤子/螺丝刀/卷尺) | HypothesisDeriverAgent 派 0-1 假设 (LLM 自律识别"无强制语义") |

### 绿样本 (跟样例库共享, 不重复造)

| kind | 绿样本 | 用于 |
|---|---|---|
| worker | [`csv_reader.py`](../../../_utility/csv_to_md/workers/csv_reader.py) (E-worker-csv_reader-2026-05-05 标杆) | Spec/Hypothesis/Exemplar 红绿 worker 绿测 |
| plan | `sample_compliant_plan_exemplar_library.md` | Plan 红绿绿测 |
| derivation source | `worker.md` (强制词富) | Deriver 红绿绿测 |

## 三 · 验证脚本 (跑红绿真接通)

| agent | 脚本 | 实测 (2026-05-06) |
|---|---|---|
| SpecDiagnosticAgent | `_scratch/dogfood_red_green_baseline.py` | OVERALL PASS — RED 5 finding 引 R-01/R-02/R-04/R-05/R-14, GREEN 2 finding 全正面 |
| HypothesisDiagnosticAgent | `_scratch/dogfood_red_green_hypothesis.py` | PASS — RED narrative 含"违反", GREEN 含"满足", H-001 在 red applied 命中 |
| ExemplarDiagnosticAgent | `_scratch/dogfood_red_green_exemplar.py` | PASS — RED 4 gap finding, GREEN 1 parity finding |
| PlanDiagnosticAgent | `_scratch/dogfood_red_green_plan.py` | PASS — narrative 双向区分 (绿合规 / 红缺失) |
| HypothesisDeriverAgent | `_scratch/dogfood_red_green_deriver.py` | PASS — GREEN 派 5, RED 派 0 (极强判别力, LLM 真自律) |

## 四 · 怎么用

每个诊断 agent 的 SPEC.test_baseline 字段 link 这里的 fixture path. 跑红绿对比脚本 (见 `_scratch/dogfood_red_green_baseline.py`) 验:

1. 红 finding 数 >> 绿 finding 数
2. 红 finding 引规范 R-XX 跟违反点对得上
3. 绿 finding 全 advisory, 无阻断

不通过 = 假接通, 必须修.

## 五 · 待补 (V1)

- 各诊断方法 (hypothesis / exemplar / plan / derivation) 的红绿样本各 1 对
- 灰度样本 (中等质量, 测梯度区分能力)
- 各 entity_kind (material / team / agent / hook / tool / plan) 的红绿样本
