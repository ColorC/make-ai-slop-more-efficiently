# [OMNI] origin=claude domain=cli/commands ts=2026-09-04 type=cli status=active
# [OMNI] summary="omni inbox —— 收件箱: preview 看候选与订阅判定, sync 落箱, subs 看矩阵, defer/dismiss/done 三个动作。消息就是未提升的任务, 所以动作全落在 task 信封上。"
# [OMNI] why="用户 2026-09-03: 专注就完成全部工作的地方; 命令行同权, 不做只有界面能干的事。"
# [OMNI] material_id="material:cli.commands.inbox"
"""omni inbox —— 个人消息总线的命令行面。

口径见 docs/plans/_cross/[2026-09-03]FOCUS-INBOX-PERSONAL-MESSAGE-BUS/spec-inbox-as-tasks.md：
消息不另立实体，所以这里**没有第二套状态机** —— 三个动作全都落在 task-core 信封上：

    延后 → task update --start-at      （在此之前不进视野，Things 3 的 defer 语义）
    忽略 → status cancelled + archive  （归档不是删除，GitHub 的 Done 口径）
    已处理 → status completed

界面能干的这里都能干；这里没有的界面也不该有。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

import click

from .tbus import _envelope_error, _resolve_dir, _run_cli


def _run(args: list[str]) -> dict[str, Any]:
    code, payload, stderr = _run_cli(_resolve_dir(None), args)
    failure = _envelope_error(payload)
    if failure or code != 0 or not payload:
        message = failure[1] if failure else ((stderr or "").strip() or f"exit={code}")
        raise click.ClickException(message)
    return payload


def _task(task_id: str) -> dict[str, Any]:
    payload = _run(["task", "show", "--id", task_id, "--json"])
    task = (payload.get("result") or {}).get("task") or {}
    if not task:
        raise click.ClickException(f"没有这条任务: {task_id}")
    return task


def parse_until(value: str, now: datetime | None = None) -> str:
    """`+3d` / `+2h` / `tomorrow` / ISO 都认。延后的语义是「在此之前别烦我」。"""
    now = now or datetime.now()
    text = (value or "").strip()
    if not text:
        raise click.ClickException("要延后到什么时候")
    rel = re.fullmatch(r"\+(\d+)([hdw])", text)
    if rel:
        n = int(rel.group(1))
        unit = rel.group(2)
        delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
        return (now + delta).isoformat()
    if text in ("tomorrow", "明天"):
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    if text in ("nextweek", "下周"):
        return (now + timedelta(weeks=1)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise click.ClickException(f"看不懂的时间: {text}（认 +3d / +2h / 明天 / 下周 / ISO）") from exc


@click.group("inbox")
def cmd_inbox() -> None:
    """收件箱 —— 多来源消息按订阅矩阵进箱; 消息就是还没被提升的任务。"""


@cmd_inbox.command("subs")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_subs(as_json: bool) -> None:
    """看订阅矩阵: 哪个来源的哪类事件允许进箱。"""
    from omnicompany.packages.services._focus import inbox as backend

    subs = backend.profile().get("subscriptions") or {}
    if as_json:
        click.echo(json.dumps(subs, ensure_ascii=False, indent=2))
        return
    default = subs.get("default") or {}
    click.echo(f"默认: {default.get('surface', 'log')}（没写规则的来源一律按这个，从严）")
    for rule in subs.get("rules") or []:
        prio = rule.get("priority")
        click.echo(
            f"  {rule.get('source')}/{rule.get('kind')} → {rule.get('surface')}"
            + (f"  定级 {prio}" if prio is not None else "")
            + (f"    // {rule.get('why')}" if rule.get("why") else "")
        )


@cmd_inbox.command("preview")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_preview(as_json: bool) -> None:
    """只跑适配器与订阅判定, 不写总线 —— 调订阅矩阵时先看结果再落地。"""
    from omnicompany.packages.services._focus import inbox as backend

    candidates = backend.collect()
    if as_json:
        click.echo(json.dumps(candidates, ensure_ascii=False, indent=2))
        return
    if not candidates:
        click.echo("（没有候选消息）")
        return
    for cand in candidates:
        click.echo(
            f"  [{cand.get('surface')}] {cand.get('source')}/{cand.get('kind')}"
            f"  {cand.get('title')}"
        )
    click.echo(f"合计 {len(candidates)} 条（log 的不会进箱）")


@cmd_inbox.command("triage")
@click.argument("key")
def cmd_triage(key: str) -> None:
    """分诊出箱: 把这条消息落成一条正经待办。

    这是唯一会往总线写东西的路径 —— 列表本身是实时取源的, 消息不物化
    (用户 2026-09-05:「这应该直接取源, 定时也太离谱了」)。
    """
    from omnicompany.packages.services._focus import inbox as backend

    out = backend.triage(key)
    if not out.get("ok"):
        raise click.ClickException(str(out.get("message") or "分诊失败"))
    click.echo(f"已出箱, 任务 {out.get('taskId')}")


@cmd_inbox.command("defer")
@click.argument("key")
@click.option("--until", required=True, help="+3d / +2h / 明天 / 下周 / ISO")
def cmd_defer(key: str, until: str) -> None:
    """往后拖: 在这个时间之前不进视野。到点自己回来 —— 列表是现算的, 不用谁来推。"""
    from omnicompany.packages.services._focus import inbox as backend

    at = parse_until(until)
    out = backend.act(key, "defer", until=at)
    if not out.get("ok"):
        raise click.ClickException(str(out.get("message") or "延后失败"))
    click.echo(f"已延后到 {at}")


@cmd_inbox.command("dismiss")
@click.argument("key")
def cmd_dismiss(key: str) -> None:
    """忽略: 只写处置层, 不动源。想拿回来用 `omni inbox undo`。"""
    from omnicompany.packages.services._focus import inbox as backend

    backend.act(key, "dismiss")
    click.echo("已忽略（源没动，undo 可撤销）")


@cmd_inbox.command("done")
@click.argument("key")
def cmd_done(key: str) -> None:
    """已处理。"""
    from omnicompany.packages.services._focus import inbox as backend

    backend.act(key, "done")
    click.echo("已标记处理完")


@cmd_inbox.command("undo")
@click.argument("key")
def cmd_undo(key: str) -> None:
    """撤销处置 —— 忽略错了要能拿回来。"""
    from omnicompany.packages.services._focus import inbox as backend

    out = backend.act(key, "undo")
    click.echo("已撤销" if out.get("cleared") else "本来就没有处置记录")
