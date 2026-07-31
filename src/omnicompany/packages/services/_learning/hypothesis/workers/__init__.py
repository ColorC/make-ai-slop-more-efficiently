# [OMNI] origin=claude-code domain=services/hypothesis ts=2026-07-10T00:00:00Z type=config
# [OMNI] material_id="material:learning.hypothesis.worker_registry.exports.py"
"""hypothesis Team 的 Worker 集合 (v5 决策库收编版)。

2 个 Pipeline-level Worker:
  - ExperimenterWorker:    主探索 AgentNodeLoop (SOFT)
  - BeliefReflectorWorker: 总结 AgentNodeLoop,直接维护统一决策库 belief (SOFT)

Diamond 继承: Worker + Router 双继承。Experimenter 业务逻辑在 ../routers_legacy.py
(2026-07-26 OMNI-040 Stage 3 迁回正式位置);BeliefReflector 在 belief_reflector.py。
旧 markdown 版 ReflectorWorker 与双脑 LockstepExperimenterWorker 已随决策本体合并清单#1
退役(khyp 文档体系拆除,无生产调用方)。
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.hypothesis.routers_legacy import (
    ExperimenterRouter as _Experimenter,
)
from .belief_reflector import BeliefReflectorRouter, BeliefReflectorWorker


class ExperimenterWorker(Worker, _Experimenter):
    """主探索 AgentNodeLoop:自由探索,输出行为轨迹(hypothesis.session → hypothesis.factlog)。"""


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
