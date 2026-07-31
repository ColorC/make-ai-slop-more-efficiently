# [OMNI] origin=claude-code domain=services/absorption/workers/v2 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v2.recon_scout.py"
"""ReconScoutV2Worker — V2 Worker #1 (Clean Migration 2026-04-20).

职责: 内嵌 AgentNodeLoop _ReconLoop, 侦察 repo 结构并产出 recon.map.
注: _ReconLoop 是 V2 内部仍基于旧 runtime.agent.AgentNodeLoop 的循环, 阶段 D 会迁移.
实现继承自 _archive/routers_v1v2_legacy.ReconScoutV2Router.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v1v2_legacy import (
    ReconScoutV2Router as _Legacy,
)


class ReconScoutV2Worker(Worker, _Legacy):
    pass
