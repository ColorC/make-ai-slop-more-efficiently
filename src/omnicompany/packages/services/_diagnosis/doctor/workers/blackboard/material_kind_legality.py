# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.blackboard.material_kind_legality_checker.py"
"""MaterialKindLegalityWorker — 黑板诊断子域 Worker #1.

Worker 协议:
  FORMAT_IN  = doctor.blackboard.audit_request
  FORMAT_OUT = doctor.blackboard.kind_legality_report

职责: 扫 Team 订阅图 · 判断每条 Material 的 kind (source/internal/sink) 合法性.

合法性规则 (F-19 / F-16):
- kind.source   无 producer Worker 合法;   有 producer → 违规 (source 应由外部触发)
- kind.internal 必须有 producer 和 consumer; 任一缺失 → 违规 (断链)
- kind.sink     无 consumer Worker 合法;   有 consumer → 违规 (sink 是终态)
- 缺 kind 标签  → 违规 (F-19 硬规则)
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import build_subscription_graph


class MaterialKindLegalityWorker(Worker):
    """诊断 Material kind 合法性 (F-19 / F-16 · 产消关系与 kind 三分对齐)."""

    DESCRIPTION = (
        "订阅 doctor.blackboard.audit_request · 动态 import 指定 team 包, "
        "构建订阅图, 逐条 Material 判 kind 合法性: "
        "kind.source 无 producer / kind.internal 双向连通 / kind.sink 无 consumer / "
        "未声明 kind → 违规. 产出 doctor.blackboard.kind_legality_report (sink)."
    )
    FORMAT_IN = "doctor.blackboard.audit_request"
    FORMAT_OUT = "doctor.blackboard.kind_legality_report"

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

            if not m.has_kind:
                findings.append({
                    "material_id": m.id,
                    "kind": None,
                    "violation": "F-19 kind.* tag 缺失 (tags 必须含 kind.source/internal/sink 之一)",
                    "severity": "HIGH",
                })
                continue

            if m.kind == "source" and producers:
                findings.append({
                    "material_id": m.id,
                    "kind": "source",
                    "violation": f"kind.source 但有 producer Worker: {producers}",
                    "severity": "HIGH",
                })
            elif m.kind == "internal":
                if not producers:
                    findings.append({
                        "material_id": m.id,
                        "kind": "internal",
                        "violation": "kind.internal 但无 producer Worker (断链)",
                        "severity": "HIGH",
                    })
                if not consumers:
                    findings.append({
                        "material_id": m.id,
                        "kind": "internal",
                        "violation": "kind.internal 但无 consumer Worker (断链)",
                        "severity": "HIGH",
                    })
            elif m.kind == "sink" and consumers:
                findings.append({
                    "material_id": m.id,
                    "kind": "sink",
                    "violation": f"kind.sink 但有 consumer Worker: {consumers}",
                    "severity": "HIGH",
                })

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=f"kind 合法性扫描完成: {len(g.materials)} Material, {len(findings)} 违规",
            output={
                "team_module_path": team_path,
                "findings": findings,
                "scanned_count": len(g.materials),
                "violation_count": len(findings),
            },
        )
