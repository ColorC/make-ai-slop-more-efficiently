# [OMNI] origin=claude-code ts=2026-07-03T00:00:00Z type=module summary="token_ledger 落盘: sessions.jsonl + internal_by_caller_day.jsonl + watermark.json, out_dir 全程可注入" why="overnight-run.md 第六节写入位置=data/llm/token_ledger/(可重建), 测试要求落盘目录可注入避免碰真实数据源" tags=token-ledger,writer,jsonl
"""token_ledger 落盘 —— 会话级 jsonl + 内部调用账 jsonl + 水位线 json。

落盘登记: config/ledgers.yaml 的 token-ledger 条目(标"派生可重建")。
生产默认落 data/llm/token_ledger/, 但本函数不硬编码该路径 —— out_dir 由调用方传入
(CLI/定时任务负责传真实路径, 测试传 tmp_path, 两边共用同一份写入逻辑)。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .aggregate import TokenLedgerAggregate


def write_token_ledger(
    aggregate: TokenLedgerAggregate,
    *,
    out_dir: Path,
    watermark_state: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """把聚合结果写到 out_dir 下三份文件, 返回各自路径。

    Args:
        aggregate: aggregate_token_ledger() 的返回值。
        out_dir: 落盘目录(生产=data/llm/token_ledger/, 测试注入 tmp_path)。
        watermark_state: 三路收集器各自 new_watermark_state 按来源嵌套的字典
            ({"claude": ..., "codex": ..., "internal": ...}), 供下次增量运行读取续跑。
            省略时落一份空字典(仍是合法 json dict, 满足"可重建"语义——下次会全量重扫)。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sessions_path = out_dir / "sessions.jsonl"
    internal_path = out_dir / "internal_by_caller_day.jsonl"
    watermark_path = out_dir / "watermark.json"

    with sessions_path.open("w", encoding="utf-8") as f:
        for row in aggregate.by_session:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    with internal_path.open("w", encoding="utf-8") as f:
        for row in aggregate.internal_by_caller_day:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    watermark_path.write_text(
        json.dumps(watermark_state or {}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {"sessions": sessions_path, "internal": internal_path, "watermark": watermark_path}


__all__ = ["write_token_ledger"]
