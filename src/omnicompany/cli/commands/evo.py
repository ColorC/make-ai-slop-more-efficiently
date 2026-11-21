# [OMNI] origin=human ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:cli.commands.evolution.record_viewer.py"
"""omni evo - 进化记录。"""
import click
from ..db import open_db, resolve_db, fmt_time, fmt_bool, truncate


@click.command("evo")
@click.option("--limit", default=20, show_default=True)
@click.option("--node", "-n", default=None, help="过滤 target_node_id 前缀")
@click.option("--db", default=None)
def cmd_evo(limit: int, node: str | None, db: str | None):
    """列出进化记录（evolution_outcome_log）。"""
    conn = open_db(resolve_db(db))

    # 表名可能不同，先检查
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    tbl = None
    for candidate in ("evolution_outcome_log", "evolution_signals", "evo_log"):
        if candidate in tables:
            tbl = candidate
            break

    if not tbl:
        click.echo("  (no evolution table found)")
        return

    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
    q = f"SELECT * FROM {tbl} WHERE 1=1"
    params: list = []
    if node and "target_node_id" in cols:
        q += " AND target_node_id LIKE ?"
        params.append(node + "%")
    if "id" in cols:
        q += " ORDER BY id DESC"
    q += f" LIMIT {limit}"

    rows = conn.execute(q, params).fetchall()
    click.echo(f"\n  {len(rows)} evo record(s) from '{tbl}'\n")
    for r in rows:
        d = dict(r)
        created = fmt_time(d.get("created_at"))
        evo_id = str(d.get("evolution_id", d.get("id", "?")))[:16]
        mtype = d.get("mutation_type", "?")
        node_id = d.get("target_node_id", "?")
        effective = fmt_bool(d.get("effective"), "effective", "no-effect")
        delta = d.get("delta_error")
        delta_str = f"  delta={delta:.3f}" if delta is not None else ""
        click.echo(f"  {created}  {evo_id:16}  {mtype:20}  {effective}{delta_str}")
        click.echo(f"    node: {truncate(str(node_id), 60)}")
        click.echo()
