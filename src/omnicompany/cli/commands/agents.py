# [OMNI] origin=claude-code ts=2026-06-21 type=cli
# [OMNI] material_id="material:cli.commands.agents.py"
"""omni agents — 机器级 agent 注册表: 列/自查/自更新身份(身份/位置/在做啥)。"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import click

from .._access import any_caller


@click.group("agents")
def cmd_agents() -> None:
    """机器级 agent 注册表(身份 / 位置 / 在做啥)。"""


@cmd_agents.command("list")
@click.option("--running", is_flag=True, help="只列进程仍存活的；不等同于 provider 正在工作")
@click.option("--working", is_flag=True, help="只列有 provider 证据确认正在工作的")
@click.option("--limit", default=40, help="最多列几条")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_agents_list(running: bool, working: bool, limit: int, as_json: bool) -> None:
    """列本机所有(或在跑的)对话的统一身份。"""
    from omnicompany.packages.services.boss_sight.services.agent_registry import (
        list_records,
    )

    # An explicit list is an observation request, so synchronously project the
    # current daemon state.  The old cached registry made dead PTYs linger and
    # made ``--running`` look like ``working``.
    recs = list_records(running_only=running, limit=None, rebuild_first=True)
    if working:
        recs = [record for record in recs if record.get("working") is True]
    recs = recs[:limit] if limit else recs
    if as_json:
        click.echo(json.dumps(recs, ensure_ascii=False, indent=2))
        return
    if not recs:
        click.echo("(无)")
        return
    for r in recs:
        dot = "●" if r.get("running") else "○"
        click.echo(click.style(f"{dot} {r.get('identity')}", bold=True))
        state = r.get("state") or r.get("run_status") or "unknown"
        click.echo(f"    状态 {state}  ·  位置 {r.get('location')}  ·  {r.get('provider')}:{(r.get('session_id') or '')[:12]}  ·  pane {r.get('pty_id') or '-'}")
        click.echo(f"    在做 {r.get('current_task')}")


@cmd_agents.command("refresh")
@any_caller
def cmd_agents_refresh() -> None:
    """重建注册表(确定性派生, 不烧 token)。"""
    from omnicompany.packages.services.boss_sight.services.agent_registry import rebuild

    recs = rebuild()
    running = sum(1 for r in recs if r.get("running"))
    click.echo(f"已重建 {len(recs)} 条(在跑 {running})")


@cmd_agents.command("whoami")
@click.option("--session", "session_id", required=True, help="本对话的 session_id")
@click.option("--provider", default=None, help="claude_code / claude_2 / codex / codex_2")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_agents_whoami(session_id: str, provider: str | None, as_json: bool) -> None:
    """自查身份: agent 用自己的 session_id 查它在注册表里是谁。"""
    from omnicompany.packages.services.boss_sight.services.agent_registry import find_record

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
    from omnicompany.packages.services.boss_sight.services.agent_registry import update_record

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
    from omnicompany.packages.services.boss_sight.services.agent_registry import recent_content

    click.echo(recent_content(key, n=n) or "(暂无内容)")


@cmd_agents.command("send")
@click.argument("target")
@click.option("-m", "--message", required=True, help="要交给目标会话的内容")
@click.option("--from-topic", default="", help="当前台账没有 topic 时补充本对话的内容身份")
@click.option(
    "--intent",
    type=click.Choice(["auto", "action", "decision", "blocker", "boundary"]),
    default="auto",
    show_default=True,
    help="说明该消息为何会改变接收方的后续工作。",
)
@any_caller
def cmd_agents_send(target: str, message: str, from_topic: str, intent: str) -> None:
    """向运行中的会话发送带来源身份和精确回报命令的消息。"""
    from omnicompany.core.session_messages import send_message

    try:
        row = send_message(target, message, from_topic=from_topic, intent=intent)
    except (ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(json.dumps(row, ensure_ascii=False, indent=2))


@cmd_agents.command("reply")
@click.argument("message_id")
@click.option("-m", "--message", required=True, help="结果或阻塞说明")
@click.option("--from-topic", default="", help="必要时补充当前对话内容身份")
@any_caller
def cmd_agents_reply(message_id: str, message: str, from_topic: str) -> None:
    """按消息 id 回报给原会话，不需要人工查找窗口。"""
    from omnicompany.core.session_messages import reply_message

    try:
        row = reply_message(message_id, message, from_topic=from_topic)
    except (ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(json.dumps(row, ensure_ascii=False, indent=2))


def _daemon_request(path: str, *, payload: dict | None = None, method: str = "POST") -> dict:
    from omnicompany.dashboard.ccdaemon.api import lifecycle

    status = lifecycle.read_status()
    if not (getattr(status, "alive", False) and getattr(status, "port", None)):
        raise click.ClickException("ccdaemon 未运行")
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{status.port}{path}",
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310 - loopback daemon
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise click.ClickException(f"ccdaemon {exc.code}: {exc.reason}") from None
    except OSError as exc:
        raise click.ClickException(f"ccdaemon 请求失败: {exc}") from None
    return json.loads(raw.decode("utf-8")) if raw else {"ok": True}


@cmd_agents.command("start")
@click.option(
    "--provider",
    type=click.Choice(["codex", "claude_code", "claude_2", "kiro", "qoder", "kimi", "opencode", "codebuddy"]),
    default="codex",
    show_default=True,
)
@click.option("--cwd", default=None, help="默认当前目录")
@click.option("--project", required=True, help="注册表中的项目 id")
@click.option("--workstream", default=None, help="项目内业务线 id")
@click.option("--topic", required=True, help="新主会话唯一职责")
@any_caller
def cmd_agents_start(provider: str, cwd: str | None, project: str, workstream: str | None, topic: str) -> None:
    """兼容入口：委托 ``omni agent start`` 的统一 CLI Worker 启动服务。"""
    from omnicompany.core.projects_registry import list_projects
    from omnicompany.core.workstreams import get_workstream
    if project not in {str(row.get("id")) for row in list_projects()}:
        raise click.ClickException(f"未注册项目: {project}")
    if workstream:
        row = get_workstream(workstream)
        if row is None or row.get("project") != project:
            raise click.ClickException("workstream 不存在或不属于指定 project")
    from omnicompany.packages.services._core.agent.control_plane import (
        AgentLaunchRequest,
        launch_agent_worker,
    )

    try:
        handle = launch_agent_worker(
            AgentLaunchRequest(
                provider=provider,
                cwd=cwd or os.getcwd(),
                project=project,
                workstream=workstream,
                topic=topic,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(json.dumps({**handle.to_dict(), "topic": topic, "compat_entry": "omni agents start"}, ensure_ascii=False, indent=2))


@cmd_agents.command("close")
@click.argument("target")
@click.option("--user-authorized", is_flag=True, help="确认用户已授权关闭这个非活跃会话")
@any_caller
def cmd_agents_close(target: str, user_authorized: bool) -> None:
    """关闭 done/idle 会话；必须显式声明用户授权，拒绝 working/waiting。"""
    from omnicompany.core.session_messages import resolve_target

    if not user_authorized:
        raise click.ClickException("缺少用户授权；确认后加 --user-authorized")
    try:
        row = resolve_target(target)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    state = str(row.get("run_status") or "")
    if state in {"working", "waiting"}:
        raise click.ClickException(f"拒绝关闭活跃会话(state={state})")
    pty_id = str(row.get("pty_id") or "")
    if not pty_id:
        raise click.ClickException("目标没有可关闭的 PTY")
    result = _daemon_request(f"/cc/sessions/{pty_id}", method="DELETE")
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


__all__ = ["cmd_agents"]
