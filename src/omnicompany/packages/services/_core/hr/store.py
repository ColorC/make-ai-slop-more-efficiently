# [OMNI] origin=claude-code ts=2026-08-17T00:00:00+08:00 type=module
# [OMNI] summary="LLM 人事系统事件落盘：append-only jsonl + 幂等去重 + 流式读 + 水位原子写"
# [OMNI] why="P1-implementation-contract.md E 棒职责——五家 collector 的归一化事件需要一个
#             可注入 data_dir、可增量重跑而不产生重复行的落盘层，供 blame.py 与 cli/commands/hr.py 消费"
# [OMNI] tags=hr,store,jsonl,watermark,idempotent
"""LLM 人事系统 —— 事件落盘层（纯确定性，不含任何 LLM 调用）。

权威依据: docs/plans/agent-framework/[2026-08-17]LLM-HR-PERFORMANCE-ATTRIBUTION/
          P1-implementation-contract.md（E 棒契约）与同目录 plan.md。

落盘产物（`data_dir` 全程可注入，禁止硬编码到 `omnicompany/data/hr/`——那是调用方
的默认选择，不是本模块的固定路径）:
    events.jsonl      —— append-only 归一化事件流(HrEvent.to_dict() 逐行 json)
    watermarks.json   —— 各 collector 的增量水位状态，原子写(先写临时文件再 os.replace)

去重键选型（详见 `_dedup_key` docstring）: `(provider, session_id, kind, target, evidence_ref)`。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator

from .schema import HrEvent, validate_event

EVENTS_FILENAME = "events.jsonl"
WATERMARKS_FILENAME = "watermarks.json"

_DedupKey = tuple[str, ...]


class HrEventRejected(ValueError):
    """落盘前校验失败——整批拒收，一行未写。不静默丢弃、不修补、不部分落盘。"""


def _events_path(data_dir: Path) -> Path:
    return Path(data_dir) / EVENTS_FILENAME


def _watermarks_path(data_dir: Path) -> Path:
    return Path(data_dir) / WATERMARKS_FILENAME


def _dedup_key(event: HrEvent) -> _DedupKey:
    """去重键 = (provider, session_id, kind, target, evidence_ref)。

    选型理由: `evidence_ref`(形如 "<abs_path>#L<lineno>")已经把来源钉到具体一行，
    看起来单独用它就够去重。但同一原始行可能合法地派生出**多条**不同事件——例如
    vcs 采集器的一次 commit 记录里，同一行可能对应多个被改动的文件，每个文件各产出
    一条事件；或者一条 tool_use 既产出 write 事件也产出 subagent 派生事件。仅用
    evidence_ref 去重会把这些正当的多条事件误判为"重复"而错误丢弃。

    加入 `kind` + `target` 后，只有"同一 provider/session 的同一来源行、同一种事件
    类型、同一目标路径"才判定为重复——这正好对应"增量重跑 / 水位回退导致同一条
    逻辑事件被再次解析出来"这个唯一目标场景。

    残余碰撞风险: 如果同一来源行确实产出两条 kind、target 都完全相同的事件，会被
    合并为一条。但那种情况下两条事件在可观测语义上本就是同一件事实(同一 provider
    同一会话同一行对同一路径做了同一种动作)，合并是期望行为，不是误伤。
    """
    if event.event_id:
        return ("event", event.event_id, event.verdict)
    return ("legacy", event.provider, event.session_id, event.kind, event.target, event.evidence_ref)


def _existing_keys(data_dir: Path) -> set[_DedupKey]:
    """流式扫一遍已落盘的 events.jsonl，只提取去重键，不把整份内容读进内存列表。"""
    path = _events_path(data_dir)
    keys: set[_DedupKey] = set()
    if not path.is_file():
        return keys
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_id = str(rec.get("event_id", ""))
            if event_id:
                keys.add(("event", event_id, str(rec.get("verdict", "unknown"))))
            else:
                keys.add((
                    "legacy",
                    str(rec.get("provider", "")),
                    str(rec.get("session_id", "")),
                    str(rec.get("kind", "")),
                    str(rec.get("target", "")),
                    str(rec.get("evidence_ref", "")),
                ))
    return keys


def append_events(events: Iterable[HrEvent], *, data_dir: Path) -> int:
    """追加写 `data_dir/events.jsonl`。

    行为:
        1. 落盘前逐条过 `validate_event()`；只要有一条不合法，**整批拒收并抛
           `HrEventRejected`**，一行都不写(不静默丢弃、不修补、不挑合法的先写)。
        2. 合法事件按 `_dedup_key` 与"本批次内 + 已落盘历史"去重，重复的跳过。
        3. 目录不存在自动创建。单次 `os.write` 写完本批次新增行(Windows 下 'a'
           模式已隐含 O_APPEND 定位到文件末尾语义)。

    Returns:
        本次真正新写入(去重后)的行数。
    """
    data_dir = Path(data_dir)
    events = list(events)

    problems_by_event: list[str] = []
    for i, ev in enumerate(events):
        problems = validate_event(ev)
        if problems:
            problems_by_event.append(f"[{i}] evidence_ref={ev.evidence_ref!r}: {problems}")
    if problems_by_event:
        raise HrEventRejected(
            f"{len(problems_by_event)}/{len(events)} 条事件未通过 validate_event()，"
            "整批拒收，一行未写：\n" + "\n".join(problems_by_event)
        )

    if not events:
        return 0

    data_dir.mkdir(parents=True, exist_ok=True)
    seen = _existing_keys(data_dir)
    lines: list[str] = []
    for ev in events:
        key = _dedup_key(ev)
        if key in seen:
            continue
        seen.add(key)
        lines.append(json.dumps(ev.to_dict(), ensure_ascii=False))

    if not lines:
        return 0

    path = _events_path(data_dir)
    payload = ("\n".join(lines) + "\n").encode("utf-8", errors="replace")
    fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    return len(lines)


def read_events(
    *,
    data_dir: Path,
    provider: str | None = None,
    kind: str | None = None,
    since: str | None = None,
    until: str | None = None,
    target: str | None = None,
    current_state: bool = True,
) -> Iterator[HrEvent]:
    """流式逐行读取 `data_dir/events.jsonl`，按 provider/kind/时间窗/路径过滤。

    - 只逐行读，不 `.read()`/`json.load()` 整份文件，不攒进内存列表——调用方若
      要列表自己 `list(read_events(...))`。
    - `since`/`until` 按字符串比较(ISO8601 UTC，形如 "2026-08-17T05:33:42.911Z"
      的定长字典序与时间序一致，无需解析成 datetime)。
    - 坏行(非法 JSON)静默跳过——本函数是只读消费端，覆盖率统计是 collector 写入
      前的职责，不在这里重算。
    """
    path = _events_path(Path(data_dir))
    if not path.is_file():
        return
    known_fields = set(HrEvent.__dataclass_fields__)
    selected: list[tuple[str, HrEvent]] = []
    current_by_id: dict[str, HrEvent] = {}
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if provider is not None and rec.get("provider") != provider:
                continue
            if kind is not None and rec.get("kind") != kind:
                continue
            ts = rec.get("ts") or ""
            if since is not None and ts < since:
                continue
            if until is not None and ts > until:
                continue
            if target is not None and rec.get("target") != target:
                continue
            persisted_event_id = str(rec.get("event_id", ""))
            clean = {k: v for k, v in rec.items() if k in known_fields}
            try:
                event = HrEvent(**clean)
            except TypeError:
                continue
            if not current_state or not persisted_event_id:
                selected.append(("", event))
                continue
            if persisted_event_id not in current_by_id:
                selected.append((persisted_event_id, event))
            current_by_id[persisted_event_id] = event

    for event_id, event in selected:
        yield current_by_id[event_id] if event_id else event


def load_watermarks(data_dir: Path) -> dict[str, Any]:
    """读 `data_dir/watermarks.json`。不存在或损坏一律返回空字典(不猜测、不报错阻断)。"""
    path = _watermarks_path(Path(data_dir))
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_watermarks(state: dict[str, Any], *, data_dir: Path) -> None:
    """原子写 `data_dir/watermarks.json`：先写临时文件再 `os.replace`。

    参照 `resource_control.py` 的 `_atomic_json`：临时文件与目标文件同目录(保证
    `os.replace` 是同一文件系统内的原子操作)，写失败时清理临时文件并重新抛出。
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _watermarks_path(data_dir)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(data_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


__all__ = [
    "EVENTS_FILENAME",
    "WATERMARKS_FILENAME",
    "HrEventRejected",
    "append_events",
    "read_events",
    "load_watermarks",
    "save_watermarks",
]
