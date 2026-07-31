# [OMNI] origin=claude-code ts=2026-07-02T00:00:00Z type=package summary="留痕账本包导出(事件数据类+store读写函数)" why="target-architecture.md 3.3节:留痕账本是记录类落点闭集里的器官,需要唯一稳定入口,避免调用方各自拼路径" tags=ledger,provenance,append-only
"""留痕账本(operation ledger) —— 唯一追加式事件流。

权威依据: docs/plans/[2026-07-02]SEMANTIC-OS-MAP/target-architecture.md 第 3.3 节。
存储真源: data/ledger/events.jsonl(仓根),一行一个事件,只追加绝不改写已有行。

用法::

    from omnicompany.packages.services._core.ledger import LedgerEvent, append, tail

    eid = append(LedgerEvent(type="pipeline.run", agent="claude-code", activity="跑了xx管线"))
    events = tail(20)

本包是纯确定性代码,不含任何 LLM 调用。"两个机关"(验证闸门/默认读历史裁决,
target-architecture.md 3.3 节)在此骨架之上由调用方后续实现;本骨架先把
"只追加事件流 + verdict 关联事件"的底座立住。
"""

from __future__ import annotations

from .store import (
    LedgerEvent,
    append,
    iter_since,
    list_deviations,
    report_deviation,
    set_verdict,
    tail,
)

__all__ = [
    "LedgerEvent",
    "append",
    "tail",
    "iter_since",
    "set_verdict",
    "report_deviation",
    "list_deviations",
]
