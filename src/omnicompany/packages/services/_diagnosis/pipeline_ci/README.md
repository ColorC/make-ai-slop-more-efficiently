<!-- [OMNI] origin=ai-ide domain=services/pipeline_ci ts=2026-05-05T19:50:00Z type=doc status=archived belongs_to_service=pipeline_ci -->
<!-- [OMNI] material_id="material:diagnosis.pipeline_ci.archive_pointer.md" -->
<!-- [OMNI] summary="pipeline_ci 整体归档 (2026-05-05). 三 Auditor 概念 (合规/拓扑/输出) 并入 doctor _spec/ 跟 _hypothesis/ 子域" -->
<!-- [OMNI] why="诊断重制 plan 阶段 1 step 5: pipeline_ci 概念可挪用但实现不留, 旧 build_team 注册 + run.py 漂移版本归档" -->
<!-- [OMNI] tags=archived,pointer,pipeline_ci,diagnosis-reconsolidation -->

# pipeline_ci · 已归档

> 2026-05-05 整体归档. 详:
> [docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/plan.md](../../../../../../docs/plans/diagnosis/%5B2026-05-05%5DDIAGNOSIS-RECONSOLIDATION/plan.md)

## 去向

- **概念并入**:
  - `domain_scanner_worker` → doctor `_entity/` 子域 (实体扫源)
  - `batch_auditor_worker` (合规/拓扑/输出) → doctor `_spec/` (合规) + `_hypothesis/` (拓扑)
  - `ci_gate_worker` (critical_count > 0 阻断) → doctor `_archive_table/` (汇总判 gate, 跟 guardian 钩子联动)
- **历史代码**: 本目录 [_archive/](_archive/) 完整保留
