# [OMNI] origin=claude-code ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:cli.evolution.inquiry_queue.implementation.py"
"""CLI 命令：inquiry — 用户询问队列管理

用法：
    omnicompany inquiry list          # 列出待回答的询问
    omnicompany inquiry show <id>     # 查看单条询问详情
    omnicompany inquiry answer <id> <text>  # 回答询问
    omnicompany inquiry all           # 列出所有询问（含已回答）
"""
from __future__ import annotations

import click

from omnicompany.packages.services._core.evolution.workflow.user_inquiry import get_default_store


@click.group("inquiry")
def cmd_inquiry():
    """用户询问队列：查看和回答进化工作流提出的问题"""


@cmd_inquiry.command("list")
@click.option("--db", type=str, default=None, help="询问数据库路径")
def inquiry_list(db: str | None):
    """列出待回答的询问"""
    store = get_default_store(db)  # db=None → 锚定路径解析(见 user_inquiry._resolve_default_db_path)
    pending = store.list_pending()
    if not pending:
        click.echo("没有待回答的询问。")
        return
    click.echo(f"待回答的询问（共 {len(pending)} 条）：\n")
    for inq in pending:
        click.echo(f"  [{inq.id}] {inq.pipeline_id} | board={inq.board_id[:8]}")
        click.echo(f"         问题: {inq.question[:120]}")
        click.echo(f"         时间: {inq.created_at[:19]}")
        click.echo()


@cmd_inquiry.command("show")
@click.argument("inquiry_id")
@click.option("--db", type=str, default=None, help="询问数据库路径")
def inquiry_show(inquiry_id: str, db: str | None):
    """查看单条询问的完整内容"""
    store = get_default_store(db)  # db=None → 锚定路径解析(见 user_inquiry._resolve_default_db_path)
    inq = store.get(inquiry_id)
    if not inq:
        click.echo(f"未找到询问 {inquiry_id}")
        return
    click.echo(f"ID:       {inq.id}")
    click.echo(f"状态:     {inq.status}")
    click.echo(f"管线:     {inq.pipeline_id}")
    click.echo(f"Board:    {inq.board_id}")
    click.echo(f"Trace:    {inq.trace_id}")
    click.echo(f"创建时间: {inq.created_at[:19]}")
    click.echo()
    click.echo(f"问题：\n{inq.question}")
    click.echo()
    click.echo(f"上下文：\n{inq.context}")
    if inq.status == "answered":
        click.echo()
        click.echo(f"回答（{inq.answered_at[:19]}）：\n{inq.answer}")


@cmd_inquiry.command("answer")
@click.argument("inquiry_id")
@click.argument("answer_text")
@click.option("--db", type=str, default=None, help="询问数据库路径")
def inquiry_answer(inquiry_id: str, answer_text: str, db: str | None):
    """回答一条询问（进化工作流将在下次运行时读取此答案）

    示例：
        omnicompany inquiry answer abc12345 "idiom_translator 的 prompt 需要明确要求导出所有公开函数"
    """
    store = get_default_store(db)  # db=None → 锚定路径解析(见 user_inquiry._resolve_default_db_path)
    inq = store.get(inquiry_id)
    if not inq:
        click.echo(f"未找到询问 {inquiry_id}")
        return
    if inq.status == "answered":
        click.echo(f"询问 {inquiry_id} 已有回答，是否覆盖？[y/N] ", nl=False)
        if input().strip().lower() != "y":
            click.echo("取消。")
            return
    ok = store.answer(inquiry_id, answer_text)
    if ok:
        click.echo("回答已记录。可使用以下命令继续进化工作流：")
        click.echo(f"    omnicompany evo continue --board {inq.board_id}")
    else:
        click.echo("记录失败，请检查 inquiry_id 是否正确。")


@cmd_inquiry.command("all")
@click.option("--limit", "-n", type=int, default=20, help="显示条数")
@click.option("--db", type=str, default=None, help="询问数据库路径")
def inquiry_all(limit: int, db: str | None):
    """列出所有询问（含已回答）"""
    store = get_default_store(db)  # db=None → 锚定路径解析(见 user_inquiry._resolve_default_db_path)
    inquiries = store.list_all(limit=limit)
    if not inquiries:
        click.echo("暂无询问记录。")
        return
    for inq in inquiries:
        status_label = {"pending": "⏳", "answered": "✅", "expired": "❌"}.get(inq.status, "?")
        click.echo(f"  {status_label} [{inq.id}] {inq.pipeline_id} | {inq.question[:80]}")
