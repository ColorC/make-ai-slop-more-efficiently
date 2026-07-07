# [OMNI] origin=claude-code ts=2026-07-03T00:00:00Z type=package summary="token 记账 v1 包导出(三路收集器+聚合器+落盘函数)" why="overnight-run.md 第六节 token 记账验收锚要求会话侧(claude/codex)+内部调用(meter.jsonl)三路归并成会话级视图, 供 boss_sight 只读接口消费" tags=token-ledger,usage,claude,codex,meter
"""token 记账 v1 —— claude / codex 会话用量 + 内部 LLM 调用账三路归并。

权威依据: docs/plans/[2026-07-02]SEMANTIC-OS-MAP/overnight-run.md 第六节
"token 记账的验收锚"。

用法::

    from omnicompany.packages.services._core.token_ledger import (
        collect_claude_sessions, collect_codex_sessions, collect_internal_usage,
        aggregate_token_ledger, write_token_ledger,
    )

    claude = collect_claude_sessions(Path.home() / ".claude" / "projects")
    codex = collect_codex_sessions(Path.home() / ".codex" / "sessions")
    internal = collect_internal_usage(meter_path)
    agg = aggregate_token_ledger(claude, codex, internal)
    write_token_ledger(agg, out_dir=out_dir)

本包是纯确定性代码, 不含任何 LLM 调用; 不修改
src/omnicompany/runtime/llm/llm.py 热路径。全部数据源根目录 / 落盘目录均可注入,
不接受隐式硬编码到真实家目录(测试用 tmp_path fixture 隔离)。
"""

from __future__ import annotations

from .claude_source import ClaudeCollectResult, ClaudeSessionUsage, collect_claude_sessions
from .codex_source import CodexCollectResult, CodexSessionUsage, collect_codex_sessions
from .internal_source import InternalCallerDayRow, InternalCollectResult, collect_internal_usage
from .aggregate import SessionLedgerRow, TokenLedgerAggregate, aggregate_token_ledger
from .writer import write_token_ledger

__all__ = [
    "ClaudeCollectResult",
    "ClaudeSessionUsage",
    "collect_claude_sessions",
    "CodexCollectResult",
    "CodexSessionUsage",
    "collect_codex_sessions",
    "InternalCallerDayRow",
    "InternalCollectResult",
    "collect_internal_usage",
    "SessionLedgerRow",
    "TokenLedgerAggregate",
    "aggregate_token_ledger",
    "write_token_ledger",
]
