# [OMNI] origin=ai-ide domain=cli/commands ts=2026-05-02T00:00:00Z type=router status=active agent=ai-ide-current
# [OMNI] summary="omni who / session 命令组, claude code session 身份显示 + 显式绑定"
# [OMNI] why="hook 自动绑定路径之外, 还要 CLI 显式兜底, 让脚本/测试场景能强制设 trace_id. hook 跟 CLI 走的逻辑一致 (都调 services/_core/identity/record_active_session)"
# [OMNI] tags=cli,identity,session,who
# [OMNI] material_id="material:cli.identity.session_manager.implementation.py"
"""omni CLI 身份命令组.

`omni who` — 显示当前 claude code session 的身份元数据 + 写过的文件清单
`omni session current` — 输出 trace_id 一个字符串 (供 shell 脚本 $(omni session current))
`omni session bind --trace-id=<>` — 显式绑定 trace_id (兜底, 测试 / 脚本场景用)

跟 dashboard cc_wrapper 的 SessionStart hook 走同一份 identity 模块, 只是触发方式不同.
"""
from __future__ import annotations

import json
import os

import click

from omnicompany.packages.services._core.identity import (
    resolve_active_trace_id,
    current_session_meta,
    record_active_session,
    get_session_binding,
    update_session_binding,
    session_writes,
)


@click.command("who")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出 (供脚本消费)")
@click.option("--writes/--no-writes", default=True, help="是否显示写过的文件清单 (默认显示, 上限 20 条)")
@click.option("--writes-limit", type=int, default=20, help="写过的文件清单条数上限")
def cmd_who(as_json: bool, writes: bool, writes_limit: int) -> None:
    """显示当前 claude code session 的身份 + 写过的文件清单.

    身份解析优先级 (高→低):
      1. OMNI_CC_TRACE_ID env (CLI 显式)
      2. OMNI_CC_PTY_ID env (dashboard PTY 启动 claude 时传)
      3. data/cc_session_active.json (SessionStart hook 写)
      4. cc_unknown_<ts> (fallback)

    跟 dashboard / web / hook 用同一身份链, CLI 这里只是查询入口.
    """
    meta = current_session_meta()
    write_files = session_writes(meta["trace_id"], limit=writes_limit) if writes else []

    if as_json:
        out = dict(meta)
        out["writes"] = write_files
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return

    click.echo(f"trace_id           : {meta['trace_id']}")
    click.echo(f"source             : {meta['source']}")
    click.echo(f"claude_session_id  : {meta['claude_session_id'] or '-'}")
    click.echo(f"pty_id             : {meta['pty_id'] or '-'}")
    click.echo(f"active_plan        : {meta['active_plan'] or '-'}")
    click.echo(f"project            : {meta.get('project') or '-'}")
    click.echo(f"task_id            : {meta.get('task_id') or '-'}")
    click.echo(f"started_at         : {meta['started_at'] or '-'}")
    click.echo(f"cwd                : {meta['cwd']}")
    click.echo(f"active_file        : {meta['active_file_path']}")
    if writes:
        click.echo()
        click.echo(f"写过的文件 ({len(write_files)} 条, 最近 {writes_limit}):")
        if not write_files:
            click.echo("  (无, 可能 cc_wrapper hook 未运行 / 此 session 还没写过文件)")
        else:
            for w in write_files:
                click.echo(f"  [{w['tool']:6s}] {w['file_path']}  ({w['timestamp']})")


@click.group("session")
def cmd_session() -> None:
    """claude code session 身份管理 (跟 dashboard 共用一身份链).

    子命令:
      current  显示当前 trace_id (一行字符串, 供 shell 脚本嵌入)
      bind     显式绑定 trace_id (兜底, 测试 / 脚本场景)
      meta     显示完整元数据 (跟 omni who 等价但只 meta 不带 writes)
    """


@cmd_session.command("current")
def cmd_session_current() -> None:
    """输出当前 trace_id (一行字符串).

    供 shell 脚本嵌入, 例如:
        TRACE=$(omni session current)
        omni register material --trace-id=$TRACE ...
    """
    click.echo(resolve_active_trace_id())


@cmd_session.command("bind")
@click.option("--trace-id", default=None, help="要绑定的 trace_id(默认当前会话)")
@click.option("--claude-session-id", default=None, help="可选: claude session id")
@click.option("--pty-id", default=None, help="可选: dashboard PTY id")
@click.option("--plan", "--active-plan", "active_plan", default=None, help="绑定 plan (plan_id, 如 dashboard/[2026-07-09]X)")
@click.option("--project", default=None, help="绑定 project")
@click.option("--task", "task_id", default=None, help="绑定 task (task_id, 如 <plan>.3)")
@click.option("--provider", default=None, help="claude_code / codex")
@click.option("--topic", default=None, help="会话自述主题(一句话,自我声明,盖过 digest 推测)")
def cmd_session_bind(
    trace_id: str | None,
    claude_session_id: str | None,
    pty_id: str | None,
    active_plan: str | None,
    project: str | None,
    task_id: str | None,
    provider: str | None,
    topic: str | None,
) -> None:
    """把当前会话绑定到 plan / project / task —— 自认领入口.

    跟 SessionStart hook 共用一份 record_active_session(). 合并式: 只更新给出的字段,
    没给的从现有绑定继承 (不清空). 会话自己跑或人从 dashboard 设,都写同一份台账。
    见 docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.2)。

    例:
      omni session bind --plan dashboard/[2026-07-09]X --task <plan>.3
      omni session bind --project omnicompany   # 只改 project, plan/task 不动
    """
    trace_id = trace_id or resolve_active_trace_id()
    # 继承现有绑定: 没给的字段别清空 (合并语义, 也保护 active 指针文件不被清 plan)。
    existing = get_session_binding(trace_id)
    active_plan = active_plan if active_plan is not None else existing.get("active_plan")
    project = project if project is not None else existing.get("project")
    task_id = task_id if task_id is not None else existing.get("task_id")
    provider = provider if provider is not None else existing.get("provider")
    claude_session_id = claude_session_id if claude_session_id is not None else existing.get("claude_session_id")
    pty_id = pty_id if pty_id is not None else existing.get("pty_id")

    p = record_active_session(
        trace_id=trace_id,
        claude_session_id=claude_session_id,
        pty_id=pty_id,
        active_plan=active_plan,
        project=project,
        task_id=task_id,
        provider=provider,
        cwd=os.getcwd(),
        source="cli_bind",
    )
    if topic:  # 自述主题写进台账(只补台账,不动 active 指针)
        update_session_binding(trace_id, topic=topic)
    bits = [f"trace_id={trace_id}"]
    for label, val in (("plan", active_plan), ("project", project), ("task", task_id), ("topic", topic)):
        if val:
            bits.append(f"{label}={val}")
    click.echo(f"OK 已绑定 {' '.join(bits)} → {p}")


@cmd_session.command("reconcile")
@click.option("--max-age-days", type=float, default=14.0, help="超过这么多天没更新的会话绑定视为死会话, 清掉")
def cmd_session_reconcile(max_age_days: float) -> None:
    """清理陈旧会话绑定台账(死会话)。活会话每轮被 hook/CLI 触碰, 长期没动的就是死的。

    可挂 cron 定期跑(见 .omni/cron/session-bindings-reconcile.json)。
    """
    from omnicompany.packages.services._core.identity import reconcile_bindings
    n = reconcile_bindings(max_age_days=max_age_days)
    click.echo(f"OK 清理陈旧会话绑定 {n} 条(阈值 {max_age_days} 天)")


@cmd_session.command("meta")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_session_meta(as_json: bool) -> None:
    """显示完整 session 元数据 (跟 omni who 等价但不带 writes 清单)."""
    meta = current_session_meta()
    if as_json:
        click.echo(json.dumps(meta, ensure_ascii=False, indent=2))
    else:
        for k, v in meta.items():
            click.echo(f"{k:20s} : {v}")


# ── omni whoami (CLI-PHASE3 alias 跟 plan 命名一致) ──────────────────
@click.command("whoami")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
@click.option("--writes/--no-writes", default=True, help="是否显示写过的文件清单")
@click.option("--writes-limit", type=int, default=20)
@click.pass_context
def cmd_whoami(ctx, as_json, writes, writes_limit):
    """显示当前身份 (跟 omni who 等价, CLI-PHASE3 plan 命名)."""
    ctx.invoke(cmd_who, as_json=as_json, writes=writes, writes_limit=writes_limit)
