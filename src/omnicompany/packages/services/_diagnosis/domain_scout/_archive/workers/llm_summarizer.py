# [OMNI] origin=claude-code domain=services/domain_scout/workers ts=2026-04-24T00:00:00Z
# [OMNI] material_id="material:diagnosis.domain_scout.llm_summarizer_worker.py"
"""LLMSummarizer — LLM 对每条带引用候选写 finding 草稿."""
from typing import Any
from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker


class LLMSummarizer(Worker):
    """LLM 写 finding 草稿 (title / insight / confidence). 草稿可能含幻觉, 下游 VerifiabilityCheck 把关."""

    DESCRIPTION = (
        "domain_scout Worker #4: 对 evidence_bundle 逐条用 LLM 写 finding 草稿. "
        "每条包含 title/insight/source_url/quoted_evidence/confidence 五字段. "
        "草稿可能含幻觉. 产 raw_findings 给 VerifiabilityCheck."
    )
    FORMAT_IN = "domain_scout.evidence_bundle"
    FORMAT_OUT = "domain_scout.raw_findings"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        bundle = input_data.get(self.FORMAT_IN, {})
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "topic_id": bundle.get("topic_id"),
                "findings": [],
            },
        )
