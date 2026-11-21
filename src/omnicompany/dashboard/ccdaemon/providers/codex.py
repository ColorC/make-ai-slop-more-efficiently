# [OMNI] origin=ai-ide ts=2026-05-11 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.providers.codex_provider.py"
"""CodexProvider — 包装 OpenAI Codex CLI (走 openai-codex-sdk Python 包).

跟 ClaudeCodeProvider 是兄弟形态 (都是包"本地 LLM CLI binary"). 区别:
- ClaudeCode: 包 `claude.exe` (Anthropic), 走 claude-agent-sdk Python
- Codex:      包 `codex.cmd` / `codex.exe` (OpenAI), 走 openai-codex-sdk Python

事件映射跟 claudecodeui 上游 [openai-codex.js](../../../../../../参考项目/claudecodeui/server/openai-codex.js)
一致 — 该 Node 文件用 `@openai/codex-sdk`, 我们用 Python 等价 `openai-codex-sdk`,
event/item 字段名 1:1 对齐.

Codex Event → NormalizedMessage 映射
====================================

| Codex SDK 事件                  | NormalizedMessage              |
|--------------------------------|--------------------------------|
| ThreadStartedEvent             | session_created (newSessionId) |
| TurnStartedEvent               | status (text='turn_started')   |
| TurnCompletedEvent             | complete (usage 进 status)     |
| TurnFailedEvent                | error                          |
| ThreadErrorEvent               | error                          |
| ItemCompletedEvent + item.type 分流: |                          |
|   AgentMessageItem             | text                           |
|   ReasoningItem                | thinking                       |
|   CommandExecutionItem         | tool_use (bash) + tool_result  |
|   FileChangeItem               | tool_use (edit) + tool_result  |
|   McpToolCallItem              | tool_use + tool_result         |
|   WebSearchItem                | tool_use (web_search)          |
|   TodoListItem                 | (skip, 内部 todo 状态)         |
|   ErrorItem                    | error                          |

ItemStartedEvent / ItemUpdatedEvent 当前 MVP 不映射 — 等 ItemCompletedEvent 拿
最终内容. 后续要真 streaming 可以 ItemUpdated 拼 stream_delta (跟踪 delta 需保留
上次 item 状态 per item.id).

依赖跟环境
==========

- pip 装: `openai-codex-sdk` (PyPI 0.1.11+)
- 本地装: codex CLI (`npm i -g @openai/codex` 或 SDK 自带 install)
- 认证: codex 走 OpenAI 账号登录 (codex login), 跟 claude binary 走订阅认证类似
- ProviderOptions 扩展 (TypedDict extras):
  - `codex_path`: codex CLI 绝对路径, 默认 'C:/Users/user/AppData/Roaming/npm/codex.cmd'
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from types import SimpleNamespace
from typing import Any, AsyncIterator

from ..write_scope import planned_write_scope
from ..normalized_protocol import NormalizedMessage
from .base import BaseProvider, ProviderOptions

logger = logging.getLogger(__name__)


DEFAULT_CODEX_PATH = "C:/Users/user/AppData/Roaming/npm/codex.cmd"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


CODEX_START_THREAD_TIMEOUT_SEC = _env_float("OMNI_CODEX_START_THREAD_TIMEOUT_SEC", 45.0)
CODEX_EVENT_HEARTBEAT_SEC = _env_float("OMNI_CODEX_EVENT_HEARTBEAT_SEC", 30.0)
CODEX_PREVIOUS_TURN_DRAIN_GRACE_SEC = _env_float("OMNI_CODEX_PREVIOUS_TURN_DRAIN_GRACE_SEC", 0.5)
# 0 means "do not abort just because Codex has not emitted a visible SDK event".
CODEX_IDLE_HARD_TIMEOUT_SEC = float(os.environ.get("OMNI_CODEX_IDLE_HARD_TIMEOUT_SEC", "0") or "0")


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate Codex plus child tool processes on Windows.

    openai-codex-sdk only kills the immediate Codex process. On Windows the CLI
    can leave its PowerShell child alive, so AbortSignal does not unblock until
    the shell command naturally finishes. taskkill /T is the smallest local
    repair that makes mid-turn interrupt observable at the user level.
    """
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
                logger.warning("Codex taskkill /T failed for pid=%s returncode=%s", proc.pid, returncode)
        except Exception:
            logger.debug("Codex process-tree termination failed", exc_info=True)
    try:
        if proc.returncode is None:
            proc.kill()
    except ProcessLookupError:
        pass
    try:
        await proc.wait()
    except Exception:
        pass


def _to_namespace(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _to_namespace(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_to_namespace(v) for v in value]
    return value


def _patch_codex_sdk_parser() -> None:
    """Keep openai-codex-sdk from aborting on valid newer Codex events.

    The local Python SDK currently declares several item.status fields as small
    Literal unions, while real Codex streams can send newer values such as
    file_change=in_progress or command_execution=declined. Those Pydantic
    errors abort the entire streamed turn before our provider can render later
    tool results, so preserve the JSON shape as namespaces when validation
    fails for known item events.
    """
    try:
        import openai_codex_sdk.thread as sdk_thread
    except Exception:
        return

    current = getattr(sdk_thread, "parse_thread_event_line", None)
    if current is None or getattr(current, "_omni_safe_parser", False):
        return
    original_parse = current

    def safe_parse_thread_event_line(line: str) -> Any:
        try:
            return original_parse(line)
        except Exception as exc:
            try:
                data = json.loads(line)
            except Exception:
                raise exc
            item = data.get("item") if isinstance(data, dict) else None
            if isinstance(item, dict) and item.get("type") in {
                "command_execution",
                "file_change",
                "mcp_tool_call",
            }:
                return _to_namespace(data)
            raise exc

    setattr(safe_parse_thread_event_line, "_omni_safe_parser", True)
    sdk_thread.parse_thread_event_line = safe_parse_thread_event_line


def _patch_codex_sdk_stdout_reader() -> None:
    """Avoid asyncio StreamReader.readline() 64 KiB limit in openai-codex-sdk.

    Codex emits one JSON event per line. Large command output can make a single
    event line much larger than asyncio's default readline limit, which raises
    "Separator is not found, and chunk exceeded the limit" before we can render
    the tool result. Read stdout in chunks and split lines ourselves.
    """
    try:
        import openai_codex_sdk.exec as sdk_exec
    except Exception:
        return

    if getattr(sdk_exec, "_terminate_process", None) is not _terminate_process_tree:
        sdk_exec._terminate_process = _terminate_process_tree

    exec_cls = getattr(sdk_exec, "CodexExec", None)
    if exec_cls is None or getattr(exec_cls.run, "_omni_chunk_reader", False):
        return

    async def safe_run(self: Any, args: Any) -> AsyncIterator[str]:
        if args.signal is not None and args.signal.aborted:
            raise sdk_exec.AbortError(sdk_exec._format_abort_reason(args.signal.reason))

        command_args = self._build_command_args(args)
        env = self._build_env(args)
        proc = await asyncio.create_subprocess_exec(
            self.executable_path,
            *command_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        if proc.stdin is None or proc.stdout is None:
            try:
                proc.kill()
            finally:
                raise sdk_exec.CodexExecError("Child process missing stdin/stdout")

        stderr_task = asyncio.create_task(sdk_exec._read_all(proc.stderr))
        abort_waiter = None
        if args.signal is not None:
            abort_waiter = asyncio.create_task(sdk_exec._wait_abort(args.signal))

        buffer = b""
        try:
            proc.stdin.write(args.input.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

            while True:
                read_task = asyncio.create_task(proc.stdout.read(65_536))
                wait_set = {read_task} if abort_waiter is None else {read_task, abort_waiter}
                done, _pending = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)

                if abort_waiter is not None and abort_waiter in done:
                    read_task.cancel()
                    await asyncio.gather(read_task, return_exceptions=True)
                    await sdk_exec._terminate_process(proc)
                    raise sdk_exec.AbortError(
                        sdk_exec._format_abort_reason(args.signal.reason if args.signal else None)
                    )

                chunk = read_task.result()
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
                    yield line.decode("utf-8", errors="replace")

            if buffer:
                yield buffer.decode("utf-8", errors="replace").rstrip("\r")

            returncode = await proc.wait()
            stderr = await stderr_task
            if returncode != 0:
                raise sdk_exec.CodexExecError(
                    f"Codex Exec exited with code {returncode}: {stderr.decode('utf-8', errors='replace')}"
                )
        finally:
            if abort_waiter is not None:
                abort_waiter.cancel()
                await asyncio.gather(abort_waiter, return_exceptions=True)
            if proc.returncode is None:
                await sdk_exec._terminate_process(proc)
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)

    setattr(safe_run, "_omni_chunk_reader", True)
    exec_cls.run = safe_run


def _codex_thread_permission_options(permission_mode: str | None) -> dict[str, str]:
    """Map ChatComposer modes to openai-codex-sdk Python ThreadOptions."""
    if permission_mode == "bypassPermissions":
        return {
            "sandbox_mode": "danger-full-access",
            "approval_policy": "never",
        }
    if permission_mode == "acceptEdits":
        return {
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
        }
    return {
        "sandbox_mode": "workspace-write",
        "approval_policy": "untrusted",
    }


class CodexProvider(BaseProvider):
    """OpenAI Codex CLI 包装. 走 openai-codex-sdk Python."""

    def __init__(self, options: ProviderOptions) -> None:
        super().__init__(options)
        self._codex: Any = None  # openai_codex_sdk.Codex 实例
        self._thread: Any = None  # 当前 Thread (lazy 创建)
        self._connected = False
        self._queue: asyncio.Queue[NormalizedMessage | None] = asyncio.Queue()
        self._run_task: asyncio.Task | None = None
        self._abort_controller: Any = None
        self._send_lock = asyncio.Lock()
        # 一个 turn 内 codex 通常发多个 reasoning ItemCompletedEvent (每步一个),
        # 直接每个都推 thinking NormalizedMessage 会让前端画 10+ "Thought for a
        # few seconds" 折叠卡 (用户 2026-05-12 反馈). 这里 buffer per-turn,
        # TurnCompletedEvent 时合并发一条.
        self._reasoning_buffer: list[str] = []

    async def connect(self) -> None:
        if self._connected:
            return
        try:
            from openai_codex_sdk import Codex
        except ImportError as e:
            raise RuntimeError(
                "CodexProvider 启动失败: 缺 openai-codex-sdk. "
                "`pip install openai-codex-sdk`"
            ) from e
        _patch_codex_sdk_parser()
        _patch_codex_sdk_stdout_reader()

        opts: dict[str, Any] = dict(self.options)
        codex_path = opts.get("codex_path", DEFAULT_CODEX_PATH)
        codex_opts: dict[str, Any] = {"codex_path_override": codex_path}
        # codex env vars 透传
        if opts.get("env"):
            codex_opts["env"] = opts["env"]

        try:
            self._codex = Codex(codex_opts)
        except Exception as e:
            raise RuntimeError(f"CodexProvider 启动失败 ({type(e).__name__}): {e}") from e

        self._connected = True
        logger.info("CodexProvider connected (codex_path=%s)", codex_path)

    async def _ensure_thread(self) -> None:
        """lazy 在第一次 send_prompt 时建 Thread (Codex SDK Thread 是 turn 容器).

        注: Thread.id 在 start_thread 后可能仍为 None, 真 id 由后端在 ThreadStartedEvent
        里返. 这里先建 Thread 不推 session_created, 等 ThreadStartedEvent 出来再推.
        """
        if self._thread is not None:
            return

        opts: dict[str, Any] = dict(self.options)
        thread_opts: dict[str, Any] = {}
        if opts.get("cwd"):
            thread_opts["working_directory"] = opts["cwd"]
        if opts.get("model"):
            thread_opts["model"] = opts["model"]
        if opts.get("effort"):
            # openai-codex-sdk ThreadOptions.model_reasoning_effort is the
            # actual Codex CLI config path; storing metadata alone is not enough.
            thread_opts["model_reasoning_effort"] = opts["effort"]
        thread_opts["skip_git_repo_check"] = True
        scope = planned_write_scope(
            cwd=str(opts.get("cwd") or os.getcwd()),
            active_plan=opts.get("active_plan"),
        )
        additional_directories = [
            str(path)
            for path in [*scope.roots[1:], *(p.parent for p in scope.paths)]
        ]
        if additional_directories:
            thread_opts["additional_directories"] = list(dict.fromkeys(additional_directories))
        thread_opts.update(_codex_thread_permission_options(opts.get("permission_mode")))

        # Codex.start_thread 是 sync; 用 to_thread 避免阻塞 event loop
        provider_session_id = opts.get("provider_session_id") or opts.get("codex_thread_id")
        start_or_resume = self._codex.resume_thread if provider_session_id else self._codex.start_thread
        start_args = (provider_session_id, thread_opts or None) if provider_session_id else (thread_opts or None,)
        self._thread = await asyncio.wait_for(
            asyncio.to_thread(start_or_resume, *start_args),
            timeout=CODEX_START_THREAD_TIMEOUT_SEC,
        )
        # 若 SDK 已经在 start_thread 后填了 id, 立刻推 session_created;
        # 否则等 ThreadStartedEvent 在 _event_to_normalized 推
        if self._thread.id:
            await self._queue.put({
                "kind": "session_created",
                "newSessionId": self._thread.id,
                "sessionId": self._thread.id,
            })

    async def send_prompt(self, prompt: str, options: dict[str, Any] | None = None) -> None:
        if not self._connected or self._codex is None:
            raise RuntimeError("CodexProvider not connected; call connect() first")

        # 等前一轮 run_task 结束再启新轮. 不等的话两个 task 同时跑同一 thread,
        # 旧轮的 events (assistant_message 等) 跟新轮的混进队列 → 前端看到旧消息
        # 复读 (用户 2026-05-12 反馈).
        async with self._send_lock:
            if self._run_task and not self._run_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._run_task),
                        timeout=CODEX_PREVIOUS_TURN_DRAIN_GRACE_SEC,
                    )
                except asyncio.TimeoutError:
                    await self._abort_running_turn("user sent a new Codex prompt")

            if options:
                self.options.update(options)
            await self._ensure_thread()
            sid = self._thread.id

            try:
                from openai_codex_sdk.abort import AbortController
            except Exception:
                AbortController = None  # type: ignore[assignment]

            controller = AbortController() if AbortController is not None else None
            self._abort_controller = controller

        async def _run() -> None:
            saw_complete = False
            try:
                # Thread.run_streamed 是 async coroutine, 返回 StreamedTurn dataclass.
                # 真事件流在 StreamedTurn.events (AsyncIterator[ThreadEvent]).
                async def _await_with_heartbeat(awaitable: Any, phase: str) -> Any:
                    task = asyncio.create_task(awaitable)
                    started = asyncio.get_running_loop().time()
                    try:
                        while True:
                            done, _ = await asyncio.wait({task}, timeout=CODEX_EVENT_HEARTBEAT_SEC)
                            if task in done:
                                return await task
                            elapsed = asyncio.get_running_loop().time() - started
                            await self._queue.put({
                                "kind": "status",
                                "text": phase,
                                "sessionId": sid,
                                "elapsed_ms": int(elapsed * 1000),
                                "canInterrupt": True,
                            })
                            if CODEX_IDLE_HARD_TIMEOUT_SEC > 0 and elapsed >= CODEX_IDLE_HARD_TIMEOUT_SEC:
                                if controller is not None:
                                    try:
                                        controller.abort(f"Codex idle timeout after {int(elapsed)}s")
                                    except Exception:
                                        logger.debug("CodexProvider hard-timeout abort failed", exc_info=True)
                                task.cancel()
                                raise asyncio.TimeoutError()
                    except asyncio.CancelledError:
                        task.cancel()
                        raise

                await self._queue.put({
                    "kind": "status",
                    "text": "codex_run_started",
                    "sessionId": sid,
                })
                turn_options = {"signal": controller.signal} if controller is not None else None
                streamed = await _await_with_heartbeat(
                    self._thread.run_streamed(prompt, turn_options),
                    "codex_starting",
                )
                events = streamed.events.__aiter__()
                saw_event = False
                while True:
                    try:
                        event = await _await_with_heartbeat(
                            events.__anext__(),
                            "codex_waiting_for_next_event" if saw_event else "codex_waiting_for_first_event",
                        )
                    except StopAsyncIteration:
                        break
                    saw_event = True
                    for nm in self._event_to_normalized(event, sid):
                        if nm.get("kind") == "complete":
                            saw_complete = True
                        await self._queue.put(nm)
                    if saw_complete:
                        break
                if not saw_complete:
                    await self._queue.put({
                        "kind": "complete",
                        "sessionId": sid,
                        "exitCode": 0,
                    })

            except asyncio.CancelledError:
                await self._queue.put({
                    "kind": "complete",
                    "sessionId": sid,
                    "aborted": True,
                })
                raise
            except asyncio.TimeoutError as e:
                if controller is not None:
                    try:
                        controller.abort("Codex turn exceeded configured hard idle timeout")
                    except Exception:
                        logger.debug("CodexProvider timeout abort failed", exc_info=True)
                logger.exception("CodexProvider run_streamed timed out")
                await self._queue.put({
                    "kind": "error",
                    "sessionId": sid,
                    "error": f"Codex exceeded configured hard idle timeout ({type(e).__name__})",
                })
                await self._queue.put({
                    "kind": "complete",
                    "sessionId": sid,
                    "exitCode": 1,
                })
            except Exception as e:
                if controller is not None and controller.signal.aborted:
                    await self._queue.put({
                        "kind": "complete",
                        "sessionId": sid,
                        "aborted": True,
                    })
                else:
                    logger.exception("CodexProvider run_streamed failed")
                    await self._queue.put({
                        "kind": "error",
                        "sessionId": sid,
                        "error": f"{type(e).__name__}: {e}",
                    })
                    await self._queue.put({
                        "kind": "complete",
                        "sessionId": sid,
                        "exitCode": 1,
                    })
            finally:
                if self._abort_controller is controller:
                    self._abort_controller = None

        self._run_task = asyncio.create_task(_run())

    async def interrupt(self) -> None:
        # openai-codex-sdk 有 AbortController / AbortSignal, 但当前 Thread.run_streamed
        # 签名只 (input, turn_options), turn_options 含 signal. MVP: 直接 cancel task
        # (codex CLI 子进程也会被 SDK 内部清理)
        await self._abort_running_turn("user interrupted Codex")

    async def _abort_running_turn(self, reason: str) -> None:
        task = self._run_task
        if task is None or task.done():
            return
        controller = self._abort_controller
        if controller is not None:
            try:
                controller.abort(reason)
            except Exception:
                logger.debug("CodexProvider abort signal failed", exc_info=True)
        else:
            task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=2.0 if controller is not None else None)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        except (asyncio.CancelledError, Exception):
            pass

    async def disconnect(self) -> None:
        await self._abort_running_turn("CodexProvider disconnect")
        await self._queue.put(None)
        self._connected = False
        self._codex = None
        self._thread = None

    async def consume_messages(self) -> AsyncIterator[NormalizedMessage]:
        while True:
            nm = await self._queue.get()
            if nm is None:
                break
            yield nm

    # ── Event/Item → NormalizedMessage 映射 ──────────────────────────────────

    def _event_to_normalized(self, event: Any, sid: str) -> list[NormalizedMessage]:
        """Codex SDK event → 0+ 个 NormalizedMessage. 见模块 docstring 映射表."""
        ev_type = getattr(event, "type", None)

        # Thread / Turn 生命周期事件
        if ev_type == "thread.started":
            # 真 thread id 来自服务端, 这里推 session_created (前面 _ensure_thread 若提早
            # 推过会有重复, 但 frontend 也会幂等处理 newSessionId; SDK 实测 thread.id 在
            # start_thread 后通常为 None, 这里是首次出真 id 的位置)
            thread_id = getattr(event, "thread_id", None) or sid
            return [{
                "kind": "session_created",
                "newSessionId": thread_id,
                "sessionId": thread_id,
            }]

        if ev_type == "turn.started":
            # turn 开新一轮, 清 reasoning buffer
            self._reasoning_buffer.clear()
            return [{"kind": "status", "text": "turn_started", "sessionId": sid}]

        if ev_type == "turn.completed":
            out: list[NormalizedMessage] = []
            # turn 结束时把累积的 reasoning 合并成一条 thinking NormalizedMessage 推出.
            # 避免每个 reasoning ItemCompletedEvent 都产生一张 thinking 卡 (10+ 张
            # "Thought for a few seconds" 那个体验糟糕).
            if self._reasoning_buffer:
                combined = "\n\n".join(t for t in self._reasoning_buffer if t.strip())
                if combined:
                    out.append({
                        "kind": "thinking",
                        "content": combined,
                        "sessionId": sid,
                    })
                self._reasoning_buffer.clear()
            # codex usage 挂在 complete NM 上 — chat.py 翻 legacy result 帧时读 nm["usage"]
            # 塞到 frame["usage"], 前端 adapter 一并算 token_budget. TurnCompletedEvent.usage
            # 三字段: input_tokens / cached_input_tokens / output_tokens. cached_input_tokens
            # 在 claude 对应 cache_read_input_tokens (复用 cache 部分).
            usage = getattr(event, "usage", None)
            complete_nm: NormalizedMessage = {"kind": "complete", "sessionId": sid, "exitCode": 0}
            if usage is not None:
                complete_nm["usage"] = {
                    "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                    "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                    "cached_input_tokens": int(getattr(usage, "cached_input_tokens", 0) or 0),
                }
            out.append(complete_nm)
            return out

        if ev_type == "turn.failed":
            err = getattr(event, "error", None)
            return [{
                "kind": "error",
                "sessionId": sid,
                "error": str(err) if err else "turn failed",
            }]

        if ev_type == "thread.error":
            msg = getattr(event, "message", "thread error")
            return [{"kind": "error", "sessionId": sid, "error": msg}]

        # Item events
        if ev_type == "item.completed":
            return self._item_to_normalized(getattr(event, "item", None), sid)

        # ItemStarted/Updated: show long-running tools before completion.
        if ev_type in ("item.started", "item.updated"):
            item = getattr(event, "item", None)
            if item is None:
                return []
            item_type = getattr(item, "type", None)
            if item_type == "command_execution":
                return [{
                    "kind": "tool_use",
                    "toolId": getattr(item, "id", "") or "",
                    "toolName": "Bash",
                    "input": {"command": getattr(item, "command", "")},
                    "sessionId": sid,
                }]
            if item_type == "file_change":
                return [{
                    "kind": "tool_use",
                    "toolId": getattr(item, "id", "") or "",
                    "toolName": "edit",
                    "input": self._file_change_input(item, include_content=False),
                    "sessionId": sid,
                }]
            if item_type == "mcp_tool_call":
                return [{
                    "kind": "tool_use",
                    "toolId": getattr(item, "id", "") or "",
                    "toolName": f"{getattr(item, 'server', '?')}.{getattr(item, 'tool', '?')}",
                    "input": getattr(item, "arguments", {}) or {},
                    "sessionId": sid,
                }]
            return []

        return []

    def _item_to_normalized(self, item: Any, sid: str) -> list[NormalizedMessage]:
        """ItemCompletedEvent.item → NormalizedMessage 序列."""
        if item is None:
            return []
        item_type = getattr(item, "type", None)
        item_id = getattr(item, "id", "") or ""

        if item_type == "agent_message":
            text = getattr(item, "text", "") or ""
            return [{"kind": "text", "content": text, "sessionId": sid}] if text else []

        if item_type == "reasoning":
            # 不立即推 thinking — buffer 到 turn 结束再合并 (避免 N 张折叠卡)
            text = getattr(item, "text", "") or ""
            if text.strip():
                self._reasoning_buffer.append(text)
            return []

        if item_type == "command_execution":
            tool_use: NormalizedMessage = {
                "kind": "tool_use",
                "toolId": item_id,
                "toolName": "Bash",
                "input": {"command": getattr(item, "command", "")},
                "sessionId": sid,
            }
            # item.started 通常已推 tool_use；completed 再带一次稳定 toolId，前端按 id
            # 合并，防止 started 事件丢失时结果没有可挂载的父节点。
            status = getattr(item, "status", "")
            exit_code = getattr(item, "exit_code", None)
            return [tool_use, {
                "kind": "tool_result",
                "toolId": item_id,
                "result": getattr(item, "aggregated_output", "") or "",
                "isError": status == "failed" or (exit_code is not None and exit_code != 0),
                "exitCode": exit_code if exit_code is not None else 0,
                "sessionId": sid,
            }]

        if item_type == "file_change":
            file_input = self._file_change_input(item, include_content=True)
            return [
                {
                    "kind": "tool_use",
                    "toolId": item_id,
                    "toolName": "edit",
                    "input": file_input,
                    "sessionId": sid,
                },
                {
                    "kind": "tool_result",
                    "toolId": item_id,
                    "result": f"status={getattr(item, 'status', '')}",
                    "isError": str(getattr(item, "status", "")) == "failed",
                    "sessionId": sid,
                },
            ]

        if item_type == "mcp_tool_call":
            tool_use: NormalizedMessage = {
                "kind": "tool_use",
                "toolId": item_id,
                "toolName": f"{getattr(item, 'server', '?')}.{getattr(item, 'tool', '?')}",
                "input": getattr(item, "arguments", {}) or {},
                "sessionId": sid,
            }
            tool_result: NormalizedMessage = {
                "kind": "tool_result",
                "toolId": item_id,
                "result": getattr(item, "result", None),
                "isError": bool(getattr(item, "error", None)),
                "sessionId": sid,
            }
            return [tool_use, tool_result]

        if item_type == "web_search":
            return [{
                "kind": "tool_use",
                "toolId": item_id,
                "toolName": "web_search",
                "input": {"query": getattr(item, "query", "")},
                "sessionId": sid,
            }]

        if item_type == "todo_list":
            # todo_list 内部状态, 不映射到面向用户的 NormalizedMessage; 留 future status
            return []

        if item_type == "error":
            return [{
                "kind": "error",
                "sessionId": sid,
                "error": getattr(item, "message", "codex item error"),
            }]

        return []

    def _file_change_input(self, item: Any, *, include_content: bool) -> dict[str, Any]:
        changes: list[dict[str, Any]] = []
        first_path = ""
        for change in (getattr(item, "changes", []) or []):
            if hasattr(change, "__dict__"):
                data = dict(change.__dict__)
            elif isinstance(change, dict):
                data = dict(change)
            else:
                data = {"raw": str(change)}
            path = str(data.get("path") or data.get("file_path") or data.get("filename") or "")
            if path and not first_path:
                first_path = path
            changes.append(data)

        cwd = str(self.options.get("cwd") or "")
        resolved = first_path
        if first_path and not os.path.isabs(first_path) and cwd:
            resolved = os.path.join(cwd, first_path)

        content = ""
        if include_content and resolved and os.path.isfile(resolved):
            try:
                with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(120_000)
            except Exception:
                content = ""

        result = {
            "changes": changes,
            "status": getattr(item, "status", ""),
        }
        if include_content:
            result.update({
                "file_path": first_path or resolved,
                "old_string": "",
                "new_string": content or "\n".join(
                    f"{c.get('kind', 'change')}: {c.get('path') or c.get('file_path') or c}"
                    for c in changes
                ),
            })
        return result


__all__ = ["CodexProvider"]
