<!-- [OMNI] origin=ai-ide domain=services/cleanup_bot ts=2026-05-05T19:45:00Z type=doc status=archived belongs_to_service=cleanup_bot -->
<!-- [OMNI] material_id="material:diagnosis.cleanup_bot.archive_pointer.md" -->
<!-- [OMNI] summary="cleanup_bot 整体归档 (2026-05-05). 不属诊断 — 它是清理工具的取证 (扫磁盘可疑路径 + LLM 判正误触 + 打印清理脚本), 跟健康诊断不同质" -->
<!-- [OMNI] why="诊断重制 plan 阶段 1 step 4: 用户决议 cleanup_bot 不属诊断设施. 真要保留挪到独立位置, 当前默认归档" -->
<!-- [OMNI] tags=archived,pointer,cleanup_bot,diagnosis-reconsolidation -->

# cleanup_bot · 已归档

> 2026-05-05 整体归档. 详:
> [docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/plan.md](../../../../../../docs/plans/diagnosis/%5B2026-05-05%5DDIAGNOSIS-RECONSOLIDATION/plan.md)

## 去向

- **不属诊断**: 三个 worker (evidence_gatherer 扫磁盘 + anomaly_detector LLM 判正误触 + rollback_planner 打印清理脚本) 是清理工具的取证, 跟"健康诊断"不同质
- **历史代码**: 本目录 [_archive/](_archive/) 完整保留

## 后续

如有真需求 (扫 AI 误触磁盘垃圾), 应作独立工具放别处, 不归诊断或 doctor 范围.
