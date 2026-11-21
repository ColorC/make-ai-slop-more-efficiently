# [OMNI] origin=human ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:cli.pain_signals.displayer.implementation.py"
"""omni pain - 节点痛觉信号。"""
import click
from ..db import open_db, resolve_db, fmt_time, truncate


@click.command("pain")
@click.option("--node", "-n", default=None, help="过滤 node_id 前缀")
@click.option("--round-num", type=int, default=None)
@click.option("--severity", "-s", default=None, help="过滤 severity")
@click.option("--limit", default=30, show_default=True)
@click.option("--db", default=None)
def cmd_pain(node: str | None, round_num: int | None, severity: str | None,
             limit: int, db: str | None):
    """列出 pain_signals。"""
    conn = open_db(resolve_db(db))
    q = "SELECT * FROM pain_signals WHERE 1=1"
    params: list = []
    if node:
        q += " AND node_id LIKE ?"
        params.append(node + "%")
    if round_num is not None:
        q += " AND round_num=?"
        params.append(round_num)
    if severity:
        q += " AND severity=?"
        params.append(severity)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(q, params).fetchall()
    click.echo(f"\n  {len(rows)} pain signal(s)\n")
    for p in rows:
        click.echo(
            f"  [{p['id']:5}] {fmt_time(p['created_at'])}  "
            f"severity={p['severity']:8}  round={p['round_num'] or '?'}"
        )
        click.echo(f"    node  : {p['node_id']}")
        click.echo(f"    signal: {truncate(p['signal_text'], 100)}")
        click.echo()
