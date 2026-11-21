# [OMNI] origin=claude-code domain=services/code_runtime_test/workers ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:utility.runtime_test.code.target_ingress_implementation.py"
"""TargetIngressWorker — Worker #1 (HARD)."""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class TargetIngressWorker(Worker):
    DESCRIPTION = "校 target 注册 · 分类 success/error/reproducibility 用例."
    FORMAT_IN = "code_runtime_test.target_spec"
    FORMAT_OUT = "code_runtime_test.target_metadata"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        target_team_id = input_data.get("target_team_id")
        test_cases = input_data.get("test_cases") or []

        if not target_team_id or not isinstance(target_team_id, str):
            return Verdict(kind=VerdictKind.FAIL, diagnosis="target_team_id 缺失或非法")
        if not isinstance(test_cases, list) or not test_cases:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="test_cases 为空")

        from omnicompany.core.registry import discover, get
        discover()
        if get(target_team_id) is None:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"target_team_id '{target_team_id}' 未注册",
            )

        success_cases = [c for c in test_cases if c.get("kind") == "success"]
        error_cases = [c for c in test_cases if c.get("kind") == "error"]
        reproducibility_cases = [c for c in test_cases if c.get("kind") == "reproducibility"]

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "target_team_id": target_team_id,
                "success_cases": success_cases,
                "error_cases": error_cases,
                "reproducibility_cases": reproducibility_cases,
                "output_extractor": input_data.get("output_extractor", ""),
            },
            diagnosis=(
                f"装载: {len(success_cases)} success + {len(error_cases)} error + "
                f"{len(reproducibility_cases)} repro"
            ),
            confidence=1.0,
        )
