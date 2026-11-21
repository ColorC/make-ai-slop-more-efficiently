# [OMNI] origin=claude-code domain=services/code_runtime_test/workers ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:utility.runtime_test.code.portrait_assembler_implementation.py"
"""PortraitAssemblerWorker — Worker #5 (HARD · sink)."""
from __future__ import annotations

import time
from typing import Any, ClassVar

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


def _pick(input_data: dict, mirror_key: str) -> dict:
    m = input_data.get(mirror_key)
    return dict(m) if isinstance(m, dict) else {}


class PortraitAssemblerWorker(Worker):
    DESCRIPTION = "装画像 sink · 3 路证据汇总 + verdict 派生 + 自然语言段落."
    FORMAT_IN: ClassVar[list[str]] = [
        "code_runtime_test.golden_evidence",
        "code_runtime_test.error_evidence",
        "code_runtime_test.reproducibility_evidence",
        "code_runtime_test.target_metadata",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "code_runtime_test.portrait"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        golden = _pick(input_data, "_from_GoldenContractRunnerWorker")
        error = _pick(input_data, "_from_ErrorPathRunnerWorker")
        repro = _pick(input_data, "_from_ReproducibilityRunnerWorker")
        meta = _pick(input_data, "_from_TargetIngressWorker")

        target_id = meta.get("target_team_id") or input_data.get("target_team_id", "?")

        # ── 物理度量 ──
        byte_exact_pct = float(golden.get("byte_exact_pct") or 0.0)
        mean_byte_diff = float(golden.get("mean_byte_diff_count") or 0.0)
        verdict_match_pct = float(error.get("verdict_match_pct") or 0.0)
        keyword_match_pct = float(error.get("keyword_match_pct") or 0.0)
        repro_pct = float(repro.get("byte_identical_pct") or 0.0)

        # ── 派生 verdict ──
        # 路 1 标杆: byte_exact_pct == 1.0 严要求 (代码产物有 ground truth, 不全等就不算 PASS)
        # 路 2 错误处理: verdict + keyword 都 == 1.0
        # 路 3 重现性: byte_identical == 1.0
        signals_pass = 0
        signals_total = 0

        # 仅 applicable 时计入 (无 cases 不参与)
        golden_applicable = bool(golden.get("case_results"))
        if golden_applicable:
            signals_total += 1
            if byte_exact_pct == 1.0:
                signals_pass += 1

        error_applicable = bool(error.get("case_results"))
        if error_applicable:
            signals_total += 1
            if verdict_match_pct == 1.0 and keyword_match_pct == 1.0:
                signals_pass += 1

        repro_applicable = bool(repro.get("case_results"))
        if repro_applicable:
            signals_total += 1
            if repro_pct == 1.0:
                signals_pass += 1

        if signals_total == 0:
            verdict_kind = VerdictKind.FAIL
            verdict_str = "FAIL"
        else:
            pass_rate = signals_pass / signals_total
            if pass_rate >= 0.999:  # 全过
                verdict_kind = VerdictKind.PASS
                verdict_str = "PASS"
            elif pass_rate >= 0.5:
                verdict_kind = VerdictKind.PARTIAL
                verdict_str = "PARTIAL"
            else:
                verdict_kind = VerdictKind.FAIL
                verdict_str = "FAIL"

        # ── 做得好/漏 ──
        what_well: list[str] = []
        what_misses: list[str] = []

        if golden_applicable:
            obs = golden.get("contract_observation", "")
            if byte_exact_pct == 1.0:
                what_well.append(f"标杆对标: {obs}")
            else:
                what_misses.append(f"标杆对标偏差: {obs}")
                # 列出具体 fail 的 case
                for r in golden.get("case_results", []):
                    if not r.get("byte_exact"):
                        what_misses.append(
                            f"用例 {r.get('name')} 字节差 {r.get('byte_diff_count')} (verdict={r.get('verdict')})"
                        )

        if error_applicable:
            obs = error.get("error_handling_observation", "")
            if verdict_match_pct == 1.0 and keyword_match_pct == 1.0:
                what_well.append(f"错误处理: {obs}")
            else:
                what_misses.append(f"错误处理不全: {obs}")
                for r in error.get("case_results", []):
                    if not r.get("verdict_match"):
                        what_misses.append(
                            f"用例 {r.get('name')} verdict 失配: 期望 {r.get('expected_verdict')} 实际 {r.get('actual_verdict')}"
                        )

        if repro_applicable:
            obs = repro.get("reproducibility_observation", "")
            if repro_pct == 1.0:
                what_well.append(f"重现性: {obs}")
            else:
                what_misses.append(f"重现性差: {obs}")

        if not what_well:
            what_well.append("各路证据均未显示明显优势")
        if not what_misses:
            what_misses.append("各路证据均未发现退化信号")

        # ── 画像段落 ──
        portrait = (
            f"team `{target_id}` 经 3 路对标验证: "
            + (f"标杆对标 {byte_exact_pct:.0%} byte-exact (平均 {mean_byte_diff:.0f} 字节差)" if golden_applicable else "标杆 N/A")
            + " · "
            + (f"错误处理 verdict {verdict_match_pct:.0%} + keyword {keyword_match_pct:.0%}" if error_applicable else "错误 N/A")
            + " · "
            + (f"重现性 {repro_pct:.0%} byte-identical" if repro_applicable else "重现 N/A")
            + f". 综合 {signals_pass}/{signals_total} 路达标 → {verdict_str}. "
        )
        if golden.get("contract_observation"):
            portrait += f"标杆层: {golden['contract_observation']} "
        if error.get("error_handling_observation"):
            portrait += f"错误层: {error['error_handling_observation']} "
        if repro.get("reproducibility_observation"):
            portrait += f"重现层: {repro['reproducibility_observation']}"
        portrait = portrait.strip()
        if len(portrait) < 150:
            portrait += " (画像段落兜底: 详细多路证据见 evidence_paths 字段, 由 L1+L2 抽样审定.)"

        report = {
            "verdict": verdict_str,
            "target_team_id": target_id,
            "evidence_paths": {
                "golden": golden,
                "error": error,
                "reproducibility": repro,
            },
            "portrait_paragraph": portrait,
            "what_target_does_well": what_well,
            "what_target_misses": what_misses,
            "physical_metrics": {
                "byte_exact_pct": byte_exact_pct,
                "mean_byte_diff_count": mean_byte_diff,
                "verdict_match_pct": verdict_match_pct,
                "keyword_match_pct": keyword_match_pct,
                "repro_pct": repro_pct,
                "golden_applicable": golden_applicable,
                "error_applicable": error_applicable,
                "repro_applicable": repro_applicable,
                "signals_pass": signals_pass,
                "signals_total": signals_total,
            },
            "run_id": f"crt_{int(time.time())}",
        }

        return Verdict(
            kind=verdict_kind,
            output=report,
            diagnosis=f"装配完成: {verdict_str} · {signals_pass}/{signals_total} 路达标",
            confidence=1.0,
        )
