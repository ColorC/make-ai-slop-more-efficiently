<!-- [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/_test_fixtures ts=2026-05-06T03:10:00Z type=fixture status=active agent=ai-ide -->
<!-- [OMNI] summary="红样本 plan fixture: 故意违反 plan_template 5 节硬下限, PlanDiagnosticAgent 跑应至少产 4 finding" -->
<!-- [OMNI] why="self_audit B-2 PlanDiagnosticAgent 红绿基线" -->
<!-- [OMNI] tags=test-fixture,red-sample,plan,non-compliant -->
<!-- [OMNI] material_id="material:diagnosis.doctor.test_fixtures.red_plans.red_minimal_plan.md" -->

# 一个糟糕计划

做点 X 啊.

## 步骤

第一步: 启动.
第二步: 推进.
第三步: 收尾.

差不多就是这样.

<!-- 故意违反 plan_template 节齐全要求:
- 缺 一·需求清单 (无 ID + 验收)
- 缺 二·产物清单 (无 path + 完成判定)
- 缺 三·验收标准 (静态/动态都没)
- 缺 五·不达标处置 (无三档判定)
- 七节里能凑上的勉强只有"步骤"段
-->
