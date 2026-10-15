"""``omni workstream`` — project-scoped focus and cross-session archive."""
from __future__ import annotations

import json

import click

from .._access import any_caller


@click.group("workstream")
def cmd_workstream() -> None:
    """项目内业务线：绑定专注范围、留 checkpoint、跨会话接续。"""


@cmd_workstream.command("list")
@click.option("--project", default=None, help="只列某项目")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_list(project: str | None, as_json: bool) -> None:
    from omnicompany.core.workstreams import list_workstreams

    rows = list_workstreams(project)
    if as_json:
        click.echo(json.dumps({"workstreams": rows}, ensure_ascii=False, indent=2))
        return
    if not rows:
        click.echo("(无；可先运行 omni workstream seed)")
        return
    for row in rows:
        click.echo(f"{row['id']}  {row['name']}  · {row.get('scope') or '-'}")


@cmd_workstream.command("show")
@click.argument("workstream_id")
@any_caller
def cmd_show(workstream_id: str) -> None:
    from omnicompany.core.workstreams import get_workstream

    row = get_workstream(workstream_id)
    if row is None:
        raise click.ClickException(f"未注册 workstream: {workstream_id}")
    click.echo(json.dumps(row, ensure_ascii=False, indent=2))


@cmd_workstream.command("register")
@click.argument("workstream_id")
@click.option("--project", required=True)
@click.option("--name", required=True)
@click.option("--kind", default="product", show_default=True)
@click.option("--scope", default="")
@click.option("--keyword", "keywords", multiple=True)
@click.option("--alias", "aliases", multiple=True)
@click.option("--context-ref", "context_refs", multiple=True)
@click.option("--business-ref", default="", help="可选：引用 demogame 自己的业务真源，不复制它")
@any_caller
def cmd_register(
    workstream_id: str,
    project: str,
    name: str,
    kind: str,
    scope: str,
    keywords: tuple[str, ...],
    aliases: tuple[str, ...],
    context_refs: tuple[str, ...],
    business_ref: str,
) -> None:
    from omnicompany.core.workstreams import set_workstream

    try:
        row = set_workstream(
            workstream_id,
            project=project,
            name=name,
            kind=kind,
            scope=scope,
            keywords=keywords,
            aliases=aliases,
            context_refs=context_refs,
            business_ref=business_ref,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(json.dumps(row, ensure_ascii=False, indent=2))


@cmd_workstream.command("seed")
@any_caller
def cmd_seed() -> None:
    """补入内置的 Walker、Dashboard 与 demogame 初始业务线。"""
    from omnicompany.core.workstreams import seed_defaults

    try:
        rows = seed_defaults()
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(f"OK 新增 {len(rows)} 条 workstream")


@cmd_workstream.command("bind")
@click.argument("workstream_id")
@click.option("--trace-id", default=None, help="默认当前会话")
@any_caller
def cmd_bind(workstream_id: str, trace_id: str | None) -> None:
    from omnicompany.core.workstreams import get_workstream
    from omnicompany.packages.services._core.identity import (
        get_session_binding,
        resolve_active_trace_id,
        update_session_binding,
    )

    row = get_workstream(workstream_id)
    if row is None:
        raise click.ClickException(f"未注册 workstream: {workstream_id}")
    trace_id = trace_id or resolve_active_trace_id()
    existing = get_session_binding(trace_id)
    bound_project = str(existing.get("project") or "")
    if bound_project and bound_project != row["project"]:
        raise click.ClickException(
            f"当前会话已绑定 project={bound_project}，不能绑定 {row['id']}；先明确切换项目"
        )
    update_session_binding(
        trace_id,
        project=row["project"],
        workstream=row["id"],
        source="cli_bind",
    )
    click.echo(f"OK {trace_id} → {row['id']}")


@cmd_workstream.command("checkpoint")
@click.argument("summary")
@click.option("--workstream", "workstream_id", default=None, help="默认取当前会话绑定")
@click.option("--kind", type=click.Choice(["checkpoint", "decision", "handoff", "fact"]), default="checkpoint")
@click.option("--ref", "refs", multiple=True, help="文件、决策、任务或材料引用；可重复")
@any_caller
def cmd_checkpoint(summary: str, workstream_id: str | None, kind: str, refs: tuple[str, ...]) -> None:
    from omnicompany.core.workstreams import add_checkpoint
    from omnicompany.packages.services._core.identity import current_session_meta

    source = current_session_meta()
    workstream_id = workstream_id or source.get("workstream")
    if not workstream_id:
        raise click.ClickException("当前会话未绑定 workstream；先运行 omni workstream bind <id>")
    try:
        row = add_checkpoint(workstream_id, summary, kind=kind, refs=refs, source=source)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(json.dumps(row, ensure_ascii=False, indent=2))


@cmd_workstream.command("context")
@click.argument("workstream_id", required=False)
@click.option("--limit", default=8, show_default=True)
@any_caller
def cmd_context(workstream_id: str | None, limit: int) -> None:
    from omnicompany.core.workstreams import compact_context
    from omnicompany.packages.services._core.identity import current_session_meta

    workstream_id = workstream_id or current_session_meta().get("workstream")
    if not workstream_id:
        raise click.ClickException("未指定且当前会话未绑定 workstream")
    try:
        click.echo(compact_context(workstream_id, limit=limit))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None


__all__ = ["cmd_workstream"]
