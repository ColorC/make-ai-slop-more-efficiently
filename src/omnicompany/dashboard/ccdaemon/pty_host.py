"""Detached PTY owner used by the dashboard terminal infrastructure.

The ccdaemon is an HTTP/WebSocket proxy and is intentionally restartable.  It
must therefore never own a user's CLI process.  Each session is held by one of
these small detached hosts instead.  The host owns ConPTY, the native CLI and
the terminal replay buffer; ccdaemon clients may disappear and reconnect
without changing any of those three things.

IPC is a token-authenticated, length-prefixed JSON protocol bound to loopback.
No prompts or terminal output are written to the registry file.
"""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import re
import struct
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from .provider_runtime import ProviderRuntimeState, provider_terminal_environment
from .pty_screen import create_screen_model

MAX_FRAME_BYTES = 96 * 1024 * 1024
# Match the daemon's bounded replay window. Reconnect only needs a small tail
# before the provider redraws its TUI; larger ANSI transcripts can monopolize
# WebView2/xterm's main thread and make the session appear stuck.
DEFAULT_REPLAY_BYTES = int(os.environ.get("OMNI_PTY_REPLAY_BYTES") or 256 * 1024)
READ_IDLE_SLEEP_S = 0.01
READ_IDLE_SLEEP_MAX_S = max(
    READ_IDLE_SLEEP_S,
    float(os.environ.get("OMNI_PTY_READ_IDLE_MAX_S") or 0.10),
)
READ_IDLE_BACKOFF = 1.6
# ConPTY's non-blocking read is a poll.  Waking it once after input is not
# sufficient: the child can produce its echo a few milliseconds after that
# first read, at which point the ordinary idle backoff would add up to 100ms.
# Keep only the session that just received input hot for a short window; fully
# idle sessions retain the low-frequency backoff above.
READ_AFTER_INPUT_SLEEP_S = max(
    0.001,
    float(os.environ.get("OMNI_PTY_READ_AFTER_INPUT_SLEEP_S") or 0.002),
)
READ_AFTER_INPUT_WINDOW_S = max(
    READ_AFTER_INPUT_SLEEP_S,
    float(os.environ.get("OMNI_PTY_READ_AFTER_INPUT_WINDOW_S") or 0.25),
)
READ_HOT_DRAIN_MAX = 4
STATE_WRITE_RETRIES = 8
STATE_WRITE_RETRY_BASE_S = 0.01
SNAPSHOT_PROTOCOL_VERSION = 2
SNAPSHOT_FRAME_CHARS = 256 * 1024
FRAME_PARSE_OFFLOAD_BYTES = max(
    16 * 1024,
    int(os.environ.get("OMNI_PTY_FRAME_PARSE_OFFLOAD_BYTES") or 64 * 1024),
)
STATE_GENERATIONS_TO_KEEP = 4

logger = logging.getLogger(__name__)
_PTY_HOST_IO_EXECUTOR = ThreadPoolExecutor(
    max_workers=16,
    thread_name_prefix="pty-host-io",
)


async def _pty_host_io(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Keep ConPTY polling and writes out of asyncio's shared executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _PTY_HOST_IO_EXECUTOR,
        lambda: fn(*args, **kwargs),
    )


def next_read_idle_sleep(current: float) -> float:
    """Back off idle ConPTY probes while keeping active output on the hot path."""

    return min(READ_IDLE_SLEEP_MAX_S, max(READ_IDLE_SLEEP_S, current) * READ_IDLE_BACKOFF)


def next_read_wait(
    idle_sleep: float,
    *,
    hot_until: float,
    now: float | None = None,
) -> tuple[float, float]:
    """Choose the next poll delay and idle-backoff value.

    The post-input hot window is deliberately per-host and short-lived, so the
    cost depends on active typing rather than the total number of open sessions.
    """

    current = time.monotonic() if now is None else now
    if current < hot_until:
        return READ_AFTER_INPUT_SLEEP_S, READ_IDLE_SLEEP_S
    return idle_sleep, next_read_idle_sleep(idle_sleep)

_TERMINAL_QUERY_RE = re.compile(
    r"\x1b\](?P<osc>10|11|12);\?(?:\x07|\x1b\\)"
    r"|\x1b\]4;(?P<idx>[0-9]+);\?(?:\x07|\x1b\\)"
    r"|\x1b\[\?(?P<mode>\d+)\$p"
    r"|\x1b\[(?P<da_prefix>[>]?)(?:0)?c"
    r"|\x1b\[(?P<dsr>[56])n"
)
_PRIVATE_MODE_CHANGE_RE = re.compile(r"\x1b\[\?([0-9;]+)([hl])")


class TerminalQueryResponder:
    """Answer terminal capability queries beside ConPTY, without network RTT.

    Browser xterms are viewers and can disappear, sleep or coexist. A native
    TUI must not depend on one of them answering OSC/CSI queries before its
    short timeout expires. Keeping this responder beside the PTY also prevents
    late browser replies from becoming visible prompt text.
    """

    _COLORS = {
        "10": "eeee/f4f4/ffff",
        "11": "0d0d/2424/5050",
        "12": "d3d3/acac/6262",
    }
    # Codex queries OSC 10/11 during TUI startup but does not drain stdin until
    # its first user turn. Any reply written into the ConPTY input at that point
    # stays buffered and is merged into the next real prompt, polluting both the
    # first user message and Codex's native thread title (see the title
    # sanitizer in pty._sanitize_provider_title). Answering color queries for
    # these providers is therefore strictly harmful: skip OSC 10/11/12 replies.
    _NO_COLOR_QUERY_PROVIDERS = frozenset({"codex", "codex_the_company"})
    # opencode 等 TUI 会逐条查询 OSC 4 调色板 (4;0;? / 4;7;? ...) 探测明暗主题。
    # 关键槽位照 _COLORS 填 (0=背景, 7/15=前景), 其余给暗色默认值, 保证主题判定
    # 与 OSC 11 一致, 且不依赖浏览器的 xterm 回执 (那边会被 ccdaemon 过滤掉)。
    _PALETTE = {
        "0": _COLORS["11"],
        "7": _COLORS["10"],
        "8": "6a6a/8282/a8a8",
        "15": _COLORS["10"],
    }
    _PALETTE_DEFAULT = _COLORS["11"]

    def __init__(self) -> None:
        self._tail = ""
        self._private_modes: set[int] = set()

    @staticmethod
    def _incomplete_escape_tail(value: str) -> str:
        start = value.rfind("\x1b")
        if start < 0:
            return ""
        candidate = value[start:]
        if len(candidate) > 128:
            return ""
        if candidate.startswith("\x1b]"):
            return "" if ("\x07" in candidate or "\x1b\\" in candidate) else candidate
        if candidate.startswith("\x1b["):
            if len(candidate) < 3:
                return candidate
            final = ord(candidate[-1])
            return "" if 0x40 <= final <= 0x7E else candidate
        return candidate if len(candidate) < 2 else ""

    def feed(self, chunk: str, *, provider: str = "") -> str:
        combined = self._tail + chunk
        self._tail = self._incomplete_escape_tail(combined)

        for match in _PRIVATE_MODE_CHANGE_RE.finditer(combined):
            enabled = match.group(2) == "h"
            for raw_mode in match.group(1).split(";"):
                try:
                    mode = int(raw_mode)
                except ValueError:
                    continue
                if enabled:
                    self._private_modes.add(mode)
                else:
                    self._private_modes.discard(mode)

        skip_color_queries = provider in self._NO_COLOR_QUERY_PROVIDERS
        replies: list[str] = []
        for match in _TERMINAL_QUERY_RE.finditer(combined):
            osc = match.group("osc")
            if osc:
                if skip_color_queries:
                    continue
                replies.append(f"\x1b]{osc};rgb:{self._COLORS[osc]}\x1b\\")
                continue
            palette_idx = match.group("idx")
            if palette_idx is not None:
                color = self._PALETTE.get(palette_idx, self._PALETTE_DEFAULT)
                replies.append(f"\x1b]4;{palette_idx};rgb:{color}\x1b\\")
                continue
            raw_mode = match.group("mode")
            if raw_mode:
                mode = int(raw_mode)
                state = 1 if mode in self._private_modes else 2
                replies.append(f"\x1b[?{mode};{state}$y")
                continue
            da_prefix = match.group("da_prefix")
            if da_prefix is not None:
                replies.append("\x1b[>0;276;0c" if da_prefix == ">" else "\x1b[?1;2c")
                continue
            dsr = match.group("dsr")
            if dsr == "5":
                replies.append("\x1b[0n")
            elif dsr == "6":
                replies.append("\x1b[1;1R")
        return "".join(replies)


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    header = await reader.readexactly(4)
    size = struct.unpack("!I", header)[0]
    if size <= 0 or size > MAX_FRAME_BYTES:
        raise ValueError(f"invalid PTY host frame size: {size}")
    payload = await reader.readexactly(size)
    text = payload.decode("utf-8")
    # Snapshot chunks can be hundreds of kilobytes. Their JSON parse is pure
    # CPU, so keep it off the ccdaemon loop; ordinary output/control frames stay
    # inline and avoid a thread hop.
    if size >= FRAME_PARSE_OFFLOAD_BYTES:
        value = await _pty_host_io(json.loads, text)
    else:
        value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("PTY host frame must contain an object")
    return value


async def write_frame(writer: asyncio.StreamWriter, value: dict[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise ValueError(f"PTY host frame too large: {len(payload)}")
    writer.write(struct.pack("!I", len(payload)))
    writer.write(payload)
    await writer.drain()


def _quote_arg(value: str) -> str:
    if not value:
        return '""'
    if any(char in value for char in (" ", "\t", '"')):
        return '"' + value.replace('"', '\\"') + '"'
    return value


class _Client:
    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2048)
        self.task: asyncio.Task[None] | None = None


class PtyHost:
    """Own one PTY independently from every dashboard/ccdaemon process."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        pty_factory: Callable[[int, int], Any] | None = None,
    ) -> None:
        self.id = str(config["id"])
        self.token = str(config["token"])
        self.cmd = [str(part) for part in config["cmd"]]
        self.cwd = str(config["cwd"])
        self.cols = int(config.get("cols") or 120)
        self.rows = int(config.get("rows") or 32)
        self.provider = str(config.get("provider") or "shell")
        self.provider_runtime = ProviderRuntimeState(self.provider)
        self.started_at = float(config.get("started_at") or time.time())
        self.state_path = Path(str(config["state_path"]))
        raw_extra = config.get("extra_env") or {}
        self.extra_env = {str(k): str(v) for k, v in raw_extra.items()} if isinstance(raw_extra, dict) else {}
        self.replay_limit = int(config.get("replay_bytes") or DEFAULT_REPLAY_BYTES)
        self._pty_factory = pty_factory
        self.pty: Any = None
        self.child_pid: int | None = None
        self.port: int | None = None
        self.server: asyncio.AbstractServer | None = None
        self.clients: set[_Client] = set()
        self.ring: deque[str] = deque()
        self.ring_bytes = 0
        # A raw ANSI tail is only a faithful terminal snapshot while its
        # beginning is still present. Once the replay window evicts bytes, a
        # full-screen TUI must be asked to redraw after the browser reattaches.
        self.replay_truncated = False
        self.last_output_at = self.started_at
        self.last_input_at = 0.0
        self.exit_reason: str | None = None
        self.done = asyncio.Event()
        self.reader_task: asyncio.Task[None] | None = None
        self._state_write_task: asyncio.Task[bool] | None = None
        self._last_state_write = 0.0
        self._state_write_lock = threading.Lock()
        self._last_state_warning = 0.0
        self._last_read_warning = 0.0
        self._reader_wakeup = asyncio.Event()
        self._reader_hot_until = 0.0
        self._query_responder = TerminalQueryResponder()
        # Scale-insensitive reattach: a bounded VT emulator that can always
        # serialize the *current* screen, however old the session is. None for
        # shells / missing pyte — those keep the raw-ring behavior untouched.
        self.screen_model = create_screen_model(self.cols, self.rows, self.provider)
        self._codebuddy_conhost_reaper: Any = None
        self._codebuddy_conhost_reaper_task: asyncio.Task[None] | None = None

    @property
    def alive(self) -> bool:
        try:
            return bool(self.pty is not None and self.pty.isalive() and not self.done.is_set())
        except Exception:
            return False

    def _state(self) -> dict[str, Any]:
        state = {
            "schema": "omnicompany.pty-host.v2",
            "id": self.id,
            "host_pid": os.getpid(),
            "host_port": self.port,
            "host_token": self.token,
            "child_pid": self.child_pid,
            "cmd": self.cmd,
            "cwd": self.cwd,
            "cols": self.cols,
            "rows": self.rows,
            "provider": self.provider,
            "started_at": self.started_at,
            "alive": self.alive,
            "last_output_at": self.last_output_at,
            "last_input_at": self.last_input_at or None,
            "buffered_chunks": len(self.ring),
            "buffered_bytes": self.ring_bytes,
            "replay_limit": self.replay_limit,
            "replay_truncated": self.replay_truncated,
            "screen_model": bool(
                self.screen_model is not None and not self.screen_model.dead
            ),
            "terminal_query_responder": True,
            "exit_reason": self.exit_reason,
            "updated_at": time.time(),
        }
        state.update(self.provider_runtime.export())
        if self._codebuddy_conhost_reaper is not None:
            state["codebuddy_conhost_cleanup"] = (
                self._codebuddy_conhost_reaper.status()
            )
        return state

    def _write_state(self, *, force: bool = False) -> bool:
        """Persist discovery metadata without ever becoming a PTY failure mode.

        On Windows, another process can briefly hold the destination without
        FILE_SHARE_DELETE while reading it. ``os.replace`` then raises
        ``PermissionError`` even though both processes are healthy. This file
        is only a discovery cache: terminal ownership, replay and live output
        all stay in this process, so an exhausted write retry must be reported
        but must never end the reader loop.
        """
        with self._state_write_lock:
            now = time.time()
            if not force and now - self._last_state_write < 1.0:
                return True

            payload = json.dumps(self._state(), ensure_ascii=False)
            self.state_path.parent.mkdir(parents=True, exist_ok=True)

            # The primary discovery format is immutable generations. Readers
            # enumerate the newest completed file, so they never hold open the
            # destination of a writer's replace operation (Windows MoveFileEx
            # refuses replacement while the destination is open, regardless
            # of FILE_SHARE_DELETE).
            generation = time.time_ns()
            version_path = self.state_path.with_name(
                f"{self.id}.state.{generation:020d}.json"
            )
            version_tmp = version_path.with_name(f".{version_path.name}.tmp")
            version_written = False
            version_error: OSError | None = None
            try:
                version_tmp.write_text(payload, encoding="utf-8")
                os.replace(version_tmp, version_path)
                version_written = True
            except OSError as exc:
                version_error = exc
                try:
                    version_tmp.unlink(missing_ok=True)
                except OSError:
                    pass

            if version_written:
                generations = sorted(
                    self.state_path.parent.glob(f"{self.id}.state.*.json"),
                    reverse=True,
                )
                for stale in generations[STATE_GENERATIONS_TO_KEEP:]:
                    try:
                        stale.unlink(missing_ok=True)
                    except OSError:
                        pass

            # Keep the legacy canonical file for rolling compatibility with an
            # older ccdaemon. New daemons never read it when a generation is
            # available, so canonical contention cannot affect the live PTY.
            last_error: OSError | None = None
            for attempt in range(STATE_WRITE_RETRIES):
                tmp = self.state_path.with_name(
                    f".{self.state_path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
                )
                try:
                    tmp.write_text(payload, encoding="utf-8")
                    os.replace(tmp, self.state_path)
                except OSError as exc:
                    last_error = exc
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
                    if attempt + 1 < STATE_WRITE_RETRIES:
                        time.sleep(
                            min(
                                STATE_WRITE_RETRY_BASE_S * (2 ** attempt),
                                0.25,
                            )
                        )
                    continue

                self._last_state_write = time.time()
                return True

            if version_written:
                self._last_state_write = time.time()
                return True
            warning_now = time.time()
            if warning_now - self._last_state_warning >= 30.0:
                logger.warning(
                    "pty_host %s could not persist discovery state after %s attempts: %s",
                    self.id,
                    STATE_WRITE_RETRIES,
                    last_error or version_error,
                )
                self._last_state_warning = warning_now
            return False

    def _pty_alive(self) -> bool:
        try:
            return bool(self.pty is not None and self.pty.isalive())
        except Exception:
            return False

    def _schedule_state_write(self, *, force: bool = False) -> None:
        """Coalesce auxiliary state writes off the terminal IO hot path."""
        task = self._state_write_task
        if task is not None and not task.done():
            return
        self._state_write_task = asyncio.create_task(
            _pty_host_io(self._write_state, force=force),
            name=f"pty-host-state-{self.id}",
        )

    def _append_output(self, chunk: str, *, observed_at: float | None = None) -> bool:
        runtime_changed = self.provider_runtime.feed(chunk, now=observed_at)
        if self.screen_model is not None:
            self.screen_model.feed(chunk)
        self.ring.append(chunk)
        self.ring_bytes += len(chunk.encode("utf-8", errors="replace"))
        while self.ring and self.ring_bytes > self.replay_limit:
            removed = self.ring.popleft()
            self.ring_bytes -= len(removed.encode("utf-8", errors="replace"))
            self.replay_truncated = True
        return runtime_changed

    async def start(self) -> None:
        if self._pty_factory is None:
            from winpty import PTY

            self._pty_factory = PTY
        self.pty = self._pty_factory(self.cols, self.rows)
        env = provider_terminal_environment(self.provider, os.environ)
        if self.extra_env:
            env.update(self.extra_env)
        env["OMNI_CC_PTY_ID"] = self.id
        env.setdefault("CLAUDE_CODE_FORCE_SYNC_OUTPUT", "1")
        env_str = "".join(f"{key}={value}\0" for key, value in env.items()) + "\0"
        appname = self.cmd[0]
        cmdline = " ".join(_quote_arg(arg) for arg in self.cmd[1:]) if len(self.cmd) > 1 else None
        ok = self.pty.spawn(appname, cmdline=cmdline, cwd=self.cwd, env=env_str)
        if not ok:
            raise RuntimeError(f"PTY host spawn failed for {self.cmd!r}")
        self.child_pid = int(self.pty.pid) if getattr(self.pty, "pid", None) else None
        if self.provider == "codebuddy" and os.name == "nt" and self.child_pid:
            from .codebuddy_conhost_reaper import CodeBuddyConhostReaper

            self._codebuddy_conhost_reaper = CodeBuddyConhostReaper(self.child_pid)
            self._codebuddy_conhost_reaper_task = asyncio.create_task(
                self._codebuddy_conhost_reaper_loop(),
                name=f"pty-host-codebuddy-conhost-{self.id}",
            )
        self.server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
        sockets = self.server.sockets or []
        if not sockets:
            raise RuntimeError("PTY host did not receive a listening socket")
        self.port = int(sockets[0].getsockname()[1])
        self._write_state(force=True)
        self.reader_task = asyncio.create_task(self._reader_loop(), name=f"pty-host-read-{self.id}")

    async def _codebuddy_conhost_reaper_loop(self) -> None:
        """Clean completed CodeBuddy ConPTY helpers outside ccdaemon's loop."""
        reaper = self._codebuddy_conhost_reaper
        if reaper is None:
            return
        try:
            while not self.done.is_set():
                reaped = await asyncio.to_thread(reaper.poll)
                if reaped:
                    logger.info(
                        "pty_host %s reaped completed CodeBuddy conhost pids=%s",
                        self.id,
                        reaped,
                    )
                    self._schedule_state_write(force=True)
                try:
                    await asyncio.wait_for(
                        self.done.wait(),
                        timeout=reaper.poll_interval_s,
                    )
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning(
                "pty_host %s CodeBuddy conhost reaper failed",
                self.id,
                exc_info=True,
            )
        finally:
            reaper.close()

    async def close(self, reason: str = "kill") -> None:
        if self.done.is_set():
            return
        self.exit_reason = reason
        try:
            if self.pty is not None and self.pty.isalive():
                self.pty.close()
        except Exception:
            pass
        self.done.set()
        self._reader_wakeup.set()
        reaper_task = self._codebuddy_conhost_reaper_task
        if reaper_task is not None and reaper_task is not asyncio.current_task():
            reaper_task.cancel()
        await self._broadcast({"type": "exit", "reason": reason})
        await _pty_host_io(self._write_state, force=True)

    async def stop_server(self) -> None:
        reaper_task = self._codebuddy_conhost_reaper_task
        if reaper_task is not None and not reaper_task.done():
            reaper_task.cancel()
            try:
                await reaper_task
            except asyncio.CancelledError:
                pass
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for client in list(self.clients):
            await self._drop_client(client)

    async def _reader_loop(self) -> None:
        exit_reason: str | None = None
        idle_sleep = READ_IDLE_SLEEP_S
        try:
            while not self.done.is_set():
                try:
                    chunk = await _pty_host_io(self.pty.read, False)
                except Exception as exc:
                    if not self._pty_alive():
                        exit_reason = "process-exit"
                        break
                    warning_now = time.time()
                    if warning_now - self._last_read_warning >= 30.0:
                        logger.warning(
                            "pty_host %s transient read error while child is alive: %s",
                            self.id,
                            exc,
                        )
                        self._last_read_warning = warning_now
                    await self._wait_for_reader_wakeup(idle_sleep)
                    idle_sleep = next_read_idle_sleep(idle_sleep)
                    continue
                if chunk:
                    idle_sleep = READ_IDLE_SLEEP_S
                    for _ in range(READ_HOT_DRAIN_MAX):
                        try:
                            more = await _pty_host_io(self.pty.read, False)
                        except Exception:
                            break
                        if not more:
                            break
                        chunk += more
                    self.last_output_at = time.time()
                    runtime_changed = self._append_output(
                        chunk, observed_at=self.last_output_at
                    )
                    terminal_reply = self._query_responder.feed(
                        chunk, provider=self.provider
                    )
                    if terminal_reply:
                        # Keep capability negotiation local to the PTY. The
                        # browser still renders the query but its duplicate
                        # reply is filtered before it can reach ConPTY.
                        await _pty_host_io(self.pty.write, terminal_reply)
                    await self._broadcast({"type": "output", "data": chunk})
                    # Runtime transitions are sparse but authoritative. Persist
                    # them immediately: the ordinary one-second coalescing window
                    # can otherwise swallow Claude Code's final idle title when no
                    # more terminal output follows. Routine output stays off the
                    # terminal IO hot path.
                    if runtime_changed:
                        await _pty_host_io(self._write_state, force=True)
                    else:
                        self._schedule_state_write()
                    continue
                if not self._pty_alive():
                    exit_reason = "process-exit"
                    break
                wait_s, idle_sleep = next_read_wait(
                    idle_sleep,
                    hot_until=self._reader_hot_until,
                )
                await self._wait_for_reader_wakeup(wait_s)
        finally:
            if not self.done.is_set():
                self.exit_reason = exit_reason or "process-exit"
                self.done.set()
                await self._broadcast({"type": "exit", "reason": self.exit_reason})
                await _pty_host_io(self._write_state, force=True)

    async def _wait_for_reader_wakeup(self, timeout: float) -> None:
        """Sleep adaptively, but wake immediately after input or resize."""

        if self._reader_wakeup.is_set():
            self._reader_wakeup.clear()
            return
        try:
            await asyncio.wait_for(self._reader_wakeup.wait(), timeout=timeout)
        except TimeoutError:
            # Do not clear here: an input arriving on the timeout boundary must
            # remain visible to the next loop iteration.
            return
        self._reader_wakeup.clear()

    async def _broadcast(self, message: dict[str, Any]) -> None:
        for client in list(self.clients):
            try:
                client.queue.put_nowait(message)
            except asyncio.QueueFull:
                await self._drop_client(client)

    async def _client_writer(self, client: _Client) -> None:
        try:
            while True:
                await write_frame(client.writer, await client.queue.get())
        except (ConnectionError, asyncio.IncompleteReadError, RuntimeError, ValueError):
            pass
        finally:
            await self._drop_client(client, cancel_task=False)

    async def _drop_client(self, client: _Client, *, cancel_task: bool = True) -> None:
        self.clients.discard(client)
        if cancel_task and client.task is not None and client.task is not asyncio.current_task():
            client.task.cancel()
        try:
            client.writer.close()
            await client.writer.wait_closed()
        except Exception:
            pass

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        client: _Client | None = None
        try:
            hello = await asyncio.wait_for(read_frame(reader), timeout=3)
            if hello.get("type") != "hello" or hello.get("token") != self.token:
                await write_frame(writer, {"type": "error", "reason": "unauthorized"})
                return
            client = _Client(writer)
            self.clients.add(client)
            # Register before capturing/sending the replay. Output produced while
            # the (potentially large) snapshot is in flight is queued behind it,
            # so a reconnect has no snapshot-to-live gap.
            protocol = int(hello.get("snapshot_protocol") or 1)
            chunks = list(self.ring)
            if protocol >= SNAPSHOT_PROTOCOL_VERSION:
                await write_frame(
                    writer,
                    {"type": "snapshot_begin", "meta": self._state()},
                )
                batch: list[str] = []
                batch_chars = 0
                for chunk in chunks:
                    if batch and batch_chars + len(chunk) > SNAPSHOT_FRAME_CHARS:
                        await write_frame(writer, {"type": "snapshot_chunk", "chunks": batch})
                        batch = []
                        batch_chars = 0
                    batch.append(chunk)
                    batch_chars += len(chunk)
                if batch:
                    await write_frame(writer, {"type": "snapshot_chunk", "chunks": batch})
                await write_frame(writer, {"type": "snapshot_end"})
            else:
                await write_frame(
                    writer,
                    {"type": "snapshot", "chunks": chunks, "meta": self._state()},
                )
            client.task = asyncio.create_task(self._client_writer(client))
            while not self.done.is_set():
                message = await read_frame(reader)
                kind = message.get("type")
                if kind == "input":
                    data = message.get("data")
                    if isinstance(data, str) and data:
                        self.last_input_at = time.time()
                        self._reader_hot_until = max(
                            self._reader_hot_until,
                            time.monotonic() + READ_AFTER_INPUT_WINDOW_S,
                        )
                        if "\x03" in data:
                            self.provider_runtime.mark_idle(
                                "user:interrupt",
                                now=self.last_input_at,
                            )
                        await _pty_host_io(self.pty.write, data)
                        self._reader_wakeup.set()
                        self._schedule_state_write()
                elif kind == "resize":
                    cols = max(2, min(500, int(message.get("cols") or self.cols)))
                    rows = max(1, min(200, int(message.get("rows") or self.rows)))
                    if cols == self.cols and rows == self.rows:
                        continue
                    await _pty_host_io(self.pty.set_size, cols, rows)
                    if self.screen_model is not None:
                        self.screen_model.resize(cols, rows)
                    self._reader_wakeup.set()
                    self.cols, self.rows = cols, rows
                    self._schedule_state_write(force=True)
                elif kind == "kill":
                    await self.close("kill")
                    break
                elif kind == "ping":
                    client.queue.put_nowait({"type": "pong", "meta": self._state()})
                elif kind == "screen":
                    # On-demand self-contained repaint frame from the screen
                    # model. ``data: None`` tells the daemon to fall back to
                    # the raw replay (model absent or dead).
                    frame = (
                        self.screen_model.render_frame()
                        if self.screen_model is not None
                        else None
                    )
                    client.queue.put_nowait(
                        {"type": "screen", "req": message.get("req"), "data": frame}
                    )
                elif kind == "set_meta":
                    provider = message.get("provider")
                    if isinstance(provider, str) and provider:
                        self.provider = provider
                        self.provider_runtime.set_provider(provider)
                        self._schedule_state_write(force=True)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass
        finally:
            if client is not None:
                await self._drop_client(client)
            else:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass


async def run_from_config(path: Path) -> int:
    from ..asyncio_runtime import configure_default_executor

    configure_default_executor(
        asyncio.get_running_loop(),
        env_name="OMNI_PTY_HOST_EXECUTOR_WORKERS",
        default_workers=4,
        thread_name_prefix="pty-host",
    )
    config = json.loads(path.read_text(encoding="utf-8"))
    host = PtyHost(config)
    try:
        await host.start()
        await host.done.wait()
        await asyncio.sleep(0.25)
        return 0
    except Exception as exc:
        host.exit_reason = f"host-start-failed: {type(exc).__name__}: {exc}"
        try:
            host._write_state(force=True)
        except Exception:
            pass
        return 1
    finally:
        await host.stop_server()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    return asyncio.run(run_from_config(Path(args.config)))


if __name__ == "__main__":
    raise SystemExit(main())
