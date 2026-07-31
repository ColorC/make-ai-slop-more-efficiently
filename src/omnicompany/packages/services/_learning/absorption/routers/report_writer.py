# [OMNI] origin=claude-code domain=services/absorption/routers ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:learning.absorption.router_shim.report_writer.py"
"""compat shim: redirect to workers/v3/report_writer.py.

注: `_build_finding_with_code` / `_split_report_parts` 等模块级辅助函数,
原位置是 `routers/report_writer.py` 顶部. 新代码如果需要这些工具,
请直接从 `_archive/routers_v3_legacy/report_writer.py` import. 本 shim 同样 re-export
以保证旧代码不断.
"""
from __future__ import annotations

from ..workers.v3.report_writer import (
    ReportWriterV3Worker,
    HumanFeedbackGateV3Worker,
    FeedbackRouterV3Worker,
)

# 原类名旧代码引用: ReportWriterV3Router / HumanFeedbackGateV3Router / FeedbackRouterV3
ReportWriterV3Router = ReportWriterV3Worker
HumanFeedbackGateV3Router = HumanFeedbackGateV3Worker
FeedbackRouterV3 = FeedbackRouterV3Worker

# 模块级辅助函数从归档 re-export (供 _archive/report_updater.py / 其他调用者使用)
from .._archive.routers_v3_legacy.report_writer import (  # noqa: E402
    _build_finding_with_code,
    _split_report_parts,
)


__all__ = [
    "ReportWriterV3Router",
    "HumanFeedbackGateV3Router",
    "FeedbackRouterV3",
    "ReportWriterV3Worker",
    "HumanFeedbackGateV3Worker",
    "FeedbackRouterV3Worker",
    "_build_finding_with_code",
    "_split_report_parts",
]
