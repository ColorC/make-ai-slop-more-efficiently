# [OMNI] origin=claude domain=services/_focus ts=2026-09-05 type=service status=active
# [OMNI] summary="会话完成注意力: 完成指纹 + 已看记录 + 活跃投影。把原先只活在前端 localStorage 的那份判据提到后端, 让收件箱的「未看会话」能当消息源, 也让 litework 读同一条总线。"
# [OMNI] why="计划 FOCUS-INBOX-PERSONAL-MESSAGE-BUS 阶段 2.2b: 队列里最重要的一类是未回复/未看的会话, 但完成指纹只存在浏览器里, 换设备即丢、第二个端读不到。"
# [OMNI] material_id="material:packages.services._focus.session_attention"
"""会话完成注意力 —— 「跑完一轮却还没被看」的那份判据。

分三样东西, 各自的真源不同:

  1. **完成指纹**(fingerprint) —— 由会话的活体状态算出来的一个身份串。同一轮完成
     算出同一个串, 又跑了一轮就换一个串。真源是 ccdaemon(它才知道 last_submit_at /
     has_user_turn / working)。
  2. **活跃投影**(projection) —— ccdaemon 把「完成相关的那几个字段 + 算好的指纹」写下来
     的一份小文件。**投影不是真源**, 删了下一次列会话就重建。它的存在只是为了让不在
     dashboard 进程里的人(CLI 的 `omni inbox sync`、litework)也能读到同一份判据。
  3. **已看记录**(seen) —— 人点进去看过哪一轮。这才是**必须持久**的那份, 也正是原先
     只存在浏览器 localStorage 里、换设备就丢的那份。

分层: 本模块在 packages 层, 不 import dashboard(arch_gate R2)。方向是 ccdaemon 往下写
投影(dashboard 依赖 packages 合法), 不是本模块往上读 ccdaemon。与 agent_attention
同一个套路(CLI 写 / ccdaemon 读的跨进程 JSON 文件), 不另发明机制。

指纹由**后端独家生成**并随投影一起发布, 前端只消费不重算 —— 两种语言各自把浮点秒
格式化成字符串是能对不上的, 让它只有一个作者就没有这个问题。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable

_lock = threading.RLock()

# 指纹格式版本。改了算法就抬它, 老记录自然失效(最多让人多看一眼, 不会误判成已看)。
FINGERPRINT_VERSION = 1


def _root() -> Path:
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root()


def seen_path() -> Path:
    override = os.environ.get("OMNI_SESSION_ATTENTION_PATH")
    if override:
        return Path(override)
    return _root() / "data" / "focus" / "session_attention.json"


def projection_path() -> Path:
    override = os.environ.get("OMNI_SESSION_COMPLETIONS_PATH")
    if override:
        return Path(override)
    return _root() / "data" / "focus" / "session_completions.json"


# ─── 完成指纹 ───────────────────────────────────────────────────────────

def compact_hash(value: str) -> str:
    """FNV-1a(32) → base36。与前端 sessionAttention.ts 的 compactHash 逐位等价。

    前端按 UTF-16 code unit 逐个异或(charCodeAt), 所以这里也必须按 UTF-16 code unit
    走 —— 直接 ord() 在 BMP 内一致, 但遇到 emoji 那种代理对就会分叉。
    """
    hash_value = 0x811C9DC5
    for unit in memoryview(value.encode("utf-16-le")).cast("H"):
        hash_value ^= unit
        hash_value = (hash_value * 0x01000193) & 0xFFFFFFFF
    return _base36(hash_value)


def _base36(value: int) -> str:
    if value == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while value:
        value, rem = divmod(value, 36)
        out = digits[rem] + out
    return out


def _number(value: Any) -> str:
    """把数字排成和 JS `String(n)` 一样的形状 —— 整数不带 .0。

    指纹只有后端一个作者, 严格说不必对齐 JS; 但迁移期要认前端存下来的老记录,
    形状对上就能少让人多看一遍。
    """
    if isinstance(value, bool) or value is None:
        return "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)


def completion_fingerprint(session_id: str, meta: dict[str, Any] | None) -> str | None:
    """最近一轮「已完成」的身份串; 又跑了一轮就是另一个串。

    对齐前端 sessionAttention.ts 的同名函数:
      · pty 没有真实用户回合(空终端)不算完成 —— 开着不用的终端不该来提醒你;
      · chat 一条消息都没有也不算。
    """
    if not meta:
        return None
    kind = str(meta.get("kind") or "")
    last_message = str(meta.get("lastMessage") or "")
    if kind == "pty":
        if meta.get("hasUserTurn") is not True:
            return None
        completion_version = meta.get("lastSubmitAt")
    else:
        if not meta.get("messageCount") and not last_message:
            return None
        completion_version = meta.get("messageCount")
        if completion_version is None:
            completion_version = meta.get("lastActivityAt")
        if completion_version is None:
            completion_version = compact_hash(last_message)
    if completion_version is None:
        return None
    started_at = meta.get("startedAt") or 0
    return ":".join((
        session_id,
        kind,
        _number(started_at),
        completion_version if isinstance(completion_version, str) else _number(completion_version),
        compact_hash(last_message),
    ))


# ─── 活体状态 → 投影行(纯函数, 只吃 dict; 谁拿到会话快照谁都能调)──────────

def is_foreign_runtime(meta: dict[str, Any]) -> bool:
    """别人家的会话。

    用户 2026-09-05:「omnicompany 就是看到 omnicompany 的, litework 就是看到 litework 的,
    两者完全独立, 物理上没有交集和依赖」。所以 litework 托管的会话一条都不进这个队列 ——
    LiTeWork 自己那一端有自己的消息段, 各看各的。
    """
    return str(meta.get("runtime_host") or "") == "litework" or str(
        meta.get("source") or ""
    ) == "litework-runtime"


def pty_run_state(meta: dict[str, Any]) -> str:
    """对齐前端 tabStatus.ts 的 ptyTabRunState —— 两边算出不同的 done, 同一个会话
    就会在两个面上一个亮一个不亮。"""
    if not meta.get("alive") or str(meta.get("status") or "") == "recoverable":
        return "ended"
    has_turn = bool(meta.get("has_user_turn"))
    if has_turn and meta.get("provider_runtime_known") is False:
        return "unknown"
    return "working" if has_turn and meta.get("working") else "done"


def chat_run_state(meta: dict[str, Any]) -> str:
    """对齐前端 chatTabRunState。

    chat 的 `alive` 是运行时还在不在, 但一段聊完的对话即使运行时早退了, 逻辑上仍然
    存在、也仍然点得开 —— 所以有 logical_alive 就以它为准, 否则会整类漏掉。
    """
    logical = meta.get("logical_alive")
    alive = meta.get("alive") if logical is None else logical
    if not alive or str(meta.get("status") or "") == "ended":
        return "ended"
    return "working" if meta.get("running") else "done"


# 跑完多久之后就不再是「消息」而是历史。用户 2026-09-06 实拍到一段 8 月初的老对话
# 冒进队列: 它只是从来没有过「已看」记录, 不是你欠着的事。
FRESH_WINDOW_S = 3 * 24 * 3600


def _finished_at(meta: dict[str, Any]) -> float:
    for key in ("last_submit_at", "last_activity_at", "last_output_at", "started_at"):
        value = meta.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return 0.0


def is_eligible(meta: dict[str, Any], now: float | None = None) -> bool:
    """能不能进这个队列。用户 2026-09-05 三条口径:

    · **别人家的不算**: litework 托管的会话一条都不进 —— 两端物理独立, 各看各的;
    · **shell 不算**: 必须是 agent 会话, 光开个终端不是「跑完一轮等你看」;
    · **worker 不算**: agent 自己派生出来的会话不该来敲你, 而且多半也跳不过去 ——
      队列里的每一条都必须是**点得开**的那种。
    """
    if is_foreign_runtime(meta):
        return False
    # 隐藏/后台会话本来就不在视野里。
    if str(meta.get("visibility") or "") in ("hidden", "background"):
        return False
    if str(meta.get("origin") or "human") != "human":
        return False
    if meta.get("archived") is True:
        return False
    if not str(meta.get("provider") or "").strip():
        return False
    # 陈年的跑完不算欠着你的事 —— 没有「已看」记录只说明它比这套账本还老。
    finished = _finished_at(meta)
    return bool(finished) and (now or time.time()) - finished <= FRESH_WINDOW_S


def _row_title(meta: dict[str, Any], session_id: str) -> str:
    """消息标题只要人认得出是哪个会话就够, 不复刻前端那条完整的标题退化链。"""
    for key in ("topic", "provider_title", "display_title", "name", "cwd"):
        value = str(meta.get(key) or "").strip()
        if value:
            return value[:160]
    return session_id


def _row(meta: dict[str, Any], kind: str, state: str) -> dict[str, Any] | None:
    session_id = str(meta.get("id") or meta.get("session_id") or "").strip()
    if not session_id:
        return None
    if not is_eligible(meta):
        return None
    if kind == "pty":
        lite = {
            "kind": "pty",
            "startedAt": meta.get("started_at"),
            "hasUserTurn": bool(meta.get("has_user_turn")),
            "lastSubmitAt": meta.get("last_submit_at"),
            "lastMessage": "",
        }
        completion_version = meta.get("last_submit_at")
    else:
        lite = {
            "kind": "chat",
            "startedAt": meta.get("started_at"),
            "messageCount": meta.get("message_count"),
            "lastActivityAt": meta.get("last_activity_at"),
            "lastMessage": meta.get("last_message") or "",
        }
        completion_version = meta.get("message_count")
    fingerprint = completion_fingerprint(session_id, lite)
    if not fingerprint:
        return None
    return {
        "sessionId": session_id,
        "kind": kind,
        "state": state,
        "fingerprint": fingerprint,
        "startedAt": meta.get("started_at"),
        "completionVersion": completion_version,
        "title": _row_title(meta, session_id),
        "provider": str(meta.get("provider") or ""),
        "project": str(meta.get("project") or ""),
    }


def build_rows(
    pty_metas: Iterable[dict[str, Any]],
    chat_metas: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """会话快照 → 投影行。web 展示面会话没有用户回合, 指纹自然算不出来, 与前端一致地落空。"""
    rows: list[dict[str, Any]] = []
    for meta in pty_metas:
        row = _row(meta, "pty", pty_run_state(meta))
        if row:
            rows.append(row)
    for meta in chat_metas:
        row = _row(meta, "chat", chat_run_state(meta))
        if row:
            rows.append(row)
    return rows


# ─── 活跃投影(ccdaemon 写, 谁都可以读)────────────────────────────────────

def _atomic_write(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def publish_projection(rows: Iterable[dict[str, Any]]) -> bool:
    """ccdaemon 调它。**指纹集没变就不写** —— 会话列表是高频调用, 不能每次都落盘。

    返回是否真的写了(测试与诊断用)。
    """
    ordered = sorted(
        (r for r in rows if r.get("sessionId") and r.get("fingerprint")),
        key=lambda r: str(r["sessionId"]),
    )
    signature = [
        [str(r["sessionId"]), str(r["fingerprint"]), str(r.get("state") or "")]
        for r in ordered
    ]
    with _lock:
        path = projection_path()
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            current = None
        if isinstance(current, dict) and current.get("signature") == signature:
            return False
        _atomic_write(path, {
            "$doc": "会话完成投影: ccdaemon 写, 收件箱与 CLI 读。投影不是真源, "
                    "删了下一次列会话就重建; 已看记录在 session_attention.json 里。",
            "version": FINGERPRINT_VERSION,
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "signature": signature,
            "rows": ordered,
        })
        return True


def read_projection() -> list[dict[str, Any]]:
    try:
        doc = json.loads(projection_path().read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = doc.get("rows")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


# ─── 已看记录(必须持久的那份)──────────────────────────────────────────

def read_seen() -> dict[str, str]:
    try:
        doc = json.loads(seen_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    seen = doc.get("seen")
    if not isinstance(seen, dict):
        return {}
    return {str(k): str(v) for k, v in seen.items() if isinstance(v, str)}


def _write_seen(seen: dict[str, str]) -> None:
    _atomic_write(seen_path(), {
        "$doc": "会话已看记录: sessionId -> 看过的那一轮的完成指纹。"
                "这是必须持久的那份(原先只在浏览器 localStorage 里, 换设备即丢)。",
        "version": FINGERPRINT_VERSION,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seen": seen,
    })


def mark_seen(session_id: str, fingerprint: str | None = None) -> str | None:
    """记下「这一轮我看过了」。

    不给指纹就用投影里的当前值 —— 这是常规路径: 前端说「我打开了这个会话」, 具体是
    哪一轮由后端说了算(前端那份可能比投影旧)。给了指纹的是迁移路径(见 import_seen)。
    """
    session_id = str(session_id or "")
    if not session_id:
        return None
    if fingerprint is None:
        for row in read_projection():
            if str(row.get("sessionId")) == session_id:
                fingerprint = str(row.get("fingerprint") or "") or None
                break
    if not fingerprint:
        return None
    with _lock:
        seen = read_seen()
        if seen.get(session_id) == fingerprint:
            return fingerprint
        seen[session_id] = fingerprint
        _write_seen(seen)
    return fingerprint


def import_seen(entries: dict[str, str]) -> int:
    """迁移用: 把浏览器 localStorage 里那份整批收下来。

    **只补不覆盖** —— 服务端已经记着的那一轮更可信(它是从投影来的); 浏览器那份可能是
    另一台设备上的旧账。返回真正写进去的条数。
    """
    added = 0
    with _lock:
        seen = read_seen()
        for key, value in (entries or {}).items():
            key, value = str(key), str(value)
            if not key or not value or key in seen:
                continue
            seen[key] = value
            added += 1
        if added:
            _write_seen(seen)
    return added


def forget(session_id: str) -> bool:
    with _lock:
        seen = read_seen()
        if str(session_id) not in seen:
            return False
        seen.pop(str(session_id))
        _write_seen(seen)
        return True


def prune(known_ids: Iterable[str]) -> int:
    """会话彻底消失后清掉它的已看记录, 免得这份文件只涨不落。"""
    keep = {str(x) for x in known_ids}
    with _lock:
        seen = read_seen()
        drop = [k for k in seen if k not in keep]
        for key in drop:
            seen.pop(key)
        if drop:
            _write_seen(seen)
        return len(drop)


# ─── 未看清单 ───────────────────────────────────────────────────────────

def _legacy_pty_seen_covers(session_id: str, row: dict[str, Any], seen_fp: str | None) -> bool:
    """迁移兜底: v1 的 pty 指纹用的是 last_output_at。

    那次输出如果发生在本次提交之后, 说明这一轮其实已经被看过 —— 认下它, 别让老用户
    在切到后端那天被灌一屏「未看」。与前端 legacyPtySeenCoversCompletion 同一条口径。
    """
    if not seen_fp or str(row.get("kind")) != "pty":
        return False
    submit_at = row.get("completionVersion")
    if not isinstance(submit_at, (int, float)):
        return False
    prefix = f"{session_id}:pty:{_number(row.get('startedAt') or 0)}:"
    if not seen_fp.startswith(prefix):
        return False
    rest = seen_fp[len(prefix):]
    sep = rest.rfind(":")
    if sep <= 0:
        return False
    try:
        seen_version = float(rest[:sep])
    except ValueError:
        return False
    return seen_version >= submit_at


def list_unseen() -> list[dict[str, Any]]:
    """跑完一轮、还没被看的会话。收件箱的 session 适配器就吃这个。"""
    seen = read_seen()
    out: list[dict[str, Any]] = []
    for row in read_projection():
        if str(row.get("state") or "") != "done":
            continue
        session_id = str(row.get("sessionId") or "")
        fingerprint = str(row.get("fingerprint") or "")
        if not session_id or not fingerprint:
            continue
        if seen.get(session_id) == fingerprint:
            continue
        if _legacy_pty_seen_covers(session_id, row, seen.get(session_id)):
            continue
        out.append(row)
    return out
