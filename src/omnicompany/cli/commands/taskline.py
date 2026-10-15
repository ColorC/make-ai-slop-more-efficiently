# [OMNI] origin=claude-code domain=cli/commands ts=2026-08-21T00:00:00+08:00 type=cli status=active
# [OMNI] summary="omni taskline — 任务线一等实体的 CLI: 建/列/看/绑/解绑/状态/fork/split, 以及按 project|plan|todo 反查关联任务线。"
# [OMNI] why="用户 2026-08-21: 任务线可以绑一个项目多个 plan 多个 todo, 可以用 cli 查阅任务线下内容, 查 todo/plan/项目时也要显示关联任务线, 可 fork, 可拆分。"
# [OMNI] material_id="material:cli.commands.taskline"
"""omni taskline — 任务线。

任务线 = 承载一件事全过程的标识，横切在 plan-groups 之上：一条线可跨组、跨计划。
绑定：一个 project、多个 plan、多个 todo，外加摘要 / 文档路径 / 工作路径 / 历史会话文件。
"""
from __future__ import annotations

import json
from typing import Any

import click

STATUS_LABEL = {
    "active": "进行中",
    "paused": "暂停",
    "done": "完成",
    "dropped": "抛弃",
    "archived": "归档",
}


def _store():
    from omnicompany.packages.services._core.lifecycle import taskline_store

    return taskline_store


def _resolve(query: str) -> str:
    """允许用 id 前缀或标题片段指认一条线。"""
    ts = _store()
    rows = ts.list_all(include_archived=True)
    exact = [r for r in rows if r["id"] == query]
    if exact:
        return exact[0]["id"]
    hits = [r for r in rows if query in r["id"] or query in (r.get("title") or "")]
    if not hits:
        raise click.ClickException(f"没有匹配的任务线: {query!r}")
    if len(hits) > 1:
        listing = "\n".join(f"  {r['id']}  {r.get('title')}" for r in hits[:8])
        raise click.ClickException(f"{query!r} 匹配到多条, 请指明:\n{listing}")
    return hits[0]["id"]


def _echo_line(rec: dict[str, Any], indent: str = "") -> None:
    status = STATUS_LABEL.get(str(rec.get("status")), str(rec.get("status")))
    lineage = ""
    if rec.get("forked_from"):
        lineage = f"  ← fork 自 {rec['forked_from']}"
    elif rec.get("split_from"):
        lineage = f"  ← 拆自 {rec['split_from']}"
    click.echo(f"{indent}{rec.get('title')}  [{status}]{lineage}")
    counts = (
        f"plan {len(rec.get('plans') or [])} · "
        f"todo {len(rec.get('todos') or [])} · "
        f"项目 {rec.get('project') or '-'}"
    )
    click.echo(f"{indent}  {rec['id']}   {counts}")


@click.group("taskline")
def cmd_taskline() -> None:
    """任务线 — 一件事的全过程标识 (绑一个项目 / 多个 plan / 多个 todo)。

    子命令:
      new      建一条
      list     列全部 (--archived 含归档)
      show     看一条全字段 (含关联 plan/todo)
      bind     绑项目 / plan / todo / 路径 / 摘要
      unbind   解绑
      status   改状态 (active/paused/done/dropped/archived)
      resume   把暂停的重新唤起
      fork     继承上下文后独立进行 (留血缘)
      split    拆成两条 (指定 todo/plan 移到新线)
      of       按 project|plan|todo 反查关联任务线
    """


@cmd_taskline.command("new")
@click.argument("title")
@click.option("--project", default=None, help="绑定的项目 id")
@click.option("--summary", default="", help="摘要说明 (新建会话时会载入)")
@click.option("--json", "as_json", is_flag=True)
def cmd_new(title: str, project: str | None, summary: str, as_json: bool) -> None:
    """建一条任务线。"""
    rec = _store().create(title, project=project, summary=summary)
    if as_json:
        click.echo(json.dumps(rec, ensure_ascii=False, indent=2))
        return
    click.echo("建好了:")
    _echo_line(rec, "  ")


@cmd_taskline.command("list")
@click.option("--archived", is_flag=True, help="含已归档")
@click.option("--project", default=None, help="只看某项目下的")
@click.option("--json", "as_json", is_flag=True)
def cmd_list(archived: bool, project: str | None, as_json: bool) -> None:
    """列任务线。"""
    rows = _store().list_all(include_archived=archived)
    if project:
        rows = [r for r in rows if str(r.get("project") or "") == project]
    if as_json:
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        click.echo("（还没有任务线）")
        return
    click.echo(f"{len(rows)} 条任务线")
    for r in rows:
        _echo_line(r, "  ")


@cmd_taskline.command("show")
@click.argument("query")
@click.option("--json", "as_json", is_flag=True)
def cmd_show(query: str, as_json: bool) -> None:
    """看一条任务线的全部内容。"""
    ts = _store()
    rec = ts.load(_resolve(query))
    assert rec is not None
    if as_json:
        click.echo(json.dumps(rec, ensure_ascii=False, indent=2))
        return
    _echo_line(rec)
    if rec.get("summary"):
        click.echo(f"  摘要: {rec['summary']}")
    for label, key in (("plan", "plans"), ("todo", "todos"),
                       ("文档路径", "doc_paths"), ("工作路径", "work_paths"),
                       ("历史会话文件", "session_files")):
        items = rec.get(key) or []
        if items:
            click.echo(f"  {label} ({len(items)}):")
            for it in items:
                click.echo(f"    - {it}")


def _split_multi(values: tuple[str, ...]) -> list[str]:
    return [v for v in values if v]


@cmd_taskline.command("bind")
@click.argument("query")
@click.option("--project", default=None)
@click.option("--summary", default=None)
@click.option("--plan", "plans", multiple=True, help="可重复")
@click.option("--todo", "todos", multiple=True, help="可重复")
@click.option("--doc", "doc_paths", multiple=True, help="文档路径, 可重复")
@click.option("--work", "work_paths", multiple=True, help="工作路径, 可重复")
@click.option("--session-file", "session_files", multiple=True, help="历史会话文件路径, 可重复")
def cmd_bind(query: str, project: str | None, summary: str | None, plans: tuple[str, ...],
             todos: tuple[str, ...], doc_paths: tuple[str, ...], work_paths: tuple[str, ...],
             session_files: tuple[str, ...]) -> None:
    """给任务线绑东西 (列表字段去重追加)。"""
    rec = _store().bind(
        _resolve(query),
        project=project, summary=summary,
        plans=_split_multi(plans), todos=_split_multi(todos),
        doc_paths=_split_multi(doc_paths), work_paths=_split_multi(work_paths),
        session_files=_split_multi(session_files),
    )
    click.echo("已绑:")
    _echo_line(rec, "  ")


@cmd_taskline.command("unbind")
@click.argument("query")
@click.option("--project", is_flag=True, help="清掉项目绑定")
@click.option("--plan", "plans", multiple=True)
@click.option("--todo", "todos", multiple=True)
@click.option("--doc", "doc_paths", multiple=True)
@click.option("--work", "work_paths", multiple=True)
@click.option("--session-file", "session_files", multiple=True)
def cmd_unbind(query: str, project: bool, plans: tuple[str, ...], todos: tuple[str, ...],
               doc_paths: tuple[str, ...], work_paths: tuple[str, ...],
               session_files: tuple[str, ...]) -> None:
    """解绑。"""
    rec = _store().unbind(
        _resolve(query),
        project="clear" if project else None,
        plans=_split_multi(plans), todos=_split_multi(todos),
        doc_paths=_split_multi(doc_paths), work_paths=_split_multi(work_paths),
        session_files=_split_multi(session_files),
    )
    click.echo("已解绑:")
    _echo_line(rec, "  ")


@cmd_taskline.command("status")
@click.argument("query")
@click.argument("status", type=click.Choice(["active", "paused", "done", "dropped", "archived"]))
def cmd_status(query: str, status: str) -> None:
    """改任务线状态。归档 != 抛弃: 抛弃是不做了, 归档是不在视野里。"""
    rec = _store().set_status(_resolve(query), status)
    click.echo(f"{rec.get('title')} → {STATUS_LABEL[status]}")


@cmd_taskline.command("resume")
@click.argument("query")
def cmd_resume(query: str) -> None:
    """把暂停的任务线重新唤起。"""
    ts = _store()
    tid = _resolve(query)
    before = ts.load(tid) or {}
    rec = ts.set_status(tid, "active")
    was = STATUS_LABEL.get(str(before.get("status")), "?")
    click.echo(f"{rec.get('title')}: {was} → 进行中")


@cmd_taskline.command("fork")
@click.argument("query")
@click.option("--title", default=None, help="新线标题 (默认 原标题（fork）)")
def cmd_fork(query: str, title: str | None) -> None:
    """继承相同上下文, 后续独立进行 —— 不再维护同一份任务线。"""
    rec = _store().fork(_resolve(query), title=title)
    click.echo("已 fork:")
    _echo_line(rec, "  ")


@cmd_taskline.command("split")
@click.argument("query")
@click.argument("title")
@click.option("--todo", "todos", multiple=True, help="移到新线的 todo, 可重复")
@click.option("--plan", "plans", multiple=True, help="移到新线的 plan, 可重复")
def cmd_split(query: str, title: str, todos: tuple[str, ...], plans: tuple[str, ...]) -> None:
    """把一条任务线拆成两条 (指定条目移到新线, 原线保留其余)。"""
    if not todos and not plans:
        raise click.ClickException("拆分要指明搬走什么: 至少给一个 --todo 或 --plan")
    new, src = _store().split(_resolve(query), title, todos=list(todos), plans=list(plans))
    click.echo("新线:")
    _echo_line(new, "  ")
    click.echo("原线:")
    _echo_line(src, "  ")


@cmd_taskline.command("of")
@click.argument("kind", type=click.Choice(["project", "plan", "todo"]))
@click.argument("ref")
@click.option("--json", "as_json", is_flag=True)
def cmd_of(kind: str, ref: str, as_json: bool) -> None:
    """反查: 这个 project / plan / todo 关联了哪些任务线。"""
    rows = _store().lines_for(kind, ref)
    if as_json:
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        click.echo(f"（{kind} {ref} 没有关联任务线）")
        return
    click.echo(f"{kind} {ref} 关联 {len(rows)} 条任务线")
    for r in rows:
        _echo_line(r, "  ")
