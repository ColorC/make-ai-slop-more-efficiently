# [OMNI] origin=claude-code ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:cli.commands.guardian.health_and_patrol.orchestrator.py"
"""omni guardian — 守护检查命令组

子命令:
    omni guardian health [--root] [--fix]       完整运行守护检查管线（原有功能）
    omni guardian patrol [--root] [--full] ...  OmniPatrol 规则引擎巡逻
    omni guardian violations [--root] [--rule]  查看已记录的违规列表
    omni guardian daemon [--interval]           后台周期巡逻守护进程
    omni guardian stamp <file> [--origin ...]   补打 OmniMark 身份头
    omni guardian stamp-dir <dir> [--dry-run]   批量补打目录下缺头文件
    omni guardian who <file>                    查看文件身份信息
"""
import asyncio
import json
import sys
from pathlib import Path

import click

from omnicompany.core.config import omni_workspace_root


_DEFAULT_ROOT = str(omni_workspace_root())


@click.group("guardian")
def cmd_guardian():
    """守护检查命令组：架构健康 + 自动巡逻 + 违规查询。"""


# 引入 guardian_extensions 子命令 (tag 一致性等扫描规则, 2026-05-02 加)
try:
    from omnicompany.cli.commands.guardian_extensions import cmd_check_tag_consistency
    cmd_guardian.add_command(cmd_check_tag_consistency)
except ImportError:
    pass


# ─── guardian health（原有功能，保持兼容）─────────────────────

@cmd_guardian.command("health")
@click.option("--root", type=str, default=_DEFAULT_ROOT,
              help="项目根目录")
@click.option("--fix", is_flag=True, default=False,
              help="自动清理 high severity 的根目录散文件（移动到 data/_archive/）")
def cmd_guardian_health(root: str, fix: bool):
    """运行守护检查管线：文件系统污染 + 架构规范 + 健康报告。"""
    from omnicompany.core.registry import discover
    from omnicompany.core.dispatch import dispatch

    discover()

    click.echo(click.style("> guardian health check", fg="cyan", bold=True))
    click.echo(f"  root: {root}")
    click.echo()

    result = asyncio.run(dispatch("guardian", {"project_root": root}))

    if not isinstance(result, dict):
        result = getattr(result, "output", {}) if hasattr(result, "output") else {}

    report = result.get("report", "(无报告)")
    total_issues = result.get("total_issues", 0)
    # 契约变更 #01 (2026-04-25): 不打分. 读 verdict + counts + issues, 不读 health_score.
    verdict = result.get("verdict", "uncertain")
    counts = result.get("counts") or {"critical": 0, "major": 0, "minor": 0}
    issues = result.get("issues") or []
    passed = bool(result.get("passed", counts.get("critical", 0) == 0))

    click.echo(report)
    click.echo()

    # 语义标签 · verdict 彩色
    verdict_color = {
        "healthy": "green",
        "unhealthy": "red",
        "uncertain": "yellow",
    }.get(verdict, "cyan")
    click.echo(click.style(f"verdict: {verdict}", fg=verdict_color, bold=True))

    # counts 分类块 · 不打分
    counts_color = "red" if counts.get("critical", 0) > 0 else (
        "yellow" if counts.get("major", 0) > 0 else "green"
    )
    click.echo(click.style(
        f"counts: critical={counts.get('critical', 0)} · "
        f"major={counts.get('major', 0)} · "
        f"minor={counts.get('minor', 0)} · "
        f"total={total_issues}",
        fg=counts_color, bold=True,
    ))

    # 列出前 10 条 issue (全量语义, 不压缩) · 每条附 evidence + fix_hint
    if issues:
        click.echo()
        click.echo(click.style("issues (全量 · 含 evidence):", fg="cyan"))
        for i, it in enumerate(issues[:10]):
            sev = it.get("severity", "?")
            sev_color = {
                "critical": "red", "major": "yellow", "minor": "cyan",
            }.get(sev, None)
            line = f"  [{sev}] {it.get('field', '?')}: {it.get('message', '')}"
            click.echo(click.style(line, fg=sev_color) if sev_color else line)
            if it.get("evidence"):
                click.echo(f"      evidence: {it['evidence']}")
            if it.get("fix_hint"):
                click.echo(f"      fix: {it['fix_hint']}")
        if len(issues) > 10:
            click.echo(f"  ... 另外 {len(issues) - 10} 条")

    if not fix or total_issues == 0:
        return

    click.echo()
    click.echo(click.style("正在自动清理...", fg="yellow"))

    import shutil

    archive_dir = Path(root) / "data" / "_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    fs_issues = result.get("fs_issues", [])
    cleaned = 0
    for issue in fs_issues:
        if issue.get("severity") != "high":
            continue
        if issue.get("category") != "root_contamination":
            continue
        rel_path = issue.get("path", "")
        src_path = Path(root) / rel_path
        if src_path.exists() and src_path.is_file():
            dst = archive_dir / src_path.name
            try:
                shutil.move(str(src_path), str(dst))
                click.echo(f"  移动: {rel_path} → data/_archive/{src_path.name}")
                cleaned += 1
            except Exception as e:
                click.echo(click.style(f"  失败: {rel_path}: {e}", fg="red"))

    click.echo(f"\n清理完成: {cleaned} 个文件已移动到 data/_archive/")


# ─── guardian report (2026-04-25 · 人类一手观察 markdown 聚合) ──────

@cmd_guardian.command("report")
@click.option("--out", "out_path", type=click.Path(), default=None,
              help="自定义输出路径 · 默认 data/services/guardian/reports/report-<ts>.md + latest.md")
@click.option("--with-llm-prose", is_flag=True, default=False,
              help="(留 hook · 本次未实) 加 LLM 自然语言开篇总结")
@click.option("--quiet", is_flag=True, default=False,
              help="只写文件 · stdout 仅显路径")
def cmd_guardian_report(out_path: str | None, with_llm_prose: bool, quiet: bool):
    """聚合 guardian 多源一手信息 (规则扫描 + LLM patrol + audit 判定 + docauthor 队列)
    渲染 markdown 报告. 走 MaterialDispatcher · 单命令 · 非常驻.

    无参跑: `omni guardian report`
    自定义出: `omni guardian report --out /tmp/myreport.md`
    """
    from omnicompany.bus.memory import MemoryBus
    from omnicompany.packages.services._core.omnicompany import MaterialDispatcher
    from omnicompany.packages.services._core.guardian.workers.report_writer import GuardianReportWorker

    if not quiet:
        click.echo(click.style("> guardian report · 一手聚合", fg="cyan", bold=True))

    worker = GuardianReportWorker()
    dispatcher = MaterialDispatcher(workers=[worker], bus=MemoryBus(), max_iterations=10)
    events = asyncio.run(dispatcher.run_job(
        initial_material_id="guardian.report-request",
        initial_payload={"with_llm_prose": with_llm_prose},
    ))

    final = next((e for e in events if e.event_type == "guardian.report-output"), None)
    if not final:
        click.echo(click.style("✗ GuardianReportWorker 未产 output (查 bus events)", fg="red"))
        sys.exit(1)
    payload = final.payload or {}

    report_md = payload.get("report_md", "")
    default_path = omni_workspace_root() / payload.get("report_path", "")

    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(report_md, encoding="utf-8")
        if not quiet:
            click.echo(f"自定义输出: {out_path}")
    if not quiet:
        click.echo(f"默认报告: {default_path}")
        click.echo("latest:   data/services/guardian/reports/latest.md")
        click.echo()
        click.echo(click.style("数据源计数:", fg="cyan"))
        for k, v in (payload.get("source_counts") or {}).items():
            click.echo(f"  {k}: {v}")
    else:
        click.echo(str(default_path))


# prompt-scan 命令移除 (2026-05-05 诊断重制 step 8) — prompt_antipattern_scanner LLM 反模式扫归档,
# 概念并入 doctor _hypothesis/ (反模式 = 复杂假设型). 详 docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/plan.md


# ─── guardian patrol（新增：OmniPatrol 规则引擎）────────────────

@cmd_guardian.command("patrol")
@click.option("--root", type=str, default=_DEFAULT_ROOT,
              help="项目根目录")
@click.option("--full", "full_scan", is_flag=True, default=False,
              help="全量扫描整个 src/ 目录（默认只扫描 git diff 变更文件）")
@click.option("--commits", "n_commits", type=int, default=1,
              help="回溯已 commit 的 N 个版本（仅在非 --full 模式下有效）")
@click.option("--no-uncommitted", "skip_uncommitted", is_flag=True, default=False,
              help="跳过未 commit 的工作树变更")
@click.option("--staged-only", "staged_only", is_flag=True, default=False,
              help="只扫 git index 里的 staged 文件（供 pre-commit hook 使用）")
@click.option("--phase2", "phase2", is_flag=True, default=False,
              help="启用 OmniTow Phase 2：对 pilot_rules 里的规则真正 quarantine/tombstone 文件")
@click.option("--llm", "use_llm", is_flag=True, default=False,
              help="启用 LLM Judge（试点）：对新增文件进行智能审查（慢，消耗 token）")
@click.option("--llm-all", "llm_all", is_flag=True, default=False,
              help="LLM 审查所有变更文件（而非仅新增文件，配合 --llm 使用）")
@click.option("--pilot", "pilot_path", type=str, default=None,
              help="LLM 试点路径前缀（例: src/omnicompany/packages/domains/demogame/）")
@click.option("--json-out", "json_output", is_flag=True, default=False,
              help="以 JSON 格式输出（便于机器处理）")
def cmd_guardian_patrol(
    root: str,
    full_scan: bool,
    n_commits: int,
    skip_uncommitted: bool,
    staged_only: bool,
    phase2: bool,
    use_llm: bool,
    llm_all: bool,
    pilot_path: str | None,
    json_output: bool,
):
    """OmniPatrol 巡逻：规则引擎 + LLM Judge（试点），warn-only，不修改文件。

    \b
    示例：
      omni guardian patrol                          # 扫描 git diff 变更（纯规则）
      omni guardian patrol --full                   # 全量扫描 src/
      omni guardian patrol --commits 5              # 回溯 5 个 commit
      omni guardian patrol --llm                    # 规则 + LLM 审查新增文件
      omni guardian patrol --llm --llm-all          # LLM 审查所有变更文件
      omni guardian patrol --llm --pilot src/omnicompany/packages/domains/demogame/
      omni guardian patrol --json-out               # JSON 输出
    """
    from omnicompany.packages.services._core.guardian import (
        run_patrol,
        format_patrol_report,
    )

    pilot_paths = (pilot_path,) if pilot_path else None

    if not json_output:
        mode_desc = "全量扫描 src/" if full_scan else f"git diff 变更（最近 {n_commits} commit）"
        llm_desc = ""
        if use_llm:
            scope = "所有变更文件" if llm_all else "新增文件"
            zone = pilot_path or "默认试点区(packages/ + runtime/)"
            llm_desc = f"  LLM Judge: 开启 | 范围={scope} | 试点={zone}"
        click.echo(click.style("> OmniPatrol 巡逻", fg="cyan", bold=True))
        click.echo(f"  root: {root}  |  模式: {mode_desc}")
        if llm_desc:
            click.echo(click.style(llm_desc, fg="yellow"))
        click.echo()

    result = run_patrol(
        project_root=root,
        full_scan=full_scan,
        committed=True,
        uncommitted=not skip_uncommitted,
        n_commits=n_commits,
        use_llm=use_llm,
        llm_new_only=not llm_all,
        llm_pilot_paths=pilot_paths,
        staged_only=staged_only,
        tow_phase2=phase2,
    )

    if json_output:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 终端彩色输出
    use_color = sys.stdout.isatty()
    report_text = format_patrol_report(result, color=use_color)
    click.echo(report_text)

    # LLM 审查统计
    if use_llm and result.get("llm_files_judged", 0) > 0:
        click.echo(f"  LLM 审查了 {result['llm_files_judged']} 个文件（ticket 带 LLM- 前缀）")
    click.echo()

    # 退出码：有 CRITICAL/HIGH 违规时返回 1（便于 CI 集成）
    bsev = result.get("by_severity", {})
    if bsev.get("CRITICAL", 0) > 0 or bsev.get("HIGH", 0) > 0:
        sys.exit(1)


# ─── guardian violations（查看历史违规日志）──────────────────────

@cmd_guardian.command("violations")
@click.option("--root", type=str, default=_DEFAULT_ROOT,
              help="项目根目录")
@click.option("--rule", "rule_id", type=str, default=None,
              help="筛选特定规则 ID（如 OMNI-003）")
@click.option("--last", "last_n", type=int, default=5,
              help="显示最近 N 次巡逻的结果")
def cmd_guardian_violations(root: str, rule_id: str | None, last_n: int):
    """查看历史巡逻违规记录（从 logs/patrol/ 读取）。"""
    log_dir = Path(root) / "logs" / "patrol"
    if not log_dir.exists():
        click.echo("暂无巡逻日志（先运行 omni guardian patrol）")
        return

    log_files = sorted(log_dir.glob("patrol-*.json"), reverse=True)[:last_n]
    if not log_files:
        click.echo("暂无巡逻日志")
        return

    total_v = 0
    for log_file in log_files:
        try:
            data = json.loads(log_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        violations = data.get("violations", [])
        if rule_id:
            violations = [v for v in violations if v.get("rule_id") == rule_id]

        if not violations:
            continue

        click.echo(click.style(
            f"[{data.get('scan_ts', log_file.stem)}]  "
            f"扫描文件: {data.get('files_scanned', '?')}  "
            f"违规: {len(violations)}",
            fg="cyan",
        ))
        for v in violations:
            sev_colors = {"CRITICAL": "red", "HIGH": "yellow", "MEDIUM": "blue", "LOW": "cyan"}
            sev = v.get("severity", "?")
            color = sev_colors.get(sev, "white")
            click.echo(
                f"  {click.style(sev, fg=color)}  "
                f"{v.get('rule_id', '?')}  "
                f"{v.get('path', '?')}"
            )
            click.echo(f"    {v.get('message', '')}")
            total_v += 1
        click.echo()

    if total_v == 0:
        rule_hint = f"（规则 {rule_id}）" if rule_id else ""
        click.echo(f"最近 {last_n} 次巡逻无违规记录{rule_hint}")


# ─── guardian daemon（长驻巡逻守护进程）─────────────────────────

@cmd_guardian.command("daemon")
@click.option("--root", type=str, default=_DEFAULT_ROOT,
              help="项目根目录")
@click.option("--wake-interval", type=int, default=10,
              help="sentinel 唤醒检查间隔 (秒, 默认 10)")
@click.option("--cooldown", type=int, default=300,
              help="两次 patrol 最小间隔 (秒, 默认 300 = 5 分钟)")
@click.option("--llm-cooldown", type=int, default=1800,
              help="两次 LLM patrol 最小间隔 (秒, 默认 1800 = 30 分钟)")
@click.option("--once", is_flag=True, default=False,
              help="只跑一次条件评估就退出 (测试/诊断用)")
@click.option("--force", is_flag=True, default=False,
              help="即使已有 daemon 在运行也强制启动 (会让旧 daemon 自退)")
def cmd_guardian_daemon(root, wake_interval, cooldown, llm_cooldown, once, force):
    """OmniSentinel 长驻巡逻守护进程 (2026-04-10 重构).

    \b
    工作模型:
      - 独立长驻进程, 不随 pipeline / assistant 退出
      - 每次核心组件启动时会写 .omni/core_activity_ts.json,
        sentinel 读到后在冷却期过后进行一次增量 patrol
        (只扫 mtime > last_patrol_ts 的文件)
      - 两次 patrol 之间有 cooldown 节流 (默认 5 min)
      - 两次 LLM-heavy patrol 之间有更长 llm_cooldown (默认 30 min)

    \b
    示例:
      omni guardian daemon                    # 默认参数启动长驻 sentinel
      omni guardian daemon --once             # 只评估一轮后退出 (诊断)
      omni guardian daemon --cooldown 60      # 1 分钟最短 patrol 间隔 (测试)
      omni guardian daemon --force            # 覆盖已有 daemon
    """
    from pathlib import Path
    from omnicompany.packages.services._core.guardian.sentinel import (
        daemon_loop, is_daemon_alive, read_pid_file, stop_daemon,
    )
    root_p = Path(root)

    if is_daemon_alive(root_p) and not once and not force:
        existing_pid = read_pid_file(root_p)
        click.echo(click.style(
            f"! 已有 sentinel daemon 在运行 (pid={existing_pid})",
            fg="yellow",
        ))
        click.echo("  - 查看状态: 读 .omni/sentinel_state.json")
        click.echo("  - 停止:     omni guardian daemon --force (下面会让旧的自退)")
        click.echo("  - 或者跑一次性检查: omni guardian daemon --once")
        return

    if force and is_daemon_alive(root_p):
        click.echo("[sentinel] --force: 停止已有 daemon...")
        stop_daemon(root_p)

    click.echo(click.style("> OmniSentinel daemon 启动", fg="cyan", bold=True))
    click.echo(f"  root:           {root}")
    click.echo(f"  wake_interval:  {wake_interval}s")
    click.echo(f"  cooldown:       {cooldown}s")
    click.echo(f"  llm_cooldown:   {llm_cooldown}s")
    click.echo(f"  mode:           {'once' if once else 'persistent'}")
    click.echo("  Ctrl-C 停止\n")

    try:
        daemon_loop(
            root_p,
            wake_interval_s=wake_interval,
            cooldown_s=cooldown,
            llm_cooldown_s=llm_cooldown,
            once=once,
            verbose=True,
        )
    except KeyboardInterrupt:
        click.echo("\n  daemon 已停止")


# ─── guardian stamp（补打 OmniMark 身份头）────────────────────────

@cmd_guardian.command("stamp")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--origin", type=str, default="unknown",
              help="来源标识（human / claude-code / unknown 等）")
@click.option("--domain", type=str, default="",
              help="所属域（如 omnicompany/guardian）")
@click.option("--agent", type=str, default="",
              help="产生该文件的模型（如 claude-sonnet-4-6）")
@click.option("--trace", type=str, default="",
              help="产生该文件的 trace_id")
@click.option("--node", type=str, default="",
              help="产生该文件的节点 ID")
@click.option("--overwrite", is_flag=True, default=False,
              help="强制覆盖已有 [OMNI] 头")
def cmd_guardian_stamp(file_path, origin, domain, agent, trace, node, overwrite):
    """为单个文件补打 OmniMark 身份头。

    \b
    示例：
      omni guardian stamp src/omnicompany/packages/xxx/yyy.py
      omni guardian stamp src/xxx.py --origin human --domain omnicompany/core
      omni guardian stamp src/xxx.py --overwrite   # 强制更新已有头
    """
    from omnicompany.core.omnimark import stamp_file, parse_omnimark

    p = Path(file_path)
    existing = parse_omnimark(p)

    if existing is not None and not overwrite:
        click.echo(
            click.style("[SKIP] ", fg="yellow") +
            f"{p}  已有 [OMNI] 头 (origin={existing.origin}，用 --overwrite 强制更新)"
        )
        return

    ok = stamp_file(p, origin=origin, domain=domain, agent=agent,
                    trace=trace, node=node)
    if ok:
        click.echo(click.style("[STAMP] ", fg="green") + str(p))
    else:
        click.echo(click.style("[FAIL]  ", fg="red") + f"{p}  写入失败")


# ─── guardian stamp-dir（批量补打）────────────────────────────────

@cmd_guardian.command("stamp-dir")
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option("--origin", type=str, default="unknown",
              help="来源标识（默认 unknown，status=pending-review）")
@click.option("--ext", type=str, default=".py",
              help="文件扩展名过滤（默认 .py，多个用逗号分隔：.py,.yaml）")
@click.option("--dry-run", is_flag=True, default=False,
              help="只列出缺头文件，不实际写入")
@click.option("--skip-graveyard", is_flag=True, default=True,
              help="跳过 _graveyard / _archive 目录（默认开启）")
def cmd_guardian_stamp_dir(directory, origin, ext, dry_run, skip_graveyard):
    """批量为目录下缺少 [OMNI] 头的文件打标。

    \b
    示例：
      omni guardian stamp-dir src/omnicompany/packages/     # 补打所有缺头 .py
      omni guardian stamp-dir src/ --dry-run                # 只看清单不写入
      omni guardian stamp-dir src/ --ext .py,.yaml          # 包含 yaml
    """
    from omnicompany.core.omnimark import stamp_file, parse_omnimark

    exts = {e.strip() for e in ext.split(",")}
    root = Path(directory)
    skip_dirs = {"_graveyard", "_archive", "__pycache__"} if skip_graveyard else {"__pycache__"}

    found = stamped = skipped = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in exts:
            continue
        if any(d in p.parts for d in skip_dirs):
            continue

        found += 1
        existing = parse_omnimark(p)
        if existing is not None:
            skipped += 1
            continue

        if dry_run:
            click.echo(f"  [MISSING] {p.relative_to(root)}")
        else:
            ok = stamp_file(p, origin=origin,
                            status="pending-review" if origin == "unknown" else "active")
            if ok:
                click.echo(click.style("[STAMP] ", fg="green") +
                           str(p.relative_to(root)))
                stamped += 1
            else:
                click.echo(click.style("[FAIL]  ", fg="red") +
                           str(p.relative_to(root)))

    action = "需补打" if dry_run else "已补打"
    click.echo(f"\n扫描: {found}  跳过(已有头): {skipped}  {action}: {found - skipped - (0 if dry_run else 0)}")
    if dry_run and found - skipped > 0:
        click.echo(f"  去掉 --dry-run 实际写入 {found - skipped} 个文件")


# ─── guardian who（查看文件身份）─────────────────────────────────

@cmd_guardian.command("who")
@click.argument("file_path", type=click.Path(exists=True))
def cmd_guardian_who(file_path):
    """查看文件的 OmniMark 身份信息。

    \b
    示例：
      omni guardian who src/omnicompany/packages/services/guardian/patrol.py
    """
    from omnicompany.core.omnimark import parse_omnimark, file_fingerprint

    p = Path(file_path)
    mark = parse_omnimark(p)

    click.echo(click.style(f"[WHO] {p}", fg="cyan", bold=True))

    if mark is None:
        click.echo(click.style("  无 [OMNI] 头 — 身份未知", fg="yellow"))
        click.echo(f"  补打命令: omni guardian stamp {p}")
        return

    rows = [
        ("origin",  mark.origin  or "-"),
        ("domain",  mark.domain  or "-"),
        ("agent",   mark.agent   or "-"),
        ("ts",      mark.ts      or "-"),
        ("trace",   mark.trace   or "-"),
        ("node",    mark.node    or "-"),
        ("status",  mark.status  or "active"),
    ]
    if mark.created_by:
        rows.append(("created_by (旧)", mark.created_by))
    if mark.intent:
        rows.append(("intent (旧)",     mark.intent))

    for k, v in rows:
        if v and v != "-":
            click.echo(f"  {k:<18} {v}")

    click.echo(f"  {'fingerprint':<18} {file_fingerprint(p)}")


# ─── guardian tickets（查看罚单）─────────────────────────────────

@cmd_guardian.command("tickets")
@click.option("--root", type=str, default=_DEFAULT_ROOT)
@click.option("--status", type=str, default=None,
              help="按状态过滤: open / whitelisted / resolved / deleted")
@click.argument("ticket_id", required=False)
def cmd_guardian_tickets(root, status, ticket_id):
    """查看违规罚单（所有 / 按状态 / 单张详情）。

    \b
    示例：
      omni guardian tickets                  # 所有未解决罚单
      omni guardian tickets --status open
      omni guardian tickets TICKET-2026-04-05-001
    """
    from omnicompany.packages.services._core.guardian.tow_truck import OmniTow
    tow = OmniTow(project_root=root)

    if ticket_id:
        data = tow.get_ticket(ticket_id)
        if not data:
            click.echo(f"罚单 {ticket_id} 不存在")
            return
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return

    tickets = tow.list_tickets(status=status)
    if not tickets:
        hint = f"（status={status}）" if status else ""
        click.echo(f"暂无罚单{hint}（先运行 omni guardian patrol）")
        return

    sev_colors = {"CRITICAL": "red", "HIGH": "yellow", "MEDIUM": "blue", "LOW": "cyan"}
    for t in tickets:
        sev = t.get("severity", "?")
        click.echo(
            click.style(f"  {sev:<8}", fg=sev_colors.get(sev, "white")) +
            f"  {t['ticket_id']}  {t['rule']}  {t['path']}"
        )
    click.echo(f"\n共 {len(tickets)} 张罚单")


# ─── guardian restore（恢复隔离文件）─────────────────────────────

@cmd_guardian.command("restore")
@click.argument("ticket_id")
@click.option("--root", type=str, default=_DEFAULT_ROOT)
def cmd_guardian_restore(ticket_id, root):
    """将罚单标记为已解决（Phase 2 后可恢复隔离文件）。

    \b
    示例：
      omni guardian restore TICKET-2026-04-05-001
    """
    from omnicompany.packages.services._core.guardian.tow_truck import OmniTow
    tow = OmniTow(project_root=root)
    ok = tow.resolve_ticket(ticket_id)
    if ok:
        click.echo(click.style(f"[OK] {ticket_id} 已标记为 resolved", fg="green"))
    else:
        click.echo(click.style(f"[FAIL] 罚单 {ticket_id} 不存在或已处理", fg="red"))


# ─── guardian whitelist（临时豁免）────────────────────────────────

@cmd_guardian.command("whitelist")
@click.argument("ticket_id")
@click.option("--root", type=str, default=_DEFAULT_ROOT)
@click.option("--hours", type=int, default=24, help="豁免时长（小时）")
@click.option("--reason", type=str, default="", help="豁免原因（必填，便于审计）")
def cmd_guardian_whitelist(ticket_id, root, hours, reason):
    """为罚单申请临时豁免白名单。

    \b
    示例：
      omni guardian whitelist TICKET-2026-04-05-001 \\
          --hours 4 --reason "正在重构，预计 4h 内完成迁移"
    """
    from omnicompany.packages.services._core.guardian.tow_truck import OmniTow
    tow = OmniTow(project_root=root)
    ok = tow.whitelist_ticket(ticket_id, hours=hours, reason=reason)
    if ok:
        click.echo(click.style(
            f"[OK] {ticket_id} 已加入白名单（{hours}h 后到期）", fg="green"
        ))
        if not reason:
            click.echo(click.style("  建议：下次加上 --reason 说明豁免原因", fg="yellow"))
    else:
        click.echo(click.style(f"[FAIL] 罚单 {ticket_id} 不存在", fg="red"))


# ─── guardian evolution-history（节点违规历史）───────────────────

@cmd_guardian.command("evolution-history")
@click.option("--root", type=str, default=_DEFAULT_ROOT)
@click.argument("node_id", required=False)
def cmd_guardian_evolution_history(root, node_id):
    """查看内部节点的违规矫正历史。

    \b
    示例：
      omni guardian evolution-history                 # 所有节点汇总
      omni guardian evolution-history implementor-router
    """
    from omnicompany.packages.services._core.guardian.evolve_signal import OmniEvolve
    evo = OmniEvolve(project_root=root)

    if node_id:
        history = evo.get_node_history(node_id)
        if not history:
            click.echo(f"节点 {node_id} 暂无违规历史")
            return
        click.echo(click.style(f"[{node_id}]", fg="cyan", bold=True) +
                   f"  pipeline={history.pipeline}"
                   f"  total={history.total_violations}"
                   f"  level={history.escalation_level()}"
                   f"  clean_runs={history.consecutive_clean_runs}")
        for v in history.violations[-5:]:   # 最近 5 条
            applied = " [已矫正]" if v.get("correction_applied") else ""
            click.echo(
                f"  L{v.get('escalation_level', 0)}  {v.get('rule', '?')}"
                f"  {v.get('detected_at', '')[:19]}{applied}"
            )
        # 显示 pending correction
        pc = evo.get_pending_correction(node_id)
        if pc and pc.get("status") == "pending":
            click.echo()
            click.echo(click.style("  [待应用矫正建议]", fg="yellow"))
            click.echo(f"  {pc.get('suggested_correction', '')[:200]}")
            click.echo(f"  应用命令: omni guardian evolution-apply {node_id}")
        return

    # 汇总所有节点
    signals = evo.list_all()
    if not signals:
        click.echo("暂无进化信号记录")
        return
    level_colors = {0: "cyan", 1: "yellow", 2: "red"}
    seen_nodes: dict[str, dict] = {}
    for s in signals:
        nid = s.get("node_id", "?")
        if nid not in seen_nodes or s.get("repeat_count", 0) > seen_nodes[nid].get("repeat_count", 0):
            seen_nodes[nid] = s
    for nid, s in seen_nodes.items():
        lvl = s.get("level", 0)
        click.echo(
            click.style(f"  L{lvl}", fg=level_colors.get(lvl, "white")) +
            f"  {nid:<30}  {s.get('pipeline', '?'):<20}"
            f"  违规{s.get('repeat_count', 0)}次  {s.get('rule', '?')}"
        )


# ─── guardian evolution-apply（应用矫正建议）───────────────────────

@cmd_guardian.command("evolution-apply")
@click.argument("node_id")
@click.option("--root", type=str, default=_DEFAULT_ROOT)
def cmd_guardian_evolution_apply(node_id, root):
    """将节点的 pending 矫正建议标记为已应用。

    实际 SYSTEM_PROMPT 修改需人工完成，此命令只记录确认状态。

    \b
    示例：
      omni guardian evolution-apply implementor-router
    """
    from omnicompany.packages.services._core.guardian.evolve_signal import OmniEvolve
    evo = OmniEvolve(project_root=root)
    pc = evo.get_pending_correction(node_id)
    if not pc:
        click.echo(f"节点 {node_id} 无 pending 矫正建议")
        return
    if pc.get("status") != "pending":
        click.echo(f"矫正建议状态: {pc['status']}（非 pending，无需操作）")
        return

    click.echo(click.style("[矫正建议]", fg="yellow", bold=True))
    click.echo(f"  规则: {pc.get('rule_violated')}  第 {pc.get('repeat_count')} 次违规")
    click.echo()
    click.echo(pc.get("suggested_correction", "（无建议）"))
    click.echo()
    if click.confirm("已手动将上述约束追加到节点 SYSTEM_PROMPT？"):
        ok = evo.apply_correction(node_id)
        if ok:
            click.echo(click.style(f"[OK] {node_id} 矫正已记录为 applied", fg="green"))
        else:
            click.echo(click.style("[FAIL] 标记失败", fg="red"))
    else:
        click.echo("已取消（矫正建议仍为 pending）")


# ─── guardian zombies（process health — Move 8 后引入）────────────

@cmd_guardian.command("zombies")
@click.option("--kill", is_flag=True, default=False,
              help="Kill identified zombie processes (use with care)")
@click.option("--include-listening", is_flag=True, default=False,
              help="Also list dashboard processes that ARE listening (for full inventory)")
def cmd_guardian_zombies(kill: bool, include_listening: bool):
    """扫描 zombie omnicompany 进程（uvicorn dashboard / agent loops 等）。

    \b
    背景:
      Move 8 调查时发现 4 个 uvicorn dashboard backend 从前一天起 zombie 跑着 ——
      不监听任何端口，但 hold 着 SQLite 文件锁，导致 stray events.db 无法归档。
      Multiprocessing child 在 parent 被 kill 后仍然存活，是另一个常见来源。

    \b
    检测策略:
      1. 列出本机所有 python.exe（以及 multiprocessing 子进程）
      2. 筛选命令行包含 omnicompany.dashboard / uvicorn / multiprocessing.spawn
      3. 用 netstat 找出哪些有 LISTEN socket
      4. 没 listen socket 的 dashboard backend = zombie 候选

    \b
    示例:
      omni guardian zombies                    # 只列，不杀
      omni guardian zombies --kill             # 自动 taskkill /F
      omni guardian zombies --include-listening  # 顺便看活的
    """
    import subprocess

    # 1. 收集所有 python 进程的 (pid, cmdline)
    try:
        ps_out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
             "| Select-Object ProcessId, CommandLine "
             "| ConvertTo-Json -Compress"],
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        click.echo(click.style(
            "[FAIL] 无法运行 powershell — 此命令目前只支持 Windows", fg="red"))
        return

    try:
        procs = json.loads(ps_out.decode("utf-8", errors="replace") or "[]")
    except json.JSONDecodeError:
        procs = []
    if isinstance(procs, dict):
        procs = [procs]

    # 2. 收集所有 LISTEN 端口 → pid 映射
    listening_pids: set[int] = set()
    try:
        netstat = subprocess.check_output(["netstat", "-ano"], stderr=subprocess.DEVNULL)
        for line in netstat.decode("utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 5 and "LISTENING" in parts:
                try:
                    listening_pids.add(int(parts[-1]))
                except ValueError:
                    pass
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # 3. 分类
    zombies: list[tuple[int, str]] = []
    alive: list[tuple[int, str]] = []
    other: list[tuple[int, str]] = []

    for p in procs:
        if not isinstance(p, dict):
            continue
        pid = int(p.get("ProcessId") or 0)
        cmd = (p.get("CommandLine") or "").strip()
        if not pid or not cmd:
            continue
        cmd_l = cmd.lower()
        # These dashboard modules intentionally use stdio or a detached local
        # control socket. The absence of a public LISTEN socket is not evidence
        # that they are zombies.
        is_managed_non_http = any(
            marker in cmd_l
            for marker in (
                "omnicompany.dashboard.ccdaemon.mcp_server",
                "omnicompany.dashboard.ccdaemon.pty_host",
                "omnicompany.dashboard.ccdaemon.remote_control",
            )
        )
        is_dashboard = "omnicompany.dashboard" in cmd_l or "uvicorn" in cmd_l
        is_orphan_child = "multiprocessing.spawn" in cmd_l
        is_omni_loop = "omnicompany" in cmd_l and not is_dashboard

        if is_managed_non_http:
            other.append((pid, cmd))
        elif is_dashboard:
            (alive if pid in listening_pids else zombies).append((pid, cmd))
        elif is_orphan_child:
            zombies.append((pid, cmd))
        elif is_omni_loop:
            other.append((pid, cmd))

    # 4. 输出
    if zombies:
        click.echo(click.style(
            f"\n[ZOMBIES] {len(zombies)} 个候选可清理进程:", fg="yellow", bold=True))
        for pid, cmd in zombies:
            short = cmd[:110] + ("…" if len(cmd) > 110 else "")
            click.echo(f"  PID {pid:>6}  {short}")
    else:
        click.echo(click.style("\n[OK] 没有发现 zombie 进程", fg="green"))

    if include_listening and alive:
        click.echo(click.style(
            f"\n[LIVE] {len(alive)} 个正在监听的 dashboard:", fg="cyan"))
        for pid, cmd in alive:
            short = cmd[:110] + ("…" if len(cmd) > 110 else "")
            click.echo(f"  PID {pid:>6}  {short}")

    if other:
        click.echo(click.style(
            f"\n[OTHER] {len(other)} 个 omnicompany 后台进程（不动）:", fg="white"))
        for pid, cmd in other:
            short = cmd[:110] + ("…" if len(cmd) > 110 else "")
            click.echo(f"  PID {pid:>6}  {short}")

    # 5. 可选 kill
    if kill and zombies:
        click.echo()
        if click.confirm(f"确认 taskkill /F {len(zombies)} 个 zombie?", default=False):
            killed = 0
            for pid, _ in zombies:
                try:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   check=True, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                    killed += 1
                except subprocess.CalledProcessError:
                    click.echo(click.style(f"  [FAIL] PID {pid}", fg="red"))
            click.echo(click.style(f"\n[KILLED] {killed}/{len(zombies)}", fg="green"))


@cmd_guardian.command("resources")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Emit the complete machine-readable snapshot.",
)
@click.option(
    "--no-persist",
    is_flag=True,
    default=False,
    help="Sample without appending monitor history or alert state.",
)
def cmd_guardian_resources(json_output: bool, no_persist: bool):
    """只读采样进程树资源与异常；不会终止、挂起或修改任何进程。"""
    from omnicompany.dashboard.ccdaemon.resource_monitor import ResourceMonitor

    monitor = ResourceMonitor()
    try:
        snapshot = monitor.sample(persist=not no_persist)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    if json_output:
        click.echo(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return

    system = snapshot["system"]
    counts = snapshot["alert_counts"]
    click.echo(click.style("Omnicompany Resource Monitor (read-only)", fg="cyan", bold=True))
    click.echo(
        "  processes={process_count}  handles={handles}  memory={memory_percent}%  "
        "cpu={cpu_percent}%  sample={duration_ms}ms".format(
            **system,
            duration_ms=snapshot["duration_ms"],
        )
    )
    click.echo(
        f"  alerts: confirmed={counts['confirmed']} "
        f"(critical={counts['critical']}, warning={counts['warning']}) "
        f"observed={counts['observed']}"
    )
    click.echo("  policy: cleanup requires owner inspection and explicit human confirmation")

    for group in snapshot["groups"]:
        click.echo(
            f"  [{group['role']}] {group['id']} "
            f"procs={group['process_count']} cpu={group['cpu_percent']}% "
            f"handles={group['handles']} private={group['private_bytes'] // (1024 * 1024)}MB"
        )

    for alert in snapshot["alerts"]:
        color = "red" if alert["severity"] == "critical" else "yellow"
        click.echo(
            click.style(
                f"  {alert['status'].upper()} {alert['severity']} "
                f"{alert['fingerprint']}: {alert['message']}",
                fg=color,
            )
        )

    if not no_persist:
        click.echo(f"  latest: {monitor.latest_path}")


# ─── guardian archmap（结构化架构地图）──────────────────────

@cmd_guardian.group("archmap")
def cmd_guardian_archmap():
    """架构地图（docs/archmap.yaml）查看和校验工具。"""


@cmd_guardian_archmap.command("show")
def cmd_archmap_show():
    """渲染 archmap.yaml 为可读树形。"""
    from omnicompany.core.archmap import load_archmap, ArchMapError
    try:
        m = load_archmap()
    except ArchMapError as e:
        click.echo(click.style(f"[ERR] {e}", fg="red"))
        sys.exit(1)
    click.echo(m.render_tree())


@cmd_guardian_archmap.command("validate")
def cmd_archmap_validate():
    """校验 archmap.yaml 结构完整性。"""
    from omnicompany.core.archmap import load_archmap, ArchMapError
    try:
        m = load_archmap(force_reload=True)
    except ArchMapError as e:
        click.echo(click.style(f"[FAIL] {e}", fg="red"))
        sys.exit(1)
    errs = m.validate()
    if errs:
        click.echo(click.style("[FAIL] archmap 校验失败:", fg="red"))
        for e in errs:
            click.echo(f"  - {e}")
        sys.exit(1)
    click.echo(click.style(
        f"[OK] archmap v{m.version}  {len(m.repo_root)} repo_root drawers + "
        f"{len(m.src_omnicompany)} src/omnicompany drawers + "
        f"{len(m.forbidden_globs)} forbidden patterns",
        fg="green",
    ))


@cmd_guardian_archmap.command("check")
@click.argument("path", type=str)
@click.option("--writer", type=str, default="claude-code",
              help="写入身份（human / claude-code / internal-pipeline / ...）")
def cmd_archmap_check(path: str, writer: str):
    """检查一个路径 + writer 身份是否允许写入。"""
    from omnicompany.core.archmap import load_archmap, ArchMapError
    try:
        m = load_archmap()
    except ArchMapError as e:
        click.echo(click.style(f"[ERR] {e}", fg="red"))
        sys.exit(1)

    r = m.is_writable(path, writer)
    if r.allowed:
        click.echo(click.style(f"[ALLOW] {path}", fg="green"))
    else:
        click.echo(click.style(f"[DENY]  {path}", fg="red"))
    click.echo(f"  writer:       {writer}")
    click.echo(f"  drawer:       {r.drawer_layer}.{r.drawer}")
    if r.always_green:
        click.echo(click.style("  always_green: yes (核心 drawer 无条件放行)", fg="cyan"))
    if r.agent_free_fire:
        click.echo(click.style("  agent_free_fire: yes (agent 在此自由写)", fg="cyan"))
    click.echo(f"  reason:       {r.reason}")


@cmd_guardian_archmap.command("diff")
@click.option("--root", type=str, default=_DEFAULT_ROOT)
def cmd_archmap_diff(root: str):
    """展示当前 archmap.yaml 和上一次 git 版本的差异。"""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "diff", "HEAD", "--", "docs/archmap.yaml"],
            capture_output=True, text=True, cwd=root,
            encoding="utf-8", errors="replace", timeout=10,
        )
        if not out.stdout.strip():
            click.echo(click.style("[OK] archmap.yaml 无未提交改动", fg="green"))
            return
        click.echo(out.stdout)
    except Exception as e:
        click.echo(click.style(f"[ERR] git diff 失败: {e}", fg="red"))
        sys.exit(1)


# ─── guardian metadata-report（Format/Router 描述质量）──────

@cmd_guardian.command("metadata-report")
@click.option("--root", type=str, default=_DEFAULT_ROOT,
              help="项目根目录")
@click.option("--by-package", is_flag=True, default=False,
              help="按 package 汇总(默认按文件)")
def cmd_guardian_metadata_report(root: str, by_package: bool):
    """扫描所有 Format / Router 给一份 metadata 质量报告。

    分三档:
      满分 — Format 有 description ≥ 100 字符 + tags + (json_schema 或 parent)
              Router 有 DESCRIPTION ≥ 50 字符 + FORMAT_IN + FORMAT_OUT
      中档 — 必填项齐但缺细节
      差档 — 缺必填项

    汇总按 package。这是 OMNI-019/020 的人类可读版,补 patrol 输出。
    """
    import re
    root_path = Path(root)
    pkg_root = root_path / "src" / "omnicompany" / "packages"
    if not pkg_root.is_dir():
        click.echo(click.style(f"[ERR] {pkg_root} 不存在", fg="red"))
        sys.exit(1)

    DESC_RE = re.compile(r'description\s*=\s*["\'](.*?)["\']', re.DOTALL)
    CLASS_RE = re.compile(r"^class\s+(\w+)\s*\(\s*Router\b", re.MULTILINE)
    ROUTER_DESC_RE = re.compile(r'DESCRIPTION\s*=\s*["\'](.*?)["\']', re.DOTALL)
    FMT_IN_RE = re.compile(r"FORMAT_IN\s*=")
    FMT_OUT_RE = re.compile(r"FORMAT_OUT\s*=")

    class Stat:
        def __init__(self):
            self.full = 0
            self.medium = 0
            self.poor = 0
            self.total = 0

    fmt_stats: dict[str, "Stat"] = {}
    rtr_stats: dict[str, "Stat"] = {}
    fmt_files = 0
    rtr_files = 0

    def _pkg_key(p: Path) -> str:
        # packages/<layer>/<name>/.../formats.py
        rel = p.relative_to(pkg_root).as_posix()
        parts = rel.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return rel

    # 扫所有 formats.py
    for fpath in pkg_root.rglob("formats.py"):
        if any("__pycache__" in pt for pt in fpath.parts):
            continue
        if "/vendors/" in fpath.as_posix() or "vendors\\" in str(fpath):
            continue
        fmt_files += 1
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        key = _pkg_key(fpath)
        st = fmt_stats.setdefault(key, Stat())
        descs = DESC_RE.findall(content)
        for d in descs:
            st.total += 1
            has_tags = "tags=" in content
            has_schema_or_parent = ("json_schema" in content) or ("parent=" in content)
            if len(d) >= 100 and has_tags and has_schema_or_parent:
                st.full += 1
            elif len(d) >= 40:
                st.medium += 1
            else:
                st.poor += 1

    # 扫所有 routers.py
    for rpath in pkg_root.rglob("routers.py"):
        if any("__pycache__" in pt for pt in rpath.parts):
            continue
        if "/vendors/" in rpath.as_posix() or "vendors\\" in str(rpath):
            continue
        rtr_files += 1
        try:
            content = rpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        key = _pkg_key(rpath)
        st = rtr_stats.setdefault(key, Stat())
        n_classes = len(CLASS_RE.findall(content))
        n_descs = len(ROUTER_DESC_RE.findall(content))
        n_in = len(FMT_IN_RE.findall(content))
        n_out = len(FMT_OUT_RE.findall(content))

        for m in ROUTER_DESC_RE.finditer(content):
            d = m.group(1)
            st.total += 1
            if len(d) >= 50 and n_in >= n_classes and n_out >= n_classes:
                st.full += 1
            elif len(d) >= 20:
                st.medium += 1
            else:
                st.poor += 1

    # 渲染
    click.echo(click.style("═" * 70, fg="cyan"))
    click.echo(click.style("OmniCompany Metadata Quality Report", fg="cyan", bold=True))
    click.echo(click.style("═" * 70, fg="cyan"))
    click.echo(f"  扫描 formats.py: {fmt_files}  |  routers.py: {rtr_files}")
    click.echo()

    def _print_table(title: str, stats: dict):
        click.echo(click.style(title, fg="yellow", bold=True))
        click.echo(f"  {'package':45} {'full':>6} {'medium':>6} {'poor':>6} {'total':>6}")
        click.echo(f"  {'-'*45} {'-'*6:>6} {'-'*6:>6} {'-'*6:>6} {'-'*6:>6}")
        total_full = total_medium = total_poor = total_total = 0
        for key in sorted(stats.keys()):
            st = stats[key]
            total_full += st.full
            total_medium += st.medium
            total_poor += st.poor
            total_total += st.total
            color = None
            if st.poor > 0:
                color = "red"
            elif st.medium > st.full:
                color = "yellow"
            else:
                color = "green"
            line = f"  {key:45} {st.full:>6} {st.medium:>6} {st.poor:>6} {st.total:>6}"
            click.echo(click.style(line, fg=color))
        click.echo(f"  {'-'*45} {'-'*6:>6} {'-'*6:>6} {'-'*6:>6} {'-'*6:>6}")
        click.echo(f"  {'TOTAL':45} {total_full:>6} {total_medium:>6} {total_poor:>6} {total_total:>6}")
        if total_total > 0:
            pct = total_full * 100 // total_total
            click.echo(f"  full %: {pct}%")
        click.echo()

    _print_table("Format 描述质量", fmt_stats)
    _print_table("Router 描述质量", rtr_stats)

    click.echo(click.style("═" * 70, fg="cyan"))
    click.echo("评分标准:")
    click.echo("  Format 满分:  description ≥ 100 字符 + tags + (json_schema 或 parent)")
    click.echo("  Format 中档:  description 40-99 字符")
    click.echo("  Format 差档:  description < 40 字符")
    click.echo("  Router 满分:  DESCRIPTION ≥ 50 字符 + FORMAT_IN + FORMAT_OUT")
    click.echo("  Router 中档:  DESCRIPTION 20-49 字符")
    click.echo("  Router 差档:  DESCRIPTION < 20 字符")


# ─── guardian trace-violation（违规溯源）─────────────────────

@cmd_guardian.command("trace-violation")
@click.argument("path", type=str)
@click.option("--root", type=str, default=_DEFAULT_ROOT,
              help="项目根目录")
@click.option("--audit-tail", type=int, default=10,
              help="最近 N 条 shield audit 记录")
def cmd_guardian_trace_violation(path: str, root: str, audit_tail: int):
    """从一个违规文件路径反查完整溯源链。

    数据源:
      1. 文件 OmniMark 头 (origin / trace / node / agent / ts)
      2. .omni/shield_audit.jsonl 该路径的所有写入历史
      3. .omni/evolution/nodes/<node>.history.json 该节点的违规历史
      4. .omni/quarantine/index.json 是否有罚单
      5. data/events.db 按 trace_id 拉事件链(可选)
    """
    from omnicompany.core.omnimark import parse_omnimark
    from omnicompany.core.archmap import load_archmap

    root_path = Path(root)
    target = root_path / path if not Path(path).is_absolute() else Path(path)
    rel = path.replace("\\", "/")

    click.echo(click.style("═" * 70, fg="cyan"))
    click.echo(click.style(f"违规溯源: {rel}", fg="cyan", bold=True))
    click.echo(click.style("═" * 70, fg="cyan"))

    # ── 1. OmniMark 身份头 ──
    click.echo(click.style("\n[1] OmniMark 身份头", fg="yellow", bold=True))
    if not target.exists():
        click.echo(click.style(f"    文件不存在: {target}", fg="red"))
        omni_origin = None
        omni_trace = None
        omni_node = None
    else:
        try:
            fields = parse_omnimark(target)
            if fields is None:
                click.echo(click.style("    (无 OmniMark 头)", fg="red"))
                omni_origin = omni_trace = omni_node = None
            else:
                click.echo(f"    origin:    {fields.origin}")
                click.echo(f"    domain:    {fields.domain or '-'}")
                click.echo(f"    agent:     {fields.agent or '-'}")
                click.echo(f"    trace:     {fields.trace or '-'}")
                click.echo(f"    node:      {fields.node or '-'}")
                click.echo(f"    ts:        {fields.ts or '-'}")
                click.echo(f"    status:    {fields.status or '-'}")
                omni_origin = fields.origin
                omni_trace = fields.trace
                omni_node = fields.node
        except Exception as e:
            click.echo(click.style(f"    解析失败: {e}", fg="red"))
            omni_origin = omni_trace = omni_node = None

    # ── 2. 来源分类 ──
    click.echo(click.style("\n[2] 来源分类 (auto_comment 决策)", fg="yellow", bold=True))
    try:
        from omnicompany.packages.services._core.guardian.auto_comment import determine_origin_class
        archmap = load_archmap()
        origin_class, _raw = determine_origin_class(target, list(archmap.internal_pipeline_origins))
        click.echo(f"    分类:      {origin_class}")
        action_map = {
            "internal-pipeline": "fix-queue (写 patch 等 apply-fixes --confirm)",
            "external-agent":   "inline-comment (立即原地备注化 + 备份)",
            "human":            "warn-only (尊重人类判断)",
            "unknown":          "inline-comment (按外部处理,保险)",
        }
        click.echo(f"    处置策略:  {action_map.get(origin_class, '-')}")
    except Exception as e:
        click.echo(click.style(f"    分类失败: {e}", fg="red"))

    # ── 3. shield_audit ──
    click.echo(click.style("\n[3] OmniShield 审计记录 (.omni/shield_audit.jsonl)", fg="yellow", bold=True))
    audit_log = root_path / ".omni" / "shield_audit.jsonl"
    if not audit_log.exists():
        click.echo(click.style("    (audit log 不存在)", fg="yellow"))
    else:
        records: list[dict] = []
        try:
            with audit_log.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        if r.get("path") == rel:
                            records.append(r)
                    except Exception:
                        pass
        except OSError:
            pass
        if not records:
            click.echo(click.style("    (该路径无 audit 记录)", fg="yellow"))
        else:
            recent = records[-audit_tail:]
            click.echo(f"    共 {len(records)} 条记录,显示最近 {len(recent)} 条:")
            for r in recent:
                v = r.get("verdict", "?")
                color = {
                    "allowed": "green",
                    "audit_only_warn": "yellow",
                    "denied": "red",
                    "bypassed": "magenta",
                }.get(v, "white")
                click.echo(click.style(
                    f"      [{v:18}] {r.get('audit_id','?'):28} writer={r.get('writer','?')}",
                    fg=color,
                ))

    # ── 4. OmniEvolve 节点历史 ──
    click.echo(click.style("\n[4] OmniEvolve 节点历史", fg="yellow", bold=True))
    if not omni_node:
        click.echo(click.style("    (无 node_id,跳过)", fg="yellow"))
    else:
        history_file = root_path / ".omni" / "evolution" / "nodes" / f"{omni_node}.history.json"
        if not history_file.exists():
            click.echo(click.style(f"    (无该节点历史: {history_file.name})", fg="yellow"))
        else:
            try:
                hist = json.loads(history_file.read_text(encoding="utf-8"))
                click.echo(f"    node_id:               {hist.get('node_id', omni_node)}")
                click.echo(f"    pipeline:              {hist.get('pipeline', '-')}")
                click.echo(f"    total_violations:      {hist.get('total_violations', 0)}")
                click.echo(f"    consecutive_clean:     {hist.get('consecutive_clean_runs', 0)}")
                viols = hist.get("violations", [])
                if viols:
                    click.echo(f"    最近 {min(3, len(viols))} 条违规:")
                    for v in viols[-3:]:
                        click.echo(f"      {v.get('detected_at', '-')[:19]}  "
                                   f"{v.get('rule', '-'):15} {v.get('path','-')}")
            except Exception as e:
                click.echo(click.style(f"    读取失败: {e}", fg="red"))

    # ── 5. quarantine 罚单 ──
    click.echo(click.style("\n[5] Quarantine 罚单 (.omni/quarantine/index.json)", fg="yellow", bold=True))
    index_file = root_path / ".omni" / "quarantine" / "index.json"
    if not index_file.exists():
        click.echo(click.style("    (quarantine index 不存在)", fg="yellow"))
    else:
        try:
            index = json.loads(index_file.read_text(encoding="utf-8"))
            matches = [e for e in index if e.get("path") == rel]
            if not matches:
                click.echo(click.style("    (该路径无罚单)", fg="yellow"))
            else:
                click.echo(f"    {len(matches)} 张相关罚单:")
                for m in matches[-5:]:
                    click.echo(f"      {m.get('ticket_id'):28}  {m.get('rule','-'):15}  status={m.get('status','-')}")
        except Exception as e:
            click.echo(click.style(f"    读取失败: {e}", fg="red"))

    # ── 6. EventBus trace ──
    click.echo(click.style("\n[6] EventBus trace (data/events.db)", fg="yellow", bold=True))
    if not omni_trace:
        click.echo(click.style("    (无 trace_id,跳过)", fg="yellow"))
    else:
        events_db = root_path / "data" / "events.db"
        if not events_db.exists():
            click.echo(click.style("    (events.db 不存在)", fg="yellow"))
        else:
            try:
                import sqlite3
                conn = sqlite3.connect(str(events_db))
                rows = conn.execute(
                    "SELECT event_type, ts FROM events WHERE trace_id=? ORDER BY ts LIMIT 100",
                    (omni_trace,),
                ).fetchall()
                conn.close()
                if not rows:
                    click.echo(click.style(f"    (trace {omni_trace} 无事件)", fg="yellow"))
                else:
                    click.echo(f"    {len(rows)} events captured")
                    click.echo(f"    第一个: {rows[0][1]}  {rows[0][0]}")
                    click.echo(f"    最后一个: {rows[-1][1]}  {rows[-1][0]}")
                    click.echo(f"    完整链: omni traces --trace-id {omni_trace}")
            except Exception as e:
                click.echo(click.style(f"    查询失败: {e}", fg="red"))

    click.echo()
    click.echo(click.style("═" * 70, fg="cyan"))


# ─── guardian apply-fixes（修补队列）─────────────────────────

@cmd_guardian.command("apply-fixes")
@click.option("--root", type=str, default=_DEFAULT_ROOT,
              help="项目根目录")
@click.option("--ticket", type=str, default=None,
              help="只处理指定 ticket_id")
@click.option("--list", "list_only", is_flag=True, default=False,
              help="只列出 pending,不应用")
@click.option("--all-pending", is_flag=True, default=False,
              help="应用所有 pending 条目(配合 --confirm)")
@click.option("--confirm", is_flag=True, default=False,
              help="真改文件(默认 dry-run)")
@click.option("--restore", is_flag=True, default=False,
              help="反向: 从 quarantine 恢复 ticket 的备份到原位")
def cmd_guardian_apply_fixes(root: str, ticket: str | None, list_only: bool,
                              all_pending: bool, confirm: bool, restore: bool):
    """修补队列管理: 处理 .omni/fix-queue/ 里 internal-pipeline 的待修 patch。

    工作流:
      1. patrol 发现 internal-pipeline 来源的违规
      2. auto_comment 写一份 patch 草稿到 .omni/fix-queue/<date>/<ticket>.json
      3. 你跑 'omni guardian apply-fixes --list' 查看 pending
      4. 'omni guardian apply-fixes --ticket T1' 应用单条 (默认 dry-run)
      5. '... --confirm' 真改
      6. 出错时 'omni guardian apply-fixes --restore --ticket T1' 恢复
    """
    from omnicompany.packages.services._core.guardian.auto_comment import (
        AutoCommentPlan, apply_comment_out_inline, restore_from_quarantine,
    )

    root_path = Path(root)

    # restore 模式
    if restore:
        if not ticket:
            click.echo(click.style("[ERR] --restore 需要 --ticket", fg="red"))
            sys.exit(1)
        result = restore_from_quarantine(ticket, root_path)
        if result is None:
            click.echo(click.style(f"[FAIL] 无法恢复 {ticket}", fg="red"))
            sys.exit(1)
        click.echo(click.style(f"[RESTORED] {result}", fg="green"))
        return

    # 扫 fix-queue
    fix_queue_root = root_path / ".omni" / "fix-queue"
    if not fix_queue_root.is_dir():
        click.echo(click.style("[OK] fix-queue 为空", fg="green"))
        return

    pending: list[tuple[Path, dict]] = []
    for date_dir in sorted(fix_queue_root.iterdir()):
        if not date_dir.is_dir() or date_dir.name.startswith("_"):
            continue
        for f in sorted(date_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("status") == "pending":
                    if ticket and data.get("ticket_id") != ticket:
                        continue
                    pending.append((f, data))
            except Exception:
                pass

    if not pending:
        msg = "[OK] 无 pending fix"
        if ticket:
            msg += f" (ticket={ticket})"
        click.echo(click.style(msg, fg="green"))
        return

    click.echo(click.style(f"待修补条目 ({len(pending)} 条):", fg="cyan", bold=True))
    for f, data in pending:
        click.echo(f"  {data.get('ticket_id'):30}  {data.get('rule_id'):12}  "
                   f"{data.get('violation_path')}  origin={data.get('origin_raw')}")

    if list_only:
        return

    if not (all_pending or ticket):
        click.echo(click.style("\n--list 列出的条目还没动。加 --ticket T 单条或 --all-pending 全跑,"
                                "再加 --confirm 真改。", fg="yellow"))
        return

    if not confirm:
        click.echo(click.style("\n[DRY RUN] 上面的条目会被备注化。加 --confirm 真改。", fg="yellow"))
        return

    # 真应用
    success = 0
    failed = 0
    for f, data in pending:
        plan = AutoCommentPlan(
            ticket_id=data["ticket_id"],
            rule_id=data["rule_id"],
            rule_name=data.get("rule_name", data["rule_id"]),
            rule_message=data.get("rule_message", ""),
            violation_path=data["violation_path"],
            origin_class=data.get("origin_class", "internal-pipeline"),
            origin_raw=data.get("origin_raw", ""),
            detected_at=data["detected_at"],
            action="inline-comment",
        )
        ok = apply_comment_out_inline(plan, root_path)
        if ok:
            success += 1
            from datetime import datetime as _dt, timezone as _tz
            from omnicompany.core.guarded_write import write_file as _gw
            data["status"] = "applied"
            data["applied_at"] = _dt.now(_tz.utc).isoformat()
            try:
                # 走 guarded_write 写 fix-queue 状态更新 (writer=internal-guardian)
                _gw(
                    f, json.dumps(data, ensure_ascii=False, indent=2),
                    origin="omnicompany",
                    writer="internal-guardian",
                    purpose="apply-fixes status update",
                )
            except Exception:
                pass
            click.echo(click.style(f"  [APPLIED] {plan.ticket_id}", fg="green"))
        else:
            failed += 1
            click.echo(click.style(f"  [FAIL]    {plan.ticket_id}", fg="red"))
    click.echo(click.style(f"\n总计: applied={success} failed={failed}", fg="cyan"))


# ─── guardian stamp-sweep（批量补 OmniMark 头）───────────────

@cmd_guardian.command("stamp-sweep")
@click.option("--root", type=str, default=_DEFAULT_ROOT,
              help="项目根目录")
@click.option("--target", type=str, default="src/omnicompany",
              help="扫描起点目录（相对项目根）")
@click.option("--dry-run", is_flag=True, default=False,
              help="只列出缺头文件 + 推断的 origin，不写入")
@click.option("--limit", type=int, default=0,
              help="最多处理多少个文件（0=不限制）")
def cmd_guardian_stamp_sweep(root: str, target: str, dry_run: bool, limit: int):
    """批量补全历史文件的 OmniMark 身份头。

    相比 `stamp-dir`，本命令会用 git blame 推断每个文件的 origin：
      - 最近修改者匹配 claude-code / workflow-factory / sw-implement 之一 → 用该值
      - 否则标为 human

    设计目标（用户明确要求）：把几百个同质化的补头任务交给此批处理，
    而不是让 agent 在会话里一个个手打。

    推荐流程：
      1. omni guardian stamp-sweep --dry-run      查看报告
      2. omni guardian stamp-sweep                实际补头
      3. omni guardian patrol --full              确认 OMNI-001 数量下降
    """
    import subprocess
    from omnicompany.core.omnimark import stamp_file, parse_omnimark

    root_path = Path(root)
    target_path = root_path / target
    if not target_path.is_dir():
        click.echo(click.style(f"[ERR] 目标目录不存在: {target_path}", fg="red"))
        sys.exit(1)

    # 2026-04-21 B3 后: data/_archive_* 4 前缀已统一为 data/_archive/{misc,agent_loop,event_split,pre_move8}/
    # `"_archive" in p.parts` 已能匹配子目录 (event_split 是 _archive 的子), 无需再列全名
    skip_parts = {"_graveyard", "_archive", "__pycache__", "node_modules", ".git"}

    # 收集缺头候选
    candidates: list[Path] = []
    for p in target_path.rglob("*.py"):
        if not p.is_file():
            continue
        if any(d in p.parts for d in skip_parts):
            continue
        if parse_omnimark(p) is not None:
            continue
        candidates.append(p)
        if limit and len(candidates) >= limit:
            break

    if not candidates:
        click.echo(click.style("[OK] 没有缺头文件", fg="green"))
        return

    click.echo(f"扫描 {target_path}")
    click.echo(f"发现 {len(candidates)} 个缺 OmniMark 头的 .py 文件\n")

    # 推断 origin（用 git blame + 路径启发）
    def infer_origin(abs_path: Path) -> str:
        try:
            rel = abs_path.relative_to(root_path).as_posix()
            out = subprocess.run(
                ["git", "log", "-n", "1", "--format=%an|%s", "--", rel],
                capture_output=True, text=True, cwd=str(root_path),
                timeout=10, encoding="utf-8", errors="replace",
            )
            line = out.stdout.strip()
            if line:
                author, subject = (line.split("|", 1) + [""])[:2]
                subj_lo = subject.lower()
                if any(k in subj_lo for k in ("[arch-tidy", "[omniguard", "[move-8", "claude")):
                    return "claude-code"
                if "workflow-factory" in subj_lo or "workflow_factory" in subj_lo:
                    return "workflow-factory"
                if "sw-implement" in subj_lo or "sw-tdd" in subj_lo:
                    return "sw-implement"
                return "human"
        except Exception:
            pass
        return "human"

    stamped = failed = 0
    origin_hist: dict[str, int] = {}
    for p in candidates:
        origin = infer_origin(p)
        origin_hist[origin] = origin_hist.get(origin, 0) + 1

        rel = p.relative_to(root_path).as_posix()
        if dry_run:
            click.echo(f"  [{origin:20}] {rel}")
            continue

        ok = stamp_file(
            p,
            origin=origin,
            status="pending-review" if origin == "unknown" else "active",
        )
        if ok:
            stamped += 1
            if stamped <= 30 or stamped % 50 == 0:
                click.echo(click.style(f"  [STAMP {stamped}] ", fg="green") +
                           f"[{origin}] {rel}")
        else:
            failed += 1
            click.echo(click.style(f"  [FAIL] {rel}", fg="red"))

    click.echo("\n" + click.style("按 origin 汇总:", fg="cyan"))
    for o, n in sorted(origin_hist.items(), key=lambda kv: -kv[1]):
        click.echo(f"  {o:20}  {n}")

    if dry_run:
        click.echo(click.style(
            f"\n[DRY RUN] 发现 {len(candidates)} 个候选。去掉 --dry-run 实际写入。",
            fg="yellow"))
    else:
        click.echo(click.style(
            f"\n[DONE] 补头 {stamped}  失败 {failed}  总计 {len(candidates)}",
            fg="green"))
        click.echo("建议下一步: omni guardian patrol --full  确认 OMNI-001 下降")


# ─── guardian register（架构变更登记）────────────────────────

_ARCH_CHANGES_LOG = "docs/ARCH-CHANGES.jsonl"


@cmd_guardian.command("register")
@click.argument("change_description")
@click.option("--initiator", type=click.Choice(["human", "claude-code", "workflow-factory"]),
              default="human", help="变更发起方")
@click.option("--drawer", type=str, default="",
              help="涉及的 drawer（core/runtime/packages/...）")
@click.option("--related-pipeline", type=str, default="",
              help="相关管线名称（如果涉及）")
@click.option("--root", type=str, default=_DEFAULT_ROOT,
              help="项目根目录")
def cmd_guardian_register(
    change_description: str,
    initiator: str,
    drawer: str,
    related_pipeline: str,
    root: str,
):
    """登记一项架构变更到 docs/ARCH-CHANGES.log（JSONL）。

    架构变更集中登记后，ArchPlacementJudge（Session 3）会参考最近 30 天的
    登记记录在 LLM 判断新文件位置时放行相关路径。

    示例:
        omni guardian register "新增 packages/tavern 域，含 tavern-learn 管线" \\
            --initiator human --drawer packages --related-pipeline tavern-learn
    """
    from datetime import datetime, timezone

    root_path = Path(root)
    log_path = root_path / _ARCH_CHANGES_LOG

    # 生成 change_id: ARCH-YYYY-MM-DD-NNN
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    existing_today = 0
    if log_path.exists():
        with log_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if entry.get("change_id", "").startswith(f"ARCH-{date_str}-"):
                        existing_today += 1
                except Exception:
                    pass
    change_id = f"ARCH-{date_str}-{existing_today + 1:03d}"

    record = {
        "change_id": change_id,
        "ts": now.isoformat(),
        "initiator": initiator,
        "drawer": drawer,
        "related_pipeline": related_pipeline,
        "change": change_description,
    }

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    click.echo(click.style(f"[REGISTERED] {change_id}", fg="green"))
    click.echo(f"  initiator: {initiator}")
    if drawer:
        click.echo(f"  drawer:    {drawer}")
    if related_pipeline:
        click.echo(f"  pipeline:  {related_pipeline}")
    click.echo(f"  change:    {change_description}")
    click.echo(f"  log:       {log_path}")


# ─── guardian shield-status（OmniShield 审计状态）─────────────

@cmd_guardian.command("shield-status")
@click.option("--tail", type=int, default=10,
              help="显示最近 N 条审计记录（默认 10）")
def cmd_guardian_shield_status(tail: int):
    """查看 OmniShield 审计状态（audit_only 模式、审计次数、最近记录）。"""
    from omnicompany.core.guarded_write import shield_status

    status = shield_status()
    click.echo(click.style("OmniShield 状态", fg="cyan", bold=True))
    click.echo(f"  mode:          {status['mode']}")
    click.echo(f"  audit_only:    {status['audit_only']}")
    click.echo(f"  total_audited: {status['total_audited']}")
    click.echo(f"  project_root:  {status['project_root']}")

    audit_log = Path(status['project_root']) / ".omni" / "shield_audit.jsonl"
    click.echo(f"\n  audit log:     {audit_log}")

    if not audit_log.exists():
        click.echo(click.style("  (audit log 尚未创建，还没有写入发生)", fg="yellow"))
        return

    # 读最后 N 行
    lines = audit_log.read_text(encoding="utf-8").splitlines()
    if not lines:
        click.echo(click.style("  (audit log 为空)", fg="yellow"))
        return

    click.echo(click.style(f"\n最近 {min(tail, len(lines))} 条审计记录:", fg="cyan"))
    for line in lines[-tail:]:
        try:
            r = json.loads(line)
            outcome = r.get("outcome", "?")
            color = "green" if outcome == "permitted" else ("yellow" if outcome == "audit_only_warn" else "red")
            click.echo(click.style(
                f"  [{outcome:18}] {r.get('path','?')[:60]:60}  origin={r.get('origin','?')}",
                fg=color,
            ))
        except Exception:
            pass


# ─── guardian hook-install / hook-check (I-19 2026-04-23) ───────────

@cmd_guardian.command("hook-install")
@click.option("--root", type=str, default=_DEFAULT_ROOT,
              help="项目根目录 (含 .git)")
@click.option("--force", is_flag=True, default=False,
              help="覆盖用户自定义 (非 Guardian 管理) 的 hook, 先备份")
@click.option("--dry-run", is_flag=True, default=False,
              help="只汇报不写盘")
def cmd_guardian_hook_install(root: str, force: bool, dry_run: bool):
    """幂等安装 Guardian git hook (pre-commit + post-commit).

    \b
    行为:
      absent          → 装模板
      managed-current → skip
      managed-stale   → 刷新到最新模板
      foreign         → 默认 skip, --force 则备份后覆盖

    \b
    示例:
      omni guardian hook-install              # 幂等安装
      omni guardian hook-install --dry-run    # 只看不写
      omni guardian hook-install --force      # 覆盖用户自定义版本 (先备份)
    """
    from omnicompany.packages.services._core.guardian.hook_installer import (
        HookInstallError,
        install_hooks,
    )
    try:
        result = install_hooks(Path(root), force=force, dry_run=dry_run)
    except HookInstallError as e:
        click.echo(click.style(f"[FAIL] {e}", fg="red"))
        sys.exit(1)

    click.echo(click.style("> guardian hook-install", fg="cyan", bold=True))
    click.echo(f"  root: {root}  dry_run={dry_run}  force={force}")
    click.echo()
    for name, action in result.items():
        color = {
            "installed": "green",
            "refreshed": "yellow",
            "replaced-foreign": "yellow",
            "skipped-current": "cyan",
            "skipped-foreign": "yellow",
        }.get(action, "white")
        click.echo(click.style(f"  {name:15} {action}", fg=color))
    # 总结
    skipped_foreign = [n for n, a in result.items() if a == "skipped-foreign"]
    if skipped_foreign:
        click.echo()
        click.echo(click.style(
            f"  [注意] {', '.join(skipped_foreign)} 存在用户自定义版本, 未覆盖. "
            f"如需强制 Guardian 管理, 加 --force (会先备份原文件).",
            fg="yellow",
        ))


@cmd_guardian.command("hook-check")
@click.option("--root", type=str, default=_DEFAULT_ROOT)
def cmd_guardian_hook_check(root: str):
    """检查 git hook 安装状态 (不写盘)."""
    from omnicompany.packages.services._core.guardian.hook_installer import check_hooks
    result = check_hooks(Path(root))
    click.echo(click.style("> guardian hook-check", fg="cyan", bold=True))
    click.echo(f"  root: {root}")
    click.echo()
    for name, status in result.items():
        color = {
            "managed-current": "green",
            "managed-stale": "yellow",
            "absent": "red",
            "foreign": "yellow",
            "no-git": "red",
        }.get(status, "white")
        click.echo(click.style(f"  {name:15} {status}", fg=color))


# ─── guardian trace (I-20 data-provenance, 2026-04-23) ──────────

@cmd_guardian.command("trace")
@click.argument("target_path", type=click.Path(exists=False))
def cmd_guardian_trace(target_path: str):
    """反查 data 文件的合法写入者身份 (I-20 data-provenance sidecar).

    \b
    查 <target>.omni.json sidecar 的 written_by / ts / run_id / trace 等字段.
    无 sidecar 时提示 "身份不明", 这本身就是污染信号.

    \b
    示例:
      omni guardian trace data/services/guardian/hygiene/hygiene-2026-04-23-XXX.json
    """
    from omnicompany.core.omnimark import read_data_sidecar, sidecar_path
    target = Path(target_path)
    prov = read_data_sidecar(target)
    click.echo(click.style("> guardian trace", fg="cyan", bold=True))
    click.echo(f"  target: {target_path}")
    click.echo(f"  sidecar: {sidecar_path(target)}")
    click.echo()
    if prov is None:
        click.echo(click.style(
            "  [UNKNOWN] 无 sidecar · 身份不明. 这是污染信号 — 不经 guardian "
            "注册入口的写入都属违规.",
            fg="yellow",
        ))
        return
    click.echo(click.style(
        f"  written_by:  {prov.written_by}",
        fg="green" if prov.written_by else "red",
    ))
    click.echo(f"  kind:        {prov.kind}")
    click.echo(f"  ts:          {prov.ts}")
    click.echo(f"  origin:      {prov.origin}")
    if prov.run_id:
        click.echo(f"  run_id:      {prov.run_id}")
    if prov.job_id:
        click.echo(f"  job_id:      {prov.job_id}")
    if prov.trace:
        click.echo(f"  trace:       {prov.trace}")
    if prov.source_path:
        click.echo(f"  source_path: {prov.source_path}")
    if prov.ttl_days is not None:
        click.echo(f"  ttl_days:    {prov.ttl_days}")


# ─── guardian hygiene (第二波 §十一, 2026-04-23) ────────────────

@cmd_guardian.group("hygiene")
def cmd_guardian_hygiene():
    """hygiene scan 工具组: 列/白名单/去白名单/状态."""


@cmd_guardian_hygiene.command("list")
@click.option("--root", type=str, default=_DEFAULT_ROOT)
@click.option("--rule", "-r", type=str, default=None,
              help="只列指定 rule_id (OMNI-047/048a/048b/049/050/051a)")
def cmd_hygiene_list(root: str, rule):
    """列当前 hygiene scan 所有活跃告警 + 候选 + 白名单命中."""
    from omnicompany.packages.services._core.guardian.workers import HygieneScanWorker

    v = HygieneScanWorker().run({"project_root": root})
    out = v.output
    click.echo(click.style("> guardian hygiene list", fg="cyan", bold=True))
    click.echo(f"  root: {root}")
    click.echo(f"  汇总: {out['violation_count']} 告警 · {out['candidate_count']} 候选 · "
               f"{out['whitelisted_count']} 白名单豁免")
    click.echo()

    def _filter(items, key):
        return items if rule is None else [i for i in items if i.get(key) == rule]

    # 硬告警
    violations = _filter(out["violations"], "rule_id")
    if violations:
        click.echo(click.style(f"  [硬告警 {len(violations)}]", fg="red", bold=True))
        from collections import defaultdict
        by_rule = defaultdict(list)
        for v in violations:
            by_rule[v["rule_id"]].append(v)
        for rid in sorted(by_rule):
            click.echo(f"    {rid} × {len(by_rule[rid])}:")
            for vv in by_rule[rid][:30]:
                click.echo(f"      {vv['path']}")
            if len(by_rule[rid]) > 30:
                click.echo(f"      ... +{len(by_rule[rid])-30} more")
        click.echo()

    # 候选
    candidates = _filter(out["candidates_for_judge"], "rule_id")
    if candidates:
        click.echo(click.style(f"  [候选 {len(candidates)} 待 LLM 复核]", fg="yellow"))
        for c in candidates[:30]:
            click.echo(f"    [{c['rule_id']}] {c['path']}")
        click.echo()

    # 白名单豁免
    wl = _filter(out["whitelisted_hits"], "rule_id")
    if wl:
        click.echo(click.style(f"  [白名单豁免 {len(wl)}]", fg="cyan"))
        for w in wl[:20]:
            click.echo(f"    [{w['rule_id']}] {w['path']}  ← {w['matched_pattern']}  ({w['reason']})")

    if not violations and not candidates and not wl:
        click.echo(click.style("  [OK] 运行空间干净", fg="green"))


@cmd_guardian_hygiene.command("whitelist")
@click.argument("rule_id")
@click.argument("path_pattern")
@click.option("--reason", "-m", type=str, default="",
              help="豁免理由 (必填推荐)")
@click.option("--root", type=str, default=_DEFAULT_ROOT)
def cmd_hygiene_whitelist(rule_id: str, path_pattern: str, reason: str, root: str):
    """添加一条白名单 (支持 glob: * / ** / ?).

    \b
    示例:
      omni guardian hygiene whitelist OMNI-050 'data/_archive/**/*.db' --reason "事故归档, 长期保留"
      omni guardian hygiene whitelist OMNI-047 'data/services/registry/*' --reason "registry 类型槽合法占位"
    """
    from omnicompany.packages.services._core.guardian.hygiene_whitelist import add_whitelist_entry
    added, msg = add_whitelist_entry(
        Path(root), rule_id, path_pattern,
        reason=reason, added_by="claude-code",
    )
    color = "green" if added else "yellow"
    click.echo(click.style(f"  [{'ADDED' if added else 'UPDATED'}] {msg}", fg=color))
    if reason:
        click.echo(f"  reason: {reason}")


@cmd_guardian_hygiene.command("unwhitelist")
@click.argument("rule_id")
@click.argument("path_pattern")
@click.option("--root", type=str, default=_DEFAULT_ROOT)
def cmd_hygiene_unwhitelist(rule_id: str, path_pattern: str, root: str):
    """删一条白名单."""
    from omnicompany.packages.services._core.guardian.hygiene_whitelist import remove_whitelist_entry
    if remove_whitelist_entry(Path(root), rule_id, path_pattern):
        click.echo(click.style(f"  [REMOVED] {rule_id} {path_pattern}", fg="yellow"))
    else:
        click.echo(click.style(f"  [NOT FOUND] {rule_id} {path_pattern}", fg="red"))


@cmd_guardian_hygiene.command("status")
@click.option("--root", type=str, default=_DEFAULT_ROOT)
def cmd_hygiene_status(root: str):
    """白名单总览 + 最近扫描汇总."""
    from omnicompany.packages.services._core.guardian.hygiene_whitelist import load_whitelist
    from omnicompany.packages.services._core.guardian.workers import HygieneScanWorker

    wl = load_whitelist(Path(root))
    click.echo(click.style("> guardian hygiene status", fg="cyan", bold=True))
    click.echo(f"  whitelist entries: {len(wl)}")
    if wl:
        click.echo()
        from collections import Counter
        by_rule = Counter(e.rule_id for e in wl)
        for r, c in sorted(by_rule.items()):
            click.echo(f"    {r}: {c}")
    click.echo()

    v = HygieneScanWorker().run({"project_root": root})
    out = v.output
    click.echo(f"  last scan: violations={out['violation_count']}  "
               f"candidates={out['candidate_count']}  whitelisted={out['whitelisted_count']}")


# ─── guardian plan-bindings (OMNI-099 巡检接线, 2026-07-03 打回硬化㈠) ────────

@cmd_guardian.group("plan-bindings")
def cmd_guardian_plan_bindings():
    """绑定注册表(计划-进度-测试-评审四件登记)巡检工具组."""


@cmd_guardian_plan_bindings.command("scan")
@click.option("--root", type=str, default=_DEFAULT_ROOT)
@click.option("--category", "-c", type=str, default=None,
              help="只列指定 category (missing_anchor/incomplete/dangling_test_file/dangling_whatnow/empty_error_samples)")
@click.option("--json-output", is_flag=True)
def cmd_plan_bindings_scan(root: str, category: str | None, json_output: bool):
    """跑一次绑定注册表巡检扫描(PlanBindingsScanWorker, 纯确定性不调 LLM)."""
    from omnicompany.packages.services._core.guardian.workers import PlanBindingsScanWorker

    v = PlanBindingsScanWorker().run({"project_root": root})
    out = v.output
    violations = out["violations"]
    if category:
        violations = [x for x in violations if f": {category} —" in x.get("message", "")]

    if json_output:
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return

    click.echo(click.style("> guardian plan-bindings scan", fg="cyan", bold=True))
    click.echo(f"  root: {root}")
    click.echo(f"  汇总: {out['violation_count']} 违规  by_category={out['by_category']}")
    click.echo()
    if violations:
        for v_ in violations[:200]:
            click.echo(f"    [{v_['rule_id']}] {v_['path']}: {v_['message'].splitlines()[0]}")
        if len(violations) > 200:
            click.echo(f"    ... +{len(violations) - 200} more")
    else:
        click.echo(click.style("  [OK] 绑定注册表巡检干净", fg="green"))
