# [OMNI] origin=ai-ide ts=2026-07-18 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.providers.kimi_provider.py"
"""KimiProvider — 包装 Kimi Code CLI (kimi.cmd) 非交互 prompt 模式.

跟 CodexProvider 是兄弟形态 (都是包"本地 LLM CLI binary"). 区别:
- Codex: 包 `codex.cmd` (OpenAI), 走 openai-codex-sdk Python 包
- Kimi:  包 `kimi.cmd` (Moonshot), 直接 spawn `kimi -p <prompt> --output-format stream-json`
  子进程, 逐行读 stdout JSON 事件. 每个 send_prompt 启一个新子进程; 多轮靠
  `kimi -S <session_id>` resume (session id 来自末行 meta 事件).

实测事件形态 (kimi 0.27.0, 2026-07-18 本机 probe)
=================================================

stdout 一行一个 JSON 对象 (message 级, 无 token delta):

| kimi stream-json 行                                                           | NormalizedMessage              |
|-------------------------------------------------------------------------------|--------------------------------|
| {"role":"assistant","content":"..."}                                          | text                           |
| {"role":"assistant","tool_calls":[{"id":..,"function":{"name":..,"arguments":..}}]} | tool_use (arguments 是 JSON 字符串, 需再 parse) |
| {"role":"tool","tool_call_id":"...","content":"..."}                           | tool_result                    |
| {"role":"meta","type":"session.resume_hint","session_id":"session_<uuid>"}     | session_created (首轮)          |

注意: 工具自身 stdout 会泄到父进程 stdout (实测 Bash 工具跑 `echo hello-kimi`
时, "hello-kimi" 原样出现在 JSON 行之前) — 解析器必须容忍非 JSON 行 (skip + log).

限制: `-p` 非交互模式不能跟 `--yolo` / `--auto` 组合 (CLI 直接报错), 实测
prompt 模式下工具调用不询问直接执行; permission_mode 无法映射, 忽略.

依赖跟环境
==========

- 本地装: kimi CLI (npm 全局, Windows 上为 .cmd, 实测 0.27.0)
- 认证: `kimi login` (device-code flow)
- ProviderOptions 扩展 (TypedDict extras):
  - `kimi_path`: kimi CLI 绝对路径, 默认 shutil.which("kimi")
  - `provider_session_id`: resume 已有 kimi session (载入/采纳用)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from typing import Any, AsyncIterator, Mapping

from ..normalized_protocol import NormalizedMessage
from .base import BaseProvider, ProviderOptions

logger = logging.getLogger(__name__)


DEFAULT_KIMI_PATH = shutil.which("kimi") or shutil.which("kimi.cmd") or "kimi"

# Dashboard 继续使用既有 session.model 字段，不另建 provider 配置库。模型值使用
# "<source>/<upstream-model>"；Kimi CLI 官方的 KIMI_MODEL_* 机制会在内存里合成
# 临时 provider，不改写 ~/.kimi-code/config.toml。
KIMI_RUNTIME_PROFILES: dict[str, dict[str, str]] = {
    "dashscope-token-plan": {
        "base_url": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "key_env": "DASHSCOPE_TOKEN_PLAN_API_KEY",
        "default_model": "qwen3.7-max",
        "display_name": "Alibaba Token Plan",
    },
    "dashscope-payg": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_env": "DASHSCOPE_API_KEY",
        "default_model": "qwen3.7-max",
        "display_name": "Alibaba DashScope PAYG",
    },
    "the_company": {
        "base_url": "https://internal-llm-proxy.example.com/v1",
        "key_env": "THE_COMPANY_API_KEY",
        "default_model": "gpt-5.6-terra",
        "display_name": "the_company API",
    },
}


def _parse_runtime_profile(model: Any) -> tuple[str, str] | None:
    """把 Dashboard model 值解析成 (source, upstream model).

    支持只填 source 以采用该源默认模型；未知前缀保持 Kimi CLI 原有 -m 行为。
    """
    value = str(model or "").strip()
    if not value:
        return None
    if value in KIMI_RUNTIME_PROFILES:
        return value, KIMI_RUNTIME_PROFILES[value]["default_model"]
    source, separator, upstream_model = value.partition("/")
    if not separator or source not in KIMI_RUNTIME_PROFILES or not upstream_model.strip():
        return None
    return source, upstream_model.strip()


def _build_runtime_environment(
    options: Mapping[str, Any],
    base_environment: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    """为 profile 模型生成 Kimi CLI 临时 provider 环境。

    普通模型返回原 env（未提供时返回 None，保留 subprocess 的环境继承语义）。
    """
    profile_model = _parse_runtime_profile(options.get("model"))
    configured_env = options.get("env")
    if not profile_model:
        return dict(configured_env) if isinstance(configured_env, Mapping) else None

    source, upstream_model = profile_model
    profile = KIMI_RUNTIME_PROFILES[source]
    env = dict(base_environment if base_environment is not None else os.environ)
    if isinstance(configured_env, Mapping):
        env.update({str(key): str(value) for key, value in configured_env.items()})
    key_env = profile["key_env"]
    api_key = str(env.get(key_env) or "").strip()
    if not api_key:
        raise RuntimeError(
            f"Kimi runtime profile '{source}' requires local secret {key_env}"
        )
    env.update({
        "KIMI_MODEL_NAME": upstream_model,
        "KIMI_MODEL_API_KEY": api_key,
        "KIMI_MODEL_PROVIDER_TYPE": "openai",
        "KIMI_MODEL_BASE_URL": profile["base_url"],
        "KIMI_MODEL_MAX_CONTEXT_SIZE": "262144",
        "KIMI_MODEL_DISPLAY_NAME": f"{profile['display_name']} · {upstream_model}",
    })
    return env


def _resolve_kimi_launch(kimi_path: str) -> list[str]:
    """返 launch argv 前缀. Windows 上 .cmd shim 会把 argv 交给 cmd.exe 重解析,
    多行 prompt (plan 注入前缀必带换行) 会在第一个 \\n 处被截断 —— 绕过 shim
    直接 spawn node + 入口 mjs (实测 0.27.0 多行 prompt 完整到达)."""
    if os.name == "nt" and kimi_path.lower().endswith(".cmd"):
        direct = os.path.join(
            os.path.dirname(kimi_path), "node_modules", "@moonshot-ai", "kimi-code", "dist", "main.mjs"
        )
        if os.path.isfile(direct):
            return ["node", direct]
    return [kimi_path]


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


KIMI_PREVIOUS_TURN_DRAIN_GRACE_SEC = _env_float("OMNI_KIMI_PREVIOUS_TURN_DRAIN_GRACE_SEC", 0.5)


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate kimi plus child tool processes on Windows (同 codex.py 的做法).

    kimi 的 Bash 工具会起子 shell, 只 kill 主进程可能留下孤儿子进程;
    taskkill /T 是最小的本地修补.
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
                logger.warning("kimi taskkill /T failed for pid=%s returncode=%s", proc.pid, returncode)
        except Exception:
            logger.debug("kimi process-tree termination failed", exc_info=True)
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
    """Chunk 读 stdout 再自行切行 — 避开 StreamReader.readline() 的 64 KiB 上限
    (大工具输出单行 JSON 可能超限, 同 codex.py 的 stdout reader patch)."""
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
            yield line.decode("utf-8", errors="replace")
    if buffer:
        yield buffer.decode("utf-8", errors="replace").rstrip("\r")


class KimiProvider(BaseProvider):
    """Kimi Code CLI 包装. spawn-per-prompt, stream-json → NormalizedMessage."""

    def __init__(self, options: ProviderOptions) -> None:
        super().__init__(options)
        self._connected = False
        self._queue: asyncio.Queue[NormalizedMessage | None] = asyncio.Queue()
        self._run_task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._aborted = False
        self._send_lock = asyncio.Lock()
        # 多轮 resume: 首轮无 id, meta 行回 session_id 后存这里; 后续轮带 -S.
        stored_session_id = (
            str(options.get("provider_session_id")) if options.get("provider_session_id") else None
        )
        # Dashboard 的旧 meta 只有一个 provider_session_id，没有记录它属于哪个第三方源。
        # profile 会话在 daemon 重启后宁可开新会话，也不能把未知来源的 resume id 发给
        # 另一个网关。managed Kimi 保持原有恢复行为。
        self._session_id: str | None = (
            None if _parse_runtime_profile(options.get("model")) else stored_session_id
        )
        self._session_announced = False
        self._last_model_option = str(options.get("model") or "").strip()

    async def connect(self) -> None:
        if self._connected:
            return
        self._connected = True
        logger.info(
            "KimiProvider connected (kimi_path=%s)",
            self.options.get("kimi_path", DEFAULT_KIMI_PATH),
        )

    def _build_args(self, prompt: str) -> list[str]:
        opts: dict[str, Any] = dict(self.options)
        kimi_path = str(opts.get("kimi_path") or DEFAULT_KIMI_PATH)
        args = [*_resolve_kimi_launch(kimi_path), "-p", prompt, "--output-format", "stream-json"]
        # -m 的优先级高于 KIMI_MODEL_*；profile 模型必须只走临时 provider 环境。
        if opts.get("model") and not _parse_runtime_profile(opts.get("model")):
            args += ["-m", str(opts["model"])]
        if self._session_id:
            args += ["-S", self._session_id]
        return args

    def _prepare_runtime(self, prompt: str) -> tuple[list[str], dict[str, str] | None]:
        """在每轮启动前解析当前 source；Dashboard 可在两轮之间更新 session.model。"""
        model_option = str(self.options.get("model") or "").strip()
        if model_option != self._last_model_option:
            previous_source = _parse_runtime_profile(self._last_model_option)
            current_source = _parse_runtime_profile(model_option)
            if previous_source or current_source:
                # 不把上一供应源的 resume id 带进新源；切回 managed Kimi 也启新会话。
                self._session_id = None
                self._session_announced = False
            self._last_model_option = model_option
        environment = _build_runtime_environment(self.options)
        return self._build_args(prompt), environment

    async def send_prompt(self, prompt: str, options: dict[str, Any] | None = None) -> None:
        if not self._connected:
            raise RuntimeError("KimiProvider not connected; call connect() first")

        # 等前一轮结束再启新轮; 等不到就 abort 旧轮 (同 codex.py 的防复读逻辑).
        async with self._send_lock:
            if self._run_task and not self._run_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._run_task),
                        timeout=KIMI_PREVIOUS_TURN_DRAIN_GRACE_SEC,
                    )
                except asyncio.TimeoutError:
                    await self._abort_running_turn("user sent a new Kimi prompt")
            if options:
                self.options.update(options)

        self._run_task = asyncio.create_task(self._run(prompt))

    async def _run(self, prompt: str) -> None:
        try:
            await self._queue.put({
                "kind": "status",
                "text": "kimi_run_started",
                "sessionId": self._session_id,
            })
            opts: dict[str, Any] = dict(self.options)
            cwd = str(opts.get("cwd") or "") or None
            args, runtime_env = self._prepare_runtime(prompt)
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=runtime_env,
            )
            self._proc = proc
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
                tail = stderr.decode("utf-8", errors="replace").strip()[-500:]
                await self._queue.put({
                    "kind": "error",
                    "sessionId": self._session_id,
                    "error": f"kimi CLI exited with code {returncode}: {tail}",
                })
                await self._queue.put({
                    "kind": "complete",
                    "sessionId": self._session_id,
                    "exitCode": int(returncode or 1),
                })
            else:
                await self._queue.put({
                    "kind": "complete",
                    "sessionId": self._session_id,
                    "exitCode": 0,
                })
        except asyncio.CancelledError:
            await self._queue.put({
                "kind": "complete",
                "sessionId": self._session_id,
                "aborted": True,
            })
            raise
        except Exception as e:
            logger.exception("KimiProvider run failed")
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
        await self._abort_running_turn("user interrupted Kimi")

    async def _abort_running_turn(self, reason: str) -> None:
        task = self._run_task
        if task is None or task.done():
            return
        logger.info("KimiProvider aborting running turn: %s", reason)
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
        await self._abort_running_turn("KimiProvider disconnect")
        await self._queue.put(None)
        self._connected = False

    async def consume_messages(self) -> AsyncIterator[NormalizedMessage]:
        while True:
            nm = await self._queue.get()
            if nm is None:
                break
            yield nm

    # ── stream-json 行 → NormalizedMessage 映射 ─────────────────────────────

    def _line_to_normalized(self, line: str) -> list[NormalizedMessage]:
        line = line.strip()
        if not line:
            return []
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            # 工具自身 stdout 会泄进父进程 stdout (见模块 docstring), 非 JSON 行跳过
            logger.debug("kimi non-JSON stdout line skipped: %.200r", line)
            return []
        if not isinstance(data, dict):
            return []

        role = data.get("role")
        if role == "meta":
            # 末行 meta 带 session_id → 首轮推 session_created, 后续轮同 id 不重复推
            if data.get("type") == "session.resume_hint":
                new_id = data.get("session_id")
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

        sid = self._session_id
        if role == "assistant":
            out: list[NormalizedMessage] = []
            content = data.get("content")
            if isinstance(content, str) and content:
                out.append({"kind": "text", "content": content, "sessionId": sid})
            for call in data.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                raw_args = fn.get("arguments")
                tool_input: dict[str, Any] = {}
                if isinstance(raw_args, str):
                    try:
                        parsed = json.loads(raw_args)
                        tool_input = parsed if isinstance(parsed, dict) else {"raw": raw_args}
                    except json.JSONDecodeError:
                        tool_input = {"raw": raw_args}
                elif isinstance(raw_args, dict):
                    tool_input = raw_args
                out.append({
                    "kind": "tool_use",
                    "toolId": str(call.get("id") or ""),
                    "toolName": str(fn.get("name") or "tool"),
                    "input": tool_input,
                    "sessionId": sid,
                })
            return out

        if role == "tool":
            return [{
                "kind": "tool_result",
                "toolId": str(data.get("tool_call_id") or ""),
                "result": data.get("content"),
                "isError": bool(data.get("is_error", False)),
                "sessionId": sid,
            }]

        return []


__all__ = ["KIMI_RUNTIME_PROFILES", "KimiProvider"]
