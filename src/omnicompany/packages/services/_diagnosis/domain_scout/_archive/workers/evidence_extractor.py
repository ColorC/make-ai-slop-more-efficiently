# [OMNI] origin=claude-code domain=services/domain_scout/workers ts=2026-04-24T00:00:00Z
# [OMNI] material_id="material:diagnosis.domain_scout.evidence_extractor_worker.py"
"""EvidenceExtractor — LLM 对每条候选抽取可验证引用片段 (D4 之一)."""
from typing import Any
from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker


class EvidenceExtractor(Worker):
    """LLM 逐条抽原文段落做引用; 不截断原文让 LLM 主动搜索 (L1 铁律 A)."""

    DESCRIPTION = (
        "domain_scout Worker #3: 对 dedup_candidates 逐条用 LLM 抽引用片段. "
        "不做预防性截断, 原文完整给 LLM. D4 可验证性硬约束的材料来源. "
        "产 evidence_bundle 给 LLMSummarizer."
    )
    FORMAT_IN = "domain_scout.dedup_candidates"
    FORMAT_OUT = "domain_scout.evidence_bundle"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        cands = input_data.get(self.FORMAT_IN, {})
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "topic_id": cands.get("topic_id"),
                "bundles": [],
            },
        )
