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
import sys
from typing import Any

import click

from omnicompany.packages.services._core.identity import (
    resolve_active_trace_id,
    current_session_meta,
    record_active_session,
    get_session_binding,
    session_scope_segments,
    upsert_session_scope_segment,
    update_session_binding,
    session_writes,
)


def _project_peer_snapshot(
    project: str,
    *,
    current_session_id: str | None = None,
    current_pty_id: str | None = None,
    current_workstream: str | None = None,
    current_task_id: str | None = None,
    records: list[dict[str, Any]] | None = None,
    bindings: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read-only same-project collaboration view; never becomes a second ledger."""
    try:
        if records is None:
            from omnicompany.packages.services.boss_sight.services.agent_registry import list_records
            from omnicompany.packages.services._core.identity import bindings_by_session_key

            records = list_records(running_only=True, rebuild_first=False)
            bindings = bindings_by_session_key()
    except Exception as exc:  # noqa: BLE001 - binding must still succeed if the cache is unavailable
        return {"project": project, "available": False, "error": str(exc), "peers": [], "warnings": []}

    peers: list[dict[str, Any]] = []
    for row in records or []:
        provider = str(row.get("provider") or "").strip()
        native_id = str(row.get("session_id") or "").strip()
        candidate_keys = [
            f"pty:{row.get('pty_id')}" if row.get("pty_id") else "",
            f"session:{native_id}" if native_id else "",
            f"{provider}:{native_id}" if provider and native_id else "",
            str(row.get("key") or ""),
        ]
        binding: dict[str, Any] = {}
        for candidate_key in candidate_keys:
            candidate = (bindings or {}).get(candidate_key)
            if not isinstance(candidate, dict):
                continue
            if candidate_key.startswith("pty:") and native_id:
                native_ids = {
                    str(candidate.get(field) or "").strip()
                    for field in ("session_id", "claude_session_id")
                    if str(candidate.get(field) or "").strip()
                }
                if native_ids and native_id not in native_ids:
                    continue
            binding = candidate
            break
        effective_project = str(binding.get("project") or row.get("project") or "")
        if not row.get("running") or effective_project != project:
            continue
        if current_session_id and str(row.get("session_id") or "") == current_session_id:
            continue
        if current_pty_id and str(row.get("pty_id") or "") == current_pty_id:
            continue
        topic = str(binding.get("topic") or row.get("topic") or row.get("current_task") or "").strip()
        peers.append(
            {
                "key": str(row.get("key") or ""),
                "identity": str(row.get("identity") or row.get("name") or row.get("key") or "未知会话"),
                "run_status": str(row.get("run_status") or "unknown"),
                "active_plan": str(binding.get("active_plan") or row.get("active_plan") or ""),
                "workstream": str(binding.get("workstream") or row.get("workstream") or ""),
                "task_id": str(binding.get("task_id") or row.get("task_id") or ""),
                "topic": topic[:160],
            }
        )
    peers.sort(key=lambda row: (row["workstream"], row["task_id"], row["identity"]))

    warnings: list[str] = []
    if peers and not current_task_id:
        warnings.append(
            "当前未绑定 task；并行写入前应认领带 file_scope 的 task，"
            "否则同一 Material/文件只保留一个写入会话。"
        )
    same_task = [row["identity"] for row in peers if current_task_id and row["task_id"] == current_task_id]
    if same_task:
        warnings.append(f"同一 task 已有会话：{'、'.join(same_task)}；先协调或改为单一写入方。")
    same_workstream = [
        row["identity"]
        for row in peers
        if current_workstream and row["workstream"] == current_workstream and row["task_id"] != current_task_id
    ]
    if same_workstream:
        warnings.append(f"同一 workstream 还有：{'、'.join(same_workstream)}；确认 task/file_scope 不重叠。")
    unscoped = [row["identity"] for row in peers if not row["workstream"] and not row["task_id"]]
    if unscoped:
        warnings.append(f"未声明 workstream/task 的同项目会话：{'、'.join(unscoped)}。")
    return {"project": project, "available": True, "peers": peers, "warnings": warnings}


def _project_peer_lines(snapshot: dict[str, Any]) -> list[str]:
    if not snapshot.get("available"):
        return ["同项目协作摘要暂不可用（绑定已完成，不受影响）。"]
    peers = snapshot.get("peers") or []
    lines = [f"同项目协作：另有 {len(peers)} 个运行会话。"]
    for row in peers:
        scope = " ".join(
            value
            for value in (
                f"workstream={row['workstream']}" if row.get("workstream") else "",
                f"task={row['task_id']}" if row.get("task_id") else "",
                f"plan={row['active_plan']}" if row.get("active_plan") else "",
            )
            if value
        ) or "未声明分工"
        topic = f" · {row['topic']}" if row.get("topic") else ""
        lines.append(f"  - {row['identity']} [{row['run_status']}] {scope}{topic}")
        if row.get("key"):
            lines.append(f"    联系：omni agents send {row['key']} -m \"<协调内容>\"")
    lines.extend(f"  ! {warning}" for warning in snapshot.get("warnings") or [])
    return lines


def _echo_project_peers(snapshot: dict[str, Any]) -> None:
    for line in _project_peer_lines(snapshot):
        click.echo(line)


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
    click.echo(f"workstream         : {meta.get('workstream') or '-'}")
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
      locate   按任意 id (pty_id / trace_id / 各 CLI 原生 session id / 文件名 / pid)
               反查会话文件位置 + 会话信息
    """


@cmd_session.command("ownership")
@click.argument("pty_id")
def cmd_session_ownership(pty_id: str) -> None:
    """Read current runtime owner/reclaim policy and the unchanged creation audit."""
    from omnicompany.packages.services._core.agent.control_plane import _request_json

    try:
        page = _request_json("/cc/tab-states")
        live = next((row for row in page.get("pty", []) if row.get("id") == pty_id), None)
        if live is None:
            raise ValueError("exact live PTY not found")
        binding = get_session_binding(pty_id)
        result = {key: live.get(key) for key in
                  ("id", "provider_session_id", "origin", "visibility", "working", "reclaim")}
        result.update(lifecycle_owner=binding.get("lifecycle_owner") or live.get("origin"),
                      creation_origin=binding.get("creation_origin") or live.get("origin"),
                      ownership_history=binding.get("ownership_history", []))
    except (ValueError, RuntimeError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@cmd_session.command("take-ownership")
@click.argument("pty_id")
@click.option("--expect-native", required=True, help="Exact current provider-native id; rejects stale targets.")
@click.option("--reason", required=True)
@click.option("--user-authorized", is_flag=True, help="The user explicitly requested ownership of this existing session.")
@click.option("--json", "as_json", is_flag=True)
def cmd_session_take_ownership(pty_id: str, expect_native: str, reason: str, user_authorized: bool, as_json: bool) -> None:
    """Take current ownership as human; preserve PTY, native goal and creation lineage."""
    from omnicompany.packages.services._core.agent.control_plane import list_agent_workers
    from omnicompany.packages.services._core.identity.ownership import take_human_ownership

    try:
        if not user_authorized:
            raise ValueError("--user-authorized is required")
        live = next((row for row in list_agent_workers() if row.get("id") == pty_id), {})
        result = take_human_ownership(
            pty_id, expected_native=expect_native, actor=resolve_active_trace_id(),
            reason=reason, user_authorized=user_authorized, live=live,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@cmd_session.command("current")
def cmd_session_current() -> None:
    """输出当前 trace_id (一行字符串).

    供 shell 脚本嵌入, 例如:
        TRACE=$(omni session current)
        omni register material --trace-id=$TRACE ...
    """
    click.echo(resolve_active_trace_id())


@cmd_session.command("peers")
@click.option("--project", default=None, help="项目 id（默认当前会话绑定项目）")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_session_peers(project: str | None, as_json: bool) -> None:
    """查看同项目运行会话、分工重叠与直接联系入口。"""
    meta = current_session_meta()
    project = project or str(meta.get("project") or "")
    if not project:
        raise click.ClickException("当前会话未绑定 project；请传 --project 或先运行 omni session bind")
    snapshot = _project_peer_snapshot(
        project,
        current_session_id=meta.get("session_id"),
        current_pty_id=meta.get("pty_id"),
        current_workstream=meta.get("workstream"),
        current_task_id=meta.get("task_id"),
    )
    if as_json:
        click.echo(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        _echo_project_peers(snapshot)


@cmd_session.command("bind")
@click.option("--trace-id", default=None, help="要绑定的 trace_id(默认当前会话)")
@click.option("--session-id", default=None, help="可选: provider 原生 session id；Codex trace 可自动推导")
@click.option("--claude-session-id", default=None, help="可选: claude session id")
@click.option("--pty-id", default=None, help="可选: dashboard PTY id")
@click.option("--plan", "--active-plan", "active_plan", default=None, help="绑定 plan (plan_id, 如 dashboard/[2026-07-09]X)")
@click.option("--project", default=None, help="绑定 project")
@click.option("--clear-project", is_flag=True, help="解除 project 绑定")
@click.option("--workstream", default=None, help="绑定项目内 workstream (如 walker.card-programming)")
@click.option("--task", "task_id", default=None, help="绑定 task (task_id, 如 <plan>.3)")
@click.option("--provider", default=None, help="claude_code / codex")
@click.option("--topic", default=None, help="会话自述主题(一句话,自我声明,盖过 digest 推测)")
def cmd_session_bind(
    trace_id: str | None,
    session_id: str | None,
    claude_session_id: str | None,
    pty_id: str | None,
    active_plan: str | None,
    project: str | None,
    clear_project: bool,
    workstream: str | None,
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
    if clear_project and project is not None:
        raise click.ClickException("use either --project or --clear-project, not both")
    project = "" if clear_project else (project if project is not None else existing.get("project"))
    workstream = workstream if workstream is not None else existing.get("workstream")
    if clear_project:
        # A workstream is scoped by its project and cannot survive a project clear.
        workstream = None
    task_id = task_id if task_id is not None else existing.get("task_id")
    try:
        from omnicompany.packages.services._core.identity import validate_session_binding_scope

        validated_project, workstream = validate_session_binding_scope(project, workstream)
        # ``--clear-project`` is a mutation intent, not an absent value.
        project = "" if clear_project else validated_project
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if workstream:
        from omnicompany.core.workstreams import get_workstream

        workstream_row = get_workstream(workstream)
        if workstream_row is None:
            raise click.ClickException(f"未注册 workstream: {workstream}")
        if project and project != workstream_row["project"]:
            raise click.ClickException(
                f"project={project} 与 workstream={workstream} 的项目 {workstream_row['project']} 不一致"
            )
        project = workstream_row["project"]
    if project:
        from omnicompany.core.projects_registry import list_projects

        registered_projects = {
            str(item.get("id") or "").strip()
            for item in list_projects()
        }
        if project not in registered_projects:
            raise click.ClickException(
                f"unregistered project: {project}; register it first with `omni project register`"
            )
    if task_id and str(task_id).isdigit() and active_plan:
        task_id = f"{active_plan}.{task_id}"
    provider = provider if provider is not None else existing.get("provider")
    if provider is None:
        for candidate in ("codex", "kiro", "qoder", "kimi", "opencode"):
            if trace_id.startswith(f"{candidate}_"):
                provider = candidate
                break
    session_id = session_id if session_id is not None else existing.get("session_id")
    claude_session_id = claude_session_id if claude_session_id is not None else existing.get("claude_session_id")
    if session_id is None and provider:
        provider_prefix = f"{provider}_"
        if trace_id.startswith(provider_prefix):
            session_id = trace_id.removeprefix(provider_prefix)
        elif trace_id.startswith("cc_"):
            session_id = trace_id.removeprefix("cc_")
    if session_id is None and claude_session_id:
        session_id = claude_session_id
    pty_id = pty_id if pty_id is not None else existing.get("pty_id")

    p = record_active_session(
        trace_id=trace_id,
        session_id=session_id,
        claude_session_id=claude_session_id,
        pty_id=pty_id,
        active_plan=active_plan,
        project=project,
        workstream="" if clear_project else workstream,
        task_id=task_id,
        provider=provider,
        cwd=os.getcwd(),
        source="cli_bind",
    )
    if topic:  # 自述主题写进台账(只补台账,不动 active 指针)
        update_session_binding(trace_id, topic=topic)
    bits = [f"trace_id={trace_id}"]
    for label, val in (("plan", active_plan), ("project", project), ("workstream", workstream), ("task", task_id), ("topic", topic)):
        if val:
            bits.append(f"{label}={val}")
    click.echo(f"OK 已绑定 {' '.join(bits)} → {p}")
    if project:
        _echo_project_peers(
            _project_peer_snapshot(
                project,
                current_session_id=session_id,
                current_pty_id=pty_id,
                current_workstream=workstream,
                current_task_id=task_id,
            )
        )


@cmd_session.command("bind-segment")
@click.option("--trace-id", default=None, help="要补绑定的 trace_id（默认当前会话）")
@click.option("--segment-id", required=True, help="稳定分段 id")
@click.option("--project", default=None, help="该分段所属 Project")
@click.option("--workstream", default=None, help="该分段所属 Workstream")
@click.option("--plan", "active_plan", default=None, help="该分段所属 Plan")
@click.option("--task", "task_id", default=None, help="该分段所属 Task")
@click.option("--start-event-ref", default=None, help="分段起始原会话事件引用")
@click.option("--end-event-ref", default=None, help="分段结束原会话事件引用")
@click.option("--start-source-line", type=int, default=None, help="原会话起始行")
@click.option("--end-source-line", type=int, default=None, help="原会话结束行")
@click.option(
    "--state",
    "binding_state",
    type=click.Choice(["confirmed", "observed", "candidate"]),
    default="candidate",
    show_default=True,
    help="内部状态：显式确认、确定性观察或语义候选；人读界面显示中文",
)
@click.option("--source", default="retrospective_semantic", show_default=True, help="绑定来源")
@click.option("--rationale", default="", help="简短依据")
@click.option("--evidence-ref", "evidence_refs", multiple=True, help="可重复的原会话证据引用")
def cmd_session_bind_segment(
    trace_id: str | None,
    segment_id: str,
    project: str | None,
    workstream: str | None,
    active_plan: str | None,
    task_id: str | None,
    start_event_ref: str | None,
    end_event_ref: str | None,
    start_source_line: int | None,
    end_source_line: int | None,
    binding_state: str,
    source: str,
    rationale: str,
    evidence_refs: tuple[str, ...],
) -> None:
    """补写同一会话中某一段的 Project/Plan/Task 绑定。"""

    trace_id = trace_id or resolve_active_trace_id()
    upsert_session_scope_segment(
        trace_id,
        segment_id,
        project=project,
        workstream=workstream,
        active_plan=active_plan,
        task_id=task_id,
        start_event_ref=start_event_ref,
        end_event_ref=end_event_ref,
        start_source_line=start_source_line,
        end_source_line=end_source_line,
        binding_state=binding_state,
        source=source,
        rationale=rationale,
        evidence_refs=evidence_refs,
    )
    click.echo(f"OK 已补会话分段绑定 trace_id={trace_id} segment={segment_id}")


@cmd_session.command("segments")
@click.option("--trace-id", default=None, help="要查看的 trace_id（默认当前会话）")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_session_segments(trace_id: str | None, as_json: bool) -> None:
    """查看同一会话内按时间或事件范围保存的工作归属。"""

    trace_id = trace_id or resolve_active_trace_id()
    rows = session_scope_segments(trace_id)
    if as_json:
        click.echo(json.dumps({"会话": trace_id, "分段": rows}, ensure_ascii=False, indent=2))
        return
    state_zh = {"confirmed": "已确认", "observed": "已观察", "candidate": "候选"}
    click.echo(f"会话: {trace_id}")
    if not rows:
        click.echo("  （尚无分段绑定，使用会话默认绑定）")
        return
    for row in rows:
        state = state_zh.get(str(row.get("binding_state") or ""), "未识别状态")
        scope = " / ".join(
            value for value in (
                row.get("project"), row.get("workstream"), row.get("active_plan"), row.get("task_id")
            ) if value
        )
        click.echo(f"  {row.get('segment_id')} · {state} · {scope}")


@cmd_session.group("companion")
def cmd_session_companion() -> None:
    """伴随会话维护队列：查看、手动入队、执行与重试。"""


@cmd_session_companion.command("status")
@click.option("--state", type=click.Choice(["queued", "running", "done", "failed"]), default=None)
@click.option("--limit", type=int, default=30, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_session_companion_status(state: str | None, limit: int, as_json: bool) -> None:
    """查看维护事件、尝试次数与最终回执状态。"""

    from omnicompany.packages.services._core.session_companion import CompanionStore

    rows = CompanionStore().list(state=state, limit=limit)
    if as_json:
        click.echo(json.dumps({"items": rows, "count": len(rows)}, ensure_ascii=False, indent=2))
        return
    if not rows:
        click.echo("（没有伴随会话维护事件）")
        return
    for row in rows:
        click.echo(
            f"{row['state']:7s} {row['event_id']} · audit={row.get('audit_status') or '-'} · "
            f"{row.get('trigger') or '-'} · "
            f"attempt={row.get('attempt') or 0} · {row.get('trace_id') or '-'}"
        )
        if row.get("finding_count"):
            click.echo(f"  findings: {row['finding_count']}")
        if row.get("last_error"):
            click.echo(f"  error: {_one_line(row['last_error'])}")


@cmd_session_companion.command("inspect")
@click.argument("event_id")
@click.option("--json", "as_json", is_flag=True, help="输出完整事件、提案、provider 审计与动作回执")
def cmd_session_companion_inspect(event_id: str, as_json: bool) -> None:
    """查看一条事件从原始输入到实际落盘回执的完整追责链。"""

    from omnicompany.packages.services._core.session_companion import CompanionStore

    from omnicompany.packages.services._core.session_companion.audit import audit_record_with_effects

    try:
        record = CompanionStore().get(event_id)
    except KeyError as exc:
        raise click.ClickException(f"没有伴随维护事件 {event_id}") from exc
    record["audit"] = audit_record_with_effects(record)
    if as_json:
        click.echo(json.dumps(record, ensure_ascii=False, indent=2))
        return
    event = record.get("event") or {}
    audit = record.get("audit") or {}
    agent = record.get("agent") or {}
    click.echo(
        f"{event_id} · queue={record.get('state')} · audit={audit.get('status')} · "
        f"attempt={record.get('attempt') or 0}"
    )
    click.echo(
        f"  trace={event.get('trace_id') or '-'} trigger={event.get('trigger') or '-'} "
        f"project={event.get('project') or '-'}"
    )
    click.echo(
        f"  agent={agent.get('provider') or '-'} / {agent.get('model') or '-'} "
        f"run={agent.get('run_id') or '-'} duration_ms={agent.get('duration_ms') or 0}"
    )
    if agent.get("search_queries"):
        click.echo(f"  searches={len(agent.get('searches') or [])}: {', '.join(agent['search_queries'])}")
    for receipt in record.get("receipts") or []:
        target_details = [
            f"{key}={receipt.get(key)}"
            for key in (
                "material_id",
                "decision_id",
                "note_id",
                "segment_id",
                "verdict",
                "project",
                "plan",
            )
            if receipt.get(key)
        ]
        click.echo(
            f"  receipt {receipt.get('action') or '-'}:{receipt.get('status') or '-'}"
            + (f" · {', '.join(target_details)}" if target_details else "")
            + (f" · {_one_line(str(receipt.get('reason') or ''))}" if receipt.get("reason") else "")
        )
    for finding in audit.get("findings") or []:
        click.echo(f"  finding [{finding.get('code')}]: {_one_line(str(finding.get('message') or ''))}")


@cmd_session_companion.command("audit")
@click.option("--limit", type=int, default=20, show_default=True)
@click.option("--trace-id", default="", help="只看指定主会话")
@click.option(
    "--status",
    "audit_status",
    type=click.Choice(["healthy", "degraded", "action_required", "failed"]),
    default=None,
)
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_session_companion_audit(
    limit: int,
    trace_id: str,
    audit_status: str | None,
    as_json: bool,
) -> None:
    """审计最近事件是否真正闭环，而不只看队列是否 done。"""

    from omnicompany.packages.services._core.identity import all_session_bindings
    from omnicompany.packages.services._core.session_companion import CompanionStore

    from omnicompany.packages.services._core.session_companion.audit import audit_record_with_effects
    from omnicompany.packages.services._core.session_companion.coverage import (
        provider_coverage_report,
        runtime_session_registry,
    )

    store = CompanionStore()
    candidates = store.list(limit=100_000)
    records = [store.get(str(candidate["event_id"])) for candidate in candidates]
    coverage = provider_coverage_report(
        records,
        bindings=all_session_bindings(),
        sessions=runtime_session_registry(),
    )
    rows: list[dict[str, Any]] = []
    for candidate, record in zip(candidates, records, strict=True):
        if trace_id and candidate.get("trace_id") != trace_id:
            continue
        record["audit"] = audit_record_with_effects(record)
        if audit_status and record["audit"].get("status") != audit_status:
            continue
        rows.append({
            **candidate,
            "project": (record.get("event") or {}).get("project") or "",
            "audit_status": record["audit"].get("status"),
            "finding_count": record["audit"].get("finding_count"),
            "audit": record["audit"],
            "agent": record.get("agent") or {},
        })
        if len(rows) >= max(1, limit):
            break
    if as_json:
        click.echo(json.dumps({
            "items": rows,
            "count": len(rows),
            "coverage": coverage,
        }, ensure_ascii=False, indent=2))
        return
    totals = coverage["totals"]
    click.echo(
        "provider coverage: "
        f"{totals['providers_covered']}/{totals['registered_provider_count']} covered, "
        f"{totals['providers_missing_events']} with sessions but no events"
    )
    for provider in coverage["providers"]:
        if provider["status"] == "no_sessions":
            continue
        click.echo(
            f"  {provider['provider']:14s} {provider['status']:14s} "
            f"sessions={provider['registered_sessions']} events={provider['companion_events']} "
            f"scoped={provider['scoped_bindings']}"
        )
    if not rows:
        click.echo("（没有匹配的伴随维护审计记录）")
        return
    for row in rows:
        audit = row.get("audit") or {}
        click.echo(
            f"{audit.get('status') or '-':15s} {row['event_id']} · {row.get('trigger') or '-'} · "
            f"{row.get('trace_id') or '-'}"
        )
        for finding in audit.get("findings") or []:
            click.echo(f"  [{finding.get('code')}] {_one_line(str(finding.get('message') or ''))}")


@cmd_session_companion.command("backfill")
@click.option("--limit", type=click.IntRange(1, 500), default=20, show_default=True)
@click.option("--apply", "apply_changes", is_flag=True, help="Explicitly enqueue; default is dry-run")
@click.option("--kick/--no-kick", default=False, help="Start the background worker after apply")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
def cmd_session_companion_backfill(
    limit: int,
    apply_changes: bool,
    kick: bool,
    as_json: bool,
) -> None:
    """Plan historical companion gaps; apply only enqueues lifecycle events."""

    from omnicompany.packages.services._core.session_companion.backfill import run_backfill

    result = run_backfill(limit=limit, apply=apply_changes, kick=kick)
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    click.echo(
        f"mode={result['mode']} candidates={result['enqueue_candidates']} "
        f"enqueued={result['enqueued']} existing={result['existing']}"
    )
    for item in result["items"]:
        click.echo(
            f"  {item['provider']:14s} {item['action']:7s} "
            f"{item.get('reason') or '-'} session={item['session_key']}"
        )


@cmd_session_companion.command("enqueue")
@click.option(
    "--trigger",
    type=click.Choice(["pre_compact", "post_compact", "stop", "native_compact", "manual"]),
    default="manual",
    show_default=True,
)
@click.option("--user-input", default="", help="本次原始用户输入；省略则取 hook 最近捕获")
@click.option("--assistant-output", default="", help="最后 AI 文字")
@click.option("--compact-summary", default="", help="provider compact 摘要")
@click.option("--dry-run", is_flag=True, help="只打印事件，不写队列/业务真源")
@click.option("--kick/--no-kick", default=True, help="入队后是否唤起后台 worker")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_session_companion_enqueue(
    trigger: str,
    user_input: str,
    assistant_output: str,
    compact_summary: str,
    dry_run: bool,
    kick: bool,
    as_json: bool,
) -> None:
    """为当前主会话手动创建一次维护事件。"""

    from omnicompany.packages.services._core.session_companion import CompanionStore
    from omnicompany.packages.services._core.session_companion.service import enqueue_lifecycle_event

    meta = current_session_meta()
    binding = get_session_binding(meta.get("trace_id"))
    store = CompanionStore()
    turn = store.user_turn(str(meta.get("trace_id") or ""))
    result = enqueue_lifecycle_event(
        trigger=trigger,
        trace_id=str(meta.get("trace_id") or ""),
        # An explicit OMNI_CC_TRACE_ID may point at a PTY belonging to a
        # different provider than the shell running this CLI.  The persisted
        # session binding is authoritative in that case; using ``meta`` first
        # silently re-keyed digests onto the caller's Codex session.
        provider=str(binding.get("provider") or meta.get("provider") or "unknown"),
        session_id=str(
            binding.get("session_id")
            or meta.get("session_id")
            or meta.get("claude_session_id")
            or ""
        ),
        cwd=str(binding.get("cwd") or meta.get("cwd") or os.getcwd()),
        first_user_input=str(turn.get("first_user_input") or user_input),
        user_input=user_input or str(turn.get("latest_user_input") or ""),
        assistant_output=assistant_output,
        compact_summary=compact_summary,
        project=str(binding.get("project") or ""),
        workstream=str(binding.get("workstream") or ""),
        active_plan=str(binding.get("active_plan") or ""),
        store=store,
        dry_run=dry_run,
        kick=kick,
    )
    if as_json or dry_run:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        click.echo(
            f"{'OK 已入队' if result.get('created') else '已存在'} "
            f"event={result.get('event_id') or '-'} state={result.get('state') or '-'}"
        )


@cmd_session_companion.command("run")
@click.option("--once/--drain", default=True, help="只处理一条，或有界清空当前队列")
@click.option("--max-events", type=int, default=8, show_default=True)
@click.option("--concurrency", type=click.IntRange(1, 2), default=2, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_session_companion_run(once: bool, max_events: int, concurrency: int, as_json: bool) -> None:
    """前台执行维护；用于调试、cron 或后台唤起失败时补跑。"""

    from omnicompany.packages.services._core.session_companion.service import CompanionService

    service = CompanionService()
    if once:
        item = service.run_once()
        result = [] if item is None else [item]
    else:
        result = service.run_pending(max_events=max_events, concurrency=concurrency)
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    elif not result:
        click.echo("（队列为空）")
    else:
        for item in result:
            click.echo(f"{item.get('state')} {item.get('event_id')}")
            if item.get("error"):
                click.echo(f"  error: {item['error']}")


@cmd_session_companion.command("retry")
@click.argument("event_id")
@click.option("--reason", default="operator retry", show_default=True, help="操作员重试原因，进入 attempt_history")
def cmd_session_companion_retry(event_id: str, reason: str) -> None:
    """把一条 failed 事件移回 queued；幂等动作仍不会重复写。"""

    from omnicompany.packages.services._core.session_companion import CompanionStore
    from omnicompany.packages.services._core.session_companion.service import kick_background

    store = CompanionStore()
    try:
        path = store.retry(event_id, reason=reason)
    except KeyError as exc:
        raise click.ClickException(f"failed 队列里没有事件 {event_id}") from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    started = kick_background(store.root)
    click.echo(f"OK 已重试 event={event_id} path={path} worker_started={str(started).lower()}")


@cmd_session_companion.command("replay")
@click.argument("event_id")
@click.option("--reason", required=True, help="操作员重放原因，进入 attempt_history")
def cmd_session_companion_replay(event_id: str, reason: str) -> None:
    """只重放审计为 action_required 的 done 事件，并保留旧尝试。"""

    from omnicompany.packages.services._core.session_companion import CompanionStore
    from omnicompany.packages.services._core.session_companion.service import kick_background

    store = CompanionStore()
    try:
        path = store.replay_action_required(event_id, reason=reason)
    except KeyError as exc:
        raise click.ClickException(f"done 队列里没有事件 {event_id}") from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    started = kick_background(store.root)
    click.echo(f"OK 已重放 event={event_id} path={path} worker_started={str(started).lower()}")


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


def _safe_echo(text: str) -> None:
    """Windows GBK 控制台下不因为一个字符炸掉整条输出。"""
    try:
        click.echo(text)
    except UnicodeEncodeError:
        enc = (sys.stdout.encoding or "utf-8")
        click.echo(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def _one_line(text: str, limit: int = 220) -> str:
    """速览行压成一行, 免得一段 handoff 把命中列表冲散。"""
    flat = " ".join(str(text).split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def _human_size(size: int | None) -> str:
    if not size:
        return "0B"
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"


@cmd_session.command("locate")
@click.argument("session_ref")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出 (供脚本 / AI 消费)")
@click.option("--limit", "-n", type=int, default=10, help="最大命中条数 (默认 10)")
@click.option("--peek", "-p", is_flag=True, help="顺带读会话文件头尾, 给出会话主题速览")
@click.option("--path-only", is_flag=True, help="只打印命中的会话文件绝对路径 (每行一个)")
@click.option("--no-orphans", is_flag=True, help="只查注册表, 不回落扫各 provider 目录")
def cmd_session_locate(
    session_ref: str,
    as_json: bool,
    limit: int,
    peek: bool,
    path_only: bool,
    no_orphans: bool,
) -> None:
    """按任意 id 反查会话文件位置 + 会话信息.

    SESSION_REF 可以是下面任意一种(前缀不全也行, ≥6 位按前缀匹配):
      - omni 注册表主键: pty_id (a1c7315e7df64739) / chat id (chat-73322cebce56)
      - 身份链 trace_id: codex_<uuid> / cc_<uuid>
      - 各家 CLI 自己的原生 session id (codex thread / claude session / kimi / qoder ...)
      - 会话文件名或绝对路径: rollout-2026-08-04T14-29-54-<uuid>.jsonl
      - 进程 pid (child_pid / host_pid)

    例:
      omni session locate a1c7315e7df64739          # 看它是谁 / 文件在哪
      omni session locate a1c7315e --peek           # 顺带看这会话在聊什么
      omni session locate <id> --path-only          # 只要路径, 直接喂给别的工具
    """
    from omnicompany.packages.services._core.identity.session_locator import locate_sessions

    matches = locate_sessions(
        session_ref, limit=limit, peek=peek or as_json, include_orphans=not no_orphans
    )

    if as_json:
        click.echo(json.dumps([m.to_dict() for m in matches], ensure_ascii=False, indent=2))
        raise SystemExit(0 if matches else 1)

    if not matches:
        _safe_echo(f"× 没找到跟 {session_ref!r} 对得上的会话")
        _safe_echo("  查过: cc_sessions.json 注册表全部 id 字段 + 各 provider 会话目录")
        raise SystemExit(1)

    if path_only:
        for m in matches:
            t = m.transcript
            if t:
                click.echo(t.path)
        raise SystemExit(0)

    for idx, m in enumerate(matches):
        if idx:
            click.echo()
        e = m.entry
        _safe_echo(f"● {m.session_id}  [{m.kind}/{m.provider or '-'}]")
        _safe_echo(f"  命中           : {m.matched_field} = {m.matched_value} ({m.match_mode}, {m.source})")
        if e.get("provider_title") or e.get("name"):
            _safe_echo(f"  标题           : {e.get('provider_title') or e.get('name')}")
        if e.get("cwd"):
            _safe_echo(f"  cwd            : {e['cwd']}")
        if e.get("provider_session_id") or e.get("claude_session_id"):
            _safe_echo(f"  原生 session id: {e.get('provider_session_id') or e.get('claude_session_id')}")
        if e.get("cmd"):
            cmd_text = " ".join(str(c) for c in e["cmd"]) if isinstance(e["cmd"], list) else str(e["cmd"])
            _safe_echo(f"  启动命令       : {cmd_text}")
        if e.get("started_at"):
            _safe_echo(f"  起止           : {_locate_ts(e.get('started_at'))} → {_locate_ts(e.get('ended_at')) or '(未结束)'}")
        if e.get("last_submit_at"):
            _safe_echo(f"  最后提交       : {_locate_ts(e.get('last_submit_at'))}")
        if e.get("child_pid") or e.get("host_pid"):
            _safe_echo(f"  进程           : child={e.get('child_pid') or '-'} host={e.get('host_pid') or '-'} port={e.get('host_port') or '-'}")
        if e.get("active_plan"):
            _safe_echo(f"  active_plan    : {e['active_plan']}")
        if m.binding:
            b = m.binding
            bits = [f"{k}={b[k]}" for k in ("active_plan", "project", "task_id", "topic") if b.get(k)]
            if bits:
                _safe_echo(f"  绑定台账       : {'  '.join(bits)}")
        if m.agent:
            a = m.agent
            _safe_echo(f"  agent 身份     : {a.get('identity') or a.get('name') or '-'}  @{a.get('location') or '-'}  "
                       f"({'running' if a.get('running') else 'stopped'}/{a.get('run_status') or '-'})")
            if a.get("current_task"):
                _safe_echo(f"  在做什么       : {_one_line(a['current_task'])}")

        _safe_echo("  会话文件:")
        for f in m.files:
            mark = "✓" if f.exists else "×"
            size = f"  {_human_size(f.size)}" if f.exists else ""
            when = f"  {f.to_dict()['mtime_str']}" if f.mtime else ""
            _safe_echo(f"    {mark} [{f.role}] {f.path or '(未找到)'}{size}{when}")
            if f.note:
                _safe_echo(f"        ↳ {f.note}")

        if m.preview:
            pv = m.preview
            meta = pv.get("meta") or {}
            shown = {k: v for k, v in meta.items() if k != "instructions"}
            if shown:
                _safe_echo(f"  文件内元数据   : {json.dumps(shown, ensure_ascii=False)[:300]}")
            if pv.get("first_user"):
                _safe_echo(f"  首条用户消息   : {_one_line(pv['first_user'])}")
            if pv.get("last_user"):
                _safe_echo(f"  最近用户消息   : {_one_line(pv['last_user'])}")
            if pv.get("last_text"):
                _safe_echo(f"  最后一条       : {_one_line(pv['last_text'])}")
            if pv.get("tail_ts"):
                _safe_echo(f"  文件末尾时间   : {pv['tail_ts']}")


def _locate_ts(ts) -> str:
    from omnicompany.packages.services._core.identity.session_locator import _ts_str

    return _ts_str(ts)


# ── omni whoami (CLI-PHASE3 alias 跟 plan 命名一致) ──────────────────
@click.command("whoami")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
@click.option("--writes/--no-writes", default=True, help="是否显示写过的文件清单")
@click.option("--writes-limit", type=int, default=20)
@click.pass_context
def cmd_whoami(ctx, as_json, writes, writes_limit):
    """显示当前身份 (跟 omni who 等价, CLI-PHASE3 plan 命名)."""
    ctx.invoke(cmd_who, as_json=as_json, writes=writes, writes_limit=writes_limit)
