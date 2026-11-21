# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.team.soft_hard_pairing_checker.py"
"""TeamSoftHardCheck — P-07 软硬配对 (HARD, Stage 3 Clean Migration 2026-04-22).

Worker 协议:
  FORMAT_IN  = diag.team.extracted
  FORMAT_OUT = diag.team.check.soft-hard

诊断目标: P-07 软硬配对原则
  LLM 节点 (method=LLM) 的直接下游中, 应存在至少一个 RULE 或 ANCHOR 节点作为验证器.
  否则 LLM 输出无确定性验证, 语义错误将静默传递到下游.
  → degrading 级 Finding.
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import load_specs_from_input, serialize_findings


class TeamSoftHardCheck(Worker):
    """P-07 LLM + 确定性验证器配对检查."""

    DESCRIPTION = (
        "P-07 软硬配对检查: LLM 节点 (method=LLM) 的直接下游应有 RULE 或 ANCHOR 节点作为验证器. "
        "无 HARD 后继则 soft_hard_pairing=degrading, 表示 LLM 输出无确定性保障. "
        "输出 check_soft_hard 字段."
    )
    FORMAT_IN = "diag.team.extracted"
    FORMAT_OUT = "diag.team.check.soft-hard"

    _CHECKS = ["soft_hard_pairing"]

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.packages.services._diagnosis.doctor.pipeline_topology import run_pipeline_checks

        specs = load_specs_from_input(input_data)
        all_findings: list[dict] = []

        for spec in specs:
            findings = run_pipeline_checks(spec, enabled=self._CHECKS)
            all_findings.extend(serialize_findings(findings, spec.id))

        has_degrading = any(f["level"] == "degrading" for f in all_findings)

        output = dict(input_data)
        output["check_soft_hard"] = {
            "check": "soft_hard",
            "checks_run": self._CHECKS,
            "passed": not has_degrading,
            "severity": "HIGH" if has_degrading else "INFO",
            "findings": all_findings,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=f"PipelineSoftHardCheck: {len(all_findings)} findings",
        )
