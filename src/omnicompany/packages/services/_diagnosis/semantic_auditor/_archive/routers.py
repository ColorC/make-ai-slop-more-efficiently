# [OMNI] origin=claude-code domain=services/semantic_auditor ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:diagnosis.semantic_auditor.router_compat_shim.python"
"""semantic_auditor/routers.py — 向后兼容 shim (Clean Migration 2026-04-20).

真实 Worker 实现在 `workers/` 目录。本文件仅为旧 import 路径保留兼容:
  - 旧名 FooRouter → 新名 FooWorker (别名)
  - 旧 `from ...semantic_auditor.routers import ArtifactSelectorRouter` 继续工作

不要往本文件加新逻辑; 新增 Worker 请直接写 `workers/<name>.py`。
归档: `_archive/routers_legacy.py` 保留旧实现供历史追溯。
"""
from __future__ import annotations

from .workers import (
    ArtifactSelectorWorker,
    StandardMatcherWorker,
    ExcerptRetrieverWorker,
    LLMAuditWorker,
    FindingWriterWorker,
)


# ─── 旧名别名 (兼容) ────────────────────────────────────────────────────────
ArtifactSelectorRouter = ArtifactSelectorWorker
StandardMatcherRouter = StandardMatcherWorker
ExcerptRetrieverRouter = ExcerptRetrieverWorker
LLMAuditRouter = LLMAuditWorker
FindingWriterRouter = FindingWriterWorker


__all__ = [
    # 新名 (推荐)
    "ArtifactSelectorWorker",
    "StandardMatcherWorker",
    "ExcerptRetrieverWorker",
    "LLMAuditWorker",
    "FindingWriterWorker",
    # 旧名 (兼容)
    "ArtifactSelectorRouter",
    "StandardMatcherRouter",
    "ExcerptRetrieverRouter",
    "LLMAuditRouter",
    "FindingWriterRouter",
]
