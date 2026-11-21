# [OMNI] origin=claude-code domain=services/code_runtime_test/workers ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:utility.runtime_test.code.golden_contract_runner_implementation.py"
"""GoldenContractRunnerWorker — 路 1 标杆对标 (HARD)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from .._dispatch_helper import extract_actual, run_target_subprocess

logger = logging.getLogger(__name__)


def _diff_count(a: str, b: str) -> tuple[int, int]:
    """返 (byte_diff_count, line_diff_count)."""
    if a == b:
        return 0, 0
    a_b = a.encode("utf-8")
    b_b = b.encode("utf-8")
    byte_diff = abs(len(a_b) - len(b_b)) + sum(1 for x, y in zip(a_b, b_b) if x != y)
    a_lines = a.splitlines()
    b_lines = b.splitlines()
    line_diff = abs(len(a_lines) - len(b_lines))
    for x, y in zip(a_lines, b_lines):
        if x != y:
            line_diff += 1
    return byte_diff, line_diff


class GoldenContractRunnerWorker(Worker):
    DESCRIPTION = "路 1 跑 success cases · diff vs expected · 计 byte_diff_count."
    FORMAT_IN = "code_runtime_test.target_metadata"
    FORMAT_OUT = "code_runtime_test.golden_evidence"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        target_id = input_data.get("target_team_id")
        cases = input_data.get("success_cases") or []
        extractor = input_data.get("output_extractor")

        if not cases:
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    "case_results": [],
                    "byte_exact_pct": 0.0,
                    "mean_byte_diff_count": 0.0,
                    "contract_observation": "无 success cases · 路 1 跳过",
                },
                diagnosis="无 success cases",
            )

        results = []
        byte_exacts = 0
        total_byte_diff = 0
        for case in cases:
            name = case.get("name", "?")
            inp = case.get("input") or {}
            exp_path = case.get("expected_path", "")

            run = run_target_subprocess(target_id, inp)
            actual = extract_actual(run["output"], extractor)
            elapsed = run["elapsed_sec"]
            run_verdict = run["verdict"]

            if not exp_path or not Path(exp_path).is_file():
                results.append({
                    "name": name,
                    "verdict": run_verdict,
                    "byte_exact": False,
                    "byte_diff_count": -1,
                    "line_diff_count": -1,
                    "elapsed_sec": elapsed,
                    "diagnosis": f"expected_path '{exp_path}' 不存在",
                })
                continue

            try:
                expected = Path(exp_path).read_text(encoding="utf-8")
            except Exception as e:
                results.append({
                    "name": name,
                    "verdict": run_verdict,
                    "byte_exact": False,
                    "byte_diff_count": -1,
                    "line_diff_count": -1,
                    "elapsed_sec": elapsed,
                    "diagnosis": f"读 expected 失败: {e}",
                })
                continue

            byte_diff, line_diff = _diff_count(actual, expected)
            byte_exact = byte_diff == 0 and run_verdict == "PASS"
            if byte_exact:
                byte_exacts += 1
            total_byte_diff += byte_diff
            entry = {
                "name": name,
                "verdict": run_verdict,
                "byte_exact": byte_exact,
                "byte_diff_count": byte_diff,
                "line_diff_count": line_diff,
                "elapsed_sec": elapsed,
            }
            if not byte_exact:
                entry["diagnosis"] = (
                    f"target verdict={run_verdict} · 字节差 {byte_diff} 行差 {line_diff}; "
                    f"actual 长度 {len(actual)} expected 长度 {len(expected)}"
                )
            results.append(entry)

        n = len(results)
        byte_exact_pct = byte_exacts / n if n else 0.0
        mean_byte_diff = total_byte_diff / n if n else 0.0

        if byte_exact_pct == 1.0:
            obs = f"全部 {n} 个 success cases 字节级完全等同 expected, 标杆对标完美通过."
        elif byte_exact_pct >= 0.5:
            obs = f"{byte_exacts}/{n} 字节级完全等同, 平均 {mean_byte_diff:.0f} 字节差异. 部分用例与标杆有偏差."
        else:
            obs = f"仅 {byte_exacts}/{n} 字节级等同, 多数用例与标杆显著偏差 (平均 {mean_byte_diff:.0f} 字节差)."

        kind = VerdictKind.PASS if byte_exact_pct == 1.0 else (
            VerdictKind.PARTIAL if byte_exact_pct >= 0.5 else VerdictKind.FAIL
        )

        return Verdict(
            kind=kind,
            output={
                "case_results": results,
                "byte_exact_pct": byte_exact_pct,
                "mean_byte_diff_count": mean_byte_diff,
                "contract_observation": obs,
            },
            diagnosis=f"标杆对标: {byte_exacts}/{n} 完全等同",
            confidence=1.0,
        )
