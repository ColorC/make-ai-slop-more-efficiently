# [OMNI] origin=claude-code domain=services/absorption/routers ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:learning.absorption.router_shim.report_updater.py"
"""compat shim: redirect to workers/v3/report_updater.py."""
from __future__ import annotations

from ..workers.v3.report_updater import ReportUpdaterV3Worker


ReportUpdaterV3Router = ReportUpdaterV3Worker


__all__ = ["ReportUpdaterV3Router", "ReportUpdaterV3Worker"]
