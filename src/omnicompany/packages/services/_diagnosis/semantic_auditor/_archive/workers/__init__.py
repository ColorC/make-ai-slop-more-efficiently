# [OMNI] origin=claude-code domain=omnifactory/semantic_auditor ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:diagnosis.semantic_auditor.worker_registry.python"
"""SemanticAuditor Team · 5 Worker 清单 (Clean Migration 2026-04-20)."""
from __future__ import annotations

from omnifactory.packages.services._core.omnicompany import Worker

from .artifact_selector import ArtifactSelectorWorker
from .standard_matcher import StandardMatcherWorker
from .excerpt_retriever import ExcerptRetrieverWorker
from .llm_audit import LLMAuditWorker
from .finding_writer import FindingWriterWorker


ALL_WORKERS: list[type[Worker]] = [
    ArtifactSelectorWorker,
    StandardMatcherWorker,
    ExcerptRetrieverWorker,
    LLMAuditWorker,
    FindingWriterWorker,
]


__all__ = [
    "ArtifactSelectorWorker",
    "StandardMatcherWorker",
    "ExcerptRetrieverWorker",
    "LLMAuditWorker",
    "FindingWriterWorker",
    "ALL_WORKERS",
]
