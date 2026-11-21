# [OMNI] origin=claude-code domain=services/domain_scout/workers ts=2026-04-24T00:00:00Z
# [OMNI] material_id="material:services.diagnosis.domain_scout.workers.aggregate_exports.py"
"""domain_scout workers (Phase A skeleton: 6 Worker 骨架, run() 返回 PASS stub).

Worker 链:
  scout_request → SourceFetcher → fetch_batch
                → DedupFilter → dedup_candidates
                → EvidenceExtractor → evidence_bundle
                → LLMSummarizer → raw_findings
                → VerifiabilityCheck (D4 硬门) → verified_findings
                → DigestWriter → digest (sink)
"""
from .source_fetcher import SourceFetcher
from .dedup_filter import DedupFilter
from .evidence_extractor import EvidenceExtractor
from .llm_summarizer import LLMSummarizer
from .verifiability_check import VerifiabilityCheck
from .digest_writer import DigestWriter


ALL_WORKERS = [
    SourceFetcher,
    DedupFilter,
    EvidenceExtractor,
    LLMSummarizer,
    VerifiabilityCheck,
    DigestWriter,
]

__all__ = [
    "SourceFetcher", "DedupFilter", "EvidenceExtractor",
    "LLMSummarizer", "VerifiabilityCheck", "DigestWriter",
    "ALL_WORKERS",
]
