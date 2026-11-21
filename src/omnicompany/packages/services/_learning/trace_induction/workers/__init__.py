# [OMNI] origin=claude-code domain=services/trace_induction ts=2026-04-22T00:00:00Z type=config
# [OMNI] material_id="material:learning.trace_induction.worker_registry.exports.py"
"""trace_induction Team 的 Worker 集合 (Stage 3 Clean Migration 2026-04-22).

6 个独立 Worker 文件:
  - trace_reader.py    → TraceReaderWorker    (HARD · 确定性 DB 读取)
  - noise_filter.py    → NoiseFilterWorker    (SOFT · LLM 标注噪音)
  - sop_generator.py   → SOPGeneratorWorker   (SOFT · LLM 生成 SOP)
  - req_writer.py      → ReqWriterWorker      (SOFT · async · LLM 生成需求稿)
  - wf_caller.py       → WFCallerWorker       (SOFT · SubTeamWorker 调 workflow-factory)
  - registrar.py       → RegistrarWorker      (HARD · 确定性注册 pipeline_index)

_shared.py 含 format_steps / parse_json_loose (供 noise_filter + sop_generator 复用).

_archive/routers_legacy.py 仅保留作为历史参考 (OMNI-024 ALLOW), 不再被 workers/ 继承。
"""
from __future__ import annotations

from .noise_filter import NoiseFilterWorker
from .registrar import RegistrarWorker
from .req_writer import ReqWriterWorker
from .sop_generator import SOPGeneratorWorker
from .trace_reader import TraceReaderWorker
from .wf_caller import WFCallerWorker


ALL_WORKERS = [
    TraceReaderWorker,
    NoiseFilterWorker,
    SOPGeneratorWorker,
    ReqWriterWorker,
    WFCallerWorker,
    RegistrarWorker,
]

__all__ = [
    "TraceReaderWorker",
    "NoiseFilterWorker",
    "SOPGeneratorWorker",
    "ReqWriterWorker",
    "WFCallerWorker",
    "RegistrarWorker",
    "ALL_WORKERS",
]
