# [OMNI] origin=claude-code domain=omnicompany/knowledge ts=2026-04-21T00:00:00Z type=router
# [OMNI] material_id="material:learning.knowledge.audit_worker.execution.py"
"""KBAuditWorker — OmniKB 全量 5 类一致性审计 (Stage 3 独立文件).

Worker 协议:
  FORMAT_IN  = kb.audit_request
  FORMAT_OUT = kb.audit_report

5 类审计: validation / anchor drift / orphan routers / staleness / format coverage。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.packages.services._learning.knowledge import (
    AuditReport,
    run_full_audit,
)
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import _project_root


class KBAuditWorker(Worker):
    """全量审计: validation + anchor drift + orphan routers + staleness + coverage。

    input_data 不需要字段 (空 dict 即可), 审计范围是整个 project_root。

    返回的 output 是 AuditReport 的 dict 形式 (summary/has_issues/<5 类检查>)。

    Verdict 判定:
      PASS    若 has_issues=false
      PARTIAL 若只有 info/warning 级别
      FAIL    若有 error 级别 (如重复 id)
    """

    DESCRIPTION = "OmniKB 全量审计 Worker: 5 类一致性检查聚合"
    FORMAT_IN = "kb.audit_request"
    FORMAT_OUT = "kb.audit_report"

    def __init__(self, *, project_root: Path | None = None) -> None:
        self._project_root = project_root or _project_root()

    def run(self, input_data: Any) -> Verdict:
        try:
            report: AuditReport = run_full_audit(self._project_root)
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"audit 异常: {e}",
            )

        output = {
            "summary": report.summary(),
            "has_issues": report.has_issues(),
            "validation_issues": [
                {
                    "id": i.entry_id,
                    "field": i.field,
                    "message": i.message,
                    "severity": i.severity,
                }
                for i in report.validation_issues
            ],
            "anchor_drifts": [
                {"karch_id": d.karch_id, "anchor": d.anchor, "reason": d.reason}
                for d in report.anchor_drifts
            ],
            "orphan_routers": [
                {"kind": o.kind, "name": o.name, "source": o.source}
                for o in report.orphan_routers
            ],
            "staleness": {
                "stale_krouters": report.staleness.stale_krouters,
                "old_draft": report.staleness.old_draft,
            },
            "format_coverage": {
                "both": report.format_coverage.both,
                "knowledge_only": report.format_coverage.knowledge_only,
                "code_only": report.format_coverage.code_only,
            },
        }

        has_error = any(
            i.severity == "error" for i in report.validation_issues
        )
        if has_error:
            kind = VerdictKind.FAIL
        elif report.has_issues():
            kind = VerdictKind.PARTIAL
        else:
            kind = VerdictKind.PASS

        return Verdict(
            kind=kind,
            output=output,
            confidence=1.0,
            diagnosis=report.summary(),
            granted_tags=["domain.knowledge", "stage.audited"],
        )
