# [OMNI] origin=claude-code domain=services/pattern_discovery ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:core.pattern_discovery.router.compatibility_shim.py"
"""pattern_discovery routers — 兼容垫片 (Phase D Clean Migration 2026-04-20).

业务实现已迁到 workers/ (Diamond shortcut 模式). 本文件保留旧名称以兼容调用方.
"""
from __future__ import annotations

from .workers import (
    SummaryReaderWorker as SummaryReaderRouter,
    PatternClustererWorker as PatternClustererRouter,
    InductionDispatcherWorker as InductionDispatcherRouter,
)

__all__ = [
    "SummaryReaderRouter",
    "PatternClustererRouter",
    "InductionDispatcherRouter",
]
