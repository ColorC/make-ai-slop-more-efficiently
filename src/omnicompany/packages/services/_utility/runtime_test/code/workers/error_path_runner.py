# [OMNI] origin=claude-code domain=services/code_runtime_test/workers ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:utility.runtime_test.code.error_path_runner_implementation.py"
"""ErrorPathRunnerWorker — 路 2 错误处理 (HARD)."""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from .._dispatch_helper import run_target_subprocess


class ErrorPathRunnerWorker(Worker):
    DESCRIPTION = "路 2 跑 error cases · 验 verdict + diagnosis 关键词."
    FORMAT_IN = "code_runtime_test.target_metadata"
    FORMAT_OUT = "code_runtime_test.error_evidence"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        target_id = input_data.get("target_team_id")
        cases = input_data.get("error_cases") or []

        if not cases:
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    "case_results": [],
                    "verdict_match_pct": 0.0,
                    "keyword_match_pct": 0.0,
                    "error_handling_observation": "无 error cases · 路 2 跳过",
                },
                diagnosis="无 error cases",
            )

        results = []
        verdict_matches = 0
        keyword_matches = 0
        for case in cases:
            name = case.get("name", "?")
            inp = case.get("input") or {}
            expected_verdict = (case.get("expected_verdict") or "FAIL").upper()
            keywords = case.get("diagnosis_keywords") or []

            run = run_target_subprocess(target_id, inp)
            actual_verdict = (run["verdict"] or "").upper()
            diag = (run["diagnosis"] or "").lower()

            verdict_match = actual_verdict == expected_verdict
            keywords_hit = [k for k in keywords if k.lower() in diag]
            keyword_match = bool(keywords_hit) if keywords else verdict_match

            if verdict_match:
                verdict_matches += 1
            if keyword_match:
                keyword_matches += 1

            results.append({
                "name": name,
                "actual_verdict": actual_verdict,
                "expected_verdict": expected_verdict,
                "verdict_match": verdict_match,
                "diagnosis": run["diagnosis"][:300],
                "keywords_hit": keywords_hit,
                "keyword_match": keyword_match,
            })

        n = len(results)
        verdict_pct = verdict_matches / n if n else 0.0
        keyword_pct = keyword_matches / n if n else 0.0

        if verdict_pct == 1.0 and keyword_pct == 1.0:
            obs = f"全部 {n} error cases 命中预期 verdict 与 diagnosis 关键词, 错误处理稳健."
        elif verdict_pct >= 0.5:
            obs = f"{verdict_matches}/{n} verdict 命中, {keyword_matches}/{n} 关键词命中, 部分 error path 不完整."
        else:
            obs = f"仅 {verdict_matches}/{n} verdict 命中, 错误路径处理不达标."

        kind = (
            VerdictKind.PASS if (verdict_pct == 1.0 and keyword_pct == 1.0)
            else (VerdictKind.PARTIAL if verdict_pct >= 0.5 else VerdictKind.FAIL)
        )

        return Verdict(
            kind=kind,
            output={
                "case_results": results,
                "verdict_match_pct": verdict_pct,
                "keyword_match_pct": keyword_pct,
                "error_handling_observation": obs,
            },
            diagnosis=f"错误处理: verdict {verdict_matches}/{n}, keyword {keyword_matches}/{n}",
            confidence=1.0,
        )
