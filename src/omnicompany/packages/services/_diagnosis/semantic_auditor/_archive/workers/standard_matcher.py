# [OMNI] origin=claude-code domain=services/semantic_auditor ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:diagnosis.semantic_auditor.standard_matcher.worker.python"
"""StandardMatcherWorker — SemanticAuditor Team Worker #2.

Worker 协议:
  FORMAT_IN  = semantic_auditor.artifact-set
  FORMAT_OUT = semantic_auditor.audit-target-set

职责: 读 standards-index.yaml, 为每个 artifact 按 kind + path_match 匹配适用 standard id 列表。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker

from ..standards_loader import load_standards_index, match_standards


class StandardMatcherWorker(Worker):
    """为每个 Artifact 匹配适用的 standard id 列表。

    输入：上游 ArtifactSelectorWorker 的 output
    输出：audit_targets = list[{artifact: {...}, applicable_standards: [standard_id, ...]}]
    """

    INPUT_KEYS = ["artifacts"]
    DESCRIPTION = (
        "读 standards-index.yaml，为每个 artifact 按 kind + path_match "
        "匹配适用 standard id 列表"
    )
    FORMAT_IN = "semantic_auditor.artifact-set"
    FORMAT_OUT = "semantic_auditor.audit-target-set"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict) or "artifacts" not in input_data:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "input_data 需含 artifacts 字段"},
            )

        root = Path(input_data.get("project_root", "."))
        try:
            index = load_standards_index(root)
        except (FileNotFoundError, ValueError) as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": f"加载 standards-index 失败: {e}"},
            )

        artifacts = input_data["artifacts"]
        if not isinstance(artifacts, list):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "artifacts 必须是 list"},
            )

        targets: list[dict[str, Any]] = []
        unmatched = 0
        for a in artifacts:
            if not isinstance(a, dict):
                continue
            path = a.get("path", "")
            kind = a.get("kind")
            ids = match_standards(kind, path, index)
            if not ids:
                unmatched += 1
                continue
            targets.append({
                "artifact": a,
                "applicable_standards": ids,
            })

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "project_root": str(root),
                "audit_targets": targets,
                "target_count": len(targets),
                "unmatched_artifacts": unmatched,
            },
        )
