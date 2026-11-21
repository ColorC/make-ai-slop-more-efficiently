# [OMNI] origin=claude-code domain=services/semantic_auditor ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:diagnosis.semantic_auditor.package_entry.python"
"""SemanticAuditor — LLM 驱动的语义合规检查 Team (Clean Migration 2026-04-20).

与 Guardian 互补：
  - Guardian → 路径/命名/结构/存在性（确定性规则，每次 commit）
  - SemanticAuditor → 语义/内容/意图一致性（LLM 驱动，按需/周期）

5 Worker Team: ArtifactSelector → StandardMatcher → ExcerptRetriever → LLMAudit → FindingWriter。
LLMAuditWorker 是 async HARD 外壳, 内部循环调度 AuditAgent (AgentNodeLoop 子类) 做单审。

设计文档见 DESIGN.md。出口共用 docs/tech_debt/REGISTRY.md。
"""
from __future__ import annotations

from .standards_loader import (
    StandardsIndex,
    load_standards_index,
    infer_kind,
    match_standards,
    retrieve_excerpt,
)
from .workers import (
    ALL_WORKERS,
    ArtifactSelectorWorker,
    StandardMatcherWorker,
    ExcerptRetrieverWorker,
    LLMAuditWorker,
    FindingWriterWorker,
)
# 旧名兼容 shim (routers.py 转发)
from .routers import (
    ArtifactSelectorRouter,
    StandardMatcherRouter,
    ExcerptRetrieverRouter,
    LLMAuditRouter,
    FindingWriterRouter,
)
from .audit_agent import AuditAgent
from .pipeline import build_pipeline

__all__ = [
    # 加载器
    "StandardsIndex",
    "load_standards_index",
    "infer_kind",
    "match_standards",
    "retrieve_excerpt",
    # 新名 Worker
    "ArtifactSelectorWorker",
    "StandardMatcherWorker",
    "ExcerptRetrieverWorker",
    "LLMAuditWorker",
    "FindingWriterWorker",
    "ALL_WORKERS",
    # 旧名 (兼容)
    "ArtifactSelectorRouter",
    "StandardMatcherRouter",
    "ExcerptRetrieverRouter",
    "LLMAuditRouter",
    "FindingWriterRouter",
    # AgentNodeLoop (不迁, 保持原继承)
    "AuditAgent",
    # Team 构造
    "build_pipeline",
]
