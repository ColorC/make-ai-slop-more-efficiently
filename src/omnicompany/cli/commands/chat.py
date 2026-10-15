# [OMNI] origin=ai-ide domain=cli/commands ts=2026-05-22T00:00:00Z type=router status=active
# [OMNI] summary="omni chat CLI — 管理 OmniChat / Claude Code / Codex 对话 session"
# [OMNI] why="dashboard chat session 需要 CLI 侧的查/切/搜/改接口, 包括 plan 绑定到 session、session 元数据管理、原始 CC/Codex 对话数据库浏览"
# [OMNI] tags=cli,chat,session,plan-binding,cc-db
# [OMNI] material_id="material:cli.chat.session_manager.implementation.py"
"""omni chat CLI — OmniChat session 管理.

管理 dashboard chat session 的全生命周期:
  - id / list / show / search / name — session 元数据管理
  - plan use / plan — 将 plan 绑定到指定 chat session
  - db list / db show / db search — 浏览 Claude Code / Codex 原始对话数据库
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import click


# ── helpers ──────────────────────────────────────────────────────────────────


def _repo_root() -> Path:
    """omnicompany 仓库根 — 走 core.config 的唯一权威解析器。

    原实现从 cwd 向上找 `src/omnicompany` + `docs` 双命中, 会被工作区根的
    游离同名目录劫持 (见 cli/commands/plan.py 同一处修复)。
    与 hooks/_shared.repo_root() 对齐。
    """
    try:
        from omnicompany.core.config import omni_workspace_root
        return omni_workspace_root()
    except Exception:
        return Path(__file__).resolve().parents[4]


def _meta_store_path() -> Path:
    state_dir = os.environ.get("OMNI_CC_DAEMON_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "cc_sessions.json"
    root = _repo_root()
    return root / "data" / "cc_sessions.json"


def _read_meta_store() -> dict[str, dict[str, Any]]:
    try:
        # cc_sessions.json is shared by the CLI, dashboard daemon, hooks and
        # multiple browser-backed PTYs.  Reuse the daemon's cross-process lock
        # instead of briefly opening the replace target without FILE_SHARE_DELETE
        # on Windows (that used to make an unrelated writer lose its update).
        from omnicompany.dashboard.ccdaemon.api import read_meta_store as read_store

        return read_store(use_cache=False)
    except (ImportError, json.JSONDecodeError, OSError, ValueError) as exc:
        raise click.ClickException(f"cc_sessions.json read failed: {exc}") from exc


def _write_meta_store(store: dict[str, dict[str, Any]]) -> None:
    try:
        # ``store`` is an entry patch, not a stale full-file replacement.  The
        # shared writer re-reads under the same inter-process lock, merges the
        # patch, writes through a unique temp file and retries Windows replace
        # contention.  A failed registry update must fail the CLI command rather
        # than print success while silently dropping the session metadata.
        from omnicompany.dashboard.ccdaemon.api import write_meta_store as write_store

        write_store(store)
    except (ImportError, OSError, ValueError) as exc:
        raise click.ClickException(f"cc_sessions.json write failed: {exc}") from exc


def _safe_echo(text: str) -> None:
    try:
        click.echo(text)
    except UnicodeEncodeError:
        click.echo(text.encode("gbk", errors="replace").decode("gbk"))


def _ts_str(ts: float | str | None) -> str:
    if ts is None:
        return "-"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        return str(ts)


def _truncate(text: str, max_len: int = 80) -> str:
    text = text.replace("\n", " ").strip()
    return text[:max_len] + "..." if len(text) > max_len else text


def _session_display_name(entry: dict[str, Any]) -> str:
    # `name` is the explicit user override; provider/summary titles are the
    # durable titles populated by the PTY daemon for CLI sessions.
    for key in ("name", "display_title", "summary_title", "provider_title"):
        name = entry.get(key) or ""
        if name:
            return str(name)
    sid = entry.get("id") or ""
    return sid[-12:] if len(sid) > 12 else sid


def _live_session_meta() -> dict[str, dict[str, Any]]:
    """Read the daemon's current observation, with durable history as fallback.

    ``cc_sessions.json`` is a resumable history registry, not a process table:
    an un-ended row may be disconnected or have been restored without its
    provider runtime.  The two live endpoints are intentionally read
    separately so the CLI can reconcile Omni PTYs, the LiteWork bridge and the
    legacy structured-chat adapter without counting them as one undifferentiated
    ``alive`` population.
    """
    try:
        from omnicompany.dashboard.ccdaemon.api import lifecycle

        status = lifecycle.read_status()
        if not getattr(status, "alive", False) or not getattr(status, "port", None):
            return {}
        live: dict[str, dict[str, Any]] = {}
        for endpoint in (
            "/cc/sessions?include_recoverable=false",
            "/cc/chat/sessions?limit=200&include_archived=true",
        ):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{status.port}{endpoint}", timeout=2
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                items = payload.get("items") or payload.get("sessions") or []
                for item in items:
                    if isinstance(item, dict) and item.get("id"):
                        live[str(item["id"])] = item
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return live
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


_LIVE_RUNTIME_FIELDS = (
    "origin", "lifecycle_owner", "creation_origin", "visibility",
    "kind", "provider", "started_at", "ended_at", "logical_alive", "alive",
    "runtime_alive", "process_alive", "processAlive", "running", "working",
    "working_known", "state", "state_evidence_at", "observed_at", "observedAt",
    "source", "session_id", "pty_id", "visible", "last_activity_at", "lastActivityAt",
    "provider_session_id", "providerSessionId", "active_plan", "workstream", "topic",
    "display_title", "summary_title", "provider_title",
)


def _with_live_runtime(entry: dict[str, Any], live: dict[str, dict[str, Any]]) -> dict[str, Any]:
    current = live.get(str(entry.get("id") or ""))
    if not current:
        return entry
    merged = dict(entry)
    for key in _LIVE_RUNTIME_FIELDS:
        if key in current and current[key] is not None:
            merged[key] = current[key]
    return merged


def _with_live_title(entry: dict[str, Any], live: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return _with_live_runtime(entry, live)


def _runtime_status(entry: dict[str, Any]) -> str:
    """Return an observation status, never inferring liveness from ended_at alone."""
    if entry.get("ended_at") is not None:
        return "ended"
    state = str(entry.get("state") or "").strip().lower()
    if state in {"working", "idle", "waiting", "unknown"}:
        return state
    if entry.get("process_alive") or entry.get("runtime_alive"):
        return "working" if entry.get("working") else "idle"
    return "unknown"


def _runtime_source(entry: dict[str, Any]) -> str:
    return str(entry.get("source") or "durable-session-store")


def _current_view_eligible(entry: dict[str, Any]) -> bool:
    """Whether a live endpoint row belongs in the default current-session view.

    The structured-chat adapter exposes resumable logical records through its
    list endpoint even after the provider runtime is gone.  They remain
    queryable history, but are not current user work.  PTY and LiteWork rows
    retain their separate observation source/status contract, including
    unknown state when that is all the runtime can prove.
    """
    if str(entry.get("kind") or "") != "chat":
        return True
    return bool(entry.get("runtime_alive") or entry.get("process_alive"))


def _first_user_message(entry: dict[str, Any]) -> str:
    """Extract first user message from event_history or history_summary."""
    for msg in entry.get("event_history") or []:
        if msg.get("kind") == "text" and msg.get("role") == "user":
            return str(msg.get("content") or "")
        if msg.get("kind") == "text" and "user" in str(msg.get("id", "")):
            return str(msg.get("content") or "")
    for msg in entry.get("history_summary") or []:
        if msg.get("role") == "user":
            return str(msg.get("text") or "")
    return ""


# ── Claude Code JSONL database helpers ───────────────────────────────────────


def _cc_projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _encode_cwd(cwd: str) -> str:
    """Encode CWD the same way Claude Code does for project directory names."""
    return cwd.replace(":", "--").replace("\\", "-").replace("/", "-")


def _list_cc_project_dirs() -> list[tuple[str, Path]]:
    """List all Claude Code project directories as (encoded_name, path)."""
    root = _cc_projects_root()
    if not root.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for entry in root.iterdir():
        if entry.is_dir() and not entry.name.startswith("."):
            out.append((entry.name, entry))
    return sorted(out)


def _list_cc_jsonl_files(project_dir: Path) -> list[tuple[str, Path]]:
    """List JSONL conversation files in a project directory as (session_id, path)."""
    if not project_dir.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for f in project_dir.iterdir():
        if f.suffix == ".jsonl" and f.is_file():
            out.append((f.stem, f))
    return sorted(out, key=lambda x: x[1].stat().st_mtime, reverse=True)


def _read_jsonl_summary(path: Path, max_lines: int = 5000) -> list[dict[str, Any]]:
    """Read a JSONL file and return parsed lines."""
    if not path.is_file():
        return []
    lines: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return lines


def _read_jsonl_tail(path: Path, max_lines: int = 500, max_bytes: int = 8 * 1024 * 1024) -> list[dict[str, Any]]:
    """Read the newest JSONL records without loading an unbounded transcript."""
    if not path.is_file():
        return []
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            f.seek(max(0, end - max_bytes))
            payload = f.read()
    except OSError:
        return []

    raw_lines = payload.splitlines()
    if end > max_bytes and raw_lines:
        raw_lines = raw_lines[1:]
    records: list[dict[str, Any]] = []
    for raw in raw_lines[-max_lines:]:
        try:
            record = json.loads(raw.decode("utf-8", errors="replace"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _json_value_text(value: Any) -> str:
    """Extract displayable text from provider-specific content blocks."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_json_value_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "thinking", "content", "message", "output", "result"):
            if key in value:
                text = _json_value_text(value[key])
                if text:
                    return text
    return ""


def _source_record_messages(record: dict[str, Any], source: Path) -> list[dict[str, Any]]:
    """Normalize a Claude/Qoder-like JSONL record for ``chat show``."""
    record_type = str(record.get("type") or "")
    timestamp = str(record.get("timestamp") or "")
    if record_type in {"user", "assistant"}:
        message = record.get("message")
        message = message if isinstance(message, dict) else {}
        content = message.get("content", record.get("content"))
        role = str(message.get("role") or record_type)
        is_meta = bool(record.get("isMeta"))
        text = _json_value_text(content)
        if not text:
            return []
        return [{
            "kind": "context_event" if is_meta else "text",
            "role": "system" if is_meta else role,
            "content": text,
            "timestamp": timestamp,
            "source": str(source),
        }]

    if record_type == "system":
        text = str(record.get("content") or "")
        if not text:
            return []
        return [{
            "kind": "error" if record.get("level") == "error" or record.get("subtype") == "api_retry" else "context_event",
            "role": "system",
            "content": text,
            "timestamp": timestamp,
            "source": str(source),
        }]

    if record_type == "last-prompt":
        text = str(record.get("lastPrompt") or "")
        if not text:
            return []
        return [{
            "kind": "context_event",
            "role": "system",
            "content": f"last prompt: {text}",
            "timestamp": timestamp,
            "source": str(source),
        }]
    return []


def _source_messages(path: Path, *, tail: int = 500) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for record in _read_jsonl_tail(path, max_lines=tail):
        messages.extend(_source_record_messages(record, path))
    return messages


def _normalize_match_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _qoder_source_path(entry: dict[str, Any]) -> Path | None:
    """Resolve Qoder transcripts, including old PTY/native-id mismatches.

    The PTY registry stores a provider runtime id, while Qoder's durable
    transcript uses its own project/session stem.  Prefer an exact stem, then
    correlate the small transcript header by workspace, title/first prompt and
    start time.  This avoids guessing from a global "latest file".
    """
    root = Path.home() / ".qoder" / "projects"
    if not root.is_dir():
        return None
    ids = [
        str(entry.get(key) or "").strip()
        for key in ("provider_session_id", "claude_session_id", "id")
    ]
    ids = [value for value in ids if value]
    files = [path for path in root.rglob("*.jsonl") if path.is_file()]
    for native_id in ids:
        exact = [path for path in files if path.stem == native_id]
        if exact:
            return max(exact, key=lambda path: path.stat().st_mtime)

    expected_cwd = _normalize_match_text(entry.get("cwd"))
    expected_title = _normalize_match_text(
        entry.get("provider_title")
        or entry.get("summary_title")
        or entry.get("display_title")
        or entry.get("name")
    )
    try:
        expected_started = float(entry.get("started_at") or 0)
    except (TypeError, ValueError):
        expected_started = 0.0

    ranked: list[tuple[int, float, Path]] = []
    for path in files:
        header = _read_jsonl_summary(path, max_lines=120)
        if not header:
            continue
        cwd = ""
        first_user = ""
        first_timestamp = 0.0
        for record in header:
            if record.get("type") == "workspace-directories":
                directories = record.get("directories")
                if isinstance(directories, list) and directories:
                    cwd = str(directories[0])
                elif isinstance(directories, str):
                    cwd = directories
            if not first_user and record.get("type") == "user":
                message = record.get("message") if isinstance(record.get("message"), dict) else {}
                first_user = _json_value_text(message.get("content", record.get("content")))
            if not first_timestamp and record.get("timestamp"):
                raw_timestamp = record.get("timestamp")
                try:
                    first_timestamp = float(raw_timestamp) / 1000.0
                except (TypeError, ValueError):
                    try:
                        first_timestamp = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00")).timestamp()
                    except (TypeError, ValueError, OSError):
                        first_timestamp = 0.0

        score = 0
        if expected_cwd and _normalize_match_text(cwd) == expected_cwd:
            score += 40
        if expected_title and (
            expected_title == _normalize_match_text(first_user)
            or expected_title in _normalize_match_text(first_user)
            or _normalize_match_text(first_user) in expected_title
        ):
            score += 80
        if expected_started and first_timestamp:
            distance = abs(first_timestamp - expected_started)
            if distance <= 120:
                score += 60
            elif distance <= 900:
                score += 20
        if score >= 60:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            ranked.append((score, mtime, path))

    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0] and ranked[0][0] < 100:
        return None
    return ranked[0][2]


def _session_source_path(entry: dict[str, Any]) -> Path | None:
    """Find a durable transcript for any session kind supported by OmniChat."""
    provider = str(entry.get("provider") or "").strip().lower()
    ids = [
        str(entry.get(key) or "").strip()
        for key in ("provider_session_id", "claude_session_id", "id")
    ]
    ids = [value for value in ids if value]
    if provider == "qoder":
        return _qoder_source_path(entry)
    if provider == "claude_code":
        root = _cc_projects_root()
    elif provider == "codebuddy":
        root = Path.home() / ".codebuddy" / "projects"
    else:
        return None
    if not root.is_dir():
        return None
    for native_id in ids:
        matches = [path for path in root.rglob(f"{native_id}.jsonl") if path.is_file()]
        if matches:
            return max(matches, key=lambda path: path.stat().st_mtime)
    return None


def _source_entry_fields(entry: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    source = _session_source_path(entry)
    if source is None:
        return entry, None
    enriched = dict(entry)
    enriched["conversation_source"] = str(source)
    enriched["conversation_source_session_id"] = source.stem
    return enriched, source


def _jsonl_first_user_content(records: list[dict[str, Any]]) -> str:
    """Extract first user message content from JSONL records."""
    for rec in records:
        if rec.get("type") == "user":
            msg = rec.get("message", {})
            if isinstance(msg, dict):
                for block in msg.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        return str(block.get("text") or "")
            content = rec.get("content")
            if isinstance(content, str):
                return content
        if rec.get("type") == "queue-operation" and rec.get("operation") == "enqueue":
            content = rec.get("content")
            if isinstance(content, str):
                return content
    return ""


# ── command group ────────────────────────────────────────────────────────────


@click.group("chat")
def cmd_chat() -> None:
    """OmniChat session 管理 — 查/切/搜/改 chat session + 原始对话数据库.

    子命令:
      id         显当前 session id
      list       列所有 session
      show       查看 session 对话内容
      search     搜索 session 内容
      name       查看/设置 session 名称
      plan       管理 session 的 plan 绑定
      db         浏览 Claude Code / Codex 原始对话数据库
    """


# ── chat id ──────────────────────────────────────────────────────────────────


@cmd_chat.command("id")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_chat_id(as_json: bool) -> None:
    """显当前 session id (来自环境变量或 active session 文件)."""
    from omnicompany.packages.services._core.identity import current_session_meta

    meta = current_session_meta()
    if as_json:
        click.echo(json.dumps({
            "trace_id": meta["trace_id"],
            "claude_session_id": meta.get("claude_session_id"),
            "pty_id": meta.get("pty_id"),
            "source": meta["source"],
        }, ensure_ascii=False, indent=2))
        return
    click.echo(meta["trace_id"])


# ── chat list ────────────────────────────────────────────────────────────────


@cmd_chat.command("list")
@click.option("--regex", "-r", "pattern", default=None, help="正则过滤 (匹配 id / name / plan / provider)")
@click.option("--all", "show_all", is_flag=True, help="包含全部持久历史（默认只显示当前观测）")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
@click.option("--limit", "-n", type=int, default=50, help="最大条数 (默认 50)")
def cmd_chat_list(pattern: str | None, show_all: bool, as_json: bool, limit: int) -> None:
    """列所有 OmniChat session (dashboard cc_sessions.json).

    默认只显示 daemon 当前观测到的 session, --all 显示全部持久历史.
    status/alive 取实时运行时证据；未结束但已断开/仅可恢复的历史会标为 unknown.
    --regex 对 id / name / active_plan / provider 做正则匹配.
    """
    store = _read_meta_store()
    live = _live_session_meta()
    if not store and not live:
        click.echo("(no sessions found)")
        return

    rx = re.compile(pattern, re.IGNORECASE) if pattern else None
    entries: dict[str, dict[str, Any]] = {
        str(entry.get("id") or sid): dict(entry)
        for sid, entry in store.items()
    }
    # Bridge/runtime rows are not guaranteed to have a durable cc_sessions.json
    # record.  Keep them in the same user-facing command, but only as explicit
    # live observations with their own source.
    for sid, current in live.items():
        entries.setdefault(sid, {"id": sid, **current})

    rows: list[dict[str, Any]] = []
    for sid, stored_entry in entries.items():
        entry = _with_live_title(stored_entry, live)
        current = live.get(str(entry.get("id") or sid))
        status = _runtime_status(entry)
        current_view_eligible = _current_view_eligible(entry)
        if not show_all and current is None:
            continue
        if not show_all and (status == "ended" or not current_view_eligible):
            continue
        if rx:
            searchable = " ".join(filter(None, [
                str(entry.get("id") or sid),
                _session_display_name(entry),
                str(entry.get("active_plan") or ""),
                str(entry.get("provider") or ""),
                str(entry.get("claude_session_id") or ""),
                str(entry.get("model") or ""),
            ]))
            if not rx.search(searchable):
                continue

        first_msg = _first_user_message(entry)
        logical_alive = entry.get("logical_alive")
        if logical_alive is None:
            logical_alive = entry.get("ended_at") is None
        process_alive = entry.get("process_alive")
        if process_alive is None:
            process_alive = entry.get("runtime_alive")
        rows.append({
            "id": entry.get("id") or sid,
            "kind": entry.get("kind") or "pty",
            "name": _session_display_name(entry),
            "provider": entry.get("provider") or "-",
            "active_plan": entry.get("active_plan") or "",
            "started_at": entry.get("started_at"),
            "ended_at": entry.get("ended_at"),
            "status": status,
            "alive": status in {"working", "idle"},
            "logical_alive": bool(logical_alive),
            "runtime_alive": bool(entry.get("runtime_alive", process_alive)),
            "process_alive": bool(process_alive),
            "working": bool(entry.get("working")),
            "working_known": bool(entry.get("working_known")),
            "state": entry.get("state") or status,
            "observed_at": entry.get("observed_at") or entry.get("observedAt"),
            "source": _runtime_source(entry),
            "current_view": "current" if current_view_eligible else "logical-history",
            "model": entry.get("model") or "-",
            "claude_session_id": entry.get("claude_session_id") or "",
            "first_message": _truncate(first_msg, 60),
            "message_count": len(entry.get("event_history") or entry.get("history_summary") or []),
        })

    rows.sort(
        key=lambda r: float(r.get("observed_at") or r.get("started_at") or 0),
        reverse=True,
    )
    rows = rows[:limit]

    if as_json:
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    if not rows:
        click.echo("(no sessions match)")
        return

    _safe_echo(f"{'status':7s}  {'kind':5s}  {'id':20s}  {'name':16s}  {'plan':36s}  {'started':19s}  first_msg")
    _safe_echo("-" * 140)
    for r in rows:
        sid_short = r["id"][-16:] if len(r["id"]) > 16 else r["id"]
        name_short = _truncate(r["name"], 14) if r["name"] else "-"
        plan_short = _truncate(r["active_plan"], 34) if r["active_plan"] else "-"
        _safe_echo(
            f"{r['status']:7s}  {r['kind']:5s}  {sid_short:20s}  {name_short:16s}  "
            f"{plan_short:36s}  {_ts_str(r['started_at']):19s}  "
            f"{_truncate(r['first_message'], 40)}"
        )
    _safe_echo(f"\n({len(rows)} sessions shown)")


# ── chat show ────────────────────────────────────────────────────────────────


@cmd_chat.command("show")
@click.argument("session_id")
@click.option("--regex", "-r", "pattern", default=None, help="正则过滤消息内容")
@click.option("--limit", "-n", type=int, default=100, help="最大消息条数 (默认 100)")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
@click.option("--meta", "meta_only", is_flag=True, help="只显示元数据不显示消息")
def cmd_chat_show(session_id: str, pattern: str | None, limit: int, as_json: bool, meta_only: bool) -> None:
    """查看指定 session 的对话内容.

    session_id 支持部分匹配 (后缀匹配).
    """
    store = _read_meta_store()
    entry = _find_session(store, session_id)
    if not entry:
        click.echo(f"ERROR: no session matched {session_id!r}", err=True)
        click.echo("Use `omni chat list` to browse sessions.", err=True)
        sys.exit(2)
    entry = _with_live_title(entry, _live_session_meta())
    entry, source_path = _source_entry_fields(entry)

    if as_json and meta_only:
        click.echo(json.dumps(entry, ensure_ascii=False, indent=2))
        return

    # show metadata header
    _safe_echo(f"session_id       : {entry.get('id')}")
    _safe_echo(f"kind             : {entry.get('kind') or 'pty'}")
    _safe_echo(f"name             : {_session_display_name(entry)}")
    _safe_echo(f"provider_title   : {entry.get('provider_title') or '-'}")
    _safe_echo(f"summary_title    : {entry.get('summary_title') or '-'}")
    _safe_echo(f"display_title    : {entry.get('display_title') or '-'}")
    _safe_echo(f"provider         : {entry.get('provider') or '-'}")
    _safe_echo(f"model            : {entry.get('model') or '-'}")
    _safe_echo(f"active_plan      : {entry.get('active_plan') or '-'}")
    _safe_echo(f"claude_session_id: {entry.get('claude_session_id') or '-'}")
    _safe_echo(f"started_at       : {_ts_str(entry.get('started_at'))}")
    _safe_echo(f"ended_at         : {_ts_str(entry.get('ended_at'))}")
    _safe_echo(f"status           : {_runtime_status(entry)}")
    _safe_echo(f"process_alive    : {bool(entry.get('process_alive', entry.get('runtime_alive')))}")
    _safe_echo(f"working          : {bool(entry.get('working'))}")
    _safe_echo(f"observation_source: {_runtime_source(entry)}")
    _safe_echo(f"conversation_source: {source_path or '-'}")

    if meta_only:
        return

    # show messages
    rx = re.compile(pattern, re.IGNORECASE) if pattern else None
    messages = entry.get("event_history") or []
    source_messages = _source_messages(source_path) if source_path else []
    if source_messages and entry.get("kind") == "pty":
        # PTY registry history is a compact UI buffer and is often empty (or
        # stale after a restart).  The provider transcript is authoritative.
        messages = source_messages
    elif not messages:
        messages = [{"kind": "text", "role": m.get("role", "?"), "content": m.get("text", "")}
                    for m in (entry.get("history_summary") or [])]
    if not messages and source_messages:
        messages = source_messages

    _safe_echo(f"\n--- messages ({len(messages)} total) ---\n")
    filtered_messages: list[dict[str, Any]] = []
    for msg in messages:
        kind = msg.get("kind", "?")
        if kind not in ("text", "thinking", "tool_use", "tool_result", "error", "context_event"):
            continue
        content = str(msg.get("content") or msg.get("text") or "")
        if rx and not rx.search(content):
            continue
        filtered_messages.append(msg)

    omitted = max(0, len(filtered_messages) - limit)
    for msg in filtered_messages[-limit:]:
        kind = msg.get("kind", "?")
        content = str(msg.get("content") or msg.get("text") or "")

        role = msg.get("role", "")
        ts = msg.get("timestamp", "")

        if as_json:
            click.echo(json.dumps(msg, ensure_ascii=False))
        else:
            label = f"[{kind}]"
            if role:
                label = f"[{role}/{kind}]"
            if kind == "tool_use":
                tool_name = msg.get("toolName") or msg.get("name") or "?"
                content = f"{tool_name}: {_truncate(content, 200)}"
            elif kind == "tool_result":
                content = _truncate(content, 200)
            elif kind == "thinking":
                content = _truncate(content, 200)
            elif kind == "context_event":
                summary = msg.get("summary") or msg.get("status") or ""
                content = _truncate(summary or content, 200)

            prefix = f"{ts[:19]:19s} " if ts else ""
            _safe_echo(f"  {prefix}{label:22s} {content}")

    if omitted:
        _safe_echo(f"\n  ... ({omitted} older messages omitted; showing latest {limit})")


# ── chat search ──────────────────────────────────────────────────────────────


@cmd_chat.command("search")
@click.argument("query")
@click.option("--regex", "-r", is_flag=True, help="把 query 当正则表达式")
@click.option("--limit", "-n", type=int, default=20, help="最大结果条数 (默认 20)")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_chat_search(query: str, regex: bool, limit: int, as_json: bool) -> None:
    """搜索所有 session 的对话内容.

    默认模糊匹配 (大小写不敏感), --regex 用正则.
    """
    store = _read_meta_store()
    if not store:
        click.echo("(no sessions found)")
        return

    rx = re.compile(query, re.IGNORECASE) if regex else None
    query_lower = query.lower()
    live = _live_session_meta()
    results: list[dict[str, Any]] = []

    for sid, entry in store.items():
        entry = _with_live_title(entry, live)
        session_id = entry.get("id") or sid
        matches: list[dict[str, Any]] = []

        for msg in entry.get("event_history") or []:
            if msg.get("kind") not in ("text", "thinking", "tool_use", "tool_result", "error"):
                continue
            content = str(msg.get("content") or "")
            if rx:
                if rx.search(content):
                    matches.append(msg)
                elif query_lower in content.lower():
                    matches.append(msg)

        if not matches and not (entry.get("event_history")):
            for msg in entry.get("history_summary") or []:
                text = str(msg.get("text") or "")
                if rx:
                    if rx.search(text):
                        matches.append({"kind": "text", "role": msg.get("role"), "content": text})
                elif query_lower in text.lower():
                    matches.append({"kind": "text", "role": msg.get("role"), "content": text})

        if not matches:
            source_path = _session_source_path(entry)
            source_messages = _source_messages(source_path, tail=1000) if source_path else []
            for msg in source_messages:
                content = str(msg.get("content") or "")
                if (rx and rx.search(content)) or (not rx and query_lower in content.lower()):
                    matches.append(msg)

        if matches:
            results.append({
                "session_id": session_id,
                "name": _session_display_name(entry),
                "active_plan": entry.get("active_plan") or "",
                "match_count": len(matches),
                "first_match": _truncate(str(matches[0].get("content") or ""), 120),
                "started_at": entry.get("started_at"),
            })

    results.sort(key=lambda r: r["match_count"], reverse=True)
    results = results[:limit]

    if as_json:
        click.echo(json.dumps(results, ensure_ascii=False, indent=2))
        return

    if not results:
        click.echo(f"(no matches for {query!r})")
        return

    _safe_echo(f"{'session_id':20s}  {'name':16s}  {'hits':5s}  first_match")
    _safe_echo("-" * 100)
    for r in results:
        sid_short = r["session_id"][-16:] if len(r["session_id"]) > 16 else r["session_id"]
        name = _truncate(r["name"], 14) if r["name"] else "-"
        _safe_echo(f"{sid_short:20s}  {name:16s}  {r['match_count']:5d}  {r['first_match']}")
    _safe_echo(f"\n({len(results)} sessions with matches)")


# ── chat name ────────────────────────────────────────────────────────────────


@cmd_chat.command("name")
@click.argument("session_id")
@click.argument("new_name", required=False, default=None)
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_chat_name(session_id: str, new_name: str | None, as_json: bool) -> None:
    """查看或设置 session 的备注名.

    不传 new_name 时显示当前名称; 传入时设置新名称.
    session_id 支持部分匹配 (后缀匹配).
    """
    store = _read_meta_store()
    matched_key = _find_session_key(store, session_id)
    if not matched_key:
        click.echo(f"ERROR: no session matched {session_id!r}", err=True)
        sys.exit(2)

    entry = store[matched_key]

    if new_name is None:
        # get mode
        current = entry.get("name") or ""
        if as_json:
            click.echo(json.dumps({"session_id": entry.get("id") or matched_key, "name": current}, ensure_ascii=False))
        else:
            _safe_echo(f"session  : {entry.get('id') or matched_key}")
            _safe_echo(f"name     : {current or '(unnamed)'}")
        return

    # set mode
    entry["name"] = new_name
    _write_meta_store({matched_key: {"name": new_name}})

    if as_json:
        click.echo(json.dumps({"session_id": entry.get("id") or matched_key, "name": new_name}, ensure_ascii=False))
    else:
        _safe_echo(f"OK name = {new_name!r} (session {entry.get('id') or matched_key})")


# ── chat plan ────────────────────────────────────────────────────────────────


@cmd_chat.group("plan")
def cmd_chat_plan() -> None:
    """管理 session 的 plan 绑定.

    子命令:
      use     将 plan 绑定到指定 session
      show    查看 session 绑定的 plan
      list    列所有 session 及其 plan 绑定
    """


@cmd_chat_plan.command("use")
@click.argument("plan_query")
@click.option("--session", "-s", "session_id", default=None,
              help="目标 session id (默认当前 session)")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_chat_plan_use(plan_query: str, session_id: str | None, as_json: bool) -> None:
    """将 plan 绑定到指定 chat session.

    plan_query 接受完整 id / 目录名 / 仅名称 (须全局唯一).
    默认绑定到当前 session, --session 指定其他 session.
    """
    from omnicompany.cli.commands.plan import _resolve_plan_query

    try:
        resolved = _resolve_plan_query(plan_query)
    except ValueError as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(2)
    if not resolved:
        click.echo(f"ERROR: no plan matched {plan_query!r}", err=True)
        sys.exit(2)

    plan_id, plan_dir = resolved

    if session_id:
        # update specific session in cc_sessions.json
        store = _read_meta_store()
        matched_key = _find_session_key(store, session_id)
        if not matched_key:
            click.echo(f"ERROR: no session matched {session_id!r}", err=True)
            sys.exit(2)
        changed_ts = time.time()
        store[matched_key]["active_plan"] = plan_id
        _write_meta_store({matched_key: {
            "active_plan": plan_id,
            "active_plan_changed_ts": changed_ts,
        }})
        actual_id = store[matched_key].get("id") or matched_key
        try:
            from omnicompany.core.plan_context_trigger import publish_plan_change
            from omnicompany.packages.services._core.identity import (
                bindings_by_session_key,
                update_session_binding,
            )

            binding = bindings_by_session_key().get(f"pty:{matched_key}") or {}
            trace_id = str(binding.get("trace_id") or matched_key)
            update_session_binding(
                trace_id,
                pty_id=matched_key,
                active_plan=plan_id,
                active_plan_changed_ts=changed_ts,
                source="cli_chat_plan_use",
            )
            publish_plan_change(matched_key, plan_id)
        except OSError as exc:
            click.echo(f"WARN: plan context trigger update failed: {exc}", err=True)
    else:
        # update current session (same logic as omni plan use)
        from omnicompany.packages.services._core.identity import (
            current_session_meta,
            record_active_session,
        )

        meta = current_session_meta()
        record_active_session(
            trace_id=meta["trace_id"],
            claude_session_id=meta.get("claude_session_id"),
            pty_id=meta.get("pty_id"),
            active_plan=plan_id,
            cwd=meta.get("cwd") or os.getcwd(),
            source="cli_chat_plan_use",
        )
        actual_id = meta["trace_id"]

        # also push to cc_sessions.json if pty_id present
        pty_id = meta.get("pty_id")
        if pty_id:
            try:
                from omnicompany.dashboard.ccdaemon.api import update_meta_field
                update_meta_field(pty_id, active_plan=plan_id)
            except Exception as e:
                click.echo(f"WARN: pty meta update failed: {e}", err=True)

    if as_json:
        click.echo(json.dumps({"session_id": actual_id, "plan_id": plan_id}, ensure_ascii=False, indent=2))
    else:
        _safe_echo(f"OK plan bound: {plan_id}")
        _safe_echo(f"   session   : {actual_id}")
        _safe_echo(f"   plan_dir  : {plan_dir}")


@cmd_chat_plan.command("show")
@click.argument("session_id", required=False, default=None)
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_chat_plan_show(session_id: str | None, as_json: bool) -> None:
    """查看 session 绑定的 plan. 默认当前 session."""
    if session_id:
        store = _read_meta_store()
        entry = _find_session(store, session_id)
        if not entry:
            click.echo(f"ERROR: no session matched {session_id!r}", err=True)
            sys.exit(2)
        plan_id = entry.get("active_plan")
        sid = entry.get("id") or session_id
    else:
        from omnicompany.packages.services._core.identity import current_session_meta
        meta = current_session_meta()
        plan_id = meta.get("active_plan")
        sid = meta["trace_id"]

    if as_json:
        click.echo(json.dumps({"session_id": sid, "active_plan": plan_id}, ensure_ascii=False, indent=2))
        return

    _safe_echo(f"session      : {sid}")
    _safe_echo(f"active_plan  : {plan_id or '(none)'}")


@cmd_chat_plan.command("list")
@click.option("--regex", "-r", "pattern", default=None, help="正则过滤")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_chat_plan_list(pattern: str | None, as_json: bool) -> None:
    """列所有 session 及其 plan 绑定."""
    store = _read_meta_store()
    rx = re.compile(pattern, re.IGNORECASE) if pattern else None

    rows: list[dict[str, Any]] = []
    for sid, entry in store.items():
        plan = entry.get("active_plan") or ""
        session_id = entry.get("id") or sid
        name = entry.get("name") or ""
        if rx:
            searchable = f"{session_id} {name} {plan}"
            if not rx.search(searchable):
                continue
        rows.append({
            "session_id": session_id,
            "name": name,
            "active_plan": plan,
            "kind": entry.get("kind") or "pty",
            "status": "alive" if entry.get("ended_at") is None else "ended",
            "started_at": entry.get("started_at"),
        })

    rows.sort(key=lambda r: float(r.get("started_at") or 0), reverse=True)

    if as_json:
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    if not rows:
        click.echo("(no sessions)")
        return

    _safe_echo(f"{'status':7s}  {'id':20s}  {'name':16s}  active_plan")
    _safe_echo("-" * 100)
    for r in rows:
        sid_short = r["session_id"][-16:] if len(r["session_id"]) > 16 else r["session_id"]
        name = _truncate(r["name"], 14) if r["name"] else "-"
        plan = r["active_plan"] or "-"
        _safe_echo(f"{r['status']:7s}  {sid_short:20s}  {name:16s}  {plan}")


# ── chat db ──────────────────────────────────────────────────────────────────


@cmd_chat.group("db")
def cmd_chat_db() -> None:
    """浏览 Claude Code / Codex 原始对话数据库.

    Claude Code 原始对话存储在 ~/.claude/projects/<encoded_cwd>/<session_id>.jsonl.
    每个项目目录对应一个工作区, 内含所有 session 的 JSONL 对话记录.

    子命令:
      list     列项目目录及 JSONL 文件
      show     查看指定 JSONL 对话内容
      search   搜索原始对话数据库内容
    """


@cmd_chat_db.command("list")
@click.option("--project", "-p", default=None, help="指定项目目录名或正则过滤")
@click.option("--regex", "-r", "pattern", default=None, help="正则过滤 session id")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
@click.option("--limit", "-n", type=int, default=50, help="每个项目最大文件数 (默认 50)")
def cmd_chat_db_list(project: str | None, pattern: str | None, as_json: bool, limit: int) -> None:
    """列 Claude Code 原始对话 JSONL 文件.

    不传 --project 时列所有项目; 传入时只列该项目下的 session 文件.
    """
    dirs = _list_cc_project_dirs()
    if not dirs:
        click.echo("(no Claude Code project directories found)")
        return

    project_rx = re.compile(project, re.IGNORECASE) if project else None
    session_rx = re.compile(pattern, re.IGNORECASE) if pattern else None

    if as_json:
        result: list[dict[str, Any]] = []

    if not project and not pattern:
        # list project dirs only
        if as_json:
            for name, path in dirs:
                if project_rx and not project_rx.search(name):
                    continue
                files = _list_cc_jsonl_files(path)
                result.append({
                    "project": name,
                    "path": str(path),
                    "session_count": len(files),
                })
            click.echo(json.dumps(result, ensure_ascii=False, indent=2))
            return

        _safe_echo(f"{'sessions':10s}  project")
        _safe_echo("-" * 80)
        for name, path in dirs:
            if project_rx and not project_rx.search(name):
                continue
            count = len(_list_cc_jsonl_files(path))
            _safe_echo(f"{count:10d}  {name}")
        return

    # list sessions in specific project(s)
    for name, path in dirs:
        if project_rx and not project_rx.search(name):
            continue
        if project and not project_rx and project != name:
            continue

        files = _list_cc_jsonl_files(path)
        if session_rx:
            files = [(sid, p) for sid, p in files if session_rx.search(sid)]
        files = files[:limit]

        if as_json:
            for sid, fpath in files:
                stat = fpath.stat()
                result.append({
                    "project": name,
                    "session_id": sid,
                    "path": str(fpath),
                    "size_kb": round(stat.st_size / 1024, 1),
                    "modified": _ts_str(stat.st_mtime),
                })
            continue

        if files:
            _safe_echo(f"\n=== {name} ({len(files)} sessions) ===")
            _safe_echo(f"  {'session_id':40s}  {'size':8s}  modified")
            for sid, fpath in files:
                stat = fpath.stat()
                _safe_echo(f"  {sid:40s}  {stat.st_size / 1024:7.1f}K  {_ts_str(stat.st_mtime)}")

    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@cmd_chat_db.command("show")
@click.argument("session_ref")
@click.option("--project", "-p", default=None, help="项目目录名 (省略时在当前项目找)")
@click.option("--regex", "-r", "pattern", default=None, help="正则过滤消息内容")
@click.option("--limit", "-n", type=int, default=200, help="最大消息条数 (默认 200)")
@click.option("--types", "-t", default=None, help="消息类型过滤 (逗号分隔, 如 user,assistant)")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出 (每行一个 JSON)")
def cmd_chat_db_show(
    session_ref: str,
    project: str | None,
    pattern: str | None,
    limit: int,
    types: str | None,
    as_json: bool,
) -> None:
    """查看指定 Claude Code JSONL 对话原始内容.

    session_ref 接受:
      - 完整 session UUID (如 0053b1ba-9e04-4e7d-9219-7d4f26d9115b)
      - 部分 UUID (后缀匹配)
      - 完整文件路径
    """
    path = _resolve_jsonl_path(session_ref, project)
    if not path:
        click.echo(f"ERROR: no JSONL file matched {session_ref!r}", err=True)
        click.echo("Use `omni chat db list --project <name>` to browse.", err=True)
        sys.exit(2)

    records = _read_jsonl_summary(path, max_lines=10000)
    rx = re.compile(pattern, re.IGNORECASE) if pattern else None
    type_set = set(types.split(",")) if types else None

    _safe_echo(f"file: {path}")
    _safe_echo(f"total records: {len(records)}")
    if not as_json:
        _safe_echo("")

    shown = 0
    for rec in records:
        rec_type = rec.get("type", "?")
        if type_set and rec_type not in type_set:
            continue

        # build content string for regex matching
        content = ""
        if rec_type in ("user", "assistant"):
            msg = rec.get("message", {})
            if isinstance(msg, dict):
                for block in msg.get("content", []):
                    if isinstance(block, dict):
                        content += block.get("text") or block.get("thinking") or ""
            if not content:
                content = str(rec.get("content") or "")
        elif rec_type == "queue-operation":
            content = str(rec.get("content") or "")
        else:
            content = json.dumps(rec, ensure_ascii=False)

        if rx and not rx.search(content):
            continue

        if as_json:
            click.echo(json.dumps(rec, ensure_ascii=False))
        else:
            ts = rec.get("timestamp", "")[:19]
            if rec_type in ("user", "assistant"):
                _safe_echo(f"  {ts}  [{rec_type:12s}]  {_truncate(content, 160)}")
            elif rec_type == "queue-operation":
                op = rec.get("operation", "?")
                _safe_echo(f"  {ts}  [queue/{op:7s}]  {_truncate(content, 120)}")
            elif rec_type == "progress":
                _safe_echo(f"  {ts}  [progress     ]  {_truncate(content, 120)}")
            else:
                _safe_echo(f"  {ts}  [{rec_type:12s}]  {_truncate(content, 120)}")

        shown += 1
        if shown >= limit:
            _safe_echo(f"\n  ... (truncated at {limit}, use --limit to show more)")
            break

    if not as_json:
        _safe_echo(f"\n({shown} records shown)")


@cmd_chat_db.command("search")
@click.argument("query")
@click.option("--project", "-p", default=None, help="限定项目目录 (正则)")
@click.option("--regex", "-r", is_flag=True, help="把 query 当正则表达式")
@click.option("--limit", "-n", type=int, default=20, help="最大结果 session 数 (默认 20)")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_chat_db_search(query: str, project: str | None, regex: bool, limit: int, as_json: bool) -> None:
    """搜索 Claude Code 原始对话数据库内容.

    遍历所有项目目录下的 JSONL 文件, 搜索消息内容.
    """
    dirs = _list_cc_project_dirs()
    project_rx = re.compile(project, re.IGNORECASE) if project else None
    rx = re.compile(query, re.IGNORECASE) if regex else None
    query_lower = query.lower()

    results: list[dict[str, Any]] = []

    for proj_name, proj_path in dirs:
        if project_rx and not project_rx.search(proj_name):
            continue

        files = _list_cc_jsonl_files(proj_path)
        for sid, fpath in files:
            records = _read_jsonl_summary(fpath, max_lines=5000)
            match_count = 0
            first_match = ""

            for rec in records:
                content = ""
                rec_type = rec.get("type", "")
                if rec_type in ("user", "assistant"):
                    msg = rec.get("message", {})
                    if isinstance(msg, dict):
                        for block in msg.get("content", []):
                            if isinstance(block, dict):
                                content += block.get("text") or block.get("thinking") or ""
                    if not content:
                        content = str(rec.get("content") or "")
                elif rec_type == "queue-operation":
                    content = str(rec.get("content") or "")
                else:
                    continue

                matched = False
                if rx:
                    matched = bool(rx.search(content))
                else:
                    matched = query_lower in content.lower()

                if matched:
                    match_count += 1
                    if not first_match:
                        first_match = _truncate(content, 120)

            if match_count > 0:
                results.append({
                    "project": proj_name,
                    "session_id": sid,
                    "path": str(fpath),
                    "match_count": match_count,
                    "first_match": first_match,
                    "modified": _ts_str(fpath.stat().st_mtime),
                })

            if len(results) >= limit:
                break
        if len(results) >= limit:
            break

    results.sort(key=lambda r: r["match_count"], reverse=True)

    if as_json:
        click.echo(json.dumps(results, ensure_ascii=False, indent=2))
        return

    if not results:
        click.echo(f"(no matches for {query!r} in Claude Code database)")
        return

    _safe_echo(f"{'project':40s}  {'session_id':38s}  {'hits':5s}  first_match")
    _safe_echo("-" * 140)
    for r in results:
        proj = _truncate(r["project"], 38)
        _safe_echo(f"{proj:40s}  {r['session_id'][:36]:38s}  {r['match_count']:5d}  {r['first_match']}")
    _safe_echo(f"\n({len(results)} sessions with matches)")


# ── session resolution helpers ───────────────────────────────────────────────


def _find_session_key(store: dict[str, dict[str, Any]], query: str) -> str | None:
    """Find session key in store by exact match or suffix match."""
    if query in store:
        return query

    # match by entry.id
    for key, entry in store.items():
        if entry.get("id") == query:
            return key

    # suffix match on key
    matches = [k for k in store if k.endswith(query)]
    if len(matches) == 1:
        return matches[0]

    # suffix match on entry.id
    matches = [k for k, e in store.items() if str(e.get("id") or "").endswith(query)]
    if len(matches) == 1:
        return matches[0]

    return None


def _find_session(store: dict[str, dict[str, Any]], query: str) -> dict[str, Any] | None:
    """Find session entry by query."""
    key = _find_session_key(store, query)
    return store.get(key) if key else None


def _resolve_jsonl_path(ref: str, project: str | None) -> Path | None:
    """Resolve a session reference to a JSONL file path."""
    # direct file path
    p = Path(ref)
    if p.is_file() and p.suffix == ".jsonl":
        return p

    root = _cc_projects_root()
    if not root.is_dir():
        return None

    # determine project dirs to search
    if project:
        search_dirs = [(project, root / project)]
        if not (root / project).is_dir():
            # try regex match
            for name, path in _list_cc_project_dirs():
                if re.search(project, name, re.IGNORECASE):
                    search_dirs = [(name, path)]
                    break
    else:
        # default: current project first, then all
        cwd_encoded = _encode_cwd(os.getcwd())
        cwd_dir = root / cwd_encoded
        if cwd_dir.is_dir():
            search_dirs = [(cwd_encoded, cwd_dir)]
        else:
            search_dirs = _list_cc_project_dirs()

    for proj_name, proj_path in search_dirs:
        # exact match
        exact = proj_path / f"{ref}.jsonl"
        if exact.is_file():
            return exact

        # suffix match
        for sid, fpath in _list_cc_jsonl_files(proj_path):
            if sid.endswith(ref):
                return fpath

    # fallback: search all projects
    if project is None:
        for proj_name, proj_path in _list_cc_project_dirs():
            exact = proj_path / f"{ref}.jsonl"
            if exact.is_file():
                return exact
            for sid, fpath in _list_cc_jsonl_files(proj_path):
                if sid.endswith(ref):
                    return fpath

    return None
