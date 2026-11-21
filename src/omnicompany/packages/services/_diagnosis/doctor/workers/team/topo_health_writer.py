# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-25T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.team.health_record_aggregator.py"
"""TeamTopoHealthWriter — Pipeline 健康档案聚合 (HARD).

**契约变更 #02 (2026-04-25)**: 去 health_grade · severity 归一.
- Finding.level 映射: blocking→critical, degrading→major, advisory→minor, info→丢弃
- verdict: critical→unhealthy, major→uncertain, 其余→healthy
- passed: critical==0

Worker 协议:
  FORMAT_IN  = diag.team.checks
  FORMAT_OUT = diag.team.health-record
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._diagnosis.doctor.health_record_v2 import SCHEMA_VERSION
from omnicompany.protocol.anchor import Verdict, VerdictKind


_KNOWN_CHECK_KEYS = [
    "check_structural",
    "check_format_contract",
    "check_maturity",
    "check_soft_hard",
    "check_narrative",
]


# Finding.level → v2 severity
_LEVEL_TO_SEVERITY = {
    "blocking":  "critical",
    "degrading": "major",
    "advisory":  "minor",
    "info":      None,        # 丢弃
}


class TeamTopoHealthWriter(Worker):
    """Pipeline 拓扑诊断健康档案汇总 (v2 · 不打分 · 不产 grade)."""

    DESCRIPTION = (
        "Pipeline 拓扑诊断汇总: 从 5 个检查器 (structural/format_contract/maturity/soft_hard/narrative) "
        "fan-in 收集 Finding, Finding.level 归一到 critical/major/minor, 产 v2 health-record "
        "(不含 grade, 不打分)."
    )
    FORMAT_IN = "diag.team.checks"
    FORMAT_OUT = "diag.team.health-record"

    def run(self, input_data: Any) -> Verdict:
        pipeline_file = input_data.get("pipeline_file", "")
        pipeline_ids = input_data.get("pipeline_ids", [])

        all_findings: list[dict] = []
        checks_summary: list[dict] = []
        for key in _KNOWN_CHECK_KEYS:
            check = input_data.get(key)
            if check:
                checks_summary.append({
                    "check": check.get("check", key),
                    "passed": check.get("passed", True),
                    "severity": check.get("severity", "INFO"),
                    "count": len(check.get("findings", [])),
                })
                all_findings.extend(check.get("findings", []))

        # 按 level 归 v2 severity
        failures_by_severity: dict[str, list[str]] = {
            "critical": [], "major": [], "minor": [],
        }
        counts = {
            "total_checks": len(checks_summary),
            "passed_checks": sum(1 for c in checks_summary if c.get("passed")),
            "critical": 0, "major": 0, "minor": 0,
        }
        for f in all_findings:
            sev = _LEVEL_TO_SEVERITY.get(f.get("level"))
            if sev is None:
                continue
            pid = f.get("pipeline_id", "?")
            check_id = f.get("check_id", "?")
            desc = f.get("description") or f.get("observation") or ""
            failures_by_severity[sev].append(f"{pid}/{check_id}: {desc}")
            counts[sev] += 1

        passed = counts["critical"] == 0
        verdict = "unhealthy" if counts["critical"] > 0 else (
            "uncertain" if counts["major"] > 0 else "healthy"
        )

        # sort findings 便于可读
        _LEVEL_ORDER = {"blocking": 0, "degrading": 1, "advisory": 2, "info": 3}
        sorted_findings = sorted(
            all_findings,
            key=lambda f: (
                _LEVEL_ORDER.get(f.get("level", "info"), 9),
                f.get("pipeline_id", ""),
                f.get("check_id", ""),
            ),
        )

        per_pipeline: dict[str, list[dict]] = {}
        for f in all_findings:
            pid = f.get("pipeline_id", "unknown")
            per_pipeline.setdefault(pid, []).append(f)

        per_pipeline_summary = {
            pid: {
                "finding_count": len(flist),
                "has_blocking":  any(f.get("level") == "blocking" for f in flist),
                "has_degrading": any(f.get("level") == "degrading" for f in flist),
                "has_advisory":  any(f.get("level") == "advisory" for f in flist),
            }
            for pid, flist in per_pipeline.items()
        }

        summary = (
            f"Pipeline 拓扑检查 {len(pipeline_ids)} 个管线 · "
            f"{len(all_findings)} 个 Finding · "
            f"counts: critical={counts['critical']} major={counts['major']} minor={counts['minor']}"
        )

        health_record = {
            "schema_version": SCHEMA_VERSION,
            "pipeline_file": pipeline_file,
            "pipeline_ids": pipeline_ids,
            "verdict": verdict,
            "passed": passed,
            "failures_by_severity": failures_by_severity,
            "counts": counts,
            "checks": checks_summary,               # 跟 worker/material health_writer 字段一致
            "findings": sorted_findings,            # 保留 raw findings 供下游
            "per_pipeline": per_pipeline_summary,
            "summary": summary,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=health_record,
            diagnosis=f"TeamTopoHealthWriter: verdict={verdict} counts={counts}",
        )
