# [OMNI] origin=ai-ide domain=research/cli ts=2026-06-14T00:00:00Z type=cli status=active
# [OMNI] summary="omni research — 公开调研导航 + 统一研究库查询 + 原生搜索协议脚手架(check/save)。"
# [OMNI] why="2026-06-30 转原生搜索:交互式由前台 agent 用原生 WebSearch 跑(SKILL),check/save 是协议脚手架;无人值守走 omni run research.run(codex 执行器)。"
# [OMNI] tags=research,cli,library,native
"""omni research — 公开调研导航 + 研究库 + 原生搜索协议脚手架。

无人值守跑调研: `omni run research.run -i topic="<题目>"`(codex 原生搜索)。
交互式: 前台 agent 按 research SKILL 协议自己搜,用 `check` 查重、`save` 落库。
本命令: 查重(check)、落库(save)、看库累积(library)、落点(status)、本地资产(find-local)。
"""
from __future__ import annotations

import click

from .._access import any_caller


@click.group("research")
def cmd_research() -> None:
    """公开调研:原生搜索协议脚手架 + 研究库。无人值守跑 `omni run research.run -i topic="..."`。"""


@cmd_research.command("status")
@any_caller
def cmd_research_status() -> None:
    """管线落点 + 研究库计数。"""
    from omnicompany.packages.domains.research import _paths, library

    recs = library.active_records()
    runs = _paths.RUNS_ROOT
    n_runs = len(list(runs.iterdir())) if runs.is_dir() else 0
    click.echo("== 公开调研管线 (Team) ==")
    click.echo(f"  管线/Worker : {_paths._OMNI_ROOT / 'src/omnicompany/packages/domains/research'}")
    click.echo(f"  统一研究库  : {_paths.RECORDS_PATH}  ({len(recs)} 条 active)")
    click.echo(f"  runs        : {n_runs}")
    click.echo(f"  reports     : {_paths.REPORTS_ROOT}")
    click.echo("  无人值守跑  : omni run research.run -i topic=\"<题目>\"  (codex 原生搜索)")
    click.echo("  交互式跑    : 按 research SKILL 协议自己搜 → omni research save")


@cmd_research.command("list")
@any_caller
def cmd_research_list() -> None:
    """列已注册的 research Team。"""
    from omnicompany.core.registry import discover, list_all

    discover()
    rows = [e for e in list_all() if e.name.startswith("research.")]
    if not rows:
        click.echo("(未发现 research 管线)")
        return
    click.echo("公开调研管线(Team,经 omni run 调度):")
    for e in sorted(rows, key=lambda x: x.name):
        click.echo(f"  omni run {e.name:<22} {e.description}")


@cmd_research.command("library")
@click.option("--topic", default="", help="按题目查重(给题目看库里有没有同题)")
@any_caller
def cmd_research_library(topic: str) -> None:
    """看统一研究库累积了什么;给 --topic 查同题是否已调研过。"""
    from omnicompany.packages.domains.research import library

    if topic:
        norm = library.normalize_topic(topic)
        hit = library.lookup_by_topic(norm)
        if hit:
            click.echo(f"✓ 库内已有同题: {hit['record_id']}")
            click.echo(f"  更新 {hit.get('updated_at', '')} · 丰富度 {hit.get('richness', 0)} · "
                       f"来源 {len(hit.get('sources') or [])} 条 · 发现 {len(hit.get('findings') or [])} 条")
            click.echo(f"  摘要: {(hit.get('summary') or '')[:200]}")
        else:
            click.echo(f"（库内无同题「{topic}」,可放心新调研）")
        return

    recs = sorted(library.active_records(), key=lambda r: r.get("updated_at", ""), reverse=True)
    if not recs:
        click.echo("（研究库还空着）")
        return
    click.echo(f"统一研究库 · {len(recs)} 条:")
    for r in recs:
        click.echo(f"  [{r.get('richness', 0):>2}] {r.get('topic', '')[:40]:<40} "
                   f"{r.get('record_id', '')}  (更新 {r.get('updated_at', '')[:10]})")


@cmd_research.command("check")
@click.argument("topic")
@any_caller
def cmd_research_check(topic: str) -> None:
    """开跑前查重(给 agent/脚本读的 JSON):库里有没有同题、已覆盖/还缺哪些角度。

    原生搜索协议第一步。exists=true 时按 perspectives_open 只补缺口(增量),不重复全搜。
    """
    import json

    from omnicompany.packages.domains.research import library

    hit = library.lookup_by_topic(library.normalize_topic(topic))
    out: dict = {"exists": bool(hit)}
    if hit:
        out.update({
            "record_id": hit["record_id"],
            "topic": hit.get("topic"),
            "summary": (hit.get("summary") or "")[:400],
            "richness": hit.get("richness", 0),
            "n_sources": len(hit.get("sources") or []),
            "n_findings": len(hit.get("findings") or []),
            "keywords": hit.get("keywords") or [],
            "aliases": hit.get("aliases") or [],
            "perspectives_covered": hit.get("perspectives_covered") or [],
            "perspectives_open": hit.get("perspectives_open") or [],
        })
    click.echo(json.dumps(out, ensure_ascii=False))


@cmd_research.command("save")
@click.option("--file", "file_path", default="", help="读 JSON 文件(原生/codex 搜完综合好的产物)")
@click.option("-j", "--json", "json_str", default="", help="直接传 JSON 字符串;都不给则读 stdin")
@any_caller
def cmd_research_save(file_path: str, json_str: str) -> None:
    """把原生搜索综合好的产物落进统一研究库(查重增量合并 + 投影 catalog + 渲 report)。

    \b
    入参 JSON:
      {
        "topic": "调研题目(必填)",
        "summary": "2-4 句概述",
        "findings": [{"claim": "具体结论", "source_url": "依据 url",
                      "support": "supported|partial|unsupported|unverified"}],
        "sources": [{"title": "...", "url": "...", "snippet": "...", "text": "正文(给了就落本地快照)"}],
        "keywords": [], "aliases": [], "perspectives_covered": [], "perspectives_open": []
      }
    """
    import json
    import sys
    from pathlib import Path

    from omnicompany.packages.domains.research import library

    if file_path:
        raw = Path(file_path).read_text(encoding="utf-8")
    elif json_str:
        raw = json_str
    else:
        raw = sys.stdin.read()
    data = json.loads(raw)
    topic = (data.get("topic") or "").strip()
    if not topic:
        raise click.ClickException("JSON 缺 topic")

    sources: list[dict] = []
    snaps: dict[str, str] = {}
    for s in data.get("sources") or []:
        src = {k: s[k] for k in ("title", "url", "snippet") if s.get(k)}
        if s.get("text") and src.get("url"):
            snaps[src["url"]] = s["text"]
        if src.get("url"):
            sources.append(src)

    synthesis = {
        "summary": data.get("summary", ""),
        "findings": data.get("findings") or [],
        "keywords": data.get("keywords") or [],
        "aliases": data.get("aliases") or [],
        "perspectives_open": data.get("perspectives_open") or [],
    }
    coverage = {"covered": data.get("perspectives_covered") or []}
    saved, is_dup, report = library.save_research_record(
        topic, synthesis, sources, coverage=coverage, snapshot_texts=snaps or None,
    )
    n_unsup = sum(1 for f in (saved.get("findings") or []) if f.get("support") == "unsupported")
    click.echo(f"{'✓ 增量更新' if is_dup else '✓ 新建'} {saved['record_id']} · "
               f"丰富度 {saved.get('richness', 0)} · 来源 {len(saved.get('sources') or [])} · "
               f"发现 {len(saved.get('findings') or [])}（{n_unsup} 条无源支撑）")
    click.echo(f"  report: {report}")


@cmd_research.command("find-local")
@click.argument("query")
@any_caller
def cmd_research_find_local(query: str) -> None:
    """先查本地(研究记录+已拉repo+资料)有没有 query —— `omni refs find` 的别名。"""
    from omnicompany.packages.domains.research import catalog

    hits = catalog.find(query)
    if not hits:
        click.echo(f"✗ 本地无「{query}」。可放心新调研/新拉。")
        return
    click.echo(f"✓ 本地有 {len(hits)} 项命中「{query}」:")
    for h in hits:
        click.echo(f"  [{h.get('kind','')}] {h.get('name','')}  {h.get('source_url') or h.get('id','')}")


@cmd_research.command("doctor")
@any_caller
def cmd_research_doctor() -> None:
    """列带病研究记录:落库校验不过(缺字段/源无 url/快照缺失)。"""
    from omnicompany.packages.domains.research import library

    recs = library.active_records()
    bad = [(r, (r.get("validation") or {}).get("issues") or [])
           for r in recs if (r.get("validation") or {}).get("ok") is False]
    if not bad:
        click.echo(f"✓ {len(recs)} 条研究记录全部合法(或未校验)。")
        return
    click.echo(f"⚠ {len(bad)}/{len(recs)} 条记录带病:")
    for r, issues in bad:
        click.echo(f"  {r.get('record_id','')}  {r.get('topic','')[:34]}")
        for i in issues[:5]:
            click.echo(f"      - {i}")
