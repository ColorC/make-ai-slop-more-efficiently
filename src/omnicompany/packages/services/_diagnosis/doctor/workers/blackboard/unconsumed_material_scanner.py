# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.blackboard.unconsumed_material_scanner.py"
"""UnconsumedMaterialScannerWorker — 黑板诊断子域 Worker #5.

Worker 协议:
  FORMAT_IN  = doctor.blackboard.audit_request
  FORMAT_OUT = doctor.blackboard.unconsumed_material_report

职责: 静态黑板订阅图扫 · 找未消费 Material.

未消费定义 (Q4): 某 Material 被某 Worker 产出 (另一 Worker 的 FORMAT_OUT), 但
Team 内无 Worker 订阅 (FORMAT_IN), 且该 Material 不是 kind.sink (sink 无 consumer 合法).

对齐 MaterialDispatcher.unconsumed_materials(events) 的运行时诊断 · 静态版.
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import build_subscription_graph


class UnconsumedMaterialScannerWorker(Worker):
    """静态扫未消费 Material (产出但无 consumer 且非 sink · Q4)."""

    DESCRIPTION = (
        "订阅 doctor.blackboard.audit_request · 动态 import Team · "
        "对每条 Material 检查: 若有 producer (某 Worker FORMAT_OUT) 但无 consumer (Worker FORMAT_IN), "
        "且该 Material 不是 kind.sink → 疑似冗余 (Q4). "
        "产出 doctor.blackboard.unconsumed_material_report (sink)."
    )
    FORMAT_IN = "doctor.blackboard.audit_request"
    FORMAT_OUT = "doctor.blackboard.unconsumed_material_report"

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
        for m in g.materials:
            producers = g.producers_of.get(m.id, [])
            consumers = g.consumers_of.get(m.id, [])
            if not producers:
                continue  # 无 producer, 不是"产了没人消"
            if consumers:
                continue
            if m.kind == "sink":
                continue  # sink 合法
            findings.append({
                "material_id": m.id,
                "producer": producers,
                "kind": m.kind,
                "violation": (
                    f"Material 被 {producers} 产出但无 Worker 订阅, 且 kind={m.kind!r} 非 sink (Q4 冗余)"
                ),
                "severity": "MEDIUM",
            })

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=f"未消费 Material 扫描完成: {len(g.materials)} Material, {len(findings)} 冗余",
            output={
                "team_module_path": team_path,
                "findings": findings,
                "material_count": len(g.materials),
                "unconsumed_count": len(findings),
            },
        )
