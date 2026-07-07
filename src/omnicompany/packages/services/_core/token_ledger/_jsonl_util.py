# [OMNI] origin=claude-code ts=2026-07-03T00:00:00Z type=module summary="token_ledger 内部共用: 按字节水位线增量读 jsonl + 坏行计数, 三路收集器共用同一套安全读法" why="三路数据源(claude/codex/meter)都是持续追加的 jsonl, 都要同一套'增量不重读+坏行容错'语义, 抽出来避免三处各写一份微妙不同的实现" tags=token-ledger,jsonl,watermark,internal
"""token_ledger 内部工具: jsonl 按字节水位线增量读取 + 坏行容错计数。

不对外导出(不进 __init__.py 的 __all__), 仅供本包内三个 sources 模块共用。

水位线语义: 每个文件的水位线是"已消费字节数"。增量读取时:
    - 若文件当前大小 <= 水位线, 判定"无新增", 返回空行列表(不重读)。
    - 若文件当前大小 > 水位线, 只读取 [水位线, 当前大小) 这一段新增字节, 按行切分解析。
    - 文件被截断/重建导致当前大小 < 水位线 的异常情况, 保守地从头重新读取整个文件
      (不假设中间状态, 宁可重读一次也不漏记)。
坏行: 单行 json 解析失败(截断/非法 json)一律跳过并计数, 不抛异常、不中断整份文件的读取。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable


def read_new_lines(
    path: Path,
    *,
    prev_offset: int = 0,
) -> tuple[list[str], int, int]:
    """读取某 jsonl 文件里水位线之后的新增行(原始字符串, 未解析)。

    Returns:
        (新增的原始行列表(已 strip, 已过滤空行), 本次读取后的新水位线字节数, 本次读取涉及的字节起点)
    """
    try:
        size = path.stat().st_size
    except OSError:
        return [], prev_offset, prev_offset

    start = prev_offset if 0 <= prev_offset <= size else 0
    if start == size:
        return [], size, start

    try:
        with path.open("rb") as fh:
            fh.seek(start)
            raw = fh.read()
    except OSError:
        return [], prev_offset, prev_offset

    text = raw.decode("utf-8", errors="replace")
    # 若从水位线中段切入(理论上水位线总落在行边界, 这里兜底: 首行可能是半行, 交给 json 解析失败计入坏行)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return lines, size, start


def parse_jsonl_lines(lines: Iterable[str]) -> tuple[list[dict[str, Any]], int]:
    """把原始字符串行解析成 dict 列表, 坏行跳过并计数。"""
    records: list[dict[str, Any]] = []
    skipped = 0
    for ln in lines:
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(obj, dict):
            records.append(obj)
        else:
            skipped += 1
    return records, skipped


def read_and_parse_incremental(
    path: Path,
    *,
    prev_offset: int = 0,
) -> tuple[list[dict[str, Any]], int, int]:
    """合并 read_new_lines + parse_jsonl_lines: 返回 (新记录, 新水位线, 坏行数)。"""
    lines, new_offset, _start = read_new_lines(path, prev_offset=prev_offset)
    records, skipped = parse_jsonl_lines(lines)
    return records, new_offset, skipped


def read_and_parse_full(path: Path) -> tuple[list[dict[str, Any]], int, int]:
    """全量读取整份文件(不看水位线), 返回 (记录, 文件大小作为水位线, 坏行数)。"""
    return read_and_parse_incremental(path, prev_offset=0)
