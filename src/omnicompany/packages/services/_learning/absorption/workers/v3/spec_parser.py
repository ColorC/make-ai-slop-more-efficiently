# [OMNI] origin=claude-code domain=services/absorption/workers/v3 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.v3.stage3.proposal_parser.worker.py"
"""SpecParserWorker (Clean Migration 2026-04-20).

职责: 从 report.v3 的 structured.proposals 解析 PRO-NNN 结构化提案 (Stage 3 入口).
消费 composite absorption.proposal.context (3 路 fan-in: report.v3 + capability_inventory + gap_registry).
实现继承自 _archive/routers_v3_legacy.spec_parser.SpecParserRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.spec_parser import (
    SpecParserRouter as _Legacy,
)


class SpecParserWorker(Worker, _Legacy):
    pass
