# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=helper
# [OMNI] material_id="material:diagnosis.doctor.worker.team.package_shared_helpers.py"
"""Team 诊断子域共享辅助 (Stage 3 Clean Migration 2026-04-22).

检查引擎 (Finding / CheckContext / run_pipeline_checks / load_pipeline_from_file /
extract_pipeline_lineage) 仍保留在 pipeline_topology.py (对外稳定 API, 来自 _archive),
本文件只做参数序列化辅助, 供 worker 内部使用.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("omnicompany.doctor.pipeline")


def load_specs_from_input(input_data: dict) -> "list[Any]":
    """从 input_data 的 specs_data 字段还原 TeamSpec 列表."""
    from omnicompany.protocol.team import TeamSpec
    specs_data = input_data.get("specs_data", [])
    specs = []
    for d in specs_data:
        try:
            specs.append(TeamSpec.model_validate(d))
        except Exception:
            pass
    return specs


def serialize_findings(findings: "list[Any]", pipeline_id: str) -> "list[dict]":
    """Finding 列表转 dict 列表."""
    return [
        {
            "pipeline_id": pipeline_id,
            "check_id":    f.check_id,
            "level":       f.level,
            "severity":    f.severity,
            "location":    f.location,
            "observation": f.observation,
            "implication": f.implication,
            "cross_refs":  f.cross_refs,
        }
        for f in findings
    ]
