# [OMNI] origin=claude-code ts=2026-07-03T00:00:00Z type=module summary="token_ledger 内部调用账收集器: 扫 data/llm/meter.jsonl, 按 caller×天 聚合(不并入会话级), 水位线按字节增量, 坏行容错" why="overnight-run.md 第六节: 内部调用早有 meter.jsonl 计量, v1 只需按调用方×天出一张独立表, 不强拼会话级(内部调用没有会话概念)" tags=token-ledger,internal,meter,usage
"""内部 LLM 调用账收集器(data/llm/meter.jsonl)。

字段形状(侦察已核实): timestamp(unix秒)/model/role/caller/input_tokens/output_tokens/
cache_read_tokens/cache_creation_tokens/cost_usd/latency_ms/stop_reason。

按 caller × 天(UTC 日期) 聚合成 InternalCallerDayRow, 不并入会话级视图
(内部调用没有"会话"概念, 强拼会话级字段会污染语义, 见测试断言)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._jsonl_util import parse_jsonl_lines, read_new_lines


@dataclass
class InternalCallerDayRow:
    caller: str
    day: str
    model: str = ""
    call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "caller": self.caller,
            "day": self.day,
            "model": self.model,
            "call_count": self.call_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


@dataclass
class InternalCollectResult:
    by_caller_day: list[InternalCallerDayRow] = field(default_factory=list)
    new_watermark_state: dict[str, Any] = field(default_factory=dict)
    skipped_bad_lines: int = 0


def _day_from_timestamp(ts: Any) -> str:
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return "unknown-day"


def collect_internal_usage(
    meter_path: Path,
    *,
    watermark_state: dict[str, Any] | None = None,
) -> InternalCollectResult:
    """扫 meter_path(默认应传 data/llm/meter.jsonl), 按 caller×天 聚合, 字节水位线增量。

    Args:
        meter_path: meter.jsonl 文件路径(测试传 tmp fixture, 生产传真实 data/llm/meter.jsonl)。
        watermark_state: 上次 collect 返回的 new_watermark_state, 省略则从头全量扫描。
    """
    watermark_state = watermark_state or {}
    key = str(meter_path)
    prev_offset = int(watermark_state.get(key, 0) or 0)

    lines, new_offset, _start = read_new_lines(meter_path, prev_offset=prev_offset)
    new_state = dict(watermark_state)
    new_state[key] = new_offset

    result = InternalCollectResult(new_watermark_state=new_state)
    if not lines:
        return result

    records, skipped = parse_jsonl_lines(lines)
    result.skipped_bad_lines = skipped

    buckets: dict[tuple[str, str, str], InternalCallerDayRow] = {}
    for rec in records:
        caller = str(rec.get("caller") or "unknown")
        model = str(rec.get("model") or "unknown")
        day = _day_from_timestamp(rec.get("timestamp"))
        bucket_key = (caller, day, model)
        row = buckets.get(bucket_key)
        if row is None:
            row = InternalCallerDayRow(caller=caller, day=day, model=model)
            buckets[bucket_key] = row
        row.call_count += 1
        row.input_tokens += int(rec.get("input_tokens") or 0)
        row.output_tokens += int(rec.get("output_tokens") or 0)
        row.cache_read_tokens += int(rec.get("cache_read_tokens") or 0)
        row.cache_creation_tokens += int(rec.get("cache_creation_tokens") or 0)
        row.cost_usd += float(rec.get("cost_usd") or 0.0)

    result.by_caller_day = list(buckets.values())
    return result


__all__ = ["InternalCallerDayRow", "InternalCollectResult", "collect_internal_usage"]
