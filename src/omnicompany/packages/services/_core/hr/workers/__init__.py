# [OMNI] material_id="material:core.hr.workers.exports.py" origin=codex ts=2026-08-18 type=config status=active agent=codex
# [OMNI] summary="HR Team Worker 导出与注册集合"
# [OMNI] tags=hr,worker,exports
"""HR worker exports."""

from .digest import HrDigestWorker
from .historical_feedback import HistoricalFeedbackClassifierWorker


ALL_WORKERS = [HrDigestWorker, HistoricalFeedbackClassifierWorker]

__all__ = ["HrDigestWorker", "HistoricalFeedbackClassifierWorker", "ALL_WORKERS"]
