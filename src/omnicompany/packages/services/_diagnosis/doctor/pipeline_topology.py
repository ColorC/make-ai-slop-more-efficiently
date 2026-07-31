# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=shim
# [OMNI] material_id="material:diagnosis.doctor.pipeline_topology.compatibility_shim.py"
"""doctor/pipeline_topology.py — 向后兼容 shim (Stage 3 Clean Migration · 命名规范化 2026-04-22).

2 个 Team 诊断 Worker 已拆到 `workers/team/`:
  - TeamTopologyCheck     (原 PipelineTopologyCheckWorker)  — 一站式拓扑诊断
  - TeamLineageExtractor  (原 PipelineLineageWorker)         — 跨 Team material 产消图

拓扑检查引擎 (Finding / CheckContext / run_pipeline_checks 等) 保留在
`_archive/pipeline_topology_legacy.py` 作基础设施, 由本 shim re-export.
"""
from __future__ import annotations

# ─── 新 Worker 类 ────────────────────────────────────────────────────────
from .workers.team import (
    TeamTopologyCheck,
    TeamLineageExtractor,
)

# ─── 旧 Router 名别名 (外部调用方仍在用) ─────────────────────────────────
PipelineTopologyCheckRouter = TeamTopologyCheck
PipelineLineageRouter = TeamLineageExtractor
PipelineTopologyCheckWorker = TeamTopologyCheck   # legacy class-name alias
PipelineLineageWorker = TeamLineageExtractor

# ─── 模块级类/函数 re-export (基础设施) ─────────────────────────────────
from ._archive.pipeline_topology_legacy import (  # noqa: E402
    # dataclasses + context
    Finding,
    CheckContext,
    # 内部检查函数
    _build_context,
    _check_no_entry,
    _check_isolated,
    _check_dead_end,
    _check_format_break,
    _check_cycle,
    _check_composite_missing,
    _check_soft_hard_pairing,
    _check_granted_tag_chain,
    _check_maturity_consistency,
    _check_purpose_quality,
    _check_duplicate_edge,
    # 主入口
    PipelineCheckSpec,
    PIPELINE_CHECKS,
    run_pipeline_checks,
    # 旧兼容接口
    TopologyIssue,
    _finding_to_issue,
    check_pipeline_topology,
    format_topology_report,
    # Loader / Lineage 工具
    load_pipeline_from_file,
    FormatEdge,
    PipelineLineage,
    extract_pipeline_lineage,
    discover_all_pipelines,
)


__all__ = [
    # New names
    "TeamTopologyCheck",
    "TeamLineageExtractor",
    # Legacy alias
    "PipelineTopologyCheckRouter",
    "PipelineLineageRouter",
    "PipelineTopologyCheckWorker",
    "PipelineLineageWorker",
    # 基础设施
    "Finding",
    "CheckContext",
    "PipelineCheckSpec",
    "PIPELINE_CHECKS",
    "TopologyIssue",
    "FormatEdge",
    "PipelineLineage",
    "run_pipeline_checks",
    "check_pipeline_topology",
    "format_topology_report",
    "load_pipeline_from_file",
    "extract_pipeline_lineage",
    "discover_all_pipelines",
]
