# [OMNI] origin=human ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:cli.commands.trace_signal_flow_rebuilder.implementation.py"
"""omni trace <trace_id> - 重建完整 Signal 流。"""
import json
import click
from ..db import open_db, resolve_db, fmt_time, fmt_bool, truncate, type_ids


@click.command("trace")
@click.argument("trace_id")
@click.option("--db", default=None, help="DB 路径")
@click.option("--verbose", "-v", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
def cmd_trace(trace_id: str, db: str | None, verbose: bool, as_json: bool):
    """查看一次 trace 的完整 Signal 流。trace_id 可以是前缀。"""
    conn = open_db(resolve_db(db))

    # 前缀匹配
    row = conn.execute(
        "SELECT trace_id FROM routing_events WHERE trace_id LIKE ? LIMIT 1",
        (trace_id + "%",)
    ).fetchone()
    if not row:
        click.echo(f"trace not found: {trace_id}")
        return
    full_id = row["trace_id"]

    # routing_event
    re = conn.execute(
        "SELECT * FROM routing_events WHERE trace_id=? ORDER BY id LIMIT 1", (full_id,)
    ).fetchone()

    # signal_spans
    spans = conn.execute(
        "SELECT * FROM signal_spans WHERE trace_id=? ORDER BY span_index", (full_id,)
    ).fetchall()

    if as_json:
        click.echo(json.dumps({
            "trace_id": full_id,
            "routing_event": dict(re) if re else None,
            "signal_spans": [dict(s) for s in spans],
        }, ensure_ascii=False, indent=2))
        return

    click.echo(f"\n{'='*60}")
    click.echo(f"  TRACE  {full_id}")
    click.echo(f"{'='*60}")
    if re:
        click.echo(f"  task   : {truncate(re['task_desc'], 100)}")
        click.echo(f"  routed : {fmt_bool(re['route_found'])}   agent_ok: {fmt_bool(re['agent_success'])}")
        click.echo(f"  time   : {fmt_time(re['created_at'])}")
        click.echo(f"  input  : {type_ids(re['input_types'])}")
        click.echo(f"  target : {type_ids(re['target_types'])}")

    if not spans:
        click.echo("\n  [no signal_spans - run migration then restart to collect data]")
        if re and re["path_nodes"]:
            click.echo(f"\n  path_nodes (legacy): {re['path_nodes']}")
        return

    click.echo(f"\n  {len(spans)} spans:\n")
    for sp in spans:
        status = "ok" if sp["success"] else "FAIL"
        lat = f"{sp['latency_ms']:.0f}ms" if sp["latency_ms"] else "?"
        click.echo(f"  [{sp['span_index']}] {status} {sp['node_id'][:32]:<32}  {sp['impl_kind'] or '?':12} {lat}")
        click.echo(f"       {truncate(sp['node_desc'], 60)}")
        if verbose:
            if sp["input_format"]:
                click.echo(f"       in_fmt : {sp['input_format']}")
            click.echo(f"       input  : {truncate(sp['input_text'], 120)}")
            if sp["success"]:
                if sp["output_format"]:
                    click.echo(f"       out_fmt: {sp['output_format']}")
                click.echo(f"       output : {truncate(sp['output_text'], 120)}")
            else:
                click.echo(f"       ERROR  : {truncate(sp['error_text'], 120)}")
        click.echo()
