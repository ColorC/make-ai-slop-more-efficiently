<!-- [OMNI] origin=ai-ide domain=services/domain_scout ts=2026-05-05T20:00:00Z type=doc status=archived belongs_to_service=domain_scout -->
<!-- [OMNI] material_id="material:diagnosis.domain_scout.archive_pointer.md" -->
<!-- [OMNI] summary="domain_scout 整体归档 (2026-05-05). 设计模式 (5 字段 finding 结构 + 独立 Reviewer 拓扑 + 原文不预防截断) 落 doctor §5.6 通用原则" -->
<!-- [OMNI] why="诊断重制 plan 阶段 1 step 6: domain_scout 跨 domain 知识抓取部分不属诊断, 设计模式可挪用. 旧 V0 design skeleton 归档" -->
<!-- [OMNI] tags=archived,pointer,domain_scout,diagnosis-reconsolidation -->

# domain_scout · 已归档

> 2026-05-05 整体归档. 详:
> [docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/plan.md](../../../../../../docs/plans/diagnosis/%5B2026-05-05%5DDIAGNOSIS-RECONSOLIDATION/plan.md)

## 去向

- **不属诊断**: source_fetcher / dedup_filter / digest_writer 是知识抓取工具, 跟健康诊断不同质. 真要保留挪 _learning/
- **设计模式并入 doctor**:
  - 5 字段 finding 结构 (title/insight/source_url/quoted_evidence/confidence) → HealthFinding Material schema 沿用
  - 独立 Reviewer 拓扑 (产 + 独立验, 非自评) → doctor 通用 agent worker 设计原则 (plan §5.6 原则 5)
  - 原文完整不预防截断 → 已是 omnicompany 铁律 A
- **历史代码**: 本目录 [_archive/](_archive/) 完整保留
