# [OMNI] origin=claude-code ts=2026-05-01 type=infra
# [OMNI] material_id="material:dashboard.cc_wrapper.pty_manager.session_lifecycle.py"
"""PTY session manager for the Claude Code wrapper.

Each PtySession owns:
- one `winpty.PTY` child process (e.g. `claude.cmd`),
- a background asyncio task that polls the PTY non-blocking and fans chunks
  out to every attached WebSocket subscriber,
- a ring buffer so late-attaching / reconnecting clients can replay recent
  output (xterm.js needs the last screenful to repaint correctly).

Sessions are kept alive after the last subscriber detaches (so the user can
close a browser tab and keep the claude CLI running). They reap themselves
when the child exits OR after `IDLE_TTL_S` with zero subscribers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import socket
import subprocess
import sys
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator
import threading

from omnicompany.dashboard.session_workdir import resolve_session_cwd

logger = logging.getLogger(__name__)
_META_STORE_THREAD_LOCK = threading.RLock()
_META_STORE_CACHE_KEY: tuple[str, int, int, int] | None = None
_META_STORE_CACHE_VALUE: dict[str, dict[str, Any]] | None = None

# Session lives this long after both its last subscriber leaves and its output
# goes quiet. A closed dashboard tab is only a detach; overnight / cross-device
# work must remain resumable. Operators can shorten this explicitly if needed.
IDLE_TTL_S = float(os.environ.get("OMNI_PTY_IDLE_TTL_S") or 30 * 24 * 60 * 60)
# Do not trade away normal CLI usage for performance. Operators may opt into a
# host-specific cap through the environment, while the default remains
# unlimited; memory/CPU pressure is handled by bounded replay buffers, detached
# hosts and UI rendering limits instead of refusing new sessions.
MAX_LIVE_SESSIONS = max(0, int(os.environ.get("OMNI_PTY_MAX_LIVE_SESSIONS") or 0))
# Per-session output ring buffer cap (chunks, not bytes).
RING_CAP = int(os.environ.get("OMNI_PTY_PROXY_RING_CHUNKS") or 50_000)
RING_BYTE_CAP = int(os.environ.get("OMNI_PTY_PROXY_RING_BYTES") or 8 * 1024 * 1024)
REPLAY_TRUNCATION_MARGIN_BYTES = int(
    os.environ.get("OMNI_PTY_REPLAY_TRUNCATION_MARGIN_BYTES") or 64 * 1024
)
# How often the read loop polls when the PTY has no data.
# CC-PLAN-SESSION-CONTEXT 段五 (2026-05-05): 20ms → 2ms. baseline bench 显示
# 端到端 echo 延迟 p50 30.8ms, 主要是这里. 改 2ms 后 p50 < 10ms 预期.
# CPU 代价: 空轮询从 50/s → 500/s, 每次 to_thread + read syscall 仍可控.
# 2026-07 P0 性能批: 2ms → 10ms. 2ms 空轮询在 N 个空闲 PTY 会话下每秒数千次
# to_thread 切换, 持续抢占事件循环; echo 热路径有 READ_HOT_DRAIN_MAX 连读兜底,
# 10ms 对交互延迟无感 (人眼阈值 ~50ms), CPU 占用降 5 倍.
READ_IDLE_SLEEP_S = 0.01
# Reader hot-drain: 拿到非空 chunk 后, 不睡接连再 read 几次, 把已 buffer 的字节
# 一次取完, 一帧 fan out. 减 JSON 编码 + WS 帧次, 减重复 to_thread 切换.
READ_HOT_DRAIN_MAX = 4
CODEX_LINK_POLL_S = float(os.environ.get("OMNI_CODEX_LINK_POLL_S") or 2.0)

# 「正在产出」判定窗口: 最后输出距今 < N 秒即视为 working (TUI 工作时持续重绘,
# 回合一结束输出立即停止; 8s 容忍渲染间歇, 又快到用户可感知状态翻转).
PTY_WORKING_WINDOW_S = float(os.environ.get("OMNI_PTY_WORKING_WINDOW_S") or 8.0)
TEMPORARY_SESSION_TITLE_MAX_CHARS = 80
TEMPORARY_SESSION_TITLE_CAPTURE_MAX_CHARS = 400
# Keyboard escape sequences (arrows, function keys, bracketed-paste markers)
# are terminal navigation, not prompt text.  Strip them before deciding whether
# Enter submitted a real turn.
_TERMINAL_INPUT_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|.?)")
_TERMINAL_RESPONSE_RE = re.compile(
    r"(?:"
    r"\x1b\](?:10|11|12);rgb:[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}(?:\x07|\x1b\\)"
    r"|\x1b\[\?(?:[0-9;]+)c"
    r"|\x1b\[>(?:[0-9;]*)c"
    r"|\x1b\[(?:0n|[0-9]+;[0-9]+R)"
    r"|\x1b\[\?[0-9]+;[0-9]+\$y"
    r")+"
)
SNAPSHOT_PROTOCOL_VERSION = 2
# A user kill is a durable tombstone, not an invitation to recreate the
# provider conversation in a new CLI process. Keep legacy spellings filtered
# so old metadata cannot reappear after this fix ships.
_NON_RECOVERABLE_EXIT_REASONS = frozenset({"kill", "user-kill"})


def _is_explicit_kill_reason(reason: Any) -> bool:
    return str(reason or "").strip().lower() in _NON_RECOVERABLE_EXIT_REASONS


def _strip_browser_terminal_responses(data: str, pending: str = "") -> tuple[str, str]:
    """Strip complete replies and retain a split reply until its next frame."""
    value = _TERMINAL_RESPONSE_RE.sub("", pending + data)
    osc_prefixes = ("\x1b]10;rgb:", "\x1b]11;rgb:", "\x1b]12;rgb:")
    for start, char in enumerate(value):
        if char != "\x1b":
            continue
        candidate = value[start:]
        osc_partial = any(prefix.startswith(candidate) for prefix in osc_prefixes)
        osc_body = False
        for prefix in osc_prefixes:
            if not candidate.startswith(prefix) or len(candidate) > len(prefix) + 33:
                continue
            body = candidate[len(prefix):]
            if body.endswith("\x1b"):
                body = body[:-1]
            if all(item in "0123456789abcdefABCDEF/" for item in body):
                osc_body = True
                break
        csi_partial = bool(
            re.fullmatch(r"\x1b\[(?:\?|>)[0-9;]*\$?", candidate)
            or re.fullmatch(r"\x1b\[[0-9;]*", candidate)
        )
        if osc_partial or osc_body or csi_partial:
            return value[:start], candidate
    return value, ""
# Default terminal geometry.
DEFAULT_COLS = 120
DEFAULT_ROWS = 32


def _is_idle_reap_candidate(sess: "PtySession", now: float) -> bool:
    return bool(
        not sess.subscribers
        and sess.last_detach_at
        and (now - sess.last_detach_at) > IDLE_TTL_S
        and (now - sess.last_output_at) > IDLE_TTL_S
    )


def _meta_store_path() -> Path:
    """Where we persist session metadata so they survive backend restart.

    Schema: { "<pty_id>": { id, cmd, cwd, started_at, ended_at,
                            claude_session_id, active_plan, exit_reason } }
    """
    # repo_root / data / cc_sessions.json — same dir as ide_events.db
    state_dir = os.environ.get("OMNI_CC_DAEMON_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "cc_sessions.json"
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root() / "data" / "cc_sessions.json"


def _pty_host_dir() -> Path:
    return _meta_store_path().parent / "pty_hosts"


def _pty_host_state_path(sid: str) -> Path:
    return _pty_host_dir() / f"{sid}.json"


def _read_pty_host_state(sid: str) -> dict[str, Any]:
    host_dir = _pty_host_dir()
    candidates = sorted(host_dir.glob(f"{sid}.state.*.json"), reverse=True)
    candidates.append(_pty_host_state_path(sid))
    for path in candidates:
        try:
            value = json.loads(_read_text_shared_delete(path))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def _write_pty_host_generation_snapshot(
    sess: "PtySession",
    host_meta: dict[str, Any] | None = None,
) -> None:
    """Publish an immutable discovery checkpoint after an authenticated attach.

    This is also the rolling-upgrade bridge for hosts that were launched by the
    pre-v2 implementation. Their canonical ``<sid>.json`` is replace-in-place
    and therefore unsafe to read concurrently on Windows. Once a new daemon
    has authenticated to such a host, it has enough trusted connection state
    to publish a v2 immutable generation without asking the live host to
    restart. Every later daemon replacement can discover it without opening
    the legacy replace target.
    """
    meta = host_meta or {}
    host_dir = _pty_host_dir()
    host_dir.mkdir(parents=True, exist_ok=True)
    generation = time.time_ns()
    final_path = host_dir / f"{sess.id}.state.{generation:020d}.json"
    while final_path.exists():
        generation += 1
        final_path = host_dir / f"{sess.id}.state.{generation:020d}.json"
    tmp_path = final_path.with_name(
        f".{final_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    payload = {
        "schema": "omnicompany.pty-host.v2",
        "id": sess.id,
        "host_pid": sess.host_pid,
        "host_port": sess.host_port,
        "host_token": sess.host_token,
        "child_pid": sess.child_pid,
        "cmd": sess.cmd,
        "cwd": sess.cwd,
        "cols": sess.cols,
        "rows": sess.rows,
        "provider": sess.provider,
        "started_at": sess.started_at,
        "alive": True,
        "last_output_at": sess.last_output_at,
        "last_input_at": meta.get("last_input_at"),
        "buffered_chunks": len(sess.ring),
        "buffered_bytes": sess.ring_bytes,
        "replay_limit": int(meta.get("replay_limit") or RING_BYTE_CAP),
        "replay_truncated": sess.replay_truncated,
        "terminal_query_responder": sess.host_answers_queries,
        "exit_reason": None,
        "updated_at": time.time(),
        "migrated_by_daemon": True,
    }
    try:
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        # The destination is new and immutable, so this replace cannot contend
        # with a reader holding an older generation.
        os.replace(tmp_path, final_path)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.warning(
            "could not publish immutable host discovery generation for %s",
            sess.id,
            exc_info=True,
        )
        return

    generations = sorted(host_dir.glob(f"{sess.id}.state.*.json"), reverse=True)
    for stale in generations[4:]:
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def _open_text_shared_delete(path: Path) -> Iterator[Any]:
    """Open text without blocking an atomic replace on Windows.

    Python's normal Windows file open omits FILE_SHARE_DELETE. A concurrent
    ``os.replace`` from a live PTY host can therefore fail with WinError 5.
    State discovery is read-only and specifically designed to tolerate
    replacement, so opt into read/write/delete sharing for this narrow path.
    """
    if os.name != "nt":
        with path.open("r", encoding="utf-8") as stream:
            yield stream
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001 | 0x00000002 | 0x00000004,  # SHARE_READ | SHARE_WRITE | SHARE_DELETE
        None,
        3,  # OPEN_EXISTING
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise ctypes.WinError()

    fd: int | None = None
    raw_handle_owned = True
    try:
        fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY)
        raw_handle_owned = False
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = None  # fdopen owns and closes the Windows handle.
            yield stream
    finally:
        if fd is not None:
            os.close(fd)
        elif raw_handle_owned:
            ctypes.windll.kernel32.CloseHandle(handle)


def _read_text_shared_delete(path: Path) -> str:
    with _open_text_shared_delete(path) as stream:
        return stream.read()


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return bool(ok) and code.value == 259
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def _pty_host_alive(state: dict[str, Any]) -> bool:
    if not state.get("alive"):
        return False
    try:
        port = int(state.get("host_port") or 0)
        pid = int(state.get("host_pid") or 0)
    except (TypeError, ValueError):
        return False
    if not _pid_alive(pid) or port <= 0:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            return True
    except OSError:
        return False


def _replay_meta_is_truncated(meta: dict[str, Any] | None) -> bool:
    """Recognize an explicitly or almost-certainly truncated ANSI replay.

    Current hosts publish ``replay_truncated``. Older live hosts cannot be
    upgraded without ending their PTY, so infer truncation when their replay is
    within a small margin of the configured byte cap.
    """
    if not meta:
        return False
    if bool(meta.get("replay_truncated")):
        return True
    try:
        buffered = int(meta.get("buffered_bytes") or 0)
        replay_limit = int(meta.get("replay_limit") or RING_BYTE_CAP)
    except (TypeError, ValueError):
        return False
    margin = min(REPLAY_TRUNCATION_MARGIN_BYTES, max(1, replay_limit // 128))
    threshold = max(1, replay_limit - margin)
    return buffered >= threshold


@dataclass
class PtySession:
    id: str
    cmd: list[str]
    cwd: str
    cols: int
    rows: int
    started_at: float
    pty: Any = None
    subscribers: set[asyncio.Queue[Any]] = field(default_factory=set)
    ring: deque[str] = field(default_factory=lambda: deque(maxlen=RING_CAP))
    ring_bytes: int = field(default=0, repr=False)
    replay_truncated: bool = False
    reader_task: asyncio.Task | None = None
    provider_link_task: asyncio.Task | None = None
    last_detach_at: float = 0.0
    # Output activity is independent from browser attachment. A detached CLI may
    # still be doing useful work, so the reaper must wait until both the user and
    # the process have been quiet for the full TTL.
    last_output_at: float = field(default_factory=time.time)
    # A live CLI prints its welcome screen and repaints its prompt before the
    # user has asked it to do anything.  Keep submission state separately so
    # those startup bytes never masquerade as an active agent turn.
    last_submit_at: float = 0.0
    has_user_turn: bool = False
    pending_input_len: int = field(default=0, repr=False)
    # Keep only a bounded copy of the not-yet-submitted first prompt so Enter
    # can publish an immediate temporary title. The provider-native title and
    # cheap-model digest can replace it later.
    pending_title_input: str = field(default="", repr=False)
    # Only keep a possible CLI executable token until Enter. We never retain
    # the submitted shell command; non-matching text is discarded immediately.
    pending_command_token: str = field(default="", repr=False)
    pending_command_is_bare: bool = field(default=True, repr=False)
    provider: str = "shell"
    closed: bool = False
    child_pid: int | None = None
    host_pid: int | None = None
    host_port: int | None = None
    host_token: str | None = field(default=None, repr=False)
    host_writer: asyncio.StreamWriter | None = field(default=None, repr=False)
    host_reader_task: asyncio.Task | None = field(default=None, repr=False)
    input_controller: asyncio.Queue[Any] | None = field(default=None, repr=False)
    host_answers_queries: bool = field(default=False, repr=False)
    terminal_query_responder: Any = field(default=None, repr=False)
    terminal_response_tails: dict[int, str] = field(default_factory=dict, repr=False)
    host_connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    host_write_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    # Serializes replay capture/subscription with live-output fanout. Without
    # this, bytes arriving between those two operations could be absent from
    # both the replay sent to a browser and its live queue.
    stream_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    # Populated by SessionStart once the provider announces its durable
    # conversation id. ``claude_session_id`` remains for compatibility.
    provider_session_id: str | None = None
    provider_title: str | None = None
    summary_title: str | None = None
    claude_session_id: str | None = None
    active_plan: str | None = None
    codex_process_uuid: str | None = field(default=None, repr=False)
    codex_log_cursor: int = field(default=0, repr=False)
    codex_thread_candidates: dict[str, int] = field(default_factory=dict, repr=False)

    def to_meta(self) -> dict[str, Any]:
        # Hosted sessions are already verified during discovery/creation and
        # then tracked over the authenticated IPC stream. Do not reopen their
        # JSON discovery file on every UI poll: besides unnecessary disk I/O,
        # ordinary Windows readers can deny the host's atomic replace.
        alive = (
            bool(not self.closed and _pid_alive(self.host_pid))
            if self.host_port
            else bool(self.pty and self.pty.isalive())
        )
        # A PTY is working only after a non-empty user submission and while the
        # agent is still producing output.  Process startup, prompt repaint,
        # resize traffic and browser attachment are deliberately insufficient.
        working = bool(
            alive
            and self.has_user_turn
            and self.last_submit_at
            and self.last_output_at >= self.last_submit_at
            and (time.time() - self.last_output_at) < PTY_WORKING_WINDOW_S
        )
        return {
            "id": self.id,
            "cmd": self.cmd,
            "cwd": self.cwd,
            "cols": self.cols,
            "rows": self.rows,
            "started_at": self.started_at,
            "alive": alive,
            "working": working,
            "has_user_turn": self.has_user_turn,
            "last_submit_at": self.last_submit_at or None,
            "provider": self.provider,
            "child_pid": self.child_pid,
            "host_pid": self.host_pid,
            "host_port": self.host_port,
            "hosted": bool(self.host_port),
            "subscribers": len(self.subscribers),
            "buffered_chunks": len(self.ring),
            "buffered_bytes": self.ring_bytes,
            "replay_truncated": self.replay_truncated,
            "last_output_at": self.last_output_at,
            "provider_session_id": self.provider_session_id,
            "provider_title": self.provider_title,
            "summary_title": self.summary_title,
            "display_title": self.summary_title or self.provider_title,
            "claude_session_id": self.claude_session_id,
            "active_plan": self.active_plan,
            "status": "alive",
        }


def _append_proxy_output(sess: PtySession, chunk: str) -> None:
    """Append output with both chunk and byte bounds for multi-session uptime."""
    if sess.ring.maxlen and len(sess.ring) >= sess.ring.maxlen:
        removed = sess.ring.popleft()
        sess.ring_bytes -= len(removed.encode("utf-8", errors="replace"))
        sess.replay_truncated = True
    sess.ring.append(chunk)
    sess.ring_bytes += len(chunk.encode("utf-8", errors="replace"))
    while sess.ring and sess.ring_bytes > RING_BYTE_CAP:
        removed = sess.ring.popleft()
        sess.ring_bytes -= len(removed.encode("utf-8", errors="replace"))
        sess.replay_truncated = True


def _replace_proxy_replay(sess: PtySession, chunks: list[str]) -> list[str]:
    """Load a host snapshot into the byte-bounded browser replay window."""
    sess.ring.clear()
    sess.ring_bytes = 0
    sess.replay_truncated = False
    for chunk in chunks:
        _append_proxy_output(sess, chunk)
    return list(sess.ring)


def _provider_for_command_name(command: str) -> str | None:
    return {
        "claude": "claude_code",
        "codex": "codex",
        "kimi": "kimi",
        "kimi-code": "kimi",
        "opencode": "opencode",
        "codebuddy": "codebuddy",
        "cbc": "codebuddy",
        "powershell": "shell",
        "pwsh": "shell",
        "cmd": "shell",
    }.get(_command_name(command))


def _pending_launch_provider(sess: PtySession, data: str) -> str | None:
    """Detect a bare agent CLI command about to be submitted from a shell."""
    if sess.provider != "shell" or not sess.pending_command_is_bare:
        return None
    token = sess.pending_command_token
    cleaned = _TERMINAL_INPUT_ESCAPE_RE.sub("", data)
    for char in cleaned:
        if char in "\r\n":
            return _provider_for_command_name(token) if token else None
        if char in ("\b", "\x7f"):
            token = token[:-1]
            continue
        if char.isspace() or not char.isprintable():
            continue
        candidate = (token + char).lower()
        if any(name.startswith(candidate) for name in _DEFAULT_PERMISSION_BYPASS_FLAGS):
            token = candidate
        else:
            return None
    return None


def _with_shell_launch_bypass(data: str, provider: str | None) -> str:
    """Append the default bypass flag before Enter for a bare shell launch."""
    if not provider:
        return data
    command_name = "claude" if provider == "claude_code" else provider
    flag = _DEFAULT_PERMISSION_BYPASS_FLAGS.get(command_name)
    if not flag:
        return data
    match = re.search(r"[\r\n]", data)
    if not match:
        return data
    return f"{data[:match.start()]} {flag}{data[match.start():]}"


def _temporary_title_from_prompt(prompt: str) -> str:
    """Return a readable, bounded title from the first submitted user turn."""
    normalized = re.sub(r"\s+", " ", prompt).strip()
    if len(normalized) <= TEMPORARY_SESSION_TITLE_MAX_CHARS:
        return normalized
    return normalized[: TEMPORARY_SESSION_TITLE_MAX_CHARS - 1].rstrip() + "…"


def _note_user_input(sess: PtySession, data: str, *, now: float | None = None) -> None:
    """Track real prompt submissions and publish a first-turn fallback title.

    xterm forwards individual keystrokes. Typing, resize/output, an empty Enter,
    and launching an agent CLI from the plain shell must not start the working
    indicator. Only a later non-empty prompt submitted to the detected agent
    counts as a turn. The first pending prompt is retained only up to a small
    bound and becomes the temporary title on Enter; Ctrl+C discards it.
    """
    cleaned = _TERMINAL_INPUT_ESCAPE_RE.sub("", data)
    submitted_at = time.time() if now is None else now
    for char in cleaned:
        if char == "\x03":
            sess.pending_input_len = 0
            sess.pending_title_input = ""
            sess.pending_command_token = ""
            sess.pending_command_is_bare = True
            sess.last_submit_at = 0.0
            continue
        if char in "\r\n":
            launch_provider = (
                _provider_for_command_name(sess.pending_command_token)
                if sess.provider == "shell" and sess.pending_command_is_bare
                else None
            )
            if launch_provider:
                sess.provider = launch_provider
                sess.has_user_turn = False
                sess.last_submit_at = 0.0
            elif sess.pending_input_len > 0 and sess.provider != "shell":
                if not sess.has_user_turn and not sess.provider_title:
                    temporary_title = _temporary_title_from_prompt(
                        sess.pending_title_input
                    )
                    if temporary_title:
                        sess.provider_title = temporary_title
                sess.has_user_turn = True
                sess.last_submit_at = submitted_at
            sess.pending_input_len = 0
            sess.pending_title_input = ""
            sess.pending_command_token = ""
            sess.pending_command_is_bare = True
            continue
        if char in ("\b", "\x7f"):
            sess.pending_input_len = max(0, sess.pending_input_len - 1)
            sess.pending_title_input = sess.pending_title_input[:-1]
            if sess.pending_command_is_bare:
                sess.pending_command_token = sess.pending_command_token[:-1]
            continue
        if char.isprintable():
            if (
                not sess.has_user_turn
                and len(sess.pending_title_input)
                < TEMPORARY_SESSION_TITLE_CAPTURE_MAX_CHARS
            ):
                sess.pending_title_input += char
            if not char.isspace():
                sess.pending_input_len += 1
                if sess.pending_command_is_bare:
                    candidate = (sess.pending_command_token + char).lower()
                    if any(name.startswith(candidate) for name in _DEFAULT_PERMISSION_BYPASS_FLAGS):
                        sess.pending_command_token = candidate
                    else:
                        sess.pending_command_token = ""
                        sess.pending_command_is_bare = False


_DEFAULT_PERMISSION_BYPASS_FLAGS = {
    "claude": "--dangerously-skip-permissions",
    "codex": "--dangerously-bypass-approvals-and-sandbox",
    "kimi": "--yolo",
    "kimi-code": "--yolo",
    "opencode": "--auto",
    "codebuddy": "--dangerously-skip-permissions",
    "cbc": "--dangerously-skip-permissions",
}


def _command_name(executable: str) -> str:
    """Return a cross-platform CLI basename for bare names and Windows paths."""
    filename = str(executable).replace("\\", "/").rsplit("/", 1)[-1]
    return os.path.splitext(filename)[0].lower()


def _with_default_bypass_permissions(cmd: list[str], *, safe_mode: bool = False) -> list[str]:
    """Add the provider's non-interactive permission bypass unless opted out.

    Dashboard and LOFA terminal entrypoints both use this PTY manager, so keeping
    the policy here makes bare ``claude``/``codex``/``kimi``/``opencode`` commands
    behave consistently regardless of which UI created the session.
    """
    normalized = list(cmd)
    if safe_mode or not normalized:
        return normalized
    flag = _DEFAULT_PERMISSION_BYPASS_FLAGS.get(_command_name(normalized[0]))
    if flag and flag not in normalized:
        normalized.append(flag)
    return normalized


def resolve_claude_cmd(safe_mode: bool = False) -> list[str] | None:
    """Locate the `claude` CLI on PATH. Returns None if not installed.

    By default (safe_mode=False) we pass `--dangerously-skip-permissions` so the
    in-dashboard wrapper doesn't pepper the user with permission prompts that
    interrupt agent flow. All tool calls remain visible via our PreToolUse trace
    hook, so the audit trail is preserved.
    Pass safe_mode=True to spawn vanilla claude with permission prompts.
    """
    for name in ("claude.cmd", "claude.exe", "claude"):
        p = shutil.which(name)
        if p:
            return _with_default_bypass_permissions([p], safe_mode=safe_mode)
    return None


def _dashboard_claude_settings_path() -> Path | None:
    """Return the project hooks settings used by dashboard-launched Claude PTYs."""
    try:
        from .installer import settings_path

        path = settings_path("project")
    except Exception as exc:
        logger.warning("dashboard Claude settings path unavailable: %s", exc)
        return None
    return path if path.is_file() else None


def _with_dashboard_claude_settings(cmd: list[str]) -> list[str]:
    """Load Omnicompany hooks even when the PTY cwd is above the repo root.

    The dashboard defaults to ``E:\\WindowsWorkspace`` while the project hooks
    live under ``omnicompany/.claude/settings.json``. Claude does not discover a
    child directory's project settings from that cwd, so pass them as additional
    settings for Claude commands only. Existing explicit ``--settings`` remains
    authoritative and other providers are unchanged.
    """
    normalized = list(cmd)
    has_explicit_settings = any(
        part == "--settings" or part.startswith("--settings=")
        for part in normalized[1:]
    )
    if (
        not normalized
        or _command_name(normalized[0]) != "claude"
        or has_explicit_settings
    ):
        return normalized
    path = _dashboard_claude_settings_path()
    if path is None:
        return normalized
    return [*normalized, "--settings", str(path)]


# ── on-disk session metadata store ──────────────────────────────────────────


def _sanitize_nonfinite(obj: Any) -> Any:
    """递归把 NaN/Infinity 洗成 None。

    cc_sessions.json 曾被 NaN 毒化过: FastAPI/json 默认 allow_nan=True 会把 NaN
    原样写盘, 读回再写形成循环。读回侧统一过这道清洗, 脏数据进过一次也不会再循环。
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize_nonfinite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nonfinite(v) for v in obj]
    return obj


def _read_meta_store(
    *,
    use_cache: bool = True,
    assume_locked: bool = False,
) -> dict[str, dict[str, Any]]:
    """Read the shared registry once per atomic file generation."""
    global _META_STORE_CACHE_KEY, _META_STORE_CACHE_VALUE

    if not assume_locked:
        with _meta_store_guard():
            return _read_meta_store(use_cache=use_cache, assume_locked=True)

    p = _meta_store_path()
    try:
        with _open_text_shared_delete(p) as stream:
            stat = os.fstat(stream.fileno())
            cache_key = (str(p), int(stat.st_mtime_ns), int(stat.st_size), int(stat.st_ino))
            if (
                use_cache
                and _META_STORE_CACHE_KEY == cache_key
                and _META_STORE_CACHE_VALUE is not None
            ):
                return _META_STORE_CACHE_VALUE
            store = json.loads(stream.read() or "{}") or {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}
    # 历史脏文件兜底: NaN/Infinity → None (见 _sanitize_nonfinite)
    sanitized = _sanitize_nonfinite(store)
    if use_cache:
        _META_STORE_CACHE_KEY = cache_key
        _META_STORE_CACHE_VALUE = sanitized
    return sanitized


@contextmanager
def _meta_store_guard() -> Iterator[None]:
    """Serialize cc_sessions.json read-modify-write across hooks and daemon."""
    lock_path = _meta_store_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _META_STORE_THREAD_LOCK, open(lock_path, "a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_meta_store_unlocked(store: dict[str, dict[str, Any]]) -> None:
    global _META_STORE_CACHE_KEY, _META_STORE_CACHE_VALUE

    p = _meta_store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 紧凑写 (不 indent): store 已涨到 9MB+, 每条聊天消息全量写一次, 体积小一半;
        # allow_nan=False 拦截 NaN/Infinity, 防再次毒化 store 文件 (曾实发)。
        text = json.dumps(store, ensure_ascii=False, allow_nan=False)
    except ValueError:
        # 有脏浮点混进来: 洗掉后兜底写, 并留 log 让毒化源可追查
        logger.warning("cc_sessions.json 含 NaN/Infinity, 清洗后重写", exc_info=True)
        text = json.dumps(_sanitize_nonfinite(store), ensure_ascii=False, allow_nan=False)
    last_error: OSError | None = None
    for attempt in range(8):
        tmp = p.with_name(f".{p.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, p)
            stat = p.stat()
            _META_STORE_CACHE_KEY = (
                str(p),
                int(stat.st_mtime_ns),
                int(stat.st_size),
                int(stat.st_ino),
            )
            _META_STORE_CACHE_VALUE = store
            return
        except OSError as exc:
            last_error = exc
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt < 7:
                time.sleep(min(0.01 * (2 ** attempt), 0.25))
    logger.error(
        "cc_sessions.json write failed after retries; refusing to silently drop metadata: %s",
        last_error,
    )
    raise OSError(f"cc_sessions.json write failed after retries: {last_error}")


def _write_meta_store(store: dict[str, dict[str, Any]]) -> None:
    """Atomically merge one or more session entries into the shared registry."""
    with _meta_store_guard():
        current = _read_meta_store(use_cache=False, assume_locked=True)
        for sid, fields in store.items():
            current[sid] = {**(current.get(sid) or {}), **fields}
        _write_meta_store_unlocked(current)


def _mutate_meta_store(mutator: Callable[[dict[str, dict[str, Any]]], None]) -> None:
    with _meta_store_guard():
        store = _read_meta_store(use_cache=False, assume_locked=True)
        mutator(store)
        _write_meta_store_unlocked(store)


def _upsert_meta_entry(
    sid: str,
    fields: dict[str, Any],
    *,
    preserve_existing: bool = False,
) -> None:
    def mutate(store: dict[str, dict[str, Any]]) -> None:
        current = store.get(sid) or {}
        store[sid] = (
            {**fields, **current}
            if preserve_existing
            else {**current, **fields}
        )

    _mutate_meta_store(mutate)


def _claude_jsonl_for(cwd: str, claude_session_id: str | None) -> Path | None:
    """Best-effort path to claude's own conversation log so we can verify a
    session is actually resumable via `claude --resume`.

    Claude's encoding (observed from real `~/.claude/projects/`):
      `C:\\Users\\alice`           → `C--Users-alice`
      `E:\\MyWorkspace\\myproject` → `E--MyWorkspace-myproject`
    Rule: colon → `--`, backslash/slash → `-`, no leading marker.
    """
    if not claude_session_id:
        return None
    enc = cwd.replace(":", "--").replace("\\", "-").replace("/", "-")
    base = Path.home() / ".claude" / "projects" / enc
    p = base / f"{claude_session_id}.jsonl"
    return p if p.is_file() else None


def _codex_jsonl_for(provider_session_id: str | None) -> Path | None:
    """Return Codex's durable rollout log for a real provider session id."""
    if not provider_session_id:
        return None
    base = Path.home() / ".codex" / "sessions"
    if not base.is_dir():
        return None
    matches = base.glob(f"*/*/*/rollout-*-{provider_session_id}.jsonl")
    return next((path for path in matches if path.is_file()), None)


def _kimi_state_for(provider_session_id: str | None) -> Path | None:
    """Return Kimi's durable state file only when the native session exists."""
    if not provider_session_id:
        return None
    session_name = str(provider_session_id)
    if not session_name.startswith("session_"):
        session_name = f"session_{session_name}"
    base = Path.home() / ".kimi-code" / "sessions"
    return next((path for path in base.glob(f"*/{session_name}/state.json") if path.is_file()), None)


def _opencode_db_for(provider_session_id: str | None) -> Path | None:
    """Return OpenCode's DB only if it contains the requested native session."""
    if not provider_session_id:
        return None
    db_path = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    if not db_path.is_file():
        return None
    try:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.2) as db:
            found = db.execute(
                "SELECT 1 FROM session WHERE id = ? LIMIT 1",
                (str(provider_session_id),),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return None
    return db_path if found else None


def _codebuddy_jsonl_for(provider_session_id: str | None) -> Path | None:
    """Return CodeBuddy's durable project transcript for a native session."""
    if not provider_session_id:
        return None
    base = Path.home() / ".codebuddy" / "projects"
    if not base.is_dir():
        return None
    return next(
        (
            path
            for path in base.glob(f"*/{provider_session_id}.jsonl")
            if path.is_file()
        ),
        None,
    )


def _codex_latest_log_id() -> int:
    """Return a cursor taken before spawning Codex.

    Codex writes its native thread id to ``logs_2.sqlite`` even when the user
    changes conversation inside the TUI with ``/resume``.  Starting from a
    pre-spawn cursor lets us follow only this PTY's new process instead of
    guessing from timestamps or scanning unrelated conversation history.
    """
    path = Path.home() / ".codex" / "logs_2.sqlite"
    if not path.is_file():
        return 0
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=1) as db:
            row = db.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()
        return int(row[0] if row else 0)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return 0


def _codex_native_thread_details(thread_ids: list[str]) -> dict[str, tuple[str, str]]:
    """Resolve only durable Codex threads, returning id -> (title, rollout)."""
    if not thread_ids:
        return {}
    path = Path.home() / ".codex" / "state_5.sqlite"
    if not path.is_file():
        return {}
    unique_ids = list(dict.fromkeys(thread_ids))
    placeholders = ",".join("?" for _ in unique_ids)
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=1) as db:
            rows = db.execute(
                f"SELECT id, title, rollout_path FROM threads WHERE id IN ({placeholders})",
                unique_ids,
            ).fetchall()
    except (OSError, sqlite3.Error):
        return {}
    return {
        str(thread_id): (str(title or ""), str(rollout_path or ""))
        for thread_id, title, rollout_path in rows
        if rollout_path and Path(str(rollout_path)).is_file()
    }


def _poll_codex_native_link(
    *,
    after_id: int,
    child_pid: int | None,
    pty_id: str,
    process_uuid: str | None,
    candidates: dict[str, int],
) -> tuple[int, str | None, dict[str, int], str | None, str | None]:
    """Follow a Codex process to the latest durable native conversation.

    The process PID is authoritative for direct Codex PTYs.  For a Codex
    launched from the plain shell, its first tool process carries
    ``OMNI_CC_PTY_ID`` and provides a safe fallback correlation.  We never use
    cwd/title/time proximity, because two simultaneous browser sessions may
    otherwise be cross-wired.
    """
    logs_path = Path.home() / ".codex" / "logs_2.sqlite"
    if not logs_path.is_file():
        return after_id, process_uuid, dict(candidates), None, None

    where = ["id > ?"]
    params: list[Any] = [after_id]
    if process_uuid:
        where.append("process_uuid = ?")
        params.append(process_uuid)
    else:
        links: list[str] = []
        if child_pid:
            links.append("process_uuid LIKE ?")
            params.append(f"pid:{int(child_pid)}:%")
        links.append("feedback_log_body LIKE ?")
        params.append(f"%OMNI_CC_PTY_ID%{pty_id}%")
        where.append("(" + " OR ".join(links) + ")")

    try:
        with sqlite3.connect(
            f"file:{logs_path.as_posix()}?mode=ro", uri=True, timeout=1
        ) as db:
            latest_row = db.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()
            latest_id = int(latest_row[0] if latest_row else after_id)
            rows = db.execute(
                "SELECT id, thread_id, process_uuid, feedback_log_body "
                f"FROM logs WHERE {' AND '.join(where)} ORDER BY id",
                params,
            ).fetchall()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return after_id, process_uuid, dict(candidates), None, None

    detected_process = process_uuid
    if detected_process is None:
        direct_prefix = f"pid:{int(child_pid)}:" if child_pid else ""
        marker = pty_id.lower()
        for _row_id, _thread_id, row_process, body in rows:
            row_process = str(row_process or "")
            if direct_prefix and row_process.startswith(direct_prefix):
                detected_process = row_process
                break
            text = str(body or "").lower()
            if "omni_cc_pty_id" in text and marker in text and row_process:
                detected_process = row_process
                break

    updated = dict(candidates)
    if detected_process:
        for row_id, thread_id, row_process, _body in rows:
            if str(row_process or "") != detected_process or not thread_id:
                continue
            updated[str(thread_id)] = int(row_id)

    details = _codex_native_thread_details(list(updated))
    durable = [
        (row_id, thread_id, details[thread_id])
        for thread_id, row_id in updated.items()
        if thread_id in details
    ]
    if not durable:
        return latest_id, detected_process, updated, None, None
    _row_id, thread_id, (title, _rollout_path) = max(durable)
    return latest_id, detected_process, updated, thread_id, title


def _conversation_jsonl_for(
    provider: str | None,
    cwd: str,
    provider_session_id: str | None,
) -> Path | None:
    if provider == "claude_code":
        return _claude_jsonl_for(cwd, provider_session_id)
    if provider == "codex":
        return _codex_jsonl_for(provider_session_id)
    if provider == "kimi":
        return _kimi_state_for(provider_session_id)
    if provider == "opencode":
        return _opencode_db_for(provider_session_id)
    if provider == "codebuddy":
        return _codebuddy_jsonl_for(provider_session_id)
    return None


def _resume_command(provider: str, provider_session_id: str) -> list[str]:
    """Build a provider-native resume command with the default bypass policy."""
    if provider == "claude_code":
        resolved = resolve_claude_cmd(safe_mode=False)
        if resolved is None:
            raise RuntimeError("claude CLI not found on PATH. Install Claude Code first.")
        return [*resolved, "--resume", provider_session_id]
    if provider == "codex":
        # Codex parses this as a global option, before the resume subcommand.
        return [
            *_with_default_bypass_permissions(["codex"], safe_mode=False),
            "resume",
            provider_session_id,
        ]
    if provider == "kimi":
        return [
            *_with_default_bypass_permissions(["kimi"], safe_mode=False),
            "-S",
            provider_session_id,
        ]
    if provider == "opencode":
        return [
            *_with_default_bypass_permissions(["opencode"], safe_mode=False),
            "--session",
            provider_session_id,
        ]
    if provider == "codebuddy":
        return [
            *_with_default_bypass_permissions(["codebuddy"], safe_mode=False),
            "--resume",
            provider_session_id,
        ]
    raise RuntimeError(f"provider {provider!r} does not support PTY resume")


def list_recoverable_sessions() -> list[dict[str, Any]]:
    """Return PTYs whose provider conversation log can really be resumed.

    ``ended_at`` is not required: an unclean daemon exit leaves live-looking
    metadata behind even though the in-memory PTY registry is gone.
    """
    out: list[dict[str, Any]] = []
    store = _read_meta_store()
    for sid, m in store.items():
        if (
            m.get("hidden_from_recovery")
            or m.get("recovery_disabled")
            or _is_explicit_kill_reason(m.get("exit_reason"))
        ):
            continue
        # Once a PTY has been resumed, expose only the newest link in that chain.
        # Otherwise one durable Codex conversation appears several times.
        resumed_to = m.get("resumed_to")
        if resumed_to and resumed_to in store:
            continue
        cmd = m.get("cmd") or []
        is_pty = m.get("kind") == "pty" or (
            not str(sid).startswith("chat-")
            and bool(cmd)
            and "(chat)" not in [str(part).lower() for part in cmd]
        )
        provider = m.get("provider") or (
            _provider_for_command_name(str(cmd[0])) if cmd else None
        )
        if not is_pty or provider not in {"claude_code", "codex", "kimi", "opencode", "codebuddy"}:
            continue
        provider_session_id = m.get("provider_session_id") or (
            m.get("claude_session_id") if provider == "claude_code" else None
        )
        if not provider_session_id:
            continue
        jsonl = _conversation_jsonl_for(
            provider,
            m.get("cwd") or "",
            provider_session_id,
        )
        if jsonl is None:
            continue
        out.append({
            **m,
            "provider": provider,
            "provider_session_id": provider_session_id,
            "alive": False,
            "status": "recoverable",
            "conversation_jsonl": str(jsonl),
            "claude_jsonl": str(jsonl) if provider == "claude_code" else None,
            "jsonl_present": True,
        })
    return sorted(
        out,
        key=lambda x: x.get("ended_at") or x.get("started_at") or 0,
        reverse=True,
    )


def update_meta_field(sid: str, *, allow_none: bool = False, **fields: Any) -> None:
    """External hook helper — write new field(s) for a session id (e.g. `claude_session_id`)."""
    if not sid:
        return
    values = fields if allow_none else {k: v for k, v in fields.items() if v is not None}
    _upsert_meta_entry(sid, values)


def _latest_resume_entry(
    store: dict[str, dict[str, Any]],
    sid: str,
) -> tuple[str, dict[str, Any] | None]:
    """Follow persisted PTY replacements so old browser tabs stay resumable."""
    current_id = sid
    seen: set[str] = set()
    while current_id not in seen:
        seen.add(current_id)
        current = store.get(current_id)
        if current is None:
            return current_id, None
        next_id = current.get("resumed_to")
        if not next_id or next_id not in store:
            return current_id, current
        current_id = str(next_id)
    return current_id, store.get(current_id)


class PtyManager:
    """Process-wide registry of live PTY sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, PtySession] = {}
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task | None = None
        self._checked_host_ids: set[str] = set()
        # 旧 PTY id → 替代会话 id (resume 链别名)。老浏览器页签始终以其原始 id 为键,
        # 高频页签状态快照据此把新会话的运行态投影回旧 id。仅 resume() 写入,
        # 目标会话死亡后投影自动跳过(无需清理, 量级是 resume 次数)。
        self._resume_aliases: dict[str, str] = {}

    def _ensure_reaper_task(self) -> None:
        """Keep one idle reaper alive whenever this manager owns live sessions."""
        if not self._sessions or (
            self._reaper_task is not None and not self._reaper_task.done()
        ):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Some read-only/test callers discover sessions outside an event
            # loop. The next async create/attach path will start the reaper.
            return
        self._reaper_task = asyncio.create_task(
            self._reaper_loop(),
            name="pty-reaper",
        )

    def _admit_new_session(self) -> None:
        """Refuse process-tree growth at capacity without disturbing live work."""
        self._discover_host_sessions()
        live_count = sum(not sess.closed for sess in self._sessions.values())
        if MAX_LIVE_SESSIONS and live_count >= MAX_LIVE_SESSIONS:
            raise RuntimeError(
                "PTY background session capacity reached "
                f"({live_count}/{MAX_LIVE_SESSIONS}). "
                "Reconnect to or end an existing session before starting another; "
                "set OMNI_PTY_MAX_LIVE_SESSIONS to override the host limit."
            )

    def list_meta(self) -> list[dict[str, Any]]:
        self._discover_host_sessions()
        store = _read_meta_store()
        for session in self._sessions.values():
            self._refresh_session_metadata(session, store.get(session.id) or {})
        try:
            from omnicompany.dashboard.boss_sight.services.agent_digest import (
                load_digests,
            )

            self.apply_digest_titles(load_digests())
        except Exception:
            # Titles are progressive enhancement: the first prompt/native
            # provider title must remain usable if the digest store is absent.
            logger.debug("could not refresh PTY digest titles", exc_info=True)
        return [s.to_meta() for s in self._sessions.values()]

    def apply_digest_titles(self, digests: dict[str, dict[str, Any]]) -> None:
        """Project cheap-model session summaries onto matching live PTYs."""
        for session in self._sessions.values():
            native_id = session.provider_session_id or session.claude_session_id
            digest = (
                digests.get(f"{session.provider}:{native_id}")
                if native_id
                else None
            )
            title = str((digest or {}).get("title") or "").strip()
            session.summary_title = title or None

    def tab_states(self) -> list[dict[str, Any]]:
        """页签活跃徽章的高频运行态投影(前端 2s 轮询, 多浏览器)。

        纯内存读取: 与 list_meta() 不同, 不读数 MB 的 cc_sessions.json, 不做
        host 发现, 不刷新 hook 字段 —— 那些属于 get()/attach//sessions 等
        低频路径, 页签打开的瞬间就会把状态同步回来。纯内存快照最多滞后
        一个轮询周期, 换来每个浏览器每 2 秒一次的轮询零磁盘开销。
        """
        metas = {sess.id: sess.to_meta() for sess in self._sessions.values()}
        items = [self._tab_state_item(meta) for meta in metas.values()]
        for old_id, new_id in self._resume_aliases.items():
            if old_id in metas:
                continue
            target = metas.get(new_id)
            if target is None:
                continue  # 别名目标已死(kill/reap): 不投影陈旧运行态
            item = self._tab_state_item(target)
            item["id"] = old_id
            items.append(item)
        return items

    @staticmethod
    def _tab_state_item(meta: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": meta["id"],
            "alive": bool(meta.get("alive")),
            "working": bool(meta.get("working")),
            "has_user_turn": bool(meta.get("has_user_turn")),
            "status": meta.get("status") or "alive",
            # 列表行展示所需的轻量 meta(活跃会话页与页签徽章共用这份轮询,
            # 不必再为每行单发一次完整 /sessions 请求)。
            "provider": meta.get("provider"),
            "cwd": meta.get("cwd"),
            "provider_title": meta.get("provider_title"),
            "display_title": meta.get("display_title"),
            "provider_session_id": meta.get("provider_session_id"),
            "started_at": meta.get("started_at"),
            "last_submit_at": meta.get("last_submit_at"),
            "last_output_at": meta.get("last_output_at"),
        }

    def _register_resume_aliases(
        self,
        store: dict[str, dict[str, Any]],
        requested_id: str,
        target_id: str,
    ) -> None:
        """登记替换链上每个旧 id → 目标会话 id 的别名。

        沿 resumed_to 走到链尾, 把途经的旧 id 全部指向当前活会话, 让仍用
        原始 id 当页签键的老浏览器标签拿到真实运行态(含多次重启的长链)。
        """
        current_id: str | None = requested_id
        seen: set[str] = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            if current_id != target_id:
                self._resume_aliases[current_id] = target_id
            next_id = (store.get(current_id) or {}).get("resumed_to")
            current_id = str(next_id) if next_id else None

    def get(self, sid: str) -> PtySession | None:
        session = self._sessions.get(sid)
        if session is not None:
            # This is the websocket input/resize hot path. Reading and recursively
            # sanitizing the multi-megabyte registry here used to happen twice per
            # keystroke (_discover + refresh), adding ~400 ms before every write.
            # list_meta refreshes external hook fields; active sessions already
            # carry their live state in memory.
            return session
        self._discover_host_sessions()
        return self._sessions.get(sid)

    @staticmethod
    def _refresh_session_metadata(sess: PtySession, meta: dict[str, Any]) -> None:
        """Merge hook/native-link updates written outside the daemon process."""
        if meta.get("provider"):
            sess.provider = str(meta["provider"])
        if meta.get("provider_session_id"):
            sess.provider_session_id = str(meta["provider_session_id"])
        if meta.get("provider_title"):
            sess.provider_title = str(meta["provider_title"])
        if meta.get("claude_session_id"):
            sess.claude_session_id = str(meta["claude_session_id"])
        if meta.get("active_plan"):
            sess.active_plan = str(meta["active_plan"])
        if "has_user_turn" in meta:
            sess.has_user_turn = bool(meta.get("has_user_turn"))
        if "last_submit_at" in meta:
            sess.last_submit_at = float(meta.get("last_submit_at") or 0.0)

    def _discover_host_sessions(self) -> None:
        """Rehydrate detached PTY hosts after a ccdaemon replacement."""
        host_dir = _pty_host_dir()
        if not host_dir.is_dir():
            return
        store = _read_meta_store()
        for path in host_dir.glob("*.json"):
            if ".state." in path.name:
                continue
            sid = path.stem
            if sid in self._sessions or sid in self._checked_host_ids:
                continue
            state = _read_pty_host_state(sid)
            # An empty read may be a transient replace/partial-state condition;
            # leave it eligible for the next discovery pass.
            if state:
                self._checked_host_ids.add(sid)
            meta = store.get(sid) or {}
            if not _pty_host_alive(state):
                # A detached owner can terminate while no ccdaemon bridge is
                # attached. Reconcile the durable registry instead of leaving
                # an impossible ended_at=null / alive=true zombie forever.
                reason: str | None = None
                if state and not state.get("alive"):
                    reason = str(state.get("exit_reason") or "host-not-alive")
                elif state.get("alive"):
                    try:
                        host_pid = int(state.get("host_pid") or 0)
                    except (TypeError, ValueError):
                        host_pid = 0
                    if host_pid and not _pid_alive(host_pid):
                        reason = "host-process-missing"
                if reason and not meta.get("ended_at"):
                    update_meta_field(
                        sid,
                        ended_at=time.time(),
                        exit_reason=reason,
                        exit_code=state.get("exit_code"),
                    )
                    logger.warning(
                        "reconciled detached pty id=%s reason=%s host_pid=%s child_pid=%s",
                        sid,
                        reason,
                        state.get("host_pid"),
                        state.get("child_pid"),
                    )
                continue
            cmd = state.get("cmd") or meta.get("cmd") or []
            sess = PtySession(
                id=sid,
                cmd=[str(part) for part in cmd],
                cwd=str(state.get("cwd") or meta.get("cwd") or os.getcwd()),
                cols=int(state.get("cols") or meta.get("cols") or DEFAULT_COLS),
                rows=int(state.get("rows") or meta.get("rows") or DEFAULT_ROWS),
                started_at=float(state.get("started_at") or meta.get("started_at") or time.time()),
                # Discovery happens with zero browser subscribers. Start a fresh
                # detach window on daemon replacement so cross-device work keeps
                # its full TTL while genuinely abandoned sessions remain reapable.
                last_detach_at=time.time(),
                last_output_at=float(state.get("last_output_at") or time.time()),
                provider=str(state.get("provider") or meta.get("provider") or "shell"),
                child_pid=int(state.get("child_pid") or 0) or None,
                host_pid=int(state.get("host_pid") or 0) or None,
                host_port=int(state.get("host_port") or 0) or None,
                host_token=str(state.get("host_token") or "") or None,
                host_answers_queries=bool(state.get("terminal_query_responder")),
                replay_truncated=_replay_meta_is_truncated(state),
                provider_session_id=meta.get("provider_session_id"),
                provider_title=meta.get("provider_title"),
                claude_session_id=meta.get("claude_session_id"),
                active_plan=meta.get("active_plan"),
                has_user_turn=bool(meta.get("has_user_turn")),
                last_submit_at=float(meta.get("last_submit_at") or 0.0),
                codex_log_cursor=0,
                codex_process_uuid=meta.get("codex_process_uuid"),
            )
            self._sessions[sid] = sess
            if sess.provider == "codex":
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    continue
                sess.provider_link_task = asyncio.create_task(
                    self._provider_link_loop(sess),
                    name=f"pty-provider-link-{sid}",
                )
        self._ensure_reaper_task()

    async def _ensure_host_connected(
        self,
        sess: PtySession,
        *,
        want_snapshot: bool = True,
    ) -> list[str]:
        if not sess.host_port or not sess.host_token:
            raise KeyError(sess.id)
        writer = sess.host_writer
        if writer is not None and not writer.is_closing():
            return list(sess.ring) if want_snapshot else []
        async with sess.host_connect_lock:
            writer = sess.host_writer
            if writer is not None and not writer.is_closing():
                return list(sess.ring) if want_snapshot else []
            from .pty_host import read_frame, write_frame

            writer: asyncio.StreamWriter | None = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", int(sess.host_port)),
                    timeout=2,
                )
                await write_frame(
                    writer,
                    {
                        "type": "hello",
                        "token": sess.host_token,
                        "client": "ccdaemon",
                        "snapshot_protocol": SNAPSHOT_PROTOCOL_VERSION,
                    },
                )
                first = await asyncio.wait_for(read_frame(reader), timeout=5)
            except Exception as exc:
                try:
                    if writer is not None:
                        writer.close()
                except Exception:
                    pass
                raise KeyError(sess.id) from exc
            chunks: list[str] = []
            meta: dict[str, Any] = {}
            if first.get("type") == "snapshot":
                chunks = [str(chunk) for chunk in first.get("chunks") or []]
                first_meta = first.get("meta") or {}
                if isinstance(first_meta, dict):
                    meta = first_meta
            elif first.get("type") == "snapshot_begin":
                first_meta = first.get("meta") or {}
                if isinstance(first_meta, dict):
                    meta = first_meta
                while True:
                    frame = await asyncio.wait_for(read_frame(reader), timeout=10)
                    kind = frame.get("type")
                    if kind == "snapshot_chunk":
                        chunks.extend(str(chunk) for chunk in frame.get("chunks") or [])
                    elif kind == "snapshot_end":
                        break
                    else:
                        writer.close()
                        raise KeyError(sess.id)
            else:
                writer.close()
                raise KeyError(sess.id)
            # A legacy host may still offer its old 64 MB replay. Keep only the
            # same bounded window used for live output, and return that bounded
            # snapshot to xterm instead of forwarding the oversized source
            # list after it has already been trimmed from ``sess.ring``.
            browser_snapshot = _replace_proxy_replay(sess, chunks)
            sess.replay_truncated = (
                sess.replay_truncated or _replay_meta_is_truncated(meta)
            )
            sess.host_writer = writer
            if isinstance(meta, dict):
                sess.child_pid = int(meta.get("child_pid") or sess.child_pid or 0) or None
                sess.last_output_at = float(meta.get("last_output_at") or sess.last_output_at)
                sess.host_answers_queries = bool(meta.get("terminal_query_responder"))
            await asyncio.to_thread(
                _write_pty_host_generation_snapshot,
                sess,
                meta,
            )
            sess.host_reader_task = asyncio.create_task(
                self._host_reader_loop(sess, reader, writer),
                name=f"pty-host-bridge-{sess.id}",
            )
            return browser_snapshot

    async def _send_host(self, sess: PtySession, message: dict[str, Any]) -> None:
        from .pty_host import write_frame

        await self._ensure_host_connected(sess, want_snapshot=False)
        async with sess.host_write_lock:
            writer = sess.host_writer
            if writer is None or writer.is_closing():
                raise KeyError(sess.id)
            try:
                await write_frame(writer, message)
            except Exception as exc:
                sess.host_writer = None
                raise KeyError(sess.id) from exc

    async def _host_reader_loop(
        self,
        sess: PtySession,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        from .pty_host import read_frame

        host_exited = False
        try:
            while not sess.closed:
                message = await read_frame(reader)
                kind = message.get("type")
                if kind == "output":
                    chunk = str(message.get("data") or "")
                    if not chunk:
                        continue
                    if not sess.host_answers_queries:
                        from .pty_host import TerminalQueryResponder, write_frame

                        if sess.terminal_query_responder is None:
                            sess.terminal_query_responder = TerminalQueryResponder()
                        terminal_reply = sess.terminal_query_responder.feed(chunk)
                        if terminal_reply:
                            async with sess.host_write_lock:
                                await write_frame(
                                    writer,
                                    {"type": "input", "data": terminal_reply},
                                )
                    async with sess.stream_lock:
                        sess.last_output_at = time.time()
                        _append_proxy_output(sess, chunk)
                        for queue in list(sess.subscribers):
                            try:
                                queue.put_nowait(chunk)
                            except asyncio.QueueFull:
                                self._disconnect_slow_subscriber(sess, queue)
                elif kind == "exit":
                    host_exited = True
                    sess.closed = True
                    reason = str(message.get("reason") or "host-exit")
                    for queue in list(sess.subscribers):
                        try:
                            queue.put_nowait({"type": "exit", "reason": reason})
                        except asyncio.QueueFull:
                            pass
                    update_meta_field(
                        sess.id,
                        ended_at=time.time(),
                        exit_reason=reason,
                    )
                    self._sessions.pop(sess.id, None)
                    break
        except (asyncio.IncompleteReadError, ConnectionError, ValueError, OSError):
            pass
        finally:
            if sess.host_writer is writer:
                sess.host_writer = None
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if not host_exited and not sess.closed:
                # Force browser sockets to reconnect. Their next attach reaches
                # this same host and receives a fresh terminal snapshot.
                for queue in list(sess.subscribers):
                    try:
                        queue.put_nowait({"type": "proxy_reconnect"})
                    except asyncio.QueueFull:
                        pass

    async def create(
        self,
        cmd: list[str] | None,
        cwd: str | None,
        cols: int = DEFAULT_COLS,
        rows: int = DEFAULT_ROWS,
        safe_mode: bool = False,
        resume_claude_session_id: str | None = None,
        resume_provider_session_id: str | None = None,
        resumed_from_pty_id: str | None = None,
    ) -> PtySession:
        self._admit_new_session()
        # Capture the Codex log watermark before spawn. The native TUI can move
        # between threads without restarting the PTY, so the durable binding is
        # followed from native lifecycle logs after this point.
        codex_log_cursor = _codex_latest_log_id()

        if cmd is None:
            resolved = resolve_claude_cmd(safe_mode=safe_mode)
            if resolved is None:
                raise RuntimeError(
                    "claude CLI not found on PATH. Install Claude Code first."
                )
            cmd = resolved
            if resume_claude_session_id:
                cmd = cmd + ["--resume", resume_claude_session_id]

        cmd = _with_default_bypass_permissions(cmd, safe_mode=safe_mode)
        cmd = _with_dashboard_claude_settings(cmd)

        cwd = resolve_session_cwd(cwd)

        # Windows: winpty 自己不查 PATHEXT, 裸命令名 (kimi/opencode/codex) 会
        # spawn 失败 (WinptyError: 系统找不到指定的文件), 且失败现场还会泄漏
        # winpty-agent 句柄 — 先在这里用 shutil.which 解析成全路径.
        if os.name == "nt" and cmd and os.path.basename(cmd[0]) == cmd[0]:
            resolved0 = shutil.which(cmd[0])
            if resolved0 is None:
                raise RuntimeError(f"command not found on PATH: {cmd[0]!r}")
            cmd = [resolved0, *cmd[1:]]

        # mint our session id BEFORE spawn so we can hand it to the child via env;
        # hooks read OMNI_CC_PTY_ID and use it as their trace_id (so the dashboard
        # cc_session entity can correlate to its own trace events).
        sid = uuid.uuid4().hex[:16]

        if os.name == "nt" and os.environ.get("OMNI_PTY_IN_PROCESS") != "1":
            return await self._create_hosted(
                sid=sid,
                cmd=cmd,
                cwd=cwd,
                cols=cols,
                rows=rows,
                codex_log_cursor=codex_log_cursor,
                resume_claude_session_id=resume_claude_session_id,
                resume_provider_session_id=resume_provider_session_id,
                resumed_from_pty_id=resumed_from_pty_id,
            )

        from winpty import PTY  # imported lazily so non-Windows hosts can import this module

        # Inherit current env, then add our marker. winpty wants `KEY=VAL\0KEY=VAL\0\0`.
        env_dict = dict(os.environ)
        env_dict["OMNI_CC_PTY_ID"] = sid
        # 消费端(LOFA xterm 6 / 其它 web 终端)支持 DEC 2026 同步输出,但 WebView+ConPTY
        # 两跳链路上 Claude Code 的自动探测(DECRQM 往返)不可靠 —— 官方文档点名这种
        # 「终端支持但没被识别」的场景应显式强开;不支持 2026 的消费端会安全忽略该序列。
        env_dict.setdefault("CLAUDE_CODE_FORCE_SYNC_OUTPUT", "1")
        env_str = "".join(f"{k}={v}\0" for k, v in env_dict.items()) + "\0"

        pty = PTY(cols, rows)
        appname = cmd[0]
        cmdline = " ".join(_quote_arg(a) for a in cmd[1:]) if len(cmd) > 1 else None
        try:
            ok = pty.spawn(appname, cmdline=cmdline, cwd=cwd, env=env_str)
        except Exception as e:
            # spawn 失败必须收掉 PTY 对象, 否则泄漏 winpty-agent 句柄 (积多次会把
            # daemon 拖进不服务状态); 统一抛 RuntimeError 让路由层回 400 而非 500.
            try:
                pty.close()
            except Exception:
                pass
            raise RuntimeError(f"PTY spawn failed for {cmd!r}: {e}") from e
        if not ok:
            try:
                pty.close()
            except Exception:
                pass
            raise RuntimeError(f"PTY spawn failed for {cmd!r}")
        sess = PtySession(
            id=sid,
            cmd=cmd,
            cwd=cwd,
            cols=cols,
            rows=rows,
            started_at=time.time(),
            pty=pty,
            provider=_provider_for_command_name(cmd[0]) or "shell",
            child_pid=int(pty.pid) if getattr(pty, "pid", None) else None,
            provider_session_id=resume_provider_session_id or resume_claude_session_id,
            claude_session_id=resume_claude_session_id,
            codex_log_cursor=codex_log_cursor,
        )
        sess.reader_task = asyncio.create_task(self._reader_loop(sess), name=f"pty-read-{sid}")
        async with self._lock:
            self._sessions[sid] = sess
        self._ensure_reaper_task()

        # Persist metadata so this session can be discovered after a backend restart.
        _upsert_meta_entry(sid, {
            "id": sid,
            "kind": "pty",
            "provider": sess.provider,
            "child_pid": sess.child_pid,
            "cmd": cmd,
            "cwd": cwd,
            "started_at": sess.started_at,
            "ended_at": None,
            # Filled by SessionStart on the first turn; resume already knows it.
            "provider_session_id": resume_provider_session_id or resume_claude_session_id,
            "provider_title": None,
            "claude_session_id": resume_claude_session_id,
            "active_plan": None,
            "has_user_turn": False,
            "last_submit_at": None,
            "resumed_from_claude_session_id": resume_claude_session_id,
            "resumed_from_provider_session_id": resume_provider_session_id,
            "resumed_from_pty_id": resumed_from_pty_id,
        }, preserve_existing=True)
        if sess.provider == "codex":
            sess.provider_link_task = asyncio.create_task(
                self._provider_link_loop(sess),
                name=f"pty-provider-link-{sid}",
            )
        logger.info("pty_session created id=%s cmd=%s", sid, cmd)
        return sess

    async def _create_hosted(
        self,
        *,
        sid: str,
        cmd: list[str],
        cwd: str,
        cols: int,
        rows: int,
        codex_log_cursor: int,
        resume_claude_session_id: str | None,
        resume_provider_session_id: str | None,
        resumed_from_pty_id: str | None,
    ) -> PtySession:
        """Spawn a detached PTY owner and register a reconnectable proxy."""
        host_dir = _pty_host_dir()
        host_dir.mkdir(parents=True, exist_ok=True)
        state_path = _pty_host_state_path(sid)
        launch_path = host_dir / f".{sid}.launch"
        log_path = host_dir / f"{sid}.log"
        started_at = time.time()
        provider = _provider_for_command_name(cmd[0]) or "shell"
        token = uuid.uuid4().hex + uuid.uuid4().hex
        config = {
            "schema": "omnicompany.pty-host-launch.v1",
            "id": sid,
            "token": token,
            "cmd": cmd,
            "cwd": cwd,
            "cols": cols,
            "rows": rows,
            "provider": provider,
            "started_at": started_at,
            "state_path": str(state_path),
        }
        launch_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        log_handle = open(log_path, "ab")
        creationflags = 0
        if sys.platform == "win32":
            creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        try:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "omnicompany.dashboard.ccdaemon.pty_host",
                    "--config",
                    str(launch_path),
                ],
                cwd=cwd,
                env=dict(os.environ),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                close_fds=True,
            )
        finally:
            log_handle.close()

        state: dict[str, Any] = {}
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            state = _read_pty_host_state(sid)
            if _pty_host_alive(state):
                break
            await asyncio.sleep(0.05)
        if not _pty_host_alive(state):
            try:
                proc.terminate()
            except Exception:
                pass
            detail = state.get("exit_reason") or f"host exited with {proc.poll()}"
            raise RuntimeError(f"detached PTY host failed to start: {detail}; log={log_path}")
        try:
            launch_path.unlink()
        except OSError:
            # The host already consumed the one-shot launch file. A stale copy
            # is harmless and can help diagnose unusual filesystem failures.
            pass

        sess = PtySession(
            id=sid,
            cmd=cmd,
            cwd=cwd,
            cols=cols,
            rows=rows,
            started_at=started_at,
            provider=provider,
            child_pid=int(state.get("child_pid") or 0) or None,
            host_pid=int(state.get("host_pid") or proc.pid),
            host_port=int(state.get("host_port") or 0) or None,
            host_token=token,
            host_answers_queries=bool(state.get("terminal_query_responder")),
            replay_truncated=_replay_meta_is_truncated(state),
            provider_session_id=resume_provider_session_id or resume_claude_session_id,
            claude_session_id=resume_claude_session_id,
            codex_log_cursor=codex_log_cursor,
        )
        async with self._lock:
            self._sessions[sid] = sess
        self._ensure_reaper_task()

        _upsert_meta_entry(sid, {
            "id": sid,
            "kind": "pty",
            "provider": provider,
            "cmd": cmd,
            "cwd": cwd,
            "cols": cols,
            "rows": rows,
            "started_at": started_at,
            "ended_at": None,
            "hosted": True,
            "host_pid": sess.host_pid,
            "host_port": sess.host_port,
            "child_pid": sess.child_pid,
            "provider_session_id": resume_provider_session_id or resume_claude_session_id,
            "provider_title": None,
            "claude_session_id": resume_claude_session_id,
            "active_plan": None,
            "has_user_turn": False,
            "last_submit_at": None,
            "resumed_from_claude_session_id": resume_claude_session_id,
            "resumed_from_provider_session_id": resume_provider_session_id,
            "resumed_from_pty_id": resumed_from_pty_id,
        }, preserve_existing=True)
        if provider == "codex":
            sess.provider_link_task = asyncio.create_task(
                self._provider_link_loop(sess),
                name=f"pty-provider-link-{sid}",
            )
        logger.info(
            "detached pty_session created id=%s host_pid=%s child_pid=%s cmd=%s",
            sid,
            sess.host_pid,
            sess.child_pid,
            cmd,
        )
        return sess

    async def resume(self, recoverable_id: str) -> PtySession:
        """Resume a verified Claude or Codex conversation in a fresh PTY."""
        store = _read_meta_store()
        requested_id = recoverable_id
        recoverable_id, entry = _latest_resume_entry(store, recoverable_id)
        if not entry:
            raise KeyError(f"no metadata for session {requested_id}")
        cmd = entry.get("cmd") or []
        provider = entry.get("provider") or (
            _provider_for_command_name(str(cmd[0])) if cmd else None
        )
        provider_session_id = entry.get("provider_session_id") or (
            entry.get("claude_session_id") if provider == "claude_code" else None
        )
        if not provider_session_id:
            raise RuntimeError(
                f"session {recoverable_id} has no provider_session_id "
                "(was it ever fully started?)"
            )
        current = self.get(recoverable_id)
        if current is not None and not current.closed:
            # Idempotent resume lets an old persisted browser tab follow its
            # replacement chain and reconnect to the already-live PTY.
            self._register_resume_aliases(store, requested_id, current.id)
            return current
        if recoverable_id not in {m["id"] for m in list_recoverable_sessions()}:
            raise RuntimeError(
                f"session {recoverable_id} is not a verified recoverable provider PTY"
            )
        cwd = entry.get("cwd") or os.getcwd()
        # we don't reuse the user's original cmd[0] — claude.cmd path may have moved
        resume_cmd = _resume_command(str(provider), str(provider_session_id))
        resumed = await self.create(
            cmd=resume_cmd,
            cwd=cwd,
            safe_mode=False,
            resume_claude_session_id=(
                str(provider_session_id) if provider == "claude_code" else None
            ),
            resume_provider_session_id=str(provider_session_id),
            resumed_from_pty_id=recoverable_id,
        )
        update_meta_field(recoverable_id, resumed_to=resumed.id)
        if requested_id != recoverable_id:
            update_meta_field(requested_id, resumed_to=resumed.id)
        self._register_resume_aliases(store, requested_id, resumed.id)
        return resumed

    async def attach(self, sid: str) -> tuple[PtySession, asyncio.Queue[Any], list[str]]:
        """Subscribe a client. Returns (session, queue, replay-snapshot)."""
        sess = self.get(sid)
        if sess is None or sess.closed:
            raise KeyError(sid)
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=1024)
        async with sess.stream_lock:
            snapshot = (
                await self._ensure_host_connected(sess)
                if sess.host_port
                else list(sess.ring)
            )
            sess.subscribers.add(q)
            if sess.input_controller is None:
                sess.input_controller = q
        return sess, q, snapshot

    def detach(self, sess: PtySession, q: asyncio.Queue[Any]) -> None:
        sess.subscribers.discard(q)
        sess.terminal_response_tails.pop(id(q), None)
        if sess.input_controller is q:
            sess.input_controller = None
        if not sess.subscribers:
            sess.last_detach_at = time.time()

    @staticmethod
    def _disconnect_slow_subscriber(
        sess: PtySession,
        queue: asyncio.Queue[Any],
    ) -> None:
        """Reconnect a slow viewer instead of leaving a silently frozen tab."""
        sess.subscribers.discard(queue)
        sess.terminal_response_tails.pop(id(queue), None)
        if sess.input_controller is queue:
            sess.input_controller = None
        try:
            while True:
                queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait({"type": "proxy_reconnect"})
        except asyncio.QueueFull:
            pass

    def claim_control(self, sess: PtySession, queue: asyncio.Queue[Any]) -> bool:
        if queue not in sess.subscribers:
            return False
        sess.input_controller = queue
        return True

    async def write(
        self,
        sid: str,
        data: str,
        *,
        source: asyncio.Queue[Any] | None = None,
    ) -> None:
        sess = self.get(sid)
        if sess is None or sess.closed:
            raise KeyError(sid)
        if source is not None:
            data, pending = _strip_browser_terminal_responses(
                data,
                sess.terminal_response_tails.pop(id(source), ""),
            )
            if pending:
                sess.terminal_response_tails[id(source)] = pending
            if not data:
                return
            self.claim_control(sess, source)
        launch_provider = _pending_launch_provider(sess, data)
        outgoing = _with_shell_launch_bypass(data, launch_provider)
        if sess.host_port:
            await self._send_host(sess, {"type": "input", "data": outgoing})
        elif sess.pty is not None:
            # winpty PTY.write expects str; runs in thread to avoid blocking loop.
            await asyncio.to_thread(sess.pty.write, outgoing)
        else:
            raise KeyError(sid)
        previous_provider = sess.provider
        previous_has_user_turn = sess.has_user_turn
        previous_last_submit_at = sess.last_submit_at
        previous_provider_title = sess.provider_title
        _note_user_input(sess, data)
        # Input arrives one xterm keystroke at a time. cc_sessions.json can be
        # several megabytes, so rewriting it for every character adds hundreds
        # of milliseconds to the websocket hot path. Persist only durable state
        # transitions (submit/Ctrl+C/provider launch), and perform that write off
        # the event loop so PTY output can continue streaming immediately.
        durable_updates: dict[str, Any] = {}
        if sess.has_user_turn != previous_has_user_turn:
            durable_updates["has_user_turn"] = sess.has_user_turn
        if sess.last_submit_at != previous_last_submit_at:
            durable_updates["last_submit_at"] = sess.last_submit_at or 0.0
        if sess.provider_title != previous_provider_title:
            durable_updates["provider_title"] = sess.provider_title
        if sess.provider != previous_provider:
            durable_updates["provider"] = sess.provider
            if sess.host_port:
                try:
                    await self._send_host(
                        sess,
                        {"type": "set_meta", "provider": sess.provider},
                    )
                except KeyError:
                    pass
            if sess.provider == "codex" and (
                sess.provider_link_task is None or sess.provider_link_task.done()
            ):
                sess.provider_link_task = asyncio.create_task(
                    self._provider_link_loop(sess),
                    name=f"pty-provider-link-{sess.id}",
                )
        if durable_updates:
            await asyncio.to_thread(update_meta_field, sess.id, **durable_updates)

    async def resize(
        self,
        sid: str,
        cols: int,
        rows: int,
        *,
        source: asyncio.Queue[Any] | None = None,
    ) -> None:
        sess = self.get(sid)
        if sess is None or sess.closed:
            raise KeyError(sid)
        if source is not None and sess.input_controller is not source:
            return
        cols = max(2, min(500, int(cols)))
        rows = max(1, min(200, int(rows)))
        if cols == sess.cols and rows == sess.rows:
            return
        if sess.host_port:
            await self._send_host(sess, {"type": "resize", "cols": cols, "rows": rows})
        elif sess.pty is not None:
            await asyncio.to_thread(sess.pty.set_size, cols, rows)
        else:
            raise KeyError(sid)
        sess.cols = cols
        sess.rows = rows

    async def redraw(
        self,
        sid: str,
        cols: int,
        rows: int,
        *,
        source: asyncio.Queue[Any] | None = None,
    ) -> None:
        """Ask a full-screen TUI to repaint after a truncated ANSI replay.

        The replay buffer intentionally has a byte cap, so a long-running
        alternate-screen application may lose the escape sequences that built
        its current screen. A short geometry nudge produces SIGWINCH in ConPTY;
        restoring the requested size makes the still-running application emit a
        fresh, self-contained screen without injecting keyboard input.
        """
        sess = self.get(sid)
        if sess is None or sess.closed:
            raise KeyError(sid)
        if source is not None and sess.input_controller is not source:
            return

        cols = max(2, min(500, int(cols)))
        rows = max(1, min(200, int(rows)))
        nudge_rows = rows - 1 if rows > 1 else min(200, rows + 1)

        async def set_size(target_rows: int) -> None:
            if sess.host_port:
                await self._send_host(
                    sess,
                    {"type": "resize", "cols": cols, "rows": target_rows},
                )
            elif sess.pty is not None:
                await asyncio.to_thread(sess.pty.set_size, cols, target_rows)
            else:
                raise KeyError(sid)

        await set_size(nudge_rows)
        try:
            await asyncio.sleep(0.04)
        finally:
            await set_size(rows)
        sess.cols = cols
        sess.rows = rows

    async def kill(self, sid: str) -> bool:
        sess = self.get(sid)
        if sess is None:
            return False

        # Persist the recovery tombstone before touching the PTY host. Browser
        # polls racing with a kill must never observe this conversation as a
        # recoverable background session, even while other tabs are attached.
        update_meta_field(
            sess.id,
            ended_at=time.time(),
            exit_reason="kill",
            termination_reason="killed",
            recovery_disabled=True,
            provider_session_id=sess.provider_session_id,
            claude_session_id=sess.claude_session_id,
            active_plan=sess.active_plan,
        )
        if sess.host_port:
            try:
                await self._send_host(sess, {"type": "kill"})
            except KeyError:
                pass
            # Marking ``closed`` stops the bridge reader, so notify attached
            # browsers here unless it already consumed the host's exit frame.
            if not sess.closed:
                for queue in list(sess.subscribers):
                    try:
                        queue.put_nowait({"type": "exit", "reason": "kill"})
                    except asyncio.QueueFull:
                        pass
                sess.closed = True
            async with self._lock:
                self._sessions.pop(sess.id, None)
            return True
        await self._close_session(sess, reason="kill")
        return True

    async def _reader_loop(self, sess: PtySession) -> None:
        """Pump PTY → subscribers + ring buffer until child exits.

        CC-PLAN-SESSION-CONTEXT 段五 (2026-05-05): chunk 热时连读排干 (最多
        READ_HOT_DRAIN_MAX 次), 把可能已经在 PTY buffer 里的连续字节合并到一帧.
        减 JSON 编码 + WS 帧次, 减重复 to_thread 切换开销.
        """
        try:
            while not sess.closed:
                try:
                    chunk = await asyncio.to_thread(sess.pty.read, False)
                except Exception as e:  # winpty.WinptyError, etc.
                    logger.warning("pty_session %s read error: %s", sess.id, e)
                    break
                if chunk:
                    # hot-drain: 不睡再多读几次, 合并 burst
                    for _ in range(READ_HOT_DRAIN_MAX):
                        try:
                            more = await asyncio.to_thread(sess.pty.read, False)
                        except Exception:
                            break
                        if not more:
                            break
                        chunk += more
                    async with sess.stream_lock:
                        sess.last_output_at = time.time()
                        _append_proxy_output(sess, chunk)
                        dead: list[asyncio.Queue[Any]] = []
                        for q in sess.subscribers:
                            try:
                                q.put_nowait(chunk)
                            except asyncio.QueueFull:
                                dead.append(q)
                        for q in dead:
                            self._disconnect_slow_subscriber(sess, q)
                else:
                    if not sess.pty.isalive():
                        # process exited; do one final drain attempt then stop
                        tail = await asyncio.to_thread(sess.pty.read, False)
                        if tail:
                            async with sess.stream_lock:
                                sess.last_output_at = time.time()
                                _append_proxy_output(sess, tail)
                                for q in sess.subscribers:
                                    try:
                                        q.put_nowait(tail)
                                    except asyncio.QueueFull:
                                        self._disconnect_slow_subscriber(sess, q)
                        break
                    await asyncio.sleep(READ_IDLE_SLEEP_S)
        finally:
            await self._close_session(sess, reason="reader-exit")

    async def _provider_link_loop(self, sess: PtySession) -> None:
        """Persist the native Codex thread currently displayed by this PTY."""
        try:
            while not sess.closed and sess.provider == "codex":
                (
                    sess.codex_log_cursor,
                    sess.codex_process_uuid,
                    sess.codex_thread_candidates,
                    provider_session_id,
                    provider_title,
                ) = await asyncio.to_thread(
                    _poll_codex_native_link,
                    after_id=sess.codex_log_cursor,
                    child_pid=sess.child_pid,
                    pty_id=sess.id,
                    process_uuid=sess.codex_process_uuid,
                    candidates=sess.codex_thread_candidates,
                )
                provider_title = _temporary_title_from_prompt(provider_title or "")
                link_changed = bool(
                    provider_session_id
                    and provider_session_id != sess.provider_session_id
                )
                title_changed = bool(
                    provider_title and provider_title != sess.provider_title
                )
                if provider_session_id and (link_changed or title_changed):
                    if link_changed:
                        sess.provider_session_id = provider_session_id
                        sess.summary_title = None
                    if provider_title:
                        sess.provider_title = provider_title
                    update_meta_field(
                        sess.id,
                        provider_session_id=provider_session_id,
                        provider_title=sess.provider_title,
                        codex_process_uuid=sess.codex_process_uuid,
                    )
                    logger.info(
                        "pty_session native Codex link id=%s provider_session_id=%s title=%r",
                        sess.id,
                        provider_session_id,
                        sess.provider_title,
                    )
                await asyncio.sleep(CODEX_LINK_POLL_S)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("Codex native-link watcher failed for %s", sess.id, exc_info=True)

    async def _close_session(self, sess: PtySession, *, reason: str) -> None:
        if sess.closed:
            return
        if sess.host_port:
            # Hosted sessions own their ConPTY and provider tree in a detached
            # process. Closing only the proxy leaves that entire tree running.
            # Explicitly tell the host to close before removing the proxy state.
            try:
                await self._send_host(sess, {"type": "kill"})
            except KeyError:
                logger.warning(
                    "detached pty host unreachable during close id=%s reason=%s",
                    sess.id,
                    reason,
                )
        sess.closed = True
        link_task = sess.provider_link_task
        if link_task is not None and link_task is not asyncio.current_task():
            link_task.cancel()
        for q in list(sess.subscribers):
            try:
                q.put_nowait({"type": "exit", "reason": reason})
            except asyncio.QueueFull:
                pass
        try:
            if sess.pty is not None and sess.pty.isalive():
                sess.pty.close() if hasattr(sess.pty, "close") else None
        except Exception:
            pass
        async with self._lock:
            self._sessions.pop(sess.id, None)

        # Mark terminated in the persistent store (don't delete — the session may
        # still be resumable via claude --resume <claude_session_id>).
        try:
            end_fields: dict[str, Any] = {
                "ended_at": time.time(),
                "exit_reason": reason,
                "provider_session_id": sess.provider_session_id,
                "claude_session_id": sess.claude_session_id,
                "active_plan": sess.active_plan,
            }
            if _is_explicit_kill_reason(reason):
                end_fields.update(
                    recovery_disabled=True,
                    termination_reason="killed",
                )
            update_meta_field(sess.id, **end_fields)
        except Exception as e:
            logger.warning("meta-store close-update failed: %s", e)
        logger.info("pty_session closed id=%s reason=%s", sess.id, reason)

    async def _reaper_loop(self) -> None:
        """Reap sessions only after both user detachment and output go idle."""
        try:
            while True:
                await asyncio.sleep(60)
                now = time.time()
                stale = [s for s in list(self._sessions.values()) if _is_idle_reap_candidate(s, now)]
                for s in stale:
                    await self._close_session(s, reason="idle-ttl")
        except asyncio.CancelledError:
            pass


def _quote_arg(a: str) -> str:
    """Minimal quoting for cmd-line args that may contain spaces."""
    if not a:
        return '""'
    if any(c in a for c in (" ", "\t", '"')):
        return '"' + a.replace('"', '\\"') + '"'
    return a


# Process-wide singleton; the FastAPI router creates it lazily.
_manager: PtyManager | None = None


def get_manager() -> PtyManager:
    global _manager
    if _manager is None:
        _manager = PtyManager()
    return _manager
