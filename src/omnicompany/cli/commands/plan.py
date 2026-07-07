# [OMNI] origin=ai-ide domain=cli/commands ts=2026-05-03T00:00:00Z type=router status=active
# [OMNI] summary="omni plan CLI command group — list / current / use / show plan bindings"
# [OMNI] why="cc_wrapper SessionStart hook 自动绑路径之外, 还要 CLI 显式查 / 切 plan 接口, 让 agent 跟用户能在不动文件的前提下切 active_plan. 走 services/_core/identity/record_active_session 同一份持久化逻辑跟 hook / web 一致"
# [OMNI] tags=cli,plan,session-binding,context
# [OMNI] material_id="material:cli.plan.session_binding_manager.implementation.py"
"""omni plan CLI command group — list / current / use / show plan bindings.

A plan is a bounded process record under `docs/plans/[topic-tree]/[date]NAME/`.
This command group lets the user (and agents) browse plans, see what's bound to
the current cc_session, and switch the binding without touching files manually.

Goes through `services/_core/identity/record_active_session` for writes — same
function the SessionStart hook uses, so CLI / hook / web all stay in sync.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import click

from omnicompany.packages.services._core.identity import (
    current_session_meta,
    record_active_session,
)


PLAN_DIR_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\](.+)$")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _repo_root() -> Path:
    here = Path.cwd().resolve()
    for d in (here, *here.parents):
        if (d / "src" / "omnicompany").is_dir() and (d / "docs").is_dir():
            return d
    return Path(__file__).resolve().parents[4]


def _plans_root() -> Path:
    return _repo_root() / "docs" / "plans"


def _parse_frontmatter(plan_md: Path) -> dict[str, Any]:
    if not plan_md.is_file():
        return {}
    try:
        text = plan_md.read_text(encoding="utf-8")
    except OSError:
        return {}
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        import yaml
        data = yaml.safe_load(m.group(1)) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _walk_plans(root: Path) -> list[tuple[str, Path]]:
    """Find all [date]NAME plan dirs under root, skipping _archive subtrees.

    Returns list of (plan_id_relative_posix, abs_path).
    """
    out: list[tuple[str, Path]] = []
    if not root.is_dir():
        return out

    def _walk(d: Path) -> None:
        try:
            for entry in d.iterdir():
                if not entry.is_dir():
                    continue
                if entry.name == "_archive":
                    continue
                if PLAN_DIR_RE.match(entry.name):
                    rel = entry.relative_to(root).as_posix()
                    out.append((rel, entry))
                    continue
                _walk(entry)
        except OSError:
            pass

    _walk(root)
    return out


def _resolve_plan_query(query: str) -> tuple[str, Path] | None:
    """Resolve user-typed plan reference to (plan_id, abs_dir).

    Accepts:
      - full id: `_infra/dashboard/[2026-05-03]CC-PLAN-SESSION-CONTEXT`
      - dir basename: `[2026-05-03]CC-PLAN-SESSION-CONTEXT`
      - just NAME: `CC-PLAN-SESSION-CONTEXT` (must be globally unique)

    Returns None if no match. Raises ValueError on ambiguous match.
    """
    root = _plans_root()
    plans = _walk_plans(root)

    # exact full-id
    for pid, p in plans:
        if pid == query:
            return (pid, p)

    # exact basename
    basename_matches = [(pid, p) for pid, p in plans if p.name == query]
    if len(basename_matches) == 1:
        return basename_matches[0]
    if len(basename_matches) > 1:
        raise ValueError(f"ambiguous: {len(basename_matches)} plans match basename {query!r}")

    # NAME-only (strip date prefix)
    name_matches = []
    for pid, p in plans:
        m = PLAN_DIR_RE.match(p.name)
        if m and m.group(2) == query:
            name_matches.append((pid, p))
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        sample = name_matches[0][0]
        raise ValueError(
            f"ambiguous: {len(name_matches)} plans named {query!r} "
            f"(use full id like '{sample}')"
        )

    return None


class _PlanGroup(click.Group):
    """split/run/report 走 TaskStore(progress-service); 服务没起时给人话提示而不是 traceback。"""

    def invoke(self, ctx: click.Context):
        from omnicompany.packages.services._core.lifecycle.task import TaskServiceUnavailable
        try:
            return super().invoke(ctx)
        except TaskServiceUnavailable as e:
            click.echo(f"ERROR: {e}", err=True)
            sys.exit(3)


@click.group("plan", cls=_PlanGroup)
def cmd_plan() -> None:
    """plan 绑定管理 — 查 / 切 / 看当前 cc_session 绑定的 plan.

    跟 dashboard cc_wrapper SessionStart hook + 网页 SessionContextPanel 共用同一份
    cc_session_active.json + cc_sessions.json 持久化, CLI / hook / web 三方一致.

    子命令:
      list     列所有非归档 plan
      current  显当前 session 绑的 plan + frontmatter
      use      切当前 session 的 active plan
      show     看指定 plan 的 frontmatter
    """


@cmd_plan.command("list")
@click.option("--package", default=None,
              help="按 package 前缀过滤 (例 _infra, _infra/dashboard, service/guardian)")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_plan_list(package: str | None, as_json: bool) -> None:
    """列 docs/plans/ 下所有非归档 plan."""
    plans = _walk_plans(_plans_root())
    if package:
        prefix = package.rstrip("/") + "/"
        plans = [(pid, p) for pid, p in plans if pid.startswith(prefix)]

    rows: list[dict[str, Any]] = []
    for pid, p in plans:
        fm = _parse_frontmatter(p / "plan.md")
        rows.append({
            "plan_id": pid,
            "title": fm.get("title") or "-",
            "status": fm.get("status") or "-",
            "work_type": fm.get("work_type") or "-",
            "date": str(fm.get("date") or "-"),
        })
    rows.sort(key=lambda r: r["date"], reverse=True)

    if as_json:
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    if not rows:
        click.echo("(no plans found)")
        return
    click.echo(f"{'date':12s}  {'status':10s}  {'work_type':22s}  plan_id")
    click.echo("-" * 100)
    for r in rows:
        click.echo(f"{r['date']:12s}  {r['status']:10s}  {r['work_type']:22s}  {r['plan_id']}")


@cmd_plan.command("current")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_plan_current(as_json: bool) -> None:
    """显当前 session 绑的 plan + frontmatter."""
    meta = current_session_meta()
    plan_id = meta.get("active_plan")

    if as_json:
        out: dict[str, Any] = {
            "plan_id": plan_id,
            "trace_id": meta.get("trace_id"),
            "claude_session_id": meta.get("claude_session_id"),
        }
        if plan_id:
            out["frontmatter"] = _parse_frontmatter(_plans_root() / plan_id / "plan.md")
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if not plan_id:
        click.echo("(no plan bound to this session)")
        click.echo("Pick one: `omni plan list` then `omni plan use <id>`")
        return

    fm = _parse_frontmatter(_plans_root() / plan_id / "plan.md")
    click.echo(f"plan_id     : {plan_id}")
    click.echo(f"title       : {fm.get('title') or '-'}")
    click.echo(f"status      : {fm.get('status') or '-'}")
    click.echo(f"work_type   : {fm.get('work_type') or '-'}")
    click.echo(f"trace_id    : {meta.get('trace_id')}")


@cmd_plan.command("use")
@click.argument("plan_query")
def cmd_plan_use(plan_query: str) -> None:
    """切当前 session 的 active plan.

    plan_query 接受:
      - 完整 id: `_infra/dashboard/[2026-05-03]CC-PLAN-SESSION-CONTEXT`
      - 目录名 : `[2026-05-03]CC-PLAN-SESSION-CONTEXT`
      - 仅名称 : `CC-PLAN-SESSION-CONTEXT` (须全局唯一)
    """
    try:
        resolved = _resolve_plan_query(plan_query)
    except ValueError as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(2)

    if not resolved:
        click.echo(f"ERROR: no plan matched {plan_query!r}", err=True)
        click.echo("Use `omni plan list` to browse available plans.", err=True)
        sys.exit(2)

    plan_id, plan_dir = resolved
    meta = current_session_meta()
    record_active_session(
        trace_id=meta["trace_id"],
        claude_session_id=meta.get("claude_session_id"),
        pty_id=meta.get("pty_id"),
        active_plan=plan_id,
        cwd=meta.get("cwd") or os.getcwd(),
        source="cli_plan_use",
    )

    # if we have a pty_id, also push into cc_sessions.json so dashboard sees it.
    # import lazily — CLI shouldn't pull fastapi unless dashboard is actually around.
    pty_id = meta.get("pty_id")
    if pty_id:
        try:
            from omnicompany.dashboard.ccdaemon.pty import update_meta_field
            update_meta_field(pty_id, active_plan=plan_id)
        except Exception as e:
            click.echo(f"WARN: pty meta update failed: {e}", err=True)

    click.echo(f"OK active_plan = {plan_id}")
    click.echo(f"   plan_dir   = {plan_dir}")
    click.echo("")
    click.echo("Note: a claude code already running in this session will see the new plan")
    click.echo("      on its NEXT SessionStart (i.e. after /clear or restart). The current")
    click.echo("      turn's injected context is fixed.")


@cmd_plan.command("show")
@click.argument("plan_query")
@click.option("--md", "as_md", is_flag=True, help="输出 plan.md 原文 (raw)")
def cmd_plan_show(plan_query: str, as_md: bool) -> None:
    """显指定 plan 的 frontmatter 概要."""
    try:
        resolved = _resolve_plan_query(plan_query)
    except ValueError as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(2)
    if not resolved:
        click.echo(f"ERROR: no plan matched {plan_query!r}", err=True)
        sys.exit(2)

    plan_id, plan_dir = resolved
    plan_md = plan_dir / "plan.md"

    if as_md:
        if plan_md.is_file():
            click.echo(plan_md.read_text(encoding="utf-8"))
        else:
            click.echo(f"(no plan.md in {plan_dir})", err=True)
            sys.exit(2)
        return

    fm = _parse_frontmatter(plan_md)
    click.echo(f"plan_id            : {plan_id}")
    click.echo(f"path               : {plan_dir}")
    click.echo(f"title              : {fm.get('title') or '-'}")
    click.echo(f"date               : {fm.get('date') or '-'}")
    click.echo(f"project            : {fm.get('project') or '-'}")
    click.echo(f"work_type          : {fm.get('work_type') or '-'}")
    click.echo(f"status             : {fm.get('status') or '-'}")
    click.echo(f"phase              : {fm.get('phase') or '-'}")
    click.echo(f"expected_completion: {fm.get('expected_completion') or '-'}")
    click.echo(f"ttl_days           : {fm.get('ttl_days') or '-'}")
    standards = fm.get("standards") or []
    if standards:
        click.echo("standards          :")
        for s in standards:
            click.echo(f"  - {s}")
    exit_criteria = fm.get("exit_criteria") or []
    if exit_criteria:
        click.echo("exit_criteria      :")
        for ec in exit_criteria:
            click.echo(f"  - {ec}")


def _resolve_plan_id(plan_query: str) -> str:
    """把 plan_query 解析成 plan_id; 解析不出就报错退出。"""
    try:
        resolved = _resolve_plan_query(plan_query)
    except ValueError as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(2)
    if not resolved:
        click.echo(f"ERROR: no plan matched {plan_query!r}", err=True)
        sys.exit(2)
    return resolved[0]


@cmd_plan.command("gate")
@click.argument("plan_query")
@click.option("--completeness-only", is_flag=True,
              help="只查内容完成度, 不要求已拆 task")
@click.option("--json", "as_json", is_flag=True)
def cmd_plan_gate(plan_query: str, completeness_only: bool, as_json: bool) -> None:
    """plan 完成度硬门检查 (覆盖矩阵). 不通过 = 非 0 退出, plan 不可投递.

    门 = 无 NEEDS CLARIFICATION 残留 + exit_criteria 非空 + 需求有验收 + 产物有完成判定
        + (非 --completeness-only 时) 已拆 task 且每个 task 有 testStrategy.
    """
    plan_id = _resolve_plan_id(plan_query)
    from omnicompany.packages.services._core.plan_audit.gate import (
        check_plan_completeness,
        check_plan_dispatch_gate,
    )
    res = (check_plan_completeness(plan_id) if completeness_only
           else check_plan_dispatch_gate(plan_id))
    if as_json:
        click.echo(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        click.echo(res["summary"])
        for b in res["blocks"]:
            click.echo(f"  ✗ [{b['kind']}] {b['detail']}")
            click.echo(f"      → {b['remediation']}")
    sys.exit(0 if res["ok"] else 1)


@cmd_plan.command("split")
@click.argument("plan_query")
@click.option("--model", default=None, help="拆分用的模型 (默认走统一 agent 默认模型)")
@click.option("--no-replace", is_flag=True, help="不清空已存在 task, 追加")
@click.option("--json", "as_json", is_flag=True)
def cmd_plan_split(plan_query: str, model: str | None, no_replace: bool, as_json: bool) -> None:
    """把 plan 拆成 task 树 (走统一 agent, 抄 task-master 模型). 落 data/lifecycle/tasks/."""
    plan_id = _resolve_plan_id(plan_query)
    from omnicompany.packages.services._core.lifecycle.splitter import split_plan_to_tasks
    res = split_plan_to_tasks(plan_id, model=model, replace=not no_replace)
    if as_json:
        click.echo(json.dumps(res, ensure_ascii=False, indent=2))
        sys.exit(0 if res["ok"] else 1)
    if not res["ok"]:
        click.echo(f"ERROR: 拆分失败: {res.get('error')}", err=True)
        sys.exit(1)
    click.echo(f"✓ 拆出 {res['count']} 个 task (plan: {plan_id})")
    for t in res["created"]:
        deps = f" deps={t['dependencies']}" if t["dependencies"] else ""
        par = " [P]" if t.get("parallel") else ""
        click.echo(f"  [{t['id']}] {t['title']} (prio={t['priority']} cx={t.get('complexity')}){par}{deps}")
    if res.get("dependency_cycle"):
        click.echo(f"⚠ 循环依赖: {res['dependency_cycle']}", err=True)


@cmd_plan.command("dispatch")
@click.argument("plan_query")
@click.option("--override-gate", is_flag=True,
              help="开发期旁路完成度硬门 (带审计, 默认硬阻断)")
@click.option("--json", "as_json", is_flag=True)
def cmd_plan_dispatch(plan_query: str, override_gate: bool, as_json: bool) -> None:
    """投递 plan 执行 — **先过完成度硬门**, 不通过则硬拒. 通过后列出可投递的 ready task.

    实际把某个 task 送进 agent 走 `omni task dispatch <task_id> --to <agent>`.
    """
    plan_id = _resolve_plan_id(plan_query)
    from omnicompany.packages.services._core.plan_audit.gate import check_plan_dispatch_gate
    from omnicompany.packages.services._core.lifecycle.task import TaskStore

    gate = check_plan_dispatch_gate(plan_id)
    if not gate["ok"] and not override_gate:
        if as_json:
            click.echo(json.dumps({"dispatched": False, "gate": gate}, ensure_ascii=False, indent=2))
        else:
            click.echo(f"❌ 派发被拒 (plan 完成度硬门未过): {gate['summary']}", err=True)
            for b in gate["blocks"]:
                click.echo(f"  ✗ [{b['kind']}] {b['detail']} → {b['remediation']}", err=True)
            click.echo("  (确认要强行派传 --override-gate, 仅开发期)", err=True)
        sys.exit(1)

    store = TaskStore()
    nxt = store.next_task(plan_id)
    ready = [t.to_dict() for t in store.list_tasks(plan_id)
             if t.status == "pending"
             and all(d in {x.id for x in store.list_tasks(plan_id) if x.status in {"done", "cancelled"}}
                     for d in t.dependencies)]
    payload = {
        "dispatched": False,
        "plan_id": plan_id,
        "gate_passed": gate["ok"] or override_gate,
        "gate_overridden": (not gate["ok"]) and override_gate,
        "next_task": nxt.to_dict() if nxt else None,
        "ready_tasks": [{"id": t["id"], "title": t["title"]} for t in ready],
        "hint": "omni task dispatch <task_id> --to <agent>",
    }
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    flag = " (⚠ 旁路硬门)" if payload["gate_overridden"] else ""
    click.echo(f"✓ 完成度硬门通过{flag} — plan {plan_id} 可投递")
    if nxt:
        click.echo(f"  下一个可做 task: [{nxt.id}] {nxt.title}")
    click.echo(f"  ready tasks: {len(ready)} 个")
    for t in ready:
        click.echo(f"    [{t['id']}] {t['title']}")
    click.echo(f"  → 投递: omni task dispatch <task_id> --to <agent>")
    click.echo(f"  → 或一键驱动: omni plan run \"{plan_id}\"  (持续/步进)")


@cmd_plan.command("report")
@click.argument("plan_query")
@click.option("--run", "run_id", default=None, help="指定 run_id (默认取该 plan 最近一次 run)")
@click.option("--no-persist", is_flag=True, help="只打印 markdown, 不落 plan_reports/")
def cmd_plan_report(plan_query: str, run_id: str | None, no_persist: bool) -> None:
    """聚合 plan 工作报告 (五章中文 markdown) 并打印到 stdout."""
    plan_id = _resolve_plan_id(plan_query)
    from omnicompany.packages.services._core.lifecycle.work_report import build_work_report
    wr = build_work_report(plan_id, run_id=run_id, persist=not no_persist)
    click.echo(wr["markdown"])


@cmd_plan.command("timeline")
@click.argument("plan_query")
@click.option("--lookback", type=int, default=180, help="回看窗口分钟数, 默认 180")
@click.option("--json", "as_json", is_flag=True, help="JSON 整体输出 (含 plan_id/sessions/totals)")
def cmd_plan_timeline(plan_query: str, lookback: int, as_json: bool) -> None:
    """打印 plan 在最近 N 分钟内的会话时间线 (每会话: 时长/工具数/token + 末尾合计)."""
    plan_id = _resolve_plan_id(plan_query)
    from omnicompany.packages.services._core.lifecycle.plan_timeline import build_plan_timeline
    tl = build_plan_timeline(plan_id, lookback_min=lookback)
    if as_json:
        click.echo(json.dumps(tl, ensure_ascii=False, indent=2))
        return
    sessions = tl.get("sessions") or []
    click.echo(f"plan: {plan_id}  (lookback={lookback}min, sessions={len(sessions)})")
    if not sessions:
        click.echo("  (窗内无会话: 该 plan 近期未起 sdk 受控会话, 或事件未落 data/events.db)")
    for sess in sessions:
        top = sess.get("top_tools") or {}
        top_str = ", ".join(f"{k}×{v}" for k, v in list(top.items())[:3]) or "-"
        tokens = sess.get("tokens") or {}
        click.echo(
            f"  {sess.get('trace_id', '?')}  "
            f"时长{sess.get('duration_s', 0)}s  "
            f"tools={sess.get('tool_count', 0)}  "
            f"tokens={tokens.get('total', 0)} "
            f"(in={tokens.get('input', 0)} out={tokens.get('output', 0)})  "
            f"top: {top_str}"
        )
    totals = tl.get("totals") or {}
    tk = totals.get("tokens") or {}
    click.echo(
        f"totals: 时长{totals.get('duration_s', 0)}s  "
        f"tools={totals.get('tool_count', 0)}  "
        f"tokens={tk.get('total', 0)} "
        f"(in={tk.get('input', 0)} out={tk.get('output', 0)})"
    )


@cmd_plan.command("run")
@click.argument("plan_query")
@click.option("--carrier", type=click.Choice(["sdk", "vscode"]), default="sdk",
              help="执行载体: sdk headless(持续跑默认, 安全不抢焦点) / vscode 真实会话")
@click.option("--cwd", default=None, help="sdk 档工作目录(默认 omni 仓)")
@click.option("--steps", type=int, default=None, help="只完成 N 个 task 后停(默认持续跑完)")
@click.option("--until", "until_task", default=None, help="跑到指定 task 完成后停")
@click.option("--hold-at-review", is_flag=True, help="每个 task 成功后停在 review 不自动 done(留人工验收闸)")
@click.option("--keep-going", is_flag=True, help="某 task 失败也继续下一个")
@click.option("--override-gate", is_flag=True, help="旁路完成度硬门(仅开发期)")
@click.option("--cold", "cold", is_flag=True,
              help="冷档: 每个 task 一个独立会话(默认暖档=一个会话接力, 省重复探索)")
@click.option("--parallel", "parallel", type=int, default=1,
              help="并行档: 同时跑 N 个文件范围不重叠的 task(几条管线一起; >1 时生效)")
@click.option("--json", "as_json", is_flag=True)
def cmd_plan_run(plan_query: str, carrier: str, cwd: str | None, steps: int | None,
                 until_task: str | None, hold_at_review: bool, keep_going: bool,
                 override_gate: bool, cold: bool, parallel: int, as_json: bool) -> None:
    """**启动一个 plan 的执行** — 过门→逐 task 投递 agent 自主完成→自动提物料→推进。

    持续跑完: omni plan run <plan>
    步进到某步: omni plan run <plan> --steps 3   /   --until <task_id>
    每步留验收闸: omni plan run <plan> --hold-at-review
    几条管线一起: omni plan run <plan> --parallel 3 (文件范围不重叠的 task 同时跑)
    """
    plan_id = _resolve_plan_id(plan_query)
    from omnicompany.packages.services._core.lifecycle.run_plan import run_plan

    def _print_event(ev: dict) -> None:
        e = ev.get("event")
        if e == "round_start":
            click.echo(f"  ⛓ 并行一批 {ev.get('tasks')} (×{ev.get('parallel')})")
        elif e == "task_start":
            click.echo(f"  ▶ [{ev['task_id']}] {ev['title']} (step {ev['step']})")
        elif e == "task_done":
            click.echo(f"    {ev.get('summary')}")

    rep = run_plan(plan_id, carrier=carrier, cwd=cwd, max_steps=steps, until_task=until_task,
                   hold_at_review=hold_at_review, keep_going_on_fail=keep_going,
                   override_gate=override_gate, session_mode="cold" if cold else "warm",
                   parallel=parallel,
                   on_event=None if as_json else _print_event)
    if as_json:
        click.echo(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        click.echo(rep["summary"])
    sys.exit(0 if rep["ok"] else 1)
