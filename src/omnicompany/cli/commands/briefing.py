# [OMNI] origin=kimi-code ts=2026-07-26 type=cli
# [OMNI] material_id="material:cli.commands.briefing.py"
"""omni briefing — 晨报 + 按需报告: 手动唤起才工作, 默认零 LLM 只读。"""
from __future__ import annotations

import json
from datetime import date, datetime

import click

from .._access import any_caller


@click.group("briefing")
def cmd_briefing() -> None:
    """晨报 + 消息面 + 按需报告(手动唤起; 默认零 LLM、只读)。"""


@cmd_briefing.command("morning")
@click.option("--llm", "use_llm", is_flag=True, help="额外调一次统一 LLM 槽位生成 100 字内语义总结")
@click.option("--refresh-owners", is_flag=True, help="逐张调任务平台 CLI 刷新经办人缓存(慢, 默认只读缓存)")
@click.option("--with-inbox/--no-inbox", default=True, show_default=True,
              help="并入消息面区块(只读当天 inbox 缓存, 不真拉远端; 无缓存则显示提示)")
@click.option("--json", "as_json", is_flag=True, help="输出全量 JSON(终端视图每组只显示前 10 条)")
@any_caller
def cmd_briefing_morning(use_llm: bool, refresh_owners: bool, with_inbox: bool, as_json: bool) -> None:
    """晨报: 今日进入 + 本周剩余 + 安排建议(+消息面), 默认零 LLM 只读。"""
    from omnicompany.packages.services._focus import briefing

    if refresh_owners:
        from omnicompany.packages.services._focus.meegle_dispatch import (
            list_current_meegle_items,
        )

        items = list_current_meegle_items()

        def _progress(index: int, total: int, task_id: str) -> None:
            click.echo(f"\r刷新经办人 {index}/{total} {task_id}", nl=False)

        owners = briefing.refresh_owner_fields(items, progress=_progress)
        click.echo(f"\r经办人缓存已刷新: {len(owners)} 张          ")
    else:
        owners = None  # None → 服务层读缓存/种子

    result = briefing.build_morning_briefing(owner_fields=owners)

    # 消息面只读当天缓存(不真拉远端); 无缓存 → None, 终端显示提示行。
    inbox = None
    if with_inbox:
        cache = briefing.inbox_cache_path(date.today())
        if cache.exists():
            try:
                lark = json.loads(cache.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                lark = None
            if lark is not None:
                cached_owners = owners if owners is not None else briefing.load_owner_fields()
                inbox = {
                    "date": result["date"],
                    "lark": lark,
                    "lark_cache_hit": True,
                    "meegle_others": briefing.meegle_others_activity(
                        cached_owners, today=date.today()),
                }
    if inbox is not None:
        result["inbox"] = inbox

    llm_summary = ""
    if use_llm:
        llm_summary = briefing._llm_summary(  # noqa: SLF001
            briefing.morning_llm_context(result), caller="briefing.morning",
        )
        result["llm_summary"] = llm_summary

    text = briefing.render_morning_text(result, per_group_limit=None if as_json else 10,
                                        inbox=inbox)
    if llm_summary:
        text += f"\n## 语义总结(--llm)\n{llm_summary}\n"
    out_path = briefing.write_briefing_md(f"morning-{result['date']}.md", text)

    if as_json:
        click.echo(json.dumps({**result, "md_path": str(out_path)}, ensure_ascii=False))
        return
    click.echo(text, nl=False)
    click.echo(click.style(f"已落盘: {out_path}", fg="bright_black"))


@cmd_briefing.command("inbox")
@click.option("--refresh", is_flag=True, help="重拉远端(默认同一天内用 inbox-<date>.json 缓存)")
@click.option("--json", "as_json", is_flag=True, help="输出全量 JSON")
@any_caller
def cmd_briefing_inbox(refresh: bool, as_json: bool) -> None:
    """消息面: 他人来找你的事(外部单聊 + 任务平台他人动作), 只读。"""
    from omnicompany.packages.services._focus import briefing

    result = briefing.build_inbox(refresh=refresh)
    text = briefing.render_inbox_text(result)
    out_path = briefing.write_briefing_md(f"inbox-{result['date']}.md", text)

    if as_json:
        click.echo(json.dumps({**result, "md_path": str(out_path)}, ensure_ascii=False))
        return
    click.echo(text, nl=False)
    click.echo(click.style(f"已落盘: {out_path}", fg="bright_black"))


@cmd_briefing.command("report")
@click.option("--date", "day_str", default=None, help="报告日期 YYYY-MM-DD(默认今天; --days 时为最后一天)")
@click.option("--days", default=1, type=click.IntRange(1, 90), show_default=True,
              help="聚合最近 N 天(llm_audit 按日期目录; 任务用当前快照)")
@click.option("--llm", "use_llm", is_flag=True, help="额外调一次统一 LLM 槽位生成语义总结")
@click.option("--json", "as_json", is_flag=True, help="输出全量 JSON")
@any_caller
def cmd_briefing_report(day_str: str | None, days: int, use_llm: bool, as_json: bool) -> None:
    """按需报告: token 用量 + 会话时长 + 任务分布 三张表。"""
    from omnicompany.packages.services._focus import briefing

    day = date.today()
    if day_str:
        try:
            day = datetime.strptime(day_str, "%Y-%m-%d").date()
        except ValueError:
            raise click.BadParameter(f"--date 需为 YYYY-MM-DD 格式, 得到: {day_str}") from None

    result = briefing.build_report(day=day, days=days)

    llm_summary = ""
    if use_llm:
        llm_summary = briefing._llm_summary(  # noqa: SLF001
            briefing.report_llm_context(result), caller="briefing.report",
        )
        result["llm_summary"] = llm_summary

    text = briefing.render_report_text(result)
    if llm_summary:
        text += f"\n## 语义总结(--llm)\n{llm_summary}\n"
    out_path = briefing.write_briefing_md(f"report-{result['date']}.md", text)

    if as_json:
        click.echo(json.dumps({**result, "md_path": str(out_path)}, ensure_ascii=False))
        return
    click.echo(text, nl=False)
    click.echo(click.style(f"已落盘: {out_path}", fg="bright_black"))


__all__ = ["cmd_briefing"]
