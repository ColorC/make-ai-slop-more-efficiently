# [OMNI] origin=claude-code domain=services/agent ts=2026-05-04 type=helper
# [OMNI] material_id="material:core.agent.slash_commands.registry_and_builtins.py"
"""SlashCommandRegistry — 用户 "/cmd args" 输入注册 + 派发.

CC 对齐 (build-src/src/commands/...): /compact /clear /cost /help /status /model 等
都是 "interaction shortcut" — 用户输 / 开头, agent 不调 LLM, 直接执行 builtin.

我们的等价 — dashboard 用户在 chat 框输 "/cost" 走这里查当前 trace 成本, 不浪费 LLM 调用.

设计:
- SlashCommandRegistry: name → handler(arg_string, context) -> reply_string
- 内置 6 个: /help /clear /cost /status /traces /compact-now
- 业务可 register 自定义
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


# Handler 签名: (arg_string: str, context: dict) -> reply_string
SlashHandler = Callable[[str, dict], str]


@dataclass
class _SlashEntry:
    name: str
    handler: SlashHandler
    description: str
    usage: str = ""


class SlashCommandRegistry:
    """进程级单例 / 命令注册表."""

    _entries: dict[str, _SlashEntry] = {}

    @classmethod
    def register(
        cls,
        name: str,
        handler: SlashHandler,
        *,
        description: str = "",
        usage: str = "",
    ) -> None:
        if not name.startswith("/"):
            name = "/" + name
        cls._entries[name] = _SlashEntry(name=name, handler=handler, description=description, usage=usage)

    @classmethod
    def list_commands(cls) -> list[tuple[str, str, str]]:
        return [(e.name, e.description, e.usage) for e in cls._entries.values()]

    @classmethod
    def dispatch(cls, raw_input: str, context: dict | None = None) -> str | None:
        """如果 raw_input 是 / 命令, 派发到 handler 返结果. 不是则 None.

        raw_input 例: '/cost' 或 '/traces migration_'
        """
        text = raw_input.strip()
        if not text.startswith("/"):
            return None
        # 分 cmd + arg
        parts = text.split(None, 1)
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else ""
        entry = cls._entries.get(cmd)
        if entry is None:
            return f"Unknown slash command: {cmd}\nAvailable: {sorted(cls._entries.keys())}"
        try:
            return entry.handler(arg, context or {})
        except Exception as e:
            return f"[{cmd} error] {type(e).__name__}: {e}"

    @classmethod
    def clear(cls) -> None:
        cls._entries.clear()


# ─── 内置命令 ───────────────────────────────────────────────────────────


def _builtin_help(_arg: str, _ctx: dict) -> str:
    cmds = SlashCommandRegistry.list_commands()
    if not cmds:
        return "No commands registered."
    lines = ["Available slash commands:"]
    for name, desc, usage in sorted(cmds):
        line = f"  {name}"
        if usage:
            line += f" {usage}"
        if desc:
            line += f"  — {desc}"
        lines.append(line)
    return "\n".join(lines)


def _builtin_cost(_arg: str, _ctx: dict) -> str:
    """显示 LLMMeter 全局 / 指定 caller 汇总. 跟 CC /cost 对齐."""
    from omnicompany.runtime.llm.llm import LLMMeter
    meter = LLMMeter.get_instance()
    s = meter.summary()
    return (
        f"Session LLM usage:\n"
        f"  calls: {s['call_count']}\n"
        f"  input tokens: {s['total_input_tokens']:,}\n"
        f"  output tokens: {s['total_output_tokens']:,}\n"
        f"  cache_read tokens: {s.get('total_cache_read_tokens', 0):,}\n"
        f"  cache_creation tokens: {s.get('total_cache_creation_tokens', 0):,}\n"
        f"  cache hit rate: {s.get('cache_hit_rate', 0):.1%}\n"
        f"  est. cost: ${s['total_cost_usd']:.4f}\n"
        f"  avg latency: {s['avg_latency_ms']:.0f}ms"
    )


def _builtin_traces(arg: str, _ctx: dict) -> str:
    """列已知 traces. arg 是可选 prefix."""
    from omnicompany.packages.services._core.agent.session_history import list_traces
    traces = list_traces(prefix=arg, limit=20)
    if not traces:
        return f"No traces found (prefix={arg!r})." if arg else "No traces in DB."
    lines = [f"Traces ({len(traces)} shown):"]
    for t in traces:
        lines.append(f"  {t['trace_id']}: {t['event_count']} events · last={t['last_ts'][:19]}")
    return "\n".join(lines)


def _builtin_status(_arg: str, ctx: dict) -> str:
    """显当前 session 状态 — trace_id / cwd / active plan 等. ctx 可注入."""
    parts = ["Session status:"]
    parts.append(f"  trace_id: {ctx.get('trace_id') or '(none)'}")
    parts.append(f"  cwd: {ctx.get('cwd') or '(none)'}")
    parts.append(f"  active_plan: {ctx.get('active_plan') or '(none)'}")
    parts.append(f"  agent: {ctx.get('agent') or '(none)'}")
    return "\n".join(parts)


def _builtin_clear(_arg: str, _ctx: dict) -> str:
    """提示 — 真清屏由 UI 层做, 这只返指令."""
    return "[CLEAR] (UI layer should clear chat display now. messages 内存层不动 — 用 /compact-now 真压缩.)"


def _builtin_compact_now(_arg: str, _ctx: dict) -> str:
    """触发 L4 compact (需 ContextCompactRouter 引用 — 由 UI 注入到 ctx)."""
    return "[COMPACT-NOW] (UI layer should call ContextCompactRouter.run with force_l4=True now. ContextCompactRouter L4 仅在 token threshold 时触发, manual force 走 UI 路径.)"


# 注册内置
def register_builtins() -> None:
    """主程序启动时调一次. 重复调幂等."""
    SlashCommandRegistry.register("/help", _builtin_help, description="List all slash commands")
    SlashCommandRegistry.register("/cost", _builtin_cost, description="Show LLM usage + cost summary")
    SlashCommandRegistry.register("/traces", _builtin_traces, description="List recent traces", usage="[prefix]")
    SlashCommandRegistry.register("/status", _builtin_status, description="Show current session info")
    SlashCommandRegistry.register("/clear", _builtin_clear, description="Clear chat display (UI hint)")
    SlashCommandRegistry.register("/compact-now", _builtin_compact_now, description="Force L4 compact (UI hint)")


# 模块加载即注册内置
register_builtins()


__all__ = ["SlashCommandRegistry", "register_builtins"]
