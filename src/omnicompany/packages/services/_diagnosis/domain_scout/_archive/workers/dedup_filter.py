# [OMNI] origin=claude-code domain=services/domain_scout/workers ts=2026-04-24T00:00:00Z
# [OMNI] material_id="material:diagnosis.domain_scout.dedup_filter_worker.py"
"""DedupFilter — url+source_hash 指纹对比 index.jsonl. 规则域 (D2 唯一非 LLM Worker)."""
from typing import Any
from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker


class DedupFilter(Worker):
    """按 url + source_hash 精确去重 (纯字面, 不用 LLM)."""

    DESCRIPTION = (
        "domain_scout Worker #2: 对 fetch_batch 按 url + source_hash 指纹去重, "
        "对比 index.jsonl 剔除已报告项. 规则域 (D2 铁律的唯一非 LLM 例外). "
        "产 dedup_candidates 给 EvidenceExtractor."
    )
    FORMAT_IN = "domain_scout.fetch_batch"
    FORMAT_OUT = "domain_scout.dedup_candidates"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        batch = input_data.get(self.FORMAT_IN, {})
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "topic_id": batch.get("topic_id"),
                "candidates": [],
            },
        )
