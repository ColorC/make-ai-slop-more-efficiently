# [OMNI] origin=claude-code domain=services/absorption/workers/v2 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v2.directed_reader.py"
"""DirectedReaderV2Worker — V2 Worker #4 (Clean Migration 2026-04-20).

职责: 内嵌 AgentNodeLoop _DirectedReaderLoop, 带问题定向读 repo 代码.
注: _DirectedReaderLoop 旧 AgentNodeLoop, 阶段 D 迁移.
实现继承自 _archive/routers_v1v2_legacy.DirectedReaderV2Router.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v1v2_legacy import (
    DirectedReaderV2Router as _Legacy,
)


class DirectedReaderV2Worker(Worker, _Legacy):
    pass
