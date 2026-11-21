# [OMNI] origin=claude-code domain=services/pipeline_ci ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.pipeline_ci.ci_gate_evaluator.worker.python"
"""CIGateWorker — CI 门控：critical_count > 0 → FAIL，否则 PASS。"""
from __future__ import annotations

from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker


class CIGateWorker(Worker):
    """CI 门控：读取 BatchAuditorWorker 聚合报告，有 critical 则 FAIL 阻断 CI。"""

    DESCRIPTION = (
        "CI 门控节点：读取 BatchAuditorWorker 的聚合报告，"
        "若 critical_count > 0 则返回 VerdictKind.FAIL（阻断 CI），"
        "否则返回 VerdictKind.PASS（绿灯）。"
        "输出标准 CI 报告 JSON，含每域详情和顶层通过/失败状态。"
    )
    FORMAT_IN = "pipeline_ci.ci-report"
    FORMAT_OUT = "pipeline_ci.ci-report"

    def run(self, input_data: Any) -> Verdict:
        critical = input_data.get("critical_count", 0)
        total = input_data.get("total_domains", 0)
        passed = input_data.get("passed_domains", 0)
        failed = input_data.get("failed_domains", 0)
        warnings = input_data.get("warning_count", 0)

        print(f"\n{'='*60}")
        print("  Pipeline CI Report")
        print(f"{'='*60}")
        print(f"  Total domains : {total}")
        print(f"  Passed        : {passed}")
        print(f"  Failed        : {failed}")
        print(f"  Critical      : {critical}")
        print(f"  Warnings      : {warnings}")
        print(f"{'='*60}")

        for domain in input_data.get("domain_results", []):
            status_icon = "✓" if domain["status"] == "PASS" else "✗"
            print(f"  {status_icon} {domain['domain_name']} "
                  f"({domain['critical']} critical, {domain['warning']} warnings)")
            for issue in domain.get("issues", []):
                if issue["severity"] == "critical":
                    print(f"      [CRITICAL] {issue['check']}: {issue['message'][:100]}")

        print(f"{'='*60}\n")

        if critical > 0:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"CI 失败: {critical} 个 critical 问题，{failed} 个域不通过",
                output=input_data,
            )

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=f"CI 通过: {total} 个域，{warnings} 个 warnings（无 critical）",
            output=input_data,
        )
