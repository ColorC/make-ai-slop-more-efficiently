# [OMNI] origin=claude-code domain=services/hypothesis ts=2026-07-10T00:00:00Z type=config
# [OMNI] material_id="material:learning.hypothesis.worker_registry.exports.py"
"""hypothesis Team 的 Worker 集合 (外置 AgentRuntime 版)。

2 个 Pipeline-level Worker:
  - ExperimenterWorker:    外置只读证据提案 + Omni 证据校验 (SOFT)
  - BeliefReflectorWorker: 外置零工具提案 + Omni 控制面写统一决策库 belief (SOFT)

旧 markdown 版 ReflectorWorker 与双脑 LockstepExperimenterWorker 已随决策本体合并清单#1
退役(khyp 文档体系拆除,无生产调用方)。
"""
from __future__ import annotations

from .belief_reflector import BeliefReflectorRouter, BeliefReflectorWorker
from .experimenter import ExperimenterWorker


ReflectorWorker = BeliefReflectorWorker  # 统一别名:总结 agent 现即 BeliefReflector

ALL_WORKERS = [
    ExperimenterWorker,
    BeliefReflectorWorker,
]

__all__ = [
    "ExperimenterWorker",
    "BeliefReflectorWorker",
    "BeliefReflectorRouter",
    "ReflectorWorker",
    "ALL_WORKERS",
]
