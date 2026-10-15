"""Read/write LiTeWork terminals through its public development CLI.

The bridge deliberately does not connect to, unlink, or otherwise own the
LiTeWork daemon socket.  LiTeWork remains the sole authority for terminal
handles and stream state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from omnicompany.packages.services._core import litework_host, litework_runtime_ws


logger = logging.getLogger(__name__)

SESSION_PREFIX = "litework:"
CACHE_TTL_SECONDS = 3.0
STALE_CACHE_SECONDS = 15.0

# LiTeWork terminals ride the same digest store as OmniCompany's own PTYs, keyed
# by the provider `terminal_to_meta` reports.  Reusing that store keeps one title
# authority instead of growing a second, LiTeWork-only heuristic.
DIGEST_PROVIDER = "shell"
# Bounds for the content-title producer.  Each candidate costs one LiTeWork CLI
# spawn plus one cheap-model call, so the loop stays small and slow on purpose.
TITLE_TICK_MIN_GAP_SECONDS = 90.0
TITLE_REFRESH_SECONDS = 600.0
TITLE_MAX_PER_TICK = 3
TITLE_TAIL_LINES = 60

# A title refresh performs a LiTeWork CLI spawn, an LLM call, and a whole-file
# digest-store replace.  Keep that deliberately slow maintenance work out of
# asyncio's shared default executor, which also serves PTY metadata and I/O.
_TITLE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="litework-title")


def is_litework_session_id(session_id: str) -> bool:
    return session_id.startswith(SESSION_PREFIX) and len(session_id) > len(SESSION_PREFIX)


def session_id_for_handle(handle: str) -> str:
    return f"{SESSION_PREFIX}{handle}"


def handle_for_session_id(session_id: str) -> str:
    if not is_litework_session_id(session_id):
        raise KeyError(session_id)
    return session_id[len(SESSION_PREFIX):]


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("result")
    return value if isinstance(value, dict) else {}


def _terminal_result(payload: dict[str, Any]) -> dict[str, Any]:
    value = _result(payload).get("terminal")
    return value if isinstance(value, dict) else {}


def short_branch(branch: str) -> str:
    """`refs/heads/feature/x` -> `feature/x`; anything else is passed through."""
    text = str(branch or "").strip()
    for prefix in ("refs/heads/", "refs/remotes/"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def structural_title(row: dict[str, Any]) -> str:
    """Readable label from metadata alone, for a terminal with no content title.

    The worktree folder alone is not enough: several panes routinely share one
    worktree, and collapsing them to the same word makes the list unusable.  The
    branch is what actually differs, so it is part of the label.
    """
    handle = str(row.get("handle") or "")
    worktree = Path(str(row.get("worktreePath") or "")).name
    branch = short_branch(row.get("branch") or "")
    if worktree and branch and branch != worktree:
        return f"{worktree} · {branch}"
    return worktree or branch or handle[:18]


def _epoch_seconds(row: dict[str, Any], *names: str) -> float | None:
    """Read an explicitly supplied epoch timestamp without deriving one.

    ``lastOutputAt`` is activity evidence, never a process start time.  The
    public LiTeWork terminal API does not currently promise a start timestamp,
    so an absent value remains unknown instead of being invented.
    """
    for name in names:
        value = row.get(name)
        if isinstance(value, (int, float)) and value > 0:
            return float(value) / 1000.0 if value > 10_000_000_000 else float(value)
    return None


def _explicit_provider(row: dict[str, Any]) -> str:
    """Use a provider only when LiTeWork explicitly reports one."""
    for name in ("provider", "agentProvider", "runtimeProvider"):
        value = str(row.get(name) or "").strip().lower().replace("-", "_")
        if value and value not in {"shell", "terminal", "unknown", "none"}:
            return value
    return "unknown"


def terminal_to_meta(row: dict[str, Any]) -> dict[str, Any]:
    handle = str(row.get("handle") or "").strip()
    if not handle:
        raise ValueError("LiTeWork terminal is missing a stable handle")
    worktree_path = str(row.get("worktreePath") or "")
    # LiTeWork only titles panes that are running an agent, and it loses even
    # those when the host process restarts, so a fallback is the normal case.
    title = str(row.get("title") or "").strip() or structural_title(row)
    last_output_ms = row.get("lastOutputAt")
    last_output_at = (
        float(last_output_ms) / 1000.0
        if isinstance(last_output_ms, (int, float)) and last_output_ms > 0
        else None
    )
    started_at = _epoch_seconds(row, "startedAt", "createdAt", "started_at", "created_at")
    process_alive = row.get("processAlive") if isinstance(row.get("processAlive"), bool) else None
    working = row.get("working") if isinstance(row.get("working"), bool) else None
    runtime_known = isinstance(working, bool)
    connected = row.get("connected") is not False and row.get("orphaned") is not True
    observed_at = time.time()
    state = "working" if working is True else "idle" if working is False and runtime_known else "unknown"
    return {
        "id": session_id_for_handle(handle),
        "cmd": [],
        "cwd": worktree_path,
        "cols": 120,
        "rows": 30,
        "started_at": started_at,
        "startedAt": started_at,
        "started_at_source": "litework_terminal" if started_at is not None else None,
        "last_output_at": last_output_at,
        "lastOutputAt": last_output_at,
        "last_activity_at": last_output_at,
        "lastActivityAt": last_output_at,
        "alive": connected,
        "connection_alive": connected,
        "process_alive": process_alive,
        "processAlive": process_alive,
        "process_alive_source": "litework_terminal_status" if process_alive is not None else None,
        "status": "alive" if connected else "ended",
        "state": state,
        "state_evidence_at": _epoch_seconds(row, "workingAt", "runtimeMarkerAt", "lastActivityAt"),
        "observed_at": observed_at,
        "observedAt": observed_at,
        "source": "litework-runtime",
        "working": bool(working) if runtime_known else False,
        "working_source": "litework_terminal_status" if runtime_known else None,
        "working_known": runtime_known,
        "workingKnown": runtime_known,
        "stateEvidenceAt": _epoch_seconds(row, "workingAt", "runtimeMarkerAt", "lastActivityAt"),
        "provider_runtime_state": state,
        "provider_runtime_known": runtime_known,
        "provider": _explicit_provider(row),
        "provider_title": title,
        "display_title": title,
        "provider_session_id": handle,
        "providerSessionId": handle,
        "session_id": session_id_for_handle(handle),
        "pty_id": row.get("ptyId"),
        "has_user_turn": False,
        "origin": "human",
        "visibility": "visible",
        "visible": True,
        "binding_source": "litework-runtime",
        "runtime_host": "litework",
        "litework": {
            "handle": handle,
            "pty_id": row.get("ptyId"),
            "incarnation_id": row.get("incarnationId"),
            "worktree_id": row.get("worktreeId"),
            "branch": row.get("branch"),
            "tab_id": row.get("tabId"),
            "leaf_id": row.get("leafId"),
            "writable": row.get("writable") is True,
        },
    }


def _tail_text(handle: str, *, limit: int = TITLE_TAIL_LINES) -> str:
    payload = litework_host.run_cli(
        ["terminal", "read", "--terminal", handle, "--limit", str(limit), "--json"]
    )
    terminal = _terminal_result(payload)
    lines = terminal.get("tail")
    if not isinstance(lines, list):
        return ""
    return "\n".join(str(line) for line in lines)


def _summarize_terminal(row: dict[str, Any]) -> dict[str, str] | None:
    """Give one LiTeWork terminal a content title through the shared digest store.

    Runs on a worker thread: both the CLI read and the model call are blocking.
    """
    from omnicompany.packages.services.boss_sight.services.agent_digest import (
        summarize_one,
        upsert_digest,
    )

    handle = str(row.get("handle") or "")
    if not handle:
        return None
    tail = _tail_text(handle)
    if not tail.strip():
        # An empty pane has no content to match; the structural title is honest.
        return None
    digest = summarize_one({
        "cwd": str(row.get("worktreePath") or ""),
        "preview": f"LiTeWork worktree {Path(str(row.get('worktreePath') or '')).name} "
                   f"on branch {short_branch(row.get('branch') or '')}",
        "last_did": tail,
    })
    last_output_ms = row.get("lastOutputAt")
    source_mtime = (
        float(last_output_ms) / 1000.0
        if isinstance(last_output_ms, (int, float)) and last_output_ms > 0
        else 0.0
    )
    return upsert_digest(
        DIGEST_PROVIDER,
        handle,
        digest,
        source="litework_bridge",
        source_mtime=source_mtime,
    )


def _load_digests() -> dict[str, dict[str, Any]]:
    try:
        from omnicompany.packages.services.boss_sight.services.agent_digest import load_digests_cached

        return load_digests_cached()
    except Exception:  # noqa: BLE001 - titles must never break the session list
        logger.debug("digest store unavailable for LiTeWork titles", exc_info=True)
        return {}


def _with_digest_titles(metas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project the shared session-digest store onto LiTeWork terminals."""
    digests = _load_digests()
    if not digests:
        return metas
    for meta in metas:
        handle = str(meta.get("provider_session_id") or "")
        digest = digests.get(f"{DIGEST_PROVIDER}:{handle}") if handle else None
        title = str((digest or {}).get("title") or "").strip()
        if not title:
            continue
        meta["summary_title"] = title
        meta["display_title"] = title
        last_step = str((digest or {}).get("last_step") or "").strip()
        if last_step:
            meta["last_step"] = last_step
    return metas


class LiteWorkTerminalBridge:
    def __init__(self) -> None:
        self._metas: list[dict[str, Any]] = []
        self._refreshed_at = 0.0
        self._refresh_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[list[dict[str, Any]]] | None = None
        self._titles_ticked_at = 0.0
        self._title_task: asyncio.Task[None] | None = None

    def list_meta(self) -> list[dict[str, Any]]:
        # LiTe 终端也要看得出属于哪个项目。桥自己拼 meta、不经过 PTY 目录, 于是
        # PtyManager 那道绑定投影漏掉了它们 —— 实测 13 个 LiTe 终端全都 project=None,
        # 哪怕权威台账里已经有 pty:<id> 的绑定。这里补投影一次, 直接复用同一个
        # staticmethod(单一实现, 不抄第二份口径)。函数内 import 避开循环依赖。
        from .pty import PtyManager

        metas = _with_digest_titles([dict(meta) for meta in self._metas])
        # Bindings last: a topic the user bound by hand outranks a model title.
        return PtyManager._with_session_bindings(metas)

    def tab_states(self) -> list[dict[str, Any]]:
        return self.list_meta()

    def schedule_refresh(self) -> None:
        """Refresh the optional external catalog without delaying PTY requests."""
        if time.monotonic() - self._refreshed_at <= CACHE_TTL_SECONDS:
            return
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = asyncio.create_task(
            self.refresh(), name="litework-terminal-refresh"
        )
        self._refresh_task.add_done_callback(self._refresh_completed)

    @staticmethod
    def _refresh_completed(task: asyncio.Task[list[dict[str, Any]]]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - optional external runtime
            logger.exception("LiTeWork terminal refresh failed")

    async def refresh(self, *, force: bool = False) -> list[dict[str, Any]]:
        now = time.monotonic()
        if not force and now - self._refreshed_at <= CACHE_TTL_SECONDS:
            return self.list_meta()
        async with self._refresh_lock:
            now = time.monotonic()
            if not force and now - self._refreshed_at <= CACHE_TTL_SECONDS:
                return self.list_meta()
            try:
                rows = await asyncio.to_thread(litework_host.list_terminals)
            except Exception as exc:  # noqa: BLE001 - optional external runtime
                if now - self._refreshed_at > STALE_CACHE_SECONDS:
                    self._metas = []
                logger.debug("LiTeWork terminal refresh unavailable: %s", exc)
                return self.list_meta()
            metas: list[dict[str, Any]] = []
            for row in rows:
                try:
                    metas.append(terminal_to_meta(row))
                except (TypeError, ValueError) as exc:
                    logger.warning("Ignoring invalid LiTeWork terminal metadata: %s", exc)
            self._metas = metas
            self._refreshed_at = time.monotonic()
            self._schedule_titles(rows)
            return self.list_meta()

    # -- content titles ----------------------------------------------------
    # LiTeWork reports `title: null` for every pane that is not running an agent,
    # and loses even the agent titles when its host restarts.  The session list
    # then degrades to repeated worktree names.  Recovering a real title needs
    # the terminal's own output, which only the (slow) public CLI exposes, so the
    # work happens off the request path under an explicit budget.

    def _title_candidates(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        digests = _load_digests()
        now = time.time()
        candidates: list[dict[str, Any]] = []
        for row in rows:
            handle = str(row.get("handle") or "")
            if not handle or row.get("connected") is False or row.get("orphaned") is True:
                continue
            digest = digests.get(f"{DIGEST_PROVIDER}:{handle}")
            if digest is None:
                candidates.append(row)
                continue
            if not str(digest.get("title") or "").strip():
                candidates.append(row)
                continue
            if now - float(digest.get("_updated_ts") or 0.0) >= TITLE_REFRESH_SECONDS:
                candidates.append(row)
        # Freshest output first: the terminals the user is actually watching.
        candidates.sort(key=lambda row: float(row.get("lastOutputAt") or 0), reverse=True)
        return candidates[:TITLE_MAX_PER_TICK]

    def _schedule_titles(self, rows: list[dict[str, Any]]) -> None:
        if os.environ.get("OMNI_LITEWORK_TITLES", "1").strip() in {"0", "false", "off"}:
            return
        now = time.monotonic()
        if now - self._titles_ticked_at < TITLE_TICK_MIN_GAP_SECONDS:
            return
        if self._title_task is not None and not self._title_task.done():
            return
        candidates = self._title_candidates(rows)
        if not candidates:
            return
        self._titles_ticked_at = now
        self._title_task = asyncio.create_task(self._refresh_titles(candidates))

    async def _refresh_titles(self, rows: list[dict[str, Any]]) -> None:
        loop = asyncio.get_running_loop()
        for row in rows:
            try:
                await loop.run_in_executor(_TITLE_EXECUTOR, _summarize_terminal, row)
            except Exception:  # noqa: BLE001 - a missing title is not an outage
                logger.debug(
                    "LiTeWork title refresh failed for %s", row.get("handle"), exc_info=True
                )

    async def close(self, session_id: str) -> bool:
        handle = handle_for_session_id(session_id)
        try:
            await asyncio.to_thread(
                litework_host.run_cli,
                ["terminal", "close", "--terminal", handle, "--json"],
            )
        except RuntimeError as exc:
            if "not found" in str(exc).lower() or "stale" in str(exc).lower():
                return False
            raise
        await self.refresh(force=True)
        return True

    async def read(self, session_id: str, *, cursor: str | None = None, limit: int = 1000) -> dict[str, Any]:
        handle = handle_for_session_id(session_id)
        args = ["terminal", "read", "--terminal", handle, "--limit", str(max(1, min(limit, 5000)))]
        if cursor is not None:
            args += ["--cursor", cursor]
        args.append("--json")
        payload = await asyncio.to_thread(litework_host.run_cli, args)
        terminal = _terminal_result(payload)
        if not terminal:
            raise KeyError(session_id)
        return terminal

    async def send(self, session_id: str, data: str, *, submit: bool = False) -> dict[str, Any]:
        handle = handle_for_session_id(session_id)
        text = str(data)
        interrupt = text == "\x03"
        enter = submit or text.endswith("\r") or text.endswith("\n")
        if enter:
            text = text.rstrip("\r\n")
        args = ["terminal", "send", "--terminal", handle]
        if interrupt:
            args.append("--interrupt")
        elif text:
            args += ["--text", text]
        if enter and not interrupt:
            args.append("--enter")
        args.append("--json")
        payload = await asyncio.to_thread(litework_host.run_cli, args)
        return _result(payload)

    async def output(self, session_id: str, *, limit: int = 4000) -> str:
        terminal = await self.read(session_id, limit=max(1, min(limit, 5000)))
        tail = terminal.get("tail")
        if not isinstance(tail, list):
            return ""
        return "\r\n".join(str(line) for line in tail)

    async def websocket(self, ws: WebSocket, session_id: str) -> None:
        """Attach a viewer to one LiTeWork terminal.

        Preferred path is LiTeWork's own runtime socket: real keystroke latency
        and live PTY bytes.  The CLI path stays as the fallback for a runtime
        that is not reachable that way, but it can only poll.
        """
        await ws.accept()
        session = None
        try:
            handle = handle_for_session_id(session_id)
            session = litework_runtime_ws.RuntimeSession(litework_runtime_ws.discover())
            await session.connect()
        except KeyError:
            await ws.send_text(json.dumps({"type": "exit", "reason": "session not found"}))
            await ws.close()
            return
        except litework_runtime_ws.RuntimeUnavailable as exc:
            logger.info("LiTeWork runtime socket unavailable, polling instead: %s", exc)
            session = None
        if session is not None:
            try:
                await self._stream_via_runtime(ws, handle, session)
                return
            except litework_runtime_ws.RuntimeUnavailable as exc:
                logger.info("LiTeWork runtime stream ended, polling instead: %s", exc)
            finally:
                await session.close()
        await self._stream_via_cli(ws, session_id)

    async def _stream_via_runtime(
        self,
        ws: WebSocket,
        handle: str,
        session: "litework_runtime_ws.RuntimeSession",
    ) -> None:
        """Bridge LiTeWork's `terminal.subscribe` stream to the ccdaemon framing."""
        stream = session.stream("terminal.subscribe", {"terminal": handle})
        # Why: the first emit carries the screen; sending it before the socket is
        # framed as a snapshot would leave the viewer with a blank terminal.
        snapshot = await stream.__anext__()
        lines = snapshot.get("lines") if isinstance(snapshot.get("lines"), list) else []
        text = str(snapshot.get("serialized") or "") or "\r\n".join(str(line) for line in lines)
        await ws.send_text(json.dumps({
            "type": "snapshot_begin",
            "meta": {
                "provider": "shell",
                "cols": 120,
                "rows": 30,
                "buffered_bytes": len(text.encode("utf-8", errors="replace")),
                "replay_truncated": bool(snapshot.get("truncated")),
                "base_seq": 0,
                "last_seq": 0,
                "screen_model": False,
                "runtime_host": "litework",
                "transport": "runtime-socket",
            },
        }))
        if text:
            await ws.send_text(json.dumps({"type": "snapshot_chunk", "chunks": [text]}))
        await ws.send_text(json.dumps({"type": "snapshot_end"}))

        async def pump_out() -> None:
            seq = 1
            async for event in stream:
                kind = event.get("type")
                if kind == "data":
                    chunk = str(event.get("chunk") or "")
                    if chunk:
                        await ws.send_text(json.dumps({"type": "output", "data": chunk, "seq": seq}))
                        seq += 1
                elif kind in {"end", "exit"}:
                    await ws.send_text(json.dumps({"type": "exit", "reason": kind}))
                    return

        async def pump_in() -> None:
            while True:
                raw = await ws.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict) or message.get("type") != "input":
                    # Resize stays with LiTeWork: the pane's own renderer owns the
                    # geometry, and a second viewer must not resize it underneath.
                    continue
                data = str(message.get("data") or "")
                if data:
                    await session.request("terminal.send", {"terminal": handle, "text": data})

        tasks = [asyncio.create_task(pump_out()), asyncio.create_task(pump_in())]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                task.result()
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Why: aclose() on a generator a cancelled task is still suspended
            # inside raises "already running"; let the cancellation land first.
            await asyncio.gather(*tasks, return_exceptions=True)
            await stream.aclose()

    async def _stream_via_cli(self, ws: WebSocket, session_id: str) -> None:
        """Fallback: poll LiTeWork's cursor CLI and adapt it to the same framing."""
        try:
            snapshot = await self.read(session_id, limit=5000)
        except (KeyError, RuntimeError):
            await ws.send_text(json.dumps({"type": "exit", "reason": "session not found"}))
            await ws.close()
            return
        tail = snapshot.get("tail") if isinstance(snapshot.get("tail"), list) else []
        cursor = str(snapshot.get("nextCursor") or snapshot.get("latestCursor") or "0")
        text = "\r\n".join(str(line) for line in tail)
        await ws.send_text(json.dumps({
            "type": "snapshot_begin",
            "meta": {
                "provider": "shell",
                "cols": 120,
                "rows": 30,
                "buffered_bytes": len(text.encode("utf-8", errors="replace")),
                "replay_truncated": bool(snapshot.get("truncated")),
                "base_seq": 0,
                "last_seq": 0,
                "screen_model": False,
                "runtime_host": "litework",
            },
        }))
        if text:
            await ws.send_text(json.dumps({"type": "snapshot_chunk", "chunks": [text]}))
        await ws.send_text(json.dumps({"type": "snapshot_end"}))

        async def pump_out() -> None:
            nonlocal cursor
            seq = 1
            while True:
                await asyncio.sleep(0.75)
                terminal = await self.read(session_id, cursor=cursor, limit=1000)
                next_cursor = str(terminal.get("nextCursor") or terminal.get("latestCursor") or cursor)
                lines = terminal.get("tail") if isinstance(terminal.get("tail"), list) else []
                if lines and next_cursor != cursor:
                    chunk = "\r\n".join(str(line) for line in lines)
                    if chunk:
                        await ws.send_text(json.dumps({"type": "output", "data": chunk, "seq": seq}))
                        seq += 1
                cursor = next_cursor
                if terminal.get("status") in {"exited", "closed"}:
                    await ws.send_text(json.dumps({"type": "exit", "reason": terminal.get("status")}))
                    await ws.close()
                    return

        async def pump_in() -> None:
            while True:
                raw = await ws.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                message_type = message.get("type")
                if message_type == "input":
                    await self.send(session_id, str(message.get("data") or ""), submit=False)
                elif message_type in {"resize", "redraw"}:
                    # The public LiTeWork CLI does not expose terminal resize.
                    # Ignoring redraw preserves the socket ownership boundary.
                    continue

        tasks = [asyncio.create_task(pump_out()), asyncio.create_task(pump_in())]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                task.result()
        except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError, KeyError):
            pass
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()


_bridge = LiteWorkTerminalBridge()


def get_litework_bridge() -> LiteWorkTerminalBridge:
    return _bridge
