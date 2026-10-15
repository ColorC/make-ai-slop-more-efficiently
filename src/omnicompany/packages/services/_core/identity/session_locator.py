# [OMNI] origin=claude-code domain=services/_core/identity ts=2026-08-06T00:00:00Z type=service status=active agent=claude
# [OMNI] summary="按任意 id(omni pty_id / chat id / trace_id / 各 CLI 原生 session id / 进程 pid / 会话文件名)反查会话: 命中 cc_sessions.json 注册表 + 落到磁盘的全部会话文件位置(原生 transcript / pty host state / host log / 绑定台账)"
# [OMNI] why="pty 侧早有 conversation_jsonl_for 这套 provider→原生会话文件 定位器, 但只在 daemon 内部用; 人和 AI 手上通常只有一个裸 id(dashboard 上看到的 pty_id, 或 codex/claude 自己打印的 session id), 没有入口把它翻成'会话文件在哪 + 这会话是干嘛的'。这层把反查抽成公共服务, CLI 与 dashboard 共用同一份定位逻辑, 不各自再拼一遍路径。"
# [OMNI] tags=identity,session,locate,pty,transcript,cross-provider
# [OMNI] material_id="material:core.identity.session_locator.implementation.py"
"""会话反查 —— 从任意 id 找到会话文件位置与会话信息。

**问题**:
一个会话在不同系统里有不同的 id:
  - omnicompany 注册表主键: pty_id (`a1c7315e7df64739`) 或 chat id (`chat-73322cebce56`)
  - 身份链 trace_id: `codex_<uuid>` / `cc_<uuid>`
  - 各家 CLI 自己的原生 session id: codex 的 thread uuid、claude code 的 session uuid、
    kimi/opencode/codebuddy/kiro/qoder 各自的 id
  - 磁盘上的文件名: `rollout-2026-08-04T14-29-54-<uuid>.jsonl`、`<uuid>.jsonl`
  - 进程 pid (host_pid / child_pid)
拿到其中任意一个, 都应该能问出同一句话: 这会话的文件在哪、它是谁、在干嘛。

**解法**:
本模块提供 `locate_sessions(query)`:
  1. 归一化 query (剥 trace 前缀 / rollout 文件名 / 路径 / 扩展名);
  2. 在 `data/cc_sessions.json` 注册表里跨 **所有** id 字段匹配 (精确 → 前缀 → 子串);
  3. 对每条命中, 复用 pty 侧既有的 `conversation_jsonl_for` 拿原生 transcript,
     再补上 omni 自己的落盘物 (pty host state / host log / 绑定台账 / 注册表本身);
  4. 注册表没有的 id (别处直接起的会话), 回落到各 provider 目录直接扫。

真源与定位器都不在本模块重新实现 —— provider→文件 的映射只有 pty.py 一份。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


# 身份链会给裸 session id 加 provider 前缀 (identity/resolver.py 的 trace_id 口径)。
_TRACE_PREFIXES = (
    "codex_",
    "claude_",
    "cc_",
    "kimi_",
    "opencode_",
    "codebuddy_",
    "kiro_",
    "qoder_",
    "zcode_",
    "pty_",
)

# 注册表条目里所有"可能是某个 id"的字段, 按可信度排序 (先命中的当作主命中字段)。
_ID_FIELDS = (
    "id",
    "provider_session_id",
    "claude_session_id",
    "resumed_from_provider_session_id",
    "resumed_from_claude_session_id",
    "resumed_from_pty_id",
    "codex_process_uuid",
)

_PID_FIELDS = ("child_pid", "host_pid")


@dataclass
class FileRef:
    """会话的一处落盘位置。"""

    role: str          # transcript / registry / pty_host_state / pty_host_log / binding / project_dir
    path: str
    exists: bool
    size: int | None = None
    mtime: float | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "path": self.path,
            "exists": self.exists,
            "size": self.size,
            "mtime": self.mtime,
            "mtime_str": _ts_str(self.mtime),
            "note": self.note,
        }


@dataclass
class SessionMatch:
    """一次反查命中的会话。"""

    session_id: str            # 注册表主键 (pty_id / chat id); 孤儿会话为原生 id
    kind: str                  # pty / chat / orphan
    provider: str | None
    matched_field: str         # 是哪个 id 字段命中的
    matched_value: str
    match_mode: str            # exact / prefix / substring / pid
    source: str                # registry / provider_scan
    entry: dict[str, Any] = field(default_factory=dict)
    files: list[FileRef] = field(default_factory=list)
    binding: dict[str, Any] | None = None
    agent: dict[str, Any] | None = None
    preview: dict[str, Any] | None = None

    @property
    def transcript(self) -> FileRef | None:
        return next((f for f in self.files if f.role == "transcript" and f.exists), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "kind": self.kind,
            "provider": self.provider,
            "matched_field": self.matched_field,
            "matched_value": self.matched_value,
            "match_mode": self.match_mode,
            "source": self.source,
            "entry": self.entry,
            "files": [f.to_dict() for f in self.files],
            "binding": self.binding,
            "agent": self.agent,
            "preview": self.preview,
        }


# ── 工具 ─────────────────────────────────────────────────────────────────────


def _ts_str(ts: float | None) -> str:
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return ""


def _file_ref(role: str, path: Path | None, *, note: str = "") -> FileRef | None:
    if path is None:
        return None
    try:
        st = path.stat()
        return FileRef(role, str(path), True, st.st_size, st.st_mtime, note)
    except OSError:
        return FileRef(role, str(path), False, None, None, note)


def normalize_query(raw: str) -> list[str]:
    """把用户给的东西归一成一串候选 id (原样优先)。

    支持: 裸 id / trace_id(`codex_<uuid>`) / 会话文件名(`rollout-<ts>-<uuid>.jsonl`,
    `<uuid>.jsonl`) / 会话文件绝对路径。
    """
    candidates: list[str] = []

    def push(value: str) -> None:
        value = value.strip().strip("\"'")
        if value and value not in candidates:
            candidates.append(value)

    push(raw)
    text = raw.strip().strip("\"'")

    # 绝对路径 / 文件名 → 取 stem
    if any(sep in text for sep in ("/", "\\")) or text.lower().endswith((".jsonl", ".json")):
        stem = Path(text).name
        for suffix in (".jsonl", ".json"):
            if stem.lower().endswith(suffix):
                stem = stem[: -len(suffix)]
        push(stem)
        text = stem

    # codex rollout 文件名: rollout-<iso时间戳>-<uuid>
    m = re.match(r"^rollout-\d{4}-\d{2}-\d{2}T[\d-]+-(?P<uuid>[0-9a-fA-F-]{8,})$", text)
    if m:
        push(m.group("uuid"))
        text = m.group("uuid")

    m = re.match(r"^model-io-(?P<session>sess_[0-9a-fA-F-]{8,})$", text)
    if m:
        push(m.group("session"))
        text = m.group("session")

    # trace_id 前缀
    for prefix in _TRACE_PREFIXES:
        if text.lower().startswith(prefix):
            push(text[len(prefix):])
            break

    return candidates


def _read_registry() -> dict[str, dict[str, Any]]:
    """读 cc_sessions.json —— 走 daemon 的跨进程锁, 不自己开文件。"""
    from omnicompany.dashboard.ccdaemon.api import read_meta_store

    return read_meta_store(use_cache=False)


def registry_path() -> Path:
    from omnicompany.dashboard.ccdaemon.api import meta_store_path

    return meta_store_path()


def _pty_host_dir() -> Path:
    from omnicompany.dashboard.ccdaemon.api import pty_host_dir as host_dir

    return host_dir()


def _conversation_jsonl_lookup(
    provider: str | None,
    cwd: str,
    provider_session_id: str | None,
) -> Path | None:
    """provider → 原生会话文件。真源是 pty.py 那一份, 这里只是可替身的接缝。"""
    from omnicompany.dashboard.ccdaemon.api import conversation_jsonl_for

    return conversation_jsonl_for(provider, cwd, provider_session_id)


# ── 注册表匹配 ───────────────────────────────────────────────────────────────


def _match_entry(
    sid: str,
    entry: dict[str, Any],
    candidates: Iterable[str],
) -> tuple[str, str, str] | None:
    """返回 (matched_field, matched_value, match_mode); 没命中返回 None。"""
    lowered = [c.lower() for c in candidates]
    values: list[tuple[str, str]] = []
    for fname in _ID_FIELDS:
        raw = entry.get(fname) if fname != "id" else (entry.get("id") or sid)
        if raw:
            values.append((fname, str(raw)))

    for mode in ("exact", "prefix", "substring"):
        for fname, value in values:
            low = value.lower()
            for cand in lowered:
                if mode == "exact" and low == cand:
                    return fname, value, mode
                if mode == "prefix" and len(cand) >= 6 and low.startswith(cand):
                    return fname, value, mode
                if mode == "substring" and len(cand) >= 8 and cand in low:
                    return fname, value, mode

    # 纯数字 → 当 pid 查 (dashboard 上只看得到进程时用)
    for cand in lowered:
        if cand.isdigit():
            for fname in _PID_FIELDS:
                if str(entry.get(fname) or "") == cand:
                    return fname, cand, "pid"
    return None


def _binding_for(entry: dict[str, Any], sid: str) -> dict[str, Any] | None:
    """会话在绑定台账 (cc_session_bindings.json) 里的记录, 有就带上。"""
    try:
        from omnicompany.packages.services._core.identity import all_session_bindings

        bindings = all_session_bindings() or {}
    except Exception:
        return None
    if not isinstance(bindings, dict):
        return None

    provider = entry.get("provider") or ""
    psid = entry.get("provider_session_id") or ""
    keys = [sid]
    if psid:
        keys += [f"{provider}_{psid}", f"cc_{psid}", psid]
    for key in keys:
        found = bindings.get(key)
        if isinstance(found, dict):
            return found
    # 兜底: 值里 session_id / claude_session_id 对得上
    for value in bindings.values():
        if not isinstance(value, dict):
            continue
        if psid and psid in {value.get("session_id"), value.get("claude_session_id")}:
            return value
    return None


def _agent_record_for(sid: str, entry: dict[str, Any]) -> tuple[Path, dict[str, Any]] | None:
    """boss_sight agent 注册表里这条会话的身份卡 (名字 / 角色 / 在做啥)。

    只读 `load_registry()`, 不走 `find_record()` —— 后者会 rebuild_first, 一条
    查询命令不该顺手重建全表。
    """
    try:
        from omnicompany.packages.services.boss_sight.services.agent_registry import (
            _store_path,
            load_registry,
        )

        store = load_registry() or {}
        path = _store_path()
    except Exception:
        return None

    psid = str(entry.get("provider_session_id") or entry.get("claude_session_id") or "")
    provider = str(entry.get("provider") or "")
    keys = [f"{provider}:{psid}"] if provider and psid else []
    for key in keys:
        record = store.get(key)
        if isinstance(record, dict):
            return path, record
    for record in store.values():
        if not isinstance(record, dict):
            continue
        if record.get("pty_id") == sid or (psid and record.get("session_id") == psid):
            return path, record
    return None


def _event_bus_ref(sid: str, entry: dict[str, Any]) -> FileRef | None:
    """事件总线里这条会话的留痕 —— 真有事件才报, 没有就不列这个文件。

    codex PTY 不走 claude code 的 hook 链, 事件表里通常一条都没有; 那种情况下
    报一个"存在但空"的 db 路径等于给假线索。
    """
    try:
        from omnicompany.core.config import omni_workspace_root

        db_path = omni_workspace_root() / "data" / "ide_events.db"
    except Exception:
        return None
    if not db_path.is_file():
        return None

    psid = str(entry.get("provider_session_id") or entry.get("claude_session_id") or "")
    provider = str(entry.get("provider") or "")
    candidates = [sid]
    if psid:
        candidates += [f"{provider}_{psid}", f"cc_{psid}", psid]
    candidates = [c for c in dict.fromkeys(candidates) if c]

    try:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.5) as db:
            placeholders = ",".join("?" * len(candidates))
            row = db.execute(
                f"SELECT COUNT(*), MAX(timestamp) FROM events WHERE trace_id IN ({placeholders})",
                candidates,
            ).fetchone()
    except (OSError, sqlite3.Error):
        return None
    count = int((row or [0])[0] or 0)
    if not count:
        return None
    return _file_ref("event_bus", db_path, note=f"{count} 条事件, 最近 {(row[1] or '')[:19]}")


def _files_for_registry_entry(sid: str, entry: dict[str, Any]) -> list[FileRef]:
    provider = entry.get("provider")
    cwd = str(entry.get("cwd") or "")
    psid = entry.get("provider_session_id") or entry.get("claude_session_id")

    files: list[FileRef] = []

    transcript = None
    try:
        transcript = _conversation_jsonl_lookup(provider, cwd, psid)
    except Exception:
        transcript = None
    if transcript is None and psid:
        # provider 字段缺失/对不上时, 按 id 横扫各家目录兜底。
        scanned = scan_provider_files(str(psid))
        if scanned:
            found_provider, transcript = scanned[0]
            ref = _file_ref(
                "transcript",
                transcript,
                note=f"provider 字段={provider or '-'}, 实际命中 {found_provider} 目录",
            )
            if ref:
                files.append(ref)
            transcript = None
    if transcript is not None:
        ref = _file_ref("transcript", transcript, note=f"{provider} 原生会话记录")
        if ref:
            files.append(ref)
    elif not files and psid:
        files.append(
            FileRef(
                "transcript",
                "",
                False,
                note=f"provider={provider or '-'} session={psid} 没在任何 provider 目录找到落盘记录",
            )
        )

    # omni 自己的落盘物
    reg = _file_ref("registry", registry_path(), note=f"注册表条目 key = {sid}")
    if reg:
        files.append(reg)

    host_dir = _pty_host_dir()
    for role, path in (
        ("pty_host_state", host_dir / f"{sid}.json"),
        ("pty_host_log", host_dir / f"{sid}.log"),
    ):
        ref = _file_ref(role, path)
        if ref and ref.exists:
            files.append(ref)
    snapshots = sorted(host_dir.glob(f"{sid}.state.*.json"), reverse=True)
    if snapshots:
        ref = _file_ref(
            "pty_host_state_snapshot",
            snapshots[0],
            note=f"共 {len(snapshots)} 份代际快照",
        )
        if ref:
            files.append(ref)

    agent = _agent_record_for(sid, entry)
    if agent is not None:
        agent_path, record = agent
        ref = _file_ref(
            "agent_registry",
            agent_path,
            note=f"身份卡 key = {record.get('key') or '-'}",
        )
        if ref:
            files.append(ref)

    bus = _event_bus_ref(sid, entry)
    if bus:
        files.append(bus)

    return files


# ── provider 目录直扫 (注册表里没有的会话) ───────────────────────────────────


def scan_provider_files(session_id: str) -> list[tuple[str, Path]]:
    """按原生 session id 在各家 CLI 的会话目录里直接找文件。

    用于注册表没登记的会话 (在 dashboard 之外直接起的)。前缀不完整时按前缀 glob。
    """
    from omnicompany.dashboard.ccdaemon.api import claude_home_for

    sid = session_id.strip()
    if not sid:
        return []
    home = Path.home()
    star = "" if len(sid) >= 32 else "*"
    hits: list[tuple[str, Path]] = []

    patterns: list[tuple[str, Path, str]] = [
        ("claude_code", home / ".claude" / "projects", f"*/{sid}{star}.jsonl"),
        # 第二个官方 Anthropic 账号的转录在自己的 CLAUDE_CONFIG_DIR 下。
        ("claude_2", claude_home_for("claude_2") / "projects", f"*/{sid}{star}.jsonl"),
        ("codex", home / ".codex" / "sessions", f"*/*/*/rollout-*-{sid}{star}.jsonl"),
        ("codebuddy", home / ".codebuddy" / "projects", f"*/{sid}{star}.jsonl"),
        ("qoder", home / ".qoder" / "projects", f"*/{sid}{star}.jsonl"),
        ("kimi", home / ".kimi-code" / "sessions", f"*/session_{sid}{star}/state.json"),
        ("kiro", home / ".kiro" / "sessions" / "cli", f"{sid}{star}.json"),
        ("zcode", home / ".zcode" / "cli" / "rollout", f"model-io-{sid}{star}.jsonl"),
    ]
    for provider, base, pattern in patterns:
        if not base.is_dir():
            continue
        try:
            for path in base.glob(pattern):
                if path.is_file():
                    hits.append((provider, path))
        except OSError:
            continue

    db_path = home / ".local" / "share" / "opencode" / "opencode.db"
    if db_path.is_file():
        try:
            uri = f"file:{db_path.as_posix()}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=0.3) as db:
                row = db.execute(
                    "SELECT 1 FROM session WHERE id LIKE ? LIMIT 1", (f"{sid}%",)
                ).fetchone()
            if row:
                hits.append(("opencode", db_path))
        except (OSError, sqlite3.Error):
            pass

    return hits


# ── 会话内容速览 ─────────────────────────────────────────────────────────────


def _iter_head_lines(path: Path, count: int) -> list[str]:
    out: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                out.append(line)
                if len(out) >= count:
                    break
    except OSError:
        return []
    return out


def _iter_tail_lines(path: Path, count: int, *, window: int = 262144) -> list[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - window))
            blob = fh.read()
    except OSError:
        return []
    text = blob.decode("utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-count:]


def _text_of(value: Any) -> str:
    """从各家不同的 message 结构里抠出可读文本。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(p for p in parts if p)
    if isinstance(value, dict):
        return _text_of(value.get("content") or value.get("text") or "")
    return ""


# role=user 的记录里塞的往往不是人说的话: AGENTS.md/CLAUDE.md 注入、环境块、
# 系统提醒、hook 追加的上下文。速览要认人话, 这些一律跳过。
_INJECTED_MARKERS = (
    "<system-reminder",
    "<environment_context",
    "<user_instructions",
    "<INSTRUCTIONS>",
    "# AGENTS.md",
    "# CLAUDE.md",
    "Caveat: The messages below",
    "<command-name>",
    "<local-command",
    "<task-notification>",
)


def _human_text(record: dict[str, Any]) -> tuple[str, bool]:
    """返回 (人类输入文本, 是否强证据); 不是人说的返回 ("", False)。

    强证据 = provider 自己标成"用户这一轮说的话"的事件:
      codex : {"type":"event_msg","payload":{"type":"user_message","message":"..."}}
    弱证据 = 单纯 role=user 的对话条目 —— 注入块也长这样, 只在没有强证据时兜底。
      claude: {"type":"user","message":{"role":"user","content":[{"text":"..."}]}}
    """
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    strong = False
    if record.get("type") == "event_msg" and payload.get("type") == "user_message":
        text = str(payload.get("message") or "").strip()
        strong = True
    else:
        if record.get("isMeta") or record.get("isCompactSummary"):
            return "", False
        message = record.get("message") if isinstance(record.get("message"), dict) else None
        role = (message or {}).get("role") or payload.get("role") or record.get("role") or ""
        if role != "user":
            return "", False
        text = _text_of(message or payload or record.get("content") or "").strip()

    if not text:
        return "", False
    head = text[:400].lstrip()
    if head.startswith(_INJECTED_MARKERS) or "system-reminder" in head:
        return "", False
    return text, strong


def _zcode_turn_text(record: dict[str, Any]) -> tuple[str, str]:
    """Extract the real user input and final assistant text from model-io JSONL."""
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    messages = request.get("messages") if isinstance(request.get("messages"), list) else []
    user_text = ""
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            user_text = _text_of(message.get("content")).strip()
            head = user_text[:400].lstrip()
            if head.startswith(_INJECTED_MARKERS) or "system-reminder" in head:
                user_text = ""
                continue
            if user_text:
                break
    response = record.get("response") if isinstance(record.get("response"), dict) else {}
    return user_text, str(response.get("text") or "").strip()


def preview_transcript(path: Path, *, head: int = 400, tail: int = 400) -> dict[str, Any]:
    """读 transcript 头尾, 提炼会话信息 (元数据 / 首条用户消息 / 最后活动)。"""
    info: dict[str, Any] = {
        "path": str(path),
        "first_user": "",
        "last_user": "",
        "last_text": "",
        "meta": {},
        "head_lines": 0,
        "tail_ts": "",
    }
    if path.suffix.lower() != ".jsonl":
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(payload, dict):
                info["meta"] = {
                    k: payload.get(k)
                    for k in ("session_id", "title", "cwd", "created_at", "updated_at")
                    if payload.get(k) is not None
                }
        except (OSError, json.JSONDecodeError):
            pass
        return info

    # 强证据(provider 明确标注的用户轮次) 优先, 没有再退回 role=user 兜底。
    first_strong = ""
    first_weak = ""

    head_lines = _iter_head_lines(path, head)
    info["head_lines"] = len(head_lines)
    for line in head_lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue

        # zcode: {"sessionId":"sess_...", "request":{"messages":...}}
        if path.name.startswith("model-io-"):
            info["meta"].update(
                {
                    dest: rec[key]
                    for key, dest in (("sessionId", "id"), ("startedAt", "started_at"), ("completedAt", "completed_at"))
                    if rec.get(key) is not None
                }
            )
            user_text, _assistant_text = _zcode_turn_text(rec)
            if user_text and not first_strong:
                first_strong = user_text

        # codex: {"type":"session_meta","payload":{...}}
        rtype = rec.get("type") or ""
        if rtype == "session_meta" and isinstance(rec.get("payload"), dict):
            payload = rec["payload"]
            info["meta"].update(
                {
                    k: payload.get(k)
                    for k in ("id", "cwd", "originator", "cli_version", "timestamp", "instructions")
                    if payload.get(k) is not None
                }
            )
            if isinstance(info["meta"].get("instructions"), str):
                info["meta"]["instructions"] = info["meta"]["instructions"][:200]
        # claude code: 每行带 sessionId / cwd / gitBranch / version
        for key, dest in (
            ("sessionId", "id"),
            ("cwd", "cwd"),
            ("gitBranch", "git_branch"),
            ("version", "cli_version"),
        ):
            if rec.get(key) and dest not in info["meta"]:
                info["meta"][dest] = rec[key]

        if not first_strong:
            human, strong = _human_text(rec)
            if human and strong:
                first_strong = human
            elif human and not first_weak:
                first_weak = human

    # 最近一条人话可能被一长串工具调用推得很远, 尾窗口逐级放大直到找到 (有上限)。
    for window in (256 * 1024, 2 * 1024 * 1024, 16 * 1024 * 1024):
        for line in reversed(_iter_tail_lines(path, tail, window=window)):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            ts = rec.get("timestamp") or rec.get("ts")
            if ts and not info["tail_ts"]:
                info["tail_ts"] = str(ts)
            if not info["last_user"]:
                if path.name.startswith("model-io-"):
                    human, text = _zcode_turn_text(rec)
                    if text and not info["last_text"]:
                        info["last_text"] = text[:600]
                else:
                    human, _strong = _human_text(rec)
                if human:
                    info["last_user"] = human[:600]
            if not info["last_text"]:
                message = rec.get("message") if isinstance(rec.get("message"), dict) else None
                text = _text_of(message or rec.get("content") or rec.get("payload") or "").strip()
                if text:
                    info["last_text"] = text[:600]
        if info["last_user"] or window >= path.stat().st_size:
            break

    info["first_user"] = first_strong or first_weak
    return info


# ── 主入口 ───────────────────────────────────────────────────────────────────


def locate_sessions(
    query: str,
    *,
    limit: int = 10,
    peek: bool = False,
    include_orphans: bool = True,
) -> list[SessionMatch]:
    """按任意 id 反查会话, 返回命中列表 (注册表命中在前)。"""
    candidates = normalize_query(query)
    if not candidates:
        return []

    matches: list[SessionMatch] = []
    seen_ids: set[str] = set()

    try:
        registry = _read_registry()
    except Exception:
        registry = {}

    scored: list[tuple[int, str, dict[str, Any], tuple[str, str, str]]] = []
    order = {"exact": 0, "pid": 1, "prefix": 2, "substring": 3}
    for sid, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        hit = _match_entry(sid, entry, candidates)
        if hit:
            scored.append((order.get(hit[2], 9), sid, entry, hit))

    scored.sort(key=lambda row: (row[0], -float(row[2].get("started_at") or 0)))
    for _rank, sid, entry, (fname, fvalue, mode) in scored[:limit]:
        match = SessionMatch(
            session_id=sid,
            kind=str(entry.get("kind") or ("chat" if sid.startswith("chat-") else "pty")),
            provider=entry.get("provider"),
            matched_field=fname,
            matched_value=fvalue,
            match_mode=mode,
            source="registry",
            entry=entry,
            files=_files_for_registry_entry(sid, entry),
            binding=_binding_for(entry, sid),
        )
        agent = _agent_record_for(sid, entry)
        match.agent = agent[1] if agent else None
        if peek and match.transcript:
            match.preview = preview_transcript(Path(match.transcript.path))
        matches.append(match)
        seen_ids.add(sid)

    if matches or not include_orphans:
        return matches

    # 注册表没有 → 直接扫 provider 目录 (别处起的会话)
    for cand in candidates:
        for provider, path in scan_provider_files(cand):
            native_id = path.stem
            if provider == "zcode" and native_id.startswith("model-io-"):
                native_id = native_id.removeprefix("model-io-")
            m = re.search(r"-([0-9a-fA-F-]{36})$", native_id)
            if m:
                native_id = m.group(1)
            if native_id in seen_ids:
                continue
            seen_ids.add(native_id)
            ref = _file_ref("transcript", path, note=f"{provider} 原生会话记录 (注册表未登记)")
            match = SessionMatch(
                session_id=native_id,
                kind="orphan",
                provider=provider,
                matched_field="native_session_id",
                matched_value=native_id,
                match_mode="exact" if native_id.lower() == cand.lower() else "prefix",
                source="provider_scan",
                files=[ref] if ref else [],
            )
            if peek and match.transcript:
                match.preview = preview_transcript(path)
            matches.append(match)
            if len(matches) >= limit:
                return matches
    return matches


__all__ = [
    "FileRef",
    "SessionMatch",
    "locate_sessions",
    "normalize_query",
    "preview_transcript",
    "scan_provider_files",
    "registry_path",
]
