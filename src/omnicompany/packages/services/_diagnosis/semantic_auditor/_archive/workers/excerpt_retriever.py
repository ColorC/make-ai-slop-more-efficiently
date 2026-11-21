# [OMNI] origin=claude-code domain=services/semantic_auditor ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:diagnosis.semantic_auditor.excerpt_extractor.worker.python"
"""ExcerptRetrieverWorker — SemanticAuditor Team Worker #3.

Worker 协议:
  FORMAT_IN  = semantic_auditor.audit-target-set
  FORMAT_OUT = semantic_auditor.audit-excerpt-set

职责: 为每个 audit_target × standard_id 取标准内容摘录 (full / section 切块)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker

from ..standards_loader import load_standards_index, retrieve_excerpt


class ExcerptRetrieverWorker(Worker):
    """为每个 audit_target × standard_id 取标准内容摘录。

    输入：上游 StandardMatcherWorker 的 output
    输出：excerpts = list[{target: {...}, standard_id: "...", excerpt_text: "...", excerpt_len: N}]

    excerpt_strategy=full → 整份
    excerpt_strategy=section → 按 key_sections 切块
    """

    INPUT_KEYS = ["audit_targets"]
    DESCRIPTION = (
        "按 excerpt_strategy 取每条 standard 的摘录，"
        "产出 (target, standard_id, excerpt_text) 三元组清单"
    )
    FORMAT_IN = "semantic_auditor.audit-target-set"
    FORMAT_OUT = "semantic_auditor.audit-excerpt-set"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict) or "audit_targets" not in input_data:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "input_data 需含 audit_targets 字段"},
            )

        root = Path(input_data.get("project_root", "."))
        try:
            index = load_standards_index(root)
        except (FileNotFoundError, ValueError) as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": f"加载 standards-index 失败: {e}"},
            )

        targets = input_data["audit_targets"]
        if not isinstance(targets, list):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "audit_targets 必须是 list"},
            )

        excerpts: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []

        for t in targets:
            if not isinstance(t, dict):
                continue
            artifact = t.get("artifact", {})
            for sid in t.get("applicable_standards", []):
                try:
                    text = retrieve_excerpt(sid, index)
                    excerpts.append({
                        "target": artifact,
                        "standard_id": sid,
                        "excerpt_text": text,
                        "excerpt_len": len(text),
                    })
                except (ValueError, FileNotFoundError) as e:
                    failed.append({
                        "target_path": artifact.get("path", ""),
                        "standard_id": sid,
                        "reason": str(e),
                    })

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "project_root": str(root),
                "excerpts": excerpts,
                "excerpt_count": len(excerpts),
                "failed_retrievals": failed,
            },
        )
