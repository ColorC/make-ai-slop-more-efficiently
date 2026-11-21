# [OMNI] origin=ai-ide ts=2026-06-23 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.providers.omni_agent_cli.py"
"""omni-agent stream-json CLI shim —— 把自家 AgentNodeLoop 暴露成一个可被上游 CCUI
spawn 的子进程 provider。

道路: docs/plans/dashboard/[2026-06-23]聊天后端迁上游CCUI/plan.md 阶段 1。

为什么是它
==========
上游 claudecodeui(Node)无法 import 一个 Python 活对象(AgentNodeLoop + Bus)。
解法跟 cursor/gemini provider 同形: CCUI 把 provider 当成"spawn 一个 CLI、用
stream-json 通信"的子进程。这个文件就是那个 CLI —— 它是迁移后**唯一留下的自家
聊天后端代码**, 且很薄: 复用现成 OmniAgentProvider(已实现 hook 捕获 → NormalizedMessage
队列), 本壳只负责 stdin 收 prompt、stdout 按行吐 NormalizedMessage(NDJSON)。

输入协议(stdin)
================
单个 JSON 对象(整段 stdin 或单行均可):
    {"prompt": "...", "model": "qwen3.6-plus", "cwd": ".", "options": {...}}
若 stdin 不是合法 JSON, 整段文本当作 prompt(裸文本兜底)。

输出协议(stdout, NDJSON)
=========================
每行一个 NormalizedMessage(沿用上游 kind):
    {"kind":"session_created","newSessionId":"...","sessionId":"..."}
    {"kind":"tool_use","toolId":"...","toolName":"...","input":{...},"sessionId":"..."}
    {"kind":"tool_result","toolId":"...","result":"...","isError":false,"sessionId":"..."}
    {"kind":"thinking","content":"...","sessionId":"..."}
    {"kind":"text","content":"...","sessionId":"..."}
    {"kind":"complete","sessionId":"...","exitCode":0,"actualSessionId":"..."}
    {"kind":"error","sessionId":"...","error":"..."}
收到 complete / error 即收尾退出。

stdout 纪律: **只有 NDJSON 事件进 stdout**。所有日志/诊断走 stderr(本模块顶部把
logging 配到 stderr)。注意上游若有 print() 默认进 stdout, 实测阶段需确认无污染。

abort
=====
进程收到 SIGINT/SIGTERM → 调 provider.interrupt()(agent.abort()), 让其吐出
aborted complete 后优雅退出。Windows 上 add_signal_handler 不可用, 走 KeyboardInterrupt 兜底。

流式颗粒度
==========
turn-level(单 turn LLM 跑完才推 text), 与现 omni_agent.py 一致, 不退步。
token-level 是后续上游 LLMCallRouter 改造, 不在本壳范围(plan §7 R2)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

# stdout 留给 NDJSON 事件; 日志一律 stderr。
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger("omni_agent_cli")

# 终结 kind: 见到即收尾。
_TERMINAL_KINDS = frozenset({"complete", "error"})


class _ProviderLike(Protocol):
    """pump() 只依赖 provider 这几个方法 —— 便于测试注入假 provider。"""

    async def connect(self) -> None: ...
    async def send_prompt(self, prompt: str, options: dict[str, Any] | None = None) -> None: ...
    async def interrupt(self) -> None: ...
    async def disconnect(self) -> None: ...
    def consume_messages(self) -> AsyncIterator[dict]: ...


def _emit(nm: dict, out: Any) -> None:
    """把一个 NormalizedMessage 作为一行 NDJSON 写到 out。"""
    out.write(json.dumps(nm, ensure_ascii=False) + "\n")
    out.flush()


# ── on-disk 历史转写(D4: 刷新后 fetchHistory 能还原)──────────────────────────
# 每条 chat NormalizedMessage 旁路落成一行 JSONL, 与 claude/codex 的磁盘历史同构,
# 路径 ~/.omni_agent/projects/<encoded-cwd>/<sessionId>.jsonl。读端在
# chatui omni-agent-sessions.provider.ts fetchHistory(编码规则两端必须一致)。
# 纪律: 全程吞异常, 绝不写 stdout(stdout 只给 NDJSON 协议)。

def _encode_cwd(cwd: str | None) -> str:
    """把 cwd 编码成目录名 —— / \\ : 全替成 -, 空则 _。与 TS 读端 encodeCwd 逐字一致。"""
    s = (cwd or "").strip() or "_"
    for ch in ("/", "\\", ":"):
        s = s.replace(ch, "-")
    return s


def _omni_agent_projects_dir() -> Path:
    return Path.home() / ".omni_agent" / "projects"


class _Transcript:
    """把每条 chat NormalizedMessage append 成一行 JSONL。lifecycle kind 不落。"""

    _PERSIST_KINDS = frozenset({"user", "text", "thinking", "tool_use", "tool_result", "error"})

    def __init__(self, cwd: str | None, session_id: str) -> None:
        self._fh: Any = None
        self._sid = session_id
        try:
            d = _omni_agent_projects_dir() / _encode_cwd(cwd)
            d.mkdir(parents=True, exist_ok=True)
            self._fh = open(d / f"{session_id}.jsonl", "a", encoding="utf-8", newline="\n")
        except Exception:  # noqa: BLE001
            self._fh = None

    def write(self, nm: dict) -> None:
        if self._fh is None or nm.get("kind") not in self._PERSIST_KINDS:
            return
        try:
            line = dict(nm)
            line.setdefault("sessionId", self._sid)
            line.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
            line.setdefault("id", f"omni_agent-{uuid.uuid4().hex[:12]}")
            self._fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            self._fh.flush()
        except Exception:  # noqa: BLE001
            pass

    def write_user(self, prompt: str) -> None:
        # 用户那一句不在事件流里(hook 只发 assistant/tool), 单独补一行 kind=user。
        # TS 读端把 kind=user 渲染成 role=user 的 text 气泡(本仓无 'user' MessageKind)。
        self.write({"kind": "user", "content": prompt, "sessionId": self._sid})

    def close(self) -> None:
        try:
            if self._fh is not None:
                self._fh.close()
        except Exception:  # noqa: BLE001
            pass


async def pump(
    provider: _ProviderLike,
    prompt: str,
    options: dict[str, Any] | None,
    out: Any,
    cwd: str | None = None,
) -> int:
    """核心循环: connect → send_prompt → 把 provider 的 NormalizedMessage 逐条吐到 out,
    见到 terminal kind 收尾。返回退出码(0=正常/PASS, 非0=error/abort)。
    同时把每条 chat 消息旁路落 JSONL(D4: 刷新恢复历史), 落盘失败绝不影响协议。

    与 LLM/网络无关的逻辑都在这里 —— 用假 provider 即可单测(见 tests)。
    """
    await provider.connect()
    await provider.send_prompt(prompt, options)

    transcript: _Transcript | None = None
    exit_code = 0
    try:
        async for nm in provider.consume_messages():
            _emit(nm, out)
            kind = nm.get("kind")
            # 旁路落盘: session_created 时拿到 sessionId 开转写并补写 user 那一行,
            # 之后每条 chat 消息 append。全程吞异常, 不污染 stdout NDJSON。
            try:
                if kind == "session_created" and transcript is None:
                    sid = nm.get("newSessionId") or nm.get("sessionId")
                    if sid:
                        transcript = _Transcript(cwd, str(sid))
                        transcript.write_user(prompt)
                elif transcript is not None:
                    transcript.write(nm)
            except Exception:  # noqa: BLE001
                pass
            if kind == "error":
                exit_code = 1
                break
            if kind == "complete":
                if nm.get("aborted"):
                    exit_code = 130
                else:
                    exit_code = int(nm.get("exitCode") or 0)
                break
    finally:
        if transcript is not None:
            transcript.close()
        await provider.disconnect()
    return exit_code


def _build_default_provider(model: str | None, cwd: str | None) -> _ProviderLike:
    """构造默认聊天 provider —— 复用 OmniAgentProvider + 最简 AgentNodeLoop 子类。

    与 ccdaemon/chat.py 里 omni_agent 分支同构(那里是临时占位); 后续若要可配置
    agent_class, 在这里扩展输入协议即可, 不动 pump。
    """
    from omnicompany.bus.memory import MemoryBus
    from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
    from omnicompany.packages.services._core.agent.routers.single_tool import FinishRouter

    from .omni_agent import OmniAgentProvider

    class _DefaultChatAgent(AgentNodeLoop):
        NODE_PROMPT = "你是聊天助手. 用户问你, 你直接调 finish 工具返回回复. 不要调其他工具."
        TOOL_ROUTERS = [FinishRouter]

    bus = MemoryBus()

    # OmniAgentProvider.connect() 是 async; MemoryBus.connect() 也是。把 bus.connect 推到
    # pump 的 connect 之前 —— 这里先建好对象, connect 由调用方在事件循环里完成。
    async def _connect_with_bus(self: Any) -> None:  # noqa: ANN401
        if not getattr(bus, "_connected", False):
            await bus.connect()
            try:
                bus._connected = True  # 幂等标记(MemoryBus 无害)
            except Exception:  # noqa: BLE001
                pass
        await _orig_connect()

    provider = OmniAgentProvider({
        "cwd": cwd,
        "model": model,
        "agent_class": _DefaultChatAgent,
        "agent_bus": bus,
    })
    _orig_connect = provider.connect
    provider.connect = _connect_with_bus.__get__(provider)  # type: ignore[method-assign]
    return provider


def _parse_input(raw: str) -> tuple[str, str | None, str | None, dict[str, Any] | None]:
    """解析 stdin。返回 (prompt, model, cwd, options)。

    优先 JSON 对象; 不是合法 JSON 或不是对象 → 整段当 prompt。
    """
    raw = raw.strip()
    if not raw:
        return "", None, None, None
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw, None, None, None
    if not isinstance(obj, dict):
        return raw, None, None, None
    prompt = str(obj.get("prompt") or obj.get("input") or "")
    model = obj.get("model")
    cwd = obj.get("cwd")
    options = obj.get("options") if isinstance(obj.get("options"), dict) else None
    return prompt, model, cwd, options


async def _main_async(raw_input: str, out: Any) -> int:
    prompt, model, cwd, options = _parse_input(raw_input)
    if not prompt:
        _emit({"kind": "error", "error": "empty prompt (stdin had no prompt)"}, out)
        return 2

    provider = _build_default_provider(model, cwd)

    # abort: SIGINT/SIGTERM → provider.interrupt()。Windows 上 add_signal_handler 抛
    # NotImplementedError, 走 KeyboardInterrupt 兜底。
    loop = asyncio.get_running_loop()
    try:
        import signal

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    sig, lambda: asyncio.create_task(provider.interrupt())
                )
            except (NotImplementedError, AttributeError, ValueError):
                pass
    except Exception:  # noqa: BLE001
        pass

    try:
        return await pump(provider, prompt, options, out, cwd=cwd)
    except KeyboardInterrupt:
        try:
            await provider.interrupt()
        except Exception:  # noqa: BLE001
            pass
        _emit({"kind": "complete", "aborted": True}, out)
        return 130


def _force_utf8_stdout() -> None:
    """把 stdout 锁成 UTF-8 —— Windows 默认 cp936/GBK 会把中文 NDJSON 写坏。
    CCUI 子进程管道按 UTF-8 读, 这里必须对齐。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001  (老 Python / 已包装的流)
        pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    # 关键: 读 stdin 的**字节**再按 UTF-8 解 —— 不能用 sys.stdin.read()(走平台 locale
    # 编码, Windows 下把 UTF-8 中文误解成游离代理对, 后续发给 LLM 时 encode 崩 'surrogates
    # not allowed')。CCUI 按 UTF-8 喂 JSON, 这里对齐。
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        raw = sys.stdin.read()
    try:
        return asyncio.run(_main_async(raw, sys.stdout))
    except Exception as e:  # noqa: BLE001
        logger.exception("omni_agent_cli fatal")
        try:
            sys.stdout.write(
                json.dumps({"kind": "error", "error": f"{type(e).__name__}: {e}"}) + "\n"
            )
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
