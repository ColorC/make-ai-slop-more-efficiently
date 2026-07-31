# [OMNI] origin=claude-code ts=2026-07-03T00:00:00Z type=module summary="token_ledger 三路归并: claude+codex 会话级行 + 按天/按项目聚合视图 + 未关联桶(禁猜)+ cron字符串匹配链接; 内部调用账独立不强拼会话级; 默认 project_resolver 改为 workspace.yaml 闭集白名单" why="overnight-run.md 第六节'首轮验收打回后的硬化'㈡: 原默认 resolver(取 cwd 末段当项目名)太宽松, 生产里未关联桶恒空是归属造假; 改用闭集白名单" tags=token-ledger,aggregate,project-resolve,unlinked
"""token_ledger 聚合器 —— 把三路收集结果归并成会话级视图 + 按天/按项目聚合 + 未关联桶。

关联只做确定性(overnight-run.md 第六节口径 + 首轮打回硬化㈡):
    - cwd → 项目: 由外部注入的 project_resolver(cwd)->str|None 决定; 解析不出就是 None,
      不允许本模块自己编一个项目名顶上——一律落"未关联"桶。默认 resolver(未注入时)
      = _project_resolver.build_workspace_project_resolver() 的闭集白名单
      (只认 C:/workspace/workspace.yaml 的 entries 名单 + C:/workspace/AIWorkSpace/
      特判), 不是任意 cwd 取末段的宽松启发式。
    - cron 任务名 ↔ meter caller: 见 _cron_link.py, 仅限白名单任务名参与, 结果标非强关联。

内部调用账(internal_by_caller_day)是独立的 caller×天 聚合, 不强拼会话级字段
(没有 session_id/cwd/provider 这些概念, 硬拼会污染语义)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ._cron_link import link_cron_tasks_to_callers
from ._project_resolver import build_workspace_project_resolver
from .claude_source import ClaudeCollectResult
from .codex_source import CodexCollectResult
from .internal_source import InternalCollectResult

_UNLINKED = "未关联"


@dataclass
class SessionLedgerRow:
    session_id: str
    cwd: str
    project: str
    started: str | None
    ended: str | None
    provider: str
    by_model: dict[str, dict[str, Any]]
    calls: int
    duration_est: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "cwd": self.cwd,
            "project": self.project,
            "started": self.started,
            "ended": self.ended,
            "provider": self.provider,
            "by_model": self.by_model,
            "calls": self.calls,
            "duration_est": self.duration_est,
        }


@dataclass
class TokenLedgerAggregate:
    sessions: list[SessionLedgerRow] = field(default_factory=list)
    by_session: list[dict[str, Any]] = field(default_factory=list)
    by_day: list[dict[str, Any]] = field(default_factory=list)
    by_project: list[dict[str, Any]] = field(default_factory=list)
    internal_by_caller_day: list[dict[str, Any]] = field(default_factory=list)
    unlinked_bucket: list[dict[str, Any]] = field(default_factory=list)
    cron_links: list[dict[str, Any]] = field(default_factory=list)


def _default_project_resolver(cwd: str) -> str | None:
    """默认 resolver: 闭集白名单(读真实 C:/workspace/workspace.yaml), 不是宽松
    启发式。惰性求值(每次调用现读一次 workspace.yaml, 不在模块导入时触碰真实文件系统,
    避免测试环境仅仅 import 本模块就意外依赖真实路径存在)。"""
    return build_workspace_project_resolver()(cwd)


def _duration_est(started: str | None, ended: str | None) -> float | None:
    if not started or not ended:
        return None
    try:
        from datetime import datetime

        def _parse(s: str) -> datetime:
            s = s.replace("Z", "+00:00")
            return datetime.fromisoformat(s)

        delta = (_parse(ended) - _parse(started)).total_seconds()
        return max(0.0, delta)
    except (ValueError, TypeError):
        return None


def _day_of(ts: str | None) -> str:
    if not ts:
        return "unknown-day"
    return str(ts)[:10]


def aggregate_token_ledger(
    claude: ClaudeCollectResult,
    codex: CodexCollectResult,
    internal: InternalCollectResult,
    *,
    project_resolver: Callable[[str], str | None] | None = None,
    known_llm_cron_tasks: Iterable[str] | None = None,
    cron_task_names: Iterable[str] | None = None,
) -> TokenLedgerAggregate:
    """把三路收集结果归并成会话级视图 + 按天/按项目聚合 + 未关联桶 + cron 匹配。"""
    resolver = project_resolver if project_resolver is not None else _default_project_resolver

    sessions: list[SessionLedgerRow] = []
    for s in claude.sessions:
        sessions.append(SessionLedgerRow(
            session_id=s.session_id, cwd=s.cwd,
            project=resolver(s.cwd) or "",
            started=s.started, ended=s.ended, provider="claude",
            by_model=s.by_model, calls=s.calls,
            duration_est=_duration_est(s.started, s.ended),
        ))
    for s in codex.sessions:
        sessions.append(SessionLedgerRow(
            session_id=s.session_id, cwd=s.cwd,
            project=resolver(s.cwd) or "",
            started=s.started, ended=s.ended, provider="codex",
            by_model=s.by_model, calls=s.calls,
            duration_est=_duration_est(s.started, s.ended),
        ))

    by_session = [s.to_dict() for s in sessions]

    # ── 按天聚合 ──
    day_buckets: dict[str, dict[str, Any]] = {}
    for row in by_session:
        day = _day_of(row["started"])
        bucket = day_buckets.setdefault(day, {"day": day, "session_count": 0, "calls": 0})
        bucket["session_count"] += 1
        bucket["calls"] += row["calls"]
    by_day = list(day_buckets.values())

    # ── 按项目聚合(含未关联桶) ──
    project_buckets: dict[str, dict[str, Any]] = {}
    unlinked_bucket: list[dict[str, Any]] = []
    for row in by_session:
        project = row["project"] or _UNLINKED
        bucket = project_buckets.setdefault(project, {"project": project, "session_count": 0, "calls": 0})
        bucket["session_count"] += 1
        bucket["calls"] += row["calls"]
        if project == _UNLINKED:
            unlinked_bucket.append({
                "session_id": row["session_id"],
                "cwd": row["cwd"],
                "provider": row["provider"],
                "calls": row["calls"],
            })
    by_project = list(project_buckets.values())

    # ── 内部调用账(独立表, 不强拼会话级字段); 解析不出项目的同样进未关联桶按项目粗分组 ──
    internal_rows: list[dict[str, Any]] = []
    for row in internal.by_caller_day:
        internal_rows.append(row.to_dict())

    callers = sorted({row.caller for row in internal.by_caller_day})
    cron_links = link_cron_tasks_to_callers(
        known_llm_cron_tasks=known_llm_cron_tasks,
        cron_task_names=cron_task_names,
        callers=callers,
    )

    return TokenLedgerAggregate(
        sessions=sessions,
        by_session=by_session,
        by_day=by_day,
        by_project=by_project,
        internal_by_caller_day=internal_rows,
        unlinked_bucket=unlinked_bucket,
        cron_links=cron_links,
    )


__all__ = ["SessionLedgerRow", "TokenLedgerAggregate", "aggregate_token_ledger"]
