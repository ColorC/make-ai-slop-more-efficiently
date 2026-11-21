# [OMNI] origin=claude-code domain=services/trace_induction ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:learning.trace_induction.router_compatibility_shim.py"
"""trace_induction routers — 兼容垫片 (Phase D Diamond shortcut 2026-04-20).

业务实现已迁到 workers/ (Diamond shortcut 模式). 本文件保留旧名称以兼容调用方.
"""
from __future__ import annotations

from .workers import (
    TraceReaderWorker as TraceReaderRouter,
    NoiseFilterWorker as NoiseFilterRouter,
    SOPGeneratorWorker as SOPGeneratorRouter,
    ReqWriterWorker as ReqWriterRouter,
    WFCallerWorker as WFCallerRouter,
    RegistrarWorker as RegistrarRouter,
)

__all__ = [
    "TraceReaderRouter",
    "NoiseFilterRouter",
    "SOPGeneratorRouter",
    "ReqWriterRouter",
    "WFCallerRouter",
    "RegistrarRouter",
]
