# [OMNI] material_id="material:cli.hr.py" origin=claude-code ts=2026-08-17T00:00:00+08:00 type=cli
# [OMNI] summary="omni hr — 归因、证据评价、Material 作者、写入 claim、PTY 协商与有界 LLM digest"
# [OMNI] why="P1-implementation-contract.md E 棒职责——给 blame.py/store.py 一个可从终端/agent 直接调用的入口"
# [OMNI] tags=hr,cli,attribution
"""omni hr —— LLM 人事系统命令组。

    看某文件是谁改的:       omni hr blame <path> [--json]
    列某 actor 动过的文件:  omni hr sessions --actor <provider:model:session_id> [--json]
    看最近事件:             omni hr events --limit 50 [--provider claude] [--kind write] [--json]
    按 actor 聚合客观计数:  omni hr stats [--defect-rate] [--json]
    记一条评价(先 blame 反查 actor): omni hr note --path <文件> --severity ... --note "..." [--actor <id>] [--json]

落盘目录固定为 `<omni_workspace_root>/data/hr/`(与 P1-implementation-contract.md
"落盘目录: omnicompany/data/hr/" 一致)。全部是只读查询命令，三档 caller 都可用。

`omni hr blame` 当前不接 git 交叉核对(`commit_index=None`)——D 棒的 `vcs_source`
在 P1 阶段尚未完成，本文件依契约不 import 尚不存在的 collector 模块；`vcs` 档的接线
留给集成阶段(见 P1-implementation-contract.md §六，由主会话做)。这不影响 `direct`/
`window`/`unknown` 三档的正确性。
"""

from __future__ import annotations

import json as json_mod
from pathlib import Path

import click

from omnicompany.core.config import omni_workspace_root

from .._access import any_caller

_TIER_LABEL = {
    "direct": "[direct·可追责]",
    "vcs": "[vcs·仅文件级,不可定位会话]",
    "window": "[window·仅线索,不得作结论]",
    "unknown": "[unknown·如实不知道]",
}


def _data_dir() -> Path:
    return omni_workspace_root() / "data" / "hr"


@click.group("hr")
def cmd_hr() -> None:
    """LLM 人事系统：离线归因 / 客观计数(见 plan.md §2.3 四档置信度)。"""


@cmd_hr.command("collect")
@click.option("--provider", type=click.Choice(["claude", "codex", "codebuddy", "grok", "kiro", "qoder"]), default=None)
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON output")
@any_caller
def hr_collect(provider: str | None, as_json: bool) -> None:
    """Incrementally collect supported CLI transcripts into the HR ledger."""
    from omnicompany.packages.services._core.hr.ingest import ingest_all

    report = ingest_all(data_dir=_data_dir(), provider=provider)
    if as_json:
        click.echo(json_mod.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return
    click.echo(
        f"collected={report.events_collected} appended={report.rows_appended} "
        f"files={report.files_seen} failed={report.files_failed} lines={report.lines_scanned}"
    )
    for source in report.sources:
        click.echo(
            f"  {source['provider']} {source['root']}: events={source['events_collected']} "
            f"appended={source['rows_appended']} coverage={source['coverage']:.3f}"
        )


@cmd_hr.command("blame")
@click.argument("path")
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON 输出(供 agent 消费)")
@any_caller
def hr_blame(path: str, as_json: bool) -> None:
    """回答"这个文件什么时候被哪个 actor 改的"，人读输出显式标注四档置信度之一。"""
    from omnicompany.packages.services._core.hr.blame import blame
    from omnicompany.packages.services._core.hr.store import read_events

    events = list(read_events(data_dir=_data_dir()))
    result = blame(path, events=events)

    if as_json:
        click.echo(json_mod.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return

    click.echo(path)
    click.echo(_TIER_LABEL.get(result.tier, f"[{result.tier}]"))
    if result.tier == "direct":
        click.echo(f"  actor: {result.actor_id}")
        click.echo(f"  ts: {result.ts}")
        click.echo(f"  evidence: {', '.join(result.evidence_refs)}")
    elif result.tier == "vcs":
        for c in result.commits:
            click.echo(
                f"  commit {c.get('commit', '?')} @ {c.get('day', '?')} "
                f"(files_changed={c.get('files_changed', '?')})"
            )
    elif result.tier == "window":
        for cand in result.candidates:
            click.echo(f"  线索候选: {cand.get('actor_id')} 窗口={cand.get('session_window')}")
    click.echo(f"  note: {result.note}")


@cmd_hr.command("material")
@click.argument("material_id")
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON output")
@any_caller
def hr_material(material_id: str, as_json: bool) -> None:
    """Show role-separated authorship for one Material ID and revision."""
    from omnicompany.packages.services._core.hr.material_authorship import (
        MaterialNotFoundError,
        get_material_authorship,
    )
    from omnicompany.packages.services._core.hr.store import read_events

    try:
        result = get_material_authorship(material_id, events=read_events(data_dir=_data_dir()))
    except MaterialNotFoundError:
        raise click.ClickException(f"unknown material_id: {material_id}") from None
    if as_json:
        click.echo(json_mod.dumps(result, ensure_ascii=False, indent=2))
        return
    click.echo(f"{result['material_id']} @ {result['revision_ref'] or 'unknown-revision'}")
    click.echo(f"  file: {result['file_path']}")
    click.echo(f"  content_author: {result['content_author']['session_ref'] or 'unknown'}")
    click.echo(f"  execution_actor: {result['execution_actor']['actor_id'] or 'unknown'}")
    click.echo(f"  maintenance_owner: {result['maintenance_owner']['actor_id'] or 'unknown'}")
    click.echo(f"  actor_reviews: {len(result['actor_reviews'])}")


@cmd_hr.command("coverage")
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON output")
@any_caller
def hr_coverage(as_json: bool) -> None:
    """Measure Material authorship coverage without blending responsibility roles."""
    from omnicompany.packages.services._core.hr.material_authorship import material_authorship_coverage
    from omnicompany.packages.services._core.hr.store import read_events

    report = material_authorship_coverage(events=read_events(data_dir=_data_dir()))
    if as_json:
        click.echo(json_mod.dumps(report, ensure_ascii=False, indent=2))
        return
    click.echo(f"materials: {report['materials_total']}")
    click.echo(
        f"  content author: {report['content_author_known']} "
        f"({report['content_author_coverage']:.1%})"
    )
    click.echo(
        f"  execution actor: {report['execution_actor_known']} "
        f"({report['execution_actor_coverage']:.1%})"
    )
    click.echo(
        f"  maintenance owner: {report['maintenance_owner_known']} "
        f"({report['maintenance_owner_coverage']:.1%})"
    )
    click.echo(
        f"  actor review linked: {report['actor_review_linked']} "
        f"({report['actor_review_coverage']:.1%})"
    )


@cmd_hr.command("evaluate")
@click.option(
    "--truth-set",
    "truth_set_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Labelled truth-set JSON (default: data/hr/truth-set.json)",
)
@click.option("--threshold", type=click.FloatRange(0.0, 1.0), default=0.95, show_default=True)
@click.option(
    "--ledger",
    is_flag=True,
    default=False,
    help="Evaluate persisted ledger rows instead of freshly reparsing labelled source files",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON output")
@any_caller
def hr_evaluate(truth_set_path: Path | None, threshold: float, ledger: bool, as_json: bool) -> None:
    """Evaluate current HR events against manually labelled transcript cohorts."""
    from omnicompany.packages.services._core.hr.store import read_events
    from omnicompany.packages.services._core.hr.truthset import (
        TruthSetError,
        collect_truth_set_sources,
        evaluate_truth_set,
        load_truth_set,
    )

    path = truth_set_path or (_data_dir() / "truth-set.json")
    try:
        manifest = load_truth_set(path)
        events = read_events(data_dir=_data_dir()) if ledger else collect_truth_set_sources(manifest)
        report = evaluate_truth_set(
            manifest,
            events,
            threshold=threshold,
        )
    except TruthSetError as exc:
        raise click.ClickException(str(exc)) from None
    if as_json:
        click.echo(json_mod.dumps(report, ensure_ascii=False, indent=2))
        return
    click.echo(f"truth set: {path}")
    for provider, row in report["providers"].items():
        precision = "n/a" if row["precision"] is None else f"{row['precision']:.1%}"
        recall = "n/a" if row["recall"] is None else f"{row['recall']:.1%}"
        click.echo(
            f"  {provider}: precision={precision} recall={recall} "
            f"tp={row['true_positive']} fp={row['false_positive']} fn={row['false_negative']} "
            f"passed={row['passed']}"
        )
    if not report["passed"]:
        raise click.ClickException("truth-set threshold not met")


@cmd_hr.command("sessions")
@click.option("--actor", required=True, help="actor_id，形如 provider:model:session_id")
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON 输出")
@any_caller
def hr_sessions(actor: str, as_json: bool) -> None:
    """列出该 actor 写入生效过的全部文件。"""
    from omnicompany.packages.services._core.hr.blame import sessions_touching
    from omnicompany.packages.services._core.hr.store import read_events

    events = list(read_events(data_dir=_data_dir()))
    try:
        targets = sessions_touching(actor, events=events)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None

    if as_json:
        click.echo(json_mod.dumps({"actor": actor, "targets": targets}, ensure_ascii=False, indent=2))
        return
    if not targets:
        click.echo(f"{actor}: 无命中记录")
        return
    for t in targets:
        click.echo(t)


@cmd_hr.command("events")
@click.option("--limit", "limit", default=50, show_default=True, help="取最近 N 条(<=0 表示不限)")
@click.option("--provider", default=None, help="按 provider 过滤")
@click.option("--kind", default=None, help="按 kind 过滤")
@click.option("--raw", is_flag=True, default=False, help="Include superseded correction rows")
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON 输出")
@any_caller
def hr_events(limit: int, provider: str | None, kind: str | None, raw: bool, as_json: bool) -> None:
    """看最近事件(过滤在流式读取时按行应用，最后按落盘顺序截取最后 N 条)。"""
    from omnicompany.packages.services._core.hr.store import read_events

    rows = list(read_events(
        data_dir=_data_dir(), provider=provider, kind=kind, current_state=not raw,
    ))
    rows = rows[-limit:] if limit > 0 else rows

    if as_json:
        click.echo(json_mod.dumps([r.to_dict() for r in rows], ensure_ascii=False, indent=2))
        return
    if not rows:
        click.echo("无事件记录")
        return
    for r in rows:
        click.echo(f"{r.ts} {r.provider} {r.kind} {r.verdict} {r.target or '(no-target)'} session={r.session_id}")


def _format_defect_row(actor_id: str, row: dict) -> str:
    rate_part = (
        f"insufficient_samples(sample_size={row['sample_size']}<{row['minimum_samples']})"
        if row["insufficient_samples"]
        else f"{row['defect_rate']}({row['defect_rate_numerator']}/{row['defect_rate_denominator']})"
    )
    return (
        f"{actor_id}: writes={row['writes']} denied={row['denied']} "
        f"stray={row['stray']} errors={row['errors']} rework={row['rework']} "
        f"defect_rate={rate_part} signal={row['signal_completeness']}"
    )


@cmd_hr.command("stats")
@click.option("--defect-rate", "defect_rate", is_flag=True, default=False,
              help="附带可拆解的客观缺陷率(denied/(writes+denied))，样本不足时标 insufficient_samples")
@click.option("--cross-provider", "cross_provider", is_flag=True, default=False,
              help="显式要求跨 provider 视图(默认按 provider 分组、不给跨 provider 排名)；"
                   "仅与 --defect-rate 同用，输出强制带不可比警告")
@click.option("--min-samples", "min_samples", default=None, type=int,
              help="覆盖默认样本量门槛(默认见 blame.DEFAULT_MINIMUM_SAMPLES)")
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON 输出")
@any_caller
def hr_stats(defect_rate: bool, cross_provider: bool, min_samples: int | None, as_json: bool) -> None:
    """按 actor 聚合客观计数(写入数/被拒数/stray 数/报错数[/缺陷率/返工数])——
    纯计数与纯算术，不做任何 LLM 评价。

    `--defect-rate` 默认按 provider 分组展示，**不给跨 provider 的统一排名**——
    各 provider 的拒绝(denial)信号采集完备度不同(见 blame.DENIAL_SIGNAL_COMPLETENESS)，
    直接横向比较会把"我们观测得够不够"读成"这个模型做得好不好"。确需跨 provider 看
    时显式加 `--cross-provider`，输出会强制带一条不可比警告，不会静默给一张排名表。

    每次算缺陷率都会顺带跑一遍 `check_completeness_table_staleness()`：如果某个
    标为 unverified/pending 的 provider 这批真实事件里出现了 denial，会提示"完备度
    表可能该复核了"——这只是发现问题，不会自动改表(verdict 归人判断)。
    """
    if cross_provider and not defect_rate:
        raise click.UsageError("--cross-provider 只能和 --defect-rate 一起用")

    from omnicompany.packages.services._core.hr.blame import (
        DEFAULT_MINIMUM_SAMPLES,
        check_completeness_table_staleness,
        compute_actor_stats,
        compute_defect_rates,
        compute_defect_rates_cross_provider,
        group_defect_rates_by_provider,
    )
    from omnicompany.packages.services._core.hr.store import read_events

    events = list(read_events(data_dir=_data_dir()))
    samples = min_samples or DEFAULT_MINIMUM_SAMPLES

    if not defect_rate:
        stats = compute_actor_stats(events)
        if as_json:
            click.echo(json_mod.dumps(stats, ensure_ascii=False, indent=2))
            return
        if not stats:
            click.echo("无事件记录")
            return
        for actor_id, row in sorted(stats.items()):
            click.echo(
                f"{actor_id}: writes={row['writes']} denied={row['denied']} "
                f"stray={row['stray']} errors={row['errors']}"
            )
        return

    staleness_warnings = check_completeness_table_staleness(events)

    if cross_provider:
        result = compute_defect_rates_cross_provider(events, minimum_samples=samples)
        result["staleness_warnings"] = staleness_warnings
        if as_json:
            click.echo(json_mod.dumps(result, ensure_ascii=False, indent=2))
            return
        click.echo(f"警告: {result['warning']}")
        for w in staleness_warnings:
            click.echo(f"完备度表提示: {w}")
        by_actor = result["by_actor"]
        if not by_actor:
            click.echo("无事件记录")
            return
        for actor_id, row in sorted(by_actor.items()):
            click.echo(_format_defect_row(actor_id, row))
        return

    # 默认: 按 provider 分组，不给跨 provider 排名
    grouped = group_defect_rates_by_provider(compute_defect_rates(events, minimum_samples=samples))
    if as_json:
        click.echo(json_mod.dumps(
            {"by_provider": grouped, "staleness_warnings": staleness_warnings},
            ensure_ascii=False, indent=2,
        ))
        return
    for w in staleness_warnings:
        click.echo(f"完备度表提示: {w}")
    if not grouped:
        click.echo("无事件记录")
        return
    for provider in sorted(grouped):
        click.echo(f"[{provider}]")
        for actor_id, row in sorted(grouped[provider].items()):
            click.echo("  " + _format_defect_row(actor_id, row))


@cmd_hr.command("note")
@click.option("--path", "path_", required=True, help="被评价的文件路径")
@click.option("--severity", required=True, type=click.Choice(["pass", "imperfect", "severe"]),
              help="pass|imperfect|severe(与 omni agent review 的 severity 词表对齐)")
@click.option("--note", "note_text", required=True, help="一句话说明")
@click.option("--actor", "actor_id", default=None,
              help="直接指定 actor_id(provider:model:session_id)，跳过 blame 反查(用户已知道是谁干的)")
@click.option("--source", required=True, type=click.Choice(["user", "agent"]),
              help="评价来源；必须显式声明，CLI 不默认冒充用户")
@click.option("--source-ref", required=True,
              help="评价来源证据，例如当前会话/消息或 agent 验收报告引用")
@click.option("--reporter", "reporter_actor_id", default="",
              help="source=agent 时必填：作出评价的 actor_id")
@click.option("--attribution-ref", "attribution_refs", multiple=True,
              help="显式 --actor 时必填，可重复：证明该 actor 归属的证据")
@click.option("--task-type", default="general", show_default=True,
              help="评价对应的任务类型；用于同任务路由反馈")
@click.option(
    "--dimension", default="quality", show_default=True,
    type=click.Choice(["quality", "correctness", "speed", "scope", "communication", "hygiene", "usability"]),
)
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON 输出")
@any_caller
def hr_note(path_: str, severity: str, note_text: str, actor_id: str | None,
            source: str, source_ref: str, reporter_actor_id: str,
            attribution_refs: tuple[str, ...], task_type: str, dimension: str,
            as_json: bool) -> None:
    """记一条评价。默认先用 blame() 反查 actor 再落盘——用户说的是"这个文件不行"，
    不是"某会话不行"，要求用户自己查是谁干的等于这条通道不存在。

    红线: 反查只到 window/unknown 档时，评价照落但不挂到猜出来的 actor 上
    (actor_id 留空)，人读输出会显式告诉你这条评价没能定位到具体会话/actor。
    """
    from omnicompany.packages.services._core.hr.note import HrNoteRejected, record_note
    from omnicompany.packages.services._core.hr.store import read_events

    events = [] if actor_id else list(read_events(data_dir=_data_dir()))
    try:
        result = record_note(
            path=path_, severity=severity, note=note_text,
            data_dir=_data_dir(), events=events, actor_id=actor_id,
            source=source, source_ref=source_ref, reporter_actor_id=reporter_actor_id,
            attribution_evidence_refs=attribution_refs,
            task_type=task_type, dimension=dimension,
        )
    except HrNoteRejected as exc:
        raise click.ClickException(str(exc)) from None

    if as_json:
        click.echo(json_mod.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return

    click.echo(f"已记录: {result.path} [{result.severity}]")
    if result.attribution_tier == "declared":
        click.echo(f"  actor(用户指定): {result.actor_id}")
    elif result.attribution_tier == "direct":
        click.echo(f"  actor(反查命中 direct 档): {result.actor_id}")
    else:
        click.echo(
            f"  [{result.attribution_tier}] 未能定位到具体会话/actor —— "
            "这条评价没有挂在任何(可能猜错的)actor 上；如需追责需要更多证据。"
        )


@cmd_hr.command("claim")
@click.option("--path", "exact_paths", multiple=True, help="声明单个文件，可重复")
@click.option("--tree", "tree_paths", multiple=True, help="声明整个目录树，可重复")
@click.option("--purpose", required=True, help="本会话在这些路径上要做什么")
@click.option("--ttl-minutes", default=240, show_default=True,
              type=click.IntRange(1, 1440), help="声明有效期；过期自动不再阻塞")
@click.option("--json", "as_json", is_flag=True, default=False)
@any_caller
def hr_claim(exact_paths: tuple[str, ...], tree_paths: tuple[str, ...], purpose: str,
             ttl_minutes: int, as_json: bool) -> None:
    """声明写入意向；与其它活跃会话重叠时拒绝并给出协商入口。"""
    from omnicompany.packages.services._core.hr.claim import (
        ClaimConflictError,
        ClaimRejected,
        create_claim,
    )
    from omnicompany.packages.services._core.identity import current_session_meta

    owner = current_session_meta()
    try:
        claim, reused = create_claim(
            data_dir=_data_dir(),
            owner=owner,
            exact_paths=exact_paths,
            tree_paths=tree_paths,
            purpose=purpose,
            cwd=Path.cwd(),
            ttl_seconds=ttl_minutes * 60,
        )
    except ClaimConflictError as exc:
        payload = exc.to_dict()
        payload["negotiation_commands"] = [
            f"omni hr negotiate {row['claim_id']} --proposal \"<how to split or when to release>\""
            for row in exc.conflicts
        ]
        if as_json:
            click.echo(json_mod.dumps(payload, ensure_ascii=False, indent=2))
        else:
            click.echo(f"写入声明冲突：{len(exc.conflicts)} 个活跃 claim", err=True)
            for row in exc.conflicts:
                click.echo(
                    f"  {row['claim_id']} owner={row['owner_trace_id']} "
                    f"purpose={row['purpose']} expires={row['expires_at']}",
                    err=True,
                )
                click.echo(
                    f"  协商: omni hr negotiate {row['claim_id']} --proposal \"<边界/释放时间>\"",
                    err=True,
                )
        raise click.exceptions.Exit(2)
    except ClaimRejected as exc:
        raise click.ClickException(str(exc)) from None

    payload = {**claim.to_dict(), "idempotent_reuse": reused}
    if as_json:
        click.echo(json_mod.dumps(payload, ensure_ascii=False, indent=2))
        return
    click.echo(
        f"claim active: {claim.id} targets={len(claim.targets)} expires={claim.expires_at}"
        + (" (existing)" if reused else "")
    )


@cmd_hr.command("claims")
@click.option("--all", "include_inactive", is_flag=True, default=False,
              help="包含 released/expired 的最后状态")
@click.option("--mine", is_flag=True, default=False, help="只看当前会话")
@click.option("--json", "as_json", is_flag=True, default=False)
@any_caller
def hr_claims(include_inactive: bool, mine: bool, as_json: bool) -> None:
    """查看当前写入意向，不把过期声明当冲突。"""
    from omnicompany.packages.services._core.hr.claim import read_claims
    from omnicompany.packages.services._core.identity import current_session_meta

    rows = read_claims(data_dir=_data_dir(), include_inactive=include_inactive)
    if mine:
        trace_id = str(current_session_meta().get("trace_id") or "")
        rows = [row for row in rows if row.owner_trace_id == trace_id]
    payload = [row.to_dict() for row in rows]
    if as_json:
        click.echo(json_mod.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not rows:
        click.echo("no write claims")
        return
    for row in rows:
        click.echo(
            f"{row.id} [{row.status}] owner={row.owner_trace_id} "
            f"targets={len(row.targets)} expires={row.expires_at} purpose={row.purpose}"
        )


@cmd_hr.command("release")
@click.argument("claim_id")
@click.option("--reason", default="completed", show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
@any_caller
def hr_release(claim_id: str, reason: str, as_json: bool) -> None:
    """由 owner 释放写入声明；释放以 append-only 状态写回账本。"""
    from omnicompany.packages.services._core.hr.claim import ClaimRejected, release_claim
    from omnicompany.packages.services._core.identity import current_session_meta

    owner = current_session_meta()
    try:
        row = release_claim(
            claim_id,
            data_dir=_data_dir(),
            owner_trace_id=str(owner.get("trace_id") or ""),
            reason=reason,
        )
    except ClaimRejected as exc:
        raise click.ClickException(str(exc)) from None
    if as_json:
        click.echo(json_mod.dumps(row.to_dict(), ensure_ascii=False, indent=2))
    else:
        click.echo(f"claim released: {row.id} reason={row.release_reason}")


@cmd_hr.command("renew")
@click.argument("claim_id")
@click.option("--ttl-minutes", default=240, show_default=True, type=click.IntRange(1, 1440))
@click.option("--json", "as_json", is_flag=True, default=False)
@any_caller
def hr_renew(claim_id: str, ttl_minutes: int, as_json: bool) -> None:
    """由 owner 显式续租，避免永久僵尸 claim。"""
    from omnicompany.packages.services._core.hr.claim import ClaimRejected, renew_claim
    from omnicompany.packages.services._core.identity import current_session_meta

    owner = current_session_meta()
    try:
        row = renew_claim(
            claim_id,
            data_dir=_data_dir(),
            owner_trace_id=str(owner.get("trace_id") or ""),
            ttl_seconds=ttl_minutes * 60,
        )
    except ClaimRejected as exc:
        raise click.ClickException(str(exc)) from None
    if as_json:
        click.echo(json_mod.dumps(row.to_dict(), ensure_ascii=False, indent=2))
    else:
        click.echo(f"claim renewed: {row.id} expires={row.expires_at}")


@cmd_hr.command("negotiate")
@click.argument("claim_id")
@click.option("--proposal", required=True, help="边界拆分、让路顺序或预计释放时间")
@click.option("--json", "as_json", is_flag=True, default=False)
@any_caller
def hr_negotiate(claim_id: str, proposal: str, as_json: bool) -> None:
    """通过现有 session message/PTY 通道向 claim owner 发结构化协商请求。"""
    from omnicompany.core.session_messages import send_message
    from omnicompany.packages.services._core.hr.claim import (
        ClaimRejected,
        build_negotiation_request,
        read_claims,
    )
    from omnicompany.packages.services._core.identity import current_session_meta

    owner = current_session_meta()
    held = next(
        (row for row in read_claims(data_dir=_data_dir()) if row.id == claim_id),
        None,
    )
    if held is None:
        raise click.ClickException(f"active claim not found: {claim_id}")
    if held.owner_trace_id == str(owner.get("trace_id") or ""):
        raise click.ClickException("cannot negotiate with your own claim; use release/renew")
    target = held.owner_pty_id or held.owner_session_id or held.owner_trace_id
    conflict = {
        "claim_id": held.id,
        "matches": held.targets,
    }
    try:
        message = build_negotiation_request(requester=owner, conflict=conflict, proposal=proposal)
        result = send_message(target, message, from_topic=str(owner.get("topic") or "claim negotiation"))
    except (ClaimRejected, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from None
    if as_json:
        click.echo(json_mod.dumps(result, ensure_ascii=False, indent=2))
    else:
        click.echo(
            f"negotiation sent: claim={claim_id} target={target} message_id={result.get('id')} "
            f"delivery={result.get('delivery', {}).get('mode')}"
        )


@cmd_hr.command("digest")
@click.option("--actor", "actor_id", required=True, help="精确 actor_id: provider:model:session")
@click.option("--task-type", default=None, help="只聚合同 task_type 互评；客观事件仍完整保留")
@click.option("--model", default="deepseek-v4.1-flash", show_default=True,
              help="必须是 agent catalog 中的 T4 模型")
@click.option("--packet-only", is_flag=True, default=False,
              help="只输出确定性证据包，不调用 LLM、不写 digest 账本")
@click.option("--refresh", is_flag=True, default=False,
              help="忽略相同 packet+model 的缓存，重新调用并追加一条 digest")
@click.option("--json", "as_json", is_flag=True, default=False)
@any_caller
def hr_digest(actor_id: str, task_type: str | None, model: str, packet_only: bool,
              refresh: bool, as_json: bool) -> None:
    """用 T4 模型把有界证据整理成人读画像；不生成总分、排名或隐式互评。"""
    from omnicompany.packages.services._core.agent.resource_control import ResourceControl
    from omnicompany.packages.services._core.hr.digest import (
        append_digest,
        build_digest_packet,
        cached_digest,
    )
    from omnicompany.packages.services._core.hr.note import read_notes
    from omnicompany.packages.services._core.hr.store import read_events
    from omnicompany.packages.services._core.hr.workers.digest import HrDigestWorker
    from omnicompany.packages.services._core.registry import get_material_id_index
    from omnicompany.protocol.anchor import VerdictKind

    control = ResourceControl()
    model_row = next((row for row in control.models() if row.get("id") == model), None)
    if model_row is None or model_row.get("tier") != "T4":
        raise click.ClickException("--model must name a registered T4 model")
    try:
        packet = build_digest_packet(
            actor_id,
            events=read_events(data_dir=_data_dir()),
            notes=read_notes(data_dir=_data_dir()),
            peer_reviews=control.reviews(),
            task_type=task_type,
            material_index=get_material_id_index(),
            project_root=omni_workspace_root(),
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None

    if packet_only:
        if as_json:
            click.echo(json_mod.dumps(packet, ensure_ascii=False, indent=2))
        else:
            completeness = packet["signal_completeness"]
            click.echo(f"packet={packet['packet_fingerprint']} actor={actor_id}")
            click.echo(
                f"  evidence={len(packet['allowed_evidence_refs'])} "
                f"materials={completeness['material_links']} "
                f"user_feedback={completeness['user_feedback_rows']} "
                f"peer_reviews={completeness['peer_review_rows']}"
            )
            click.echo("  speed: insufficient (queue/pause/external-wait time is not separable)")
        return

    record = None if refresh else cached_digest(
        data_dir=_data_dir(),
        packet_fingerprint=packet["packet_fingerprint"],
        model=model,
    )
    was_cached = record is not None
    if record is None:
        verdict = HrDigestWorker(model=model).run({"packet": packet})
        if verdict.kind != VerdictKind.PASS:
            raise click.ClickException(verdict.diagnosis or "HR digest worker failed")
        record = append_digest(
            data_dir=_data_dir(), packet=packet, model=model, digest=verdict.output["digest"]
        )

    payload = {
        "cached": was_cached,
        "record": record.to_dict(),
        "signal_completeness": packet["signal_completeness"],
    }
    if as_json:
        click.echo(json_mod.dumps(payload, ensure_ascii=False, indent=2))
        return
    digest = record.digest
    click.echo(f"digest={record.id} model={record.model}" + (" (cached)" if was_cached else ""))
    click.echo(f"  {digest['summary']}")
    click.echo(
        f"  strengths={len(digest['strength_signals'])} risks={len(digest['risk_signals'])} "
        f"review_reminders={len(digest['review_reminders'])}"
    )
    click.echo(f"  speed={digest['speed_assessment']['status']}: {digest['speed_assessment']['statement']}")
    click.echo(f"  conclusion={digest['overall_conclusion']} (no score or ranking)")


@cmd_hr.command("amend-review")
@click.option("--actor", "actor_id", required=True,
              help="执行纠正的维护 actor；不会伪装成原 reviewer")
@click.option("--supersede", "superseded_review_ids", multiple=True, required=True,
              help="被后续纠正取代的原 review id；可重复")
@click.option("--replacement", "replacement_review_id", required=True,
              help="账本中已经存在的纠正 review id")
@click.option("--reason", required=True, help="为何两条记录属于同一发现且后者纠正前者")
@click.option("--evidence", multiple=True, required=True, help="纠正裁决证据；可重复")
@click.option("--json", "as_json", is_flag=True, default=False)
@any_caller
def hr_amend_review(
    actor_id: str,
    superseded_review_ids: tuple[str, ...],
    replacement_review_id: str,
    reason: str,
    evidence: tuple[str, ...],
    as_json: bool,
) -> None:
    """追加互评纠正关系；保留原始历史，矩阵、画像与路由只读取当前态。"""
    from omnicompany.packages.services._core.agent.resource_control import ResourceControl

    try:
        row = ResourceControl().record_review_amendment(
            actor_id=actor_id,
            superseded_review_ids=list(superseded_review_ids),
            replacement_review_id=replacement_review_id,
            reason=reason,
            evidence=list(evidence),
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    if as_json:
        click.echo(json_mod.dumps(row, ensure_ascii=False, indent=2))
        return
    suffix = " (deduplicated)" if row.get("deduplicated") else ""
    click.echo(
        f"review_amendment={row['id']}{suffix} replacement={row['replacement_review_id']} "
        f"superseded={len(row['superseded_review_ids'])}"
    )


@cmd_hr.command("feedback")
@click.option("--actor", "actor_id", default=None, help="按 actor_id 过滤")
@click.option("--source", type=click.Choice(["user", "agent"]), default=None)
@click.option("--severity", type=click.Choice(["pass", "imperfect", "severe"]), default=None)
@click.option("--limit", default=50, show_default=True, type=int, help="最近 N 条；<=0 表示不限")
@click.option("--json", "as_json", is_flag=True, default=False)
@any_caller
def hr_feedback(actor_id: str | None, source: str | None, severity: str | None,
                limit: int, as_json: bool) -> None:
    """查询有来源证据的用户/agent 评价；计数不自动转换成质量分。"""
    from omnicompany.packages.services._core.hr.note import read_notes, summarize_notes

    rows = [
        item for item in read_notes(data_dir=_data_dir())
        if (not actor_id or item.actor_id == actor_id)
        and (not source or item.source == source)
        and (not severity or item.severity == severity)
    ]
    if limit > 0:
        rows = rows[-limit:]
    payload = {"summary": summarize_notes(rows), "rows": [item.to_dict() for item in rows]}
    if as_json:
        click.echo(json_mod.dumps(payload, ensure_ascii=False, indent=2))
        return
    click.echo(
        f"feedback={payload['summary']['total']} "
        f"attributed={payload['summary']['attributed_total']} "
        f"unattributed={payload['summary']['unattributed']['total']}"
    )
    for item in rows:
        click.echo(
            f"{item.ts} [{item.severity}] source={item.source} "
            f"actor={item.actor_id or 'unknown'} path={item.path} id={item.id}"
        )


@cmd_hr.command("feedback-backfill")
@click.option(
    "--transcript", "transcripts", multiple=True, required=True,
    type=click.Path(path_type=Path, exists=True),
    help="单个 CLI transcript 或目录；可重复。默认只审计，不写账本。",
)
@click.option("--apply", "apply_rows", is_flag=True, default=False,
              help="追加高置信评价；按原消息 source_ref/fingerprint 幂等")
@click.option("--json", "as_json", is_flag=True, default=False)
@any_caller
def hr_feedback_backfill(transcripts: tuple[Path, ...], apply_rows: bool, as_json: bool) -> None:
    """从根 CLI 会话的真实用户消息回填评价；模型互评不参与此步骤。"""
    from omnicompany.packages.services._core.hr.historical_feedback import (
        apply_historical_feedback,
        scan_history,
    )

    candidates, reports = scan_history(transcripts)
    records = []
    duplicate_count = 0
    skipped_count = sum(not item.auto_applicable for item in candidates)
    if apply_rows:
        records, duplicate_count, skipped_count = apply_historical_feedback(
            candidates, data_dir=_data_dir()
        )
    payload = {
        "mode": "apply" if apply_rows else "audit",
        "reports": reports,
        "candidate_count": len(candidates),
        "auto_applicable_count": sum(item.auto_applicable for item in candidates),
        "applied_count": len(records),
        "duplicate_count": duplicate_count,
        "skipped_count": skipped_count,
        "evidence_policy": {
            "primary": "user_feedback",
            "auxiliary": "peer_review",
            "rule": "peer review cannot override explicit user feedback",
        },
        "candidates": [item.to_dict() for item in candidates],
        "records": [item.to_dict() for item in records],
    }
    if as_json:
        click.echo(json_mod.dumps(payload, ensure_ascii=False, indent=2))
        return
    click.echo(
        f"historical_feedback mode={payload['mode']} candidates={len(candidates)} "
        f"auto_applicable={payload['auto_applicable_count']} applied={len(records)} "
        f"duplicates={duplicate_count}"
    )
    for item in candidates:
        click.echo(
            f"[{item.severity}/{item.dimension}] scope={item.target_scope} "
            f"actor={item.actor_id or 'unattributed'} source={item.source_ref}"
        )


@cmd_hr.command("interaction-backfill")
@click.option(
    "--transcript", "transcripts", multiple=True, required=True,
    type=click.Path(path_type=Path, exists=True),
    help="单个原生 transcript 或目录；保留每个真实用户回合。",
)
@click.option("--apply", "apply_rows", is_flag=True, default=False,
              help="把信号幂等追加到 interaction_signals.jsonl；默认只审计")
@click.option("--json", "as_json", is_flag=True, default=False)
@any_caller
def hr_interaction_backfill(
    transcripts: tuple[Path, ...], apply_rows: bool, as_json: bool,
) -> None:
    """提取用户显式/隐式反应；普通需求保留为中性上下文，不伪装成通过。"""
    from omnicompany.packages.services._core.hr.evaluation_signals import (
        append_interaction_signals,
        scan_interaction_history,
        summarize_interaction_signals,
    )

    signals, reports = scan_interaction_history(transcripts)
    appended = duplicates = 0
    if apply_rows:
        appended, duplicates = append_interaction_signals(signals, data_dir=_data_dir())
    payload = {
        "mode": "apply" if apply_rows else "audit",
        "summary": summarize_interaction_signals(row.to_dict() for row in signals),
        "reports": reports,
        "appended": appended,
        "duplicates": duplicates,
    }
    if as_json:
        click.echo(json_mod.dumps(payload, ensure_ascii=False, indent=2))
        return
    summary = payload["summary"]
    click.echo(
        f"interaction_signals mode={payload['mode']} turns={summary['observed_user_turns']} "
        f"scored={summary['scored_user_reactions']} neutral={summary['neutral_user_turns']} "
        f"unattributed={summary['unattributed_user_turns']} appended={appended} "
        f"duplicates={duplicates}"
    )


@cmd_hr.command("amend-feedback")
@click.option("--note-id", required=True)
@click.option("--actor", "actor_id", required=True, help="执行更正的维护 actor，不改变原评价来源")
@click.option("--task-type", default=None)
@click.option("--dimension", default=None,
              type=click.Choice(["quality", "correctness", "speed", "scope", "communication", "hygiene", "usability"]))
@click.option("--severity", default=None, type=click.Choice(["pass", "imperfect", "severe"]))
@click.option("--reason", required=True)
@click.option("--evidence", "evidence_refs", multiple=True, required=True)
@click.option("--json", "as_json", is_flag=True, default=False)
@any_caller
def hr_amend_feedback(
    note_id: str, actor_id: str, task_type: str | None, dimension: str | None,
    severity: str | None, reason: str, evidence_refs: tuple[str, ...], as_json: bool,
) -> None:
    """追加评价分类更正；原始用户文本、source_ref 和归因保持不变。"""
    from omnicompany.packages.services._core.hr.note import (
        HrNoteRejected,
        record_note_amendment,
    )

    changes = {
        key: value for key, value in {
            "task_type": task_type, "dimension": dimension, "severity": severity,
        }.items() if value is not None
    }
    try:
        row = record_note_amendment(
            note_id=note_id, actor_id=actor_id, changes=changes, reason=reason,
            evidence_refs=evidence_refs, data_dir=_data_dir(),
        )
    except HrNoteRejected as exc:
        raise click.ClickException(str(exc)) from None
    if as_json:
        click.echo(json_mod.dumps(row.to_dict(), ensure_ascii=False, indent=2))
    else:
        click.echo(f"feedback amendment={row.id} note={row.note_id} changes={row.changes}")


@cmd_hr.command("actor")
@click.argument("actor_id")
@click.option("--min-samples", default=None, type=int)
@click.option("--json", "as_json", is_flag=True, default=False)
@any_caller
def hr_actor(actor_id: str, min_samples: int | None, as_json: bool) -> None:
    """汇合某 actor 的客观事件、用户反馈和去重互评，不生成总榜。"""
    from omnicompany.packages.services._core.hr.blame import DEFAULT_MINIMUM_SAMPLES
    from omnicompany.packages.services._core.hr.note import read_notes
    from omnicompany.packages.services._core.hr.profile import build_actor_profile
    from omnicompany.packages.services._core.hr.store import read_events

    from omnicompany.packages.services._core.agent.resource_control import ResourceControl

    try:
        payload = build_actor_profile(
            actor_id,
            events=read_events(data_dir=_data_dir()),
            notes=read_notes(data_dir=_data_dir()),
            peer_reviews=ResourceControl().reviews(),
            minimum_samples=min_samples or DEFAULT_MINIMUM_SAMPLES,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    if as_json:
        click.echo(json_mod.dumps(payload, ensure_ascii=False, indent=2))
        return
    click.echo(actor_id)
    objective = payload["objective"]
    if objective is None:
        click.echo("  objective: no matching event samples")
    else:
        click.echo("  objective: " + _format_defect_row(actor_id, objective))
    feedback = payload["feedback"]["summary"]
    reviews = payload["peer_reviews"]["summary"]
    click.echo(
        f"  feedback: total={feedback['total']} pass={feedback['pass']} "
        f"imperfect={feedback['imperfect']} severe={feedback['severe']}"
    )
    click.echo(
        f"  peer_reviews: total={reviews['total']} pass={reviews['pass']} "
        f"imperfect={reviews['imperfect']} severe={reviews['severe']}"
    )
    click.echo(f"  targets: {payload['targets']['count']}")
    click.echo("  quality_score: withheld (inspect evidence channels above)")


@cmd_hr.command("evaluate-models")
@click.option(
    "--family",
    "families",
    multiple=True,
    help="只看指定模型家族，可重复；默认 grok/gpt/glm/kimi/deepseek/claude/qwen",
)
@click.option("--min-work-samples", default=3, show_default=True, type=click.IntRange(1, None))
@click.option("--min-user-samples", default=1, show_default=True, type=click.IntRange(1, None))
@click.option("--min-peer-samples", default=3, show_default=True, type=click.IntRange(1, None))
@click.option(
    "--min-comparable-samples", default=3, show_default=True, type=click.IntRange(1, None),
    help="同一模型×任务进入正式横评所需的用户评价数；不足时仍显示暂定分与补样缺口",
)
@click.option(
    "--report",
    "report_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="同时写出 Markdown 报告",
)
@click.option(
    "--json-out",
    "json_out_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="把完整证据矩阵写成 JSON 快照",
)
@click.option(
    "--html-out",
    "html_out_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="写出自包含 HTML 审阅报告",
)
@click.option("--json", "as_json", is_flag=True, default=False)
@any_caller
def hr_evaluate_models(
    families: tuple[str, ...],
    min_work_samples: int,
    min_user_samples: int,
    min_peer_samples: int,
    min_comparable_samples: int,
    report_path: Path | None,
    json_out_path: Path | None,
    html_out_path: Path | None,
    as_json: bool,
) -> None:
    """按模型×任务汇总真实工作、用户评价和辅助互评，不输出跨任务总榜。"""
    from omnicompany.dashboard.ccdaemon.api import read_meta_store
    from omnicompany.packages.services._core.agent.resource_control import ResourceControl
    from omnicompany.packages.services._core.hr.model_evaluation import (
        DEFAULT_FAMILIES,
        build_model_evaluation,
        render_model_evaluation_html,
        render_model_evaluation_markdown,
    )
    from omnicompany.packages.services._core.hr.evaluation_signals import read_interaction_signals
    from omnicompany.packages.services._core.hr.note import read_notes
    from omnicompany.packages.services._core.hr.store import read_events

    control = ResourceControl()
    try:
        session_registry = read_meta_store(use_cache=False)
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"cannot read session registry: {exc}") from None
    selected_families = tuple(dict.fromkeys(families or DEFAULT_FAMILIES))
    payload = build_model_evaluation(
        events=read_events(data_dir=_data_dir()),
        notes=read_notes(data_dir=_data_dir()),
        peer_reviews=control.reviews(),
        interaction_signals=read_interaction_signals(data_dir=_data_dir()),
        session_registry=session_registry,
        catalog_models=control.models(),
        requested_families=selected_families,
        minimum_work_samples=min_work_samples,
        minimum_user_samples=min_user_samples,
        minimum_peer_samples=min_peer_samples,
        minimum_comparable_samples=min_comparable_samples,
    )
    markdown = render_model_evaluation_markdown(payload)
    if report_path is not None:
        destination = report_path.resolve(strict=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(markdown, encoding="utf-8")
        payload["report_path"] = str(destination)
    if html_out_path is not None:
        destination = html_out_path.resolve(strict=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(render_model_evaluation_html(payload), encoding="utf-8")
        payload["html_path"] = str(destination)
    if json_out_path is not None:
        destination = json_out_path.resolve(strict=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload["json_path"] = str(destination)
        destination.write_text(
            json_mod.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if as_json:
        click.echo(json_mod.dumps(payload, ensure_ascii=False, indent=2))
        return
    if report_path is not None or json_out_path is not None or html_out_path is not None:
        click.echo(
            f"report={payload.get('report_path', '-')} html={payload.get('html_path', '-')} "
            f"json={payload.get('json_path', '-')} "
            f"models={len(payload['models'])} "
            f"events={payload['coverage']['event_rows']} "
            f"user_feedback={payload['coverage']['user_feedback_rows']}"
        )
        return
    click.echo(markdown)


__all__ = ["cmd_hr"]
