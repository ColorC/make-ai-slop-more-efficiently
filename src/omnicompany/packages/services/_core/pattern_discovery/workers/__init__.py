# [OMNI] origin=claude-code domain=services/pattern_discovery ts=2026-04-22T00:00:00Z type=config
# [OMNI] material_id="material:core.pattern_discovery.worker_aggregate.exports.py"
"""pattern_discovery Team 的 Worker 集合 (Stage 3 Clean Migration 2026-04-22).

3 个独立 Worker 文件:
  - summary_reader.py       → SummaryReaderWorker       (HARD · 确定性 DB 读取)
  - pattern_clusterer.py    → PatternClustererWorker    (SOFT · LLM 语义聚类)
  - induction_dispatcher.py → InductionDispatcherWorker (SOFT · SubPipeline 调用)

_archive/routers_legacy.py 仅保留作为历史参考 (OMNI-024 ALLOW), 不再被 workers/ 继承。
"""
from __future__ import annotations

from .induction_dispatcher import InductionDispatcherWorker
from .pattern_clusterer import PatternClustererWorker
from .summary_reader import SummaryReaderWorker


ALL_WORKERS = [
    SummaryReaderWorker,
    PatternClustererWorker,
    InductionDispatcherWorker,
]

__all__ = [
    "SummaryReaderWorker",
    "PatternClustererWorker",
    "InductionDispatcherWorker",
    "ALL_WORKERS",
]
