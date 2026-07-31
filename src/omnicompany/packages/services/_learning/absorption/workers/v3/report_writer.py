# [OMNI] origin=claude-code domain=services/absorption/workers/v3 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v3.report_writer_feedback.py"
"""V3 Report Writer 三 Worker (Clean Migration 2026-04-20).

Workers:
  - ReportWriterV3Worker — LLM 综合报告 + 路径硬替换
  - HumanFeedbackGateV3Worker — 读 feedback.md → PARTIAL JUMP 至 supplement_explorer
  - FeedbackRouterV3Worker — RULE + 判断: EMIT 或 JUMP

实现继承自 _archive/routers_v3_legacy.report_writer.{ReportWriterV3Router, HumanFeedbackGateV3Router, FeedbackRouterV3}.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.report_writer import (
    ReportWriterV3Router as _ReportWriterLegacy,
    HumanFeedbackGateV3Router as _GateLegacy,
    FeedbackRouterV3 as _FeedbackLegacy,
)


class ReportWriterV3Worker(Worker, _ReportWriterLegacy):
    pass


class HumanFeedbackGateV3Worker(Worker, _GateLegacy):
    pass


class FeedbackRouterV3Worker(Worker, _FeedbackLegacy):
    pass
