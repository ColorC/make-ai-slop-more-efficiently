# [OMNI] origin=ai-ide domain=cli ts=2026-06-27T00:00:00Z type=cli status=active
# [OMNI] summary="omni notify —— AI 推一条带跳转链接的提醒到驾驶舱铃铛。我做完一个东西推一条,用户在铃铛点了直达对应页面。"
# [OMNI] why="用户诉求(2026-06-27):AI 能发提醒让我跳转去某页面,知道东西放哪儿了。"
# [OMNI] tags=cli,dashboard,notification,jump
"""omni notify —— AI 推带跳转的提醒到铃铛。

例:
  omni notify push "决策树已就绪" --open project:aigc-image:tree
  omni notify push "看下这份报告" --open url:/review-stage?material=xxx
  omni notify list
"""

from __future__ import annotations

import click

from .._access import any_caller, external_or_controller


def _parse_open(spec: str) -> dict | None:
    """'type:id[:facet]' → {type,id,facet};'url:/path' → {url};空 → None。"""
    spec = (spec or "").strip()
    if not spec:
        return None
    if spec.startswith("url:"):
        return {"url": spec[4:]}
    parts = spec.split(":")
    ref: dict = {"type": parts[0], "id": parts[1] if len(parts) > 1 else "main"}
    if len(parts) > 2:
        ref["facet"] = parts[2]
    return ref


@click.group("notify")
def cmd_notify() -> None:
    """AI 推带跳转的提醒到驾驶舱铃铛(用户点了直达对应页面)。"""


@cmd_notify.command("push")
@click.argument("title")
@click.option("--open", "open_spec", default="",
              help="跳转目标:type:id[:facet](如 project:aigc-image:tree)或 url:/path")
@click.option("--body", default="", help="补充说明")
@external_or_controller
def cmd_notify_push(title, open_spec, body) -> None:
    """推一条提醒到铃铛。例:omni notify push \"决策树已就绪\" --open project:aigc-image:tree"""
    from omnicompany.dashboard.boss_sight.services import agent_notify

    rec = agent_notify.push_notice(title, open_ref=_parse_open(open_spec), body=body)
    click.echo(f"✓ 推送提醒 {rec['id']}:{title}" + (f"  → {open_spec}" if open_spec else "  (无跳转)"))


@cmd_notify.command("list")
@click.option("--all", "show_all", is_flag=True, help="含已点掉的")
@any_caller
def cmd_notify_list(show_all) -> None:
    """列当前铃铛提醒。"""
    from omnicompany.dashboard.boss_sight.services import agent_notify

    notices = agent_notify.list_notices(include_resolved=show_all)
    if not notices:
        click.echo("(铃铛没有 AI 提醒)")
        return
    for n in notices:
        ref = n.get("open_ref") or {}
        tgt = (ref.get("url") or (f"{ref.get('type')}:{ref.get('id')}" + (f":{ref.get('facet')}" if ref.get("facet") else ""))) if ref else "—"
        click.echo(f"  [{n['status']}] {n['title']}  → {tgt}")
