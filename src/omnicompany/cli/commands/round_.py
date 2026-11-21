# [OMNI] origin=human ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:cli.commands.execution_round_inspector.implementation.py"
"""omni round [N] - 查看单轮或摘要列表。"""
import click
from ..db import open_db, resolve_db, fmt_time, fmt_bool, truncate


@click.command("round")
@click.argument("round_num", type=int, required=False)
@click.option("--last", "-n", default=10, show_default=True, help="列出最近 N 轮")
@click.option("--db", default=None)
def cmd_round(round_num: int | None, last: int, db: str | None):
    """查看 round 详情或最近 N 轮摘要。"""
    conn = open_db(resolve_db(db))

    # 检查 execution_rounds 是否存在
    has_er = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='execution_rounds'"
    ).fetchone() is not None

    if round_num is not None:
        _show_round(conn, round_num, has_er)
    else:
        _list_rounds(conn, last, has_er)


def _show_round(conn, round_num: int, has_er: bool):
    if has_er:
        r = conn.execute("SELECT * FROM execution_rounds WHERE round_num=?", (round_num,)).fetchone()
    else:
        r = None

    events = conn.execute(
        "SELECT * FROM routing_events WHERE round_num=? ORDER BY id", (round_num,)
    ).fetchall()

    click.echo(f"\n{'='*60}")
    click.echo(f"  ROUND #{round_num}")
    click.echo(f"{'='*60}")

    if r:
        click.echo(f"  task      : {truncate(r['task_desc'], 100)}")
        click.echo(f"  started   : {fmt_time(r['started_at'])}")
        click.echo(f"  completed : {fmt_time(r['completed_at'])}")
        click.echo(f"  success   : {fmt_bool(r['agent_success'])}")
        click.echo(f"  evo       : {fmt_bool(r['evo_triggered'])}")
        click.echo(f"  open_loops: {r['open_loop_count'] or 0}")
        if r['final_output_text']:
            click.echo(f"  output    : {truncate(r['final_output_text'], 120)}")
    elif events:
        # fallback
        click.echo(f"  task : {truncate(events[0]['task_desc'], 100)}")
        click.echo(f"  time : {fmt_time(events[0]['created_at'])}")

    if events:
        click.echo(f"\n  {len(events)} routing event(s):\n")
        for re in events:
            status = "ok" if re["agent_success"] else "FAIL"
            routed = "routed" if re["route_found"] else "NO ROUTE"
            click.echo(f"  {status} {re['trace_id'][:16]}  {routed:10}  {truncate(re['task_desc'], 60)}")
    else:
        click.echo("  (no routing_events for this round)")


def _list_rounds(conn, last: int, has_er: bool):
    if has_er:
        rows = conn.execute(
            "SELECT * FROM execution_rounds ORDER BY round_num DESC LIMIT ?", (last,)
        ).fetchall()
        click.echo(f"\n{'Round':>6}  {'Time':16}  {'OK':3}  {'Evo':3}  {'Loops':5}  Task")
        click.echo("─" * 70)
        for r in rows:
            click.echo(
                f"  #{r['round_num']:<4}  {fmt_time(r['started_at']):16}  "
                f"{fmt_bool(r['agent_success']):3}  "
                f"{fmt_bool(r['evo_triggered']):3}  "
                f"{r['open_loop_count'] or 0:5}  "
                f"{truncate(r['task_desc'], 40)}"
            )
    else:
        # fallback: aggregate from routing_events
        rows = conn.execute("""
            SELECT round_num,
                   MIN(created_at) as started_at,
                   COUNT(*) as total,
                   SUM(route_found) as routed,
                   SUM(agent_success) as success
            FROM routing_events
            WHERE round_num IS NOT NULL
            GROUP BY round_num
            ORDER BY round_num DESC
            LIMIT ?
        """, (last,)).fetchall()
        click.echo(f"\n{'Round':>6}  {'Time':16}  {'OK/Total':10}  Routed")
        click.echo("─" * 60)
        for r in rows:
            click.echo(
                f"  #{r['round_num']:<4}  {fmt_time(r['started_at']):16}  "
                f"{r['success'] or 0}/{r['total']:5}    {r['routed'] or 0}"
            )
