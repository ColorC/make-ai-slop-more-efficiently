# [OMNI] origin=human ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:cli.routing.open_loops.displayer.implementation.py"
"""omni open-loops - 未闭合的路由事件。"""
import click
from ..db import open_db, resolve_db, fmt_time, truncate, type_ids


@click.command("open-loops")
@click.option("--last", "-n", default=20, show_default=True, help="最近 N 轮")
@click.option("--round-num", type=int, default=None)
@click.option("--db", default=None)
def cmd_loops(last: int, round_num: int | None, db: str | None):
    """列出未闭合的路由（agent_success=0 或 route_found=0）。"""
    conn = open_db(resolve_db(db))

    if round_num is not None:
        q = "SELECT * FROM routing_events WHERE round_num=? AND (agent_success=0 OR route_found=0) ORDER BY id DESC"
        rows = conn.execute(q, (round_num,)).fetchall()
    else:
        max_rn = conn.execute("SELECT MAX(round_num) FROM routing_events").fetchone()[0] or 0
        min_rn = max(0, max_rn - last)
        q = ("SELECT * FROM routing_events WHERE (agent_success=0 OR route_found=0) "
             "AND (round_num IS NULL OR round_num >= ?) ORDER BY id DESC")
        rows = conn.execute(q, (min_rn,)).fetchall()

    click.echo(f"\n  {len(rows)} open loop(s)\n")
    for re in rows:
        routed = "routed" if re["route_found"] else "NO ROUTE"
        click.echo(
            f"  round={re['round_num'] or '?':4}  {re['trace_id'][:16]}  "
            f"{routed:10}  {fmt_time(re['created_at'])}"
        )
        click.echo(f"    task   : {truncate(re['task_desc'], 90)}")
        click.echo(f"    input  : {type_ids(re['input_types'])}")
        click.echo(f"    target : {type_ids(re['target_types'])}")
        click.echo()
