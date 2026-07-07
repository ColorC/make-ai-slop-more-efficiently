# [OMNI] origin=claude-code ts=2026-06-21 type=cli
# [OMNI] material_id="material:cli.commands.agents.py"
"""omni agents — 机器级 agent 注册表: 列/自查/自更新身份(身份/位置/在做啥)。"""
from __future__ import annotations

import json

import click

from .._access import any_caller


@click.group("agents")
def cmd_agents() -> None:
    """机器级 agent 注册表(身份 / 位置 / 在做啥)。"""


@cmd_agents.command("list")
@click.option("--running", is_flag=True, help="只列在跑的")
@click.option("--limit", default=40, help="最多列几条")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_agents_list(running: bool, limit: int, as_json: bool) -> None:
    """列本机所有(或在跑的)对话的统一身份。"""
    from omnicompany.dashboard.boss_sight.services.agent_registry import list_records

    recs = list_records(running_only=running, limit=limit)
    if as_json:
        click.echo(json.dumps(recs, ensure_ascii=False, indent=2))
        return
    if not recs:
        click.echo("(无)")
        return
    for r in recs:
        dot = "●" if r.get("running") else "○"
        click.echo(click.style(f"{dot} {r.get('identity')}", bold=True))
        click.echo(f"    位置 {r.get('location')}  ·  {r.get('provider')}:{(r.get('session_id') or '')[:12]}  ·  pane {r.get('pty_id') or '-'}")
        click.echo(f"    在做 {r.get('current_task')}")


@cmd_agents.command("refresh")
@any_caller
def cmd_agents_refresh() -> None:
    """重建注册表(确定性派生, 不烧 token)。"""
    from omnicompany.dashboard.boss_sight.services.agent_registry import rebuild

    recs = rebuild()
    running = sum(1 for r in recs if r.get("running"))
    click.echo(f"已重建 {len(recs)} 条(在跑 {running})")


@cmd_agents.command("whoami")
@click.option("--session", "session_id", required=True, help="本对话的 session_id")
@click.option("--provider", default=None, help="claude_code / codex")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_agents_whoami(session_id: str, provider: str | None, as_json: bool) -> None:
    """自查身份: agent 用自己的 session_id 查它在注册表里是谁。"""
    from omnicompany.dashboard.boss_sight.services.agent_registry import find_record

    rec = find_record(session_id, provider)
    if not rec:
        raise click.ClickException("注册表里没找到该 session(可能还没被扫到, 先 omni agents refresh)")
    click.echo(json.dumps(rec, ensure_ascii=False, indent=2) if as_json else rec.get("identity"))


@cmd_agents.command("update")
@click.option("--key", required=True, help="provider:session_id")
@click.option("--name", default=None)
@click.option("--project", default=None)
@click.option("--role", default=None)
@click.option("--location", default=None)
@click.option("--current", "current_task", default=None, help="当前在做(主)")
@click.option("--initial", "initial_task", default=None, help="最初要做(次)")
@any_caller
def cmd_agents_update(key: str, name: str | None, project: str | None, role: str | None,
                      location: str | None, current_task: str | None, initial_task: str | None) -> None:
    """自更新身份: 改某记录的字段并钉住(之后 rebuild 不覆盖这些段)。"""
    from omnicompany.dashboard.boss_sight.services.agent_registry import update_record

    fields = {k: v for k, v in {
        "name": name, "project": project, "role": role, "location": location,
        "current_task": current_task, "initial_task": initial_task,
    }.items() if v is not None}
    if not fields:
        raise click.ClickException("没给要改的字段")
    try:
        rec = update_record(key, fields)
    except KeyError:
        raise click.ClickException(f"注册表里没有 key={key}(先 omni agents list 看 key)") from None
    click.echo(json.dumps(rec, ensure_ascii=False, indent=2))


@cmd_agents.command("tail")
@click.option("--key", required=True, help="provider:session_id")
@click.option("--n", default=6, help="最近几条消息")
@any_caller
def cmd_agents_tail(key: str, n: int) -> None:
    """某对话最近 n 条消息(悬浮预览用, 直接输出文本)。"""
    from omnicompany.dashboard.boss_sight.services.agent_registry import recent_content

    click.echo(recent_content(key, n=n) or "(暂无内容)")


__all__ = ["cmd_agents"]
