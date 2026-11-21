<!-- [OMNI] origin=ai-ide domain=services/lap_auditor ts=2026-05-05T19:30:00Z type=doc status=archived belongs_to_service=lap_auditor -->
<!-- [OMNI] material_id="material:diagnosis.lap_auditor.archive_pointer.md" -->
<!-- [OMNI] summary="lap_auditor 整体归档 (2026-05-05). 概念并入 doctor _spec/ 子域. 历史代码在 _archive/" -->
<!-- [OMNI] why="诊断重制 plan 阶段 1 step 3: lap_auditor 三件套合规审计跟新 doctor 规范子域同源, 旧实现归档让位" -->
<!-- [OMNI] tags=archived,pointer,lap_auditor,diagnosis-reconsolidation -->

# lap_auditor · 已归档

> 2026-05-05 整体归档. 详诊断重制计划:
> [docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/plan.md](../../../../../../docs/plans/diagnosis/%5B2026-05-05%5DDIAGNOSIS-RECONSOLIDATION/plan.md)

## 去向

- **概念并入**: doctor `_spec/` 子域. lap_auditor 旧四红线 (事件总线 / Material 真实性 / 接口规范 / Domain 隔离) 拆成 SpecChecker 集
- **历史代码**: 本目录 [_archive/](_archive/) 完整保留, 不再维护

## 不再保留原因

新 doctor 规范子域以 `docs/standards/` + `docs/standards/protocol/*_template.md` 为唯一真相源. lap_auditor 旧实现的"四红线" hardcode 在 LLM prompt, 跟新唯一源体系冲突, 重写比搬代码省事.
