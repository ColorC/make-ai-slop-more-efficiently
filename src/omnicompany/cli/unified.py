# [OMNI] origin=claude-code ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:cli.unified.command_hub.implementation.py"
"""omnicompany.cli.unified — 统一 CLI 命令组（基础设施）

为 omni CLI 添加执行、观测、管理类命令。
全部委托给 dispatch.py / observe.py / registry.py SDK，
自身不包含任何业务逻辑。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click


# ═══════════════════════════════════════════════════════════════════
# 终端安全输出（Windows GBK 兼容）
# ═══════════════════════════════════════════════════════════════════

_UNICODE_SAFE = {
    "\u2131": "F",      # ℱ → F
    "\u26a0": "[!]",    # ⚠ → [!]
    "\u2605": "*",      # ★ → *
    "\u2713": "Y",      # ✓ → Y
    "\u00b7": ".",      # · → .
    "\u2550": "=",      # ═ → =
    "\u2554": "+",      # ╔ → +
    "\u255a": "+",      # ╚ → +
    "\u2551": "|",      # ║ → |
    "\u2557": "+",      # ╗ → +
    "\u255d": "+",      # ╝ → +
    "\u25b6": ">",      # ▶ → >
}


def _safe_echo(text: str, **kwargs) -> None:
    """click.echo 的 Windows GBK 安全包装。"""
    for src, dst in _UNICODE_SAFE.items():
        text = text.replace(src, dst)
    try:
        click.echo(text, **kwargs)
    except UnicodeEncodeError:
        # 最后兜底：不可编码字符替换为 ?
        click.echo(text.encode("gbk", errors="replace").decode("gbk"), **kwargs)


# ═══════════════════════════════════════════════════════════════════
# 终端着色与事件格式化
# ═══════════════════════════════════════════════════════════════════

_COLORS = {
    "task.": "green",
    "agent.llm.": "cyan",
    "agent.tool.": "yellow",
    "agent.think": "white",
    "agent.state.": "magenta",
    "agent.delegate": "blue",
    "system.": "red",
}

def _color_for(event_type: str) -> str:
    for prefix, color in _COLORS.items():
        if event_type.startswith(prefix):
            return color
    return "white"

def _format_event(ev) -> str:
    """格式化 EventSummary 为终端可读行（支持节点描述、信号摘要等）。"""
    ts = ev.timestamp.split("T")[-1][:12] if "T" in ev.timestamp else ev.timestamp[-12:]
    etype = ev.event_type.ljust(22)
    source = ev.source.ljust(20)

    summary_parts: list[str] = []
    p = getattr(ev, "payload", {})

    node = p.get("node", "")
    if node:
        summary_parts.append(f"node={node}")

    desc = p.get("description", "")
    if desc:
        summary_parts.append(f'"{desc[:50]}"')

    out_sig = p.get("output_signal", {})
    if out_sig:
        sig_fmt = out_sig.get("format", "")
        sig_text = out_sig.get("text", "")[:50]
        summary_parts.append(f"[{sig_fmt}] {sig_text}")

    fc = p.get("format_check", {})
    fc_status = fc.get("status", "")
    if fc_status and fc_status != "PASS":
        summary_parts.append(click.style(f"format_check={fc_status}", fg="red"))

    out_summary = p.get("output_summary", "")
    if out_summary and not out_sig:
        summary_parts.append(out_summary[:60])

    verdict = p.get("verdict", "")
    if verdict:
        color = "green" if verdict == "pass" else ("red" if verdict == "fail" else "yellow")
        summary_parts.append(click.style(verdict, fg=color, bold=True))

    if "instruction" in p:
        summary_parts.append(f'"{p["instruction"][:60]}"')
    if "error" in p:
        summary_parts.append(click.style(f'error={p["error"][:60]}', fg="red"))

    meta = getattr(ev, "metadata", {})
    if meta:
        pt = meta.get("prompt_tokens")
        if pt:
            ct = meta.get("completion_tokens", 0)
            summary_parts.append(f"tokens={pt}+{ct}")
        cost = meta.get("cost_usd")
        if cost:
            summary_parts.append(f"${cost:.4f}")
        dur = meta.get("duration_ms")
        if dur:
            summary_parts.append(f"{dur:.0f}ms")

    summary = "  ".join(summary_parts) if summary_parts else ""

    color = _color_for(ev.event_type)
    colored_type = click.style(etype, fg=color, bold=True)
    colored_source = click.style(source, fg="bright_black")

    return f"[{ts}] {colored_type} {colored_source} {summary}"




# ═══════════════════════════════════════════════════════════════════
# 执行类命令
# ═══════════════════════════════════════════════════════════════════

@click.command("run")
@click.argument("pipeline_name")
@click.option("--input", "-i", "inputs", multiple=True,
              help="key=value 形式的输入参数（可多次指定）")
@click.option("--json-input", "-j", type=str, default=None,
              help="JSON 格式的完整输入字典")
@click.option("--db", type=str, default=None,
              help="events.db 路径覆盖")
@click.option("--max-steps", type=int, default=None,
              help="最大决策步数覆盖")
@click.option("--output", "-o", type=str, default=None,
              help="把完整 sink material 写到此文件 (str 直写 · dict 转 JSON). 用于黑盒测试/batch 消费.")
def cmd_run(pipeline_name: str, inputs: tuple, json_input: str | None,
            db: str | None, max_steps: int | None, output: str | None):
    """执行已注册的管线。

    \b
    示例:
        omni run agent "列出目录下的文件"
        omni run lap-audit -i target=src/omnicompany/runtime/runner.py
        omni run demogame-learn -j '{"table": "TavernPool"}'
    """
    from omnicompany.core.registry import discover, get_or_raise
    discover()

    # 构建输入字典
    input_dict: dict = {}
    if json_input:
        try:
            input_dict = json.loads(json_input)
        except json.JSONDecodeError as e:
            click.echo(f"错误: --json-input 不是有效 JSON: {e}", err=True)
            sys.exit(1)
    for kv in inputs:
        if "=" in kv:
            k, v = kv.split("=", 1)
            input_dict[k.strip()] = v.strip()
        else:
            # 单个位置参数作为 task
            input_dict["task"] = kv

    entry = get_or_raise(pipeline_name)
    click.echo(click.style(f"> {entry.name}", fg="cyan", bold=True)
               + f"  {entry.description}")
    click.echo(click.style(f"  domain={entry.domain}", fg="bright_black"))

    from omnicompany.core.dispatch import dispatch
    try:
        result = asyncio.run(dispatch(
            pipeline_name, input_dict,
            db_path=db, max_steps=max_steps,
        ))
        # 决定 exit code: Verdict.kind == FAIL → exit 非零 (让 CI / 黑盒 test 能抓失败)
        exit_code = 0
        from omnicompany.protocol.anchor import Verdict as _Verdict
        if isinstance(result, _Verdict):
            kind_val = getattr(result.kind, "value", result.kind)
            if kind_val == "fail":
                exit_code = 2
            payload = result.output
        else:
            payload = result

        if output:
            _write_output_to_file(payload, output)
        _print_result(result)
        if exit_code:
            sys.exit(exit_code)
    except KeyError as e:
        click.echo(click.style(f"错误: {e}", fg="red"), err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        click.echo("\n中断")
    except Exception as e:
        click.echo(click.style(f"错误: {e}", fg="red"), err=True)
        raise


def _write_output_to_file(payload, output_path: str) -> None:
    """写完整 sink 到文件 · str 直写 · dict 转 JSON. 黑盒测试 / batch 用."""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        p.write_text(payload, encoding="utf-8")
    elif isinstance(payload, (dict, list)):
        # default=str: event 引擎 result 携带 FactoryEvent 等非原生对象, 黑盒消费降级为字符串即可
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    else:
        p.write_text(str(payload), encoding="utf-8")
    click.echo(click.style(f"  → output written to {output_path}", fg="bright_black"))


@click.command("exec")
@click.argument("pipeline_name")
@click.option("--only", type=str, default=None,
              help="逗号分隔的节点 ID 列表（顺序串接执行）")
@click.option("--node", type=str, default=None,
              help="单个节点 ID（单点执行）")
@click.option("--input", "-i", "inputs", multiple=True,
              help="key=value 输入参数")
@click.option("--json-input", "-j", type=str, default=None,
              help="JSON 格式的完整输入")
@click.option("--db", type=str, default=None,
              help="events.db 路径覆盖")
def cmd_exec(pipeline_name: str, only: str | None, node: str | None,
             inputs: tuple, json_input: str | None, db: str | None):
    """自由执行：点名节点串接或单点执行。

    \b
    示例:
        omni exec demogame-learn --only schema_bootstrap,field_classifier
        omni exec demogame-learn --node benchmark_validator -j '{"schema": {...}}'
    """
    from omnicompany.core.registry import discover
    discover()

    # 确定节点列表
    if node:
        node_ids = [node]
    elif only:
        node_ids = [n.strip() for n in only.split(",") if n.strip()]
    else:
        click.echo("错误: 必须指定 --only 或 --node", err=True)
        sys.exit(1)

    # 构建输入
    input_dict: dict = {}
    if json_input:
        try:
            input_dict = json.loads(json_input)
        except json.JSONDecodeError as e:
            click.echo(f"错误: --json-input 不是有效 JSON: {e}", err=True)
            sys.exit(1)
    for kv in inputs:
        if "=" in kv:
            k, v = kv.split("=", 1)
            input_dict[k.strip()] = v.strip()

    click.echo(click.style(f"▶ exec {pipeline_name}", fg="yellow", bold=True)
               + f"  nodes={node_ids}")

    from omnicompany.core.dispatch import exec_nodes
    try:
        results = asyncio.run(exec_nodes(
            pipeline_name, node_ids, input_dict, db_path=db,
        ))
        for r in results:
            verdict_color = "green" if r["verdict_kind"] == "pass" else "red"
            click.echo(
                f"  {r['node_id']}: "
                + click.style(r["verdict_kind"], fg=verdict_color, bold=True)
                + f"  {r.get('diagnosis', '')[:80]}"
            )
    except KeyError as e:
        click.echo(click.style(f"错误: {e}", fg="red"), err=True)
        sys.exit(1)


@click.command("replay")
@click.argument("trace_id")
@click.option("--from-step", type=int, default=0,
              help="从第几步开始重放")
@click.option("--node", type=str, default=None,
              help="只重放指定节点")
@click.option("--db", type=str, default=None,
              help="events.db 路径覆盖")
@click.option("--domain", type=str, default="default",
              help="领域标识")
def cmd_replay(trace_id: str, from_step: int, node: str | None,
               db: str | None, domain: str):
    """重放历史 trace。

    \b
    示例:
        omni replay 01JQXYZ...         # 重放完整 trace
        omni replay 01JQXYZ... --from-step 5
        omni replay 01JQXYZ... --node llm
    """
    from omnicompany.core.dispatch import replay_trace
    results = asyncio.run(replay_trace(
        trace_id, from_step=from_step, only_node=node,
        db_path=db, domain=domain,
    ))

    if not results:
        click.echo(f"未找到 trace {trace_id} 的事件")
        return

    click.echo(click.style(f"Trace {trace_id[:16]}... ({len(results)} steps)", bold=True))
    for r in results:
        verdict = r.get("verdict", "")
        v_color = "green" if verdict == "pass" else ("red" if verdict == "fail" else "yellow")
        click.echo(
            f"  [{r['step']:3d}] {r['node_id']:20s} "
            + click.style(verdict or "-", fg=v_color)
            + f"  {r.get('diagnosis', '')[:60]}"
        )


# ═══════════════════════════════════════════════════════════════════
# 观测类命令
# ═══════════════════════════════════════════════════════════════════

@click.command("tail")
@click.option("--source", type=str, default=None, help="按 source 过滤")
@click.option("--type", "event_type", type=str, default=None, help="按事件类型过滤")
@click.option("--domain", type=str, default="*", help="领域过滤")
@click.option("-n", "limit", type=int, default=30, help="显示条数")
def cmd_tail(source: str | None, event_type: str | None, domain: str, limit: int):
    """查看最近的事件流。"""
    from omnicompany.core.observe import tail_events
    events = asyncio.run(tail_events(
        domain=domain, source=source, event_type=event_type, limit=limit,
    ))

    if not events:
        click.echo("暂无事件")
        return

    for ev in events:
        click.echo(_format_event(ev))


@click.command("trace")
@click.argument("trace_id")
@click.option("--domain", type=str, default="*", help="领域过滤")
def cmd_trace_view(trace_id: str, domain: str):
    """查看单条 trace 的完整事件链。"""
    from omnicompany.core.observe import read_trace
    events = asyncio.run(read_trace(trace_id, domain=domain))

    if not events:
        click.echo(f"未找到 trace {trace_id}")
        return

    click.echo(click.style(
        f"Trace: {trace_id}  ({len(events)} events)", bold=True,
    ))
    click.echo(click.style("─" * 80, fg="bright_black"))

    for ev in events:
        click.echo(_format_event(ev))
        if ev.diagnosis:
            click.echo(click.style(f"         {ev.diagnosis[:80]}", fg="bright_black"))


@click.command("traces")
@click.option("-n", "limit", type=int, default=20, help="显示条数")
@click.option("--source", type=str, default=None, help="按 source 过滤")
@click.option("--domain", type=str, default="*", help="领域过滤")
def cmd_traces(limit: int, source: str | None, domain: str):
    """列出最近的 trace。"""
    from omnicompany.core.observe import list_traces
    traces = asyncio.run(list_traces(domain=domain, n=limit, source=source))

    if not traces:
        click.echo("暂无 trace 记录")
        return

    click.echo(click.style(f"Recent traces ({len(traces)}):", bold=True))
    for t in traces:
        err_tag = click.style(" ERR", fg="red") if t.has_error else ""
        click.echo(
            f"  {t.trace_id[:20]}...  "
            f"events={t.event_count:3d}  "
            f"source={t.source.ljust(16)}  "
            f"started={t.first_ts}"
            f"{err_tag}"
        )


# ═══════════════════════════════════════════════════════════════════
# 管理类命令
# ═══════════════════════════════════════════════════════════════════

@click.command("pipelines")
@click.option("--verbose", "-v", is_flag=True, help="显示 cli_args 和节点数")
@click.option("--grep", "-g", "grep_query", type=str, default=None,
              help="按关键词过滤（匹配名称、域、描述）")
@click.option("--domain", "-d", type=str, default=None,
              help="按 domain 过滤")
def cmd_pipelines(verbose: bool, grep_query: str | None, domain: str | None):
    """列出所有已注册的管线。

    \b
    示例:
        omni pipelines                    # 列出全部
        omni pipelines -g demogame           # 按关键词搜索
        omni pipelines -d sw_verify       # 按 domain 过滤
        omni pipelines -v                 # 含节点数和参数
    """
    from omnicompany.core.registry import discover, list_all
    discover()
    entries = list_all()

    if not entries:
        click.echo("暂无已注册的管线。请在管线模块中调用 register()。")
        return

    # Filter
    if grep_query:
        q = grep_query.lower()
        entries = [e for e in entries
                   if q in e.name.lower()
                   or q in e.domain.lower()
                   or q in e.description.lower()]
    if domain:
        entries = [e for e in entries if e.domain == domain]

    if not entries:
        click.echo("无匹配的管线")
        return

    click.echo(click.style(f"Registered pipelines ({len(entries)}):", bold=True))
    for e in entries:
        # 基本信息行
        node_info = ""
        if verbose:
            try:
                pipeline = e.build_team()
                node_info = f"  {len(pipeline.nodes)} nodes"
            except Exception:
                node_info = "  ? nodes"

        click.echo(
            f"  {click.style(e.name.ljust(20), fg='cyan', bold=True)}"
            f"  domain={e.domain.ljust(12)}"
            f"{node_info}"
            f"  {e.description}"
        )

        # verbose: 展示 cli_args
        if verbose and e.cli_args:
            args_parts = []
            for arg in e.cli_args:
                if arg.required:
                    args_parts.append(f"--{arg.name} (required)")
                elif arg.default is not None:
                    args_parts.append(f"--{arg.name}={arg.default}")
                else:
                    args_parts.append(f"--{arg.name}")
            click.echo(click.style(
                f"    args: {', '.join(args_parts)}", fg="bright_black",
            ))


@click.command("nodes")
@click.option("--grep", "-g", "grep_query", type=str, default=None,
              help="按关键词过滤（匹配节点ID、Router类名、描述）")
@click.option("--format", "-f", "format_query", type=str, default=None,
              help="按 Format ID 过滤（匹配 format_in 或 format_out）")
@click.option("--pipeline", "-p", "pipeline_name", type=str, default=None,
              help="只看指定管线内的节点")
@click.option("--domain", "-d", type=str, default=None,
              help="按 domain 过滤")
def cmd_nodes(grep_query: str | None, format_query: str | None,
              pipeline_name: str | None, domain: str | None):
    """跨管线节点浏览器 — 发现所有可用节点及其 Format 签名。

    每个节点展示: 所属管线、节点ID、Format签名、Router类、是否决策节点。
    用于发现可自由组合的公开域节点。

    \b
    示例:
        omni nodes                           # 全部节点
        omni nodes -g "classifier"           # 按关键词搜索
        omni nodes -f "demogame.table_schema"   # 找所有接受此 Format 的节点
        omni nodes -p demogame-learn            # 只看某管线
        omni nodes -d demogame                  # 按域过滤
    """
    from omnicompany.core.registry import discover, list_all, get_or_raise
    from omnicompany.core.dispatch import _call_build_bindings
    discover()

    # Determine which pipelines to scan
    if pipeline_name:
        try:
            entries = [get_or_raise(pipeline_name)]
        except KeyError as e:
            click.echo(click.style(str(e), fg="red"), err=True)
            return
    else:
        entries = list_all()

    if domain:
        entries = [e for e in entries if e.domain == domain]

    # Collect all nodes across pipelines
    all_nodes: list[dict] = []
    for entry in entries:
        try:
            pipeline = entry.build_team()
        except Exception:
            continue
        try:
            bindings = _call_build_bindings(entry, {})
        except Exception:
            bindings = {}

        for node in pipeline.nodes:
            # Extract format info
            fmt_in = ""
            fmt_out = ""
            if node.anchor:
                fi = node.anchor.format_in
                fmt_in = " + ".join(fi) if isinstance(fi, list) else (fi or "")
                fmt_out = node.anchor.format_out or ""
            elif node.transformer:
                fmt_in = node.transformer.from_format or ""
                fmt_out = node.transformer.to_format or ""
            else:
                try:
                    fi = node.format_in
                    fmt_in = " + ".join(fi) if isinstance(fi, list) else (fi or "")
                    fmt_out = getattr(node, "format_out", "") or ""
                except (ValueError, AttributeError):
                    pass

            # Router info
            router = bindings.get(node.id)
            router_class = type(router).__name__ if router else "?"
            desc = getattr(router, "DESCRIPTION", "") or ""
            is_decision = getattr(node, "is_decision", False)

            all_nodes.append({
                "pipeline": entry.name,
                "domain": entry.domain,
                "node_id": node.id,
                "router_class": router_class,
                "format_in": fmt_in,
                "format_out": fmt_out,
                "description": desc,
                "is_decision": is_decision,
            })

    # Apply filters
    if grep_query:
        q = grep_query.lower()
        all_nodes = [n for n in all_nodes
                     if q in n["node_id"].lower()
                     or q in n["router_class"].lower()
                     or q in n["description"].lower()
                     or q in n["pipeline"].lower()]

    if format_query:
        q = format_query.lower()
        all_nodes = [n for n in all_nodes
                     if q in n["format_in"].lower()
                     or q in n["format_out"].lower()]

    if not all_nodes:
        click.echo("无匹配的节点")
        return

    # Group by pipeline for display
    click.echo(click.style(f"Nodes ({len(all_nodes)}):", bold=True))
    click.echo(
        f"  {'Pipeline':<18} {'Node ID':<26} {'Format':<45} {'Router':<30} {'Dec'}"
    )
    click.echo(f"  {'─' * 125}")

    current_pipeline = None
    for n in all_nodes:
        fmt_str = f"{n['format_in']} -> {n['format_out']}" if n["format_in"] else "—"
        if len(fmt_str) > 44:
            fmt_str = fmt_str[:41] + "..."
        dec = " *" if n["is_decision"] else ""
        pipe_display = n["pipeline"] if n["pipeline"] != current_pipeline else ""
        current_pipeline = n["pipeline"]
        _safe_echo(
            f"  {pipe_display:<18} {n['node_id']:<26} {fmt_str:<45} {n['router_class']:<30}{dec}"
        )

    # Format compatibility index
    if not pipeline_name and not grep_query:
        # Show unique Format IDs for discovery
        all_formats: set[str] = set()
        for n in all_nodes:
            for f in n["format_in"].split(" + "):
                if f.strip():
                    all_formats.add(f.strip())
            if n["format_out"].strip():
                all_formats.add(n["format_out"].strip())
        if all_formats:
            click.echo(click.style(f"\nFormat vocabulary ({len(all_formats)}):", bold=True))
            for f in sorted(all_formats):
                producers = sum(1 for n in all_nodes if n["format_out"] == f)
                consumers = sum(1 for n in all_nodes if f in n["format_in"])
                _safe_echo(
                    f"  {f:<40} "
                    + click.style(f"producers={producers}", fg="green")
                    + f"  consumers={consumers}"
                )


@click.command("describe")
@click.argument("pipeline_name")
@click.option("--verbose", "-v", is_flag=True, help="显示 Router 类名、INPUT_KEYS 等详情")
def cmd_describe(pipeline_name: str, verbose: bool):
    """展示管线的完整画像：参数、节点拓扑、Format、路由表。

    \b
    示例:
        omni describe demogame-learn
        omni describe debug -v
    """
    from omnicompany.core.registry import discover, get_or_raise
    discover()

    entry = get_or_raise(pipeline_name)

    # 标题
    click.echo(click.style(f"\nPipeline: {entry.name}", fg="cyan", bold=True))
    click.echo(f"  domain:      {entry.domain}")
    click.echo(f"  max_steps:   {entry.default_max_steps}")
    click.echo(f"  description: {entry.description}")

    # cli_args
    if entry.cli_args:
        click.echo(click.style("\nArguments:", bold=True))
        for arg in entry.cli_args:
            req = click.style("(required)", fg="red") if arg.required else ""
            default = f"(default: {arg.default})" if arg.default is not None else ""
            click.echo(f"  --{arg.name.ljust(16)} {arg.help}  {default}{req}")

    # DAG 内省
    try:
        pipeline = entry.build_team()
    except Exception as e:
        click.echo(click.style(f"\n  build_team() 失败: {e}", fg="red"))
        return

    try:
        bindings = entry.build_bindings({})
    except Exception:
        bindings = {}

    from omnicompany.runtime.routing.registry import RouterRegistry, format_dag_table
    snap = RouterRegistry.inspect_dag(pipeline, bindings, layer=entry.domain)
    click.echo("")
    table_str = format_dag_table(snap, verbose=verbose)
    _safe_echo(table_str)


@click.command("routers")
@click.option("--grep", "-g", "grep_query", type=str, default=None,
              help="按关键词过滤（匹配类名、模块名、docstring）")
@click.option("--format", "-f", "format_query", type=str, default=None,
              help="按 Format ID 过滤（匹配 FORMAT_IN 或 FORMAT_OUT）")
@click.option("--pipeline", "-p", "pipeline_name", type=str, default=None,
              help="只看指定管线内的 Router")
def cmd_routers(grep_query: str | None, format_query: str | None,
                pipeline_name: str | None):
    """搜索和列出所有 Router 类。

    \b
    示例:
        omni routers                     # 列出所有 Router
        omni routers --grep "分类"        # 按关键词搜索
        omni routers --format demogame      # 按 Format ID 搜索
        omni routers --pipeline debug    # 只看 debug 管线的 Router
    """
    from omnicompany.core.registry import discover
    discover()

    if pipeline_name:
        # 管线内 Router
        from omnicompany.core.registry import get_or_raise
        entry = get_or_raise(pipeline_name)
        try:
            pipeline = entry.build_team()
            bindings = entry.build_bindings({})
        except Exception as e:
            click.echo(click.style(f"加载管线失败: {e}", fg="red"))
            return

        from omnicompany.runtime.routing.registry import RouterRegistry
        snap = RouterRegistry.inspect_dag(pipeline, bindings, layer=entry.domain)

        click.echo(click.style(
            f"Routers in {pipeline_name} ({len(snap.nodes)}):", bold=True,
        ))
        click.echo(
            f"  {'Node ID':<24} {'Router Class':<30} {'Format':<35} {'Desc'}"
        )
        click.echo(f"  {'─'*100}")
        for nb in snap.nodes:
            fmt_str = f"{nb.format_in} → {nb.format_out}" if nb.format_in else "—"
            desc = nb.description[:40] if nb.description else ""
            # 过滤
            if grep_query:
                haystack = f"{nb.node_id} {nb.router_class} {nb.description}".lower()
                if grep_query.lower() not in haystack:
                    continue
            if format_query:
                if (format_query.lower() not in nb.format_in.lower()
                        and format_query.lower() not in nb.format_out.lower()):
                    continue
            dec = " *" if nb.is_decision else ""
            _safe_echo(
                f"  {nb.node_id:<24} {nb.router_class:<30} {fmt_str:<35} {desc}{dec}"
            )
        return

    # 全局 Router 类扫描
    from omnicompany.runtime.routing.registry import RouterRegistry
    all_routers = RouterRegistry.discover_router_classes()

    # 过滤
    filtered = all_routers
    if grep_query:
        q = grep_query.lower()
        filtered = [r for r in filtered
                    if q in r.class_name.lower()
                    or q in r.module.lower()
                    or q in r.docstring.lower()]

    if not filtered:
        click.echo("未找到匹配的 Router")
        return

    click.echo(click.style(
        f"Router classes ({len(filtered)}/{len(all_routers)}):", bold=True,
    ))
    click.echo(
        f"  {'Class':<35} {'Module':<45} {'Async':<6} Desc"
    )
    click.echo(f"  {'─'*110}")
    for r in filtered:
        async_mark = "Y" if r.is_async else "."
        _safe_echo(
            f"  {r.class_name:<35} {r.module:<45} {async_mark:<6} {r.docstring[:40]}"
        )

    click.echo(f"\n  Total: {len(filtered)} Router classes")


@click.command("formats")
@click.argument("pipeline_name", required=False)
def cmd_formats(pipeline_name: str | None):
    """展示管线使用的 Format 及其继承链。

    \b
    示例:
        omni formats demogame-learn      # 某管线的 Format
        omni formats                  # 全局已注册 Format
    """
    from omnicompany.core.registry import discover
    discover()

    if pipeline_name:
        from omnicompany.core.registry import get_or_raise
        entry = get_or_raise(pipeline_name)
        try:
            pipeline = entry.build_team()
        except Exception as e:
            click.echo(click.style(f"build_team() 失败: {e}", fg="red"))
            return

        # 收集管线中所有 Format ID
        format_ids: list[str] = []
        seen: set[str] = set()
        for node in pipeline.nodes:
            for fid in _node_format_ids(node):
                if fid and fid not in seen:
                    format_ids.append(fid)
                    seen.add(fid)

        click.echo(click.style(
            f"Formats used by {pipeline_name} ({len(format_ids)}):", bold=True,
        ))

        # 尝试加载 FormatRegistry 获取详情
        registry = _try_load_format_registry(entry.domain)
        if registry:
            click.echo(
                f"  {'Format ID':<30} {'Name':<20} {'Parent':<25} Tags"
            )
            click.echo(f"  {'─'*90}")
            for fid in format_ids:
                try:
                    fmt = registry.get(fid)
                    tags = ", ".join(fmt.tags) if fmt.tags else ""
                    click.echo(
                        f"  {fmt.id:<30} {fmt.name:<20} "
                        f"{(fmt.parent or '-'):<25} {tags}"
                    )
                except (KeyError, Exception):
                    click.echo(f"  {fid:<30} (not in registry)")
        else:
            # 无 registry，只列 ID
            for fid in format_ids:
                click.echo(f"  {fid}")
        return

    # 全局模式：列出所有内置 Format
    try:
        from omnicompany.protocol.format import create_builtin_registry
        registry = create_builtin_registry()
        all_formats = registry.list_all() if hasattr(registry, "list_all") else []
    except Exception:
        all_formats = []

    if not all_formats:
        click.echo("提示: 全局模式需要 FormatRegistry.list_all() 支持，"
                    "或指定管线名: omni formats <pipeline>")
        return

    click.echo(click.style(f"All registered Formats ({len(all_formats)}):", bold=True))
    for fmt in all_formats:
        tags = ", ".join(fmt.tags) if fmt.tags else ""
        click.echo(
            f"  {fmt.id:<30} {fmt.name:<20} "
            f"{(fmt.parent or '—'):<25} {tags}"
        )


@click.command("errors")
@click.option("--domain", type=str, default="*", help="领域过滤")
@click.option("-n", "limit", type=int, default=20, help="显示条数")
def cmd_errors(domain: str, limit: int):
    """列出最近的失败事件。

    \b
    示例:
        omni errors                    # 全部 domain
        omni errors --domain demogame     # 只看 demogame
        omni errors -n 5              # 最近 5 条
    """
    from omnicompany.core.observe import tail_events
    events = asyncio.run(tail_events(domain=domain, limit=limit * 5))

    # 过滤出失败事件
    errors = []
    for ev in events:
        p = ev.payload
        verdict = p.get("verdict", "")
        is_error = (
            ev.event_type in ("task.error", "system.error")
            or verdict == "fail"
            or "error" in p
        )
        if is_error:
            errors.append(ev)
        if len(errors) >= limit:
            break

    if not errors:
        click.echo("暂无错误事件")
        return

    click.echo(click.style(f"Recent errors ({len(errors)}):", bold=True))
    for ev in errors:
        p = ev.payload
        node = p.get("node", "")
        diagnosis = p.get("diagnosis", p.get("error", ""))[:80]
        verdict = p.get("verdict", ev.event_type)

        ts = ev.timestamp.split("T")[-1][:8] if "T" in ev.timestamp else ev.timestamp[-8:]
        trace_short = ev.trace_id[:16] if ev.trace_id else "?"

        click.echo(
            f"  {ts}  {trace_short}...  "
            + click.style(f"{verdict:<6}", fg="red")
            + f"  {node:<20}  {diagnosis}"
        )

    click.echo(click.style(
        "\n  提示: omni diagnose <trace_id> 查看完整诊断", fg="bright_black",
    ))


@click.command("diagnose")
@click.argument("trace_id")
@click.option("--domain", type=str, default="*", help="领域过滤")
def cmd_diagnose(trace_id: str, domain: str):
    """诊断一次失败的 trace：聚合错误、定位根因、建议修复命令。

    \b
    示例:
        omni diagnose 01JQXYZ...
    """
    from omnicompany.core.observe import read_trace

    # 支持前缀匹配
    events = asyncio.run(read_trace(trace_id, domain=domain))
    if not events:
        # 尝试前缀搜索
        full_id = _resolve_trace_prefix(trace_id, domain)
        if full_id:
            events = asyncio.run(read_trace(full_id, domain=domain))
            trace_id = full_id

    if not events:
        click.echo(f"未找到 trace {trace_id}")
        return

    # 分类事件
    all_events = events
    error_events = []
    last_pass_output = None
    node_history: dict[str, list] = {}  # node -> [events]

    for ev in all_events:
        p = ev.payload
        node = p.get("node", "")
        verdict = p.get("verdict", "")

        if node:
            node_history.setdefault(node, []).append(ev)

        if verdict == "pass":
            last_pass_output = ev

        if (verdict == "fail"
                or ev.event_type in ("task.error", "system.error")
                or "error" in p):
            error_events.append(ev)

    # 标题
    err_count = len(error_events)
    status_color = "red" if err_count > 0 else "green"
    status_text = f"FAILED ({err_count} errors)" if err_count else "OK"
    click.echo(click.style(
        f"\nTrace: {trace_id}  ({len(all_events)} events)", bold=True,
    ))
    click.echo("  Status: " + click.style(status_text, fg=status_color, bold=True))

    if not error_events:
        click.echo("  无错误事件")
        return

    # 错误详情
    click.echo(click.style("\nErrors:", bold=True))
    for ev in error_events:
        p = ev.payload
        node = p.get("node", "?")
        diagnosis = p.get("diagnosis", p.get("error", ""))[:120]
        ts = ev.timestamp.split("T")[-1][:8] if "T" in ev.timestamp else ""

        click.echo(
            f"  [{ts}] "
            + click.style(node, fg="yellow")
            + "  "
            + click.style("FAIL", fg="red", bold=True)
            + f"  {diagnosis}"
        )

    # 重试统计
    retry_nodes = {n: evs for n, evs in node_history.items()
                   if sum(1 for e in evs if e.payload.get("verdict") == "fail") > 1}
    if retry_nodes:
        click.echo(click.style("\nRetry summary:", bold=True))
        for node, evs in retry_nodes.items():
            fails = sum(1 for e in evs if e.payload.get("verdict") == "fail")
            passes = sum(1 for e in evs if e.payload.get("verdict") == "pass")
            click.echo(f"  {node}: {fails} fail, {passes} pass")

    # 建议
    click.echo(click.style("\nSuggestions:", bold=True))
    # 找到第一个失败的节点
    first_fail = error_events[0]
    fail_node = first_fail.payload.get("node", "?")
    # 找到该节点的管线来源
    source = first_fail.source or "?"
    click.echo(
        f"  1. 单节点调试:  omni exec {source} --node {fail_node} -j '<input>'"
    )
    click.echo(
        f"  2. 查看完整链:  omni trace-view {trace_id}"
    )
    if last_pass_output:
        last_node = last_pass_output.payload.get("node", "?")
        click.echo(
            f"  3. 上游正常到:  {last_node} (PASS)"
        )


def _node_format_ids(node) -> list[str]:
    """从 TeamNode 提取 format_in / format_out ID。"""
    ids = []
    if node.anchor:
        ids.append(node.anchor.format_in)
        ids.append(node.anchor.format_out)
    elif node.transformer:
        ids.append(node.transformer.from_format)
        ids.append(node.transformer.to_format)
    return ids


def _resolve_trace_prefix(prefix: str, domain: str = "*") -> str | None:
    """在 events.db 中做 trace_id 前缀匹配。"""
    import sqlite3
    from omnicompany.core.observe import _find_event_dbs
    for db_path in _find_event_dbs(domain):
        try:
            conn = sqlite3.connect(str(db_path), timeout=3.0)
            row = conn.execute(
                "SELECT trace_id FROM events WHERE trace_id LIKE ? LIMIT 1",
                (prefix + "%",),
            ).fetchone()
            conn.close()
            if row:
                return row[0]
        except Exception:
            pass
    return None


def _try_load_format_registry(domain: str):
    """尝试加载指定 domain 的 FormatRegistry（含自定义 Format）。

    Post-2026-04-07: all domains live under `packages/<domain>/`.
    """
    try:
        from omnicompany.protocol.format import create_builtin_registry
        registry = create_builtin_registry()

        import importlib
        candidates = [
            f"omnicompany.packages.{domain}.formats",
        ]
        for path in candidates:
            try:
                mod = importlib.import_module(path)
                register_fn = getattr(mod, "register_formats", None)
                if register_fn:
                    register_fn(registry)
                    break  # First match wins
            except (ImportError, AttributeError):
                continue

        return registry
    except Exception:
        return None


@click.command("health")
def cmd_health():
    """系统健康检查。"""
    from omnicompany.core.observe import health_check
    status = health_check()

    click.echo(click.style(f"System Status: {status['status']}", bold=True))
    domains = status.get("domains", {})
    if not domains:
        click.echo("  (无 data 目录或无 events.db)")
        return

    for name, info in domains.items():
        if info["status"] == "ok":
            click.echo(
                f"  {name.ljust(16)} "
                + click.style("OK", fg="green")
                + f"  events={info['event_count']:,}  "
                f"latest={info['latest_event']}  "
                f"size={info['db_size_kb']:.0f}KB"
            )
        else:
            click.echo(
                f"  {name.ljust(16)} "
                + click.style(info["status"], fg="red")
            )


# ═══════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════

def _print_result(result):
    """格式化打印管线执行结果。"""
    if isinstance(result, dict):
        click.echo(click.style("\n─── Result ───", fg="bright_black"))
        for k, v in result.items():
            if k.startswith("_"):
                continue
            val_str = str(v)[:200]
            click.echo(f"  {k}: {val_str}")
    else:
        click.echo(f"\nResult: {str(result)[:500]}")
