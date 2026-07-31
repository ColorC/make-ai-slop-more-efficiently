# [OMNI] origin=claude-code domain=omnicompany/knowledge ts=2026-04-21T00:00:00Z type=config
# [OMNI] material_id="material:learning.knowledge.worker_registry.exports.py"
"""knowledge Team · 5 Worker 清单 (Stage 3 Clean Migration 2026-04-21).

每个 Worker 独立文件, 无 Diamond shortcut, _archive 不再被 workers import。

所有 5 个 Worker 均为独立操作入口（各自有独立 FORMAT_IN/FORMAT_OUT），
无相互链式依赖，各自形成 source→sink 的原子操作。
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from .kb_audit_worker import KBAuditWorker
from .kb_index_rebuild_worker import KBIndexRebuildWorker
from .kb_locate_worker import KBLocateWorker
from .kb_query_worker import KBQueryWorker
from .kb_write_worker import KBWriteWorker

ALL_WORKERS: list[type[Worker]] = [
    KBQueryWorker,
    KBWriteWorker,
    KBLocateWorker,
    KBAuditWorker,
    KBIndexRebuildWorker,
]

__all__ = [
    "ALL_WORKERS",
    "KBQueryWorker",
    "KBWriteWorker",
    "KBLocateWorker",
    "KBAuditWorker",
    "KBIndexRebuildWorker",
]
