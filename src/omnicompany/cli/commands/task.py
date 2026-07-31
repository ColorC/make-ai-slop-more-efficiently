# [OMNI] origin=claude-code domain=cli/commands ts=2026-06-25T00:00:00Z type=router status=active
# [OMNI] summary="omni task — task 一等对象 CLI: list/show/next/start/complete/status/assign/add + dispatch/watch/inject/bindings/reassign/takeover"
# [OMNI] why="WORK-LIFECYCLE-AND-DISPATCH N-task/N-dispatch: 统一待办+投递观测介入兜底入口, 复用现有 dispatch/worker/会话扫描"
# [OMNI] tags=cli,task,lifecycle,dispatch,watch,backup
# [OMNI] material_id="material:cli.commands.task.py"
"""omni task — task 一等对象的统一 CLI。

Stage 2 (对象层): list / show / next / start / complete / status / assign / add
Stage 3 (投递观测介入兜底): dispatch / watch / inject / bindings / reassign / takeover
  —— 复用现成设施(omni dispatch / omni worker / 会话扫描 / plan_worker_bindings),不另造。
"""
from __future__ import annotations

import json
import sys

import click

from .._access import any_caller


def _store():
    from omnicompany.packages.services._core.lifecycle.task import TaskStore
    return TaskStore()


def _emit(data) -> None:
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


class _TaskGroup(click.Group):
    """任务唯一真源在 progress-service; 它没起时给人话提示而不是 traceback(错误样本①)。"""

    def invoke(self, ctx: click.Context):
        from omnicompany.packages.services._core.lifecycle.task import TaskServiceUnavailable
        try:
            return super().invoke(ctx)
        except TaskServiceUnavailable as e:
            click.echo(f"ERROR: {e}", err=True)
            sys.exit(3)


@click.group("task", cls=_TaskGroup)
def cmd_task() -> None:
    """task 一等对象 — 统一待办 / 状态机 / 依赖 / 投递观测介入兜底。(存储=progress-service :8230)"""


@cmd_task.command("list")
@click.option("--plan", "plan_id", default=None, help="只看某 plan 的 task")
@click.option("--status", default=None, help="按状态过滤")
@click.option("--team", "team_id", default=None, help="按目标团队过滤")
@click.option("--position", "position_id", default=None, help="按目标岗位过滤")
@click.option("--assignee", default=None, help="按当前负责人过滤")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_task_list(
    plan_id: str | None,
    status: str | None,
    team_id: str | None,
    position_id: str | None,
    assignee: str | None,
    as_json: bool,
) -> None:
    """列 task；团队/岗位“收件箱”是 canonical Task 的只读筛选视图。"""
    tasks = _store().list_tasks(
        plan_id,
        team_id=team_id,
        position_id=position_id,
        assignee=assignee,
    )
    if status:
        tasks = [t for t in tasks if t.status == status]
    rows = [t.to_dict() for t in tasks]
    if as_json:
        _emit(rows)
        return
    if not rows:
        click.echo("(no tasks)")
        return
    for t in rows:
        deps = f" deps={t['dependencies']}" if t["dependencies"] else ""
        asg = f" @{t['assignee']}" if t.get("assignee") else ""
        pos = f" →岗位:{t['position_id']}" if t.get("position_id") else ""
        click.echo(f"  [{t['id']}] {t['status']:11s} {t['title']}{asg}{pos}{deps}")


@cmd_task.command("show")
@click.argument("task_id")
@click.option("--plan", "plan_id", default=None)
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_task_show(task_id: str, plan_id: str | None, as_json: bool) -> None:
    """看一个 task 全字段。"""
    t = _store().get(task_id, plan_id)
    if not t:
        click.echo(f"ERROR: task 不存在: {task_id}", err=True)
        sys.exit(2)
    if as_json:
        _emit(t.to_dict())
        return
    d = t.to_dict()
    for k in ("id", "plan_id", "title", "status", "priority", "complexity", "parallel",
              "dependencies", "assignee", "team_id", "position_id",
              "test_strategy", "description", "details"):
        click.echo(f"{k:14s}: {d.get(k)}")


@cmd_task.command("next")
@click.argument("plan_id")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_task_next(plan_id: str, as_json: bool) -> None:
    """挑下一个可做 task (依赖全 done + 优先级最高)。"""
    t = _store().next_task(plan_id)
    if as_json:
        _emit(t.to_dict() if t else None)
        return
    if not t:
        click.echo("(没有 ready task — 都做完了 / 被依赖阻塞)")
        return
    click.echo(f"[{t.id}] {t.title} (prio={t.priority})")


def _set_status(task_id: str, status: str, plan_id: str | None, as_json: bool = False) -> None:
    try:
        t = _store().set_status(task_id, status, plan_id)
    except (KeyError, ValueError) as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(2)
    if as_json:
        _emit(t.to_dict())
        return
    click.echo(f"✓ [{t.id}] → {t.status}")


@cmd_task.command("start")
@click.argument("task_id")
@click.option("--plan", "plan_id", default=None)
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_task_start(task_id: str, plan_id: str | None, as_json: bool) -> None:
    """标记 task 开始 (→ in_progress)。"""
    _set_status(task_id, "in_progress", plan_id, as_json=as_json)


@cmd_task.command("complete")
@click.argument("task_id")
@click.option("--plan", "plan_id", default=None)
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_task_complete(task_id: str, plan_id: str | None, as_json: bool) -> None:
    """标记 task 完成 (→ done)。"""
    _set_status(task_id, "done", plan_id, as_json=as_json)


@cmd_task.command("status")
@click.argument("task_id")
@click.argument("status")
@click.option("--plan", "plan_id", default=None)
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_task_status(task_id: str, status: str, plan_id: str | None, as_json: bool) -> None:
    """改 task 状态 (pending/in_progress/review/done/blocked/deferred/cancelled)。"""
    _set_status(task_id, status, plan_id, as_json=as_json)


@cmd_task.command("assign")
@click.argument("task_id")
@click.argument("assignee")
@click.option("--plan", "plan_id", default=None)
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_task_assign(task_id: str, assignee: str, plan_id: str | None, as_json: bool) -> None:
    """把 task 指派给某 agent / 身份。"""
    try:
        t = _store().update(task_id, plan_id=plan_id, assignee=assignee)
    except KeyError as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(2)
    if as_json:
        _emit(t.to_dict())
        return
    click.echo(f"✓ [{t.id}] 指派给 {assignee}")


@cmd_task.command("claim-route")
@click.argument("task_id")
@click.option("--plan", "plan_id", required=True, help="任务所属计划")
@click.option("--project", "project_id", required=True, help="任务所属项目")
@click.option("--team", "team_id", required=True, help="项目已引用的目标团队")
@click.option("--position", "position_id", required=True, help="团队岗位")
@click.option("--assignee", required=True, help="当前认领人或 Agent 身份")
@click.option(
    "--evidence-ref",
    "evidence_refs",
    multiple=True,
    required=True,
    help="认领与岗位判断依据；可重复传入",
)
@click.option("--trace-id", default=None, help="可选审计追踪号")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_task_claim_route(
    task_id: str,
    plan_id: str,
    project_id: str,
    team_id: str,
    position_id: str,
    assignee: str,
    evidence_refs: tuple[str, ...],
    trace_id: str | None,
    as_json: bool,
) -> None:
    """原子认领任务并放入团队岗位；只落位，不执行。"""
    import asyncio

    from omnicompany.bus.sqlite import SQLiteBus
    from omnicompany.core import registry as runtime_registry
    from omnicompany.core.projects_registry import (
        list_projects,
        plan_governance,
    )
    from omnicompany.packages.services._core.lifecycle.claim_route import (
        TaskPositionClaimRequest,
        claim_task_to_position,
    )

    project = next(
        (item for item in list_projects() if item.get("id") == project_id),
        None,
    )
    if project is None:
        message = f"项目不存在: {project_id}"
        if as_json:
            _emit({"ok": False, "status": "rejected", "reason": message})
        else:
            click.echo(f"未认领：{message}", err=True)
        raise click.exceptions.Exit(2)

    try:
        runtime_registry.discover()
        entry = runtime_registry.get_or_raise(team_id)
        team = entry.build_team()
    except Exception as exc:  # noqa: BLE001
        message = f"团队无法从现有运行目录构建: {exc}"
        if as_json:
            _emit({"ok": False, "status": "rejected", "reason": message})
        else:
            click.echo(f"未认领：{message}", err=True)
        raise click.exceptions.Exit(2) from exc

    request = TaskPositionClaimRequest(
        project_id=project_id,
        plan_id=plan_id,
        task_id=task_id,
        team_id=team_id,
        position_id=position_id,
        assignee=assignee,
        evidence_refs=tuple(evidence_refs),
    )

    async def run_claim():
        bus = SQLiteBus()
        await bus.connect()
        try:
            return await claim_task_to_position(
                request,
                project=project,
                team=team,
                store=_store(),
                governance=plan_governance(),
                bus=bus,
                trace_id=trace_id,
                source="cli.task.claim_route",
            )
        finally:
            await bus.close()

    try:
        receipt = asyncio.run(run_claim())
    except (RuntimeError, ValueError) as exc:
        if as_json:
            _emit({"ok": False, "status": "rejected", "reason": str(exc)})
        else:
            click.echo(f"未认领：{exc}", err=True)
        raise click.exceptions.Exit(2) from exc
    if as_json:
        _emit(receipt.to_dict())
    else:
        click.echo(receipt.summary_zh)
    raise click.exceptions.Exit(0 if receipt.ok else 2)


@cmd_task.command("update")
@click.argument("task_id")
@click.option("--note", required=True, help="一条进度记录(平实人话: 做了什么/改了哪些文件/跑了什么/结果)")
@click.option("--plan", "plan_id", default=None)
@any_caller
def cmd_task_update(task_id: str, note: str, plan_id: str | None) -> None:
    """给任务记一条进度(边做边记, 抄 task-master update_subtask)。工作报告会用这些记录。"""
    try:
        t = _store().add_note(task_id, note, plan_id)
    except KeyError as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(2)
    try:  # 会话侧反向挂一条引用(这个会话给这个 task 记了进度)
        from omnicompany.packages.services._core.identity import link_record_to_session
        link_record_to_session(None, kind="task_note", record_id=t.id,
                               ref_id=(f"plan:{t.plan_id}" if getattr(t, "plan_id", None) else None))
    except Exception:
        pass
    click.echo(f"✓ [{t.id}] 记下进度(共 {len(t.notes)} 条): {note[:50]}")


@cmd_task.command("add")
@click.argument("plan_id")
@click.option("--title", required=True)
@click.option("--details", default="")
@click.option("--test-strategy", default="")
@click.option("--priority", default="medium")
@click.option("--deps", default="", help="逗号分隔的前置 task id")
@any_caller
def cmd_task_add(plan_id: str, title: str, details: str, test_strategy: str,
                 priority: str, deps: str) -> None:
    """手动加一个 task 到某 plan。"""
    dep_ids = [d.strip() for d in deps.split(",") if d.strip()]
    try:
        t = _store().add(plan_id, title=title, details=details, test_strategy=test_strategy,
                         priority=priority, dependencies=dep_ids)
    except ValueError as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(2)
    click.echo(f"✓ 新增 task [{t.id}] {t.title}")


# ───────────────────── Stage 3: 投递 / 观测 / 介入 / 兜底 ─────────────────────

@cmd_task.command("bindings")
@click.option("--plan", "plan_id", default=None)
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_task_bindings(plan_id: str | None, as_json: bool) -> None:
    """看 task↔agent 绑定 (谁在做哪个 task)。"""
    from omnicompany.packages.services._core.lifecycle.dispatch_task import list_task_bindings
    rows = list_task_bindings(plan_id)
    if as_json:
        _emit(rows)
        return
    if not rows:
        click.echo("(no task bindings)")
        return
    for b in rows:
        click.echo(f"  task[{b['task_id']}] → {b.get('agent') or '?'} "
                   f"({b.get('status')}) plan={b.get('plan_id')}")


@cmd_task.command("board")
@click.argument("plan_id")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_task_board(plan_id: str, as_json: bool) -> None:
    """plan 全景钻取 (task→agent→material→审阅状态), 喂 web dashboard multiagent view。"""
    from omnicompany.packages.services._core.lifecycle.board import board_data
    data = board_data(plan_id)
    if as_json:
        _emit(data)
        return
    click.echo(f"plan: {data.get('title')} [{data.get('status')}]  gate={data['gate']['summary']}")
    click.echo(f"tasks: {data['task_count']}  状态分布: {data['status_counts']}")
    for t in data["tasks"]:
        agent = f" @{t['agent']}" if t.get("agent") else ""
        click.echo(f"  [{t['id']}] {t['status']:11s} {t['title']}{agent}")
    if data["materials"]:
        click.echo(f"materials: {len(data['materials'])}")


@cmd_task.command("dispatch")
@click.argument("task_id")
@click.option("--to", "agent", default=None, help="目标 agent (会话 key / 身份); 省略=自动路由")
@click.option("--plan", "plan_id", default=None)
@click.option("--carrier", type=click.Choice(["vscode", "sdk"]), default="vscode",
              help="执行载体: vscode 真实会话(默认) / sdk 受控会话")
@click.option("--cwd", "cwd", default=None, help="sdk 档的工作目录(默认 omni 仓; 可指沙箱/worktree)")
@click.option("--dry", is_flag=True, help="只组装投递消息不真发")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_task_dispatch(task_id: str, agent: str | None, plan_id: str | None,
                      carrier: str, cwd: str | None, dry: bool, as_json: bool) -> None:
    """把一个 task 投递给 agent 执行 (vscode 真实会话 / sdk 受控会话)。"""
    from omnicompany.packages.services._core.lifecycle.dispatch_task import dispatch_task
    res = dispatch_task(task_id, agent=agent, plan_id=plan_id, carrier=carrier, dry=dry, cwd=cwd)
    if as_json:
        _emit(res)
    else:
        click.echo(res.get("summary", json.dumps(res, ensure_ascii=False)))
    sys.exit(0 if res.get("ok") else 1)


@cmd_task.command("watch")
@click.argument("target")
@click.option("--lines", default=40, help="tail 行数")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_task_watch(target: str, lines: int, as_json: bool) -> None:
    """实时洞察某 task/plan 在哪个 agent 跑、跑到哪 (逐行 tail + 卡死判断)。"""
    from omnicompany.packages.services._core.lifecycle.dispatch_task import watch_target
    res = watch_target(target, lines=lines)
    if as_json:
        _emit(res)
        return
    if not res.get("ok"):
        click.echo(res.get("error", "watch 失败"), err=True)
        sys.exit(1)
    sessions = res.get("sessions", [])
    if not sessions:
        note = res.get("note")
        if note:
            click.echo(note)
        return
    for s in sessions:
        flag = " ⚠卡死" if s.get("stalled") else ""
        click.echo(f"── agent={s.get('agent')} session={s.get('session_id')} "
                   f"status={s.get('run_status')}{flag} (idle {s.get('idle_sec')}s)")
        for ln in s.get("tail", []):
            click.echo(f"   {ln}")


@cmd_task.command("inject")
@click.argument("task_id")
@click.option("-m", "--message", required=True, help="要注入到目标会话的消息")
@click.option("--plan", "plan_id", default=None)
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_task_inject(task_id: str, message: str, plan_id: str | None, as_json: bool) -> None:
    """向 task 所在会话注入一条消息 (设约束 / 追加指令 / 反馈)。"""
    from omnicompany.packages.services._core.lifecycle.dispatch_task import inject_to_task
    res = inject_to_task(task_id, message, plan_id=plan_id)
    if as_json:
        _emit(res)
    else:
        click.echo(res.get("summary", json.dumps(res, ensure_ascii=False)))
    sys.exit(0 if res.get("ok") else 1)


@cmd_task.command("reassign")
@click.argument("task_id")
@click.option("--to", "agent", required=True, help="新 agent")
@click.option("--plan", "plan_id", default=None)
@any_caller
def cmd_task_reassign(task_id: str, agent: str, plan_id: str | None) -> None:
    """卡死/质量差时把 task 重派给另一个 agent (双-agent 兜底)。"""
    from omnicompany.packages.services._core.lifecycle.dispatch_task import reassign_task
    res = reassign_task(task_id, agent, plan_id=plan_id)
    click.echo(res.get("summary", json.dumps(res, ensure_ascii=False)))
    sys.exit(0 if res.get("ok") else 1)


@cmd_task.command("takeover")
@click.argument("task_id")
@click.option("--plan", "plan_id", default=None)
@any_caller
def cmd_task_takeover(task_id: str, plan_id: str | None) -> None:
    """外部对话本地兜底接管这个 task (标记 takeover + 给出接管上下文)。"""
    from omnicompany.packages.services._core.lifecycle.dispatch_task import takeover_task
    res = takeover_task(task_id, plan_id=plan_id)
    click.echo(res.get("summary", json.dumps(res, ensure_ascii=False)))
    if res.get("context"):
        click.echo(res["context"])
    sys.exit(0 if res.get("ok") else 1)


__all__ = ["cmd_task"]
