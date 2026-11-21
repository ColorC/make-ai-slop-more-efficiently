# [OMNI] origin=claude-code ts=2026-08-13 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.web_host.py"
"""Web-display session host for the CLI Agent Hub.

Companion to the winpty-backed PtyHost for one case: sessions whose display
surface is a web page instead of a terminal relay. Today that is exactly the
DeepSeek Harness Web UI (``dsh web``), run through the supervisor in
``config/agents/dsh/dsh-web.py``.

The supervisor protocol is line-delimited JSON on stdout:
``{"web":"starting"|"listening"|"exited"|"error", ...}``. The hub holds the
supervisor's stdin open for the session's whole life; closing it tears down
the dsh process tree, so sessions never leak servers. Web sessions are
ephemeral across daemon restarts by design (dsh's own sessions persist under
~/.dsh and reopen inside the Web UI).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

WEB_LOG_RING_LINES = 400
_WEB_SUPERVISOR_GONE_GRACE_S = 5.0


@dataclass
class WebSession:
    id: str
    cmd: list[str]
    cwd: str
    provider: str
    origin: str
    started_at: float
    proc: asyncio.subprocess.Process
    ended_at: float | None = None
    phase: str = "starting"
    web_url: str | None = None
    web_port: int | None = None
    error: str | None = None
    exit_code: int | None = None
    # 运行态: supervisor 轮询 dsh 的 session.list 上报({"web":"runtime",...})。
    # 未收到首条 runtime 事件前保持默认(向后兼容旧 supervisor)。
    working: bool = False
    display_title: str | None = None
    runtime_seen: bool = False
    last_output_at: float = field(default_factory=time.time)
    log: deque[str] = field(default_factory=lambda: deque(maxlen=WEB_LOG_RING_LINES))
    monitor: asyncio.Task | None = None
    workspace_task: asyncio.Task | None = None

    @property
    def alive(self) -> bool:
        return self.phase in ("starting", "ready")

    @property
    def proxy_path(self) -> str:
        """Dashboard-relative URL path for the session (unified /api/cc channel).

        The internal dsh port is loopback-only; every browser request goes
        through the dashboard's /api/cc catch-all and ccdaemon's web proxy,
        so remote hosts reach the UI exactly like any PTY pane.
        """
        return f"/api/cc/web/{self.id}/"

    def to_meta(self) -> dict[str, Any]:
        now = time.time()
        process_alive = self.proc.returncode is None
        state = "working" if self.working else "idle" if self.runtime_seen else "unknown"
        return {
            "id": self.id,
            "session_id": self.id,
            "pty_id": self.id,
            "kind": "pty",
            "display_kind": "web",
            "provider": self.provider,
            "web_url": self.proxy_path if self.web_port else None,
            "web_phase": self.phase,
            "cmd": self.cmd,
            "cwd": self.cwd,
            "started_at": self.started_at,
            "startedAt": self.started_at,
            "alive": self.alive,
            "process_alive": process_alive,
            "processAlive": process_alive,
            "observed_at": now,
            "observedAt": now,
            "source": "ccdaemon.web_host",
            "status": "alive" if self.alive else "ended",
            "state": state,
            "working_known": self.runtime_seen,
            "state_evidence_at": self.last_output_at if self.runtime_seen else None,
            "working": self.working,
            "provider_runtime_state": "working" if self.working else "idle",
            "provider_runtime_known": self.runtime_seen,
            "display_title": self.display_title,
            "has_user_turn": False,
            "last_submit_at": None,
            "child_pid": None,
            "origin": self.origin,
            "error": self.error,
            "exit_code": self.exit_code,
            "last_output_at": self.last_output_at,
            "last_activity_at": self.last_output_at,
            "ended_at": self.ended_at,
            "close_actor": None,
            "close_reason": "process-exit" if self.ended_at is not None else None,
            "close_time": self.ended_at,
            "visibility": "visible",
        }


class WebHostManager:
    """In-daemon registry + supervision of web-display sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, WebSession] = {}
        self._lock = asyncio.Lock()

    def get(self, sid: str) -> WebSession | None:
        return self._sessions.get(sid)

    async def create(
        self,
        cmd: list[str],
        cwd: str,
        origin: str,
    ) -> WebSession:
        sid = uuid.uuid4().hex[:16]
        env = _web_session_env()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            "--sid",
            sid,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or None,
            env=env,
        )
        sess = WebSession(
            id=sid,
            cmd=cmd,
            cwd=cwd,
            provider="dsh_web",
            origin=origin,
            started_at=time.time(),
            proc=proc,
        )
        async with self._lock:
            self._sessions[sid] = sess
        sess.monitor = asyncio.create_task(self._monitor(sess), name=f"web-session-{sid}")
        logger.info("web session created id=%s provider=%s origin=%s", sid, sess.provider, origin)
        return sess

    async def _monitor(self, sess: WebSession) -> None:
        try:
            await self._pump_stdout(sess)
        finally:
            await self._finalize(sess)

    async def _pump_stdout(self, sess: WebSession) -> None:
        assert sess.proc.stdout is not None
        stderr_task = asyncio.create_task(self._drain_stderr(sess))
        try:
            while True:
                line = await sess.proc.stdout.readline()
                if not line:
                    break
                sess.last_output_at = time.time()
                try:
                    payload = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    sess.log.append(line.decode("utf-8", errors="replace").rstrip())
                    continue
                event = payload.get("web")
                if event == "listening":
                    sess.web_url = str(payload.get("url") or "")
                    sess.web_port = payload.get("port")
                    sess.phase = "ready"
                    sess.workspace_task = asyncio.create_task(
                        self._ensure_default_workspace(sess),
                        name=f"web-workspace-{sess.id}",
                    )
                elif event == "error":
                    sess.error = str(payload.get("message") or "dsh web failed")
                elif event == "runtime":
                    # supervisor 轮询 session.list 上报: 会话列表的「进行中」与标题真源。
                    sess.runtime_seen = True
                    sess.working = bool(payload.get("working"))
                    title = payload.get("title")
                    sess.display_title = str(title).strip() if title else None
                elif event == "exited":
                    sess.exit_code = payload.get("code")
        finally:
            await stderr_task

    async def _drain_stderr(self, sess: WebSession) -> None:
        stream = sess.proc.stderr
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            sess.log.append(line.decode("utf-8", errors="replace").rstrip())

    async def _finalize(self, sess: WebSession) -> None:
        try:
            sess.exit_code = await asyncio.wait_for(sess.proc.wait(), timeout=_WEB_SUPERVISOR_GONE_GRACE_S)
        except asyncio.TimeoutError:
            sess.exit_code = -1
        # supervisor 进程退出 = 会话终局(其 stdout EOF 才会走到这里)。
        # 崩溃自愈期间 supervisor 活着并会重新上报 listening(回到 ready);
        # 到这里必须落 ended, 否则死会话会以 alive 假象赖在会话列表里。
        sess.phase = "ended"
        sess.ended_at = time.time()
        sess.last_output_at = time.time()

    async def _ensure_default_workspace(self, sess: WebSession) -> None:
        """Pre-register the session cwd as a dsh workspace once the UI is up.

        A fresh dsh Web UI has no selected workspace until the user adds one;
        the composer stays blocked. The session's resolved cwd (dashboard
        default = the cross-project root C:/workspace/) is registered
        through the loopback RPC API so the UI's first-load flow finds a real
        workspace and auto-selects it. Idempotent: an already-registered path
        is adopted, never duplicated; the registry persists under ~/.dsh.
        """
        if not sess.web_port or not sess.cwd:
            return
        base = f"http://127.0.0.1:{sess.web_port}"
        client = await _client()
        try:
            for attempt in range(5):
                try:
                    listing = await _rpc(client, base, "workspace.list", {})
                    items = (listing.get("result", {}).get("value") or {}).get("items") or []
                    registered = any(str(item.get("path") or "") == sess.cwd for item in items)
                    if registered:
                        return
                    created = await _rpc(client, base, "workspace.create", {"path": sess.cwd})
                    if created.get("result", {}).get("ok"):
                        logger.info(
                            "web session %s: registered default workspace %s",
                            sess.id, sess.cwd,
                        )
                        return
                    logger.warning("web session %s: workspace.create refused: %s", sess.id, created)
                except Exception as exc:  # noqa: BLE001 - retry until the API settles
                    logger.debug("web session %s: workspace bootstrap attempt %s failed: %s", sess.id, attempt + 1, exc)
                await asyncio.sleep(1.5)
        finally:
            pass

    async def kill(self, sid: str) -> bool:
        sess = self._sessions.get(sid)
        if sess is None:
            return False
        await self._teardown(sess)
        return True

    async def _teardown(self, sess: WebSession) -> None:
        """Close the supervisor's stdin (its teardown contract) and escalate."""
        if sess.workspace_task is not None and not sess.workspace_task.done():
            sess.workspace_task.cancel()
        if sess.proc.stdin is not None:
            try:
                sess.proc.stdin.close()
            except Exception:
                pass
        await asyncio.sleep(0.5)
        if sess.proc.returncode is None:
            _kill_process_tree(sess.proc.pid)
        try:
            await asyncio.wait_for(sess.proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        sess.phase = "ended"
        sess.ended_at = time.time()

    def list_meta(self, *, include_ended: bool = False) -> list[dict[str, Any]]:
        """Session-list projection.

        ``include_ended=False``(会话列表用): 已结束的 web 会话是展示面一次性
        会话, 没有 resume 价值, 不应继续占据会话列表 —— 否则探测/临时会话
        越堆越多, 把活会话挤下去。
        """
        return [s.to_meta() for s in self._sessions.values() if include_ended or s.alive]

    def tab_states(self) -> list[dict[str, Any]]:
        """页签高频状态: 保留已结束会话, 让还开着的页签能看到 ended 状态。"""
        return [s.to_meta() for s in self._sessions.values()]

    async def shutdown(self) -> None:
        sessions = list(self._sessions.values())
        for sess in sessions:
            await self._teardown(sess)


def _web_session_env() -> dict[str, str]:
    from .provider_runtime import provider_terminal_environment

    return provider_terminal_environment("dsh_web", os.environ)


_HTTP_CLIENT: httpx.AsyncClient | None = None
_HTTP_CLIENT_LOOP: asyncio.AbstractEventLoop | None = None


async def _client() -> httpx.AsyncClient:
    """Shared loopback client for dsh API bootstrap calls."""
    global _HTTP_CLIENT, _HTTP_CLIENT_LOOP

    loop = asyncio.get_running_loop()
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed or _HTTP_CLIENT_LOOP is not loop:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            trust_env=False,
        )
        _HTTP_CLIENT_LOOP = loop
    return _HTTP_CLIENT


async def _rpc(client: httpx.AsyncClient, base: str, method: str, args: dict[str, Any]) -> dict[str, Any]:
    """One unary RPC through dsh's /api bridge (loopback → trust fence passes).

    The API Proxy validates the request payload directly (no ``args`` wrapper;
    verified against workspace.create/delete on dsh 0.1.0-rc.6).
    """
    envelope = {
        "type": "client-request",
        "rpcId": str(uuid.uuid4()),
        "method": method,
        "payload": args,
    }
    response = await client.post(
        f"{base}/api/{method}",
        content=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    response.raise_for_status()
    return response.json()


def _kill_process_tree(pid: int) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            logger.debug("web host taskkill failed for pid=%s", pid, exc_info=True)


_web_manager: WebHostManager | None = None


def get_web_manager() -> WebHostManager:
    global _web_manager
    if _web_manager is None:
        _web_manager = WebHostManager()
    return _web_manager


__all__ = ["WebSession", "WebHostManager", "get_web_manager"]
