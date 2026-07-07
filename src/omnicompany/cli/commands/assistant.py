# [OMNI] origin=claude-code ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:cli.assistant.task_dispatcher.command_suite.py"
"""omni assistant — 外部 CLI 入口，对齐 claude -p 模式。

设计原则：
- 与网页端同源：都通过 assistant_db + IDEAgentLoop，不复制业务逻辑
- 三类操作：
    1. chat   — 发任务给 assistant（一次性或持续）
    2. CRUD   — 管理 goals/plans/workspaces/rules/cron（修订上下文）
    3. watch  — 监督进度（读 history / events.db）

外部 agent 通过这个 CLI 可以：
- 发布任务    → omni assistant chat "帮我..."
- 查看状态    → omni assistant status
- 修订目标    → omni assistant goal update <id> --tick "condition"
- 看做了啥    → omni assistant history / omni assistant tail
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click


# ═══════════════════════════════════════════════════════════════════
# Windows GBK 安全输出
# ═══════════════════════════════════════════════════════════════════

def _safe_echo(text: str) -> None:
    try:
        click.echo(text)
    except UnicodeEncodeError:
        click.echo(text.encode("gbk", errors="replace").decode("gbk"))


_LEGACY_REMOVED_MSG = (
    "omni assistant 的 goal / status / plan / workspace / rule / cron / history 子命令依赖已废弃的 "
    "assistant_db SQL 设施 (2026-04-18 router 化铁律后退役, 不复活).\n"
    "替代 (进度/目标/plan 现在的真源):\n"
    "  - 目标 / 计划 / 进度: whatnow 目标系统 (:8230) —— `whatnow board` / `whatnow add/patch/progress`\n"
    "  - 新计划自动纳管 + 进度刷新: `omni governance plans-sync` (每日 cron gov-progress-sync 已挂)\n"
    "  - 定时任务: `omni cron`;chat 仍工作: `omni assistant chat` (走 native_agent, 跟 assistant_db 无关)"
)


def _require_adb():
    """已退役的 assistant_db: 不复活, 给清晰指引后退出(替代旧的裸 import 崩溃)。"""
    try:
        from omnicompany.dashboard import assistant_db as adb  # noqa: F401
        return adb
    except ImportError:
        click.echo(_LEGACY_REMOVED_MSG, err=True)
        sys.exit(2)


def _get_db_path() -> Path:
    try:
        from omnicompany.dashboard.assistant_context_builder import get_assistant_db_path
        return get_assistant_db_path()
    except ImportError:
        click.echo(_LEGACY_REMOVED_MSG, err=True)
        sys.exit(2)


def _open_db():
    try:
        from omnicompany.dashboard import assistant_db as adb
    except ImportError:
        click.echo(_LEGACY_REMOVED_MSG, err=True)
        sys.exit(2)
    p = _get_db_path()
    adb.ensure_schema(p)
    return adb.connect(p)


def _json_out(obj, pretty: bool = True) -> None:
    if pretty:
        _safe_echo(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    else:
        _safe_echo(json.dumps(obj, ensure_ascii=False, default=str))


# ═══════════════════════════════════════════════════════════════════
# omni assistant — 顶层组
# ═══════════════════════════════════════════════════════════════════

@click.group("assistant")
def cmd_assistant():
    """Assistant context management + task dispatch (claude -p style)."""


# ═══════════════════════════════════════════════════════════════════
# chat — 对齐 claude -p: 一次性任务发布
# ═══════════════════════════════════════════════════════════════════

@cmd_assistant.command("chat")
@click.argument("instruction", required=False)
@click.option("--stdin", "from_stdin", is_flag=True, help="Read instruction from stdin")
@click.option("--cwd", default=None, help="Working directory for the agent")
@click.option("--max-turns", type=int, default=None, help="Override max_turns")
@click.option("--policy", default=None, help="Model policy (production/balanced/cheap/robust_test/max_quality)")
@click.option("--json", "json_out", is_flag=True, help="Emit result as JSON")
def cmd_chat(instruction: str | None, from_stdin: bool, cwd: str | None,
             max_turns: int | None, policy: str | None, json_out: bool):
    """Send a one-shot task to IDEAgentLoop, stream events, print final result.

    Examples:
      omni assistant chat "list files in current directory"
      echo "analyze this" | omni assistant chat --stdin
      omni assistant chat "do X" --cwd /path/to/project --max-turns 50
      omni assistant chat "test" --policy robust_test
    """
    if from_stdin:
        instruction = sys.stdin.read().strip()
    if not instruction:
        raise click.UsageError("instruction required (arg or --stdin)")

    if policy:
        from omnicompany.runtime.llm.llm import ModelRegistry
        try:
            ModelRegistry.get_instance().set_active_policy(policy)
            _safe_echo(f"[policy] {policy}")
        except ValueError as e:
            raise click.UsageError(str(e))

    async def _run():
        # 2026-05-02: 切到 NativeIdeAgent (新 router 化架构, ConfigurableAgent 子类)
        # 旧 IDEAgentLoop 继承的旧 AgentNodeLoop 已 deprecate
        from omnicompany.dashboard.native_agent import NativeIdeAgent
        from omnicompany.bus.sqlite import SQLiteBus
        import uuid
        # max_turns 通过 LoopConfig dataclasses.replace 注入
        cfg = None
        if max_turns is not None:
            import dataclasses
            from omnicompany.dashboard.native_agent import _NATIVE_LOOP_CONFIG
            cfg = dataclasses.replace(_NATIVE_LOOP_CONFIG, max_turns=max_turns)

        from omnicompany.core.config import resolve_unified_db_path
        db_path = resolve_unified_db_path("ide_events.db")
        bus = SQLiteBus(basename="ide_events.db")
        await bus.connect()
        trace_id = uuid.uuid4().hex[:16]
        _safe_echo(f"[trace] {trace_id}  ({db_path})")
        try:
            from omnicompany.protocol.events import FactoryEvent
            from datetime import datetime, timezone
            await bus.publish(FactoryEvent(
                trace_id=trace_id,
                event_type="task.intent",
                source="ide.user",
                payload={"instruction": instruction},
                timestamp=datetime.now(timezone.utc),
            ))
            agent = NativeIdeAgent(cwd=cwd, bus=bus, config=cfg)
            verdict = await agent.run({"instruction": instruction, "trace_id": trace_id})
            return verdict
        finally:
            pending = [t for t in asyncio.all_tasks()
                       if t is not asyncio.current_task() and not t.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await bus.close()

    try:
        verdict = asyncio.run(_run())
    except KeyboardInterrupt:
        _safe_echo("[interrupted]")
        sys.exit(130)
    except Exception as e:
        _safe_echo(f"[error] {e}")
        sys.exit(1)

    if json_out:
        _json_out({
            "kind": str(verdict.kind),
            "output": verdict.output,
            "metadata": verdict.metadata,
        })
    else:
        _safe_echo(str(verdict.output or ""))


# ═══════════════════════════════════════════════════════════════════
# status — 对齐 get_system_prompt 的 context section
# ═══════════════════════════════════════════════════════════════════

@cmd_assistant.command("status")
@click.option("--json", "json_out", is_flag=True, help="Emit as JSON")
def cmd_status(json_out: bool):
    """Show current assistant context (same as what's injected into system prompt)."""
    try:
        from omnicompany.dashboard import assistant_db as adb
        from omnicompany.dashboard.assistant_context_builder import (
            build_context_section, get_work_until_plan, get_config_flag,
        )
    except ImportError:
        click.echo(_LEGACY_REMOVED_MSG, err=True)
        sys.exit(2)

    db_path = _get_db_path()

    if json_out:
        conn = adb.connect(db_path)
        out = {
            "workspaces": adb.list_workspaces(conn),
            "goals": {
                "active": adb.list_goals(conn, status="active"),
                "planned": adb.list_goals(conn, status="planned"),
                "done": adb.list_goals(conn, status="done"),
            },
            "plans": adb.list_plans(conn),
            "extra_items": adb.list_extra(conn),
            "cron_jobs": adb.list_cron(conn),
            "work_until_plan": get_work_until_plan(db_path),
            "plan_update_enabled": get_config_flag(db_path, "plan_update_enabled") == "1",
        }
        conn.close()
        _json_out(out)
    else:
        wu = get_work_until_plan(db_path)
        section = build_context_section(db_path, work_until_plan=wu)
        if section:
            _safe_echo(section)
        else:
            _safe_echo("(empty assistant context)")


# ═══════════════════════════════════════════════════════════════════
# goal — CRUD
# ═══════════════════════════════════════════════════════════════════

@cmd_assistant.group("goal")
def cmd_goal():
    """Manage goals (the target states we're working towards)."""


@cmd_goal.command("list")
@click.option("--status", default=None, type=click.Choice(["active", "planned", "done", "cancelled"]))
@click.option("--json", "json_out", is_flag=True)
def cmd_goal_list(status, json_out):
    adb = _require_adb()
    conn = _open_db()
    rows = adb.list_goals(conn, status=status)
    conn.close()
    if json_out:
        _json_out(rows)
        return
    if not rows:
        _safe_echo("(no goals)")
        return
    for g in rows:
        proof = g.get("implementation_proof") or ""
        checks = [ln for ln in proof.split("\n") if ln.strip().startswith("- [")]
        done = sum(1 for ln in checks if "- [x]" in ln.lower())
        total = len(checks)
        progress = f" [{done}/{total}]" if total else ""
        _safe_echo(f"{g['goal_id']}  [{g['status']}]{progress}  {g['title']}")


@cmd_goal.command("create")
@click.option("--title", required=True)
@click.option("--proof", "implementation_proof", default=None,
              help="Markdown checklist, e.g. '- [ ] cond1\\n- [ ] cond2'")
@click.option("--status", default="active",
              type=click.Choice(["active", "planned", "done", "cancelled"]))
@click.option("--plan", "related_plan", default=None)
def cmd_goal_create(title, implementation_proof, status, related_plan):
    adb = _require_adb()
    conn = _open_db()
    g = adb.create_goal(conn, {
        "title": title,
        "implementation_proof": implementation_proof,
        "status": status,
        "related_plan": related_plan,
    })
    conn.close()
    _safe_echo(f"{g['goal_id']}  {g['title']}")


@cmd_goal.command("update")
@click.argument("goal_id")
@click.option("--title", default=None)
@click.option("--status", default=None,
              type=click.Choice(["active", "planned", "done", "cancelled"]))
@click.option("--proof", "implementation_proof", default=None,
              help="Full replacement of proof text")
@click.option("--tick", default=None, help="Mark a proof line as done by substring match")
@click.option("--untick", default=None, help="Mark a proof line as not done by substring match")
def cmd_goal_update(goal_id, title, status, implementation_proof, tick, untick):
    adb = _require_adb()
    conn = _open_db()

    patch = {}
    if title:
        patch["title"] = title
    if status:
        patch["status"] = status
    if implementation_proof is not None:
        patch["implementation_proof"] = implementation_proof

    if tick or untick:
        # Load current proof, toggle matching line
        row = conn.execute("SELECT implementation_proof FROM goals WHERE goal_id = ?", (goal_id,)).fetchone()
        if not row:
            conn.close()
            raise click.ClickException(f"goal {goal_id} not found")
        proof = row.get("implementation_proof") or ""
        lines = proof.split("\n")
        needle = tick or untick
        for i, ln in enumerate(lines):
            if needle.lower() in ln.lower() and ln.strip().startswith("- ["):
                if tick:
                    lines[i] = ln.replace("- [ ]", "- [x]", 1).replace("- [X]", "- [x]", 1)
                else:
                    lines[i] = ln.replace("- [x]", "- [ ]", 1).replace("- [X]", "- [ ]", 1)
                break
        else:
            conn.close()
            raise click.ClickException(f"no proof line matching '{needle}'")
        patch["implementation_proof"] = "\n".join(lines)

    if not patch:
        conn.close()
        raise click.UsageError("nothing to update")

    g = adb.update_goal(conn, goal_id, patch)
    conn.close()
    if not g:
        raise click.ClickException(f"goal {goal_id} not found")
    _safe_echo(f"{g['goal_id']}  [{g['status']}]  {g['title']}")


@cmd_goal.command("delete")
@click.argument("goal_id")
@click.option("-y", "--yes", is_flag=True)
def cmd_goal_delete(goal_id, yes):
    if not yes:
        click.confirm(f"Delete goal {goal_id}?", abort=True)
    adb = _require_adb()
    conn = _open_db()
    ok = adb.delete_goal(conn, goal_id)
    conn.close()
    _safe_echo("deleted" if ok else "not found")


# ═══════════════════════════════════════════════════════════════════
# plan — CRUD + work-until toggle
# ═══════════════════════════════════════════════════════════════════

@cmd_assistant.group("plan")
def cmd_plan():
    """Manage plans (roadmaps towards goals)."""


@cmd_plan.command("list")
@click.option("--status", default=None, type=click.Choice(["active", "done", "paused"]))
@click.option("--json", "json_out", is_flag=True)
def cmd_plan_list(status, json_out):
    adb = _require_adb()
    conn = _open_db()
    rows = adb.list_plans(conn, status=status)
    conn.close()
    if json_out:
        _json_out(rows)
        return
    for p in rows:
        phase = f"  [{p.get('current_phase')}]" if p.get("current_phase") else ""
        _safe_echo(f"{p['plan_id']}  [{p['status']}]{phase}  {p['title']}  → {p['folder_path']}")


@cmd_plan.command("register")
@click.option("--title", required=True)
@click.option("--folder", "folder_path", required=True)
@click.option("--phase", "current_phase", default=None)
@click.option("--goal", "goal_ids", multiple=True, help="Associated goal_id (repeatable)")
def cmd_plan_register(title, folder_path, current_phase, goal_ids):
    adb = _require_adb()
    conn = _open_db()
    p = adb.create_plan(conn, {
        "title": title,
        "folder_path": folder_path,
        "current_phase": current_phase,
        "goal_ids": list(goal_ids),
    })
    conn.close()
    _safe_echo(f"{p['plan_id']}  {p['title']}")


@cmd_plan.command("phase")
@click.argument("plan_id")
@click.argument("current_phase")
def cmd_plan_phase(plan_id, current_phase):
    """Update the current phase of a plan."""
    adb = _require_adb()
    conn = _open_db()
    p = adb.update_plan(conn, plan_id, {"current_phase": current_phase})
    conn.close()
    if not p:
        raise click.ClickException(f"plan {plan_id} not found")
    _safe_echo(f"{p['plan_id']}  phase={p['current_phase']}")


@cmd_plan.command("work-until")
@click.argument("plan_title_or_off")
def cmd_plan_work_until(plan_title_or_off):
    """Enable Work-Until for a plan title, or 'off' to disable.

    Example: omni assistant plan work-until "ASSISTANT-CONTEXT-SYSTEM"
             omni assistant plan work-until off
    """
    try:
        from omnicompany.dashboard.assistant_context_builder import set_work_until_plan, set_config_flag
    except ImportError:
        click.echo(_LEGACY_REMOVED_MSG, err=True)
        sys.exit(2)
    db = _get_db_path()
    if plan_title_or_off.lower() in ("off", "none", "disable"):
        set_work_until_plan(db, None)
        _safe_echo("Work-Until: OFF")
    else:
        set_work_until_plan(db, plan_title_or_off)
        set_config_flag(db, "plan_update_enabled", "1")
        _safe_echo(f"Work-Until: {plan_title_or_off} (plan-sync auto-enabled)")


# ═══════════════════════════════════════════════════════════════════
# workspace / rule / cron — minimal CRUD for external orchestration
# ═══════════════════════════════════════════════════════════════════

@cmd_assistant.group("workspace")
def cmd_workspace():
    """Manage workspaces."""


@cmd_workspace.command("list")
@click.option("--json", "json_out", is_flag=True)
def cmd_ws_list(json_out):
    adb = _require_adb()
    conn = _open_db()
    rows = adb.list_workspaces(conn, active_only=False)
    conn.close()
    if json_out:
        _json_out(rows)
        return
    for w in rows:
        status = "on " if w["active"] else "off"
        path = w.get("path") or w.get("url") or ""
        _safe_echo(f"[{status}] {w['key']:15}  {path}")


@cmd_workspace.command("set")
@click.argument("key")
@click.option("--title", required=True)
@click.option("--path", "path_val", default=None)
@click.option("--url", default=None)
@click.option("--desc", "description", default=None)
def cmd_ws_set(key, title, path_val, url, description):
    adb = _require_adb()
    conn = _open_db()
    w = adb.upsert_workspace(conn, {
        "key": key, "title": title, "kind": "url" if url else "folder",
        "path": path_val, "url": url, "description": description,
        "key_files": [], "tags": [], "active": True,
    })
    conn.close()
    _safe_echo(f"{w['key']}  {w['title']}")


@cmd_assistant.group("rule")
def cmd_rule():
    """Manage rules (scoped to goal/plan or global)."""


@cmd_rule.command("list")
@click.option("--json", "json_out", is_flag=True)
def cmd_rule_list(json_out):
    adb = _require_adb()
    conn = _open_db()
    rows = [r for r in adb.list_extra(conn) if r["kind"] == "rule"]
    conn.close()
    if json_out:
        _json_out(rows)
        return
    for r in rows:
        scope = r.get("scope")
        tag = "[global]" if not scope else "[scoped]"
        _safe_echo(f"{r['item_id']}  {tag}  {r['title']}")


@cmd_rule.command("add")
@click.option("--title", required=True)
@click.option("--content", required=True)
@click.option("--goal-id", "goal_id", default=None)
@click.option("--plan-id", "plan_id", default=None)
def cmd_rule_add(title, content, goal_id, plan_id):
    adb = _require_adb()
    scope = None
    if goal_id or plan_id:
        scope = {}
        if goal_id:
            scope["goal_ids"] = [goal_id]
        if plan_id:
            scope["plan_ids"] = [plan_id]
    conn = _open_db()
    r = adb.create_extra(conn, {
        "kind": "rule", "title": title, "content": content, "scope": scope,
    })
    conn.close()
    _safe_echo(f"{r['item_id']}  {r['title']}")


@cmd_assistant.group("cron")
def cmd_cron():
    """Manage scheduled jobs."""


@cmd_cron.command("list")
@click.option("--json", "json_out", is_flag=True)
def cmd_cron_list(json_out):
    adb = _require_adb()
    conn = _open_db()
    rows = adb.list_cron(conn)
    conn.close()
    if json_out:
        _json_out(rows)
        return
    for j in rows:
        status = "on " if j["active"] else "off"
        _safe_echo(f"[{status}] {j['job_id']}  {j['schedule']}  — {j['task_prompt'][:80]}")


@cmd_cron.command("add")
@click.option("--schedule", required=True, help="5-field cron expression")
@click.option("--task", "task_prompt", required=True)
def cmd_cron_add(schedule, task_prompt):
    adb = _require_adb()
    conn = _open_db()
    j = adb.create_cron(conn, {"schedule": schedule, "task_prompt": task_prompt})
    conn.close()
    _safe_echo(f"{j['job_id']}  {j['schedule']}")


@cmd_cron.command("delete")
@click.argument("job_id")
def cmd_cron_delete(job_id):
    adb = _require_adb()
    conn = _open_db()
    ok = adb.delete_cron(conn, job_id)
    conn.close()
    _safe_echo("deleted" if ok else "not found")


# ═══════════════════════════════════════════════════════════════════
# policy — 模型策略管理
# ═══════════════════════════════════════════════════════════════════

@cmd_assistant.group("policy")
def cmd_policy():
    """Manage the active model policy (production/balanced/cheap/robust_test/max_quality).

    The policy maps role→tier→model across the whole system. Set once, affects all
    subsequent LLM calls. Use for budget control, robustness testing, or quality boost.
    """


@cmd_policy.command("show")
@click.option("--json", "json_out", is_flag=True)
def cmd_policy_show(json_out):
    """Show current policy and how each role resolves to a concrete model."""
    from omnicompany.runtime.llm.llm import ModelRegistry
    r = ModelRegistry.get_instance()
    info = r.describe()
    if json_out:
        _json_out(info)
        return
    _safe_echo(f"Active policy: {info['active_policy']}")
    _safe_echo(f"Available:     {', '.join(info['available_policies'])}")
    _safe_echo("")
    _safe_echo("Role → resolved model:")
    for role, meta in info["roles"].items():
        pin_marker = " [PINNED]" if meta["pinned"] else ""
        _safe_echo(f"  {role:22}  [{meta['tier']:8}]  → {meta['resolved_model']}{pin_marker}")


@cmd_policy.command("set")
@click.argument("policy_name")
def cmd_policy_set(policy_name):
    """Switch the active policy for this process.

    Note: CLI is short-lived, so this only affects the current command.
    For persistent switching, use OMNI_MODEL_POLICY env var:
        export OMNI_MODEL_POLICY=robust_test
    """
    from omnicompany.runtime.llm.llm import ModelRegistry
    r = ModelRegistry.get_instance()
    try:
        r.set_active_policy(policy_name)
        _safe_echo(f"Policy set to: {policy_name}")
        _safe_echo("(process-local; set OMNI_MODEL_POLICY env var for persistence)")
    except ValueError as e:
        raise click.ClickException(str(e))


@cmd_policy.command("pin")
@click.argument("role")
@click.argument("model")
def cmd_policy_pin(role, model):
    """Pin a specific role to a specific model (overrides policy).

    Example: omni assistant policy pin ide_agent claude-sonnet-4-6
             omni assistant policy pin ide_agent ""    # clear pin
    """
    from omnicompany.runtime.llm.llm import ModelRegistry
    r = ModelRegistry.get_instance()
    r.set_role_model(role, model)
    if model:
        _safe_echo(f"Pinned {role} -> {model}")
    else:
        _safe_echo(f"Cleared pin on {role}")


# ═══════════════════════════════════════════════════════════════════
# history / watch — 监督进度
# ═══════════════════════════════════════════════════════════════════

@cmd_assistant.command("history")
@click.option("-n", "--limit", default=10, type=int)
@click.option("--json", "json_out", is_flag=True)
def cmd_history(limit, json_out):
    """Show compact-time work history archives."""
    adb = _require_adb()
    conn = _open_db()
    rows = adb.list_history(conn, limit=limit)
    conn.close()
    if json_out:
        _json_out(rows)
        return
    if not rows:
        _safe_echo("(no history)")
        return
    import datetime as _dt
    for h in rows:
        ts = _dt.datetime.fromtimestamp(h["compacted_at"]).strftime("%Y-%m-%d %H:%M")
        _safe_echo(f"=== {ts}  session={h['session_id'][:12]} ===")
        _safe_echo(h["summary"])
        _safe_echo("")


@cmd_assistant.command("tail")
@click.option("--event-type", default=None, help="Filter events.db by event_type prefix")
@click.option("-n", "--limit", default=20, type=int)
def cmd_tail(event_type, limit):
    """Tail recent events from ide_events.db (cross-check agent activity)."""
    import sqlite3

    from omnicompany.core.config import resolve_unified_db_path
    events_db = resolve_unified_db_path("ide_events.db")
    if not events_db.exists():
        _safe_echo(f"(no events.db at {events_db})")
        return

    with sqlite3.connect(events_db) as conn:
        conn.row_factory = sqlite3.Row
        q = "SELECT id, event_type, source, timestamp FROM events"
        vals: list = []
        if event_type:
            q += " WHERE event_type LIKE ?"
            vals.append(event_type + "%")
        q += " ORDER BY timestamp DESC LIMIT ?"
        vals.append(limit)
        rows = conn.execute(q, vals).fetchall()

    for r in reversed(rows):  # oldest first
        _safe_echo(f"{r['timestamp'][:19]}  {r['event_type']:30}  src={r['source']}  id={r['id'][:12]}")
