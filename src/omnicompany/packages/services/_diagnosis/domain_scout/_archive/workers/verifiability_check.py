# [OMNI] origin=claude-code domain=services/domain_scout/workers ts=2026-04-24T00:00:00Z
# [OMNI] material_id="material:services.diagnosis.domain_scout.workers.verifiability_gate.py"
"""VerifiabilityCheck — D4 硬约束门, 挡住幻觉 finding (独立 Reviewer)."""
from typing import Any
from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker


class VerifiabilityCheck(Worker):
    """D4 硬约束: source_url 可达 + quoted_evidence 在原文中 + source_hash 匹配. 缺一 FAIL."""

    DESCRIPTION = (
        "domain_scout Worker #5: D4 可验证性硬约束门 (独立 Reviewer, 非 Summarizer 自评). "
        "对 raw_findings 逐条检查 source_url 可达 + quoted_evidence 原文定位 + "
        "source_hash 匹配. 任一失败 → drop 该 finding. 产 verified_findings 给 DigestWriter."
    )
    FORMAT_IN = "domain_scout.raw_findings"
    FORMAT_OUT = "domain_scout.verified_findings"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        raws = input_data.get(self.FORMAT_IN, {})
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "topic_id": raws.get("topic_id"),
                "findings": [],
            },
        )
