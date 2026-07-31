# [OMNI] origin=codex ts=2026-07-31 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.providers.codebuddy_provider.py"
"""CodeBuddy CLI adapter for the Omnicompany normalized chat protocol.

CodeBuddy's non-interactive mode is a newline-delimited JSON stream:

    codebuddy -p --output-format stream-json [--resume <session-id>]

The prompt is written through stdin. This avoids Windows command-line length
and quoting limits when Omnicompany prepends plan context to a user turn.
Each prompt starts one CLI process; later turns resume the durable CodeBuddy
session announced by the ``system/init`` event.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from typing import Any, AsyncIterator

from ..normalized_protocol import NormalizedMessage
from .base import BaseProvider, ProviderOptions

logger = logging.getLogger(__name__)


DEFAULT_CODEBUDDY_PATH = (
    os.environ.get("OMNI_CODEBUDDY_PATH")
    or shutil.which("codebuddy")
    or "codebuddy"
)


def _resolve_codebuddy_launch(codebuddy_path: str) -> list[str]:
    """Bypass the npm ``.cmd`` shim on Windows and launch its Node entrypoint."""
    if os.name == "nt" and codebuddy_path.lower().endswith(".cmd"):
        entrypoint = os.path.join(
            os.path.dirname(codebuddy_path),
            "node_modules",
            "@tencent-ai",
            "codebuddy-code",
            "bin",
            "codebuddy",
        )
        node = shutil.which("node")
        if node and os.path.isfile(entrypoint):
            return [node, entrypoint]
    return [codebuddy_path]


async def _read_all(stream: asyncio.StreamReader | None) -> bytes:
    if stream is None:
        return b""
    chunks: list[bytes] = []
    while True:
        chunk = await stream.read(65_536)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


async def _iter_stdout_lines(stream: asyncio.StreamReader) -> AsyncIterator[str]:
    """Read NDJSON without ``StreamReader.readline``'s 64 KiB line limit."""
    buffer = b""
    while True:
        chunk = await stream.read(65_536)
        if not chunk:
            break
        buffer += chunk
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            line = buffer[:newline]
            buffer = buffer[newline + 1 :]
            if line.endswith(b"\r"):
                line = line[:-1]
            text = line.decode("utf-8", errors="replace")
            if "\ufffd" in text:
                raise RuntimeError("CodeBuddy stdout contains invalid UTF-8")
            yield text
    if buffer:
        text = buffer.decode("utf-8", errors="replace").rstrip("\r")
        if "\ufffd" in text:
            raise RuntimeError("CodeBuddy stdout contains invalid UTF-8")
        yield text


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(proc.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            await asyncio.wait_for(killer.wait(), timeout=5)
        except Exception:
            logger.debug("CodeBuddy process-tree termination failed", exc_info=True)
    try:
        if proc.returncode is None:
            proc.kill()
    except ProcessLookupError:
        pass
    try:
        await proc.wait()
    except Exception:
        pass


def _permission_mode(value: Any) -> str:
    mode = str(value or "bypassPermissions")
    if mode in {
        "acceptEdits",
        "bypassPermissions",
        "default",
        "plan",
        "dontAsk",
        "auto",
    }:
        return mode
    return "default"


class CodeBuddyProvider(BaseProvider):
    """Spawn-per-prompt CodeBuddy CLI provider."""

    def __init__(self, options: ProviderOptions) -> None:
        super().__init__(options)
        self._connected = False
        self._queue: asyncio.Queue[NormalizedMessage | None] = asyncio.Queue()
        self._run_task: asyncio.Task[None] | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._aborted = False
        self._send_lock = asyncio.Lock()
        self._session_id = (
            str(options.get("provider_session_id"))
            if options.get("provider_session_id")
            else None
        )
        self._session_announced = bool(self._session_id)
        self._usage: dict[str, Any] | None = None
        self._result_failed = False

    async def connect(self) -> None:
        if self._connected:
            return
        self._connected = True
        logger.info(
            "CodeBuddyProvider connected (path=%s)",
            self.options.get("codebuddy_path", DEFAULT_CODEBUDDY_PATH),
        )

    def _build_args(self) -> list[str]:
        opts: dict[str, Any] = dict(self.options)
        path = str(opts.get("codebuddy_path") or DEFAULT_CODEBUDDY_PATH)
        args = [
            *_resolve_codebuddy_launch(path),
            "-p",
            "--output-format",
            "stream-json",
            "--permission-mode",
            _permission_mode(opts.get("permission_mode")),
        ]
        if self._session_id:
            args += ["--resume", self._session_id]
        model = str(opts.get("model") or "").strip()
        if model:
            args += ["--model", model]
        effort = str(opts.get("effort") or "").strip()
        if effort:
            args += ["--effort", effort]
        max_turns = opts.get("max_turns")
        if isinstance(max_turns, int) and max_turns > 0:
            args += ["--max-turns", str(max_turns)]
        system_prompt = str(opts.get("system_prompt") or "").strip()
        if system_prompt:
            args += ["--append-system-prompt", system_prompt]
        allowed_tools = opts.get("allowed_tools")
        if isinstance(allowed_tools, list) and allowed_tools:
            args += ["--allowedTools", *[str(item) for item in allowed_tools]]
        disallowed_tools = opts.get("disallowed_tools")
        if isinstance(disallowed_tools, list) and disallowed_tools:
            args += ["--disallowedTools", *[str(item) for item in disallowed_tools]]
        return args

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        configured = self.options.get("env")
        if isinstance(configured, dict):
            env.update({str(key): str(value) for key, value in configured.items()})
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    async def send_prompt(
        self,
        prompt: str,
        options: dict[str, Any] | None = None,
    ) -> None:
        if not self._connected:
            raise RuntimeError("CodeBuddyProvider not connected; call connect() first")
        async with self._send_lock:
            if self._run_task and not self._run_task.done():
                await self._abort_running_turn("new CodeBuddy prompt superseded the previous turn")
            if options:
                self.options.update(options)
            self._run_task = asyncio.create_task(self._run(prompt))

    async def _run(self, prompt: str) -> None:
        self._usage = None
        self._result_failed = False
        try:
            await self._queue.put(
                {
                    "kind": "status",
                    "text": "codebuddy_run_started",
                    "sessionId": self._session_id,
                }
            )
            opts: dict[str, Any] = dict(self.options)
            cwd = str(opts.get("cwd") or "") or None
            spawn_kwargs: dict[str, Any] = {}
            if os.name == "nt":
                spawn_kwargs["creationflags"] = getattr(
                    subprocess, "CREATE_NO_WINDOW", 0
                )
            proc = await asyncio.create_subprocess_exec(
                *self._build_args(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=self._build_env(),
                **spawn_kwargs,
            )
            self._proc = proc
            if proc.stdin is None or proc.stdout is None:
                raise RuntimeError("CodeBuddy subprocess pipes were not created")
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
            stderr_task = asyncio.create_task(_read_all(proc.stderr))
            try:
                async for line in _iter_stdout_lines(proc.stdout):
                    for message in self._line_to_normalized(line):
                        await self._queue.put(message)
            finally:
                stderr = await stderr_task
            returncode = await proc.wait()
            exit_code = int(returncode or 0)
            if self._aborted:
                await self._queue.put(
                    {
                        "kind": "complete",
                        "sessionId": self._session_id,
                        "aborted": True,
                    }
                )
            elif exit_code != 0:
                tail = stderr.decode("utf-8", errors="replace").strip()[-1000:]
                await self._queue.put(
                    {
                        "kind": "error",
                        "sessionId": self._session_id,
                        "error": f"CodeBuddy CLI exited with code {exit_code}: {tail}",
                    }
                )
                await self._queue.put(
                    {
                        "kind": "complete",
                        "sessionId": self._session_id,
                        "exitCode": exit_code,
                    }
                )
            else:
                complete: NormalizedMessage = {
                    "kind": "complete",
                    "sessionId": self._session_id,
                    "exitCode": 1 if self._result_failed else 0,
                }
                if self._usage:
                    complete["usage"] = self._usage  # type: ignore[typeddict-unknown-key]
                await self._queue.put(complete)
        except asyncio.CancelledError:
            await self._queue.put(
                {
                    "kind": "complete",
                    "sessionId": self._session_id,
                    "aborted": True,
                }
            )
            raise
        except Exception as exc:
            proc = self._proc
            if proc is not None and proc.returncode is None:
                await _terminate_process_tree(proc)
            logger.exception("CodeBuddyProvider run failed")
            await self._queue.put(
                {
                    "kind": "error",
                    "sessionId": self._session_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            await self._queue.put(
                {
                    "kind": "complete",
                    "sessionId": self._session_id,
                    "exitCode": 1,
                }
            )
        finally:
            self._proc = None
            self._aborted = False

    async def interrupt(self) -> None:
        await self._abort_running_turn("user interrupted CodeBuddy")

    async def _abort_running_turn(self, reason: str) -> None:
        task = self._run_task
        if task is None or task.done():
            return
        logger.info("CodeBuddyProvider aborting running turn: %s", reason)
        self._aborted = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            await _terminate_process_tree(proc)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def disconnect(self) -> None:
        await self._abort_running_turn("CodeBuddyProvider disconnect")
        await self._queue.put(None)
        self._connected = False

    async def consume_messages(self) -> AsyncIterator[NormalizedMessage]:
        while True:
            message = await self._queue.get()
            if message is None:
                break
            yield message

    def _capture_session(self, data: dict[str, Any]) -> list[NormalizedMessage]:
        new_id = data.get("session_id") or data.get("sessionId")
        if isinstance(new_id, str) and new_id:
            self._session_id = new_id
            if not self._session_announced:
                self._session_announced = True
                return [
                    {
                        "kind": "session_created",
                        "newSessionId": new_id,
                        "sessionId": new_id,
                    }
                ]
        return []

    def _line_to_normalized(self, line: str) -> list[NormalizedMessage]:
        line = line.strip()
        if not line:
            return []
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("CodeBuddy non-JSON stdout line skipped: %.200r", line)
            return []
        if not isinstance(data, dict):
            return []

        out = self._capture_session(data)
        event_type = str(data.get("type") or "")
        sid = self._session_id

        if event_type == "assistant":
            message = data.get("message")
            blocks = message.get("content") if isinstance(message, dict) else []
            if not isinstance(blocks, list):
                blocks = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type") or "")
                if block_type in {"text", "output_text"}:
                    text = block.get("text")
                    if isinstance(text, str) and text:
                        out.append(
                            {"kind": "text", "content": text, "sessionId": sid}
                        )
                elif block_type == "thinking":
                    text = block.get("thinking") or block.get("text")
                    if isinstance(text, str) and text:
                        out.append(
                            {"kind": "thinking", "content": text, "sessionId": sid}
                        )
                elif block_type in {"tool_use", "server_tool_use"}:
                    tool_input = block.get("input")
                    out.append(
                        {
                            "kind": "tool_use",
                            "toolId": str(block.get("id") or ""),
                            "toolName": str(block.get("name") or "tool"),
                            "input": tool_input
                            if isinstance(tool_input, dict)
                            else {},
                            "sessionId": sid,
                        }
                    )
            return out

        if event_type == "user":
            message = data.get("message")
            blocks = message.get("content") if isinstance(message, dict) else []
            if not isinstance(blocks, list):
                blocks = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if str(block.get("type") or "") not in {
                    "tool_result",
                    "server_tool_result",
                }:
                    continue
                out.append(
                    {
                        "kind": "tool_result",
                        "toolId": str(
                            block.get("tool_use_id") or block.get("toolUseId") or ""
                        ),
                        "result": block.get("content"),
                        "isError": bool(
                            block.get("is_error") or block.get("isError")
                        ),
                        "sessionId": sid,
                    }
                )
            return out

        if event_type == "result":
            usage = data.get("usage")
            if isinstance(usage, dict):
                self._usage = {
                    "input_tokens": int(usage.get("input_tokens") or 0),
                    "output_tokens": int(usage.get("output_tokens") or 0),
                    "cache_creation_input_tokens": int(
                        usage.get("cache_creation_input_tokens") or 0
                    ),
                    "cache_read_input_tokens": int(
                        usage.get("cache_read_input_tokens") or 0
                    ),
                }
            if data.get("is_error"):
                self._result_failed = True
                errors = data.get("errors")
                error = (
                    "; ".join(str(item) for item in errors)
                    if isinstance(errors, list) and errors
                    else str(data.get("result") or "CodeBuddy execution failed")
                )
                out.append({"kind": "error", "error": error, "sessionId": sid})
            return out

        if event_type == "stream_event":
            event = data.get("event")
            delta = event.get("delta") if isinstance(event, dict) else None
            if isinstance(delta, dict):
                delta_type = str(delta.get("type") or "")
                text = delta.get("text") or delta.get("thinking")
                if isinstance(text, str) and text:
                    out.append(
                        {
                            "kind": "thinking"
                            if "thinking" in delta_type
                            else "stream_delta",
                            "content": text,
                            "sessionId": sid,
                        }
                    )
            return out

        if event_type == "rate_limit_event":
            out.append(
                {
                    "kind": "status",
                    "sessionId": sid,
                    "text": "codebuddy_rate_limit",
                }
            )
            return out

        return out


__all__ = ["CodeBuddyProvider", "DEFAULT_CODEBUDDY_PATH"]
