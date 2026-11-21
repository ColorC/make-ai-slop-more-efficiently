# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.team.structural_topology_checker.py"
"""TeamStructuralCheck — 结构合法性 (HARD, Stage 3 Clean Migration 2026-04-22).

Worker 协议:
  FORMAT_IN  = diag.team.extracted
  FORMAT_OUT = diag.team.check.structural

诊断目标: 5 项纯拓扑检查, 不依赖 Format 定义:
  - no_entry         (blocking)   入口节点存在性
  - isolated         (degrading)  孤立节点 (从 entry 不可达)
  - dead_end         (advisory)   悬空终端 (有入无出但非合法终端)
  - cycle            (blocking)   非 feedback 边构成的有向环
  - duplicate_edge   (advisory)   重复边 (同 source→target)

输出 check_structural 字段; 任何 blocking Finding 表示管线无法正确执行.
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import load_specs_from_input, serialize_findings


class TeamStructuralCheck(Worker):
    """5 项纯拓扑结构合法性检查."""

    DESCRIPTION = (
        "Pipeline 结构合法性检查: no_entry (入口节点存在性) / isolated (孤立节点) / "
        "dead_end (悬空终端) / cycle (非 feedback 边成环) / duplicate_edge (重复边). "
        "输出 check_structural 字段, blocking Finding 表示管线无法正确执行."
    )
    FORMAT_IN = "diag.team.extracted"
    FORMAT_OUT = "diag.team.check.structural"

    _CHECKS = ["no_entry", "isolated", "dead_end", "cycle", "duplicate_edge"]

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.packages.services._diagnosis.doctor.pipeline_topology import run_pipeline_checks

        specs = load_specs_from_input(input_data)
        all_findings: list[dict] = []

        for spec in specs:
            findings = run_pipeline_checks(spec, enabled=self._CHECKS)
            all_findings.extend(serialize_findings(findings, spec.id))

        has_blocking = any(f["level"] == "blocking" for f in all_findings)
        has_degrading = any(f["level"] == "degrading" for f in all_findings)

        output = dict(input_data)
        output["check_structural"] = {
            "check": "structural",
            "checks_run": self._CHECKS,
            "passed": not has_blocking,
            "severity": "CRITICAL" if has_blocking else "HIGH" if has_degrading else "INFO",
            "findings": all_findings,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=(
                f"PipelineStructuralCheck: {len(all_findings)} findings "
                f"({'blocking' if has_blocking else 'ok'})"
            ),
        )
