# [OMNI] origin=claude-code domain=cli/commands ts=2026-04-23T00:00:00Z type=cli
# [OMNI] material_id="material:cli.commands.human_approval.inbox_manager.py"
"""omni human — HumanBus inbox / resolve 人类审批入口.

用法:
  omni human inbox                        # 列 pending 问题
  omni human inbox --status resolved      # 列已答
  omni human inbox --kind core_diagnose   # 按 kind 过滤
  omni human resolve <id> <answer>        # 回答 pending 问题
  omni human show <id>                    # 查看单条详情
  omni human expire                       # 过期超 7 天 pending
"""
from __future__ import annotations

import click

from omnicompany.runtime.buses import HumanBus, HumanKind
from omnicompany.runtime.buses.human_bus import QuestionStatus


def _format_time(ts: float | None) -> str:
    if ts is None:
        return "-"
    import datetime

    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _truncate(text: str, limit: int = 80) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


@click.group("human")
def cmd_human():
    """HumanBus — 人类审批 inbox / resolve."""


@cmd_human.command("inbox")
@click.option("--status", "-s", default="pending", help="pending | resolved | default_applied | expired | all")
@click.option("--kind", "-k", default=None, help="auto_continue | core_diagnose | human_blocking")
@click.option("--target-kind", "-t", default=None, help="l2_claude_code | colleague_feishu | core_self_repair | ...")
@click.option("--target-id", default=None, help="特定身份 id (如collab platform open_id)")
@click.option("--limit", "-n", default=50, show_default=True)
def cmd_inbox(status: str, kind: str | None, target_kind: str | None, target_id: str | None, limit: int):
    """列出 Human Bus inbox 问题."""
    bus = HumanBus()
    status_arg = None if status == "all" else QuestionStatus(status)
    kind_arg = HumanKind(kind) if kind else None
    items = bus.inbox(
        status=status_arg, kind=kind_arg,
        target_kind=target_kind, target_id=target_id, limit=limit,
    )
    if not items:
        click.echo(f"  (no items matching status={status} kind={kind or 'any'} target_kind={target_kind or 'any'})")
        return
    click.echo(
        f"\n  {len(items)} item(s) · status={status} kind={kind or 'any'} "
        f"target_kind={target_kind or 'any'}\n"
    )
    for q in items:
        kind_colored = click.style(q.kind.value.ljust(14), fg="cyan")
        status_colored = click.style(q.status.value.ljust(16), fg="yellow" if q.status == QuestionStatus.PENDING else "green")
        target_str = q.target.kind
        if q.target.id:
            target_str += f":{q.target.id}"
        target_colored = click.style(target_str.ljust(26), fg="magenta")
        click.echo(f"  [{q.id}]  {kind_colored}  {status_colored}  {target_colored}  {_format_time(q.created_at)}")
        if q.source:
            click.echo(f"    source: {q.source}")
        click.echo(f"    Q: {_truncate(q.question, 120)}")
        if q.default_answer and q.status == QuestionStatus.PENDING:
            click.echo(f"    default: {_truncate(q.default_answer, 80)}")
        if q.answer:
            click.echo(f"    A ({q.resolver}): {_truncate(q.answer, 80)}")
        click.echo()


@cmd_human.command("resolve")
@click.argument("question_id")
@click.argument("answer")
@click.option("--resolver", default="human", help="resolver 标识, 默认 human")
def cmd_resolve(question_id: str, answer: str, resolver: str):
    """回答 pending 问题."""
    bus = HumanBus()
    q = bus.get(question_id)
    if q is None:
        click.echo(click.style(f"  question {question_id} not found", fg="red"), err=True)
        raise click.Abort()
    if q.status != QuestionStatus.PENDING:
        click.echo(
            click.style(f"  question {question_id} is {q.status.value}, not pending", fg="yellow"),
            err=True,
        )
        raise click.Abort()
    resolved = bus.resolve(question_id, answer, resolver=resolver)
    click.echo(click.style(f"  resolved [{question_id}] by {resolver}", fg="green"))
    click.echo(f"  A: {resolved.answer}")


@cmd_human.command("show")
@click.argument("question_id")
def cmd_show(question_id: str):
    """查看问题详情."""
    bus = HumanBus()
    q = bus.get(question_id)
    if q is None:
        click.echo(click.style(f"  question {question_id} not found", fg="red"), err=True)
        raise click.Abort()
    import json as _json

    click.echo(_json.dumps(q.to_dict(), indent=2, ensure_ascii=False, default=str))


@cmd_human.command("expire")
@click.option("--days", default=7, show_default=True, help="超过 N 天的 pending 标 expired")
def cmd_expire(days: int):
    """过期老 pending 问题."""
    bus = HumanBus()
    count = bus.expire_old(older_than_seconds=days * 86400)
    click.echo(f"  expired {count} pending question(s) older than {days}d")
