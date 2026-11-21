# [OMNI] origin=ai-ide ts=2026-07-18 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.providers.opencode_provider.py"
"""OpenCodeProvider — 包装 sst/opencode CLI (opencode.cmd) 非交互 run 模式.

跟 CodexProvider / KimiProvider 是兄弟形态 (都是包"本地 LLM CLI binary"):
spawn `opencode run --format json --dir <cwd> [--session <id>] [--model <m>] <prompt>`
子进程, 逐行读 stdout JSON 事件. 每个 send_prompt 启一个新子进程; 多轮靠
`--session <id>` resume (session id 来自每个事件的顶层 sessionID 字段).

参数形态跟 omnicompany 的
[`chatui/server/opencode-cli.js`](../../chatui/server/opencode-cli.js) :196-211 一致
(该 Node 实现已在生产用).

实测事件形态 (opencode 1.18.2, 2026-07-18 本机 probe)
=====================================================

stdout 一行一个 JSON 对象, 顶层 `type` + `sessionID`, 内容在 `part` 里:

| opencode 事件 (type / part.type)                                       | NormalizedMessage       |
|------------------------------------------------------------------------|-------------------------|
| step_start  (part {"type":"step-start", ...})                          | (skip)                  |
| text        (part {"type":"text","text":"...","time":{start,end}})     | text                    |
| reasoning   (part {"type":"reasoning","text":"..."})                   | thinking                |
| tool_use    (part {"type":"tool","tool":"bash","callID":"call_...",     | tool_use + tool_result  |
|   "state":{"status":"completed","input":{...},"output":"...",           | (state.status=error 时   |
|   "metadata":{"exit":0}}})                                             |  isError=True)          |
| step_finish (part {"type":"step-finish","reason":"stop"|"tool-calls",   | (skip, tokens 累计进     |
|   "tokens":{"input":..,"output":..,"reasoning":..,                      |  complete.usage)         |
|   "cache":{"read":..,"write":..}},"cost":..})                           |                         |
| error                                                                  | error                   |

turn 结束信号 = 进程退出 (一个 run 可有多个 step_start/step_finish 对),
complete 在进程 close 时推, usage 累计各 step_finish 的 tokens.

依赖跟环境
==========

- 本地装: opencode CLI (npm 全局, Windows 上为 .cmd, 实测 1.18.2)
- 认证: opencode 自家 auth (`opencode auth login`)
- ProviderOptions 扩展 (TypedDict extras):
  - `opencode_path`: opencode CLI 绝对路径, 默认 shutil.which("opencode")
  - `provider_session_id`: resume 已有 opencode session (载入/采纳用)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from typing import Any, AsyncIterator

from omnicompany.packages.services._core.agent.external_workers.opencode import (
    DEFAULT_OPENCODE_MODEL,
    default_opencode_config_path,
)

from ..normalized_protocol import NormalizedMessage
from .base import BaseProvider, ProviderOptions

logger = logging.getLogger(__name__)


DEFAULT_OPENCODE_PATH = shutil.which("opencode") or shutil.which("opencode.cmd") or "opencode"


def _resolve_opencode_launch(opencode_path: str) -> list[str]:
    """返 launch argv 前缀. Windows 上 .cmd shim 会把 argv 交给 cmd.exe 重解析,
    多行 prompt (plan 注入前缀必带换行) 会在第一个 \\n 处被截断 —— 绕过 shim
    直接 spawn 原生 opencode.exe (实测 1.18.2 多行 prompt 完整到达)."""
    if os.name == "nt" and opencode_path.lower().endswith(".cmd"):
        direct = os.path.join(
            os.path.dirname(opencode_path), "node_modules", "opencode-ai", "bin", "opencode.exe"
        )
        if os.path.isfile(direct):
            return [direct]
    return [opencode_path]


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


OPENCODE_PREVIOUS_TURN_DRAIN_GRACE_SEC = _env_float("OMNI_OPENCODE_PREVIOUS_TURN_DRAIN_GRACE_SEC", 0.5)


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate opencode plus child tool processes on Windows (同 codex.py 的做法)."""
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
            returncode = await asyncio.wait_for(killer.wait(), timeout=5)
            if returncode != 0:
                logger.warning("opencode taskkill /T failed for pid=%s returncode=%s", proc.pid, returncode)
        except Exception:
            logger.debug("opencode process-tree termination failed", exc_info=True)
    try:
        if proc.returncode is None:
            proc.kill()
    except ProcessLookupError:
        pass
    try:
        await proc.wait()
    except Exception:
        pass


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
    """Chunk 读 stdout 再自行切行 — 避开 StreamReader.readline() 的 64 KiB 上限."""
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
            buffer = buffer[newline + 1:]
            if line.endswith(b"\r"):
                line = line[:-1]
            text = line.decode("utf-8", errors="replace")
            if "\ufffd" in text:
                raise RuntimeError(
                    "OpenCode stdout contains Unicode replacement characters"
                )
            yield text
    if buffer:
        text = buffer.decode("utf-8", errors="replace").rstrip("\r")
        if "\ufffd" in text:
            raise RuntimeError(
                "OpenCode stdout contains Unicode replacement characters"
            )
        yield text


class OpenCodeProvider(BaseProvider):
    """sst/opencode CLI 包装. spawn-per-prompt, json events → NormalizedMessage."""

    def __init__(self, options: ProviderOptions) -> None:
        super().__init__(options)
        self._connected = False
        self._queue: asyncio.Queue[NormalizedMessage | None] = asyncio.Queue()
        self._run_task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._aborted = False
        self._send_lock = asyncio.Lock()
        # 多轮 resume: 首轮无 id, 首个事件的 sessionID 字段抓回来后存这里;
        # 后续轮带 --session.
        self._session_id: str | None = (
            str(options.get("provider_session_id")) if options.get("provider_session_id") else None
        )
        self._session_announced = False
        # per-turn token 累计 (各 step_finish 的 tokens 是 per-step 值, 求和)
        self._usage_acc: dict[str, int] = self._empty_usage()

    @staticmethod
    def _empty_usage() -> dict[str, int]:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

    async def connect(self) -> None:
        if self._connected:
            return
        self._connected = True
        logger.info(
            "OpenCodeProvider connected (opencode_path=%s)",
            self.options.get("opencode_path", DEFAULT_OPENCODE_PATH),
        )

    def _build_args(self) -> list[str]:
        opts: dict[str, Any] = dict(self.options)
        opencode_path = str(opts.get("opencode_path") or DEFAULT_OPENCODE_PATH)
        args = [
            *_resolve_opencode_launch(opencode_path),
            "run",
            "--format",
            "json",
            "--auto",
            "--agent",
            str(opts.get("agent") or "omni-worker"),
        ]
        # opencode 的工作区选择走 --dir (光靠子进程 cwd 不够, 见 opencode-cli.js)
        cwd = str(opts.get("cwd") or "")
        if cwd:
            args += ["--dir", cwd]
        if self._session_id:
            args += ["--session", self._session_id]
        model = str(opts.get("model") or DEFAULT_OPENCODE_MODEL)
        if "/" not in model:
            raise ValueError(
                f"OpenCode model must be a full provider/model id; got {model!r}"
            )
        args += ["--model", model]
        return args

    def _build_env(self) -> dict[str, str]:
        opts: dict[str, Any] = dict(self.options)
        env = os.environ.copy()
        configured = opts.get("env")
        if isinstance(configured, dict):
            env.update({str(key): str(value) for key, value in configured.items()})
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        config = str(
            opts.get("opencode_config")
            or env.get("OPENCODE_CONFIG")
            or default_opencode_config_path()
        )
        config_path = os.path.abspath(os.path.expanduser(config))
        if not os.path.isfile(config_path):
            raise RuntimeError(f"OpenCode config does not exist: {config_path}")
        env["OPENCODE_CONFIG"] = config_path
        return env

    async def send_prompt(self, prompt: str, options: dict[str, Any] | None = None) -> None:
        if not self._connected:
            raise RuntimeError("OpenCodeProvider not connected; call connect() first")

        # 等前一轮结束再启新轮; 等不到就 abort 旧轮 (同 codex.py 的防复读逻辑).
        async with self._send_lock:
            if self._run_task and not self._run_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._run_task),
                        timeout=OPENCODE_PREVIOUS_TURN_DRAIN_GRACE_SEC,
                    )
                except asyncio.TimeoutError:
                    await self._abort_running_turn("user sent a new OpenCode prompt")
            if options:
                self.options.update(options)

        self._run_task = asyncio.create_task(self._run(prompt))

    async def _run(self, prompt: str) -> None:
        self._usage_acc = self._empty_usage()
        try:
            await self._queue.put({
                "kind": "status",
                "text": "opencode_run_started",
                "sessionId": self._session_id,
            })
            opts: dict[str, Any] = dict(self.options)
            cwd = str(opts.get("cwd") or "") or None
            proc = await asyncio.create_subprocess_exec(
                *self._build_args(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=self._build_env(),
            )
            self._proc = proc
            if proc.stdin is None:
                raise RuntimeError("OpenCode stdin pipe was not created")
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
            stderr_task = asyncio.create_task(_read_all(proc.stderr))
            try:
                async for line in _iter_stdout_lines(proc.stdout):  # type: ignore[arg-type]
                    for nm in self._line_to_normalized(line):
                        await self._queue.put(nm)
            finally:
                stderr = await stderr_task
            returncode = await proc.wait()
            if self._aborted:
                await self._queue.put({
                    "kind": "complete",
                    "sessionId": self._session_id,
                    "aborted": True,
                })
            elif returncode != 0:
                decoded_stderr = stderr.decode("utf-8", errors="replace")
                if "\ufffd" in decoded_stderr:
                    raise RuntimeError(
                        "OpenCode stderr contains Unicode replacement characters"
                    )
                tail = decoded_stderr.strip()[-500:]
                await self._queue.put({
                    "kind": "error",
                    "sessionId": self._session_id,
                    "error": f"opencode CLI exited with code {returncode}: {tail}",
                })
                await self._queue.put({
                    "kind": "complete",
                    "sessionId": self._session_id,
                    "exitCode": int(returncode or 1),
                })
            else:
                complete_nm: NormalizedMessage = {
                    "kind": "complete",
                    "sessionId": self._session_id,
                    "exitCode": 0,
                }
                # usage 挂 complete — chat.py 翻 token_budget 时读 nm["usage"] (同 codex)
                if any(self._usage_acc.values()):
                    complete_nm["usage"] = dict(self._usage_acc)  # type: ignore[typeddict-unknown-key]
                await self._queue.put(complete_nm)
        except asyncio.CancelledError:
            await self._queue.put({
                "kind": "complete",
                "sessionId": self._session_id,
                "aborted": True,
            })
            raise
        except Exception as e:
            logger.exception("OpenCodeProvider run failed")
            await self._queue.put({
                "kind": "error",
                "sessionId": self._session_id,
                "error": f"{type(e).__name__}: {e}",
            })
            await self._queue.put({
                "kind": "complete",
                "sessionId": self._session_id,
                "exitCode": 1,
            })
        finally:
            self._proc = None
            self._aborted = False

    async def interrupt(self) -> None:
        await self._abort_running_turn("user interrupted OpenCode")

    async def _abort_running_turn(self, reason: str) -> None:
        task = self._run_task
        if task is None or task.done():
            return
        logger.info("OpenCodeProvider aborting running turn: %s", reason)
        self._aborted = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            await _terminate_process_tree(proc)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def disconnect(self) -> None:
        await self._abort_running_turn("OpenCodeProvider disconnect")
        await self._queue.put(None)
        self._connected = False

    async def consume_messages(self) -> AsyncIterator[NormalizedMessage]:
        while True:
            nm = await self._queue.get()
            if nm is None:
                break
            yield nm

    # ── json 事件行 → NormalizedMessage 映射 ────────────────────────────────

    def _capture_session(self, data: dict[str, Any]) -> list[NormalizedMessage]:
        """每个事件顶层都带 sessionID — 首轮抓到就推 session_created (只推一次)."""
        new_id = data.get("sessionID")
        if isinstance(new_id, str) and new_id:
            self._session_id = new_id
            if not self._session_announced:
                self._session_announced = True
                return [{
                    "kind": "session_created",
                    "newSessionId": new_id,
                    "sessionId": new_id,
                }]
        return []

    def _line_to_normalized(self, line: str) -> list[NormalizedMessage]:
        line = line.strip()
        if not line:
            return []
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("opencode non-JSON stdout line skipped: %.200r", line)
            return []
        if not isinstance(data, dict):
            return []

        out: list[NormalizedMessage] = self._capture_session(data)
        sid = self._session_id
        ev_type = data.get("type")
        part = data.get("part") if isinstance(data.get("part"), dict) else {}

        if ev_type == "text":
            text = part.get("text") or data.get("text") or ""
            if isinstance(text, str) and text.strip():
                out.append({"kind": "text", "content": text, "sessionId": sid})
            return out

        if ev_type == "reasoning":
            text = part.get("text") or ""
            if isinstance(text, str) and text.strip():
                out.append({"kind": "thinking", "content": text, "sessionId": sid})
            return out

        if ev_type == "tool_use":
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            tool_id = str(part.get("callID") or part.get("id") or "")
            tool_input = state.get("input")
            out.append({
                "kind": "tool_use",
                "toolId": tool_id,
                "toolName": str(part.get("tool") or "tool"),
                "input": tool_input if isinstance(tool_input, dict) else {},
                "sessionId": sid,
            })
            status = str(state.get("status") or "")
            if status in ("completed", "error"):
                metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
                exit_code = metadata.get("exit")
                result: Any = state.get("output")
                if status == "error" and state.get("error"):
                    result = state.get("error")
                tool_result: NormalizedMessage = {
                    "kind": "tool_result",
                    "toolId": tool_id,
                    "result": result,
                    "isError": status == "error",
                    "sessionId": sid,
                }
                if isinstance(exit_code, int):
                    tool_result["exitCode"] = exit_code
                    if exit_code != 0:
                        tool_result["isError"] = True
                out.append(tool_result)
            return out

        if ev_type == "step_finish":
            tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            self._usage_acc["input_tokens"] += int(tokens.get("input") or 0)
            self._usage_acc["output_tokens"] += int(tokens.get("output") or 0)
            self._usage_acc["cached_input_tokens"] += int(cache.get("read") or 0)
            self._usage_acc["cache_creation_input_tokens"] += int(cache.get("write") or 0)
            return out

        if ev_type == "error":
            err = data.get("error") or part.get("error") or data.get("message") or "opencode error"
            out.append({
                "kind": "error",
                "sessionId": sid,
                "error": err if isinstance(err, str) else json.dumps(err, ensure_ascii=False),
            })
            return out

        # step_start / 其它: 不映射
        return out


__all__ = ["DEFAULT_OPENCODE_MODEL", "DEFAULT_OPENCODE_PATH", "OpenCodeProvider"]
