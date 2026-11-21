# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.team.maturity_consistency_checker.py"
"""TeamMaturityCheck — 成熟度短板 (HARD, Stage 3 Clean Migration 2026-04-22).

Worker 协议:
  FORMAT_IN  = diag.team.extracted
  FORMAT_OUT = diag.team.check.maturity

诊断目标: maturity_consistency (短板原则):
  CRYSTALLIZED 节点不应直接依赖 GROWING/HYPOTHETICAL 上游节点.
  若依赖, CRYSTALLIZED 声明具有误导性, 实际可靠性受上游制约.
  → degrading 级 Finding.
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import load_specs_from_input, serialize_findings


class TeamMaturityCheck(Worker):
    """Pipeline 成熟度一致性检查 (短板原则)."""

    DESCRIPTION = (
        "Pipeline 成熟度一致性检查 (短板原则): CRYSTALLIZED 节点不应直接依赖 GROWING/HYPOTHETICAL 上游. "
        "违反则 maturity_consistency=degrading, 表示 CRYSTALLIZED 声明具有误导性. "
        "输出 check_maturity 字段."
    )
    FORMAT_IN = "diag.team.extracted"
    FORMAT_OUT = "diag.team.check.maturity"

    _CHECKS = ["maturity_consistency"]

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.packages.services._diagnosis.doctor.pipeline_topology import run_pipeline_checks

        specs = load_specs_from_input(input_data)
        all_findings: list[dict] = []

        for spec in specs:
            findings = run_pipeline_checks(spec, enabled=self._CHECKS)
            all_findings.extend(serialize_findings(findings, spec.id))

        has_degrading = any(f["level"] == "degrading" for f in all_findings)

        output = dict(input_data)
        output["check_maturity"] = {
            "check": "maturity",
            "checks_run": self._CHECKS,
            "passed": not has_degrading,
            "severity": "HIGH" if has_degrading else "INFO",
            "findings": all_findings,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=f"PipelineMaturityCheck: {len(all_findings)} findings",
        )
