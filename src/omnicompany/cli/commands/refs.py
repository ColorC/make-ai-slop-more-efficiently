# [OMNI] origin=ai-ide domain=research/cli ts=2026-06-14T00:00:00Z type=cli status=active
# [OMNI] summary="omni refs — 本地资产发现。用公开内容/参考源/调研前先查这里:研究记录+已拉repo+资料,有就有没有就没有。"
# [OMNI] why="用户痛点:agent 注意不到本地已有(参考源/调研过的)。这是'先查本地再上网'的统一入口。catalog 真源 data/domains/research/library/catalog.json 可直接 grep。"
# [OMNI] tags=research,cli,refs,discovery
"""omni refs — 本地资产发现(find / sync / catalog)。

用公开领域内容、"参考 X 源码"、要调研某主题前: 先 `omni refs find "<关键词>"`。
"""
from __future__ import annotations

import click

from .._access import any_caller


@click.group("refs")
def cmd_refs() -> None:
    """本地资产发现。用公开内容/参考源前先 `omni refs find "<关键词>"`。"""


@cmd_refs.command("find")
@click.argument("query")
@click.option("--no-semantic", is_flag=True, help="只走确定性召回,不调模型语义兜底")
@any_caller
def cmd_refs_find(query: str, no_semantic: bool) -> None:
    """查本地有没有这东西(研究记录 / 已拉 repo / 资料)。有就列,没有就明说。"""
    from omnicompany.packages.domains.research import catalog

    hits = catalog.find(query, allow_semantic=not no_semantic)
    if not hits:
        click.echo(f"✗ 本地无「{query}」(研究记录/已拉 repo/资料都没有)。可放心新拉/新调研。")
        return
    click.echo(f"✓ 本地有 {len(hits)} 项命中「{query}」:")
    for h in hits:
        click.echo(f"  [{h.get('kind','')}] {h.get('name','')}  ({h.get('id','')})")
        if h.get("path"):
            click.echo(f"        路径: {h['path']}")
        if h.get("source_url"):
            click.echo(f"        来源: {h['source_url']}")
        if h.get("description"):
            click.echo(f"        说明: {h['description'][:140]}")


@cmd_refs.command("sync")
@any_caller
def cmd_refs_sync() -> None:
    """全量重扫参考项目 + 投影研究记录,重建 catalog(确定性、不馊)。"""
    from omnicompany.packages.domains.research import catalog

    r = catalog.rebuild()
    click.echo(f"catalog 重建完成: {r['total']} 项 {r['counts']}")


@cmd_refs.command("catalog")
@click.option("--kind", default="", help="只看某类: repo / material / research_record")
@any_caller
def cmd_refs_catalog(kind: str) -> None:
    """列 catalog 现有条目。"""
    from omnicompany.packages.domains.research import catalog

    items = catalog.active_items()
    if kind:
        items = [i for i in items if i.get("kind") == kind]
    if not items:
        click.echo("(catalog 空,先 `omni refs sync`)")
        return
    from collections import Counter
    click.echo(f"本地资产 catalog · {len(items)} 项 {dict(Counter(i.get('kind') for i in items))}:")
    for i in sorted(items, key=lambda x: (x.get("kind", ""), x.get("name", ""))):
        click.echo(f"  [{i.get('kind',''):15}] {i.get('name','')[:40]:<40} {i.get('source_url','') or i.get('id','')}")
