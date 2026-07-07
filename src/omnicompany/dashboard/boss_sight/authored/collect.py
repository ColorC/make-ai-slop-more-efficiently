# [OMNI] origin=claude-code domain=dashboard/boss_sight ts=2026-07-03T00:00:00Z type=infra status=active
# [OMNI] summary="决策炼化『长prompt自动采集步』: 扫claude+codex用户消息(复用work_history.sources抽取器), 长度达标写成uses=llm_input札记喂extract_decisions, 幂等键=会话id+消息哈希, 洪水闸(首跑仅收最近window_days天, 单日上限daily_cap条, 超限收最长的+余量写待收清单)。"
# [OMNI] why="批3开工锚(overnight-run.md「批3 开工锚」第二路侦察修正㈠): 存量长消息一次性灌进炼化会冲掉手挑信号, 折进既有 decisions-run 不新起管线, 炼化侧(extract_decisions)零改动。"
# [OMNI] tags=semantic-os,decisions,auto-collect,batch3
"""决策炼化『长 prompt 自动采集步』。

collect_long_prompts(...): 扫 claude/codex 用户消息(签名同 work_history.sources 的
claude_user_messages(days) / codex_user_messages(days)) → 长度达标(>=min_chars)的用户
亲手消息, 写成 AuthoredStore 里 uses=["llm_input"] 的札记(author="user",
extra.source="auto-collected"), 供既有 extract_decisions() 消费(零改动)。

幂等: note_id = compute_note_id(会话, 消息正文) 存进 extra.dedup_key, 重跑按此去重
(不依赖 AuthoredStore.create() 生成的 uuid id, 那是内部主键不是幂等键)。

洪水闸: 按天分组, 单日超过 daily_cap 条时只收最长的 daily_cap 条, 其余写入
waitlist_path(未传则仍在 report 里如实报出 overflow 计数, 不静默丢弃)。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

_DEFAULT_MIN_CHARS = 500
_DEFAULT_WINDOW_DAYS = 7
_DEFAULT_DAILY_CAP = 30


def compute_note_id(session: str, text: str) -> str:
    """幂等键 = 会话id + 消息哈希。相同(会话,消息)恒定同一 id, 不同则不同。"""
    h = hashlib.sha256(f"{session}\x00{text}".encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"autocollect_{h}"


def _byte_len(text: str) -> int:
    """长度阈值按 UTF-8 编码字节数算(而非 Python 字符数) —— 中文场景下"500 字"这类口语
    阈值落到字节度量更符合直觉(纯 ASCII 场景两者一致, 不影响英文消息判断)。"""
    return len(text.encode("utf-8", errors="ignore"))


def _is_user_message(msg: dict[str, Any]) -> bool:
    """契约上要求对带有非用户标记(如误传 role=assistant)的记录也拒收, 不假设上游绝对干净。"""
    role = msg.get("role")
    if role and str(role).lower() not in ("user", ""):
        return False
    return True


def _day_key(ts: str) -> str:
    """从消息的 ts 字段取日期分组键(容错: 解析失败落入 'unknown' 桶, 仍参与采集但不影响其余天)。"""
    raw = str(ts or "").strip()
    if not raw:
        return "unknown"
    # 常见形态: ISO8601('2026-07-01T09:00:00Z' 等) — 直接切前 10 位足够做"天"分组。
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    return "unknown"


def _work_history_sources():
    # work_history 是隐私排除的可选服务(不进公开白名单): 动态导入, 缺失时降级为空源
    import importlib
    try:
        return importlib.import_module("omnicompany.packages.services._governance.work_history.sources")
    except ModuleNotFoundError:
        return None


def _default_claude_source(days: int) -> Iterator[dict[str, Any]]:
    srcs = _work_history_sources()
    return srcs.claude_user_messages(days) if srcs else iter(())


def _default_codex_source(days: int) -> Iterator[dict[str, Any]]:
    srcs = _work_history_sources()
    return srcs.codex_user_messages(days) if srcs else iter(())


def collect_long_prompts(
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    daily_cap: int = _DEFAULT_DAILY_CAP,
    min_chars: int = _DEFAULT_MIN_CHARS,
    claude_source: Callable[[int], Iterable[dict[str, Any]]] | None = None,
    codex_source: Callable[[int], Iterable[dict[str, Any]]] | None = None,
    store: Any = None,
    waitlist_path: Path | str | None = None,
) -> dict[str, Any]:
    """扫 claude+codex 用户消息, 长消息写成 llm_input 札记喂决策炼化。只收用户消息, 幂等,
    洪水闸(见模块 docstring)。"""
    from .store import get_authored_store

    claude_source = claude_source or _default_claude_source
    codex_source = codex_source or _default_codex_source
    store = store or get_authored_store()

    # 幂等: 已采集过的 dedup_key 集合(existing llm_input 札记里 extra.dedup_key)。
    existing_keys = {
        n.extra.get("dedup_key")
        for n in store.list(uses="llm_input", include_archived=True)
        if n.extra.get("dedup_key")
    }

    candidates: list[dict[str, Any]] = []
    for msg in claude_source(window_days):
        candidates.append(msg)
    for msg in codex_source(window_days):
        candidates.append(msg)

    # 过滤: 只留用户亲手消息 + 长度达标 + 非重复
    filtered: list[dict[str, Any]] = []
    for msg in candidates:
        if not _is_user_message(msg):
            continue
        text = str(msg.get("text") or "")
        if _byte_len(text) < min_chars:
            continue
        session = str(msg.get("proj") or msg.get("session") or "")
        dedup_key = compute_note_id(session, text)
        if dedup_key in existing_keys:
            continue
        filtered.append({**msg, "_session": session, "_text": text, "_dedup_key": dedup_key})

    skipped_duplicate = sum(
        1 for msg in candidates
        if _is_user_message(msg)
        and _byte_len(str(msg.get("text") or "")) >= min_chars
        and compute_note_id(str(msg.get("proj") or msg.get("session") or ""), str(msg.get("text") or "")) in existing_keys
    )

    # 洪水闸: 按天分组, 单日超过 daily_cap 条时只收最长的, 其余进待收清单。
    by_day: dict[str, list[dict[str, Any]]] = {}
    for msg in filtered:
        by_day.setdefault(_day_key(msg.get("ts", "")), []).append(msg)

    to_collect: list[dict[str, Any]] = []
    overflow_items: list[dict[str, Any]] = []
    seen_keys_this_run: set[str] = set()
    for _day, msgs in by_day.items():
        msgs_sorted = sorted(msgs, key=lambda m: _byte_len(m["_text"]), reverse=True)
        kept, overflowed = msgs_sorted[:daily_cap], msgs_sorted[daily_cap:]
        for msg in kept:
            if msg["_dedup_key"] in seen_keys_this_run:
                continue  # 同一批次里(claude+codex 都命中同一会话同一文本)不重复收
            seen_keys_this_run.add(msg["_dedup_key"])
            to_collect.append(msg)
        overflow_items.extend(overflowed)

    collected = 0
    for msg in to_collect:
        store.create(
            content=msg["_text"],
            author="user",
            uses=["llm_input"],
            target={"kind": "llm_session", "id": msg["_session"]},
            extra={"source": "auto-collected", "dedup_key": msg["_dedup_key"],
                   "src": msg.get("src", ""), "ts": msg.get("ts", "")},
        )
        collected += 1

    overflow = len(overflow_items)
    if waitlist_path and overflow_items:
        wp = Path(waitlist_path)
        wp.parent.mkdir(parents=True, exist_ok=True)
        with wp.open("a", encoding="utf-8") as fh:
            for msg in overflow_items:
                fh.write(json.dumps({
                    "session": msg["_session"], "text": msg["_text"],
                    "src": msg.get("src", ""), "ts": msg.get("ts", ""),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False) + "\n")

    return {
        "ok": True,
        "collected": collected,
        "skipped_duplicate": skipped_duplicate,
        "overflow": overflow,
        "window_days": window_days,
        "daily_cap": daily_cap,
        "min_chars": min_chars,
    }


__all__ = ["collect_long_prompts", "compute_note_id"]
