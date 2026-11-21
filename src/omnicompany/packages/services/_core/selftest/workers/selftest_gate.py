# [OMNI] origin=claude-code domain=omnicompany/selftest ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.selftest.pass_fail.decision_gate.py"
"""SelftestGateWorker — Selftest Team Worker #3.

Worker 协议:
  FORMAT_IN  = selftest.selftest-report
  FORMAT_OUT = selftest.selftest-report  (同 format, 主干恒定模式)

职责: failed_checks > 0 则 FAIL 并打印可读报告, 否则 PASS。
"""
from __future__ import annotations

from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.packages.services._core.omnicompany import Worker


class SelftestGateWorker(Worker):
    """门控: failed_checks > 0 则 FAIL, 并打印可读报告。"""

    DESCRIPTION = (
        "Selftest 门控节点: 读取 FunctionalTester Worker 的综合报告, "
        "若 failed_checks > 0 则返回 VerdictKind.FAIL 并打印所有失败项, "
        "否则返回 VerdictKind.PASS。"
    )
    FORMAT_IN = "selftest.selftest-report"
    FORMAT_OUT = "selftest.selftest-report"

    def run(self, input_data: Any) -> Verdict:
        total = input_data.get("total_checks", 0)
        passed = input_data.get("passed_checks", 0)
        failed = input_data.get("failed_checks", 0)
        total_pipelines = input_data.get("total_pipelines", 0)
        failed_pipelines = input_data.get("failed_pipelines", 0)

        print(f"\n{'='*62}")
        print("  OmniCompany Selftest Report")
        print(f"{'='*62}")
        print(f"  Registered pipelines : {total_pipelines}  "
              f"({failed_pipelines} failed to load)")
        print(f"  Total checks         : {total}")
        print(f"  Passed               : {passed}")
        print(f"  Failed               : {failed}")
        print(f"{'='*62}")

        for r in input_data.get("pipeline_results", []):
            if not r["ok"]:
                print(f"  FAIL [registry] {r['name']}")
                for err in r.get("errors", []):
                    print(f"      ERROR: {err[:120]}")
            elif r.get("warnings"):
                print(f"  WARN [registry] {r['name']}")
                for w in r.get("warnings", []):
                    print(f"      WARN: {w[:120]}")

        for r in input_data.get("functional_results", []):
            icon = "PASS" if r["ok"] else "FAIL"
            detail = r.get("detail", r.get("error", ""))
            print(f"  {icon} [functional] {r['name']}: {detail[:100]}")

        print(f"{'='*62}\n")

        if failed > 0:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"Selftest 失败: {failed}/{total} 项检查不通过",
                output=input_data,
            )

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=f"Selftest 全部通过: {total} 项检查, {total_pipelines} 个管线正常",
            output=input_data,
        )
