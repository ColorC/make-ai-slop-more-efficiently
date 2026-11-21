# [OMNI] origin=claude-code ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:cli.commands.domain.node_loader.py"
"""omni domain - 私域节点管理。"""
import click
from ..db import resolve_db


@click.group("domain")
def cmd_domain():
    """管理私域节点（加载、列出、状态）。"""


@cmd_domain.command("load")
@click.argument("domain_dir", required=False)
@click.option("--db", default=None)
def domain_load(domain_dir: str | None, db: str | None):
    """加载私域节点到 semantic_network.db。

    DOMAIN_DIR: domain 目录路径（含 domain.yaml）。
    不指定则从 OMNI_DOMAINS / OMNI_DOMAINS_CONFIG 环境变量读取。
    """
    from omnicompany.runtime.storage.domain_loader import load_domain, load_domains_from_env
    db_path = resolve_db(db)

    if domain_dir:
        loaded = load_domain(domain_dir, db_path)
        click.echo(f"Loaded {len(loaded)} nodes from {domain_dir}:")
        for nid in loaded:
            click.echo(f"  {nid}")
    else:
        results = load_domains_from_env(db_path)
        if not results:
            click.echo("No domains loaded. Set OMNI_DOMAINS or OMNI_DOMAINS_CONFIG env var.")
            return
        for domain_id, nodes in results.items():
            click.echo(f"  [{domain_id}] {len(nodes)} nodes")


@cmd_domain.command("list")
@click.option("--db", default=None)
def domain_list(db: str | None):
    """列出 DB 中所有私域节点的来源统计。"""
    import sqlite3
    conn = sqlite3.connect(resolve_db(db))
    rows = conn.execute("""
        SELECT source_channel, COUNT(*) as cnt
        FROM semantic_nodes
        WHERE source_channel LIKE 'private:%'
        GROUP BY source_channel
        ORDER BY source_channel
    """).fetchall()
    if not rows:
        click.echo("No private-domain nodes found.")
        return
    click.echo(f"\n  {'source_channel':30}  nodes")
    click.echo("  " + "-" * 40)
    for r in rows:
        click.echo(f"  {r[0]:30}  {r[1]}")
