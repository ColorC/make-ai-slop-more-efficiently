# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.blackboard.format_in_mode_declarer.py"
"""FormatInModeCheckerWorker — 黑板诊断子域 Worker #2.

Worker 协议:
  FORMAT_IN  = doctor.blackboard.audit_request
  FORMAT_OUT = doctor.blackboard.mode_check_report

职责: 扫 Team Worker · FORMAT_IN = list[str] 时必须显式声明 FORMAT_IN_MODE = "and" | "or" (R-24).
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import build_subscription_graph


class FormatInModeCheckerWorker(Worker):
    """诊断 FORMAT_IN_MODE 显式声明合规性 (R-24)."""

    DESCRIPTION = (
        "订阅 doctor.blackboard.audit_request · 动态 import Team · "
        "对每个 Worker 检查: FORMAT_IN 是 list[str] 多入时必须显式类属性声明 "
        "FORMAT_IN_MODE='and'|'or', 缺失即违规 (Worker 基类默认 'and' 不算显式). "
        "产出 doctor.blackboard.mode_check_report (sink)."
    )
    FORMAT_IN = "doctor.blackboard.audit_request"
    FORMAT_OUT = "doctor.blackboard.mode_check_report"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        req = input_data.get("doctor.blackboard.audit_request", input_data)
        team_path = req.get("team_module_path", "")
        if not team_path:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="team_module_path 缺失")

        try:
            g = build_subscription_graph(team_path)
        except Exception as exc:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"加载 Team 失败: {type(exc).__name__}: {exc}",
            )

        findings: list[dict] = []
        for w in g.workers:
            if not w.is_multi_input:
                continue
            if w.format_in_mode is None:
                findings.append({
                    "worker_class": w.name,
                    "format_in": w.format_in,
                    "mode_declared": False,
                    "violation": "R-24 · FORMAT_IN list[str] 多入但未显式声明 FORMAT_IN_MODE",
                    "severity": "HIGH",
                    "source_file": w.source_file,
                })
            elif w.format_in_mode not in ("and", "or"):
                findings.append({
                    "worker_class": w.name,
                    "format_in": w.format_in,
                    "mode_declared": True,
                    "violation": f"R-24 · FORMAT_IN_MODE 非法值 '{w.format_in_mode}' (应为 'and' 或 'or')",
                    "severity": "HIGH",
                    "source_file": w.source_file,
                })

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=f"FORMAT_IN_MODE 扫描完成: {len(g.workers)} Worker, {len(findings)} 违规",
            output={
                "team_module_path": team_path,
                "findings": findings,
                "scanned_count": len(g.workers),
                "violation_count": len(findings),
            },
        )
