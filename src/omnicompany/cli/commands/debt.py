# [OMNI] origin=claude-code domain=omnicompany/cli ts=2026-04-18T00:00:00Z
# [OMNI] material_id="material:cli.commands.tech_debt.registry_manager.py"
"""omni debt — 技术债登记处管理命令组

与 omni guardian 的分工（见 packages/services/tech_debt/DESIGN.md D1）：
  - guardian = 违规生产者（扫规则、patrol、daemon、stamp、zombies）
  - debt     = 债务消费者 + resolver（list、stats、resolve）

子命令:
    omni debt list [--section X] [--status X] [--json]  列出条目
    omni debt stats [--json]                              统计
    omni debt resolve <ID> --reason TEXT [--by NAME]      解决条目
    omni debt reconcile [--apply] [--not-hit-rounds N]    自动对账批量销账
      （默认 dry-run：path_gone / 连续 N 轮未命中 / 域白名单 → §已解决）
    omni debt auto-triage [--apply] [--max N] [--json]    LLM 自动分诊
      （默认 dry-run：terra 判 playbook_fix/false_positive/needs_human；
       --apply 时执行机械 playbook + 销账 + 写状态 + 刷新当天 digest）
    omni debt digest [--date YYYY-MM-DD] [--json]         分诊日报一屏摘要
    omni debt scan [--fast|--full|--drift-only] [--limit N] [--json]
      --fast        (默认) Guardian patrol + DriftChecker（快速确定性，<30s）
      --drift-only  只跑 DriftChecker（DESIGN.md 漂移 + plan 停滞）
      --full        --fast + SemanticAuditor 5 节点管线（慢，含 LLM）
    omni debt add <section> --fields '<JSON>' [--by AGENT]   主动登记条目

scan 是"调度器"：guardian 是 Guardian 自己的内部命令（patrol/daemon/stamp），
debt scan 是面向 REGISTRY 视角的跨 producer 协调入口。
"""
from __future__ import annotations

import json as _json_mod
import sys
from pathlib import Path
from typing import Any

import click

from omnicompany.core.config import omni_workspace_root


_DEFAULT_ROOT = str(omni_workspace_root())

_SECTION_CHOICES = (
    "activity",
    "semantic_pending",
    "doc_drift",
    "plan_merge",
    "capability_gap",
    "resolved",
)

_STATUS_CHOICES = (
    "open",
    "needs_human_review",
    "resolved",
    "pending",
    "all",
)

# 严重度着色（对齐 guardian patrol）
_SEV_COLOR = {
    "CRITICAL": "red",
    "HIGH": "yellow",
    "MEDIUM": "blue",
    "LOW": "cyan",
    "INFO": "white",
}


def _style(text: str, **kw) -> str:
    """Click style 的安全包装（GBK 终端不炸）。"""
    try:
        return click.style(text, **kw)
    except Exception:
        return text


@click.group("debt")
def cmd_debt():
    """技术债登记处（REGISTRY.md）管理：列出 / 统计 / 解决条目。"""


# ═══ omni debt list ══════════════════════════════════════════════


@cmd_debt.command("list")
@click.option("--root", type=str, default=_DEFAULT_ROOT, help="项目根目录")
@click.option("--section", type=click.Choice(_SECTION_CHOICES), default=None,
              help="只列出某个 section；省略=全部 active section")
@click.option("--status", type=click.Choice(_STATUS_CHOICES), default="open",
              help="按 status 过滤；--status all 显示所有；默认 open")
@click.option("--json", "json_out", is_flag=True, default=False,
              help="JSON 输出（供 agent / CI 消费）")
@click.option("--limit", type=int, default=0,
              help="最多显示多少条；0=全部")
def cmd_debt_list(root: str, section: str | None, status: str, json_out: bool, limit: int):
    """列出 REGISTRY.md 条目。默认显示所有 open 条目。"""
    from omnicompany.packages.services._diagnosis.tech_debt import (
        load_registry, list_rows,
    )

    root_path = Path(root)
    try:
        snapshot = load_registry(root_path)
    except FileNotFoundError as e:
        click.echo(_style(f"ERROR: {e}", fg="red"), err=True)
        sys.exit(1)

    status_filter = None if status == "all" else status
    rows = list_rows(snapshot, section=section, status=status_filter)
    if limit > 0:
        rows = rows[:limit]

    if json_out:
        payload = [{
            "section": r.section,
            "id": r.id,
            "status": r.status,
            **r.fields,
        } for r in rows]
        click.echo(_json_mod.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not rows:
        click.echo(_style("(空) 当前条件下无条目。", fg="green"))
        return

    # 按 section 分组人类可读输出
    by_section: dict[str, list] = {}
    for r in rows:
        by_section.setdefault(r.section, []).append(r)

    for sec_name, sec_rows in by_section.items():
        click.echo(_style(f"\n── {sec_name} ({len(sec_rows)}) ──", fg="cyan", bold=True))
        for r in sec_rows:
            _print_row(r)

    click.echo(_style(f"\n总计: {len(rows)} 条", fg="cyan"))


def _print_row(r: Any) -> None:
    """单行人类可读打印，不同 section 展示不同关键字段。"""
    fields = r.fields
    if r.section == "activity":
        sev = fields.get("severity", "")
        sev_color = _SEV_COLOR.get(sev, "white")
        click.echo(
            f"  {r.id:<7} "
            f"[{_style(sev, fg=sev_color)}] "
            f"{fields.get('rule_id', ''):<12} "
            f"{_trunc(fields.get('path', ''), 80)} "
            f"({fields.get('status', '')})"
        )
    elif r.section == "semantic_pending":
        conf = fields.get("confidence", "")
        click.echo(
            f"  {r.id:<7} "
            f"{fields.get('standard_id', ''):<20} "
            f"conf={conf:<5} "
            f"{_trunc(fields.get('target_path', ''), 60)} "
            f"({fields.get('status', '')})"
        )
        desc = fields.get("description", "")
        if desc:
            click.echo(f"          {_trunc(desc, 110)}")
    elif r.section == "plan_merge":
        click.echo(
            f"  {r.id:<7} "
            f"{_trunc(fields.get('archived_plan', ''), 60):<60}  "
            f"→ {_trunc(fields.get('target_design_md', ''), 50)} "
            f"({fields.get('status', '')})"
        )
    elif r.section == "capability_gap":
        prio = fields.get("priority", "")
        click.echo(
            f"  {r.id:<7} "
            f"[{prio}] "
            f"{_trunc(fields.get('description', ''), 90)} "
            f"({fields.get('status', '')})"
        )
    elif r.section == "doc_drift":
        kind = fields.get("kind", "")
        click.echo(
            f"  {r.id:<7} "
            f"[{kind:<18}] "
            f"{_trunc(fields.get('target', ''), 60):<60} "
            f"漂移 {fields.get('drift_days', '')} 天 "
            f"({fields.get('status', '')})"
        )
    elif r.section == "resolved":
        click.echo(
            f"  {r.id:<7} "
            f"[{fields.get('kind', '')}] "
            f"{fields.get('resolved_date', ''):<12} "
            f"{_trunc(fields.get('how', ''), 80)}"
        )
    else:
        click.echo(f"  {r.id}: {fields}")


def _trunc(s: str, n: int) -> str:
    s = str(s)
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


# ═══ omni debt stats ═════════════════════════════════════════════


@cmd_debt.command("stats")
@click.option("--root", type=str, default=_DEFAULT_ROOT, help="项目根目录")
@click.option("--json", "json_out", is_flag=True, default=False,
              help="JSON 输出")
def cmd_debt_stats(root: str, json_out: bool):
    """全局统计：按 section / status / severity / rule_id 聚合。"""
    from omnicompany.packages.services._diagnosis.tech_debt import (
        load_registry, compute_stats,
    )

    root_path = Path(root)
    try:
        snapshot = load_registry(root_path)
    except FileNotFoundError as e:
        click.echo(_style(f"ERROR: {e}", fg="red"), err=True)
        sys.exit(1)

    stats = compute_stats(snapshot)
    if json_out:
        click.echo(_json_mod.dumps(stats, ensure_ascii=False, indent=2))
        return

    click.echo(_style("\n=== 技术债统计 ===", fg="cyan", bold=True))
    click.echo(f"总活跃条目: {stats['total_rows'] - stats['resolved_count']}")
    click.echo(f"已解决:     {stats['resolved_count']}")

    click.echo(_style("\n按 section:", fg="yellow"))
    for name, n in sorted(stats["by_section"].items(), key=lambda x: -x[1]):
        click.echo(f"  {name:<22} {n}")

    click.echo(_style("\n按 status:", fg="yellow"))
    for st, n in sorted(stats["by_status"].items(), key=lambda x: -x[1]):
        click.echo(f"  {st:<22} {n}")

    if stats["by_severity"]:
        click.echo(_style("\n按 severity:", fg="yellow"))
        for sev, n in sorted(stats["by_severity"].items(), key=lambda x: -x[1]):
            color = _SEV_COLOR.get(sev, "white")
            click.echo(f"  {_style(sev, fg=color):<22}  {n}")

    if stats["by_rule_id"]:
        top = sorted(stats["by_rule_id"].items(), key=lambda x: -x[1])[:10]
        click.echo(_style("\n按 rule_id / standard_id (Top 10):", fg="yellow"))
        for rid, n in top:
            click.echo(f"  {rid:<22} {n}")


# ═══ omni debt resolve ═══════════════════════════════════════════


@cmd_debt.command("resolve")
@click.argument("row_id")
@click.option("--reason", required=True, type=str,
              help="解决方式说明（会写到 §已解决 + ARCH-CHANGES）")
@click.option("--by", "resolved_by", type=str, default="human",
              help="谁解决的（默认 human；agent 填 agent 名）")
@click.option("--root", type=str, default=_DEFAULT_ROOT, help="项目根目录")
@click.option("--json", "json_out", is_flag=True, default=False)
def cmd_debt_resolve(
    row_id: str, reason: str, resolved_by: str, root: str, json_out: bool
):
    """把条目从原 section 移到 §已解决 + 记 ARCH-CHANGES 事件。

    \b
    示例：
      omni debt resolve D-024 --reason "research/PROGRESS.md 已删除"
      omni debt resolve SA-001 --reason "034d/e 已迁移 LLM 巡逻" --by claude-code
    """
    from omnicompany.packages.services._diagnosis.tech_debt import resolve_row

    root_path = Path(root)
    result = resolve_row(
        root_path, row_id, reason=reason, resolved_by=resolved_by,
    )

    if json_out:
        click.echo(_json_mod.dumps({
            "ok": result.ok,
            "row_id": result.row_id,
            "section_from": result.section_from,
            "reason": result.reason,
            "error": result.error,
            "arch_event_id": result.arch_event_id,
        }, ensure_ascii=False, indent=2))
        sys.exit(0 if result.ok else 1)

    if result.ok:
        click.echo(_style(
            f"[OK] {row_id} resolved from §{result.section_from} "
            f"(ARCH event: {result.arch_event_id})",
            fg="green",
        ))
    else:
        click.echo(_style(f"[FAIL] {row_id}: {result.error}", fg="red"), err=True)
        sys.exit(1)


# ═══ omni debt reconcile ═════════════════════════════════════════


@cmd_debt.command("reconcile")
@click.option("--apply", "do_apply", is_flag=True, default=False,
              help="真销账（默认 dry-run，只打印统计和样本）")
@click.option("--not-hit-rounds", type=int, default=3, show_default=True,
              help="连续 N 轮巡逻未再命中即销账（not_hit_in_N_scans）")
@click.option("--keep-resolved", type=int, default=30, show_default=True,
              help="§已解决 正文保留最近 N 条，溢出归档 resolved-archive-<YYYYMM>.md")
@click.option("--root", type=str, default=_DEFAULT_ROOT, help="项目根目录")
@click.option("--json", "json_out", is_flag=True, default=False)
def cmd_debt_reconcile(
    do_apply: bool, not_hit_rounds: int, keep_resolved: int,
    root: str, json_out: bool,
):
    """自动对账 §活跃违规：批量销账 path_gone / 连续 N 轮未命中 / 域白名单条目。

    \b
    判据（仅处理 status=open 且 OMNI- 开头的条目）：
      1. path_gone           路径已不存在于磁盘
      2. not_hit_in_N_scans  scan_state 里 scan_round - last_hit >= N
      3. domain_whitelisted  域级白名单裁决（reconciler.RESOLVE_WHITELIST）

    \b
    示例：
      omni debt reconcile                  # dry-run：看将销账的统计和样本
      omni debt reconcile --apply          # 真销账 + 记聚合 ARCH 事件
      omni debt reconcile --not-hit-rounds 5 --apply
    """
    from omnicompany.packages.services._diagnosis.tech_debt import reconcile_registry

    root_path = Path(root)
    result = reconcile_registry(
        root_path,
        apply=do_apply,
        not_hit_rounds=not_hit_rounds,
        keep_resolved=keep_resolved,
    )

    if json_out:
        click.echo(_json_mod.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if not result["error"] else 1)

    if result["error"]:
        click.echo(_style(f"[FAIL] {result['error']}", fg="red"), err=True)
        sys.exit(1)

    mode = "APPLY" if do_apply else "DRY-RUN"
    click.echo(_style(f"\n=== debt reconcile ({mode}) ===", fg="cyan", bold=True))
    click.echo(
        f"open: {result['open_before']} → {result['open_after']} "
        f"(将销账 {result['resolved_total']} 条)"
    )
    if not result["by_reason"]:
        click.echo(_style("  无可自动销账的条目。", fg="green"))
    for reason, bucket in sorted(result["by_reason"].items()):
        click.echo(_style(f"\n  {reason}: {bucket['count']} 条", fg="yellow"))
        for s in bucket["samples"]:
            click.echo(f"    {s['id']:<9} {s['rule_id']:<10} {_trunc(s['path'], 90)}")
        if bucket["count"] > len(bucket["samples"]):
            click.echo(f"    ... 其余 {bucket['count'] - len(bucket['samples'])} 条略")

    if do_apply and result["resolved_total"]:
        click.echo("")
        if result.get("arch_event_id"):
            click.echo(f"  ARCH 事件: {result['arch_event_id']}")
        if result.get("detail_file"):
            click.echo(f"  全量明细: {result['detail_file']}")
        if result.get("archived_overflow"):
            click.echo(
                f"  §已解决 溢出归档 {result['archived_overflow']} 条"
                f" → {result.get('archive_file')}"
            )
        click.echo(_style("[OK] reconcile applied", fg="green", bold=True))
    elif not do_apply and result["resolved_total"]:
        click.echo(_style(
            "\n(dry-run) 以上条目未动；加 --apply 真销账。", fg="yellow",
        ))


# ═══ omni debt scan ══════════════════════════════════════════════


@cmd_debt.command("scan")
@click.option("--fast", "mode_fast", is_flag=True, default=False,
              help="Guardian patrol + DriftChecker（默认；快速确定性）")
@click.option("--full", "mode_full", is_flag=True, default=False,
              help="(已归档) SemanticAuditor 部分移交 doctor 假设派生子域；现等价 --fast")
@click.option("--drift-only", "mode_drift_only", is_flag=True, default=False,
              help="只跑 DriftChecker（DESIGN.md + plan 漂移检查）")
@click.option("--limit", type=int, default=20,
              help="--full 时，最多审多少个 artifact（成本控制；默认 20）")
@click.option("--source", type=click.Choice(["git-diff", "full-scan"]), default="git-diff",
              help="--full 的 artifact 来源；默认 git-diff")
@click.option("--root", type=str, default=_DEFAULT_ROOT, help="项目根目录")
@click.option("--json", "json_out", is_flag=True, default=False)
@click.option("--dry-run", is_flag=True, default=False,
              help="只模拟（不写 REGISTRY / 不调 LLM），仅看会扫谁")
def cmd_debt_scan(
    mode_fast: bool, mode_full: bool, mode_drift_only: bool,
    limit: int, source: str,
    root: str, json_out: bool, dry_run: bool,
):
    """协调调度：Guardian patrol + DriftChecker（--fast 默认）+ SemanticAuditor（--full）。

    \b
    示例：
      omni debt scan                       # = --fast (Guardian + DriftChecker)
      omni debt scan --drift-only          # 只跑 DESIGN.md/plan 漂移检查
      omni debt scan --full --limit 10     # 含 LLM，限 10 个 artifact
      omni debt scan --dry-run --full      # 看会扫谁，不调 LLM
    """
    # 模式互斥
    set_flags = sum([mode_fast, mode_full, mode_drift_only])
    if set_flags > 1:
        click.echo(_style("ERROR: --fast / --full / --drift-only 互斥", fg="red"), err=True)
        sys.exit(2)
    if mode_full:
        mode = "full"
    elif mode_drift_only:
        mode = "drift-only"
    else:
        mode = "fast"

    from omnicompany.packages.services._diagnosis.tech_debt import append_event, run_drift_audit

    root_path = Path(root)

    append_event(
        root_path,
        event_type="scan-started",
        initiator="tech_debt",
        drawer="services/tech_debt",
        related_pipeline="",
        change=f"debt scan mode={mode} limit={limit} source={source}"
               + (" dry-run" if dry_run else ""),
        payload={"mode": mode, "limit": limit, "source": source, "dry_run": dry_run},
    )

    summary: dict[str, Any] = {
        "mode": mode, "dry_run": dry_run,
        "guardian": None, "drift": None, "semantic": None,
    }

    # ─── Guardian patrol（fast / full 跑，drift-only 跳过） ───
    if mode in ("fast", "full"):
        try:
            from omnicompany.packages.services._core.guardian import run_patrol
            click.echo(_style("\n[guardian] patrol ...", fg="cyan", bold=True))
            if dry_run:
                click.echo("  (dry-run) 跳过")
                g_result = {"violations_found": 0, "registry_sync": {}}
            else:
                g_result = run_patrol(
                    project_root=str(root_path), full_scan=False,
                    n_commits=0, auto_tow=False,
                )
            summary["guardian"] = {
                "violations_found": g_result.get("violations_found", 0),
                "registry_sync": g_result.get("registry_sync", {}),
            }
            if not json_out:
                rs = g_result.get("registry_sync", {}) or {}
                click.echo(
                    f"  violations_found={g_result.get('violations_found', 0)} "
                    f"| REGISTRY: added={rs.get('added', 0)} bumped={rs.get('bumped', 0)}"
                )
        except Exception as e:
            click.echo(_style(f"  Guardian patrol 失败: {e}", fg="red"), err=True)
            summary["guardian"] = {"error": str(e)}

    # ─── DriftChecker（所有模式都跑；drift-only 只跑这个） ───
    try:
        click.echo(_style("\n[drift] DESIGN.md + plan ...", fg="cyan", bold=True))
        drift_result = run_drift_audit(root_path, dry_run=dry_run)
        summary["drift"] = drift_result
        if not json_out:
            click.echo(
                f"  findings={drift_result['total_findings']} "
                f"(design={drift_result['design_count']} plan={drift_result['plan_count']}) "
                f"| added={drift_result.get('added', 0)} "
                f"deduped={drift_result.get('deduped', 0)} "
                f"errors={drift_result.get('errors', 0)}"
            )
    except Exception as e:
        click.echo(_style(f"  DriftChecker 失败: {e}", fg="red"), err=True)
        summary["drift"] = {"error": str(e)}

    # SemanticAuditor 已归档 (2026-05-05 诊断重制 step 7) — 5 worker 概念并入 doctor _hypothesis/.
    # --full 模式当前等价 --fast, 等 doctor 假设派生子域落实后接管.
    if mode == "full":
        click.echo(_style(
            "\n[note] SemanticAuditor 已归档. --full 当前等价 --fast. "
            "见 docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/plan.md",
            fg="yellow",
        ))
        summary["semantic"] = {"status": "archived"}

    # scan-completed 事件
    append_event(
        root_path,
        event_type="scan-completed",
        initiator="tech_debt",
        drawer="services/tech_debt",
        related_pipeline="",
        change=f"debt scan mode={mode} done",
        payload=_compact_summary(summary),
    )

    if json_out:
        click.echo(_json_mod.dumps(summary, ensure_ascii=False, indent=2))
    else:
        click.echo(_style("\n[OK] scan completed", fg="green", bold=True))


def _compact_summary(summary: dict) -> dict:
    """从 summary 里挑出结构化 payload（给 ARCH-CHANGES 用，不过大）。"""
    out: dict = {"mode": summary["mode"], "dry_run": summary["dry_run"]}
    g = summary.get("guardian")
    if g and isinstance(g, dict):
        out["guardian"] = {
            "violations_found": g.get("violations_found", 0),
            "registry_added": (g.get("registry_sync") or {}).get("added", 0),
        }
    s = summary.get("semantic")
    if s and isinstance(s, dict):
        out["semantic"] = {
            "artifact_count": s.get("artifact_count", 0),
            "finding_count": s.get("finding_count", 0),
            "writer_added": s.get("writer_added", 0),
        }
    return out


# _run_semantic_dry / _run_semantic_full 移除 (2026-05-05 诊断重制 step 7) —
# semantic_auditor 整体归档, 5 worker 概念并入 doctor _hypothesis/.

# ═══ omni debt add ═══════════════════════════════════════════════


@cmd_debt.command("add")
@click.argument("section", type=click.Choice([
    "activity", "semantic_pending", "doc_drift", "plan_merge", "capability_gap",
]))
@click.option("--fields", "fields_json", required=True, type=str,
              help='JSON 字符串：该 section 的字段字典，参考 omni debt add --help-fields')
@click.option("--by", "initiator", type=str, default="human",
              help="登记者（默认 human；agent 填 agent 名）")
@click.option("--dedup-on", type=str, default=None,
              help="逗号分隔字段名，用作去重键；命中已有 open 条目则跳过")
@click.option("--root", type=str, default=_DEFAULT_ROOT, help="项目根目录")
@click.option("--json", "json_out", is_flag=True, default=False)
def cmd_debt_add(
    section: str, fields_json: str, initiator: str,
    dedup_on: str | None, root: str, json_out: bool,
):
    """主动登记一条债务条目到 REGISTRY（外部 agent / 人工使用）。

    \b
    每个 section 的字段：
      activity         rule_id / path / severity
      semantic_pending standard_id / target_path / description / confidence /
                       disposition
      doc_drift        kind / target / last_change / last_update / drift_days
      plan_merge       archived_plan / target_design_md
      capability_gap   description / priority
    共用 status（默认 open）。

    \b
    示例：
      omni debt add capability_gap --fields '{"description":"autocompact 失效","priority":"P1"}'
      omni debt add activity --fields '{"rule_id":"OVERSEER","path":"x","severity":"HIGH"}' --by claude-code
      omni debt add doc_drift --fields '{"kind":"design_md_drift","target":"x/DESIGN.md","drift_days":"30"}' \\
          --dedup-on kind,target
    """
    from omnicompany.packages.services._diagnosis.tech_debt import append_row, append_event

    try:
        fields = _json_mod.loads(fields_json)
    except _json_mod.JSONDecodeError as e:
        click.echo(_style(f"ERROR: --fields 不是合法 JSON: {e}", fg="red"), err=True)
        sys.exit(2)
    if not isinstance(fields, dict):
        click.echo(_style("ERROR: --fields 必须是 JSON 对象（dict）", fg="red"), err=True)
        sys.exit(2)

    dedup_keys: tuple[str, ...] = ()
    if dedup_on:
        dedup_keys = tuple(k.strip() for k in dedup_on.split(",") if k.strip())

    root_path = Path(root)
    result = append_row(
        root_path,
        section_name=section,
        fields=fields,
        dedup_keys=dedup_keys,
    )

    # 记 ARCH-CHANGES 事件（成功才登记）
    arch_event_id = ""
    if result.ok and result.action == "added":
        ev = append_event(
            root_path,
            event_type="violation-found",  # 手工登记也算新条目
            initiator=initiator,
            drawer="services/tech_debt",
            related_pipeline="",
            change=f"{result.row_id} (manual-add section={section})",
            payload={"section": section, "row_id": result.row_id, "fields": fields},
        )
        if ev is not None:
            arch_event_id = ev.change_id

    if json_out:
        click.echo(_json_mod.dumps({
            "ok": result.ok,
            "action": result.action,
            "row_id": result.row_id,
            "error": result.error,
            "arch_event_id": arch_event_id,
        }, ensure_ascii=False, indent=2))
        sys.exit(0 if result.ok else 1)

    if result.ok and result.action == "added":
        click.echo(_style(
            f"[OK] {result.row_id} added to §{section} "
            f"(ARCH event: {arch_event_id})", fg="green",
        ))
    elif result.ok and result.action == "deduped":
        click.echo(_style(
            f"[SKIP] already tracked as {result.row_id} in §{section}",
            fg="yellow",
        ))
    else:
        click.echo(_style(f"[FAIL] {result.error}", fg="red"), err=True)
        sys.exit(1)


# _run_semantic_full 移除 (2026-05-05 诊断重制 step 7) — semantic_auditor 整体归档.

# ═══ omni debt auto-triage ═══════════════════════════════════════


@cmd_debt.command("auto-triage")
@click.option("--apply", "do_apply", is_flag=True, default=False,
              help="真处置（默认 dry-run：只分诊打印去向，不改文件/状态/REGISTRY）")
@click.option("--max", "max_n", type=int, default=200, show_default=True,
              help="本轮最多分诊多少条（每天默认 200）")
@click.option("--root", type=str, default=_DEFAULT_ROOT, help="项目根目录")
@click.option("--json", "json_out", is_flag=True, default=False)
def cmd_debt_auto_triage(do_apply: bool, max_n: int, root: str, json_out: bool):
    """LLM 自动分诊 open 条目：terra 判 playbook_fix / false_positive / needs_human。

    \b
    处置协议：
      playbook_fix   机械 playbook 确定性执行（补头/改名+引用修复/补 kind tag），
                     成功 → 销账 autotriage:<playbook>；失败 → needs_human 不销账
      false_positive 销账 autotriage:false-positive（附模型理由）
      needs_human    只记状态文件，进 digest 等人判
    执行面未建的 playbook（迁 data/迁 src/Router 迁移等）本轮只判不执行，
    判为可修也落 needs_human（理由=执行面未建）。

    \b
    示例：
      omni debt auto-triage                    # dry-run 看分诊去向
      omni debt auto-triage --apply --max 200  # 每日真处置（cron 同款）
    """
    from omnicompany.packages.services._diagnosis.tech_debt import run_autotriage

    root_path = Path(root)
    result = run_autotriage(root_path, apply=do_apply, max_n=max_n)

    if json_out:
        click.echo(_json_mod.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if not result["error"] else 1)

    if result["error"]:
        click.echo(_style(f"[FAIL] {result['error']}", fg="red"), err=True)
        sys.exit(1)

    mode = "APPLY" if do_apply else "DRY-RUN"
    click.echo(_style(f"\n=== debt auto-triage ({mode}, model={result['model']}) ===",
                      fg="cyan", bold=True))
    click.echo(
        f"候选 {result['candidates']} 条 · 分诊 {result['triaged']} 条 · "
        f"LLM 调用 {result['llm_calls']} 次（失败 {result['llm_errors']} 批）"
    )
    meter = result.get("meter") or {}
    if meter.get("call_count"):
        click.echo(
            f"tokens: in={meter.get('total_input_tokens', 0)} "
            f"out={meter.get('total_output_tokens', 0)} "
            f"· 成本约 ${meter.get('total_cost_usd', 0):.4f}"
        )
    ba = result.get("by_action") or {}
    click.echo(
        f"去向: playbook_fix {ba.get('playbook_fix', 0)} · "
        f"false_positive {ba.get('false_positive', 0)} · "
        f"needs_human {ba.get('needs_human', 0)}"
    )
    if do_apply:
        executed = result.get("executed") or {}
        ex_txt = " · ".join(f"{k}={v}" for k, v in sorted(executed.items())) or "无"
        click.echo(
            f"执行: {ex_txt} · 销账 {result['resolved']} 条 · "
            f"执行失败降级 {result['exec_failed']} 条 · "
            f"执行面未建降级 {result['downgraded']} 条"
        )

    # 样本输出（每类最多 5 条）
    by_action: dict[str, list] = {}
    for v in result.get("verdicts", []):
        by_action.setdefault(v["action"], []).append(v)
    for action in ("playbook_fix", "false_positive", "needs_human"):
        vs = by_action.get(action) or []
        if not vs:
            continue
        click.echo(_style(f"\n  {action}: {len(vs)} 条", fg="yellow"))
        for v in vs[:5]:
            note = v.get("exec_note") or v.get("reason", "")
            click.echo(
                f"    {v['id']:<9} {v['rule_id']:<10} "
                f"{_trunc(v.get('path', ''), 70)} — {_trunc(note, 60)}"
            )
        if len(vs) > 5:
            click.echo(f"    ... 其余 {len(vs) - 5} 条略")

    if result.get("digest_file"):
        click.echo(f"\n  digest: {result['digest_file']}")
    if not do_apply and result["triaged"]:
        click.echo(_style(
            "\n(dry-run) 未执行任何处置；加 --apply 真执行 + 销账。", fg="yellow",
        ))
    elif do_apply:
        click.echo(_style("\n[OK] auto-triage applied", fg="green", bold=True))


# ═══ omni debt digest ════════════════════════════════════════════


@cmd_debt.command("digest")
@click.option("--date", "date_str", type=str, default=None,
              help="digest 日期（YYYY-MM-DD，默认今天）")
@click.option("--root", type=str, default=_DEFAULT_ROOT, help="项目根目录")
@click.option("--json", "json_out", is_flag=True, default=False)
def cmd_debt_digest(date_str: str | None, root: str, json_out: bool):
    """生成/刷新一屏分诊日报：处置统计 + needs_human 清单 + 新增趋势。

    \b
    落盘 data/services/tech_debt/digest-<date>.md。
    auto-triage 跑完会自动刷新当天 digest；本命令用于单独补生成。
    """
    from omnicompany.packages.services._diagnosis.tech_debt import write_digest
    from omnicompany.packages.services._diagnosis.tech_debt.autotriage import (
        build_digest_markdown,
    )

    root_path = Path(root)
    if not date_str:
        from datetime import datetime, timezone
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md = build_digest_markdown(root_path, date=date_str)
    if json_out:
        click.echo(_json_mod.dumps({"date": date_str, "markdown": md},
                                   ensure_ascii=False, indent=2))
        return
    rel = write_digest(root_path, date=date_str)
    click.echo(md)
    click.echo(_style(f"\n[OK] digest → {rel}", fg="green"))
