# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.blackboard.orphan_worker_scanner.py"
"""OrphanWorkerScannerWorker — 黑板诊断子域 Worker #4.

Worker 协议:
  FORMAT_IN  = doctor.blackboard.audit_request
  FORMAT_OUT = doctor.blackboard.orphan_worker_report

职责: 静态黑板订阅图扫 · 找孤儿 Worker.

孤儿定义 (Q4): 某 Worker 订阅 Material M, 但 M 在 Team 内无 producer Worker,
且 M 不是 kind.source (source 允许外部触发无 producer 合法).

对齐 MaterialDispatcher.orphan_workers(events) 的运行时诊断 · 本 Worker 做静态版.
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import build_subscription_graph


class OrphanWorkerScannerWorker(Worker):
    """静态扫孤儿 Worker (订阅 Material 无 producer 且非 source · Q4)."""

    DESCRIPTION = (
        "订阅 doctor.blackboard.audit_request · 动态 import Team · "
        "对每个 Worker 检查: FORMAT_IN 订阅的每条 Material 是否有 producer (另一 Worker 的 FORMAT_OUT) "
        "或 kind.source (外部触发). 两者皆无 → 孤儿 Worker. "
        "产出 doctor.blackboard.orphan_worker_report (sink)."
    )
    FORMAT_IN = "doctor.blackboard.audit_request"
    FORMAT_OUT = "doctor.blackboard.orphan_worker_report"

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
            missing: list[str] = []
            for fin in w.format_in:
                producers = g.producers_of.get(fin, [])
                if producers:
                    continue
                m = g.material_by_id(fin)
                # 无 producer 且 Material 未标 source (或干脆未声明) → 孤儿候选
                if m is None or m.kind != "source":
                    missing.append(fin)
            if missing:
                findings.append({
                    "worker_class": w.name,
                    "format_in": w.format_in,
                    "missing_producer_for": missing,
                    "violation": (
                        f"Worker 订阅的 Material {missing} 在 Team 内无 producer 且非 kind.source "
                        "(孤儿 · Q4)"
                    ),
                    "severity": "HIGH",
                    "source_file": w.source_file,
                })

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=f"孤儿 Worker 扫描完成: {len(g.workers)} Worker, {len(findings)} 孤儿",
            output={
                "team_module_path": team_path,
                "findings": findings,
                "worker_count": len(g.workers),
                "orphan_count": len(findings),
            },
        )
