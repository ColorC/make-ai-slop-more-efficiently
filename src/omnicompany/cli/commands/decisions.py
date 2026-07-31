# [OMNI] origin=ai-ide domain=decisions/cli ts=2026-06-18T00:00:00Z type=cli status=active
# [OMNI] summary="omni decisions — 统一决策库的手记/召回/连边/体检入口。主线=决策记录:手记一条 → 落库 → 查回 → 接进决策树。"
# [OMNI] why="决策记录主线要先能'手记+查回'才算可用(非提取)。提供源无关的人读/手填入口,抽取管线后续往同一库灌数。"
# [OMNI] tags=decisions,cli,decision-record,library
"""omni decisions —— 统一决策库导航 + 手记 + 召回。

手记决策: omni decisions record --kind decision -s "选了X" --reject "A:贵" --choose "X:稳" -r "因为..."
手记猜想: omni decisions record --kind belief   -s "猜Y成立" --risk high --query "怎么验"
查重/召回: omni decisions find "X"      看一条: omni decisions show <id>
接树:     omni decisions link <决策id> rests_on <猜想id>
"""

from __future__ import annotations

import click

from .._access import any_caller

_KINDS = ["decision", "belief", "comment"]


def _parse_opt_why(raw: str) -> tuple[str, str]:
    """'选项:理由' → (选项, 理由)。无冒号则整串为选项。"""
    if ":" in raw:
        opt, why = raw.split(":", 1)
        return opt.strip(), why.strip()
    return raw.strip(), ""


@click.group("decisions")
def cmd_decisions() -> None:
    """统一决策库:手记决策/猜想/评论,召回,接进决策树。"""


@cmd_decisions.command("status")
@any_caller
def cmd_decisions_status() -> None:
    """决策库落点 + 计数(按 kind)。"""
    from collections import Counter

    from omnicompany.packages.domains.decisions import _paths, library

    recs = library.active_records()
    by_kind = Counter(r.get("kind", "?") for r in recs)
    click.echo("== 统一决策库 ==")
    click.echo(f"  库文件 : {_paths.RECORDS_PATH}")
    click.echo(f"  索引   : {_paths.INDEX_PATH}")
    click.echo(f"  记录   : {len(recs)} 条 active "
               f"(决策 {by_kind.get('decision', 0)} · 猜想 {by_kind.get('belief', 0)} · 评论 {by_kind.get('comment', 0)})")
    bad = sum(1 for r in recs if (r.get("validation") or {}).get("ok") is False)
    if bad:
        click.echo(f"  ⚠ 带病  : {bad} 条(omni decisions doctor 看详情)")
    click.echo("  手记   : omni decisions record --kind decision -s \"...\" --reject \"A:理由\" --choose \"B:理由\"")


@cmd_decisions.command("record")
@click.option("--kind", "-k", type=click.Choice(_KINDS), default="decision", help="决策/猜想/评论")
@click.option("--statement", "-s", required=True, help="一句话:决策结论 / 猜想陈述 / 评论要点")
@click.option("--choose", "choose", multiple=True, help="采纳项 '选项:理由'(decision,可多次)")
@click.option("--reject", "reject", multiple=True, help="被否决项 '选项:理由'(decision,可多次)")
@click.option("--rationale", "-r", default="", help="为什么这么选(理由综述)")
@click.option("--anchor", default="", help="挂在哪份载体上 'kind:ref' 如 doc:path/to.md / msg:<id>")
@click.option("--project", default="", help="所属项目 id(如 vilo / omnicompany)")
@click.option("--track", default="", help="所属轨道 'kind:id',如 plan:DECISION-MEMORY / business:vilo-card-creation")
@click.option("--applies-to", default="", help="针对对象:具体那张卡/材料/对象的描述")
@click.option("--tag", "tags", multiple=True, help="标签(可多次)")
@click.option("--alias", "aliases", multiple=True, help="召回别名(可多次,防术语对不上漏检)")
@click.option("--confidence", type=click.Choice(["high", "medium", "low"]), default=None)
@click.option("--authority", type=click.Choice(["user_explicit", "high", "medium", "low", "derived", "unknown"]),
              default="user_explicit", help="来源权威(默认 user_explicit=本人手记拍板)")
@click.option("--channel", type=click.Choice(["claude", "codex", "api", "note", "internal_doc", "manual"]),
              default="manual", help="来源渠道(手记默认 manual)")
@click.option("--risk", type=click.Choice(["low", "medium", "high"]), default=None, help="belief: 猜想错了的代价")
@click.option("--query", "evidence_query", default="", help="belief: 怎么验证这个猜想")
@click.option("--boundary", default="", help="decision: 失效边界(什么条件下需重审)")
@any_caller
def cmd_decisions_record(kind, statement, choose, reject, rationale, anchor, project, track, applies_to,
                         tags, aliases, confidence, authority, channel, risk, evidence_query, boundary) -> None:
    """手记一条决策/猜想/评论 → 落统一库。"""
    from omnicompany.packages.domains.decisions import record as record_one

    _trace_id = None
    try:  # 会话链接: best-effort
        from omnicompany.packages.services._core.identity import resolve_active_trace_id
        _trace_id = resolve_active_trace_id()
    except Exception:
        pass
    _origin = {"channel": channel}
    if _trace_id:
        _origin["session"] = _trace_id
    fields: dict = {"authority": authority, "origin": _origin}
    if project:
        fields["project"] = project
    if track:
        tkind, _, tid = track.partition(":")
        fields["track"] = {"kind": (tkind.strip() or "plan"), "id": tid.strip()}
    if applies_to:
        fields["applies_to"] = applies_to
    if tags:
        fields["tags"] = list(tags)
    if aliases:
        fields["aliases"] = list(aliases)
    if confidence:
        fields["confidence"] = confidence
    if anchor:
        akind, _, aref = anchor.partition(":")
        fields["anchor"] = {"kind": (akind.strip() or "other"), "ref": aref.strip()}

    if kind == "decision":
        space = ([{"option": o, "chosen": True, "why": w} for o, w in map(_parse_opt_why, choose)]
                 + [{"option": o, "chosen": False, "why": w} for o, w in map(_parse_opt_why, reject)])
        if space:
            fields["decision_space"] = space
        if rationale:
            fields["rationale"] = rationale
        if boundary:
            fields["boundary"] = boundary
    elif kind == "belief":
        if risk:
            fields["risk_if_wrong"] = risk
        if evidence_query:
            fields["evidence_query"] = evidence_query

    rec = record_one(kind, statement, **fields)
    try:  # 会话侧反向挂一条引用(这个会话产出了这条决策)
        from omnicompany.packages.services._core.identity import link_record_to_session
        link_record_to_session(_trace_id, kind="decision", record_id=rec["id"],
                               ref_id=(track or project or None))  # track 已是 'kind:id' 形
    except Exception:
        pass
    ok = (rec.get("validation") or {}).get("ok")
    click.echo(f"✓ 记下 {rec['id']}  [{rec['kind']}] {rec['statement'][:50]}")
    if not ok:
        for i in (rec.get("validation") or {}).get("issues") or []:
            click.echo(f"  ⚠ {i}")


@cmd_decisions.command("list")
@click.option("--kind", "-k", type=click.Choice(_KINDS), default=None, help="只看某类")
@click.option("--project", "-p", default=None, help="只看某项目")
@any_caller
def cmd_decisions_list(kind, project) -> None:
    """列决策库里的 active 记录(最新在前)。"""
    from omnicompany.packages.domains.decisions import library

    recs = [r for r in library.active_records()
            if (not kind or r.get("kind") == kind)
            and (not project or (r.get("project") or "") == project)]
    recs.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    if not recs:
        click.echo("(决策库还空着)" if not (kind or project) else "(没有匹配的记录)")
        return
    click.echo(f"统一决策库 · {len(recs)} 条:")
    for r in recs:
        tr = r.get("track") or {}
        addr = r.get("project") or ""
        if tr.get("id"):
            addr += f"/{tr.get('kind')}:{tr.get('id')}"
        click.echo(f"  {r.get('id',''):<20} [{r.get('kind','')[:8]:<8}] {r.get('status','')[:9]:<9} "
                   f"{(addr[:26]):<26} {r.get('statement','')[:40]}")


@cmd_decisions.command("find")
@click.argument("query")
@any_caller
def cmd_decisions_find(query) -> None:
    """查库里有没有 query 指的决策/猜想(先确定性,零命中再语义兜底)。"""
    from omnicompany.packages.domains.decisions import catalog

    hits = catalog.find(query)
    if not hits:
        click.echo(f"✗ 库内无「{query}」。")
        return
    click.echo(f"✓ {len(hits)} 条命中「{query}」:")
    for r in hits:
        click.echo(f"  {r.get('id',''):<20} [{r.get('kind','')}] {r.get('statement','')[:46]}")


@cmd_decisions.command("recall")
@click.argument("situation")
@any_caller
def cmd_decisions_recall(situation) -> None:
    """回忆:面对某情境,你过去的决策倾向是什么(从决策库聚合,不是查单条)。"""
    from omnicompany.packages.domains.decisions import catalog

    res = catalog.recall(situation)
    if res.get("llm") is False:
        click.echo("(LLM 暂不可用,稍后再试)")
        return
    if not res.get("tendency"):
        click.echo(f"(没从库里归纳出跟「{situation}」相关的明确倾向)")
        return
    click.echo(f"≡ 面对「{situation}」,你过去的倾向:")
    for line in str(res["tendency"]).splitlines():
        click.echo(f"  {line}")
    sup = res.get("supporting") or []
    if sup:
        click.echo("  —— 支撑的决策:")
        for r in sup:
            tr = (r.get("track") or {}).get("id", "")
            click.echo(f"   · [{r.get('project','')}{('/' + tr) if tr else ''}] {r.get('statement','')[:52]}")


@cmd_decisions.command("show")
@click.argument("record_id")
@any_caller
def cmd_decisions_show(record_id) -> None:
    """看一条记录的全貌(含决策空间/链/挑战日志)。"""
    import json

    from omnicompany.packages.domains.decisions import library

    rec = library.get(record_id)
    if not rec:
        click.echo(f"✗ 无此记录: {record_id}")
        return
    click.echo(json.dumps(rec, ensure_ascii=False, indent=2))


@cmd_decisions.command("link")
@click.argument("src_id")
@click.argument("rel", type=click.Choice(["rests_on", "supersedes", "parent", "related", "enforced_by"]))
@click.argument("dst_id")
@any_caller
def cmd_decisions_link(src_id, rel, dst_id) -> None:
    """给决策树加边:src --rel--> dst(如 决策 rests_on 猜想;enforced_by 的 dst 是执法器标识非记录 id)。"""
    from omnicompany.packages.domains.decisions import catalog, library

    try:
        rec = library.add_link(src_id, rel, dst_id)
    except ValueError as e:
        click.echo(f"✗ {e}")
        return
    catalog.rebuild_index()
    click.echo(f"✓ {src_id} --{rel}--> {dst_id}")
    click.echo(f"  links: {rec.get('links')}")


@cmd_decisions.command("mark")
@click.argument("record_id")
@click.argument("status")
@any_caller
def cmd_decisions_mark(record_id, status) -> None:
    """改一条记录的生命周期状态(decision: adopted/superseded… · belief: falsified… · comment: resolved/promoted)。"""
    from omnicompany.packages.domains.decisions import catalog, library

    try:
        rec = library.set_status(record_id, status)
    except ValueError as e:
        click.echo(f"✗ {e}")
        return
    catalog.rebuild_index()
    click.echo(f"✓ {record_id} → status={rec.get('status')}")


@cmd_decisions.command("challenge")
@click.argument("record_id")
@click.option("--reason", "-r", required=True, help="挑战理由(什么观察/证据在质疑它)")
@click.option("--source", default="", help="证据出处(路径/URL/会话)")
@click.option("--by", "challenger", default="", help="挑战者(缺省=当前执行方)")
@any_caller
def cmd_decisions_challenge(record_id, reason, source, challenger) -> None:
    """挑战一条记录(反证优先):追加 challenge_log;belief 同时置 challenged。"""
    from omnicompany.packages.domains.decisions import catalog, library

    try:
        rec = library.challenge(record_id, reason, source=source, challenger=challenger)
    except ValueError as e:
        click.echo(f"✗ {e}")
        return
    catalog.rebuild_index()
    click.echo(f"✓ {record_id} 已记挑战 → status={rec.get('status')}")


@cmd_decisions.command("resolve")
@click.argument("record_id")
@click.argument("outcome", type=click.Choice(["supported", "partial", "falsified"]))
@click.option("--evidence", "-e", default="", help="证据(什么观察支持这个裁定)")
@click.option("--method", "-m", default="", help="验证方法(怎么验的)")
@click.option("--by", default="", help="裁定者")
@any_caller
def cmd_decisions_resolve(record_id, outcome, evidence, method, by) -> None:
    """裁定 belief 下场(supported/partial/falsified);falsified 时自动点名下游受影响记录(回传必做)。"""
    from omnicompany.packages.domains.decisions import catalog, library

    try:
        rec = library.resolve(record_id, outcome, evidence=evidence, method=method, by=by)
    except ValueError as e:
        click.echo(f"✗ {e}")
        return
    catalog.rebuild_index()
    click.echo(f"✓ {record_id} → {outcome}")
    if outcome == "falsified":
        downstream = library.impacted_by(record_id)
        if downstream:
            click.echo(f"⚠ 回传必做:{len(downstream)} 条记录 rests_on 此条,逐条复核(不自动改状态):")
            for d in downstream:
                click.echo(f"    {d.get('id','')}  {d.get('statement','')[:50]}")
        else:
            click.echo("  无下游 rests_on 依赖。")


@cmd_decisions.command("impacted")
@click.argument("record_id")
@any_caller
def cmd_decisions_impacted(record_id) -> None:
    """查哪些记录 rests_on 此条(证伪传播点名;只查直接一层,不自动改状态)。"""
    from omnicompany.packages.domains.decisions import library

    downstream = library.impacted_by(record_id)
    if not downstream:
        click.echo("(无下游 rests_on 依赖)")
        return
    for d in downstream:
        click.echo(f"  {d.get('id','')}  [{d.get('status','')}]  {d.get('statement','')[:60]}")


@cmd_decisions.command("doctor")
@click.option("--stamped", is_flag=True, help="只读写入时校验戳(快);缺省按当前规则现场重验全库")
@any_caller
def cmd_decisions_doctor(stamped) -> None:
    """列带病记录:落库校验不过(缺字段/决策没列被否决项/猜想没标风险/状态词表外)。"""
    from omnicompany.packages.domains.decisions import library

    recs = library.active_records()
    if stamped:
        bad = [(r, (r.get("validation") or {}).get("issues") or [])
               for r in recs if (r.get("validation") or {}).get("ok") is False]
    else:
        # 现场重验:规则演进后旧库存量也照新规则点名(不改库,只报告)
        bad = []
        for r in recs:
            issues = library.validate_record(r)
            if issues:
                bad.append((r, issues))
    if not bad:
        click.echo(f"✓ {len(recs)} 条记录全部合法。")
        return
    click.echo(f"⚠ {len(bad)}/{len(recs)} 条带病:")
    for r, issues in bad:
        click.echo(f"  {r.get('id','')}  {r.get('statement','')[:36]}")
        for i in issues[:5]:
            click.echo(f"      - {i}")


@cmd_decisions.command("extract-run")
@click.option("--batch", "-n", default=3, help="本次炼几个会话(小的先)")
@click.option("--model", "-m", default=None, help="便宜模型档(默认 omni 默认结构化模型;可传 gpt-5.3-codex 等)")
@click.option("--source", type=click.Choice(["claude", "codex", "all"]), default="claude",
              help="会话来源;Codex 使用原生 rollout JSONL")
@click.option("--session-id", "session_ids", multiple=True, help="只炼指定真实 session id(可多次)")
@click.option("--chunk-chars", default=24000, type=click.IntRange(4000, 48000),
              help="每次原生结构化调用的正文字符数;长会话默认 24k")
@click.option("--loop", is_flag=True, help="循环炼到 pending 清空(后台持续炼化用)")
@any_caller
def cmd_decisions_extract_run(batch, model, source, session_ids, chunk_chars, loop) -> None:
    """后台炼化存量对话:断点续跑/增量。每批炼 N 个未炼会话,带证据+严格归位,去重并库,标 checkpoint。"""
    from omnicompany.packages.domains.decisions import extract_run

    rounds = 0
    while True:
        selected = set(session_ids) or None
        res = extract_run.run_batch(limit=batch, model=model, source=source,
                                    session_ids=selected, chunk_chars=chunk_chars)
        rounds += 1
        for p in res["processed"]:
            tag = f"+{p['added']}" if "error" not in p else f"ERR {p['error']}"
            click.echo(f"  [{p['session'][:8]}] {tag}")
        click.echo(f"  本轮 {len(res['processed'])} 个,剩 {res['remaining']} 个未炼")
        if not loop or res["remaining"] == 0 or not res["processed"]:
            break
    click.echo(f"完成 {rounds} 轮。" + ("(pending 已清空)" if extract_run.status(source=source)["remaining"] == 0 else ""))


@cmd_decisions.command("extract-status")
@click.option("--source", type=click.Choice(["claude", "codex", "all"]), default="claude")
@any_caller
def cmd_decisions_extract_status(source) -> None:
    """炼化进度:已炼/未炼会话数、剩余体量、出错数。"""
    from omnicompany.packages.domains.decisions import extract_run

    s = extract_run.status(source=source)
    click.echo(f"炼化进度: 已炼 {s['done']} 个会话 · 未炼 {s['remaining']} 个(约 {s['remaining_mb']}MB)· 出错 {s['errors']} 个")
    if extract_run.PROGRESS.is_file():
        import json as _json
        try:
            p = _json.loads(extract_run.PROGRESS.read_text(encoding="utf-8"))
            click.echo(f"当前: {p.get('status')} · {str(p.get('session', ''))[:12]} · "
                       f"块 {p.get('chunk', 0)}/{p.get('chunks', 0)} · 已入库 {p.get('added', 0)}")
        except (OSError, ValueError):
            pass


@cmd_decisions.command("brief")
@click.option("--plan", default="", help="计划 id(缺省=当前会话绑定的 active_plan)")
@click.option("--project", "-p", default="", help="项目(缺省=当前会话绑定)")
@click.option("--pipeline", default="", help="管线名(拉它声明的 book_refs/when/scale)")
@click.option("--keys", default="", help="关键词,逗号分隔(条目名/结论子串硬筛)")
@click.option("--no-binding", is_flag=True, help="不读会话绑定(看全书索引)")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_decisions_brief(plan, project, pipeline, keys, no_binding, as_json) -> None:
    """开工切片:正在推进的 plan/project(缺省自动读会话绑定)该延续什么、该守什么、别踩什么。"""
    import json as _json

    from omnicompany.packages.domains.decisions.brief import build_brief, render_brief_text

    b = build_brief(plan=plan, project=project, pipeline=pipeline,
                    keys=[k for k in keys.split(",") if k.strip()],
                    use_session_binding=not no_binding)
    click.echo(_json.dumps(b, ensure_ascii=False, indent=2) if as_json else render_brief_text(b))


@cmd_decisions.command("patrol")
@click.option("--json", "as_json", is_flag=True, help="输出完整 JSON 报告")
@any_caller
def cmd_decisions_patrol(as_json) -> None:
    """决策本体巡检:双向引用完整性(手册↔库↔管线)+清场废墟检测+when 覆盖。违规时退出码 1。"""
    import json as _json

    from omnicompany.packages.domains.decisions.patrol import run_patrol

    report = run_patrol()
    if as_json:
        click.echo(_json.dumps(report, ensure_ascii=False, indent=2))
    else:
        cov = report.get("when_coverage") or {}
        click.echo(("✓ 本体巡检零违规" if report["ok"]
                    else f"⚠ 本体巡检 {report['violation_count']} 项违规"))
        for key, items in (report.get("issues") or {}).items():
            if items:
                click.echo(f"  [{key}] × {len(items)}")
                for it in items[:8]:
                    click.echo(f"      {it}")
        for r in report.get("ruins") or []:
            click.echo(f"  [清场未完成] {r['path']}  ({r['why']})")
        if "total" in cov:
            click.echo(f"  when 覆盖: {cov['with_when']}/{cov['total']} 条管线已声明"
                       f"(scale {cov['with_scale']}/{cov['total']})")
    if not report["ok"]:
        raise SystemExit(1)


@cmd_decisions.command("checklist")
@click.argument("part_stem")
@any_caller
def cmd_decisions_checklist(part_stem) -> None:
    """把手册分册渲染成检查单投影(每节≤9条;如: omni decisions checklist 10-vilo叙事)。"""
    from omnicompany.packages.domains.decisions.checklist_projection import render_checklist

    try:
        path = render_checklist(part_stem)
    except ValueError as e:
        click.echo(f"✗ {e}")
        raise SystemExit(1)
    click.echo(f"✓ 检查单投影已渲染 → {path}")


@cmd_decisions.command("knowledge")
@any_caller
def cmd_decisions_knowledge() -> None:
    """重渲 30-知识 投影(belief 按域摘要 → docs/ontology/30-知识.md;投影可重建非真源)。"""
    from omnicompany.packages.domains.decisions.knowledge_projection import (
        render_knowledge_projection,
    )

    path = render_knowledge_projection()
    click.echo(f"✓ 30-知识 投影已重渲 → {path}")


@cmd_decisions.command("reindex")
@any_caller
def cmd_decisions_reindex() -> None:
    """把当前库投影重建 index.json(供 grep / 人读)。"""
    from omnicompany.packages.domains.decisions import _paths, catalog

    res = catalog.rebuild_index()
    click.echo(f"✓ 索引重建:{res['total']} 条 → {_paths.INDEX_PATH}")


# ── 探索路径可视化 / 决策树管理(plan B7)──────────────────────────────────────

from .._access import external_or_controller  # noqa: E402


@cmd_decisions.command("graph")
@click.option("--project", "-p", default=None, help="按项目过滤(如 aigc)")
@click.option("--kind", "-k", multiple=True, help="按 record_kind 过滤(decision/belief/comment)")
@click.option("--no-deleted", is_flag=True, help="不含墓碑节点")
@click.option("--json", "as_json", is_flag=True, help="打印完整图 JSON(默认只打 stats)")
@any_caller
def cmd_decisions_graph(project, kind, no_deleted, as_json) -> None:
    """投影出探索 DAG(决策库→material-centric 图):看 stats 或导完整 JSON。"""
    import json as _json

    from omnicompany.packages.domains.decisions.exploration import projection

    g = projection.build_graph(project=project, kinds=list(kind) or None,
                               include_deleted=not no_deleted)
    if as_json:
        click.echo(_json.dumps(g, ensure_ascii=False, indent=2))
        return
    s = g["stats"]
    click.echo(f"探索图{f'·{project}' if project else '(全库)'}: "
               f"{s['n_nodes']} 节点 · {s['n_edges']} 边 · {s['n_roots']} 散落根 · "
               f"{s['n_version_chains']} 版本链 · {s['n_deleted']} 墓碑")
    click.echo(f"  按类型: {s['by_kind']}")
    click.echo(f"  按关系: {s['by_rel']}")


@cmd_decisions.command("gaps")
@click.option("--project", "-p", default="aigc", help="项目(默认 aigc)")
@any_caller
def cmd_decisions_gaps(project) -> None:
    """列探索图的真本体缺口(产物/设施/真源 注册没注册、丢没丢)。"""
    from omnicompany.packages.domains.decisions.exploration import backfill

    rows = backfill.plan_backfill(project)
    if not rows:
        click.echo(f"(项目 {project} 无登记缺口规格)")
        return
    icon = {"registered": "✓ 可注册", "lost": "✗ 已丢失", "unlocated": "? 未定位"}
    click.echo(f"探索图缺口 · {project} · {len(rows)} 项:")
    for r in rows:
        click.echo(f"  {icon.get(r['status'], r['status']):<10} [{r['kind']}] {r['label'][:34]:<34} → {r['link_to']}")


@cmd_decisions.command("backfill")
@click.option("--project", "-p", default="aigc", help="项目(默认 aigc)")
@click.option("--run", is_flag=True, help="真注册+写台账(默认 dry-run 只算)")
@external_or_controller
def cmd_decisions_backfill(project, run) -> None:
    """把缺的产物/设施/真源注册进 material registry(external_pointer,幂等),写台账。"""
    from omnicompany.packages.domains.decisions.exploration import backfill

    s = backfill.run_backfill(project=project, dry_run=not run)
    mode = "已注册" if run else "DRY-RUN(加 --run 真注册)"
    click.echo(f"回填 · {project} · {mode}:共 {s['total']} 项,{s['by_status']}")
    if run:
        click.echo(f"  注册 {s['registered']} 条 external_pointer · 台账 {s['ledger']}")


@cmd_decisions.command("versions")
@click.option("--project", "-p", default=None, help="按项目过滤")
@any_caller
def cmd_decisions_versions(project) -> None:
    """列版本链(耐用物 supersedes 串成的演化序列)。"""
    from omnicompany.packages.domains.decisions.exploration import projection

    g = projection.build_graph(project=project)
    label = {n["id"]: n.get("label") or n["id"] for n in g["nodes"]}
    chains = g["version_chains"]
    if not chains:
        click.echo("(无版本链:暂无 supersedes 边 / 同族版本号)")
        return
    click.echo(f"版本链 · {len(chains)} 条:")
    for ch in chains:
        click.echo("  " + " → ".join(label.get(i, i)[:24] for i in ch))


@cmd_decisions.command("dedup")
@click.option("--project", "-p", default=None, help="按项目过滤")
@click.option("--threshold", "-t", default=0.6, type=float, help="相似度阈值(0~1,默认 0.6)")
@any_caller
def cmd_decisions_dedup(project, threshold) -> None:
    """找近重复决策簇(同 session + 语句高相似),供去重/标『同一决策多次落定』。"""
    from omnicompany.packages.domains.decisions.exploration import manage

    clusters = manage.duplicate_clusters(project=project, threshold=threshold)
    if not clusters:
        click.echo("(没找到近重复决策簇)")
        return
    click.echo(f"近重复决策簇 · {len(clusters)} 簇:")
    for i, cl in enumerate(clusters, 1):
        click.echo(f"  簇{i}({len(cl)} 条,session {cl[0]['session_ref'][:8]}):")
        for c in cl:
            click.echo(f"    {c['id']:<22} {c['statement'][:46]}")


@cmd_decisions.command("causal")
@click.option("--project", "-p", default="aigc", help="项目(默认 aigc)")
@click.option("--model", default=None, help="覆盖模型(默认 gpt-5.5;qwen3.6-plus 更省)")
@click.option("--write", is_flag=True, help="真写因果边 sidecar(默认 dry-run)")
@external_or_controller
def cmd_decisions_causal(project, model, write) -> None:
    """从对话散文抽 refines/critiques/responds_to_critique 因果边(独立 agent,走 omni LLM 网关)。"""
    from omnicompany.packages.domains.decisions.exploration import causal_extract

    s = causal_extract.extract_for_project(project, model=model, dry_run=not write)
    mode = "已写入" if write else "DRY-RUN(加 --write 落库)"
    click.echo(f"因果抽取 · {project} · {mode}:扫 {s['sessions']} session,"
               f"得 {s['edges_found']} 边,写 {s['edges_written']} 边")
    for e in s.get("samples", []):
        click.echo(f"  {e['src']} --{e['rel']}--> {e['dst']}")


@cmd_decisions.command("consolidate")
@click.option("--project", "-p", required=True, help="按项目筛候选裁决(必填)")
@click.option("--tag", default=None, help="再按标签筛(可选)")
@click.option("--model", "-m", default=None, help="覆盖聚类模型(默认 qwen3.6-plus)")
@click.option("--submit", is_flag=True, help="把规则候选登记进内部候选流水线(信号入口②)")
@external_or_controller
def cmd_decisions_consolidate(project, tag, model, submit) -> None:
    """反向固化器 v0:已拍板高权威裁决 → 规则候选报告(L3,禁自动写规则文档,须人裁)。"""
    from omnicompany.packages.domains.decisions import consolidate

    res = consolidate.run(project=project, tag=tag, model=model, submit=submit)
    if res.get("skipped"):
        click.echo(f"(未生成报告){res['reason']}")
        return
    click.echo(f"✓ 规则候选报告: {res['report_path']}")
    click.echo(f"  候选裁决 {res['eligible_count']} 条 → 规则候选 {res['candidate_count']} 条")
    if submit:
        n_new = sum(1 for s in res.get("submitted") or [] if s.get("material_id"))
        n_dup = sum(1 for s in res.get("submitted") or [] if s.get("skipped_duplicate"))
        click.echo(f"  已登记内部候选 {n_new} 条(去重跳过 {n_dup});确认后由 candidate-apply 写回。")
    click.echo("  规则候选=L3 级,禁自动写进规则文档,须人裁。")


@cmd_decisions.command("candidate")
@click.option("--title", required=True, help="候选标题")
@click.option("--body", default="", help="markdown 草案正文(与 --file 二选一)")
@click.option("--file", "body_file", default="", help="从文件读正文")
@click.option("--source", type=click.Choice(["memory", "human"]), default="human",
              show_default=True, help="发起来源(自动信号走 candidates-scan/consolidate --submit)")
@click.option("--action", type=click.Choice(["new", "revise", "retire"]), required=True)
@click.option("--target", "targets", multiple=True, help="目标记录 id(revise/retire 必填,可多次)")
@click.option("--statement", default="", help="提议的新陈述(action=new/revise 时写回用)")
@click.option("--project", "-p", default="omnicompany", show_default=True)
@click.option("--plan-id", default="", help="来源计划 id；缺省读取当前会话绑定")
@click.option("--by", default="", help="发起者")
@any_caller
def cmd_decisions_candidate(title, body, body_file, source, action, targets, statement,
                            project, plan_id, by) -> None:
    """登记一条改陈述库的内部候选(入口④人工/AI记忆)；不混入普通审阅队列。"""
    from pathlib import Path as _P

    from omnicompany.packages.domains.decisions import candidates
    from omnicompany.packages.domains.decisions.brief import read_session_binding

    if body_file:
        body = _P(body_file).read_text(encoding="utf-8")
    if not body.strip():
        body = f"## 候选\n\n{title}\n\n(正文未附;裁决意见写在评论区)"
    proposed = {"statement": statement} if statement.strip() else {}
    source_plan_id = plan_id.strip() or read_session_binding()["plan"]
    try:
        res = candidates.submit_candidate(
            title=title, body_md=body, source=source, action=action,
            target_ids=list(targets), proposed=proposed, project=project, by=by,
            source_plan_id=source_plan_id)
    except ValueError as e:
        click.echo(f"✗ {e}")
        raise SystemExit(1)
    if res.get("skipped_duplicate"):
        click.echo(f"(已有同类 pending 候选 {res['skipped_duplicate']},未重复进队)")
    else:
        bound = f" · plan={source_plan_id}" if source_plan_id else ""
        click.echo(f"✓ 内部候选已登记(普通审阅队列不可见): {res['material_id']}{bound}")


@cmd_decisions.command("candidates-scan")
@click.option("--deviation-min", default=2, show_default=True, help="偏离聚集阈值(同一陈述≥N笔)")
@click.option("--expiry-max", default=10, show_default=True, help="到期复核单轮上限(单边化)")
@any_caller
def cmd_decisions_candidates_scan(deviation_min, expiry_max) -> None:
    """跑自动信号入口:①偏离聚集 ③到期复核 → 登记内部候选(幂等去重)。"""
    from omnicompany.packages.domains.decisions import candidates

    dev = candidates.scan_deviations(min_count=deviation_min)
    exp = candidates.scan_expiry(max_candidates=expiry_max)
    click.echo(f"✓ 偏离聚集候选 {len(dev)} 条 / 到期复核候选 {len(exp)} 条")
    for it in dev + exp:
        mark = it.get("material_id") or f"dup:{it.get('skipped_duplicate')}"
        click.echo(f"    {it.get('ref')}  → {mark}")
    st = candidates.queue_status()
    click.echo(f"  队列现状: {st['by_status']} 来源分布 {st['by_source']}")


@cmd_decisions.command("candidate-apply")
@click.option("--verified", "verified_note", default="",
              help="验证说明:目标带验证件时必须写明跑过什么(改版先过验证件)")
@click.option("--by", default="", help="执行者")
@any_caller
def cmd_decisions_candidate_apply(verified_note, by) -> None:
    """把审阅台已 accept 的候选写回陈述库(新版本+取代链;retire=状态置换,永不删除)。"""
    from omnicompany.packages.domains.decisions import candidates, catalog

    results = candidates.apply_accepted(verified_note=verified_note, by=by)
    if not results:
        click.echo("(无已 accept 待写回的候选)")
        return
    wrote = False
    for r in results:
        if r.get("skipped"):
            click.echo(f"  ⚠ {r['material_id']}: {r['reason']}")
        else:
            wrote = True
            click.echo(f"  ✓ {r['material_id']} action={r['action']} "
                       f"→ {r.get('applied_record_id') or (r.get('targets') or '')}")
    if wrote:
        catalog.rebuild_index()


@cmd_decisions.command("narrative")
@click.option("--mode", type=click.Choice(["project", "scope", "period"]), default="project",
              help="project=单领域 / scope=命名跨项目作用域 / period=时期全景")
@click.option("--project", "-p", default=None, help="A 模式的领域(如 aigc)")
@click.option("--scope", default=None, help="命名跨项目作用域(如 outward-publishing)")
@click.option("--model", default=None, help="覆盖模型(默认 gpt-5.5)")
@click.option("--max-sessions", "max_sessions", default=4, type=int, help="取料会话上限")
@click.option("--force", is_flag=True, help="忽略缓存重抽")
@click.option("--deterministic", is_flag=True, help="零模型:按真实时间和 project 生成可重建快照")
@external_or_controller
def cmd_decisions_narrative(mode, project, scope, model, max_sessions, force, deterministic) -> None:
    """提炼探索历程(连续操作流 + 主题泳道,独立 agent/gpt-5.5,带缓存)。"""
    from omnicompany.packages.domains.decisions.exploration import narrative

    res = narrative.extract_narrative(mode=mode, project=project, scope=scope, model=model,
                                      force=force, max_sessions=max_sessions,
                                      deterministic=deterministic)
    label = project or scope or ""
    click.echo(f"探索历程 · {mode}{('/' + label) if label else ''}:"
               f"{len(res.get('lanes', []))} 泳道 · {len(res.get('events', []))} 事件")
    for ln in res.get("lanes", []):
        click.echo(f"  [{ln.get('theme')}]")
    if res.get("note"):
        click.echo(f"  ⚠ {res['note']}")
    click.echo(f"  缓存: {narrative.cache_path(mode, project, scope)}")


# ── 标准化动词层(计划第三期,BLF-2026-07-04-001)────────────────────────────────

@cmd_decisions.group("verb")
def cmd_decisions_verb() -> None:
    """决策边/画布连线的标准化动词标注(默认六词:拆分/推导/联想/生成/反证/延伸,可表外)。"""


@cmd_decisions_verb.command("add")
@click.argument("src")
@click.argument("rel")
@click.argument("dst")
@click.option("--verb", "-v", required=True, help="动词(默认六词表之一,或表外自定义)")
@click.option("--rationale", "-r", default="", help="一句话:为什么是这个动作")
@click.option("--from-state", "from_state", default="", help="前置状态(schema C,可空)")
@click.option("--to-state", "to_state", default="", help="效果状态(schema C,可空)")
@click.option("--challenge", "challenges", multiple=True, help="反证挂载(可多次)")
@click.option("--annotator", default="human", help="标注者身份(幂等键的一部分,默认 human)")
@click.option("--source", "source_kind", type=click.Choice(["human", "ai"]), default="human",
              help="标注来源:human=人工/ai=模型")
@any_caller
def cmd_decisions_verb_add(src, rel, dst, verb, rationale, from_state, to_state, challenges,
                           annotator, source_kind) -> None:
    """给一条边(src --rel--> dst)标一个动词。verb 不在六词表时仍照记,提示'表外词'。"""
    from omnicompany.packages.domains.decisions import verbs

    if verbs.is_out_of_table(verb):
        click.echo(f"  ⚠ 表外词「{verb}」(允许,进统计单列;默认六词表:{'/'.join(verbs.VERBS)})")
    rec = verbs.append_annotation(
        source=src, target=dst, rel=rel, verb=verb, rationale=rationale,
        from_state=from_state, to_state=to_state, challenges=list(challenges),
        annotator=annotator, source_kind=source_kind,
    )
    click.echo(f"✓ {src} --{rel}--> {dst}  [{rec['verb']}]  by {rec['annotator']}({rec['source']})")


@cmd_decisions_verb.command("list")
@click.option("--verb", "-v", default=None, help="只看某个动词")
@any_caller
def cmd_decisions_verb_list(verb) -> None:
    """列当前态的动词标注(同边同标注者取最新)。"""
    from omnicompany.packages.domains.decisions import verbs

    recs = verbs.list_annotations(verb=verb)
    if not recs:
        click.echo("(暂无标注)" if not verb else f"(没有标「{verb}」的标注)")
        return
    click.echo(f"动词标注 · {len(recs)} 条:")
    for r in recs:
        e = r.get("edge") or {}
        click.echo(f"  {e.get('source',''):<20} --{e.get('rel',''):<12}--> {e.get('target',''):<20} "
                   f"[{r.get('verb',''):<6}] by {r.get('annotator',''):<12}({r.get('source','')})")


@cmd_decisions_verb.command("stats")
@any_caller
def cmd_decisions_verb_stats() -> None:
    """词频/per-rel 分布/human-ai 计数/表外词/边界冲突(证据列表不打分)。"""
    from omnicompany.packages.domains.decisions import verbs

    s = verbs.stats()
    click.echo(f"标注统计 · 共 {s['total']} 条(human {s['by_source'].get('human', 0)} · "
               f"ai {s['by_source'].get('ai', 0)})")
    click.echo("  词频:")
    for v, n in sorted(s["verb_freq"].items(), key=lambda kv: -kv[1]):
        tag = " (表外)" if verbs.is_out_of_table(v) else ""
        click.echo(f"    {v}{tag}: {n}")
    if s["out_of_table"]:
        click.echo(f"  表外词: {s['out_of_table']}")
    if s["conflicts"]:
        click.echo(f"  ⚠ 边界冲突 {len(s['conflicts'])} 处(同边不同标注者给了不同动词):")
        for c in s["conflicts"]:
            e = c["edge"]
            click.echo(f"    {e['source']} --{e['rel']}--> {e['target']}: {c['verbs']}")
    else:
        click.echo("  边界冲突: 无")


@cmd_decisions_verb.command("auto-annotate")
@click.option("--project", "-p", default=None, help="按项目过滤取边(默认不过滤)")
@click.option("--cap", "-n", default=100, type=int, help="本次最多标注几条边(默认 100)")
@click.option("--model", "-m", default="qwen3.6-plus", help="结构化模型(默认 qwen3.6-plus)")
@click.option("--two-pass", "two_pass", is_flag=True,
              help="同一批边用两个视角各标一遍(annotator 后缀 -p1/-p2),供边界冲突统计")
@any_caller
def cmd_decisions_verb_auto_annotate(project, cap, model, two_pass) -> None:
    """AI 半程标注器:从决策图边采样,调结构化模型选动词(六词或'其他:<词>'),断点续跑。"""
    from omnicompany.packages.domains.decisions import auto_annotate

    res = auto_annotate.run(project=project, cap=cap, model=model, two_pass=two_pass)
    click.echo(f"✓ 自动标注:采样 {res['sampled']} 条边,新写 {res['written']} 条标注"
               f"(跳过已标 {res['skipped']} 条)")
    if res.get("note"):
        click.echo(f"  ⚠ {res['note']}")
