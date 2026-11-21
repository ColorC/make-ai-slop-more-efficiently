# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.team.format_contract_checker.py"
"""TeamMaterialContractCheck — Format 契约 (HARD, Stage 3 Clean Migration 2026-04-22).

Worker 协议:
  FORMAT_IN  = diag.team.extracted
  FORMAT_OUT = diag.team.check.format-contract

诊断目标: 相邻边 Format 连续性和标签承诺链完整性:
  - format_break         (blocking)  相邻边 format_out ≠ format_in (运行时 KeyError 直接源)
  - composite_missing    (degrading) composite Format 上游覆盖缺失 (需 FormatRegistry)
  - granted_tag_chain    (degrading) required_tags 未被上游 tags 静态覆盖 (语义假设违约源)

输出 check_format_contract 字段.
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import load_specs_from_input, serialize_findings


class TeamMaterialContractCheck(Worker):
    """相邻边 Format 连续性 + composite fan-in 覆盖检查."""

    DESCRIPTION = (
        "Pipeline Format 契约检查: format_break (相邻边 Format 断裂, blocking) / "
        "composite_missing (composite Format 上游覆盖缺失, degrading) / "
        "granted_tag_chain (required_tags 被上游 tags 静态覆盖, degrading). "
        "输出 check_format_contract 字段."
    )
    FORMAT_IN = "diag.team.extracted"
    FORMAT_OUT = "diag.team.check.format-contract"

    _CHECKS = ["format_break", "composite_missing", "granted_tag_chain"]

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.packages.services._diagnosis.doctor.pipeline_topology import run_pipeline_checks

        specs = load_specs_from_input(input_data)

        # 尝试加载 FormatRegistry (供 composite_missing / granted_tag_chain 使用)
        format_registry = None
        try:
            from omnicompany.core.registry import discover
            from omnicompany.protocol.format import _default_registry  # type: ignore
            discover()
            format_registry = _default_registry
        except Exception:
            pass

        all_findings: list[dict] = []
        for spec in specs:
            findings = run_pipeline_checks(spec, enabled=self._CHECKS, format_registry=format_registry)
            all_findings.extend(serialize_findings(findings, spec.id))

        has_blocking = any(f["level"] == "blocking" for f in all_findings)
        has_degrading = any(f["level"] == "degrading" for f in all_findings)

        output = dict(input_data)
        output["check_format_contract"] = {
            "check": "format_contract",
            "checks_run": self._CHECKS,
            "passed": not has_blocking,
            "severity": "CRITICAL" if has_blocking else "HIGH" if has_degrading else "INFO",
            "format_registry_loaded": format_registry is not None,
            "findings": all_findings,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=(
                f"PipelineFormatContractCheck: {len(all_findings)} findings "
                f"(registry={'ok' if format_registry else 'unavailable'})"
            ),
        )
