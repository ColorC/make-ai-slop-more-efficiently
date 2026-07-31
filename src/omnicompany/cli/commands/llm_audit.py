# [OMNI] origin=claude-code domain=cli/llm_audit ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:cli.audit.llm_call_and_pipeline_query.implementation.py"
"""omni llm audit — 查询 LLM 调用档案 (Phase 2.5 产出).

档案位置: `data/llm_audit/<date>/<trace_id>.jsonl`
每条记录包含: trace_id / node_id / pipeline_id / role / model / caller /
system_prompt / messages / response_text / tokens / info_audit / ...

使用示例:
    # 按 trace_id 查某次 pipeline run 的所有 LLM 调用
    omni llm audit --trace-id 01KNS1TVVRWRDE7HJ90FRX5D8R

    # 按 pipeline + 节点过滤, 最近 5 条
    omni llm audit --pipeline workflow-factory --node req_analyzer --last 5

    # 全文关键词搜索 (system_prompt + response_text)
    omni llm audit --grep "Schema"

    # 按起始日期
    omni llm audit --since 2026-04-09

    # 输出格式: 默认摘要, --full 完整 prompt/response, --json 原始 JSONL
    omni llm audit --trace-id xxx --full
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import click

from omnicompany.core.config import resolve_runtime_data_dir


def _audit_root() -> Path:
    """llm_audit 根目录: data/_runtime/llm_audit/ (2026-04-21 B4 迁移).

    原路径 data/llm_audit/ 违反 archmap.yaml data.forbid_new_subdirs.
    与 runtime/info_audit/audit_store.py::_audit_root() 保持一致.
    """
    return resolve_runtime_data_dir("llm_audit")


def _iter_records(
    *,
    trace_id: str | None,
    pipeline: str | None,
    node: str | None,
    grep: str | None,
    since: str | None,
    last: int | None,
) -> list[dict[str, Any]]:
    """扫描 llm_audit 目录, 按过滤条件倒序收集记录。"""
    root = _audit_root()
    if not root.exists():
        return []

    # 日期过滤
    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.strptime(since, "%Y-%m-%d")
        except ValueError:
            raise click.BadParameter(f"--since 需为 YYYY-MM-DD 格式, 得到: {since}")

    # 按日期倒序
    day_dirs = sorted(
        [p for p in root.iterdir() if p.is_dir()],
        reverse=True,
    )
    if since_dt:
        day_dirs = [
            d
            for d in day_dirs
            if _parse_day(d.name) is not None and _parse_day(d.name) >= since_dt
        ]

    out: list[dict[str, Any]] = []
    for day_dir in day_dirs:
        # trace_id 过滤可直接定位文件
        if trace_id:
            jf = day_dir / f"{trace_id}.jsonl"
            if jf.exists():
                out.extend(_read_and_filter(jf, pipeline, node, grep))
            # 也可能被清洗过 (非字母数字字符 → _)
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in trace_id)[:64]
            if safe != trace_id:
                jf2 = day_dir / f"{safe}.jsonl"
                if jf2.exists():
                    out.extend(_read_and_filter(jf2, pipeline, node, grep))
            continue

        # 扫描整个日期目录
        for jf in sorted(day_dir.glob("*.jsonl"), reverse=True):
            out.extend(_read_and_filter(jf, pipeline, node, grep))
            if last and len(out) >= last:
                break
        if last and len(out) >= last:
            break

    # 按时间戳倒序
    out.sort(key=lambda r: r.get("ts", 0), reverse=True)
    if last:
        out = out[:last]
    return out


def _parse_day(name: str) -> datetime | None:
    try:
        return datetime.strptime(name, "%Y-%m-%d")
    except ValueError:
        return None


def _read_and_filter(
    path: Path,
    pipeline: str | None,
    node: str | None,
    grep: str | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if pipeline and rec.get("pipeline_id") != pipeline:
            continue
        if node and rec.get("node_id") != node:
            continue
        if grep:
            hay = (
                (rec.get("system_prompt") or "")
                + "\n"
                + (rec.get("response_text") or "")
            )
            if grep.lower() not in hay.lower():
                continue
        out.append(rec)
    return out


def _fmt_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "?"


def _fmt_summary(rec: dict[str, Any]) -> str:
    ts = _fmt_ts(rec.get("ts", 0))
    trace = (rec.get("trace_id") or "")[:12]
    pipe = (rec.get("pipeline_id") or "-")[:20]
    node = (rec.get("node_id") or "-")[:20]
    role = (rec.get("role") or "-")[:12]
    model = (rec.get("model") or "-")[:20]
    tin = rec.get("input_tokens", 0)
    tout = rec.get("output_tokens", 0)
    lat = rec.get("latency_ms", 0) / 1000.0
    audit = rec.get("info_audit")
    suff = "-"
    crit = 0
    if isinstance(audit, dict):
        suff = audit.get("sufficiency", "-")
        missing = audit.get("missing_info") or []
        crit = sum(1 for m in missing if m.get("critical"))
    return (
        f"{ts}  trace={trace}  {pipe:20} / {node:20}  "
        f"{role:12}  {model:20}  in={tin:5}/out={tout:5}  "
        f"{lat:5.1f}s  audit={suff}"
        + (f" [{crit} crit]" if crit else "")
    )


def _fmt_full(rec: dict[str, Any]) -> str:
    """完整输出: 摘要行 + system + messages 预览 + response + info_audit。"""
    lines = ["=" * 80]
    lines.append(_fmt_summary(rec))
    lines.append("-" * 80)
    sys_prompt = rec.get("system_prompt") or ""
    if sys_prompt:
        lines.append("[SYSTEM]")
        lines.append(sys_prompt)
        lines.append("")

    msgs = rec.get("messages") or []
    if msgs:
        lines.append("[MESSAGES]")
        for i, m in enumerate(msgs):
            role = m.get("role", "?")
            content = m.get("content_preview") or m.get("content") or ""
            if isinstance(content, list):
                content = " | ".join(
                    str(x.get("text", x))[:200] if isinstance(x, dict) else str(x)[:200]
                    for x in content
                )
            lines.append(f"  #{i} {role}:")
            for ln in str(content).splitlines():
                lines.append(f"    {ln}")
        lines.append("")

    resp = rec.get("response_text") or ""
    if resp:
        lines.append("[RESPONSE]")
        lines.append(resp)
        lines.append("")

    audit = rec.get("info_audit")
    if audit:
        lines.append("[INFO_AUDIT]")
        lines.append(json.dumps(audit, ensure_ascii=False, indent=2))
        lines.append("")

    tool_calls = rec.get("tool_calls") or []
    if tool_calls:
        lines.append("[TOOL_CALLS]")
        for tc in tool_calls:
            lines.append(f"  {json.dumps(tc, ensure_ascii=False)[:500]}")
        lines.append("")

    return "\n".join(lines)


@click.group("llm")
def cmd_llm():
    """LLM 调用档案查询 (Phase 2.5)."""
    pass


@cmd_llm.command("audit")
@click.option("--trace-id", type=str, default=None,
              help="按 trace_id 精确查询 (同一 pipeline run 的所有调用)")
@click.option("--pipeline", type=str, default=None,
              help="按 pipeline_id 过滤")
@click.option("--node", type=str, default=None,
              help="按 node_id 过滤")
@click.option("--grep", type=str, default=None,
              help="在 system_prompt + response_text 里关键词搜 (不区分大小写)")
@click.option("--since", type=str, default=None,
              help="起始日期, YYYY-MM-DD 格式 (默认全部)")
@click.option("--last", type=int, default=20,
              help="只显示最近 N 条 (默认 20; 0 = 不限)")
@click.option("--full", is_flag=True, default=False,
              help="完整输出 system/messages/response/info_audit (默认只显示摘要)")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="原始 JSON 输出 (每行一条, 方便管道处理)")
def cmd_llm_audit(
    trace_id: str | None,
    pipeline: str | None,
    node: str | None,
    grep: str | None,
    since: str | None,
    last: int,
    full: bool,
    as_json: bool,
):
    """查询 LLM 调用档案 (data/llm_audit/<date>/<trace>.jsonl).

    \b
    示例:
        omni llm audit --trace-id 01KNS1TVVRWRDE7HJ90FRX5D8R
        omni llm audit --pipeline workflow-factory --node req_analyzer --last 5
        omni llm audit --grep "Schema" --since 2026-04-09
        omni llm audit --trace-id xxx --full
    """
    records = _iter_records(
        trace_id=trace_id,
        pipeline=pipeline,
        node=node,
        grep=grep,
        since=since,
        last=last if last > 0 else None,
    )

    if not records:
        click.echo(
            click.style("没有匹配的 LLM 调用档案。", fg="yellow")
            + f"  根目录: {_audit_root()}"
        )
        return

    if as_json:
        for rec in records:
            click.echo(json.dumps(rec, ensure_ascii=False))
        return

    if full:
        for rec in records:
            click.echo(_fmt_full(rec))
        click.echo("=" * 80)
        click.echo(click.style(f"共 {len(records)} 条", fg="cyan"))
        return

    # 默认: 摘要表
    click.echo(click.style(
        f"{'time':19}  {'trace':13}  {'pipeline':20} / {'node':20}  "
        f"{'role':12}  {'model':20}  {'tokens':13}  {'lat':5}  audit",
        fg="bright_black",
    ))
    click.echo("-" * 160)
    for rec in records:
        click.echo(_fmt_summary(rec))
    click.echo(click.style(f"\n共 {len(records)} 条", fg="cyan"))


# ═══════════════════════════════════════════════════════════════════
# P5.3 — omni pipeline audit-info <pipeline>
# ═══════════════════════════════════════════════════════════════════

@click.group("pipeline")
def cmd_pipeline():
    """管线级审计命令组 (Phase 5.3)."""
    pass


@cmd_pipeline.command("audit-info")
@click.argument("pipeline_name")
@click.option("--node", "only_node", type=str, default=None,
              help="只审计指定单节点 (默认全部 SOFT 节点)")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="JSON 输出 (便于脚本消费)")
def cmd_pipeline_audit_info(pipeline_name: str, only_node: str | None, as_json: bool):
    """对指定 pipeline 的每个 SOFT 节点跑 isolated info_audit probe.

    \b
    作用: 不真跑业务管线 (零副作用), 只对每个 SOFT 节点:
      1. 读取 FORMAT_IN/OUT + DESCRIPTION (来自 Router 类变量)
      2. 从 data/llm_audit 找最近一条该节点的历史 prompt (若存在)
      3. 调用 isolated probe LLM 产出 InfoAuditReport
      4. 汇总成节点级信息充分度表

    \b
    示例:
        omni pipeline audit-info workflow-factory
        omni pipeline audit-info demogame-learn --node field_classifier
        omni pipeline audit-info workflow-factory --json > audit.json
    """
    import asyncio as _asyncio
    import json as _json

    from omnicompany.core.registry import discover, get_or_raise
    from omnicompany.runtime.info_audit.audit_store import load_historical_llm_calls
    from omnicompany.runtime.info_audit.probe import run_info_audit_probe_strict

    discover()

    try:
        entry = get_or_raise(pipeline_name)
    except KeyError as e:
        click.echo(click.style(f"错误: {e}", fg="red"), err=True)
        return

    pipeline = entry.build_team()
    try:
        bindings = entry.build_bindings({}) if _has_positional(entry.build_bindings) else entry.build_bindings()
    except TypeError:
        bindings = entry.build_bindings()
    if not isinstance(bindings, dict):
        bindings = {}

    # 筛选 SOFT 节点
    soft_nodes = [
        n for n in pipeline.nodes
        if n.anchor and n.anchor.validator.kind.value == "soft"
    ]
    if only_node:
        soft_nodes = [n for n in soft_nodes if n.id == only_node]
        if not soft_nodes:
            click.echo(click.style(
                f"错误: 节点 {only_node} 不是 SOFT 节点或不存在于 {pipeline_name}",
                fg="red",
            ), err=True)
            return

    if not soft_nodes:
        click.echo(click.style(
            f"管线 {pipeline_name} 没有 SOFT 节点, 无需审计",
            fg="yellow",
        ))
        return

    click.echo(click.style(
        f"> {pipeline_name}  audit-info  ({len(soft_nodes)} SOFT 节点)",
        fg="cyan", bold=True,
    ))

    async def _run_one(node):
        router = bindings.get(node.id)
        fmt_in = getattr(router, "FORMAT_IN", None) or (
            node.anchor.format_in if node.anchor else ""
        )
        fmt_out = getattr(router, "FORMAT_OUT", None) or (
            node.anchor.format_out if node.anchor else ""
        )
        description = getattr(router, "DESCRIPTION", "") or node.id

        history = load_historical_llm_calls(
            pipeline_id=pipeline.id,
            node_id=node.id,
            last_n=1,
        )
        sys_prev = ""
        user_prev = ""
        resp_prev = ""
        if history:
            h0 = history[0]
            sys_prev = (h0.get("system_prompt") or "")[:2500]
            for m in (h0.get("messages") or []):
                if m.get("role") == "user":
                    user_prev = (m.get("content_preview") or "")[:2500]
                    break
            resp_prev = (h0.get("response_text") or "")[:2500]

        try:
            rep = await _asyncio.to_thread(
                run_info_audit_probe_strict,
                format_in=str(fmt_in) or "(unknown)",
                format_out=str(fmt_out) or "(unknown)",
                description=description,
                original_system=sys_prev,
                original_user_preview=user_prev,
                original_response_preview=resp_prev,
            )
        except Exception as e:
            from omnicompany.protocol.info_audit import InfoAuditReport
            rep = InfoAuditReport.parse_failed(f"probe exception: {e}")

        return {
            "node_id": node.id,
            "format_in": str(fmt_in),
            "format_out": str(fmt_out),
            "has_history": bool(history),
            "sufficiency": rep.sufficiency.value,
            "missing_count": len(rep.missing_info),
            "missing_critical_count": len(rep.missing_critical),
            "confidence_self": rep.confidence_self,
            "concerns": list(rep.concerns),
            "top_critical": (rep.missing_critical[0] if rep.missing_critical else None),
            "report": rep.model_dump(),
        }

    async def _run_all():
        return [await _run_one(n) for n in soft_nodes]

    rows = _asyncio.run(_run_all())

    if as_json:
        click.echo(_json.dumps({
            "pipeline": pipeline_name,
            "pipeline_id": pipeline.id,
            "soft_node_count": len(soft_nodes),
            "nodes": rows,
        }, ensure_ascii=False, indent=2))
        return

    # 表格输出
    click.echo("")
    header = (
        f"{'Node ID':25} | {'Sufficiency':12} | {'Missing':7} | "
        f"{'Crit':4} | {'Hist':4} | Top Concern"
    )
    click.echo(click.style(header, fg="bright_black"))
    click.echo("-" * 100)
    for r in rows:
        suff = r["sufficiency"]
        suff_color = {
            "sufficient": "green",
            "partial": "yellow",
            "insufficient": "red",
            "unknown": "white",
        }.get(suff, "white")
        top = r["top_critical"] or ("-" if r["missing_count"] == 0 else "(non-critical)")
        if top and len(top) > 40:
            top = top[:37] + "..."
        click.echo(
            f"{r['node_id']:25} | "
            + click.style(f"{suff:12}", fg=suff_color)
            + f" | {r['missing_count']:7} | {r['missing_critical_count']:4} | "
            f"{'Y' if r['has_history'] else 'n':4} | {top}"
        )

    # 摘要
    crit_total = sum(r["missing_critical_count"] for r in rows)
    bad = sum(1 for r in rows if r["sufficiency"] in ("partial", "insufficient"))
    click.echo("")
    click.echo(click.style(
        f"摘要: {len(rows)} SOFT 节点, {bad} 个 partial/insufficient, "
        f"{crit_total} 个 critical 缺失项",
        fg="cyan" if crit_total == 0 else "yellow",
    ))


def _has_positional(func) -> bool:
    """判断一个函数是否有强制的位置参数 (用于 build_bindings 签名适配)。"""
    import inspect
    try:
        sig = inspect.signature(func)
    except (ValueError, TypeError):
        return False
    for p in sig.parameters.values():
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            if p.default is inspect.Parameter.empty:
                return True
    return False


# ── omni pipeline check <pipeline_file> ─────────────────────────────────────

@cmd_pipeline.command("check")
@click.argument("pipeline_file")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="以 JSON 格式输出问题列表")
@click.option("--all-pipelines", "check_all", is_flag=True, default=False,
              help="扫描 src/omnicompany 下所有 pipeline.py 文件并全量检查")
@click.option("--narrative", "run_narrative", is_flag=True, default=False,
              help="启用 L4 叙事审计（LLM，需要 API key；检查管线语义连贯性和节点单一性）")
def cmd_pipeline_check(pipeline_file: str, as_json: bool, check_all: bool, run_narrative: bool):
    """对 pipeline.py 文件做拓扑静态检查（B1 Pipeline 拓扑健康检查）。

    \b
    检查项（静态，默认启用）：
      - 孤立节点（从 entry 不可达）
      - Format 链断裂（相邻边 format_out ≠ format_in）
      - 循环依赖（非 feedback 边构成的有向环）
      - Entry 节点缺失/不存在
      - Fan-in composite Format 覆盖缺失
      - soft_hard_pairing（LLM 节点缺 RULE 验证器）
      - maturity_consistency（CRYSTALLIZED 上游有不稳定节点）

    \b
    检查项（LLM，--narrative 启用）：
      - narrative_semantic_jump（节点间语义跳跃）
      - narrative_purpose_misalign（管线意图与结构不符）
      - narrative_node_overload（节点职责过重）

    \b
    示例：
        omni pipeline check src/omnicompany/packages/services/doctor/pipeline.py
        omni pipeline check src/omnicompany/packages/domains/demogame/table_learning/table_learning_pipeline.py
        omni pipeline check . --all-pipelines
        omni pipeline check src/omnicompany/packages/services/doctor/pipeline.py --narrative
    """
    import json as _json
    from pathlib import Path
    from omnicompany.packages.services._diagnosis.doctor.run import run_pipeline_topology_check

    SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "INFO": 3}

    def _color(severity: str) -> str:
        return {"CRITICAL": "red", "HIGH": "red", "MEDIUM": "yellow", "INFO": "bright_black"}.get(severity, "white")

    def _process_file(path: str) -> list[dict]:
        """通过 TeamRunner + SQLiteBus 检查一个 pipeline.py 文件，返回 JSON-able 结果列表。"""
        try:
            report = run_pipeline_topology_check(path, run_llm=run_narrative)
        except FileNotFoundError:
            click.echo(click.style(f"文件不存在: {path}", fg="red"), err=True)
            return []
        except Exception as e:
            click.echo(click.style(f"检查失败 [{path}]: {e}", fg="red"), err=True)
            return []

        if report.get("error"):
            click.echo(click.style(f"错误 [{path}]: {report['error']}", fg="yellow"), err=True)
            return []

        results = []
        for pl in report.get("pipelines", []):
            results.append({
                "file": path,
                "pipeline_id": pl["pipeline_id"],
                "pipeline_name": pl["pipeline_name"],
                "node_count": pl["node_count"],
                "edge_count": pl["edge_count"],
                "issue_count": pl["issue_count"],
                "issues": pl["issues"],
            })
        return results

    # ── 收集要检查的文件 ──────────────────────────────────────────────────────
    files_to_check: list[str] = []
    if check_all:
        # 扫描 src/omnicompany 下所有 pipeline.py 和 *_pipeline.py
        src_root = Path(__file__).parents[4]  # cli/commands → cli → omnicompany → src/omnicompany
        for pat in ("**/pipeline.py", "**/*_pipeline.py", "**/pipeline_*.py"):
            for p in sorted(src_root.rglob(pat)):
                if "__pycache__" not in str(p) and "_graveyard" not in str(p):
                    files_to_check.append(str(p))
    else:
        files_to_check = [pipeline_file]

    # ── 执行检查 ──────────────────────────────────────────────────────────────
    all_results: list[dict] = []
    for f in files_to_check:
        all_results.extend(_process_file(f))

    if as_json:
        click.echo(_json.dumps(all_results, ensure_ascii=False, indent=2))
        return

    # ── 文本输出 ──────────────────────────────────────────────────────────────
    total_issues = sum(r["issue_count"] for r in all_results)
    total_pipelines = len(all_results)

    for result in all_results:
        issues = result["issues"]
        header = click.style(f"\n{result['pipeline_id']}", fg="cyan", bold=True)
        header += f"  ({result['node_count']} 节点 / {result['edge_count']} 边)"
        if result.get("file") and check_all:
            header += click.style(f"\n  {result['file']}", fg="bright_black")
        click.echo(header)

        if not issues:
            click.echo(click.style("  ✓ 拓扑健康，无问题", fg="green"))
            continue

        counts: dict[str, int] = {}
        for iss in issues:
            counts[iss["severity"]] = counts.get(iss["severity"], 0) + 1
        dist = "  ".join(
            click.style(f"{s}:{counts[s]}", fg=_color(s))
            for s in ["CRITICAL", "HIGH", "MEDIUM", "INFO"] if s in counts
        )
        click.echo(f"  发现 {result['issue_count']} 个问题：{dist}")

        for iss in issues:
            sev_str = click.style(f"[{iss['severity']}]", fg=_color(iss["severity"]), bold=True)
            loc = ""
            if iss["node_ids"]:
                loc = f" [{', '.join(iss['node_ids'])}]"
            elif iss["edge"]:
                loc = f" [{iss['edge'][0]} → {iss['edge'][1]}]"
            click.echo(f"  {sev_str} {iss['check']}{loc}")
            click.echo(click.style(f"    {iss['observation']}", fg="bright_black"))

    # ── 总结 ────────────────────────────────────────────────────────────────
    click.echo()
    if total_pipelines == 0:
        click.echo(click.style("未找到任何 TeamSpec。", fg="yellow"))
    elif total_issues == 0:
        click.echo(click.style(f"✓ 全部 {total_pipelines} 个管线拓扑健康。", fg="green", bold=True))
    else:
        click.echo(click.style(
            f"✗ {total_pipelines} 个管线共发现 {total_issues} 个问题。",
            fg="red", bold=True,
        ))


# ── omni pipeline lineage ────────────────────────────────────────────────────

@cmd_pipeline.command("lineage")
@click.option("--source-root", "source_root", default="src/omnicompany",
              show_default=True, help="源码扫描根目录")
@click.option("--format", "filter_format", default=None,
              help="只展示涉及此 Format ID 的条目（生产者+消费者）")
@click.option("--pipeline", "filter_pipeline", default=None,
              help="只展示指定 pipeline_id 的 lineage")
@click.option("--cross", "only_cross", is_flag=True, default=False,
              help="只显示跨管线交接点（A 产出 → B 消费）")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="以 JSON 格式输出")
def cmd_pipeline_lineage(
    source_root: str,
    filter_format: str | None,
    filter_pipeline: str | None,
    only_cross: bool,
    as_json: bool,
):
    """提取跨管线 format 产消图（B2 Pipeline Lineage）。

    \b
    扫描 source_root 下所有注册管线，为每个节点提取 format_in → format_out，
    并构建跨管线的 format 产消关系图。

    \b
    示例：
        omni pipeline lineage
        omni pipeline lineage --format demogame.table_schema
        omni pipeline lineage --pipeline demogame-table-learning
        omni pipeline lineage --cross
    """
    import json as _json
    from omnicompany.packages.services._diagnosis.doctor.run import run_pipeline_lineage

    try:
        report = run_pipeline_lineage(
            source_root=source_root,
            format_id=filter_format,
            pipeline_id=filter_pipeline,
        )
    except Exception as exc:
        click.echo(click.style(f"lineage 提取失败: {exc}", fg="red"), err=True)
        raise SystemExit(1)

    if report.get("error"):
        click.echo(click.style(f"错误: {report['error']}", fg="red"), err=True)
        raise SystemExit(1)

    if as_json:
        click.echo(_json.dumps(report, ensure_ascii=False, indent=2))
        return

    pip_count    = report.get("pipeline_count", 0)
    fmt_count    = report.get("format_count", 0)
    cross_list   = report.get("cross_pipeline_handoffs", [])
    formats      = report.get("formats", {})
    pipelines    = report.get("pipelines", [])

    # ── 跨管线交接点 ──────────────────────────────────────────────────────────
    if only_cross or not filter_format:
        click.echo(click.style(
            f"\n跨管线 Format 交接点（{len(cross_list)} 个）", fg="cyan", bold=True
        ))
        if not cross_list:
            click.echo(click.style("  （无跨管线交接）", fg="bright_black"))
        for item in cross_list:
            click.echo(f"  {click.style(item['format_id'], fg='yellow')}")
            click.echo(f"    产出: {', '.join(item['produced_by'])}")
            click.echo(f"    消费: {', '.join(item['consumed_by'])}")

    if only_cross:
        return

    # ── 指定 Format 的产消详情 ────────────────────────────────────────────────
    if filter_format:
        info = formats.get(filter_format, {})
        producers = info.get("producers", [])
        consumers = info.get("consumers", [])
        click.echo(click.style(f"\nFormat: {filter_format}", fg="cyan", bold=True))
        click.echo(click.style(f"  生产者 ({len(producers)})", fg="green"))
        for p in producers:
            click.echo(f"    {p['pipeline_id']} / {p['node_id']} [{p['node_kind']}]")
        click.echo(click.style(f"  消费者 ({len(consumers)})", fg="yellow"))
        for c in consumers:
            click.echo(f"    {c['pipeline_id']} / {c['node_id']} [{c['node_kind']}]")
        return

    # ── 管线 format_flow 详情 ─────────────────────────────────────────────────
    if filter_pipeline:
        for pip in pipelines:
            click.echo(click.style(
                f"\n{pip['pipeline_id']}  ({pip['node_count']} 节点)", fg="cyan", bold=True
            ))
            click.echo(click.style(f"  {pip['source_file']}", fg="bright_black"))
            for e in pip["format_flow"]:
                fin  = e["format_in"]
                fout = e["format_out"] or "—"
                fin_str = str(fin) if isinstance(fin, list) else (fin or "—")
                click.echo(
                    f"  {click.style(e['node_id'], bold=True)} [{e['node_kind']}]"
                    f"  {click.style(fin_str, fg='yellow')} → {click.style(fout, fg='green')}"
                )
        return

    # ── 全量摘要 ──────────────────────────────────────────────────────────────
    click.echo(click.style(
        f"\n扫描 {pip_count} 个管线，{fmt_count} 个 Format", fg="cyan", bold=True
    ))
    click.echo(f"  源码根目录: {source_root}")
    click.echo()
    click.echo(click.style("管线列表（节点数 / Format 数）:", bold=True))
    for pip in pipelines:
        fmts_in_pip = {e["format_in"] for e in pip["format_flow"] if e["format_in"]}
        fmts_in_pip |= {e["format_out"] for e in pip["format_flow"] if e["format_out"]}
        click.echo(
            f"  {click.style(pip['pipeline_id'], fg='cyan')}  "
            f"{pip['node_count']} 节点 / {len(fmts_in_pip)} 个 Format"
        )
    if cross_list:
        click.echo()
        click.echo(click.style(
            f"跨管线交接点: {len(cross_list)} 个  (--cross 查看详情)", fg="yellow"
        ))


# ── omni pipeline manifest ───────────────────────────────────────────────────

@cmd_pipeline.group("manifest")
def cmd_pipeline_manifest():
    """Pipeline manifest（综合声明档案）管理命令。"""
    pass


@cmd_pipeline_manifest.command("init")
@click.argument("pipeline_file")
@click.option("--force", is_flag=True, default=False,
              help="若 manifest.yaml 已存在，强制覆盖")
@click.option("--print", "do_print", is_flag=True, default=False,
              help="只打印骨架，不写文件")
def cmd_pipeline_manifest_init(pipeline_file: str, force: bool, do_print: bool):
    """从 pipeline.py 生成 .omni/manifest.yaml 骨架。

    \b
    自动填充：id / purpose（来自 TeamSpec.purpose）/ boundaries（entry_format + exit_format）
    需要人工补充：design_rationale / current_status.known_issues / sub_pipelines

    \b
    生成位置：<pipeline_dir>/.omni/manifest.yaml

    \b
    示例：
        omni pipeline manifest init src/omnicompany/packages/services/doctor/pipeline.py
        omni pipeline manifest init pipeline.py --print
        omni pipeline manifest init pipeline.py --force
    """
    import importlib.util
    import inspect as _inspect
    import sys as _sys
    from pathlib import Path

    from omnicompany.protocol.manifest import (
        dump_manifest_yaml,
        generate_manifest_skeleton,
        manifest_path,
        save_manifest,
    )

    path = Path(pipeline_file)
    if not path.exists():
        click.echo(click.style(f"错误：文件不存在: {path}", fg="red"), err=True)
        raise SystemExit(1)

    # 动态加载 pipeline.py
    try:
        _spec = importlib.util.spec_from_file_location("_tmp_pipeline", str(path.resolve()))
        mod = importlib.util.module_from_spec(_spec)
        parent_dir = str(path.parent.resolve())
        if parent_dir not in _sys.path:
            _sys.path.insert(0, parent_dir)
        _spec.loader.exec_module(mod)
    except Exception as exc:
        click.echo(click.style(f"错误：加载 {pipeline_file} 失败: {exc}", fg="red"), err=True)
        raise SystemExit(1)

    # 找所有无参数 build_*() 函数
    builders = []
    for name, obj in vars(mod).items():
        if not name.startswith("build_") or not callable(obj):
            continue
        try:
            sig = _inspect.signature(obj)
            if all(
                p.default is not _inspect.Parameter.empty
                for p in sig.parameters.values()
            ):
                builders.append((name, obj))
        except Exception:
            pass

    if not builders:
        click.echo(click.style(
            f"警告：{pipeline_file} 中未找到无参数 build_*() 函数。", fg="yellow"
        ))

    specs = []
    for name, builder in builders:
        try:
            specs.append(builder())
        except Exception as exc:
            click.echo(click.style(f"警告：{name}() 加载失败: {exc}", fg="yellow"), err=True)

    if not specs:
        click.echo(click.style("错误：无法加载任何 TeamSpec", fg="red"), err=True)
        raise SystemExit(1)

    # 取第一个 spec 作为主管线
    main_spec = specs[0]
    skeleton = generate_manifest_skeleton(main_spec, path)
    yaml_content = dump_manifest_yaml(skeleton)

    if do_print:
        click.echo(yaml_content)
        click.echo(click.style(
            f"# 来自 {builders[0][0]}()  →  {manifest_path(path)}", fg="bright_black"
        ))
        return

    out_path = manifest_path(path)
    if out_path.exists() and not force:
        click.echo(click.style(
            f"manifest.yaml 已存在: {out_path}\n"
            "  使用 --force 强制覆盖，或 --print 只查看骨架。", fg="yellow"
        ))
        raise SystemExit(1)

    try:
        saved = save_manifest(skeleton, path)
        click.echo(click.style(f"✓ manifest.yaml 已生成: {saved}", fg="green", bold=True))
        click.echo(click.style(
            "\n  请补充以下字段：\n"
            "    - design_rationale（为什么采用这种设计）\n"
            "    - current_status.known_issues\n"
            "    - boundaries.sub_pipelines（如适用）",
            fg="bright_black",
        ))
        if len(specs) > 1:
            click.echo(click.style(
                f"\n  注意：文件共有 {len(specs)} 个管线，只为 {main_spec.id} 生成了骨架。",
                fg="yellow",
            ))
    except Exception as exc:
        click.echo(click.style(f"错误：写入失败: {exc}", fg="red"), err=True)
        raise SystemExit(1)
