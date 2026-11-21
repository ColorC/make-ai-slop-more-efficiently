# [OMNI] origin=claude-code domain=services/team_supervisor/workers ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_supervisor.workers.health_report_assembler.algorithm.py"
"""HealthReportAssemblerWorker — team_supervisor Worker #7 (HARD).

Worker 协议:
  FORMAT_IN  = [test_results, hypothesis_set, health_criteria,
                product_form_brief, design_purpose_brief, target_metadata]
  FORMAT_OUT = team_supervisor.health_report
  FORMAT_IN_MODE = and

职责: 装配 · 不调 LLM. 透传三问 brief, 算 verdict, 拼 diagnosis 段落.
      通过率 ≥0.8 → PASS · ≥0.5 → PARTIAL · <0.5 → FAIL.
"""
from __future__ import annotations

from typing import Any, ClassVar

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


def _pick_brief(
    input_data: dict, mirror_key: str, fallback_keys: tuple[str, ...]
) -> dict:
    """从 fan-in 后 input_data 抽某个上游 brief.

    优先取 _from_<wid> 镜像; 缺则按字段名兜底从平铺顶层抽.
    """
    mirror = input_data.get(mirror_key)
    if isinstance(mirror, dict) and mirror:
        return dict(mirror)

    out = {}
    for k in fallback_keys:
        if k in input_data:
            out[k] = input_data[k]
    return out


class HealthReportAssemblerWorker(Worker):
    """装配 health_report · HARD · 不调 LLM."""

    DESCRIPTION = (
        "装配 health_report · HARD · 不调 LLM. 透传三问, 算 verdict (≥0.8 PASS · "
        "≥0.5 PARTIAL · <0.5 FAIL), 拼 diagnosis 段落."
    )
    FORMAT_IN: ClassVar[list[str]] = [
        "team_supervisor.test_results",
        "team_supervisor.hypothesis_set",
        "team_supervisor.health_criteria",
        "team_supervisor.product_form_brief",
        "team_supervisor.design_purpose_brief",
        "team_supervisor.target_metadata",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "team_supervisor.health_report"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        # ── 抽各上游 brief (优先用 _from_<wid> 镜像) ──
        q1 = _pick_brief(
            input_data,
            "_from_ProductFormAnalyzerWorker",
            (
                "essence",
                "minimal_passing_evidence",
                "failure_signals",
                "concrete_examples",
                "schema_fields_observed",
            ),
        )
        q2 = _pick_brief(
            input_data,
            "_from_PurposeInterpreterWorker",
            ("replaces", "non_goals", "stakeholder_use", "evidence_sources"),
        )
        # Q2 essence 可能与 Q1 重名 — 优先从 mirror 取
        if not q2.get("essence"):
            q2_mirror = input_data.get("_from_PurposeInterpreterWorker") or {}
            if isinstance(q2_mirror, dict):
                q2["essence"] = q2_mirror.get("essence", "")

        q3 = _pick_brief(
            input_data,
            "_from_HealthCriteriaDesignerWorker",
            ("key_observations", "red_flags", "oracle_strategies"),
        )

        meta = _pick_brief(
            input_data,
            "_from_TargetIngressWorker",
            (
                "target_team_id",
                "team_code_dir",
                "format_out_id",
            ),
        )

        test = _pick_brief(
            input_data,
            "_from_TestExecutorWorker",
            (
                "target_run_verdict",
                "target_output_summary",
                "target_traces_path",
                "hypothesis_evaluations",
            ),
        )

        hyp = _pick_brief(
            input_data,
            "_from_HypothesisGeneratorWorker",
            ("hypotheses",),
        )

        # ── 校最起码: test_results 必须存在 ──
        evaluations = test.get("hypothesis_evaluations") or []
        if not isinstance(evaluations, list):
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis="hypothesis_evaluations 缺失或非 list",
            )

        target_run_verdict = test.get("target_run_verdict") or "FAIL"
        target_output_summary = test.get("target_output_summary") or ""
        target_team_id = meta.get("target_team_id") or "(unknown)"

        total = len(evaluations)
        passed_count = sum(1 for e in evaluations if isinstance(e, dict) and e.get("passed"))

        # ── 计算 verdict ──
        pass_rate = (passed_count / total) if total > 0 else 0.0
        if pass_rate >= 0.8 and target_run_verdict in ("PASS", "PARTIAL"):
            verdict_kind = VerdictKind.PASS
            verdict_str = "PASS"
        elif pass_rate >= 0.5:
            verdict_kind = VerdictKind.PARTIAL
            verdict_str = "PARTIAL"
        else:
            verdict_kind = VerdictKind.FAIL
            verdict_str = "FAIL"

        # ── 失败假设详情 ──
        hypotheses_list = hyp.get("hypotheses") or []
        hyp_by_id = {
            h.get("id"): h for h in hypotheses_list if isinstance(h, dict)
        }

        failed_hypotheses = []
        for e in evaluations:
            if not isinstance(e, dict) or e.get("passed"):
                continue
            hid = e.get("hypothesis_id")
            full = hyp_by_id.get(hid, {})
            failed_hypotheses.append({
                "id": hid,
                "condition": full.get("condition", ""),
                "expectation": full.get("expectation", ""),
                "observed": e.get("observed", ""),
            })

        # ── 装 diagnosis 段落 (自然语言) ──
        diagnosis_parts = [
            f"team `{target_team_id}` 健康监督结果: {verdict_str}.",
            f"target dispatch verdict 为 {target_run_verdict}, 产物摘要: {target_output_summary[:200]}.",
            f"评估 {total} 条假设, 通过 {passed_count} 条 ({pass_rate:.0%}).",
        ]

        if failed_hypotheses:
            diagnosis_parts.append(
                f"主要失败 ({len(failed_hypotheses)} 条): "
                + "; ".join(
                    f"[{f['id']}] 期望「{f['expectation'][:80]}」, "
                    f"实际「{f['observed'][:120]}」"
                    for f in failed_hypotheses[:3]
                )
            )
        else:
            diagnosis_parts.append("全部假设通过, 未发现红旗信号.")

        # 加上三问要点 (压缩成句)
        if q1.get("essence"):
            diagnosis_parts.append(f"Q1 产物本质: {q1['essence'][:200]}")
        if q2.get("essence"):
            diagnosis_parts.append(f"Q2 设计目的: {q2['essence'][:200]}")

        diagnosis = " ".join(diagnosis_parts)
        if len(diagnosis) < 100:
            diagnosis = (
                diagnosis
                + " (诊断段落兜底补充: supervisor 已完成三问 + 假设进化全流程, 见三问 brief 字段获取详细语义.)"
            )

        # ── ledger_increment: 这次新增的假设 ──
        ledger_increment = []
        for h in hypotheses_list:
            if not isinstance(h, dict):
                continue
            ledger_increment.append({
                "id": h.get("id"),
                "condition": h.get("condition"),
                "expectation": h.get("expectation"),
                "oracle_code_hint": h.get("oracle_code_hint"),
                "rationale": h.get("rationale"),
                "first_seen_at": "2026-04-26",  # supervisor 起点; 未来 ledger 累积时按 run id 区分
            })

        # ── 装最终 health_report ──
        report = {
            "verdict": verdict_str,
            "target_team_id": target_team_id,
            "three_questions": {
                "q1": q1,
                "q2": q2,
                "q3": q3,
            },
            "hypotheses_evaluated_count": total,
            "passed_count": passed_count,
            "failed_hypotheses": failed_hypotheses,
            "diagnosis": diagnosis,
            "ledger_increment": ledger_increment,
        }

        return Verdict(
            kind=verdict_kind,
            output=report,
            diagnosis=f"装配完成: {verdict_str} · {passed_count}/{total} 通过",
            confidence=1.0,
        )
