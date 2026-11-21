# [OMNI] origin=claude-code domain=services/code_runtime_test/workers ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:utility.runtime_test.code.reproducibility_runner_implementation.py"
"""ReproducibilityRunnerWorker — 路 3 重现性 (HARD)."""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from .._dispatch_helper import extract_actual, run_target_subprocess


class ReproducibilityRunnerWorker(Worker):
    DESCRIPTION = "路 3 同 input 跑 2 次 · byte-identical."
    FORMAT_IN = "code_runtime_test.target_metadata"
    FORMAT_OUT = "code_runtime_test.reproducibility_evidence"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        target_id = input_data.get("target_team_id")
        cases = input_data.get("reproducibility_cases") or []
        extractor = input_data.get("output_extractor")

        if not cases:
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    "case_results": [],
                    "byte_identical_pct": 0.0,
                    "reproducibility_observation": "无 reproducibility cases · 路 3 跳过",
                },
                diagnosis="无 repro cases",
            )

        results = []
        identical = 0
        for case in cases:
            name = case.get("name", "?")
            inp = case.get("input") or {}

            run1 = run_target_subprocess(target_id, inp)
            actual1 = extract_actual(run1["output"], extractor)
            run2 = run_target_subprocess(target_id, inp)
            actual2 = extract_actual(run2["output"], extractor)

            byte_identical = actual1 == actual2 and run1["verdict"] == run2["verdict"] == "PASS"
            diff_bytes = sum(1 for a, b in zip(actual1.encode(), actual2.encode()) if a != b) + abs(len(actual1) - len(actual2))

            if byte_identical:
                identical += 1
            results.append({
                "name": name,
                "run1_byte_count": len(actual1.encode("utf-8")),
                "run2_byte_count": len(actual2.encode("utf-8")),
                "byte_identical": byte_identical,
                "diff_byte_count": diff_bytes,
            })

        n = len(results)
        identical_pct = identical / n if n else 0.0

        if identical_pct == 1.0:
            obs = f"全部 {n} 次同 input 重跑产 byte-identical 输出, 重现性完美."
        elif identical_pct >= 0.5:
            obs = f"{identical}/{n} 重现成功, 部分 case 跨次有差异 (LLM 抖动或非确定性)."
        else:
            obs = f"重现性差: 仅 {identical}/{n} byte-identical, target 跨次输出不一致."

        kind = (
            VerdictKind.PASS if identical_pct == 1.0
            else (VerdictKind.PARTIAL if identical_pct >= 0.5 else VerdictKind.FAIL)
        )

        return Verdict(
            kind=kind,
            output={
                "case_results": results,
                "byte_identical_pct": identical_pct,
                "reproducibility_observation": obs,
            },
            diagnosis=f"重现性: {identical}/{n}",
            confidence=1.0,
        )
