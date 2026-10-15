"""Thin JSONL client for an external AgentRuntime implementation.

This module is control-plane transport only. It deliberately contains no Agent
loop, provider SDK, Material routing, Team scheduling, or permission policy.
Every message is validated against the language-neutral repository schema.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
import inspect
import json
import os
from pathlib import Path
import subprocess
from typing import Any
import uuid

from jsonschema import Draft202012Validator


RuntimeMessage = dict[str, Any]
RuntimeEventSink = Callable[[RuntimeMessage], None | Awaitable[None]]


class AgentRuntimeProcessError(RuntimeError):
    """The external runtime process violated or lost its protocol boundary."""


def _default_schema_path() -> Path:
    packaged = (
        Path(__file__).resolve().parent
        / "contracts"
        / "v0"
        / "agent-runtime.schema.json"
    )
    if packaged.is_file():
        return packaged
    return (
        Path(__file__).resolve().parents[4]
        / "contracts"
        / "agent-runtime"
        / "v0"
        / "agent-runtime.schema.json"
    )


class AgentRuntimeProcessClient:
    """Multiplex schema-valid AgentRuntime messages over newline-delimited JSON."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        schema_path: str | os.PathLike[str] | None = None,
        close_timeout_seconds: float = 15.0,
    ) -> None:
        if not command or any(not str(part).strip() for part in command):
            raise ValueError("AgentRuntime command must contain non-blank arguments")
        if close_timeout_seconds <= 0:
            raise ValueError("close_timeout_seconds must be positive")
        self._command = tuple(str(part) for part in command)
        self._cwd = None if cwd is None else str(Path(cwd).resolve())
        self._env = env
        self._close_timeout_seconds = close_timeout_seconds
        resolved_schema = Path(schema_path or _default_schema_path()).resolve()
        self._validator = Draft202012Validator(
            json.loads(resolved_schema.read_text(encoding="utf-8"))
        )
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail = ""
        self._write_lock = asyncio.Lock()
        self._capability_waiters: dict[str, asyncio.Future[RuntimeMessage]] = {}
        self._run_waiters: dict[str, asyncio.Future[RuntimeMessage]] = {}
        self._event_sinks: dict[str, RuntimeEventSink] = {}
        self._closed = False

    async def __aenter__(self) -> AgentRuntimeProcessClient:
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._closed:
            raise AgentRuntimeProcessError("AgentRuntime client is closed")
        if self._process is not None:
            return
        process_options: dict[str, Any] = {}
        if os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            cwd=self._cwd,
            env=self._env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **process_options,
        )
        self._reader_task = asyncio.create_task(self._read_messages())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def capabilities(self, request_id: str | None = None) -> RuntimeMessage:
        request = {
            "kind": "runtime.capabilities.request",
            "protocol_version": "0.1.0",
            "request_id": request_id or f"cap-{uuid.uuid4().hex}",
        }
        self._validate(request)
        await self.start()
        future = asyncio.get_running_loop().create_future()
        self._capability_waiters[request["request_id"]] = future
        try:
            await self._send(request)
            return await future
        finally:
            self._capability_waiters.pop(request["request_id"], None)

    def validate(self, message: object) -> None:
        """Validate one boundary message without starting the runtime process."""
        self._validate(message)

    async def run(
        self,
        request: RuntimeMessage,
        event_sink: RuntimeEventSink | None = None,
    ) -> RuntimeMessage:
        self._validate(request)
        if request.get("kind") != "run.start":
            raise ValueError("run() requires a run.start message")
        run_id = str(request["run_id"])
        if run_id in self._run_waiters:
            raise ValueError(f"run_id is already active: {run_id}")
        await self.start()
        future = asyncio.get_running_loop().create_future()
        self._run_waiters[run_id] = future
        if event_sink is not None:
            self._event_sinks[run_id] = event_sink
        try:
            await self._send(request)
            return await future
        finally:
            self._run_waiters.pop(run_id, None)
            self._event_sinks.pop(run_id, None)

    async def cancel(
        self,
        run_id: str,
        reason: str,
        *,
        request_id: str | None = None,
    ) -> None:
        request = {
            "kind": "run.cancel",
            "protocol_version": "0.1.0",
            "request_id": request_id or f"cancel-{uuid.uuid4().hex}",
            "run_id": run_id,
            "reason": reason,
        }
        self._validate(request)
        if run_id not in self._run_waiters:
            raise ValueError(f"run_id is not active: {run_id}")
        await self._send(request)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            return
        for run_id in tuple(self._run_waiters):
            try:
                await self.cancel(run_id, "AgentRuntime client closing")
            except (AgentRuntimeProcessError, ValueError):
                pass
        if process.stdin is not None:
            process.stdin.close()
            await process.stdin.wait_closed()
        try:
            await asyncio.wait_for(process.wait(), timeout=self._close_timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        if self._reader_task is not None:
            await asyncio.gather(self._reader_task, return_exceptions=True)
        if self._stderr_task is not None:
            await asyncio.gather(self._stderr_task, return_exceptions=True)
        self._process = None

    def _validate(self, message: object) -> None:
        errors = sorted(self._validator.iter_errors(message), key=lambda error: list(error.path))
        if not errors:
            return
        detail = "; ".join(error.message for error in errors[:5])
        raise AgentRuntimeProcessError(f"AgentRuntime contract violation: {detail}")

    async def _send(self, message: RuntimeMessage) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise AgentRuntimeProcessError("AgentRuntime process is not writable")
        encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            process.stdin.write(encoded)
            await process.stdin.drain()

    async def _read_messages(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            while line := await process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AgentRuntimeProcessError(
                        f"AgentRuntime stdout is not JSONL: {line[:200]!r}"
                    ) from exc
                self._validate(message)
                kind = message.get("kind")
                if kind == "runtime.capabilities":
                    waiter = self._capability_waiters.get(str(message.get("request_id")))
                    if waiter is not None and not waiter.done():
                        waiter.set_result(message)
                    continue
                run_id = str(message.get("run_id") or "")
                if kind == "run.result":
                    waiter = self._run_waiters.get(run_id)
                    if waiter is not None and not waiter.done():
                        waiter.set_result(message)
                    continue
                sink = self._event_sinks.get(run_id)
                if sink is not None:
                    returned = sink(message)
                    if inspect.isawaitable(returned):
                        await returned
        except Exception as exc:
            error = exc if isinstance(exc, AgentRuntimeProcessError) else AgentRuntimeProcessError(str(exc))
            self._fail_waiters(error)
            raise
        finally:
            if self._run_waiters or self._capability_waiters:
                self._fail_waiters(
                    AgentRuntimeProcessError(
                        "AgentRuntime process closed before completing requests"
                        + (f"; stderr: {self._stderr_tail[-1000:]}" if self._stderr_tail else "")
                    )
                )

    async def _read_stderr(self) -> None:
        process = self._process
        assert process is not None and process.stderr is not None
        while chunk := await process.stderr.read(4096):
            self._stderr_tail = (self._stderr_tail + chunk.decode(errors="replace"))[-65_536:]

    def _fail_waiters(self, error: AgentRuntimeProcessError) -> None:
        for future in (*self._capability_waiters.values(), *self._run_waiters.values()):
            if not future.done():
                future.set_exception(error)
