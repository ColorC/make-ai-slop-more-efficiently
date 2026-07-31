# [OMNI] origin=claude-code ts=2026-04-25T00:00:00Z
# [OMNI] material_id="material:cli.commands.docauthor.auto_doc_pipeline.py"
"""omni docauthor — 自动文档作者命令组.

**面向 L2 (我) 的工作流** (不是给普通人用):
  - scan:  扫全仓赤字 · 列出需要 docauthor 处理的 skeleton DESIGN + 缺 manifest 包
  - run:   跑单目标一个 kind (manifest|design), bus 驱动 · MaterialDispatcher 激活 ·
           SQLiteBus 落盘 data/events.db 全留档
  - run-all: 批量跑所有赤字目标, 直接落 src/ (用户 2026-04-25 硬指示 "直接做完")
  - observe: 从 data/events.db 查指定 target 的最近 docauthor 事件链
  - issues:  查指定 target 最近 Reviewer 的 issue 全量 (含 evidence)

CLI 是 L2 诊断/观测入口. 不打分, 不压缩语义; 让我自己看完整信号.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

from omnicompany.core.config import omni_workspace_root, resolve_unified_db_path


REPO_ROOT = omni_workspace_root()


# ═══════════════════════════════════════════════════════════════════
# 工具: 扫描赤字目标
# ═══════════════════════════════════════════════════════════════════

def _scan_missing_manifests(repo_root: Path) -> list[dict]:
    """列出所有 services/domains 包里缺 .omni/manifest.yaml 的目标."""
    targets: list[dict] = []
    roots = [
        ("packages/services", "service"),
        ("packages/domains", "domain"),
    ]
    for rel_root, kind in roots:
        abs_root = repo_root / "src/omnicompany" / rel_root
        if not abs_root.exists():
            continue
        for pkg_dir in sorted(abs_root.iterdir()):
            if not pkg_dir.is_dir() or pkg_dir.name.startswith(("_", ".")):
                continue
            # 顶层 svc: packages/services/<svc>/
            _check_and_record(pkg_dir, repo_root, kind, targets)
            # domain 子包: packages/domains/<dom>/<subpkg>/
            if rel_root == "packages/domains":
                for sub in sorted(pkg_dir.iterdir()):
                    if sub.is_dir() and not sub.name.startswith(("_", ".", "__")):
                        _check_and_record(sub, repo_root, "domain_subpkg", targets)
    return targets


def _check_and_record(pkg_dir: Path, repo_root: Path, kind: str, acc: list[dict]) -> None:
    manifest = pkg_dir / ".omni" / "manifest.yaml"
    design = pkg_dir / "DESIGN.md"
    rel = pkg_dir.relative_to(repo_root).as_posix()
    # 过滤: 本身没任何 Python/Markdown 文件的空目录
    has_source = any(pkg_dir.glob("*.py")) or any(pkg_dir.glob("*.md"))
    if not has_source:
        return
    if not manifest.exists():
        acc.append({
            "target": rel,
            "kind_needed": "manifest",
            "pkg_kind": kind,
            "has_design": design.exists(),
        })


def _scan_skeleton_designs(repo_root: Path) -> list[dict]:
    """通过 guardian 规则查 skeleton status 的 DESIGN.md."""
    targets: list[dict] = []
    design_files = list((repo_root / "src/omnicompany").rglob("DESIGN.md"))
    for d in design_files:
        if "_archive" in d.parts or "_graveyard" in d.parts or "gold_samples" in d.parts:
            continue
        try:
            text = d.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "status=skeleton" in text[:500]:
            rel_dir = d.parent.relative_to(repo_root).as_posix()
            targets.append({
                "target": rel_dir,
                "kind_needed": "design",
                "design_path": d.relative_to(repo_root).as_posix(),
            })
    return targets


# ═══════════════════════════════════════════════════════════════════
# CLI 组
# ═══════════════════════════════════════════════════════════════════

@click.group("docauthor")
def cmd_docauthor():
    """docauthor · 自动文档作者 (bus 驱动 · L2 工作流)."""


# ─── scan ─────────────────────────────────────────────────────────

@cmd_docauthor.command("scan")
@click.option("--kind", type=click.Choice(["manifest", "design", "readme", "skill", "all"]),
              default="all", help="扫哪种赤字")
@click.option("--json-output", is_flag=True, help="JSON 格式输出 (供我程序化读)")
@click.option("--repo-root", type=click.Path(exists=True, file_okay=False),
              default=str(REPO_ROOT), help="仓库根")
def cmd_scan(kind: str, json_output: bool, repo_root: str):
    """扫全仓赤字 (缺 manifest / skeleton DESIGN)."""
    root = Path(repo_root).resolve()
    deficits: list[dict] = []
    if kind in {"manifest", "all"}:
        deficits.extend(_scan_missing_manifests(root))
    if kind in {"design", "all"}:
        deficits.extend(_scan_skeleton_designs(root))

    if json_output:
        click.echo(json.dumps(deficits, ensure_ascii=False, indent=2))
        return

    click.echo(click.style(f"\ndocauthor 赤字扫描 (kind={kind})", fg="cyan", bold=True))
    by_kind: dict[str, list[dict]] = {}
    for d in deficits:
        by_kind.setdefault(d["kind_needed"], []).append(d)
    for k, xs in by_kind.items():
        click.echo(click.style(f"\n[{k}] {len(xs)} 份赤字:", fg="yellow"))
        for d in xs:
            extra = f"  (has_design={d.get('has_design')})" if k == "manifest" else ""
            click.echo(f"  {d['target']}{extra}")
    click.echo(f"\n总计: {len(deficits)} 份赤字")


# ─── run ──────────────────────────────────────────────────────────

@cmd_docauthor.command("run")
@click.argument("kind", type=click.Choice(["manifest", "design", "readme", "skill"]))
@click.argument("target", type=str)
@click.option("--max-refine", type=int, default=1, help="refine 上限 (默认 1)")
@click.option("--dry-run", is_flag=True, help="不写盘 src/, 仅观察事件流")
@click.option("--repo-root", type=click.Path(exists=True, file_okay=False),
              default=str(REPO_ROOT), help="仓库根")
def cmd_run(kind: str, target: str, max_refine: int, dry_run: bool, repo_root: str):
    """跑单目标 docauthor job (bus 驱动 · SQLiteBus 落 data/events.db).

    示例:
      omni docauthor run manifest src/omnicompany/packages/services/foo
      omni docauthor run design src/omnicompany/packages/domains/voxelcraft/item --max-refine 2
    """
    from omnicompany.packages.services._authoring.docauthor.team import run_job, summarize_events

    root = Path(repo_root).resolve()
    click.echo(click.style(f"\n> docauthor run {kind} {target}", fg="cyan", bold=True))
    click.echo(f"  max_refine={max_refine} · dry_run={dry_run}")
    click.echo("  bus: data/events.db (SQLiteBus 默认)\n")

    events = asyncio.run(run_job(
        kind=kind, target=target,
        max_refine_iters=max_refine,
        dry_run=dry_run,
        repo_root=root,
    ))
    summary = summarize_events(events)
    final = summary.get("final_event_payload") or {}

    click.echo(f"事件总数: {summary['total_events']}")
    for etype, cnt in summary["event_count_by_type"].items():
        click.echo(f"  {etype}: {cnt}")
    click.echo()
    click.echo(f"refine iter 观察到的最大值: {summary['refine_iters_observed']}")
    click.echo(f"终局 status: {final.get('terminal_status', '(no final)')}")
    click.echo(f"落盘: {final.get('write_status', '?')} → {final.get('landing_rel', '?')}")
    click.echo(f"issue 计数: {final.get('issue_counts', {})}")

    issues = final.get("issues") or []
    if issues:
        click.echo(click.style("\n终局 issues (完整, 非压缩):", fg="yellow"))
        for it in issues:
            click.echo(f"  [{it.get('severity','?')}] {it.get('field','?')}")
            click.echo(f"    msg: {it.get('message','')}")
            if it.get("evidence"):
                click.echo(f"    evidence: {it['evidence']}")
            if it.get("fix_hint"):
                click.echo(f"    fix: {it['fix_hint']}")

    if final.get("llm_notes"):
        click.echo(click.style("\nReviewer 总体语义描述:", fg="cyan"))
        click.echo(f"  {final['llm_notes']}")

    # 退出码: terminal=passed → 0; exhausted → 1
    sys.exit(0 if final.get("passed") else 1)


# ─── run-all ──────────────────────────────────────────────────────

@cmd_docauthor.command("run-all")
@click.option("--kind", type=click.Choice(["manifest", "design", "readme", "skill", "all"]),
              default="all", help="只处理某种赤字")
@click.option("--max-refine", type=int, default=1)
@click.option("--dry-run", is_flag=True, help="不写盘 src/")
@click.option("--limit", type=int, default=0, help="> 0 时只跑前 N 个 (调试)")
@click.option("--continue-on-fail", is_flag=True, default=True,
              help="单目标 exhausted 不停批, 默认开")
@click.option("--repo-root", type=click.Path(exists=True, file_okay=False),
              default=str(REPO_ROOT))
def cmd_run_all(kind: str, max_refine: int, dry_run: bool, limit: int,
                continue_on_fail: bool, repo_root: str):
    """批量扫赤字 + 逐个跑 docauthor job + 批汇总报告."""
    from omnicompany.packages.services._authoring.docauthor.team import run_job, summarize_events

    root = Path(repo_root).resolve()
    deficits: list[dict] = []
    if kind in {"manifest", "all"}:
        deficits.extend(_scan_missing_manifests(root))
    if kind in {"design", "all"}:
        deficits.extend(_scan_skeleton_designs(root))
    if limit > 0:
        deficits = deficits[:limit]

    click.echo(click.style(f"\n> docauthor run-all ({len(deficits)} 目标)",
                           fg="cyan", bold=True))
    click.echo(f"  max_refine={max_refine} · dry_run={dry_run}\n")

    results: list[dict] = []
    for i, d in enumerate(deficits):
        target = d["target"]
        kind_needed = d["kind_needed"]
        click.echo(click.style(
            f"\n[{i+1}/{len(deficits)}] {kind_needed} :: {target}", fg="cyan"))
        try:
            events = asyncio.run(run_job(
                kind=kind_needed, target=target,
                max_refine_iters=max_refine,
                dry_run=dry_run, repo_root=root,
            ))
            summary = summarize_events(events)
            final = summary.get("final_event_payload") or {}
            results.append({
                "target": target, "kind": kind_needed,
                "terminal_status": final.get("terminal_status"),
                "iter": final.get("iter"),
                "write_status": final.get("write_status"),
                "landing_rel": final.get("landing_rel"),
                "issue_counts": final.get("issue_counts", {}),
                "passed": bool(final.get("passed")),
                "event_count": summary["total_events"],
            })
            click.echo(f"  → {final.get('terminal_status','?')} · "
                       f"iter={final.get('iter','?')} · "
                       f"issues={final.get('issue_counts', {})}")
        except Exception as e:
            results.append({
                "target": target, "kind": kind_needed,
                "error": f"{type(e).__name__}: {e}",
            })
            click.echo(click.style(f"  ✗ ERROR: {type(e).__name__}: {e}", fg="red"))
            if not continue_on_fail:
                break

    # 汇总
    click.echo(click.style("\n═══ 汇总 ═══", fg="cyan", bold=True))
    passed_count = sum(1 for r in results if r.get("passed"))
    exhausted_count = sum(1 for r in results
                          if (r.get("terminal_status") or "").startswith("exhausted"))
    err_count = sum(1 for r in results if r.get("error"))
    click.echo(f"passed: {passed_count}, exhausted: {exhausted_count}, error: {err_count}, "
               f"total: {len(results)}")

    # 写批报告
    report_dir = root / "data/services/docauthor/batch_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = report_dir / f"run_all_{ts}.json"
    report_path.write_text(
        json.dumps({"results": results, "ts": ts,
                    "max_refine": max_refine, "dry_run": dry_run},
                   ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    click.echo(f"\n批报告: {report_path.relative_to(root).as_posix()}")


# ─── observe ──────────────────────────────────────────────────────

@cmd_docauthor.command("observe")
@click.argument("target", type=str)
@click.option("--n", type=int, default=3, help="最近 N 个 job · 默认 3")
@click.option("--json-output", is_flag=True)
def cmd_observe(target: str, n: int, json_output: bool):
    """从 data/events.db 查指定 target 的最近 docauthor 事件链."""
    import sqlite3

    db_path = resolve_unified_db_path()
    if not db_path.exists():
        click.echo(click.style(f"events.db 不存在: {db_path}", fg="red"))
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # 找最近 N 个 job-start 事件 (docauthor.manifest-request 或 design-request · source=dispatcher.initial)
    # 匹配 target_path 在 payload 里
    cur = conn.execute("""
        SELECT trace_id, event_type, source, timestamp, data
        FROM events
        WHERE event_type IN ('docauthor.manifest-request', 'docauthor.design-request')
          AND source = 'dispatcher.initial'
          AND data LIKE ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (f"%{target}%", n))
    initial_rows = cur.fetchall()
    if not initial_rows:
        click.echo(f"未找到 target={target} 的 docauthor job")
        return

    out_list = []
    for row in initial_rows:
        trace_id = row["trace_id"]
        # 拉该 trace 下所有事件
        chain_cur = conn.execute("""
            SELECT event_type, source, timestamp, data
            FROM events
            WHERE trace_id = ?
            ORDER BY timestamp
        """, (trace_id,))
        chain = [dict(r) for r in chain_cur.fetchall()]
        # 找子 job (refine)
        child_jobs = set()
        for ch in chain:
            try:
                d = json.loads(ch["data"])
                parent = d.get("payload", {}).get("_parent_job_id")
                if parent == trace_id:
                    child_jobs.add(ch.get("trace_id"))
            except (json.JSONDecodeError, AttributeError):
                pass
        out_list.append({
            "trace_id": trace_id,
            "initial_ts": row["timestamp"],
            "initial_type": row["event_type"],
            "event_count": len(chain),
            "event_types": [ch["event_type"] for ch in chain],
        })

    if json_output:
        click.echo(json.dumps(out_list, ensure_ascii=False, indent=2))
        return

    click.echo(click.style(f"\n> docauthor observe {target} (最近 {n})",
                           fg="cyan", bold=True))
    for item in out_list:
        click.echo(f"\n  trace_id: {item['trace_id']}")
        click.echo(f"  start:    {item['initial_ts']}")
        click.echo(f"  events:   {item['event_count']}")
        click.echo(f"  chain:    {' → '.join(item['event_types'])}")


# ─── issues ──────────────────────────────────────────────────────

@cmd_docauthor.command("issues")
@click.argument("target", type=str)
def cmd_issues(target: str):
    """查指定 target 最近 review-verdict 的 issue 全量 (含 evidence)."""
    import sqlite3

    db_path = resolve_unified_db_path()
    if not db_path.exists():
        click.echo(click.style(f"events.db 不存在: {db_path}", fg="red"))
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT timestamp, data
        FROM events
        WHERE event_type = 'docauthor.review-verdict'
          AND data LIKE ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (f"%{target}%",))
    row = cur.fetchone()
    if not row:
        click.echo(f"未找到 target={target} 的 review-verdict")
        return
    data = json.loads(row["data"])
    payload = data.get("payload", {})
    click.echo(click.style(f"\n> docauthor issues {target}", fg="cyan", bold=True))
    click.echo(f"ts={row['timestamp']} · target_type={payload.get('target_type')} · "
               f"passed={payload.get('passed')} · iter={payload.get('iter')}")
    click.echo(f"counts: {payload.get('counts', {})}")
    click.echo()
    for it in payload.get("issues", []):
        click.echo(f"[{it.get('severity','?')}] {it.get('field','?')}")
        click.echo(f"  msg: {it.get('message','')}")
        if it.get("evidence"):
            click.echo(f"  evidence: {it['evidence']}")
        if it.get("fix_hint"):
            click.echo(f"  fix: {it['fix_hint']}")
        click.echo()
    if payload.get("llm_notes"):
        click.echo(click.style("Reviewer 总评:", fg="cyan"))
        click.echo(f"  {payload['llm_notes']}")


__all__ = ["cmd_docauthor"]
