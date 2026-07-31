# [OMNI] origin=claude-code domain=services/hypothesis ts=2026-07-10T00:00:00Z type=config
# [OMNI] material_id="material:services.learning.hypothesis.router.compatibility_shim.py"
"""hypothesis routers — 兼容垫片 (v5 决策库收编版)。

业务实现在 workers/ (Diamond shortcut 模式)。本文件保留旧名称以兼容调用方:
ReflectorRouter 现指 BeliefReflectorWorker(直接维护统一决策库 belief)。
旧 markdown 编辑工具(EditRouter/WriteFileRouter/ValidateHypothesisDocRouter)与
LockstepExperimenterRouter 已随决策本体合并清单#1 退役。
"""
from __future__ import annotations

from .workers import (
    BeliefReflectorWorker as ReflectorRouter,
    ExperimenterWorker as ExperimenterRouter,
)
from .routers_legacy import BashRouter

__all__ = [
    "ExperimenterRouter",
    "ReflectorRouter",
    "BashRouter",
]
