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
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import marshal
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
from typing import Any, Callable, Iterator, Mapping
import threading

from omnicompany.dashboard.session_workdir import resolve_session_cwd
from .latency_monitor import get_latency_monitor
from .provider_identity import (
    CLAUDE_FAMILY_PROVIDER_IDS,
    CODEX_FAMILY_PROVIDER_IDS,
    PTY_AGENT_PROVIDER_IDS,
    claude_home_for,
    codex_home_for,
    second_account_env,
)
from .provider_runtime import (
    PROVIDER_BUSY_HEARTBEAT_S,
    ProviderRuntimeState,
    provider_terminal_environment,
)

# P3.2: meta store 与 transcript 定位器拆为 exec 分片（命名空间与拆分前等价）
_PTY_PARTS_DIR = Path(__file__).with_name("pty_parts")
for _part in ("meta_store.py", "transcripts.py"):
    _part_path = _PTY_PARTS_DIR / _part
    exec(compile(_part_path.read_text(encoding="utf-8"), str(_part_path), "exec"))
del _PTY_PARTS_DIR, _part, _part_path

logger = logging.getLogger(__name__)
_PTY_HOST_DISCOVERY_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="cc-host-discovery",
)
_PTY_IO_EXECUTOR = ThreadPoolExecutor(
    max_workers=16,
    thread_name_prefix="cc-pty-io",
)


async def _pty_io(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Keep in-process ConPTY reads/writes out of the shared executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _PTY_IO_EXECUTOR,
        lambda: fn(*args, **kwargs),
    )
# Cache for the P4.2b archive file only (see _read_meta_store_with_archive):
# keyed purely on the archive file's own (mtime, size, ino), independent of
# the hot store's cache key. It changes at most once a day, so this makes a
# recovery-list read that would otherwise re-parse it every call effectively
# free once stable. Deliberately *not* combined into a single merged-result
# cache keyed off `_META_STORE_CACHE_KEY` — that global is only updated by the
# real `_read_meta_store()` body, so a caller (test or otherwise) that
# monkeypatches `_read_meta_store` itself would desync the two, serving a
# stale merged result from an unrelated earlier call.

# Session lives this long after both its last subscriber leaves and its output
# goes quiet. A closed dashboard tab is only a detach; overnight / cross-device
# work must remain resumable. Operators can shorten this explicitly if needed.
IDLE_TTL_S = float(os.environ.get("OMNI_PTY_IDLE_TTL_S") or 30 * 24 * 60 * 60)
# A session a *program* opened is a different promise from one a person opened.
# Nobody is coming back to a worker some script spawned, so the 30-day grace that
# protects human work turns into an unbounded leak: measured 33 creations in one
# hour, most abandoned. Agent-created workers therefore carry their own short
# idle TTL and are reclaimed automatically once genuinely quiet.
AGENT_WORKER_IDLE_TTL_S = float(
    os.environ.get("OMNI_PTY_AGENT_IDLE_TTL_S") or 30 * 60
)
# How long after the deadline passes before the reminder is logged again.
RECLAIM_REMINDER_INTERVAL_S = float(
    os.environ.get("OMNI_PTY_RECLAIM_REMINDER_INTERVAL_S") or 10 * 60
)
#: Sessions opened by a human in the dashboard. Everything else is ``agent``:
#: an unrecognized caller must be reclaimable, never immortal.
SESSION_ORIGIN_HUMAN = "human"
SESSION_ORIGIN_AGENT = "agent"
# Do not trade away normal CLI usage for performance. Operators may opt into a
# host-specific cap through the environment, while the default remains
# unlimited; memory/CPU pressure is handled by bounded replay buffers, detached
# hosts and UI rendering limits instead of refusing new sessions.
MAX_LIVE_SESSIONS = max(0, int(os.environ.get("OMNI_PTY_MAX_LIVE_SESSIONS") or 0))
# Per-session output ring buffer cap (chunks, not bytes).
RING_CAP = int(os.environ.get("OMNI_PTY_PROXY_RING_CHUNKS") or 50_000)
RING_BYTE_CAP = int(os.environ.get("OMNI_PTY_PROXY_RING_BYTES") or 256 * 1024)
REPLAY_TRUNCATION_MARGIN_BYTES = int(
    os.environ.get("OMNI_PTY_REPLAY_TRUNCATION_MARGIN_BYTES") or 64 * 1024
)
# PTY-RELOAD-CRAWL (2026-08-18): agent TUIs (codex family in particular)
# answer any SIGWINCH by wiping the screen and re-printing their entire
# transcript. Every geometry nudge therefore costs a full-transcript re-print
# fanned out to all subscribers, so nudges closer together than this floor are
# dropped — the earlier re-print already carries the same screen content.
REDRAW_MIN_INTERVAL_S = float(
    os.environ.get("OMNI_PTY_REDRAW_MIN_INTERVAL_S") or 1.5
)
# After a nudge (or a real resize) the re-print arrives as hundreds of
# line-sized chunks over 1s+ (measured idle: ~200 chunks / 1.3s for codex).
# For this window the WS fanout coalesces output into a few large frames so
# the repaint applies atomically instead of crawling in line by line.
REDRAW_BURST_WINDOW_S = float(
    os.environ.get("OMNI_PTY_REDRAW_BURST_WINDOW_S") or 3.0
)
# PERF-OBSERVATORY P4.1 backpressure: a subscriber whose queue fills up is
# paused (future chunks skipped for it, ring keeps accumulating regardless)
# instead of disconnected. Once its queue drains to this many items it gets
# one inline ring resync instead of every chunk it missed while paused.
SUBSCRIBER_RESYNC_LOW_WATER = int(
    os.environ.get("OMNI_PTY_SUBSCRIBER_RESYNC_LOW_WATER") or 64
)
# A subscriber that stays paused this long without draining is treated as
# genuinely dead (frozen tab, crashed browser) rather than merely slow, and is
# still force-reset via the original kick path as a last resort.
SUBSCRIBER_OVERFLOW_KICK_TIMEOUT_S = float(
    os.environ.get("OMNI_PTY_SUBSCRIBER_OVERFLOW_KICK_TIMEOUT_S") or 30.0
)
# How often the read loop polls when the PTY has no data.
# CC-PLAN-SESSION-CONTEXT 段五 (2026-05-05): 20ms → 2ms. baseline bench 显示
# 端到端 echo 延迟 p50 30.8ms, 主要是这里. 改 2ms 后 p50 < 10ms 预期.
# CPU 代价: 空轮询从 50/s → 500/s, 每次 to_thread + read syscall 仍可控.
# 2026-07 P0 性能批: 2ms → 10ms. 2ms 空轮询在 N 个空闲 PTY 会话下每秒数千次
# to_thread 切换, 持续抢占事件循环; echo 热路径有 READ_HOT_DRAIN_MAX 连读兜底,
# 10ms 对交互延迟无感 (人眼阈值 ~50ms), CPU 占用降 5 倍.
READ_IDLE_SLEEP_S = 0.01

# ── auto-continue: 传输层瞬时失败自动续跑 ──────────────────────────────────
# CLI 偶发会在半路被传输层错误打断（实测例：kiro 因多进程 token 刷新竞争返回
# 403 "The bearer token included in the request is invalid."）。这类失败重发一次
# 即恢复，不是身份失效，所以读循环里认到错误行就自动补一条"继续"。
# 连续最多 AUTO_CONTINUE_MAX 次；任何人工输入都会把计数清零。设 0 关闭。
AUTO_CONTINUE_MAX = int(os.environ.get("OMNI_PTY_AUTO_CONTINUE_MAX") or 3)
AUTO_CONTINUE_DELAY_S = float(os.environ.get("OMNI_PTY_AUTO_CONTINUE_DELAY_S") or 5.0)
AUTO_CONTINUE_TEXT = os.environ.get("OMNI_PTY_AUTO_CONTINUE_TEXT") or "继续"
# 只保留尾窗做匹配，避免整个 ring 反复命中同一条旧错误。
AUTO_CONTINUE_TAIL_CAP = 4096
AUTO_CONTINUE_PATTERNS = (
    "prompt failed",
    "error in the response stream",
    "bearer token included in the request is invalid",
    "accessdeniedexception",
    "throttlingexception",
    "serviceunavailable",
    "internalservererror",
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _strip_ansi_for_match(text: str) -> str:
    """去掉 ANSI 控制序列，让全屏 TUI 的输出也能做纯文本匹配。"""
    return _ANSI_ESCAPE_RE.sub("", text)
# Reader hot-drain: 拿到非空 chunk 后, 不睡接连再 read 几次, 把已 buffer 的字节
# 一次取完, 一帧 fan out. 减 JSON 编码 + WS 帧次, 减重复 to_thread 切换.
READ_HOT_DRAIN_MAX = 4
CODEX_LINK_POLL_S = float(os.environ.get("OMNI_CODEX_LINK_POLL_S") or 2.0)
CODEX_LINK_STABLE_POLL_S = max(
    CODEX_LINK_POLL_S,
    float(os.environ.get("OMNI_CODEX_LINK_STABLE_POLL_S") or 30.0),
)
ZCODE_LINK_POLL_S = max(
    0.25,
    float(os.environ.get("OMNI_ZCODE_LINK_POLL_S") or 1.0),
)
ZCODE_LINK_WINDOW_S = max(
    2.0,
    float(os.environ.get("OMNI_ZCODE_LINK_WINDOW_S") or 45.0),
)
KIRO_LINK_POLL_S = max(
    0.25,
    float(os.environ.get("OMNI_KIRO_LINK_POLL_S") or 1.0),
)
# Discovery needs to notice the first native Kiro session quickly.  Once that
# authoritative lock has been bound, polling only refreshes a small title file
# and can be much less frequent without affecting terminal runtime state.
KIRO_LINK_STABLE_POLL_S = max(
    KIRO_LINK_POLL_S,
    float(os.environ.get("OMNI_KIRO_LINK_STABLE_POLL_S") or 5.0),
)
HOST_RUNTIME_CONNECT_BACKOFF_S = max(
    5.0,
    float(os.environ.get("OMNI_HOST_RUNTIME_CONNECT_BACKOFF_S") or 60.0),
)

# Compatibility for callers that imported the old name. Runtime state no longer
# follows arbitrary output timestamps; only provider markers refresh this TTL.
PTY_WORKING_WINDOW_S = PROVIDER_BUSY_HEARTBEAT_S
TEMPORARY_SESSION_TITLE_MAX_CHARS = 80
TEMPORARY_SESSION_TITLE_CAPTURE_MAX_CHARS = 400
PTY_COMPANION_FALLBACK_PROVIDER_IDS = PTY_AGENT_PROVIDER_IDS - {
    "claude_code",
    "claude_2",
    "codex",
    "codex_the_company",
    "codex_2",
    "kimi",
}
_NATIVE_LINK_PROVIDER_IDS = frozenset(
    {"codex", "codex_the_company", "codex_glm", "codex_2", "kiro", "zcode"}
)
# Keyboard escape sequences (arrows, function keys, bracketed-paste markers)
# are terminal navigation, not prompt text. Strip them before deciding whether
# the provider-specific submit key completed a real turn.
_TERMINAL_INPUT_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|.?)")
_TERMINAL_RESPONSE_RE = re.compile(
    r"(?:"
    r"\x1b\](?:10|11|12);rgb:[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}(?:\x07|\x1b\\)"
    r"|\x1b\]4;[0-9]+;rgb:[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}(?:\x07|\x1b\\)"
    r"|\x1b\[\?(?:[0-9;]+)c"
    r"|\x1b\[>(?:[0-9;]*)c"
    r"|\x1b\[(?:0n|[0-9]+;[0-9]+R)"
    r"|\x1b\[\?[0-9]+;[0-9]+\$y"
    r")+"
)
# 浏览器 xterm 会逐条回答 OSC 4 调色板查询 (形如 \x1b]4;0;rgb:0909/1a1a1a\x1b\),
# 一条完整响应可能跨 WS 帧。用这个宽松前缀判断把没结尾的响应整段留到下一帧。
_OSC4_PARTIAL_RE = re.compile(
    r"\x1b\]4;[0-9]{0,4}"
    r"(?:;rgb:[0-9a-fA-F]{0,4}(?:/[0-9a-fA-F]{0,4}){0,2})?"
    r"(?:\x1b)?"
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
        # A standalone ESC is a complete keyboard input, not sufficient evidence
        # of an OSC reply. Holding it as a prefix waits forever for another key
        # and breaks Escape/Alt+Escape on every browser terminal. Recognized OSC
        # bodies above this tail can still retain their split ST terminator.
        if candidate == "\x1b":
            continue
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
        osc4_partial = bool(_OSC4_PARTIAL_RE.fullmatch(candidate))
        if osc_partial or osc_body or csi_partial or osc4_partial:
            return value[:start], candidate
    return value, ""
# Default terminal geometry.
DEFAULT_COLS = 120
DEFAULT_ROWS = 32


def session_current_owner(sess: "PtySession") -> str:
    from omnicompany.packages.services._core.identity.ownership import current_owner

    return current_owner(sess.id, sess.provider_session_id, sess.origin)


def session_idle_ttl_s(sess: "PtySession") -> float:
    """Idle grace follows current ownership; creation ancestry remains historical."""
    return (
        AGENT_WORKER_IDLE_TTL_S
        if session_current_owner(sess) == SESSION_ORIGIN_AGENT
        else IDLE_TTL_S
    )


def _is_idle_reap_candidate(sess: "PtySession", now: float) -> bool:
    # A provider that is still publishing a busy marker is doing work no matter
    # how the session was opened, and no matter that nobody is watching. Output
    # quiet is not the same as idle: a long tool call prints nothing.
    if sess.provider_runtime.working(now=now):
        return False
    ttl = session_idle_ttl_s(sess)
    return bool(
        not sess.subscribers
        and sess.last_detach_at
        and (now - sess.last_detach_at) > ttl
        and (now - sess.last_output_at) > ttl
    )


def _persist_last_detach_at(sess: "PtySession") -> None:
    """Record "the last viewer left at T" in the durable session registry.

    The reaper only counts idleness from a real detach (``last_detach_at``).
    A replacement daemon must be able to tell "never watched" apart from
    "watched, then left"; otherwise it has to guess, and the old guess (stamp
    every rehydrated session as just detached) reaped never-watched agent
    workers 30 minutes after each ``omni cc daemon restart``. The write is
    monotonic, skips unknown ids, and runs off the event loop because the
    registry is a multi-MB JSON file.
    """
    stamp = float(sess.last_detach_at or 0.0)
    if not stamp:
        return
    sid = sess.id

    def mutate(store: dict[str, dict[str, Any]]) -> bool:
        current = store.get(sid)
        if current is None:
            return False
        if float(current.get("last_detach_at") or 0.0) >= stamp:
            return False
        store[sid] = {**current, "last_detach_at": stamp}
        return True

    def write() -> None:
        try:
            _mutate_meta_store(mutate)
        except Exception as exc:  # noqa: BLE001 - bookkeeping must not break detach
            logger.warning("meta-store last_detach_at update failed for %s: %s", sid, exc)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        write()
        return
    loop.run_in_executor(None, write)


def _rehydrated_last_detach_at(meta: dict[str, Any], now: float) -> float:
    """``last_detach_at`` for a session a replacement daemon takes over.

    Restart must not start a reclaim countdown. A session no viewer ever left
    stays at 0 (never reaped, only reminded), exactly as before the restart.
    A session that was watched at some point gets ``now``: ``omni cc daemon
    restart`` hard-terminates the old process, so a viewer still attached at
    that moment left no detach record, and restoring an older stamp could reap
    it on the first reaper tick. ``now`` never shortens the pre-restart window.
    """
    return now if float(meta.get("last_detach_at") or 0.0) else 0.0


def session_reclaim_state(sess: "PtySession", now: float) -> dict[str, Any]:
    """The reclaim contract for one session, as reported back to its creator."""
    owner = session_current_owner(sess)
    if owner != SESSION_ORIGIN_AGENT:
        return {"required": False, "origin": owner}
    ttl = session_idle_ttl_s(sess)
    quiet_since = max(sess.last_detach_at or sess.started_at, sess.last_output_at)
    return {
        "required": True,
        "origin": owner,
        "ttl_seconds": ttl,
        "deadline": quiet_since + ttl,
        "overdue": (now - quiet_since) > ttl,
        "how": f"DELETE /cc/sessions/{sess.id}  ·  omni cc session kill {sess.id}",
        "note": (
            "你(或你的程序)开的 Agent Worker。用完请自己回收；"
            f"闲置超过 {int(ttl // 60)} 分钟且 provider 未在运行时会被自动回收。"
        ),
    }




# PERF-OBSERVATORY P4.2b: sessions that ended more than this many days ago move
# out of the hot store into `_meta_store_archive_path()`. The main store is
# read/written on every PTY create/close and every OmniChat persist tick, so
# keeping it bounded to "recent + still alive" keeps that read-modify-write
# cheap regardless of how much history has accumulated.
META_STORE_ARCHIVE_AFTER_DAYS = float(
    os.environ.get("OMNI_CC_SESSIONS_ARCHIVE_DAYS") or 7
)
# How often the reaper loop's piggybacked maintenance tick re-checks for
# archivable sessions. Deliberately not a dedicated task/thread.
META_STORE_ARCHIVE_CHECK_INTERVAL_S = float(
    os.environ.get("OMNI_CC_SESSIONS_ARCHIVE_CHECK_INTERVAL_S") or 24 * 60 * 60
)




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
    # Upgrade a pre-runtime-state host from the daemon's already interpreted
    # live stream without replacing or terminating that user's PTY.
    payload.update(sess.provider_runtime.export())
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
    # PERF-OBSERVATORY P4.1: monotonic seq assigned to each ring chunk on
    # append, never reset for the session's in-process lifetime (a daemon
    # restart is a new PtySession instance and legitimately restarts at 0 —
    # a reconnecting client's stale last_seq then simply misses the window
    # and falls back to a full snapshot, see PtyManager._resume_decision).
    next_seq: int = field(default=0, repr=False)
    # PTY-RELOAD-CRAWL (2026-08-18): agent TUIs answer a resize/redraw nudge by
    # wiping the screen (CSI 2J/3J) and re-printing their whole transcript as
    # hundreds of line-sized chunks over seconds (codex: ~200 chunks / ~100KB
    # measured). While ``output_burst_until`` (time.monotonic) is in the
    # future, the WS fanout coalesces those chunks into a few large frames so
    # viewers get one atomic repaint instead of a line-by-line crawl.
    output_burst_until: float = field(default=0.0, repr=False)
    # Session-wide floor between geometry nudges (time.monotonic). Stacked
    # triggers (truncated replay + visibility reveal + several tabs) each cost
    # a full transcript re-print that fans out to every subscriber.
    last_redraw_at: float = field(default=0.0, repr=False)
    # One pending repaint deferred to the end of the floor window (see
    # PtyManager.redraw ``ensure``). A repaint that a viewer depends on must be
    # coalesced, never dropped.
    trailing_redraw_task: asyncio.Task | None = field(default=None, repr=False)
    # id(queue) -> time.time() when that subscriber's queue first hit
    # QueueFull. A key present means "currently paused": no further chunks
    # are enqueued for it until it drains (see PtyManager._publish_output).
    subscriber_overflow_since: dict[int, float] = field(default_factory=dict, repr=False)
    # id(queue) -> watchdog task. 恢复/强踢不能只挂在生产端下一次输出上:
    # 爆发后安静的会话若无此看门狗, 被暂停的订阅者会永久冻结(用户被迫重开页签)。
    subscriber_overflow_watchdogs: dict[int, asyncio.Task] = field(default_factory=dict, repr=False)
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
    # auto-continue 状态：连续自动续跑次数 / 匹配用的输出尾窗 / 延迟注入任务。
    auto_continue_count: int = field(default=0, repr=False)
    auto_continue_tail: str = field(default="", repr=False)
    auto_continue_task: asyncio.Task | None = field(default=None, repr=False)
    pending_command_is_bare: bool = field(default=True, repr=False)
    provider: str = "shell"
    #: Who opened this session — see ``SESSION_ORIGIN_*``. Defaults to ``agent``
    #: so a caller that never identified itself is still reclaimable.
    origin: str = SESSION_ORIGIN_AGENT
    #: visible = normal list; hidden = omit from multiagent; background = list last.
    visibility: str = "visible"
    #: Last time the overdue-reclaim reminder was logged for this session.
    reclaim_reminded_at: float = field(default=0.0, repr=False)
    closed: bool = False
    child_pid: int | None = None
    host_pid: int | None = None
    host_port: int | None = None
    host_token: str | None = field(default=None, repr=False)
    host_writer: asyncio.StreamWriter | None = field(default=None, repr=False)
    host_reader_task: asyncio.Task | None = field(default=None, repr=False)
    host_connect_task: asyncio.Task | None = field(default=None, repr=False)
    host_connect_retry_at: float = field(default=0.0, repr=False)
    input_controller: asyncio.Queue[Any] | None = field(default=None, repr=False)
    host_answers_queries: bool = field(default=False, repr=False)
    # Whether the connected host advertises a screen model (see pty_screen).
    # Old hosts never do; the daemon then never sends "screen" requests, so
    # sessions on pre-upgrade hosts keep byte-identical attach behavior.
    host_screen_model: bool = field(default=False, repr=False)
    screen_req_seq: int = field(default=0, repr=False)
    screen_pending: dict[int, asyncio.Future] = field(default_factory=dict, repr=False)
    terminal_query_responder: Any = field(default=None, repr=False)
    terminal_response_tails: dict[int, str] = field(default_factory=dict, repr=False)
    latency_pending_input_ns: int = field(default=0, repr=False)
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
    # Dashboard input is not the only way a provider-backed PTY can receive a
    # turn: Codex may be driven from its native app while the same ConPTY is
    # mirrored here.  The native-link watcher establishes this bit only after
    # the linked rollout advances beyond its first observed mtime, so startup
    # paint never masquerades as agent work.
    provider_activity_seen: bool = field(default=False, repr=False)
    codex_rollout_mtime: float = field(default=0.0, repr=False)
    provider_runtime: ProviderRuntimeState = field(init=False, repr=False)
    #: Agent Worker 主题(如 'assistant'); shell 会话为 None。
    topic: str | None = None
    #: 费用问题(欠费/配额/余额类报错)在输出流里被识别后置真, 前端据此弹切换按钮。
    billing_issue: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self.provider_runtime = ProviderRuntimeState(self.provider)

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
        # A PTY is working only after a real turn was observed and while its
        # provider is refreshing an explicit busy marker. Ordinary TUI repaint,
        # log growth, resize traffic and browser attachment are insufficient.
        turn_observed = bool(
            (self.has_user_turn and self.last_submit_at)
            or self.provider_activity_seen
        )
        now = time.time()
        working = bool(alive and turn_observed and self.provider_runtime.working(now=now))
        runtime_known = self.provider_runtime.known()
        state = "working" if working else "idle" if alive and runtime_known else "unknown"
        marker_at = self.provider_runtime.marker_at or None
        observed_at = now
        return {
            "id": self.id,
            "session_id": self.id,
            "pty_id": self.id,
            "cmd": self.cmd,
            "cwd": self.cwd,
            "cols": self.cols,
            "rows": self.rows,
            "started_at": self.started_at,
            "startedAt": self.started_at,
            "alive": alive,
            "process_alive": alive,
            "processAlive": alive,
            "observed_at": observed_at,
            "observedAt": observed_at,
            "source": "ccdaemon.pty",
            "working": working,
            "working_known": runtime_known,
            "workingKnown": runtime_known,
            "state": state,
            "state_evidence_at": marker_at or self.last_submit_at or self.last_output_at,
            "stateEvidenceAt": marker_at or self.last_submit_at or self.last_output_at,
            "working_source": self.provider_runtime.source if working else None,
            "provider_runtime_state": self.provider_runtime.status(now=now),
            "provider_runtime_marker_at": marker_at,
            "provider_runtime_known": runtime_known,
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
            "lastOutputAt": self.last_output_at,
            "last_detach_at": self.last_detach_at or None,
            "last_activity_at": max(
                (value for value in (self.last_output_at, self.last_submit_at) if value),
                default=self.started_at,
            ),
            "lastActivityAt": max(
                (value for value in (self.last_output_at, self.last_submit_at) if value),
                default=self.started_at,
            ),
            "providerSessionId": self.provider_session_id,
            "origin": session_current_owner(self),
            "lifecycle_owner": session_current_owner(self),
            "creation_origin": self.origin,
            "visibility": self.visibility,
            "visible": self.visibility != "hidden",
            "reclaim": session_reclaim_state(self, now),
            "provider_session_id": self.provider_session_id,
            "provider_title": self.provider_title,
            "summary_title": self.summary_title,
            "display_title": self.summary_title or self.provider_title,
            "claude_session_id": self.claude_session_id,
            "active_plan": self.active_plan,
            "topic": self.topic,
            "billing_issue": self.billing_issue,
            "status": "alive" if alive else "ended" if self.closed else "unknown",
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


def _build_proxy_replay(chunks: list[str]) -> tuple[deque[str], int, bool]:
    """Build a bounded replay without mutating live session state."""
    ring: deque[str] = deque(maxlen=RING_CAP)
    ring_bytes = 0
    replay_truncated = False
    for chunk in chunks:
        if ring.maxlen and len(ring) >= ring.maxlen:
            removed = ring.popleft()
            ring_bytes -= len(removed.encode("utf-8", errors="replace"))
            replay_truncated = True
        ring.append(chunk)
        ring_bytes += len(chunk.encode("utf-8", errors="replace"))
        while ring and ring_bytes > RING_BYTE_CAP:
            removed = ring.popleft()
            ring_bytes -= len(removed.encode("utf-8", errors="replace"))
            replay_truncated = True
    return ring, ring_bytes, replay_truncated


def _replace_proxy_replay(sess: PtySession, chunks: list[str]) -> list[str]:
    """Load a host snapshot into the byte-bounded browser replay window."""
    ring, ring_bytes, replay_truncated = _build_proxy_replay(chunks)
    sess.ring = ring
    sess.ring_bytes = ring_bytes
    sess.replay_truncated = replay_truncated
    return list(sess.ring)


def _provider_for_command_name(command: str) -> str | None:
    return {
        "claude": "claude_code",
        "codex": "codex",
        "gemini": "gemini",
        "gemini.cmd": "gemini",
        "gemini.ps1": "gemini",
        "agy": "antigravity",
        "agy.exe": "antigravity",
        "kimi": "kimi",
        "kimi-code": "kimi",
        "opencode": "opencode",
        "kiro-cli": "kiro",
        "qodercli": "qoder",
        "qoderclicn": "qoder",
        "codebuddy": "codebuddy",
        "cbc": "codebuddy",
        "dsh": "dsh",
        "dsh.cmd": "dsh",
        "zcode": "zcode",
        "zcode.cmd": "zcode",
        "grok": "grok",
        "grok.exe": "grok",
        "powershell": "shell",
        "pwsh": "shell",
        "cmd": "shell",
    }.get(_command_name(command))


def _env_selects_second_codex_account(env: Mapping[str, str] | None) -> bool:
    """Whether a child environment points Codex at the second official account.

    ``codex_2`` is the one Codex variant with nothing to detect on argv: a second
    official account is purely a different ``CODEX_HOME`` (the whole credential
    lives in ``$CODEX_HOME/auth.json``), so the environment is the only honest
    discriminator. Compare resolved paths rather than strings so a trailing
    separator or different casing on Windows still matches.
    """
    if not env:
        return False
    raw = str(env.get("CODEX_HOME") or "").strip()
    if not raw:
        return False
    try:
        candidate = Path(raw).resolve()
        expected = codex_home_for("codex_2").resolve()
    except OSError:
        return False
    return candidate == expected


def _env_selects_second_claude_account(env: Mapping[str, str] | None) -> bool:
    """Whether a child environment points Claude Code at the second account.

    Same shape as ``_env_selects_second_codex_account``: ``claude_2`` has
    nothing to detect on argv (identical ``claude`` command line), because a
    second official account is purely a different ``CLAUDE_CONFIG_DIR`` — the
    whole credential lives in ``$CLAUDE_CONFIG_DIR/.credentials.json``.
    """
    if not env:
        return False
    raw = str(env.get("CLAUDE_CONFIG_DIR") or "").strip()
    if not raw:
        return False
    try:
        candidate = Path(raw).resolve()
        expected = claude_home_for("claude_2").resolve()
    except OSError:
        return False
    return candidate == expected


def _provider_for_command(
    cmd: list[str], extra_env: Mapping[str, str] | None = None
) -> str:
    """Detect the runtime provider for a full command line.

    ``codex_the_company`` sessions run the same Codex CLI as the official
    subscription, but carry the process-local the_company provider overrides
    (``-c model_provider="the_company"``) built by the agent control plane. The PTY
    protocol (Tab submit, native link, title spinners) is identical to codex,
    while the distinct provider name lets the UI show the_company branding.

    ``codex_2`` is the same official Codex subscription under a second account.
    It carries no argv override at all, so it is detected from ``extra_env``
    (``CODEX_HOME``). Callers that cannot supply the environment — legacy
    metadata repair paths — fall back to plain ``codex``; the persisted provider
    stays authoritative there, so an already-classified session is not demoted.

    ``dsh`` sessions run the DeepSeek Harness PTY adapter
    (config/agents/dsh/dsh-pty.py) under the default Python interpreter. The
    command line carries the ``--omni-dsh-pty`` provider marker so the pane is
    classified as dsh instead of a plain shell.
    """
    provider = _provider_for_command_name(cmd[0]) or "shell"
    if provider == "codex" and 'model_provider="the_company"' in cmd[1:]:
        return "codex_the_company"
    # 同一把 Codex CLI, 进程级覆盖指向 Z.ai 的 GLM Coding Plan 端点。
    if provider == "codex" and 'model_provider="zai"' in cmd[1:]:
        return "codex_glm"
    if provider == "codex" and _env_selects_second_codex_account(extra_env):
        return "codex_2"
    if provider == "claude_code" and _env_selects_second_claude_account(extra_env):
        return "claude_2"
    if provider == "shell" and "--omni-dsh-pty" in cmd[1:]:
        return "dsh"
    return provider


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
    command_name = {
        "claude_code": "claude",
        "claude_2": "claude",
        "kiro": "kiro-cli",
        "qoder": "qodercli",
    }.get(provider, provider)
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


# A TUI that queries the terminal for colors before its stdin reader starts can
# leave the reply unconsumed; the next real input then merges the reply bytes
# into the first user turn. Providers that strip ESC bytes from their native
# thread title leave these ESC-less residuals behind, which the raw escape
# regexes above can no longer match. They can only appear as a leading block.
_ESC_LESS_TERMINAL_RESPONSE_LEAD_RE = re.compile(
    r"^(?:"
    r"\]1[012];rgb:[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}\\?"
    r"|\]4;[0-9]+;rgb:[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}\\?"
    r")+"
)


def _sanitize_provider_title(title: str | None) -> str:
    """Clean a provider-adopted native conversation title for display.

    Provider-native titles are taken from the provider's own store (Codex
    ``state_5.sqlite``, Kiro session records) and may embed terminal
    query-reply payloads that leaked into the first user turn. Strip the raw
    terminal responses and their ESC-less residuals before bounding the result.
    """
    cleaned = _TERMINAL_RESPONSE_RE.sub("", title or "")
    cleaned = _ESC_LESS_TERMINAL_RESPONSE_LEAD_RE.sub("", cleaned)
    return _temporary_title_from_prompt(cleaned)


def _note_user_input(sess: PtySession, data: str, *, now: float | None = None) -> str:
    """Track real prompt submissions and publish a first-turn fallback title.

    xterm forwards individual keystrokes. Typing, resize/output, an empty submit,
    and launching an agent CLI from the plain shell must not start the working
    indicator. Only a later non-empty prompt submitted to the detected agent
    counts as a turn. Codex currently uses Tab to submit; other supported CLIs
    use Enter. The first pending prompt is retained only up to a small bound and
    becomes the temporary title on submit; Ctrl+C discards it.

    Terminal responses (OSC 4/10/11/12 颜色查询回执等) 也会走输入通道 —— 它们不是
    用户敲的字, 必须先洗掉, 否则 opencode 这类会查调色板的 TUI 会把
    "4;0;rgb:0909/1a1a…" 灌进临时会话标题。
    """
    cleaned = _TERMINAL_INPUT_ESCAPE_RE.sub(
        "",
        _TERMINAL_RESPONSE_RE.sub("", data),
    )
    submitted_at = time.time() if now is None else now
    submitted_prompt = ""
    for char in cleaned:
        if char == "\x03":
            sess.pending_input_len = 0
            sess.pending_title_input = ""
            sess.pending_command_token = ""
            sess.pending_command_is_bare = True
            sess.last_submit_at = 0.0
            sess.provider_runtime.mark_idle("user:interrupt", now=submitted_at)
            continue
        # Tab submits for every Codex variant — they are one CLI, so key off the
        # family set rather than a hand-listed tuple that a new variant can miss.
        if char in "\r\n" or (char == "\t" and sess.provider in CODEX_FAMILY_PROVIDER_IDS):
            launch_provider = (
                _provider_for_command_name(sess.pending_command_token)
                if sess.provider == "shell" and sess.pending_command_is_bare
                else None
            )
            if launch_provider:
                sess.provider = launch_provider
                sess.provider_runtime.set_provider(launch_provider)
                sess.has_user_turn = False
                sess.last_submit_at = 0.0
            elif sess.pending_input_len > 0 and sess.provider != "shell":
                submitted_prompt = _temporary_title_from_prompt(
                    sess.pending_title_input
                )
                if not sess.has_user_turn and not sess.provider_title:
                    if submitted_prompt:
                        sess.provider_title = submitted_prompt
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
                len(sess.pending_title_input) < TEMPORARY_SESSION_TITLE_CAPTURE_MAX_CHARS
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
    return submitted_prompt


def _capture_session_companion_turn(sess: PtySession, prompt: str) -> dict[str, Any]:
    """Capture a real PTY submit for providers that do not expose native hooks."""

    if sess.provider not in PTY_COMPANION_FALLBACK_PROVIDER_IDS or not prompt.strip():
        return {"captured": False, "reason": "native_hook_or_empty"}
    try:
        from omnicompany.packages.services._core.session_companion import CompanionStore

        value = CompanionStore().capture_user_turn(sess.id, prompt)
        return {"captured": bool(value), "trace_id": sess.id}
    except Exception as exc:  # noqa: BLE001 - terminal input must remain fail-open
        logger.warning(
            "session companion PTY turn capture failed id=%s provider=%s: %s",
            sess.id,
            sess.provider,
            exc,
        )
        return {"captured": False, "reason": f"{type(exc).__name__}: {exc}"}


def _enqueue_session_companion_fallback(
    sess: PtySession,
    *,
    reason: str,
) -> dict[str, Any]:
    """Enqueue a bounded Stop event when a CLI runtime has no native hook."""

    if sess.provider not in PTY_COMPANION_FALLBACK_PROVIDER_IDS or not sess.has_user_turn:
        return {"created": False, "reason": "native_hook_or_no_user_turn"}
    try:
        from omnicompany.packages.services._core.identity import get_session_binding
        from omnicompany.packages.services._core.session_companion import CompanionStore
        from omnicompany.packages.services._core.session_companion.service import (
            enqueue_lifecycle_event,
        )

        store = CompanionStore()
        turn = store.user_turn(sess.id)
        latest_user = str(turn.get("latest_user_input") or "").strip()
        first_user = str(turn.get("first_user_input") or "").strip()
        if not (latest_user or first_user):
            return {"created": False, "reason": "captured_turn_missing"}
        binding = get_session_binding(sess.id)
        bound_native_id = str(binding.get("session_id") or "").strip()
        assistant_output = _companion_assistant_output(
            sess,
            native_session_id=bound_native_id or sess.provider_session_id,
        )
        return enqueue_lifecycle_event(
            trigger="stop",
            trace_id=sess.id,
            provider=sess.provider,
            # The companion search path resolves the provider-native transcript
            # from event.session_id. Prefer the binding's verified native id;
            # the PTY id is only an observability trace id.
            session_id=bound_native_id or str(sess.provider_session_id or sess.id),
            cwd=sess.cwd,
            first_user_input=first_user or latest_user,
            user_input=latest_user or first_user,
            assistant_output=assistant_output,
            project=str(binding.get("project") or ""),
            workstream=str(binding.get("workstream") or ""),
            active_plan=str(binding.get("active_plan") or ""),
            # The submit identifies the turn.  Keep this stable across the
            # idle transition and the later process-close fallback so the
            # companion queue deduplicates both observations.
            generation=f"pty-stop:{sess.last_submit_at:.6f}",
            store=store,
        )
    except Exception as exc:  # noqa: BLE001 - terminal shutdown must remain fail-open
        logger.warning(
            "session companion PTY stop enqueue failed id=%s provider=%s: %s",
            sess.id,
            sess.provider,
            exc,
        )
        return {"created": False, "reason": f"{type(exc).__name__}: {exc}"}


def _companion_pty_replay(sess: PtySession) -> str:
    """Return bounded visible PTY output for providers without transcripts.

    This is deliberately labelled as an incomplete screen replay.  It gives
    zcode/grok companion runs useful output without claiming a native history
    that the provider never exposed.
    """
    raw = "".join(str(chunk) for chunk in list(sess.ring))
    if not raw:
        return ""
    # Remove ANSI CSI/OSC controls and carriage-return redraw noise while
    # retaining visible text.  The ring is already byte bounded.
    clean = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", raw)
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", clean)
    clean = clean.replace("\r", "\n")
    lines = [line.rstrip() for line in clean.splitlines() if line.strip()]
    if not lines:
        return ""
    text = "\n".join(lines)[-12000:]
    return "[PTY replay only; screen buffer may be incomplete]\n" + text


def _companion_assistant_output(
    sess: PtySession,
    *,
    native_session_id: str | None = None,
) -> str:
    """Prefer a linked provider transcript over terminal redraws when available."""
    if sess.provider == "zcode" and native_session_id:
        try:
            from omnicompany.packages.services._core.identity.session_locator import (
                preview_transcript,
            )

            rollout = _zcode_rollout_for(native_session_id)
            if rollout is not None:
                native_output = str(preview_transcript(rollout).get("last_text") or "").strip()
                if native_output:
                    return native_output
        except Exception:  # noqa: BLE001 - shutdown output must remain fail-open
            logger.warning(
                "zcode native companion read failed id=%s", sess.id, exc_info=True
            )
    return _companion_pty_replay(sess)


def _enqueue_companion_after_idle(
    sess: PtySession,
    *,
    was_working: bool,
    observed_at: float,
) -> dict[str, Any]:
    """Capture the first completed PTY turn without waiting for process exit."""

    if not was_working or sess.provider not in PTY_COMPANION_FALLBACK_PROVIDER_IDS:
        return {"created": False, "reason": "not a fallback provider"}
    if not sess.has_user_turn or sess.provider_runtime.working(now=observed_at):
        return {"created": False, "reason": "not idle or no user turn"}
    return _enqueue_session_companion_fallback(sess, reason="provider-idle")


_DEFAULT_PERMISSION_BYPASS_FLAGS = {
    "claude": "--dangerously-skip-permissions",
    "codex": "--dangerously-bypass-approvals-and-sandbox",
    # Gemini CLI 0.34: --yolo 等价于 --approval-mode yolo (自动批准全部工具)。
    "gemini": "--yolo",
    # Antigravity CLI (agy) 与 Claude 同款语义的非交互放行开关。
    "agy": "--dangerously-skip-permissions",
    "kimi": "--yolo",
    "kimi-code": "--yolo",
    "opencode": "--auto",
    "kiro-cli": "--trust-all-tools",
    "codebuddy": "--dangerously-skip-permissions",
    "cbc": "--dangerously-skip-permissions",
    "qodercli": "--dangerously-skip-permissions",
    "qoderclicn": "--dangerously-skip-permissions",
}


def _command_name(executable: str) -> str:
    """Return a cross-platform CLI basename for bare names and Windows paths."""
    filename = str(executable).replace("\\", "/").rsplit("/", 1)[-1]
    return os.path.splitext(filename)[0].lower()


def _resolve_cli_executable(executable: str) -> str | None:
    """Resolve a CLI, including installers that update user PATH out-of-process."""
    resolved = shutil.which(executable)
    if resolved is not None:
        return resolved
    if os.name == "nt" and _command_name(executable) == "kiro-cli":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidate = Path(local_app_data) / "Kiro-Cli" / "kiro-cli.exe"
            if candidate.is_file():
                return str(candidate)
    if os.name == "nt" and _command_name(executable) == "grok":
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            candidate = Path(user_profile) / ".grok" / "bin" / "grok.exe"
            if candidate.is_file():
                return str(candidate)
    if os.name == "nt" and _command_name(executable) == "agy":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            # Antigravity CLI 官方安装器把二进制放在这里, PATH 注册表更新对
            # 早于它启动的 daemon 进程不可见, 因此需要显式 fallback。
            candidate = Path(local_app_data) / "agy" / "bin" / "agy.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def _with_default_bypass_permissions(cmd: list[str], *, safe_mode: bool = False) -> list[str]:
    """Add the provider's non-interactive permission bypass unless opted out.

    Dashboard and LOFA terminal entrypoints both use this PTY manager, so keeping
    the policy here makes supported agent CLI commands behave consistently
    regardless of which UI created the session.
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

    The dashboard defaults to ``C:/workspace/`` while the project hooks
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
        or "--safe-mode" in normalized
        or has_explicit_settings
    ):
        return normalized
    path = _dashboard_claude_settings_path()
    if path is None:
        return normalized
    return [*normalized, "--settings", str(path)]


_QODER_COMMAND_NAMES = frozenset({"qodercli", "qoderclicn"})


def _with_qoder_token_plan(cmd: list[str]) -> list[str]:
    """给 dashboard 启动的 Qoder PTY 定默认模型: 内置百炼 Qwen-3.8-Max。

    模型 key 是 ``bailian/qwen3.8-max-tp`` (= ``qodercli --list-models`` 的
    "Qwen-3.8-Max (Alibaba Cloud Model Studio - China)"), 账号内置路由, 只要
    登录过就能用 —— 不再需要注入受管 settings (见 `qoder_settings` 模块
    docstring)。key 的大小写/连字符写错会被 qodercli 静默回退到 lite, 所以
    常量集中在 qoder_settings 一处。显式给了 `-m`/`--model` 的命令保持原样
    —— 用户手写的优先; 用户显式给的 `--settings` 也不动。
    """
    normalized = list(cmd)
    if not normalized or _command_name(normalized[0]) not in _QODER_COMMAND_NAMES:
        return normalized
    rest = normalized[1:]
    has_model = any(
        part in ("-m", "--model") or part.startswith("--model=") for part in rest
    )
    if has_model:
        return normalized
    from .qoder_settings import QODER_DEFAULT_MODEL_KEY

    return [*normalized, "-m", QODER_DEFAULT_MODEL_KEY]


# ── on-disk session metadata store ──────────────────────────────────────────




























def _upsert_meta_entry(
    sid: str,
    fields: dict[str, Any],
    *,
    preserve_existing: bool = False,
) -> None:
    def mutate(store: dict[str, dict[str, Any]]) -> bool:
        current = store.get(sid) or {}
        merged = (
            {**fields, **current}
            if preserve_existing
            else {**current, **fields}
        )
        if merged == current:
            return False
        store[sid] = merged
        return True

    _mutate_meta_store(mutate)


def _is_archivable_meta_entry(entry: dict[str, Any], cutoff_ts: float) -> bool:
    """True only for sessions that definitely ended and did so before `cutoff_ts`.

    Conservative on purpose: anything without a parseable `ended_at` (still
    alive, or a shape we don't recognize) is never archived, so a live session
    can never be swept out of the hot store from under it.
    """
    ended_at = entry.get("ended_at")
    if not ended_at:
        return False
    try:
        return float(ended_at) < cutoff_ts
    except (TypeError, ValueError):
        return False


def _archive_stale_sessions(*, now: float | None = None) -> dict[str, int]:
    """Compact sessions ended more than `META_STORE_ARCHIVE_AFTER_DAYS` ago out
    of the hot store into `_meta_store_archive_path()`.

    Runs the whole read -> partition -> archive-write -> shrink sequence
    inside the single `_meta_store_guard()` lock, so it composes as one more
    atomic mutation alongside every other reader/writer instead of racing them.
    Ordering matters for durability: the archive file is written (merged with
    whatever it already held, same-id entries overwritten by the newer copy)
    *before* the hot store is shrunk, so a crash between the two steps leaves
    the entry duplicated in both files rather than dropped from either. Any
    exception aborts this round with no changes committed to either file —
    this is best-effort housekeeping, never allowed to risk session metadata.
    """
    now = time.time() if now is None else now
    cutoff_ts = now - META_STORE_ARCHIVE_AFTER_DAYS * 86400
    with _meta_store_guard():
        store = _read_meta_store(use_cache=True, assume_locked=True)
        stale_ids = [
            sid for sid, entry in store.items()
            if _is_archivable_meta_entry(entry, cutoff_ts)
        ]
        if not stale_ids:
            return {"archived": 0, "remaining": len(store)}

        archive_path = _meta_store_archive_path()
        try:
            archive_store: dict[str, dict[str, Any]] = {}
            if archive_path.is_file():
                archive_store = json.loads(
                    archive_path.read_text(encoding="utf-8") or "{}"
                ) or {}
            for sid in stale_ids:
                archive_store[sid] = store[sid]  # newer (hot) copy wins on key clash
            archive_text = json.dumps(archive_store, ensure_ascii=False, allow_nan=False)
            archive_tmp = archive_path.with_name(
                f".{archive_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_tmp.write_text(archive_text, encoding="utf-8")
            os.replace(archive_tmp, archive_path)
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning(
                "cc_sessions archive write failed; skipping this compaction round: %s",
                exc,
            )
            return {"archived": 0, "remaining": len(store)}

        for sid in stale_ids:
            store.pop(sid, None)
        try:
            _write_meta_store_unlocked(store)
        except OSError as exc:
            # Archive already has the durable copy; the hot store still has
            # its old (larger) copy too. Nothing is lost, just not yet
            # compacted - the next scheduled round will retry (archive write
            # above is idempotent per-id, newer copy always wins).
            logger.error(
                "cc_sessions main-store shrink failed after archive succeeded "
                "(entries duplicated in both files, will retry): %s",
                exc,
            )
            return {"archived": 0, "remaining": len(store) + len(stale_ids)}
        logger.info(
            "cc_sessions archived %d session(s) ended before cutoff; %d remain in hot store",
            len(stale_ids),
            len(store),
        )
        return {"archived": len(stale_ids), "remaining": len(store)}






def _zcode_session_db_path() -> Path:
    return Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"




def _normalized_cwd(value: str) -> str:
    return os.path.normcase(os.path.normpath(str(value or "")))


def _poll_zcode_native_link(
    *,
    started_at: float,
    cwd: str,
    provider_session_id: str | None,
) -> tuple[str | None, str | None]:
    """Link only one same-cwd zcode session created immediately after this PTY.

    zcode does not expose a child-process UUID.  Time/cwd correlation is weaker
    than Codex's link, so ambiguity is intentionally a non-link.
    """
    if provider_session_id:
        return provider_session_id, None
    db_path = _zcode_session_db_path()
    if not db_path.is_file() or not cwd:
        return None, None
    start_ms = int(float(started_at) * 1000)
    end_ms = start_ms + int(ZCODE_LINK_WINDOW_S * 1000)
    try:
        with sqlite3.connect(
            f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=0.2
        ) as db:
            rows = db.execute(
                "SELECT id, directory, path, title FROM session "
                "WHERE time_created >= ? AND time_created <= ? ORDER BY time_created",
                (start_ms, end_ms),
            ).fetchall()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None, None
    expected_cwd = _normalized_cwd(cwd)
    candidates = [
        (str(native_id), str(title or ""))
        for native_id, directory, path, title in rows
        if expected_cwd in {_normalized_cwd(str(directory)), _normalized_cwd(str(path))}
    ]
    return candidates[0] if len(candidates) == 1 else (None, None)














def _process_is_descendant_or_same(pid: int, ancestor_pid: int) -> bool:
    """Return whether *pid* belongs to the PTY child process tree."""
    if pid <= 0 or ancestor_pid <= 0:
        return False
    if pid == ancestor_pid:
        return True
    try:
        import psutil

        # ``Process.parents()`` eagerly walks all the way to the system root
        # before callers can inspect the result.  On a process-heavy Windows
        # host every ppid lookup can take hundreds of milliseconds.  Walk one
        # edge at a time so the common Kiro -> bun -> PTY-child case stops as
        # soon as the authoritative ancestor is reached.
        current_pid = pid
        seen: set[int] = set()
        while current_pid > 0 and current_pid not in seen and len(seen) < 64:
            if current_pid == ancestor_pid:
                return True
            seen.add(current_pid)
            current_pid = int(psutil.Process(current_pid).ppid())
        return False
    except (ImportError, OSError):
        return False
    except Exception:
        # psutil uses platform-specific NoSuchProcess/AccessDenied subclasses;
        # a vanished or inaccessible process is simply not an authoritative link.
        return False


def _poll_kiro_native_link(
    *,
    child_pid: int | None,
    provider_session_id: str | None,
) -> tuple[str | None, str | None]:
    """Resolve the Kiro session whose native lock belongs to this PTY tree.

    Kiro writes ``<session-id>.lock`` with the owning CLI PID.  PID ancestry is
    authoritative for both direct Kiro PTYs and Kiro launched inside a shell;
    cwd/mtime proximity is deliberately not used because simultaneous sessions
    in the same repository must never be cross-wired.
    """
    if not child_pid:
        return provider_session_id, None
    base = Path.home() / ".kiro" / "sessions" / "cli"
    if not base.is_dir():
        return provider_session_id, None

    # A previously bound id was established by the process-tree check below or
    # by an explicit native resume.  While its exact lock still exists, avoid
    # rescanning every Kiro lock and rebuilding process ancestry once a second.
    # If Kiro switches sessions it removes/replaces that lock and discovery
    # naturally falls through to the authoritative slow path again.
    if provider_session_id:
        current_lock = base / f"{provider_session_id}.lock"
        current_record = _kiro_session_record(provider_session_id)
        if current_lock.is_file() and current_record is not None:
            title = str(current_record[1].get("title") or "").strip() or None
            return provider_session_id, title

    # Prefer fresh locks and discard incomplete session records before the
    # comparatively expensive Windows process-tree check.  Full discovery is
    # now limited to initial binding or an actual native-session transition.
    lock_candidates: list[tuple[int, Path]] = []
    for lock_path in base.glob("*.lock"):
        try:
            lock_candidates.append((lock_path.stat().st_mtime_ns, lock_path))
        except OSError:
            continue

    matches: list[tuple[str, str | None]] = []
    for _mtime_ns, lock_path in sorted(
        lock_candidates,
        key=lambda candidate: candidate[0],
        reverse=True,
    ):
        session_id = lock_path.stem
        session_record = _kiro_session_record(session_id)
        if session_record is None:
            continue
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock_pid = int(lock.get("pid") or 0)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if not _process_is_descendant_or_same(lock_pid, int(child_pid)):
            continue
        title = str(session_record[1].get("title") or "").strip() or None
        matches.append((session_id, title))

    if provider_session_id:
        current = next((item for item in matches if item[0] == provider_session_id), None)
        if current is not None:
            return current
    if len(matches) == 1:
        return matches[0]
    return provider_session_id, None


def _codex_latest_log_id(provider: str = "codex") -> int:
    """Return a cursor taken before spawning Codex.

    Codex writes its native thread id to ``logs_2.sqlite`` even when the user
    changes conversation inside the TUI with ``/resume``.  Starting from a
    pre-spawn cursor lets us follow only this PTY's new process instead of
    guessing from timestamps or scanning unrelated conversation history.

    The cursor is per account: each CODEX_HOME has its own ``logs_2.sqlite`` with
    an independent id sequence, so a cursor taken from the wrong home would
    either skip every new row or replay unrelated ones.
    """
    path = codex_home_for(provider) / "logs_2.sqlite"
    if not path.is_file():
        return 0
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=1) as db:
            row = db.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()
        return int(row[0] if row else 0)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return 0


def _codex_native_thread_details(
    thread_ids: list[str], provider: str = "codex"
) -> dict[str, tuple[str, str]]:
    """Resolve only durable Codex threads, returning id -> (title, rollout)."""
    if not thread_ids:
        return {}
    path = codex_home_for(provider) / "state_5.sqlite"
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
    provider: str = "codex",
) -> tuple[int, str | None, dict[str, int], str | None, str | None]:
    """Follow a Codex process to the latest durable native conversation.

    The process PID is authoritative for direct Codex PTYs.  For a Codex
    launched from the plain shell, its first tool process carries
    ``OMNI_CC_PTY_ID`` and provides a safe fallback correlation.  We never use
    cwd/title/time proximity, because two simultaneous browser sessions may
    otherwise be cross-wired.

    ``provider`` picks the account whose log database is followed; a ``codex_2``
    session writes to its own home and is invisible in the primary account's.
    """
    logs_path = codex_home_for(provider) / "logs_2.sqlite"
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

    details = _codex_native_thread_details(list(updated), provider)
    durable = [
        (row_id, thread_id, details[thread_id])
        for thread_id, row_id in updated.items()
        if thread_id in details
    ]
    if not durable:
        return latest_id, detected_process, updated, None, None
    _row_id, thread_id, (title, _rollout_path) = max(durable)
    return latest_id, detected_process, updated, thread_id, title




def _carried_launch_options(provider: str, original_cmd: list[str] | None) -> list[str]:
    """Model and reasoning-effort flags of the PTY being resumed.

    A resume used to relaunch on the provider defaults, silently moving a
    ``gpt-6.1-sol``/``ultra`` Codex or a ``fable`` Claude session onto another
    model (2026-09-17, again 2026-10-12 for 19 sessions at once). Only these two
    options are carried; executable, permission bypass and settings are derived
    exactly as for a fresh session.
    """
    tokens = [str(part) for part in (original_cmd or [])[1:]]
    if provider in CLAUDE_FAMILY_PROVIDER_IDS:
        value_flags: tuple[str, ...] = ("--model", "--effort")
        effort_config_key = None
    elif provider in CODEX_FAMILY_PROVIDER_IDS:
        value_flags = ("--model", "-m")
        effort_config_key = "model_reasoning_effort="
    else:
        return []
    carried: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if provider in CODEX_FAMILY_PROVIDER_IDS and tok == "resume":
            break  # everything after the subcommand belongs to the old resume
        if tok in value_flags and nxt is not None:
            carried.extend([tok, nxt])
            i += 2
            continue
        if any(tok.startswith(flag + "=") for flag in value_flags if flag.startswith("--")):
            carried.append(tok)
        elif (
            effort_config_key
            and tok in ("-c", "--config")
            and nxt is not None
            and nxt.startswith(effort_config_key)
        ):
            carried.extend([tok, nxt])
            i += 2
            continue
        i += 1
    return carried


def _resume_command(
    provider: str,
    provider_session_id: str,
    original_cmd: list[str] | None = None,
) -> list[str]:
    """Build a provider-native resume command with the default bypass policy.

    ``original_cmd`` is the command of the PTY being resumed; its model and
    reasoning-effort flags are kept (see ``_carried_launch_options``).
    """
    carried = _carried_launch_options(provider, original_cmd)
    if provider in CLAUDE_FAMILY_PROVIDER_IDS:
        resolved = resolve_claude_cmd(safe_mode=False)
        if resolved is None:
            raise RuntimeError("claude CLI not found on PATH. Install Claude Code first.")
        return [*resolved, *carried, "--resume", provider_session_id]
    if provider in CODEX_FAMILY_PROVIDER_IDS:
        # Codex parses this as a global option, before the resume subcommand.
        # codex_2 needs no argv override — its account comes from CODEX_HOME,
        # injected by provider_terminal_environment for the resumed session.
        cmd = _with_default_bypass_permissions(["codex"], safe_mode=False)
        if provider == "codex_the_company":
            from omnicompany.packages.services._core.agent.control_plane import (
                THE_COMPANY_CODEX_CLI_OVERRIDES,
            )

            for flag, value in THE_COMPANY_CODEX_CLI_OVERRIDES:
                cmd.extend([flag, value])
        if provider == "codex_glm":
            from omnicompany.packages.services._core.agent.control_plane import (
                GLM_CODEX_CLI_OVERRIDES,
            )

            for flag, value in GLM_CODEX_CLI_OVERRIDES:
                cmd.extend([flag, value])
        return [*cmd, *carried, "resume", provider_session_id]
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
    if provider == "kiro":
        return [
            *_with_default_bypass_permissions(["kiro-cli", "chat"], safe_mode=False),
            "--resume-id",
            provider_session_id,
        ]
    if provider == "qoder":
        # 默认模型 key 由 create() 的 _with_qoder_token_plan 补上,
        # 跟新建会话走同一条注入, 免得 resume 掉回 Qoder 自带模型。
        return [
            *_with_default_bypass_permissions(["qodercli"], safe_mode=False),
            "--resume",
            provider_session_id,
        ]
    raise RuntimeError(f"provider {provider!r} does not support PTY resume")


def list_recoverable_sessions() -> list[dict[str, Any]]:
    """Return PTYs whose provider conversation log can really be resumed.

    ``ended_at`` is not required: an unclean daemon exit leaves live-looking
    metadata behind even though the in-memory PTY registry is gone.

    Reads the hot store merged with the P4.2b archive (`_read_meta_store_with_
    archive`): a session ended long enough ago to have been compacted out of
    the hot store is still genuinely resumable via `--resume` as long as its
    provider conversation log exists on disk, so archiving it must not make it
    disappear from this listing.
    """
    out: list[dict[str, Any]] = []
    store = _read_meta_store_with_archive()
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
        recoverable_provider = provider in CODEX_FAMILY_PROVIDER_IDS or provider in {
            "claude_code",
            "claude_2",
            "kimi",
            "opencode",
            "codebuddy",
            "kiro",
            "qoder",
        }
        if not is_pty or not recoverable_provider:
            continue
        provider_session_id = m.get("provider_session_id") or (
            m.get("claude_session_id") if provider in CLAUDE_FAMILY_PROVIDER_IDS else None
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
            "process_alive": False,
            "status": "recoverable",
            "close_actor": m.get("close_actor"),
            "close_reason": m.get("close_reason") or m.get("exit_reason") or "unknown",
            "close_time": m.get("close_time") or m.get("ended_at"),
            "close_evidence": {
                "requester_ip": m.get("kill_requester_ip"),
                "requester_user_agent": m.get("kill_requester_ua"),
                "requester_referer": m.get("kill_requester_referer"),
            },
            "conversation_jsonl": str(jsonl),
            "claude_jsonl": str(jsonl) if provider == "claude_code" else None,
            "jsonl_present": True,
        })
    return sorted(
        out,
        key=lambda x: x.get("ended_at") or x.get("started_at") or 0,
        reverse=True,
    )


def list_remembered_human_sessions() -> list[dict[str, Any]]:
    """Return explicitly preserved human PTY tombstones that cannot resume.

    A provider transcript is required before a session may be advertised as
    recoverable.  Human-created sessions must still remain visible after an
    accidental operator kill, though, even when that provider never persisted a
    native history.  ``recovery_listed`` is an explicit repair marker so this
    does not turn every old human tombstone into permanent catalogue clutter.
    """
    store = _read_meta_store_with_archive()
    out: list[dict[str, Any]] = []
    for sid, meta in store.items():
        if (
            meta.get("origin") != SESSION_ORIGIN_HUMAN
            or not meta.get("recovery_listed")
            or not meta.get("ended_at")
            or meta.get("hidden_from_recovery")
        ):
            continue
        resumed_to = meta.get("resumed_to")
        if resumed_to and resumed_to in store:
            continue
        out.append(
            {
                **meta,
                "id": sid,
                "alive": False,
                "process_alive": False,
                "status": "ended",
                "resumable": False,
                "close_actor": meta.get("close_actor"),
                "close_reason": meta.get("close_reason") or meta.get("exit_reason") or "unknown",
                "close_time": meta.get("close_time") or meta.get("ended_at"),
                "close_evidence": {
                    "requester_ip": meta.get("kill_requester_ip"),
                    "requester_user_agent": meta.get("kill_requester_ua"),
                    "requester_referer": meta.get("kill_requester_referer"),
                },
                "recovery_unavailable_reason": meta.get(
                    "recovery_unavailable_reason"
                )
                or "provider-history-missing",
            }
        )
    return sorted(
        out,
        key=lambda item: item.get("ended_at") or item.get("started_at") or 0,
        reverse=True,
    )


def update_meta_field(sid: str, *, allow_none: bool = False, **fields: Any) -> None:
    """External hook helper — write new field(s) for a session id (e.g. `claude_session_id`)."""
    if not sid:
        return
    values = fields if allow_none else {k: v for k, v in fields.items() if v is not None}
    _upsert_meta_entry(sid, values)


def restore_recoverable_session(
    sid: str,
    *,
    provider_session_id: str | None = None,
) -> dict[str, Any]:
    """Convert an ended PTY tombstone back into a recoverable session entry.

    This is intentionally explicit and validates the provider's durable
    conversation before changing recovery policy. It exists for operator
    correction of an accidental kill; normal idle hibernation never writes a
    kill tombstone in the first place.
    """
    store = _read_meta_store_with_archive()
    resolved_id, current = _latest_resume_entry(store, sid)
    if current is None:
        raise KeyError(sid)
    if not current.get("ended_at"):
        raise RuntimeError(f"session is still alive: {resolved_id}")
    restored = dict(current)
    if provider_session_id:
        restored["provider_session_id"] = provider_session_id
        if restored.get("provider") in CLAUDE_FAMILY_PROVIDER_IDS:
            restored["claude_session_id"] = provider_session_id
    provider = restored.get("provider")
    native_id = restored.get("provider_session_id") or (
        restored.get("claude_session_id") if provider in CLAUDE_FAMILY_PROVIDER_IDS else None
    )
    transcript = _conversation_jsonl_for(
        provider,
        str(restored.get("cwd") or ""),
        native_id,
    )
    if transcript is None:
        if restored.get("origin") != SESSION_ORIGIN_HUMAN:
            raise RuntimeError(
                f"provider conversation is not durable: {provider}:{native_id or '-'}"
            )
        restored.update(
            exit_reason="operator-restored-unavailable",
            termination_reason="ended",
            recovery_disabled=True,
            hidden_from_recovery=False,
            recovery_listed=True,
            recovery_unavailable_reason="provider-history-missing",
            recovery_restored_at=time.time(),
            recovery_restored_from="operator-correction",
        )
        _upsert_meta_entry(resolved_id, restored)
        return {
            **restored,
            "id": resolved_id,
            "alive": False,
            "status": "ended",
            "resumable": False,
        }
    restored.update(
        exit_reason="operator-restored",
        termination_reason="recoverable",
        recovery_disabled=False,
        hidden_from_recovery=False,
        recovery_listed=True,
        recovery_restored_at=time.time(),
        recovery_restored_from="operator-correction",
    )
    _upsert_meta_entry(resolved_id, restored)
    return {
        **restored,
        "id": resolved_id,
        "alive": False,
        "status": "recoverable",
        "resumable": True,
        "conversation_jsonl": str(transcript),
    }


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
        self._initial_host_discovery_done = False
        self._tab_state_bootstrap_lock = asyncio.Lock()
        self._tab_state_bootstrap_task: asyncio.Task[None] | None = None
        self._latency_monitor = get_latency_monitor()
        # 旧 PTY id → 替代会话 id (resume 链别名)。老浏览器页签始终以其原始 id 为键,
        # 高频页签状态快照据此把新会话的运行态投影回旧 id。仅 resume() 写入,
        # 目标会话死亡后投影自动跳过(无需清理, 量级是 resume 次数)。
        self._resume_aliases: dict[str, str] = {}
        # assistant 会话费用监视去重: 新建路径(pty_routes)与重接路径(prepare_tab_states)
        # 各挂一次, 同一 id 不重复起 watcher。
        self._billing_watched: set[str] = set()
        # PERF-OBSERVATORY P4.2b: 0.0 makes the first reaper tick after daemon
        # startup due immediately, satisfying "启动时执行一次+每日一次" without a
        # dedicated thread/task — it just piggybacks the existing 60s reaper.
        self._last_archive_check_at: float = 0.0

    def mark_billing_watched(self, sid: str) -> None:
        self._billing_watched.add(sid)

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
                load_digests_cached,
            )

            self.apply_digest_titles(load_digests_cached())
        except Exception:
            # Titles are progressive enhancement: the first prompt/native
            # provider title must remain usable if the digest store is absent.
            logger.debug("could not refresh PTY digest titles", exc_info=True)
        return self._with_session_bindings([s.to_meta() for s in self._sessions.values()])

    def in_memory_session_count(self) -> int:
        """Cheap health-probe count; never discovers hosts or reads registries."""

        return sum(1 for session in self._sessions.values() if not session.closed)

    def apply_digest_titles(self, digests: dict[str, dict[str, Any]]) -> None:
        """Project cheap-model session summaries onto matching live PTYs."""
        for session in self._sessions.values():
            # Some PTY providers (for example Qoder) do not expose a native
            # session id.  Their companion events are intentionally keyed by
            # PTY id, so use the same identity for the sidebar projection.
            native_id = session.provider_session_id or session.claude_session_id or session.id
            digest = (
                digests.get(f"{session.provider}:{native_id}")
                if native_id
                else None
            )
            title = str((digest or {}).get("title") or "").strip()
            session.summary_title = title or None

    @staticmethod
    def _with_session_bindings(metas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Project the authoritative binding ledger into the PTY catalogue."""
        try:
            from omnicompany.packages.services._core.identity import bindings_by_session_key

            bindings = bindings_by_session_key()
        except Exception:
            bindings = {}
        projects: list[dict[str, Any]] | None = None
        for meta in metas:
            provider = str(meta.get("provider") or "").strip()
            native_id = str(meta.get("provider_session_id") or meta.get("claude_session_id") or "").strip()
            candidates = [
                f"pty:{meta.get('id')}" if meta.get("id") else "",
                f"session:{native_id}" if native_id else "",
                f"{provider}:{native_id}" if provider and native_id else "",
            ]
            def _compatible(key: str) -> bool:
                if not key.startswith("pty:") or not native_id:
                    return True
                candidate = bindings[key]
                candidate_ids = {
                    str(candidate.get(field) or "").strip()
                    for field in ("session_id", "claude_session_id")
                    if str(candidate.get(field) or "").strip()
                }
                # A PTY can be reused by a new native conversation. Its alias
                # is valid only while the native id still matches.
                return not candidate_ids or native_id in candidate_ids

            binding = next(
                (bindings[key] for key in candidates if key and key in bindings and _compatible(key)),
                None,
            )
            if binding:
                project = str(binding.get("project") or "").strip()
                if project:
                    try:
                        if projects is None:
                            from omnicompany.core.projects_registry import list_projects

                            projects = list_projects()
                        from omnicompany.dashboard.boss_sight.services.project_resolution import canonical_project_id

                        project = canonical_project_id(label=project, projects=projects)
                    except Exception:
                        project = ""
                meta["project"] = project or None
                meta["workstream"] = str(binding.get("workstream") or "") or None
                if binding.get("active_plan"):
                    meta["active_plan"] = str(binding.get("active_plan") or "") or None
                if binding.get("topic"):
                    topic = str(binding.get("topic") or "").strip() or None
                    meta["topic"] = topic
                    meta["display_title"] = topic
                meta["binding_source"] = str(binding.get("source") or "") or None
        return metas

    def tab_states(self) -> list[dict[str, Any]]:
        """页签活跃徽章的高频运行态投影(前端 2s 轮询, 多浏览器)。

        纯内存读取: 与 list_meta() 不同, 不读数 MB 的 cc_sessions.json, 不做
        host 发现, 不刷新 hook 字段 —— 那些属于 get()/attach//sessions 等
        低频路径, 页签打开的瞬间就会把状态同步回来。纯内存快照最多滞后
        一个轮询周期, 换来每个浏览器每 2 秒一次的轮询零磁盘开销。
        """
        bound = self._with_session_bindings([sess.to_meta() for sess in self._sessions.values()])
        metas = {meta["id"]: meta for meta in bound}
        items = [self._tab_state_item(meta) for meta in metas.values()]
        for old_id, new_id in self._resume_aliases.items():
            if old_id in metas:
                continue
            target = metas.get(new_id)
            if target is None:
                continue  # 别名目标已死(kill/reap): 不投影陈旧运行态
            item = self._tab_state_item(target)
            item["id"] = old_id
            # Keep legacy/open tabs addressable without presenting the alias as
            # another live runtime.  Consumers that render a session list must
            # omit aliases; consumers keyed by the old tab id may still read its
            # projected state and reconnect through the canonical target.
            item["resume_alias_for"] = new_id
            items.append(item)
        return items

    @staticmethod
    def _tab_state_item(meta: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": meta["id"],
            "session_id": meta.get("session_id") or meta["id"],
            "pty_id": meta.get("pty_id") or meta["id"],
            "alive": bool(meta.get("alive")),
            "process_alive": meta.get("process_alive"),
            "processAlive": meta.get("processAlive", meta.get("process_alive")),
            "observed_at": meta.get("observed_at"),
            "observedAt": meta.get("observedAt", meta.get("observed_at")),
            "source": meta.get("source") or "ccdaemon.pty",
            "working": bool(meta.get("working")),
            "working_known": bool(meta.get("working_known")),
            "workingKnown": bool(meta.get("workingKnown", meta.get("working_known"))),
            "state": meta.get("state") or "unknown",
            "state_evidence_at": meta.get("state_evidence_at"),
            "stateEvidenceAt": meta.get("stateEvidenceAt", meta.get("state_evidence_at")),
            "working_source": meta.get("working_source"),
            "provider_runtime_state": meta.get("provider_runtime_state"),
            "provider_runtime_marker_at": meta.get("provider_runtime_marker_at"),
            "provider_runtime_known": bool(meta.get("provider_runtime_known")),
            "has_user_turn": bool(meta.get("has_user_turn")),
            "status": meta.get("status") or "alive",
            "origin": meta.get("origin"),
            "visibility": meta.get("visibility") or "visible",
            "visible": bool(meta.get("visible", meta.get("visibility") != "hidden")),
            "reclaim": meta.get("reclaim"),
            # 列表行展示所需的轻量 meta(活跃会话页与页签徽章共用这份轮询,
            # 不必再为每行单发一次完整 /sessions 请求)。
            "provider": meta.get("provider"),
            "cwd": meta.get("cwd"),
            "provider_title": meta.get("provider_title"),
            "display_title": meta.get("display_title"),
            "provider_session_id": meta.get("provider_session_id"),
            "project": meta.get("project"),
            "workstream": meta.get("workstream"),
            "active_plan": meta.get("active_plan"),
            "topic": meta.get("topic"),
            "binding_source": meta.get("binding_source"),
            "started_at": meta.get("started_at"),
            "last_submit_at": meta.get("last_submit_at"),
            "last_output_at": meta.get("last_output_at"),
            "last_activity_at": meta.get("last_activity_at"),
            "lastActivityAt": meta.get("lastActivityAt", meta.get("last_activity_at")),
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
            provider = str(meta["provider"])
            # Legacy records may have been written as shell (or the old
            # Claude fallback) before command-based provider detection existed.
            # Keep the immutable PTY command authoritative for a concrete CLI.
            detected = _provider_for_command(sess.cmd)
            sess.provider = detected if detected != "shell" and provider in {"shell", "claude_code"} else provider
            sess.provider_runtime.set_provider(sess.provider)
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
        # Detached hosts can only predate this manager instance. Sessions born
        # afterwards are registered by create(), so rescanning thousands of
        # historical generation files on every /sessions refresh is pure cost.
        # Initial discovery may happen in a synchronous HTTP worker before an
        # event loop is available. Start deferred native watchers on the first
        # async entry instead of leaving rehydrated identities frozen forever.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            for existing in self._sessions.values():
                if (not existing.closed and existing.provider in _NATIVE_LINK_PROVIDER_IDS
                        and (existing.provider_link_task is None or existing.provider_link_task.done())):
                    existing.provider_link_task = asyncio.create_task(
                        self._provider_link_loop(existing), name=f"pty-provider-link-{existing.id}")
        if self._initial_host_discovery_done:
            return
        host_dir = _pty_host_dir()
        if not host_dir.is_dir():
            self._initial_host_discovery_done = True
            return
        store = _read_meta_store()
        reconciled_updates: dict[str, dict[str, Any]] = {}
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
                    reconciled_updates[sid] = {
                        "ended_at": time.time(),
                        "exit_reason": reason,
                        "exit_code": state.get("exit_code"),
                    }
                continue
            cmd = state.get("cmd") or meta.get("cmd") or []
            persisted_provider = str(state.get("provider") or meta.get("provider") or "shell")
            persisted_env = state.get("extra_env") or meta.get("extra_env") or None
            detected_provider = _provider_for_command(
                [str(part) for part in cmd],
                persisted_env if isinstance(persisted_env, dict) else None,
            )
            provider = (
                detected_provider
                if detected_provider != "shell" and persisted_provider in {"shell", "claude_code"}
                else persisted_provider
            )
            sess = PtySession(
                id=sid,
                cmd=[str(part) for part in cmd],
                cwd=str(state.get("cwd") or meta.get("cwd") or os.getcwd()),
                cols=int(state.get("cols") or meta.get("cols") or DEFAULT_COLS),
                rows=int(state.get("rows") or meta.get("rows") or DEFAULT_ROWS),
                started_at=float(state.get("started_at") or meta.get("started_at") or time.time()),
                last_detach_at=_rehydrated_last_detach_at(meta, time.time()),
                last_output_at=float(state.get("last_output_at") or time.time()),
                provider=provider,
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
                # Records predating ``origin`` are read as human-opened. The
                # unknown-caller default is deliberately inverted here: at
                # creation an unidentified caller must be reclaimable, but a
                # session that already survived a daemon restart may well be the
                # user's own long-lived terminal, and a 30-minute TTL would kill
                # it on the next reap. Clearing the existing backlog is an
                # explicit operation, not a silent side effect of upgrading.
                origin=str(meta.get("origin") or SESSION_ORIGIN_HUMAN),
                visibility=str(meta.get("visibility") or "visible"),
                topic=meta.get("topic"),
                codex_log_cursor=0,
                codex_process_uuid=meta.get("codex_process_uuid"),
            )
            sess.provider_runtime.restore(state)
            self._sessions[sid] = sess
            if sess.provider in _NATIVE_LINK_PROVIDER_IDS:
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    continue
                sess.provider_link_task = asyncio.create_task(
                    self._provider_link_loop(sess),
                    name=f"pty-provider-link-{sid}",
                )
        if reconciled_updates:
            def reconcile(store: dict[str, dict[str, Any]]) -> bool:
                changed = False
                for sid, fields in reconciled_updates.items():
                    current = store.get(sid) or {}
                    # A concurrent close/update is newer and authoritative.
                    if current.get("ended_at"):
                        continue
                    store[sid] = {**current, **fields}
                    changed = True
                return changed

            _mutate_meta_store(reconcile)
            logger.warning(
                "reconciled %s detached PTY tombstones in one metadata transaction",
                len(reconciled_updates),
            )
        self._ensure_reaper_task()
        self._initial_host_discovery_done = True

    async def prepare_tab_states(self) -> None:
        """Bootstrap detached hosts once and reconnect their runtime streams.

        Reconnects run in background. Until a legacy host replay yields an
        authoritative provider marker, the API exposes an unknown state rather
        than incorrectly declaring the turn done.
        """
        if not self._initial_host_discovery_done:
            async with self._tab_state_bootstrap_lock:
                if not self._initial_host_discovery_done:
                    if self._sessions:
                        self._initial_host_discovery_done = True
                    elif (
                        self._tab_state_bootstrap_task is None
                        or self._tab_state_bootstrap_task.done()
                    ):
                        self._tab_state_bootstrap_task = asyncio.create_task(
                            self._bootstrap_host_discovery(),
                            name="cc-host-discovery",
                        )
        self._schedule_host_runtime_bridges()
        self._start_billing_watchers()

    async def _bootstrap_host_discovery(self) -> None:
        """Rehydrate detached PTYs without holding the tab-state request open."""
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                _PTY_HOST_DISCOVERY_EXECUTOR,
                self._discover_host_sessions,
            )
        except Exception:  # noqa: BLE001 - a stale host must not break polling
            logger.exception("detached PTY host discovery failed")
        finally:
            self._schedule_host_runtime_bridges()
            self._start_billing_watchers()

    def _start_billing_watchers(self) -> None:
        """为重接的 assistant 会话补挂费用监视(新建路径在 pty_routes 挂, 这里只补漏)。"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        from .assistant import ASSISTANT_TOPIC
        from .pty_routes import _monitor_billing_issue, _track_agent_worker_monitor

        for sid, sess in list(self._sessions.items()):
            if sess.closed or sess.topic != ASSISTANT_TOPIC or sid in self._billing_watched:
                continue
            self._billing_watched.add(sid)
            _track_agent_worker_monitor(asyncio.create_task(
                _monitor_billing_issue(sid),
                name=f"assistant-billing-watch-{sid}",
            ))
            logger.info("assistant billing watcher attached to rehydrated session %s", sid)

    def _schedule_host_runtime_bridges(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        # Replaying several multi-megabyte terminals concurrently can starve
        # unrelated sessions. Prime one detached host at a time; the next cheap
        # tab-state poll advances the queue.
        if any(
            sess.host_connect_task is not None and not sess.host_connect_task.done()
            for sess in self._sessions.values()
        ):
            return
        now = time.monotonic()
        for sess in self._sessions.values():
            writer = sess.host_writer
            if (
                sess.closed
                or not sess.host_port
                or sess.host_connect_retry_at > now
                or (writer is not None and not writer.is_closing())
                or (
                    sess.host_connect_task is not None
                    and not sess.host_connect_task.done()
                )
            ):
                continue
            sess.host_connect_task = loop.create_task(
                self._prime_host_runtime(sess),
                name=f"pty-host-runtime-{sess.id}",
            )
            break

    async def _prime_host_runtime(self, sess: PtySession) -> None:
        task = asyncio.current_task()
        started_ns = time.perf_counter_ns()
        try:
            await self._ensure_host_connected(sess, want_snapshot=False)
            sess.host_connect_retry_at = 0.0
        except (KeyError, TimeoutError):
            sess.host_connect_retry_at = (
                time.monotonic() + HOST_RUNTIME_CONNECT_BACKOFF_S
            )
            logger.debug("could not reconnect detached PTY host %s", sess.id)
        finally:
            self._latency_monitor.observe_pty_host_reconnect(
                sess.id,
                time.perf_counter_ns() - started_ns,
            )
            if sess.host_connect_task is task:
                sess.host_connect_task = None

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
            try:
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
                            raise ValueError("unexpected PTY host snapshot frame")
                else:
                    raise ValueError("PTY host did not provide a snapshot")
            except Exception as exc:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
                raise KeyError(sess.id) from exc
            # A legacy host may still offer its old 64 MB replay. Keep only the
            # same bounded window used for live output, and return that bounded
            # snapshot to xterm instead of forwarding the oversized source
            # list after it has already been trimmed from ``sess.ring``.
            ring, ring_bytes, replay_truncated = await _pty_io(
                _build_proxy_replay,
                chunks,
            )
            sess.ring = ring
            sess.ring_bytes = ring_bytes
            sess.replay_truncated = replay_truncated
            # PERF-OBSERVATORY P4.1: this ring is a wholesale replacement from
            # the host's own buffer (first bridge connect, or a mid-session
            # bridge reconnect), not an incremental append — its content has
            # no real correspondence to any seq this process already handed
            # out. Advance next_seq *past* whatever it already was so the new
            # ring is treated as freshly appended from this point on: no seq
            # collision/reuse is possible, and a client whose last_seq predates
            # this reconnect always falls outside the window and safely gets a
            # full snapshot instead of a resume built on unrelated content.
            sess.next_seq += len(sess.ring)
            browser_snapshot = list(ring) if want_snapshot else []
            sess.replay_truncated = (
                sess.replay_truncated or _replay_meta_is_truncated(meta)
            )
            sess.host_writer = writer
            if isinstance(meta, dict):
                sess.child_pid = int(meta.get("child_pid") or sess.child_pid or 0) or None
                sess.last_output_at = float(meta.get("last_output_at") or sess.last_output_at)
                sess.host_answers_queries = bool(meta.get("terminal_query_responder"))
                sess.host_screen_model = bool(meta.get("screen_model"))
                restored = sess.provider_runtime.restore(meta)
                if not restored:
                    recovered_runtime = ProviderRuntimeState(sess.provider)
                    recovered = await _pty_io(
                        recovered_runtime.restore_from_replay,
                        chunks,
                        observed_at=sess.last_output_at,
                    )
                    if recovered:
                        sess.provider_runtime = recovered_runtime
            await _pty_io(
                _write_pty_host_generation_snapshot,
                sess,
                meta,
            )
            sess.host_reader_task = asyncio.create_task(
                self._host_reader_loop(sess, reader, writer),
                name=f"pty-host-bridge-{sess.id}",
            )
            return browser_snapshot

    async def _request_host_screen(
        self, sess: PtySession, *, timeout: float = 2.0
    ) -> str | None:
        """Fetch one self-contained repaint frame from the host's screen model.

        Returns ``None`` on any failure (old host, dead model, timeout,
        connection loss) so every caller falls back to the raw-ring replay —
        exactly today's behavior. Never called while holding ``stream_lock``:
        the reply arrives on the same host bridge that also carries output
        frames, and the bridge reader needs that lock to publish them.
        """
        if sess.closed or not sess.host_port or not sess.host_screen_model:
            return None
        sess.screen_req_seq += 1
        req = sess.screen_req_seq
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        sess.screen_pending[req] = fut
        try:
            await self._send_host(sess, {"type": "screen", "req": req})
            data = await asyncio.wait_for(fut, timeout=timeout)
        except Exception:
            return None
        finally:
            sess.screen_pending.pop(req, None)
        if isinstance(data, str) and data:
            # Long-lived hosts run the screen model they started with; a model
            # that latched a bogus underline would paint every blank row as a
            # full-width line, so sanitize here for old and new hosts alike.
            from .pty_screen import strip_underline_sgr

            return strip_underline_sgr(data)
        return None

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
        messages_since_yield = 0
        try:
            while not sess.closed:
                message = await read_frame(reader)
                kind = message.get("type")
                if kind == "output":
                    chunk = str(message.get("data") or "")
                    if not chunk:
                        continue
                    if sess.latency_pending_input_ns:
                        pending_ns = sess.latency_pending_input_ns
                        sess.latency_pending_input_ns = 0
                        self._latency_monitor.finish_pty_reaction(sess.id, pending_ns)
                    if not sess.host_answers_queries:
                        from .pty_host import TerminalQueryResponder, write_frame

                        if sess.terminal_query_responder is None:
                            sess.terminal_query_responder = TerminalQueryResponder()
                        terminal_reply = sess.terminal_query_responder.feed(
                            chunk, provider=sess.provider
                        )
                        if terminal_reply:
                            async with sess.host_write_lock:
                                await write_frame(
                                    writer,
                                    {"type": "input", "data": terminal_reply},
                                )
                    async with sess.stream_lock:
                        observed_at = time.time()
                        sess.last_output_at = observed_at
                        was_working = sess.provider_runtime.working(now=observed_at)
                        runtime_changed = sess.provider_runtime.feed(chunk, now=observed_at)
                        self._publish_output(sess, chunk)
                        if str(sess.provider).lower().startswith("codex"):
                            from .codex_capacity_retry import observe_pty_chunk

                            asyncio.create_task(
                                observe_pty_chunk(
                                    sess,
                                    chunk,
                                    was_working=was_working,
                                    observed_at=observed_at,
                                ),
                                name=f"codex-capacity-observe-{sess.id}",
                            )
                            if runtime_changed and sess.provider_runtime.working(now=observed_at):
                                from .codex_capacity_retry import observe_pty_progress

                                asyncio.create_task(
                                    observe_pty_progress(sess, observed_at=observed_at),
                                    name=f"codex-capacity-progress-{sess.id}",
                                )
                    if runtime_changed and was_working:
                        asyncio.create_task(
                            asyncio.to_thread(
                                _enqueue_companion_after_idle,
                                sess,
                                was_working=was_working,
                                observed_at=observed_at,
                            ),
                            name=f"pty-companion-idle-{sess.id}",
                        )
                elif kind == "screen":
                    req = message.get("req")
                    fut = (
                        sess.screen_pending.pop(req, None)
                        if req is not None
                        else None
                    )
                    if fut is not None and not fut.done():
                        fut.set_result(message.get("data"))
                elif kind == "exit":
                    host_exited = True
                    sess.closed = True
                    sess.latency_pending_input_ns = 0
                    self._latency_monitor.end_session(sess.id)
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
                messages_since_yield += 1
                if messages_since_yield >= 64:
                    messages_since_yield = 0
                    await asyncio.sleep(0)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError, OSError):
            pass
        finally:
            if sess.host_writer is writer:
                sess.host_writer = None
            for fut in list(sess.screen_pending.values()):
                if not fut.done():
                    fut.set_result(None)
            sess.screen_pending.clear()
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
        extra_env: dict[str, str] | None = None,
        resume_claude_session_id: str | None = None,
        resume_provider_session_id: str | None = None,
        resumed_from_pty_id: str | None = None,
        origin: str = SESSION_ORIGIN_AGENT,
        visibility: str = "visible",
    ) -> PtySession:
        self._admit_new_session()

        if cmd is None:
            resolved = resolve_claude_cmd(safe_mode=safe_mode)
            if resolved is None:
                raise RuntimeError(
                    "claude CLI not found on PATH. Install Claude Code first."
                )
            cmd = resolved
            if resume_claude_session_id:
                cmd = cmd + ["--resume", resume_claude_session_id]

        from omnicompany.runtime.powershell_runtime import normalize_powershell_command

        cmd = normalize_powershell_command(cmd)
        cmd = _with_default_bypass_permissions(cmd, safe_mode=safe_mode)
        cmd = _with_dashboard_claude_settings(cmd)
        cmd = _with_qoder_token_plan(cmd)

        cwd = resolve_session_cwd(cwd)

        # Windows: winpty 自己不查 PATHEXT, 裸命令名 (kimi/opencode/codex) 会
        # spawn 失败 (WinptyError: 系统找不到指定的文件), 且失败现场还会泄漏
        # winpty-agent 句柄 — 先在这里用 shutil.which 解析成全路径.
        if os.name == "nt" and cmd and os.path.basename(cmd[0]) == cmd[0]:
            resolved0 = _resolve_cli_executable(cmd[0])
            if resolved0 is None:
                raise RuntimeError(f"command not found on PATH: {cmd[0]!r}")
            cmd = [resolved0, *cmd[1:]]

        # Classify before taking the Codex watermark: the cursor has to come from
        # the very ``logs_2.sqlite`` this session will write to, and each account's
        # CODEX_HOME has its own id sequence. Reading the primary account's cursor
        # for a codex_2 session would make every later row look already-seen.
        provider = _provider_for_command(cmd, extra_env)
        # Capture the Codex log watermark before spawn. The native TUI can move
        # between threads without restarting the PTY, so the durable binding is
        # followed from native lifecycle logs after this point.
        codex_log_cursor = _codex_latest_log_id(provider)

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
                extra_env=extra_env,
                codex_log_cursor=codex_log_cursor,
                resume_claude_session_id=resume_claude_session_id,
                resume_provider_session_id=resume_provider_session_id,
                resumed_from_pty_id=resumed_from_pty_id,
                origin=origin,
                visibility=visibility,
            )

        from winpty import PTY  # imported lazily so non-Windows hosts can import this module

        # Inherit current env, then add our marker. winpty wants `KEY=VAL\0KEY=VAL\0\0`.
        env_dict = provider_terminal_environment(provider, os.environ)
        if extra_env:
            env_dict.update({str(k): str(v) for k, v in extra_env.items()})
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
            provider=provider,
            child_pid=int(pty.pid) if getattr(pty, "pid", None) else None,
            provider_session_id=resume_provider_session_id or resume_claude_session_id,
            claude_session_id=resume_claude_session_id,
            codex_log_cursor=codex_log_cursor,
            origin=origin,
            visibility=visibility,
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
            "origin": sess.origin,
            "visibility": sess.visibility,
        }, preserve_existing=True)
        if sess.provider in _NATIVE_LINK_PROVIDER_IDS:
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
        origin: str = SESSION_ORIGIN_AGENT,
        visibility: str = "visible",
        extra_env: dict[str, str] | None = None,
    ) -> PtySession:
        """Spawn a detached PTY owner and register a reconnectable proxy."""
        host_dir = _pty_host_dir()
        host_dir.mkdir(parents=True, exist_ok=True)
        state_path = _pty_host_state_path(sid)
        launch_path = host_dir / f".{sid}.launch"
        log_path = host_dir / f"{sid}.log"
        started_at = time.time()
        provider = _provider_for_command(cmd, extra_env)
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
            "extra_env": {str(k): str(v) for k, v in (extra_env or {}).items()},
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
            origin=origin,
            visibility=visibility,
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
            "origin": sess.origin,
            "visibility": sess.visibility,
        }, preserve_existing=True)
        if provider in _NATIVE_LINK_PROVIDER_IDS:
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

    async def migrate_codex_account(self, requested_id: str) -> PtySession:
        """Retire one second-account PTY, retaining its native conversation."""
        # Serialize migrations: concurrent HTTP retries must not spawn two writers.
        if not hasattr(self, "_account_migration_lock"):
            self._account_migration_lock = asyncio.Lock()
        async with self._account_migration_lock:
            store = _read_meta_store()
            sid, entry = _latest_resume_entry(store, requested_id)
            if not entry:
                raise KeyError(f"no metadata for session {requested_id}")
            if entry.get("provider") == "codex" and sid != requested_id:
                return await self.resume(sid)
            if entry.get("provider") != "codex_2":
                raise RuntimeError("migration requires an exact codex_2 PTY id")
            current = self.get(sid)
            if current is not None and not current.closed and current.to_meta().get("working"):
                raise RuntimeError(f"session {sid} is working; interrupt it explicitly before migration")
            native_id = str(entry.get("provider_session_id") or "")
            try:
                uuid.UUID(native_id)
            except ValueError as exc:
                raise RuntimeError(f"session {sid} has no valid native UUID") from exc
            source = _codex_jsonl_for(native_id, "codex_2")
            if source is None:
                raise RuntimeError(f"native rollout missing for {native_id}")
            source = Path(source).resolve()
            source_root = (codex_home_for("codex_2") / "sessions").resolve()
            try:
                relative = source.relative_to(source_root)
            except ValueError as exc:
                raise RuntimeError(f"rollout outside account sessions: {source}") from exc
            payload = source.read_bytes()
            try:
                first = json.loads(payload.splitlines()[0])
                valid = first.get("type") == "session_meta" and first.get("payload", {}).get("id") == native_id
            except (ValueError, IndexError, AttributeError):
                valid = False
            if not valid:
                raise RuntimeError(f"rollout session_meta UUID mismatch: {source}")
            primary_home = codex_home_for("codex").resolve()
            target = primary_home / "sessions" / relative
            if target.resolve() == source:
                raise RuntimeError("source and primary account homes must differ")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with target.open("xb") as output:
                    output.write(payload)
            except FileExistsError:
                if target.read_bytes() != payload:
                    raise RuntimeError(f"different native rollout already exists; preserved: {target}")
            resume_cmd = _resume_command("codex", native_id, original_cmd=list(entry.get("cmd") or []))
            from omnicompany.packages.services._core.identity import (
                binding_for_session, update_session_binding,
            )
            binding = binding_for_session(provider="codex_2", session_id=native_id, pty_id=sid)
            # Keep the source alive until the operator verifies the successor.
            # No auth, database or cache is copied.
            resumed = await self.create(
                cmd=resume_cmd, cwd=entry.get("cwd"), safe_mode=False,
                extra_env={"CODEX_HOME": str(primary_home)},
                resume_provider_session_id=native_id, resumed_from_pty_id=sid,
                origin="human", visibility="visible",
                cols=int(entry.get("cols") or DEFAULT_COLS), rows=int(entry.get("rows") or DEFAULT_ROWS),
            )
            resumed.active_plan = entry.get("active_plan")
            if binding:
                fields = {key: binding[key] for key in (
                    "project", "workstream", "active_plan", "task_id", "topic",
                    "session_id", "scope_segments", "binding_kind", "source",
                ) if key in binding}
                update_session_binding(resumed.id, **fields, pty_id=resumed.id, provider="codex")
            update_meta_field(resumed.id, active_plan=resumed.active_plan,
                              migrated_rollout_source=str(source), migrated_rollout_target=str(target))
            update_meta_field(sid, resumed_to=resumed.id)
            if requested_id != sid:
                update_meta_field(requested_id, resumed_to=resumed.id)
            self._register_resume_aliases(store, requested_id, resumed.id)
            return resumed

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
        if provider == "codex_2":
            return await self.migrate_codex_account(requested_id)
        provider_session_id = entry.get("provider_session_id") or (
            entry.get("claude_session_id") if provider in CLAUDE_FAMILY_PROVIDER_IDS else None
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
        resume_cmd = _resume_command(
            str(provider), str(provider_session_id), original_cmd=list(cmd)
        )
        # codex_2 carries its account nowhere on argv, so the resumed PTY has to
        # be handed the same CODEX_HOME explicitly. provider_terminal_environment
        # would also set it, but only *after* classification — and classification
        # is what reads this env, so passing it here is what breaks the cycle.
        # Without it a codex_2 resume would relaunch as the primary account and
        # then fail to find the thread, which lives in the other store.
        resume_env = second_account_env(provider) or None
        resumed = await self.create(
            cmd=resume_cmd,
            cwd=cwd,
            safe_mode=False,
            extra_env=resume_env,
            resume_claude_session_id=(
                str(provider_session_id) if provider in CLAUDE_FAMILY_PROVIDER_IDS else None
            ),
            resume_provider_session_id=str(provider_session_id),
            resumed_from_pty_id=recoverable_id,
        )
        update_meta_field(recoverable_id, resumed_to=resumed.id)
        if requested_id != recoverable_id:
            update_meta_field(requested_id, resumed_to=resumed.id)
        self._register_resume_aliases(store, requested_id, resumed.id)
        return resumed

    async def attach(
        self, sid: str, *, last_seq: int | None = None
    ) -> (
        tuple[PtySession, asyncio.Queue[Any], list[str]]
        | tuple[PtySession, asyncio.Queue[Any], list[str], dict[str, Any]]
    ):
        """Subscribe a client. Returns (session, queue, replay-snapshot).

        PERF-OBSERVATORY P4.1 incremental resume: when ``last_seq`` is given,
        also returns a 4th element ``resume``. If the ring still covers
        ``last_seq`` this is ``{"resumed": True, "from_seq": N, "chunks": [...]}``
        — ``chunks`` is *only* the incremental output after ``last_seq`` and
        ``snapshot`` is left empty; the caller must send those chunks as live
        ``output`` frames, never as a snapshot (no client-side ``term.reset()``).
        Otherwise it's ``{"resumed": False}`` and ``snapshot`` is populated
        exactly as when ``last_seq`` is omitted — the full ring replay, i.e.
        the unchanged old-client path. Omitting ``last_seq`` entirely keeps the
        original 3-tuple return and behavior with zero change.
        """
        sess = self.get(sid)
        if sess is None or sess.closed:
            raise KeyError(sid)
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=1024)
        async with sess.stream_lock:
            # A daemon can be reloaded with a smaller replay policy while its
            # detached PTY host survives. Trim any already-connected proxy ring
            # before exposing it to a browser; reconnecting hosts are bounded by
            # ``_ensure_host_connected`` below.
            if sess.ring_bytes > RING_BYTE_CAP or len(sess.ring) > RING_CAP:
                ring, ring_bytes, replay_truncated = _build_proxy_replay(
                    list(sess.ring)
                )
                sess.ring = ring
                sess.ring_bytes = ring_bytes
                sess.replay_truncated = sess.replay_truncated or replay_truncated
            # The resume decision must see the ring *after* a host reconnect
            # (which can replace it wholesale with the host's own replay), so
            # ensure connectivity first and decide afterward.
            if sess.host_port:
                await self._ensure_host_connected(sess, want_snapshot=False)
            resume: dict[str, Any] | None = (
                self._resume_decision(sess, last_seq) if last_seq is not None else None
            )
            if resume is not None and resume.get("resumed"):
                snapshot: list[str] = []
            else:
                # sess.ring_bytes tracks the *current* ring's byte size
                # incrementally (see _append_proxy_output / _build_proxy_replay)
                # and already reflects sess.ring, the source of ``snapshot``.
                snapshot = list(sess.ring)
                try:
                    self._latency_monitor.observe_snapshot_replay(sess.ring_bytes)
                except Exception:  # noqa: BLE001 - passive observation, fail-open
                    pass
            sess.subscribers.add(q)
            if sess.input_controller is None:
                sess.input_controller = q
        # A truncated raw replay cannot rebuild a full-screen TUI — today that
        # attach paints blank. If the host keeps a screen model, substitute one
        # self-contained current-screen frame (O(cols x rows), independent of
        # session age). Runs after stream_lock is released: the reply shares
        # the host bridge with output frames, whose fanout needs that lock.
        # Chunks published to ``q`` meanwhile may also be included in the
        # frame; they are cursor-addressed repaints, so re-applying them is
        # visually idempotent. Any failure falls back to the raw replay.
        if (
            sess.replay_truncated
            and sess.host_screen_model
            and sess.provider in PTY_AGENT_PROVIDER_IDS
            and (resume is None or not resume.get("resumed"))
        ):
            frame = await self._request_host_screen(sess)
            if frame:
                snapshot = [frame]
        if last_seq is None:
            return sess, q, snapshot
        return sess, q, snapshot, (resume or {"resumed": False})

    @staticmethod
    def _resume_decision(sess: PtySession, last_seq: int) -> dict[str, Any]:
        """Can this ring serve an incremental replay after ``last_seq``?

        The ring's live window is ``[base_seq, next_seq - 1]``. ``last_seq ==
        base_seq - 1`` means the client already has everything up to (but not
        including) the oldest buffered chunk — a perfect incremental resume.
        ``last_seq`` inside the window is a client that's behind but still
        coverable. Anything else (evicted by ring truncation, or a stale
        ``last_seq`` from a different daemon-process generation whose seq
        counter restarted at 0) falls back to a full snapshot, exactly like an
        unset ``last_seq``.
        """
        base_seq = sess.next_seq - len(sess.ring)
        newest_seq = sess.next_seq - 1
        if not (base_seq - 1 <= last_seq <= newest_seq):
            return {"resumed": False}
        skip = last_seq - base_seq + 1
        return {
            "resumed": True,
            "from_seq": last_seq + 1,
            "chunks": list(sess.ring)[skip:],
        }

    def detach(self, sess: PtySession, q: asyncio.Queue[Any]) -> None:
        sess.subscribers.discard(q)
        sess.terminal_response_tails.pop(id(q), None)
        sess.subscriber_overflow_since.pop(id(q), None)
        if sess.input_controller is q:
            sess.input_controller = None
        if not sess.subscribers:
            sess.last_detach_at = time.time()
            _persist_last_detach_at(sess)

    @staticmethod
    def _disconnect_slow_subscriber(
        sess: PtySession,
        queue: asyncio.Queue[Any],
    ) -> None:
        """Reconnect a slow viewer instead of leaving a silently frozen tab.

        PERF-OBSERVATORY P4.1: this is now a last resort. An overflowed queue
        is normally paused and inline-resynced (see ``_publish_output`` /
        ``_resync_if_overflowed_locked``); this kick path only fires when a
        subscriber stays paused past ``SUBSCRIBER_OVERFLOW_KICK_TIMEOUT_S``
        without ever draining — a genuinely frozen tab, not a brief stall.
        """
        sess.subscribers.discard(queue)
        sess.terminal_response_tails.pop(id(queue), None)
        sess.subscriber_overflow_since.pop(id(queue), None)
        if sess.input_controller is queue:
            sess.input_controller = None
        if not sess.subscribers:
            sess.last_detach_at = time.time()
            _persist_last_detach_at(sess)
        try:
            while True:
                queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait({"type": "proxy_reconnect"})
        except asyncio.QueueFull:
            pass
        try:
            get_latency_monitor().record_subscriber_kick()
        except Exception:  # noqa: BLE001 - passive observation, fail-open
            pass

    def _publish_output(self, sess: PtySession, chunk: str) -> None:
        """Append output to the ring under a fresh seq and fan out to subscribers.

        PERF-OBSERVATORY P4.1 backpressure: a subscriber whose queue is full is
        paused rather than disconnected — no further chunks are enqueued for
        it (the ring keeps accumulating regardless) until its queue drains to
        ``SUBSCRIBER_RESYNC_LOW_WATER``, at which point it gets one inline
        ring resync (reusing the snapshot framing) instead of every chunk it
        missed. A subscriber that never drains within
        ``SUBSCRIBER_OVERFLOW_KICK_TIMEOUT_S`` is still force-reset via the
        original kick path as a last resort.
        """
        seq = sess.next_seq
        sess.next_seq = seq + 1
        _append_proxy_output(sess, chunk)
        for q in list(sess.subscribers):
            qid = id(q)
            overflow_since = sess.subscriber_overflow_since.get(qid)
            if overflow_since is not None:
                if q.qsize() <= SUBSCRIBER_RESYNC_LOW_WATER:
                    self._resync_if_overflowed_locked(sess, q)
                elif time.time() - overflow_since > SUBSCRIBER_OVERFLOW_KICK_TIMEOUT_S:
                    self._disconnect_slow_subscriber(sess, q)
                continue
            try:
                q.put_nowait((seq, chunk))
            except asyncio.QueueFull:
                sess.subscriber_overflow_since[qid] = time.time()
                self._ensure_overflow_watchdog(sess, q)

    def _ensure_overflow_watchdog(self, sess: PtySession, q: asyncio.Queue[Any]) -> None:
        """Overflow recovery must not depend on the producer emitting again.

        Without this, a burst that overflows a subscriber followed by a quiet
        session leaves it paused forever (neither the low-water resync nor the
        30s force-kick in ``_publish_output`` ever runs again) — the frozen-tab
        symptom. One watchdog per paused subscriber; exits when unpaused/gone.
        """
        qid = id(q)
        existing = sess.subscriber_overflow_watchdogs.get(qid)
        if existing is not None and not existing.done():
            return
        try:
            task = asyncio.get_running_loop().create_task(
                self._overflow_watchdog(sess, q)
            )
            sess.subscriber_overflow_watchdogs[qid] = task
        except Exception:  # noqa: BLE001 - fail-open; publish 路径不可被观测反噬
            pass

    async def _overflow_watchdog(self, sess: PtySession, q: asyncio.Queue[Any]) -> None:
        qid = id(q)
        try:
            while True:
                await asyncio.sleep(0.5)
                async with sess.stream_lock:
                    since = sess.subscriber_overflow_since.get(qid)
                    if since is None or q not in sess.subscribers or sess.closed:
                        return
                    if q.qsize() <= SUBSCRIBER_RESYNC_LOW_WATER:
                        if self._resync_if_overflowed_locked(sess, q):
                            return
                    if time.time() - since > SUBSCRIBER_OVERFLOW_KICK_TIMEOUT_S:
                        self._disconnect_slow_subscriber(sess, q)
                        return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - fail-open
            pass
        finally:
            sess.subscriber_overflow_watchdogs.pop(qid, None)

    def _resync_if_overflowed_locked(
        self, sess: PtySession, queue: asyncio.Queue[Any]
    ) -> bool:
        """Give a paused subscriber one ring resync instead of a forced kick.

        Caller must already hold ``sess.stream_lock`` — the fan-out loops do;
        external callers (visibility:true) should go through
        ``resync_if_overflowed`` below, which acquires it. No-op (returns
        False) if this subscriber isn't currently paused.
        """
        qid = id(queue)
        if qid not in sess.subscriber_overflow_since:
            return False
        base_seq = sess.next_seq - len(sess.ring)
        last_seq = sess.next_seq - 1
        payload = {
            "type": "resync",
            "base_seq": base_seq,
            "last_seq": last_seq,
            "chunks": list(sess.ring),
            # Screen-model sessions: the WS pump substitutes one self-contained
            # host-rendered frame for this raw replay (ring kept as fallback).
            "prefer_screen": True,
        }
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # 满队列是 overflow 的常态(隐藏页签期间 sender 不消费)。resync 本身
            # 完整重放 ring, 队里积压的数据 chunk 全被它涵盖 —— 腾掉它们无损;
            # dict 控制消息(exit/reconnect/resync)保留。
            kept: list[Any] = []
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(item, dict):
                    kept.append(item)
            for item in kept:
                try:
                    queue.put_nowait(item)
                except asyncio.QueueFull:
                    break
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                return False
        sess.subscriber_overflow_since.pop(qid, None)
        try:
            self._latency_monitor.record_subscriber_inline_resync()
        except Exception:  # noqa: BLE001 - passive observation, fail-open
            pass
        return True

    async def resync_if_overflowed(
        self, sess: PtySession, queue: asyncio.Queue[Any]
    ) -> bool:
        """Public entry for a caller not already holding ``sess.stream_lock``
        (the WS route's ``visibility:true`` handler) to catch a paused
        subscriber up immediately rather than waiting for the next chunk's
        opportunistic low-water check."""
        async with sess.stream_lock:
            return self._resync_if_overflowed_locked(sess, queue)

    async def _publish_geometry(
        self,
        sess: PtySession,
        cols: int,
        rows: int,
        targets: list[asyncio.Queue[Any]],
    ) -> None:
        """Tell viewers the PTY grid they must render at.

        Only the input controller sizes the PTY. A viewer whose xterm keeps
        its own fit width renders full-width rules and relative cursor moves
        at the wrong width, which smears inline TUIs (Claude Code's prompt
        box) into screens of broken horizontal lines. Viewers adopt this
        grid instead. A full queue (a hidden tab stops draining) must not
        drop the hint: nothing re-sends it, and that viewer would replay and
        repaint at its stale grid after reveal. Queued output is evicted to
        make room; the viewer is then paused and caught up by the usual
        one-ring resync, so no output is lost either.
        """
        payload = {"type": "geometry", "cols": cols, "rows": rows}
        async with sess.stream_lock:
            for q in targets:
                self._enqueue_control_locked(sess, q, dict(payload))

    def _enqueue_control_locked(
        self,
        sess: PtySession,
        q: asyncio.Queue[Any],
        item: dict[str, Any],
    ) -> bool:
        """Put a control message, evicting queued output chunks if full.

        Caller holds ``sess.stream_lock``. Control messages already queued
        stay in order. Evicted chunks mark the subscriber overflowed, so it
        receives one ring resync once it drains (see ``_publish_output``).
        """
        try:
            q.put_nowait(item)
            return True
        except asyncio.QueueFull:
            pass
        kept: list[Any] = []
        evicted = 0
        while True:
            try:
                queued = q.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(queued, dict):
                kept.append(queued)
            else:
                evicted += 1
        for queued in kept:
            q.put_nowait(queued)
        if evicted:
            sess.subscriber_overflow_since.setdefault(id(q), time.time())
            self._ensure_overflow_watchdog(sess, q)
        try:
            q.put_nowait(item)
            return True
        except asyncio.QueueFull:
            return False

    async def _publish_screen_resync(
        self,
        sess: PtySession,
        *,
        only: asyncio.Queue[Any] | None = None,
    ) -> None:
        """Enqueue one ``prefer_screen`` resync for every subscriber (or ``only``).

        Screen-model sessions converge viewers by pushing one atomic
        host-rendered frame (substituted by the WS pump) instead of nudging
        the guest TUI into a full-transcript reprint. The raw ring rides
        along as fallback for a dead/unreachable model. Queued data chunks
        predate the frame render and are covered by it, so a full queue may
        evict them losslessly; dict control messages are kept, and chunks
        arriving after the render land behind the payload in order.
        """
        async with sess.stream_lock:
            payload = {
                "type": "resync",
                "base_seq": sess.next_seq - len(sess.ring),
                "last_seq": sess.next_seq - 1,
                "chunks": list(sess.ring),
                "prefer_screen": True,
            }
            targets = [only] if only is not None else list(sess.subscribers)
            for q in targets:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    kept: list[Any] = []
                    while True:
                        try:
                            item = q.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if isinstance(item, dict):
                            kept.append(item)
                    for item in kept:
                        try:
                            q.put_nowait(item)
                        except asyncio.QueueFull:
                            break
                    try:
                        q.put_nowait(payload)
                    except asyncio.QueueFull:
                        continue
                # A successfully enqueued resync catches this subscriber up;
                # let live chunks resume instead of waiting for the overflow
                # watchdog to notice the drain.
                sess.subscriber_overflow_since.pop(id(q), None)

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
            # 人工输入即视为接管，自动续跑预算清零。
            sess.auto_continue_count = 0
            try:
                from .codex_capacity_retry import get_service

                asyncio.create_task(
                    get_service().cancel_for_session(sess.id, "human-input"),
                    name=f"codex-capacity-cancel-human-{sess.id}",
                )
            except Exception:
                pass
        launch_provider = _pending_launch_provider(sess, data)
        outgoing = _with_shell_launch_bypass(data, launch_provider)
        track_reaction = not bool(sess.latency_pending_input_ns)
        latency_started_ns = self._latency_monitor.begin_pty_input(
            sid,
            track_reaction=track_reaction,
        )
        if track_reaction:
            sess.latency_pending_input_ns = latency_started_ns
        try:
            if sess.host_port:
                await self._send_host(sess, {"type": "input", "data": outgoing})
            elif sess.pty is not None:
                # winpty PTY.write expects str; runs in thread to avoid blocking loop.
                await _pty_io(sess.pty.write, outgoing)
            else:
                raise KeyError(sid)
        except Exception:
            self._latency_monitor.cancel_pty_input(sid, latency_started_ns)
            if sess.latency_pending_input_ns == latency_started_ns:
                sess.latency_pending_input_ns = 0
            raise
        self._latency_monitor.finish_pty_input(sid, latency_started_ns)
        previous_provider = sess.provider
        previous_has_user_turn = sess.has_user_turn
        previous_last_submit_at = sess.last_submit_at
        previous_provider_title = sess.provider_title
        submitted_prompt = _note_user_input(sess, data)
        if submitted_prompt and sess.provider in PTY_COMPANION_FALLBACK_PROVIDER_IDS:
            await asyncio.to_thread(
                _capture_session_companion_turn,
                sess,
                submitted_prompt,
            )
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
            if sess.provider in _NATIVE_LINK_PROVIDER_IDS and (
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
        cols = max(2, min(500, int(cols)))
        rows = max(1, min(200, int(rows)))
        if source is not None:
            if sess.input_controller is None and source in sess.subscribers:
                # The controller left (tab hidden or closed). A remaining
                # viewer still reporting its grid takes over sizing;
                # otherwise nobody could resize this PTY until someone types.
                sess.input_controller = source
            if sess.input_controller is not source:
                # Never let a non-controller change the controller's
                # geometry, but do not leave it rendering at its own width
                # either: hand it the PTY grid and one fresh frame at it.
                if (cols, rows) != (sess.cols, sess.rows):
                    await self._publish_geometry(sess, sess.cols, sess.rows, [source])
                    if sess.host_screen_model:
                        await self._publish_screen_resync(sess, only=source)
                    elif sess.provider in PTY_AGENT_PROVIDER_IDS:
                        # No host screen model (e.g. pyte missing from the
                        # daemon's interpreter): this viewer has been rendering
                        # output at its own fit grid. Adopting the PTY grid does
                        # not undo that, and Claude Code's diff renderer never
                        # re-sends cells it believes unchanged, so the screen
                        # stays torn (dropped characters, stale rows). Ask the
                        # TUI for a self-contained repaint at the PTY grid; it
                        # reaches this viewer after the geometry hint.
                        await self.redraw(sid, sess.cols, sess.rows, ensure=True)
                return
        if cols == sess.cols and rows == sess.rows:
            return
        # Other viewers adopt the new grid before any output rendered at it.
        followers = [q for q in sess.subscribers if q is not source]
        if followers:
            await self._publish_geometry(sess, cols, rows, followers)
        if sess.host_screen_model:
            # Screen-model sessions repaint from the host's VT model: resize
            # the model (host-side) and push one self-contained frame at the
            # new geometry. No reliance on the guest TUI answering SIGWINCH
            # (codex under winpty ignores it) and no transcript re-print, so
            # no burst-coalescing window either.
            if sess.host_port:
                await self._send_host(sess, {"type": "resize", "cols": cols, "rows": rows})
            elif sess.pty is not None:
                await _pty_io(sess.pty.set_size, cols, rows)
            else:
                raise KeyError(sid)
            sess.cols = cols
            sess.rows = rows
            await self._publish_screen_resync(sess)
            return
        # A real grid change makes agent TUIs re-print their transcript; let
        # the WS fanout coalesce that burst into an atomic repaint.
        sess.output_burst_until = time.monotonic() + REDRAW_BURST_WINDOW_S
        if sess.host_port:
            await self._send_host(sess, {"type": "resize", "cols": cols, "rows": rows})
        elif sess.pty is not None:
            await _pty_io(sess.pty.set_size, cols, rows)
        else:
            raise KeyError(sid)
        sess.cols = cols
        sess.rows = rows

    def _schedule_trailing_redraw(self, sess: PtySession, now: float) -> None:
        """Run one repaint when the redraw floor window ends (coalesced)."""
        pending = sess.trailing_redraw_task
        if pending is not None and not pending.done():
            return
        delay = max(0.0, sess.last_redraw_at + REDRAW_MIN_INTERVAL_S - now)

        async def run() -> None:
            await asyncio.sleep(delay)
            if sess.closed:
                return
            try:
                # The session grid at run time: a follower adopted it, and the
                # controller may have resized again since the request.
                await self.redraw(sess.id, sess.cols, sess.rows, ensure=True)
            except KeyError:
                pass
            except Exception:  # noqa: BLE001 - a repaint hint must not kill the loop
                logger.debug("trailing redraw failed for %s", sess.id, exc_info=True)

        sess.trailing_redraw_task = asyncio.create_task(
            run(), name=f"pty-trailing-redraw-{sess.id}"
        )

    async def redraw(
        self,
        sid: str,
        cols: int,
        rows: int,
        *,
        source: asyncio.Queue[Any] | None = None,
        ensure: bool = False,
    ) -> None:
        """Ask a full-screen TUI to repaint after a truncated ANSI replay.

        The replay buffer intentionally has a byte cap, so a long-running
        alternate-screen application may lose the escape sequences that built
        its current screen. A short geometry nudge produces SIGWINCH in ConPTY;
        restoring the requested size makes the still-running application emit a
        fresh, self-contained screen without injecting keyboard input.

        ``ensure``: the caller's viewer cannot be correct without a repaint
        produced *after* this call (it just changed grid). Inside the floor
        window such a request is coalesced into one trailing repaint at the
        end of the window instead of being dropped: the earlier repaint may
        have been rendered at the viewer's old grid.
        """
        sess = self.get(sid)
        if sess is None or sess.closed:
            raise KeyError(sid)
        if sess.host_screen_model:
            # Screen-model sessions never nudge: converge every viewer with
            # one host-rendered frame (the WS pump substitutes it for the
            # ring fallback). No guest cooperation, no transcript re-print,
            # no burst window. The throttle floor stays as cheap protection
            # against stacked triggers (reveal + attach + several tabs).
            now = time.monotonic()
            if now - sess.last_redraw_at < REDRAW_MIN_INTERVAL_S:
                if ensure:
                    self._schedule_trailing_redraw(sess, now)
                return
            sess.last_redraw_at = now
            await self._publish_screen_resync(sess)
            return
        if source is not None and sess.input_controller is not source:
            # A viewer without input control still legitimately needs a
            # repaint after a truncated replay (silently dropping it leaves
            # that tab blank forever). Honor the nudge, but never let a
            # non-controller change the controller's geometry: jiggle around
            # the session's current size instead of the caller's.
            cols, rows = sess.cols, sess.rows

        # PTY-RELOAD-CRAWL: every nudge costs agent TUIs a full-transcript
        # re-print fanned out to all subscribers. Stacked triggers (truncated
        # replay + reveal + several tabs opening) within the floor are dropped;
        # the earlier re-print already rebuilt the same screen for everyone.
        # ``ensure`` callers are the exception (see docstring): coalesced into
        # one trailing nudge, so the floor still bounds the re-print rate.
        now = time.monotonic()
        if now - sess.last_redraw_at < REDRAW_MIN_INTERVAL_S:
            if ensure:
                self._schedule_trailing_redraw(sess, now)
            return
        sess.last_redraw_at = now
        sess.output_burst_until = now + REDRAW_BURST_WINDOW_S

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
                await _pty_io(sess.pty.set_size, cols, target_rows)
            else:
                raise KeyError(sid)

        await set_size(nudge_rows)
        try:
            await asyncio.sleep(0.04)
        finally:
            await set_size(rows)
        sess.cols = cols
        sess.rows = rows

    async def kill(self, sid: str, *, requester: dict[str, Any] | None = None) -> bool:
        sess = self.get(sid)
        if sess is None:
            return False

        # Persist the recovery tombstone before touching the PTY host. Browser
        # polls racing with a kill must never observe this conversation as a
        # recoverable background session, even while other tabs are attached.
        killed_at = time.time()
        requester = requester or {}
        update_meta_field(
            sess.id,
            ended_at=killed_at,
            exit_reason="kill",
            close_reason="kill",
            close_time=killed_at,
            close_actor=requester.get("actor"),
            termination_reason="killed",
            recovery_disabled=True,
            provider_session_id=sess.provider_session_id,
            claude_session_id=sess.claude_session_id,
            active_plan=sess.active_plan,
            kill_requester_ip=requester.get("ip"),
            kill_requester_ua=requester.get("user_agent"),
            kill_requester_referer=requester.get("referer"),
        )
        # 2026-08-15 用户报告会话持续"莫名其妙变少"。kill 一律走这条审计线,
        # 用 main.py 同款 stderr 模式保证进 daemon log(uvicorn 不转发 app logger)。
        print(
            f"[ccdaemon] session killed id={sess.id} provider={sess.provider} "
            f"age={int(killed_at - sess.started_at)}s "
            f"ip={requester.get('ip') or '?'} "
            f"ua={requester.get('user_agent') or '?'} "
            f"referer={requester.get('referer') or '?'}",
            file=sys.stderr,
            flush=True,
        )
        if sess.host_port:
            await asyncio.to_thread(
                _enqueue_session_companion_fallback,
                sess,
                reason="kill",
            )
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

    async def close_for_handoff(self, sid: str) -> bool:
        """Close a replaced PTY while keeping its provider conversation resumable."""
        sess = self.get(sid)
        if sess is None:
            return False
        await self._close_session(sess, reason="provider-switch")
        return True

    def _maybe_auto_continue(self, sess: PtySession, chunk: str) -> None:
        """认到传输层错误行就排一次"继续"注入，连续上限 AUTO_CONTINUE_MAX。"""
        if AUTO_CONTINUE_MAX <= 0 or sess.closed:
            return
        task = sess.auto_continue_task
        if task is not None and not task.done():
            return  # 已经排了一次，等它落地
        tail = (sess.auto_continue_tail + _strip_ansi_for_match(chunk))
        tail = tail[-AUTO_CONTINUE_TAIL_CAP:]
        low = tail.lower()
        hit = next((p for p in AUTO_CONTINUE_PATTERNS if p in low), None)
        if hit is None:
            sess.auto_continue_tail = tail
            return
        # 清尾窗：同一条错误只触发一次，不让它在后续 chunk 里被反复命中。
        sess.auto_continue_tail = ""
        if sess.auto_continue_count >= AUTO_CONTINUE_MAX:
            logger.warning(
                "pty_session %s auto-continue budget spent (%d/%d), leaving it to a human",
                sess.id,
                sess.auto_continue_count,
                AUTO_CONTINUE_MAX,
            )
            return
        sess.auto_continue_count += 1
        logger.warning(
            "pty_session %s hit transport error (%r); auto-continue %d/%d",
            sess.id,
            hit,
            sess.auto_continue_count,
            AUTO_CONTINUE_MAX,
        )
        sess.auto_continue_task = asyncio.create_task(
            self._auto_continue_write(sess, sess.auto_continue_count)
        )

    async def _auto_continue_write(self, sess: PtySession, attempt: int) -> None:
        """退避后把"继续"写进 PTY。source=None 所以不会清掉自身计数。"""
        try:
            await asyncio.sleep(AUTO_CONTINUE_DELAY_S * attempt)
            if sess.closed:
                return
            await self.write(sess.id, AUTO_CONTINUE_TEXT + "\r")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("pty_session %s auto-continue write failed: %s", sess.id, e)

    async def _reader_loop(self, sess: PtySession) -> None:
        """Pump PTY → subscribers + ring buffer until child exits.

        CC-PLAN-SESSION-CONTEXT 段五 (2026-05-05): chunk 热时连读排干 (最多
        READ_HOT_DRAIN_MAX 次), 把可能已经在 PTY buffer 里的连续字节合并到一帧.
        减 JSON 编码 + WS 帧次, 减重复 to_thread 切换开销.
        """
        try:
            while not sess.closed:
                try:
                    chunk = await _pty_io(sess.pty.read, False)
                except Exception as e:  # winpty.WinptyError, etc.
                    logger.warning("pty_session %s read error: %s", sess.id, e)
                    break
                if chunk:
                    if sess.latency_pending_input_ns:
                        pending_ns = sess.latency_pending_input_ns
                        sess.latency_pending_input_ns = 0
                        self._latency_monitor.finish_pty_reaction(sess.id, pending_ns)
                    # hot-drain: 不睡再多读几次, 合并 burst
                    for _ in range(READ_HOT_DRAIN_MAX):
                        try:
                            more = await _pty_io(sess.pty.read, False)
                        except Exception:
                            break
                        if not more:
                            break
                        chunk += more
                    async with sess.stream_lock:
                        observed_at = time.time()
                        sess.last_output_at = observed_at
                        was_working = sess.provider_runtime.working(now=observed_at)
                        runtime_changed = sess.provider_runtime.feed(chunk, now=observed_at)
                        self._publish_output(sess, chunk)
                    if runtime_changed and was_working:
                        asyncio.create_task(
                            asyncio.to_thread(
                                _enqueue_companion_after_idle,
                                sess,
                                was_working=was_working,
                                observed_at=observed_at,
                            ),
                            name=f"pty-companion-idle-{sess.id}",
                        )
                    self._maybe_auto_continue(sess, chunk)
                else:
                    if not sess.pty.isalive():
                        # process exited; do one final drain attempt then stop
                        tail = await _pty_io(sess.pty.read, False)
                        if tail:
                            if sess.latency_pending_input_ns:
                                pending_ns = sess.latency_pending_input_ns
                                sess.latency_pending_input_ns = 0
                                self._latency_monitor.finish_pty_reaction(
                                    sess.id,
                                    pending_ns,
                                )
                            async with sess.stream_lock:
                                observed_at = time.time()
                                sess.last_output_at = observed_at
                                was_working = sess.provider_runtime.working(now=observed_at)
                                runtime_changed = sess.provider_runtime.feed(tail, now=observed_at)
                                self._publish_output(sess, tail)
                            if runtime_changed and was_working:
                                asyncio.create_task(
                                    asyncio.to_thread(
                                        _enqueue_companion_after_idle,
                                        sess,
                                        was_working=was_working,
                                        observed_at=observed_at,
                                    ),
                                    name=f"pty-companion-idle-{sess.id}",
                                )
                        break
                    await asyncio.sleep(READ_IDLE_SLEEP_S)
        finally:
            await self._close_session(sess, reason="reader-exit")

    async def _provider_link_loop(self, sess: PtySession) -> None:
        """Persist the provider-native conversation displayed by this PTY."""
        if sess.provider == "kiro":
            await self._kiro_provider_link_loop(sess)
            return
        if sess.provider == "zcode":
            await self._zcode_provider_link_loop(sess)
            return
        try:
            # Every Codex variant resolves its native thread through this loop.
            # A hand-listed tuple silently starved codex_glm of a link watcher
            # even though it is registered in NATIVE_TRACE_PROVIDER_IDS.
            while not sess.closed and sess.provider in CODEX_FAMILY_PROVIDER_IDS:
                from .codex_runtime_identity import (
                    CODEX_IDENTITY_WATCH_EXECUTOR,
                    resolve_codex_runtime_identity,
                )

                identity = await asyncio.get_running_loop().run_in_executor(
                    CODEX_IDENTITY_WATCH_EXECUTOR, resolve_codex_runtime_identity, sess.child_pid,
                )
                provider_session_id = identity.get("native_session_id") if identity.get("known") else None
                provider_title = identity.get("title") if identity.get("known") else None
                if identity.get("known"):
                    sess.codex_process_uuid = identity.get("process_uuid")
                provider_title = _sanitize_provider_title(provider_title)
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
                        codex_home=identity.get("provider_home"),
                        codex_identity_source=identity.get("reason"),
                    )
                    logger.info(
                        "pty_session native Codex link id=%s provider_session_id=%s title=%r",
                        sess.id,
                        provider_session_id,
                        sess.provider_title,
                    )
                rollout = Path(identity["rollout_path"]) if identity.get("known") else None
                try:
                    rollout_mtime = rollout.stat().st_mtime if rollout is not None else 0.0
                except OSError:
                    rollout_mtime = 0.0
                if (
                    rollout_mtime
                    and sess.codex_rollout_mtime
                    and rollout_mtime > sess.codex_rollout_mtime
                ):
                    sess.provider_activity_seen = True
                if rollout_mtime:
                    sess.codex_rollout_mtime = rollout_mtime
                await asyncio.sleep(
                    CODEX_LINK_STABLE_POLL_S
                    if sess.provider_session_id and sess.codex_process_uuid
                    else CODEX_LINK_POLL_S
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("Codex native-link watcher failed for %s", sess.id, exc_info=True)

    async def _zcode_provider_link_loop(self, sess: PtySession) -> None:
        """Persist an unambiguous zcode native session selected by time and cwd."""
        try:
            while not sess.closed and sess.provider == "zcode":
                provider_session_id, provider_title = await asyncio.to_thread(
                    _poll_zcode_native_link,
                    started_at=sess.started_at,
                    cwd=sess.cwd,
                    provider_session_id=sess.provider_session_id,
                )
                provider_title = _sanitize_provider_title(provider_title)
                if provider_session_id and provider_session_id != sess.provider_session_id:
                    sess.provider_session_id = provider_session_id
                    sess.summary_title = None
                    if provider_title:
                        sess.provider_title = provider_title
                    update_meta_field(
                        sess.id,
                        provider_session_id=provider_session_id,
                        provider_title=sess.provider_title,
                        provider_session_link_source="zcode_time_cwd_derived",
                    )
                    logger.info(
                        "pty_session native zcode link id=%s provider_session_id=%s",
                        sess.id,
                        provider_session_id,
                    )
                await asyncio.sleep(
                    ZCODE_LINK_POLL_S if not sess.provider_session_id else KIRO_LINK_STABLE_POLL_S
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("zcode native-link watcher failed for %s", sess.id, exc_info=True)

    async def _kiro_provider_link_loop(self, sess: PtySession) -> None:
        """Persist the Kiro session bound to this PTY's process tree."""
        try:
            while not sess.closed and sess.provider == "kiro":
                provider_session_id, provider_title = await asyncio.to_thread(
                    _poll_kiro_native_link,
                    child_pid=sess.child_pid,
                    provider_session_id=sess.provider_session_id,
                )
                provider_title = _sanitize_provider_title(provider_title)
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
                    )
                    logger.info(
                        "pty_session native Kiro link id=%s provider_session_id=%s title=%r",
                        sess.id,
                        provider_session_id,
                        sess.provider_title,
                    )
                await asyncio.sleep(
                    KIRO_LINK_STABLE_POLL_S
                    if sess.provider_session_id
                    else KIRO_LINK_POLL_S
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("Kiro native-link watcher failed for %s", sess.id, exc_info=True)

    async def _close_session(self, sess: PtySession, *, reason: str) -> None:
        if sess.closed:
            return
        await asyncio.to_thread(
            _enqueue_session_companion_fallback,
            sess,
            reason=reason,
        )
        # Claim the close before asking a detached host to exit. The host may
        # report its low-level "kill" frame immediately; without this guard its
        # bridge races this caller and overwrites semantic reasons such as
        # provider-switch, turning a resumable handoff source into a dead link.
        sess.closed = True
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
        sess.latency_pending_input_ns = 0
        self._latency_monitor.end_session(sess.id)
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

        # Pure workers own a task-only cwd outside the workspace. Reclaim only
        # that exact generated root after the PTY is closed; native login homes
        # and resumable provider transcripts remain untouched.
        try:
            from .pure_runtime import cleanup_pure_runtime

            cleanup_pure_runtime(sess.cwd)
        except Exception:
            logger.warning("pure runtime cleanup failed for %s", sess.id, exc_info=True)

        # Mark terminated in the persistent store (don't delete — the session may
        # still be resumable via claude --resume <claude_session_id>).
        try:
            ended_at = time.time()
            end_fields: dict[str, Any] = {
                "ended_at": ended_at,
                "exit_reason": reason,
                "close_reason": reason,
                "close_time": ended_at,
                "close_actor": None,
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
                if now - self._last_archive_check_at >= META_STORE_ARCHIVE_CHECK_INTERVAL_S:
                    self._last_archive_check_at = now
                    try:
                        await asyncio.to_thread(_archive_stale_sessions, now=now)
                    except Exception:
                        logger.warning(
                            "cc_sessions archive maintenance tick failed", exc_info=True
                        )
                sessions = list(self._sessions.values())
                self._remind_overdue_agent_sessions(sessions, now)
                stale = [s for s in sessions if _is_idle_reap_candidate(s, now)]
                for s in stale:
                    reason = (
                        "agent-idle-ttl"
                        if session_current_owner(s) == SESSION_ORIGIN_AGENT
                        else "idle-ttl"
                    )
                    logger.info(
                        "reclaiming pty id=%s origin=%s reason=%s quiet_for=%.0fs",
                        s.id,
                        s.origin,
                        reason,
                        now - max(s.last_detach_at, s.last_output_at),
                    )
                    await self._close_session(s, reason=reason)
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _remind_overdue_agent_sessions(
        sessions: list[PtySession],
        now: float,
    ) -> None:
        """Nag the creator before we reclaim on their behalf.

        The create response already carries the contract, but a program that
        spawned a worker and moved on never re-reads it. A periodic warning on
        the daemon log is the one place an operator or a supervising agent will
        actually find the leak, and it names the exact command to run.
        """
        for sess in sessions:
            state = session_reclaim_state(sess, now)
            if not state.get("overdue"):
                continue
            if now - sess.reclaim_reminded_at < RECLAIM_REMINDER_INTERVAL_S:
                continue
            sess.reclaim_reminded_at = now
            logger.warning(
                "agent-created pty %s is overdue for reclaim (idle %.0fs, provider=%s); "
                "creator should run: omni cc session kill %s",
                sess.id,
                now - max(sess.last_detach_at, sess.last_output_at),
                sess.provider,
                sess.id,
            )


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
