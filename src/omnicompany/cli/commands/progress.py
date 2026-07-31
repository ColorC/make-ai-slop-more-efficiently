# [OMNI] origin=ai-ide ts=2026-06-06 type=cli
"""omni progress — project / plan 历史时间线条目 CRUD(用户 + 所有 agent 共用)。

写 data/boss_sight/progress.json(不改 plan/project 文件), 与网页"时间线"同一数据源。
条目自动记时间戳 + 所属 plan/project。

例:
  omni progress add plan "webworks/[2026-06-05]VILO-CONTENT-CREATION-PIPELINE" "完成 manifest, 跑通第一版 runner"
  omni progress list plan "webworks/[2026-06-05]VILO-CONTENT-CREATION-PIPELINE"
  omni progress edit a1b2c3d4e5 "修正: runner 改走批量"
  omni progress remove a1b2c3d4e5
"""
from __future__ import annotations

import json

import click

from .._access import any_caller, current_caller


@click.group("progress")
def cmd_progress() -> None:
    """project / plan 历史时间线 (add/list/edit/remove)。"""


@cmd_progress.command("add")
@click.argument("ref_type", type=click.Choice(["plan", "project"]))
@click.argument("ref_id")
@click.argument("text")
@any_caller
def cmd_progress_add(ref_type: str, ref_id: str, text: str) -> None:
    """记一条历史(自动记时间戳 + 所属 plan/project)。"""
    from omnicompany.dashboard.boss_sight.progress import add_entry

    by = current_caller() or "human"
    entry = add_entry(ref_type, ref_id, text, by=by)
    click.echo(json.dumps({"ok": True, "entry": entry}, ensure_ascii=False, indent=2))


@cmd_progress.command("list")
@click.argument("ref_type", type=click.Choice(["plan", "project"]), required=False)
@click.argument("ref_id", required=False)
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
@any_caller
def cmd_progress_list(ref_type: str | None, ref_id: str | None, as_json: bool) -> None:
    """列出历史(可按 plan/project 过滤), 按时间升序。"""
    from omnicompany.dashboard.boss_sight.progress import list_entries

    entries = list_entries(ref_type, ref_id)
    if as_json:
        click.echo(json.dumps(entries, ensure_ascii=False, indent=2))
        return
    if not entries:
        click.echo("(无历史条目)")
        return
    for e in entries:
        ts = (e.get("created_at") or "")[:19].replace("T", " ")
        click.echo(f"  {ts}  [{e.get('id')}]  {e.get('ref_type')}:{e.get('ref_id')}  by {e.get('by')}")
        click.echo(f"      {e.get('text')}")


@cmd_progress.command("edit")
@click.argument("entry_id")
@click.argument("text")
@any_caller
def cmd_progress_edit(entry_id: str, text: str) -> None:
    """改一条历史的文本。"""
    from omnicompany.dashboard.boss_sight.progress import edit_entry

    entry = edit_entry(entry_id, text)
    if entry is None:
        click.echo(json.dumps({"ok": False, "error": f"未找到条目 {entry_id}"}, ensure_ascii=False))
        raise SystemExit(1)
    click.echo(json.dumps({"ok": True, "entry": entry}, ensure_ascii=False, indent=2))


@cmd_progress.command("remove")
@click.argument("entry_id")
@any_caller
def cmd_progress_remove(entry_id: str) -> None:
    """删一条历史。"""
    from omnicompany.dashboard.boss_sight.progress import remove_entry

    ok = remove_entry(entry_id)
    click.echo(json.dumps({"ok": ok}, ensure_ascii=False))


__all__ = ["cmd_progress"]
