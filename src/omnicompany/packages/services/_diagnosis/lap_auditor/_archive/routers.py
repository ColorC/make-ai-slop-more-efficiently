# [OMNI] origin=claude-code domain=lap_auditor/routers.py ts=2026-04-21T00:00:00Z type=shim
# [OMNI] material_id="material:diagnosis.lap_auditor.router_compat_shim.python"
"""lap_auditor/routers.py — 向后兼容 shim (Clean Migration 2026-04-21).

真实 Worker 实现在 `workers/` 目录。本文件仅为旧 import 路径保留兼容:
  旧名 FooRouter → 新名 FooWorker (别名)

不要往本文件加新逻辑；新增 Worker 请直接写 `workers/__init__.py`。
归档: `_archive/routers_legacy.py` 保留原 3-Router 实现。
"""
from __future__ import annotations

from .workers import (
    ContextGetterWorker,
    ReportFormatterWorker,
    SpecAuditorWorker,
)

# 旧名兼容别名
ContextGetterRouter = ContextGetterWorker
SpecAuditorRouter = SpecAuditorWorker
ReportFormatterRouter = ReportFormatterWorker

__all__ = [
    "ContextGetterWorker",
    "SpecAuditorWorker",
    "ReportFormatterWorker",
    "ContextGetterRouter",
    "SpecAuditorRouter",
    "ReportFormatterRouter",
]
