# [OMNI] origin=human ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:cli.semantic_nodes.browser.implementation.py"
"""omni node [node_id] - 节点详情或列表。"""
import click
from ..db import open_db, resolve_db, fmt_bool, truncate, type_ids


@click.command("node")
@click.argument("node_id", required=False)
@click.option("--active/--all", default=True, help="只看 active 节点")
@click.option("--traces", is_flag=True, help="同时显示最近 5 次 span")
@click.option("--limit", default=40, show_default=True)
@click.option("--db", default=None)
def cmd_node(node_id: str | None,
             active: bool, traces: bool, limit: int, db: str | None):
    """查看节点详情或列出节点。"""
    conn = open_db(resolve_db(db))
    if node_id:
        _show_node(conn, node_id, traces)
    else:
        _list_nodes(conn, active, limit)


def _show_node(conn, node_id: str, traces: bool):
    # 前缀匹配
    row = conn.execute(
        "SELECT * FROM semantic_nodes WHERE node_id LIKE ? LIMIT 1", (node_id + "%",)
    ).fetchone()
    if not row:
        click.echo(f"node not found: {node_id}")
        return

    n = row
    click.echo(f"\n{'='*60}")
    click.echo(f"  NODE  {n['node_id']}")
    click.echo(f"{'='*60}")
    click.echo(f"  impl_kind : {n['impl_kind']}")
    click.echo(f"  active    : {fmt_bool(n['active'])}")
    if n['description']:
        click.echo(f"  desc      : {truncate(n['description'], 100)}")
    click.echo(f"  in_types  : {type_ids(n['input_types'])}")
    click.echo(f"  out_types : {type_ids(n['output_types'])}")
    if n['processing_prompt']:
        click.echo("\n  --- processing_prompt ---")
        click.echo(truncate(n['processing_prompt'], 500))

    if traces:
        has_ss = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_spans'"
        ).fetchone()
        if has_ss:
            spans = conn.execute(
                "SELECT * FROM signal_spans WHERE node_id=? ORDER BY id DESC LIMIT 5",
                (n['node_id'],)
            ).fetchall()
            if spans:
                click.echo("\n  --- recent spans ---")
                for sp in spans:
                    status = "ok" if sp["success"] else "FAIL"
                    lat = f"{sp['latency_ms']:.0f}ms" if sp["latency_ms"] else "?"
                    click.echo(f"  [{sp['span_index']}] {status}  round={sp['round_num']}  {lat}")
                    click.echo(f"       in : {truncate(sp['input_text'], 80)}")
                    if sp["success"]:
                        click.echo(f"       out: {truncate(sp['output_text'], 80)}")
                    else:
                        click.echo(f"       err: {truncate(sp['error_text'], 80)}")


def _list_nodes(conn, active_only, limit):
    q = "SELECT * FROM semantic_nodes WHERE 1=1"
    params: list = []
    if active_only:
        q += " AND active=1"
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(q, params).fetchall()
    click.echo(f"\n  {'node_id':40}  {'impl':12}  {'active':6}  description")
    click.echo("─" * 90)
    for n in rows:
        click.echo(
            f"  {n['node_id'][:40]:40}  {n['impl_kind'] or '?':12}  "
            f"{'yes' if n['active'] else 'no':6}  {truncate(n['description'] or '-', 40)}"
        )
    click.echo(f"\n  {len(rows)} nodes")
