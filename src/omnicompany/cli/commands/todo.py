# [OMNI] origin=claude-code domain=cli/commands ts=2026-08-20T00:00:00+08:00 type=cli status=active
# [OMNI] summary="omni todo — 会话里提出但还没立 plan 的请求的捕获入口；落 plan-groups 的 _todo 组，带会话归属与 material_id，可归档。"
# [OMNI] why="用户 2026-08-20 明示：我会话里提出来要做什么计划和方面了就不能丢；但不能细到 todo 清单那种感觉。捕获必须零摩擦，视图才有东西可显示。"
# [OMNI] material_id="material:cli.commands.todo"
"""omni todo — 会话提出的请求不丢。

与 `omni task` 的分工：
  task = 已挂在某个 plan 下的工作项（有计划、有闸门、可派发）
  todo = 会话里刚提出、还没立 plan 的请求（只保证不丢，够分量再升 plan）

存储走 plan-groups 的 `_todo` 组（唯一真源），**不进 `_inbox`** ——
`_inbox` 是外部工作台批量导入的残留，不是本人待办。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import click


def _store():
    from omnicompany.packages.services._core.lifecycle import plan_groups_store as pgs

    return pgs


GROUP = "_todo"

# todo 五态 (2026-08-21 用户: 归档/抛弃/完成三种终态语义要分开)。
# 存储仍落 plan-groups 七态, 原值留 legacy —— 不另造第二个任务存储。
STATUS_LABEL = {
    "pending": "待做",
    "in_progress": "进行中",
    "paused": "暂停",
    "deferred": "暂停",
    "done": "完成",
    "cancelled": "抛弃",
    "review": "待验",
    "blocked": "阻塞",
}


def _tasklines_of(todo_id: str) -> list[str]:
    """双向可见: 查 todo 时显示它关联的任务线 (2026-08-21 用户要求)。"""
    if not todo_id:
        return []
    try:
        from omnicompany.packages.services._core.lifecycle import taskline_store

        return [str(r.get("title") or r.get("id")) for r in taskline_store.lines_for("todo", todo_id)]
    except Exception:
        return []


TERMINAL_HINT = "完成=做到了 / 抛弃=不做了 / 归档=不在视野里"



def _session_meta() -> dict[str, Any]:
    try:
        from omnicompany.packages.services._core.identity import current_session_meta

        return current_session_meta() or {}
    except Exception:
        return {}


def _slug(text: str) -> str:
    base = re.sub(r"[^0-9a-zA-Z一-鿿]+", "-", text).strip("-").lower()
    return (base[:40] or "todo")


def _new_id(title: str) -> str:
    return f"todo-{int(time.time())}-{_slug(title)[:24]}"


@click.group("todo")
def cmd_todo() -> None:
    """会话里提出、尚未立计划的请求 (不丢即可, 不做细碎清单)。

    子命令:
      add      记一条 (默认绑当前会话)
      list     列未归档 (--archived 看已归档, --session 只看本会话)
      archive  手动归档 / --undo 取消归档
      promote  升成 plan 的待办 (打标记 + 指路, 不代替立档)
    """


@cmd_todo.command("add")
@click.argument("title")
@click.option("--note", default="", help="补充说明 (可选)")
@click.option("--plan", "plan_id", default=None, help="如果已经知道该归哪个 plan, 直接挂上")
@click.option("--no-session", is_flag=True, help="不绑当前会话 (记成总体待办)")
@click.option("--json", "as_json", is_flag=True)
def cmd_todo_add(title: str, note: str, plan_id: str | None, no_session: bool, as_json: bool) -> None:
    """记一条会话里提出的请求。"""
    pgs = _store()
    meta = {} if no_session else _session_meta()
    tid = _new_id(title)
    record: dict[str, Any] = {
        "id": tid,
        "title": title.strip(),
        "status": "pending",
        "plan_id": plan_id,
        "description": note or None,
        "material_id": f"material:todo.{tid}",
        "source": "conversation",
        # Status/title edits made through the user-facing TODO command are
        # human-owned; session maintenance must not treat them as timer input.
        "status_source": "human",
        "session_trace_id": meta.get("trace_id") or None,
        "session_plan": meta.get("active_plan") or None,
    }
    entry = pgs.add_task(GROUP, record)
    if as_json:
        click.echo(json.dumps(entry, ensure_ascii=False, indent=2))
        return
    click.echo(f"记下了: {entry['title']}")
    click.echo(f"  id        : {entry['id']}")
    click.echo(f"  material  : {record['material_id']}")
    if record["session_trace_id"]:
        click.echo(f"  会话      : {record['session_trace_id']}")
    if plan_id:
        click.echo(f"  归属 plan : {plan_id}")


def _rows(include_archived: bool, session_only: bool) -> list[dict[str, Any]]:
    pgs = _store()
    payload = pgs.load_group(GROUP) or {}
    trace = _session_meta().get("trace_id") if session_only else None
    out = []
    for t in payload.get("tasks", []):
        if bool(t.get("archived")) != include_archived:
            continue
        legacy = t.get("legacy") or {}
        if trace and legacy.get("session_trace_id") != trace:
            continue
        out.append({
            "id": t.get("id"),
            "title": t.get("title"),
            "status": t.get("status"),
            "plan_id": t.get("plan_id"),
            "material_id": legacy.get("material_id"),
            "session_trace_id": legacy.get("session_trace_id"),
            "memo": legacy.get("memo"),
            "tasklines": _tasklines_of(str(t.get("id") or "")),
            "updated_at": t.get("updated_at"),
        })
    out.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)
    return out


@cmd_todo.command("list")
@click.option("--archived", is_flag=True, help="看已归档的")
@click.option("--session", "session_only", is_flag=True, help="只看当前会话提出的")
@click.option("--json", "as_json", is_flag=True)
def cmd_todo_list(archived: bool, session_only: bool, as_json: bool) -> None:
    """列会话提出的请求。"""
    rows = _rows(archived, session_only)
    if as_json:
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        click.echo("（无）" if archived else "（没有待办的会话请求）")
        return
    label = "已归档" if archived else "未归档"
    scope = "本会话" if session_only else "全部"
    click.echo(f"{scope} · {label} · {len(rows)} 条")
    for r in rows:
        mark = "→" if r.get("plan_id") else "·"
        plan = f"  [{r['plan_id']}]" if r.get("plan_id") else ""
        state = STATUS_LABEL.get(str(r.get("status")), str(r.get("status") or ""))
        click.echo(f"  {mark} [{state}] {r['title']}{plan}")
        click.echo(f"      {r['id']}")
        if r.get("tasklines"):
            click.echo(f"      任务线: {', '.join(r['tasklines'])}")
        if r.get("memo"):
            click.echo(f"      备忘: {r['memo']}")


@cmd_todo.command("archive")
@click.argument("todo_id")
@click.option("--undo", is_flag=True, help="取消归档")
def cmd_todo_archive(todo_id: str, undo: bool) -> None:
    """手动归档一条 (或 --undo 取消)。"""
    try:
        entry = _store().set_archived(todo_id, not undo)
    except Exception as e:  # noqa: BLE001
        click.echo(f"ERROR: {e}", err=True)
        raise SystemExit(1) from e
    click.echo(("取消归档: " if undo else "已归档: ") + str(entry.get("title")))


@cmd_todo.command("promote")
@click.argument("todo_id")
@click.option("--plan", "plan_id", required=True, help="升到哪个 plan (先用 omni plan list 挑)")
def cmd_todo_promote(todo_id: str, plan_id: str) -> None:
    """把一条会话请求挂到 plan 上 (立档本身仍是人/会话的事)。"""
    try:
        entry = _store().patch_task(todo_id, plan_id=plan_id)
    except Exception as e:  # noqa: BLE001
        click.echo(f"ERROR: {e}", err=True)
        raise SystemExit(1) from e
    click.echo(f"已挂到 plan: {plan_id}")
    click.echo(f"  {entry.get('title')}")


@cmd_todo.command("memo")
@click.argument("todo_id")
@click.argument("text")
def cmd_todo_memo(todo_id: str, text: str) -> None:
    """给一条 todo 记备忘 (追加, 不覆盖历史)。"""
    pgs = _store()
    found = pgs.find_task(todo_id)
    if not found:
        click.echo(f"ERROR: 找不到 {todo_id}", err=True)
        raise SystemExit(1)
    _, _, entry = found
    old = (entry.get("legacy") or {}).get("memo") or ""
    merged = (old + chr(10) if old else "") + text.strip()
    pgs.patch_task(todo_id, memo=merged)
    click.echo(f"备忘已记: {entry.get('title')}")
    click.echo(f"  {text.strip()}")


def _set(todo_id: str, status: str, word: str) -> None:
    pgs = _store()
    try:
        entry = pgs.patch_task(todo_id, status=status, status_source="human")
    except Exception as e:  # noqa: BLE001
        click.echo(f"ERROR: {e}", err=True)
        raise SystemExit(1) from e
    click.echo(f"{word}: {entry.get('title')}")


@cmd_todo.command("pause")
@click.argument("todo_id")
def cmd_todo_pause(todo_id: str) -> None:
    """暂停一条 todo (可再 resume 唤起)。"""
    _set(todo_id, "deferred", "已暂停")


@cmd_todo.command("resume")
@click.argument("todo_id")
def cmd_todo_resume(todo_id: str) -> None:
    """把暂停的 todo 重新唤起。"""
    _set(todo_id, "in_progress", "已唤起")


@cmd_todo.command("done")
@click.argument("todo_id")
def cmd_todo_done(todo_id: str) -> None:
    """标记完成 (做到了)。"""
    _set(todo_id, "done", "已完成")


@cmd_todo.command("drop")
@click.argument("todo_id")
def cmd_todo_drop(todo_id: str) -> None:
    """抛弃 (不做了) —— 与归档不同: 归档只是不在视野里。"""
    _set(todo_id, "cancelled", "已抛弃")
