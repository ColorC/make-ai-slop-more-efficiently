# [OMNI] origin=claude-code ts=2026-07-02T00:00:00Z type=module summary="留痕账本读写实现:append-only jsonl,事件数据类,verdict用追加关联事件而非改写原行" why="target-architecture.md 3.3节要求'一条只追加的事件流';3.5节记录类落点铁律要求每类记录唯一头+登记表内位置;本模块是ledger-ops条目的骨架实现" tags=ledger,provenance,append-only,jsonl
"""留痕账本存储层 —— 纯确定性代码, 不含任何 LLM 调用。

设计要点(权威 = target-architecture.md 3.3 节):
    - 信封字段借 CloudEvents / OpenLineage / W3C PROV 概念, 轻量 JSON, 不接重基建。
    - 每条事件默认 verdict="unverified"(机关一·验证闸门), 升级为 verified 时
      **不修改原行**, 而是追加一条 type="verdict.update" 的关联事件——全程只追加。
    - 落盘路径固定为仓根 data/ledger/events.jsonl, 由本模块唯一写入,
      不接受调用方传入自定义路径(见 config/ledgers.yaml ledger-ops 条: "记录工具本身不
      接受路径参数, 想写别处只能绕过工具, 绕过即违规")。测试通过 monkeypatch 模块级
      路径常量来隔离, 不通过构造参数。
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# ── 路径(单点真源, 与 config/ledgers.yaml ledger-ops 条目登记的位置一致) ──────
# store.py → ledger[0]/_core[1]/services[2]/packages[3]/omnicompany[4]/src[5]/仓根[6]
_OMNI_ROOT = Path(__file__).resolve().parents[6]
LEDGER_DIR = _OMNI_ROOT / "data" / "ledger"
EVENTS_PATH = LEDGER_DIR / "events.jsonl"

_UNVERIFIED = "unverified"
_VERDICT_UPDATE_TYPE = "verdict.update"


def _now_iso() -> str:
    """UTC ISO8601, 精确到秒, 带 Z 后缀。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


@dataclass
class LedgerEvent:
    """一条留痕事件(信封字段借 CloudEvents/OpenLineage/PROV 概念)。

    字段:
        id: 事件 id, 前缀 evt_ + uuid4 hex, 由 append() 自动生成(不接受外部指定)。
        time: 事件发生时间, UTC ISO8601 字符串, append() 若不传则取当前时间。
        type: 事件类型(如 "pipeline.run" / "decision.applied" / "verdict.update")。
        agent: 执行者标识(如 "claude-code" / "omni-cron" / 人名)。
        activity: 人读一句话描述做了什么。
        inputs: 输入引用列表(统一引用字符串或路径, 见 3.2 节统一引用)。
        outputs: 输出引用列表。
        consumed_decisions: 本次运行读取并应用的历史裁决 id 列表(机关二·默认读历史裁决,
            默认空列表, 由调用方在管线开跑前查历史裁决后回填)。
        verdict: 裁决态, 默认 "unverified"(机关一·验证闸门), 只能通过 set_verdict()
            追加关联事件的方式"升级"视图, 不直接改这个字段本身去改写已有行。
        meta: 任意附加元数据(自由字典, 不做 schema 强校验)。
    """

    id: str = field(default_factory=_new_event_id)
    time: str = field(default_factory=_now_iso)
    type: str = "generic"
    agent: str = ""
    activity: str = ""
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    consumed_decisions: list[str] = field(default_factory=list)
    verdict: str = _UNVERIFIED
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LedgerEvent":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in d.items() if k in known}
        return cls(**clean)


def _ensure_dir() -> None:
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)


def _read_lines() -> list[dict[str, Any]]:
    """按行读取全部事件(原始 dict), 跳过空行/坏行。只读, 不做任何折叠。"""
    if not EVENTS_PATH.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _existing_ids_by_idempotency_key(key: str) -> str | None:
    """查是否已有事件带同一个 idempotency_key(存放于 meta.idempotency_key)。

    返回已存在事件的 id, 不存在则 None。全量扫一遍(账本量级小, 骨架阶段够用;
    量大后可加索引, 不在本次骨架范围)。
    """
    for rec in _read_lines():
        if (rec.get("meta") or {}).get("idempotency_key") == key:
            return rec.get("id")
    return None


def _append_line(record: dict[str, Any]) -> None:
    """原子追加单行 json + 换行到 events.jsonl。绝不修改已有行。"""
    _ensure_dir()
    line = json.dumps(record, ensure_ascii=False) + "\n"
    # 以 append 模式打开, 单次 write 系统调用, 配合 O_APPEND 语义保证不覆盖已有内容。
    # Windows 下 'a' 模式已隐含 O_APPEND 行为(每次 write 定位到文件末尾)。
    fd = os.open(str(EVENTS_PATH), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def append(event: LedgerEvent, *, idempotency_key: str | None = None) -> str:
    """追加一条留痕事件, 返回事件 id。

    Args:
        event: 待写入的 LedgerEvent。
        idempotency_key: 可选幂等键。若传入且账本中已存在带同一 key 的事件
            (存于该事件 meta.idempotency_key), 则不重写, 直接返回已有事件的 id。

    行为:
        - 目录不存在自动创建。
        - 单行 json + 换行, 只追加, 绝不修改已有行。
        - id 由 uuid4 生成, 冲突概率可忽略不计; idempotency_key 是业务语义上的
          "同一件事别记两遍"防重, 与 id 冲突是两回事。
    """
    if idempotency_key:
        existing_id = _existing_ids_by_idempotency_key(idempotency_key)
        if existing_id:
            return existing_id
        event.meta = dict(event.meta or {})
        event.meta["idempotency_key"] = idempotency_key

    record = event.to_dict()
    _append_line(record)
    return event.id


def tail(n: int = 20) -> list[dict[str, Any]]:
    """取最近 n 条事件原始记录(不折叠, 按写入顺序返回最后 n 行)。"""
    lines = _read_lines()
    if n <= 0:
        return []
    return lines[-n:]


def iter_since(since: str | float | None = None) -> Iterator[dict[str, Any]]:
    """按时间或游标增量迭代事件。

    Args:
        since: 可为
            - None: 从头迭代全部事件。
            - str: ISO8601 时间字符串, 或事件 id(形如 "evt_xxx")。
                * 若能解析成时间, 按 time 字段 > since 过滤。
                * 若匹配到某条事件 id, 从该事件之后(不含自身)开始迭代
                  (按文件内出现顺序, 即游标语义)。
            - float: 视为 unix epoch 秒, 按 time 字段 > since 过滤。
    """
    lines = _read_lines()
    if since is None:
        yield from lines
        return

    # 游标语义: since 是某个已存在的事件 id
    if isinstance(since, str) and since.startswith("evt_"):
        found_idx = None
        for i, rec in enumerate(lines):
            if rec.get("id") == since:
                found_idx = i
                break
        if found_idx is not None:
            yield from lines[found_idx + 1:]
            return
        # 传入的 id 账本里没有: 视为空游标, 不猜测, 返回空
        return

    # 时间语义
    since_dt: datetime | None = None
    if isinstance(since, (int, float)):
        since_dt = datetime.fromtimestamp(float(since), tz=timezone.utc)
    elif isinstance(since, str):
        try:
            s = since.replace("Z", "+00:00")
            since_dt = datetime.fromisoformat(s)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            since_dt = None

    if since_dt is None:
        # 无法解析, 不猜测, 全量返回(保守, 不静默丢事件)
        yield from lines
        return

    for rec in lines:
        t = rec.get("time")
        if not t:
            continue
        try:
            t_dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
            if t_dt.tzinfo is None:
                t_dt = t_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if t_dt > since_dt:
            yield rec


def set_verdict(event_id: str, verdict: str, *, by: str = "", reason: str = "") -> str:
    """把某条事件的裁决态"升级"为 verified(或其它态), 只追加不改写。

    实现: 不修改 event_id 对应的原行, 而是追加一条新事件:
        type="verdict.update"
        meta={"target_event_id": event_id, "verdict": verdict, "by": by, "reason": reason}

    Args:
        event_id: 被裁决的原事件 id。
        verdict: 新裁决态(如 "verified")。
        by: 谁做的裁决(agent 或人名)。
        reason: 裁决理由。

    Returns:
        新追加的 verdict.update 事件的 id。

    调用方若要看某事件"当前裁决态", 需自行折叠(找该 event_id 的原始事件 +
    所有 target_event_id == event_id 的 verdict.update 事件, 取最后一条的 verdict),
    这与决策库 fold() 折叠取最新的范式一致。本骨架不提供折叠视图函数
    (留给后续机关一·验证闸门实现时按需要建, 骨架阶段只保证账本本身只追加)。
    """
    update_event = LedgerEvent(
        type=_VERDICT_UPDATE_TYPE,
        agent=by,
        activity=f"将事件 {event_id} 的 verdict 更新为 {verdict}" + (f"({reason})" if reason else ""),
        inputs=[event_id],
        meta={
            "target_event_id": event_id,
            "verdict": verdict,
            "by": by,
            "reason": reason,
        },
    )
    return append(update_event)
