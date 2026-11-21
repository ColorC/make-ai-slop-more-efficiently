# [OMNI] origin=claude-code domain=runtime/agent_crystallize/trace_accumulator ts=2026-04-15T00:00:00Z
# [OMNI] material_id="material:runtime.agent_crystallize.trace_counter.accumulator.py"
"""trace_accumulator — agent loop trace 计数, 驱动 N≥3 自动 crystallize.

设计:
  - 持久化到 data/crystallize/trace_counts.json
  - key = "{pipeline_id}:{node_id}"
  - value = int (累计跑次数)

接入方式:
  runner.py 的 crystallize 触发块在每次 agent loop 结束后调 increment_trace_count(),
  根据返回的 count 决定开启几个 crystallizer:
    count == 1 → 只开 TraceSummarizer (只记录)
    count == 2 → 只开 TraceSummarizer (只记录)
    count >= 3  → 开全套 crystallizer (含 DescriptionRefiner + self-judge)

这样不需要 OMNICOMPANY_CRYSTALLIZE env var, 数据自然积累到临界点后自动开启.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from omnicompany.core.config import resolve_db_dir

logger = logging.getLogger(__name__)


def _counts_path() -> Path:
    return resolve_db_dir("crystallize") / "trace_counts.json"


def _load() -> dict[str, int]:
    p = _counts_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(counts: dict[str, int]) -> None:
    p = _counts_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(counts, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("trace_accumulator save failed: %s", e)


def _key(pipeline_id: str, node_id: str) -> str:
    return f"{pipeline_id}:{node_id}"


def increment_trace_count(pipeline_id: str, node_id: str) -> int:
    """累加 (pipeline_id, node_id) 的 trace 计数并返回新值.

    永不抛异常 (文件 I/O 失败时静默返回当前内存值).
    """
    try:
        counts = _load()
        k = _key(pipeline_id, node_id)
        counts[k] = counts.get(k, 0) + 1
        _save(counts)
        logger.debug("trace_accumulator: %s = %d", k, counts[k])
        return counts[k]
    except Exception as e:
        logger.debug("trace_accumulator increment failed: %s", e)
        return 1


def get_trace_count(pipeline_id: str, node_id: str) -> int:
    """查询 (pipeline_id, node_id) 的历史 trace 计数 (无记录返回 0)."""
    try:
        return _load().get(_key(pipeline_id, node_id), 0)
    except Exception:
        return 0


def reset_trace_count(pipeline_id: str, node_id: str) -> None:
    """重置计数 (调试用, 让 N≥3 重新从零开始)."""
    try:
        counts = _load()
        k = _key(pipeline_id, node_id)
        if k in counts:
            del counts[k]
            _save(counts)
    except Exception as e:
        logger.debug("trace_accumulator reset failed: %s", e)
