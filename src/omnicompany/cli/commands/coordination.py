# [OMNI] origin=codex domain=cli/commands ts=2026-09-05 type=cli status=active
"""Quiet cross-session coordination: pull, acknowledge, terminal events, goals."""

from __future__ import annotations

import json
from typing import Any

import click


def _store():
    from omnicompany.core.quiet_coordination import QuietCoordinationStore

    return QuietCoordinationStore()


def _emit(value: Any, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    elif isinstance(value, dict):
        click.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    else:
        click.echo(value)


def _payload(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"--payload 必须是 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise click.ClickException("--payload 必须是 JSON object")
    return value


def _error(exc: Exception, as_json: bool) -> None:
    if as_json:
        _emit({"ok": False, "error": str(exc), "error_type": type(exc).__name__}, True)
        raise click.exceptions.Exit(2)
    raise click.ClickException(str(exc)) from exc


@click.group("coordination")
def cmd_coordination() -> None:
    """默认持久信箱；父会话明确 action 可由自有 worker 安全领取。"""


@cmd_coordination.command("peek")
@click.argument("session_id")
@click.option("--limit", type=click.IntRange(1, 500), default=100, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_coordination_peek(session_id: str, limit: int, as_json: bool) -> None:
    """读取一个精确 session 的未确认协调消息；不会唤醒或消费。"""
    try:
        records = _store().peek(session_id, limit=limit)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _error(exc, as_json)
        return
    _emit({"ok": True, "session_id": session_id, "messages": records}, as_json)


@cmd_coordination.command("ack")
@click.argument("message_id")
@click.option("--session", "recipient_session_id", required=True, help="确认者的精确 session")
@click.option("--project", "recipient_project", default="", help="确认者的 project")
@click.option("--run", "recipient_run_id", default="", help="确认者的 run generation")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_coordination_ack(
    message_id: str,
    recipient_session_id: str,
    recipient_project: str,
    recipient_run_id: str,
    as_json: bool,
) -> None:
    """以精确 session/project/run 确认一条协调消息。"""
    try:
        result = _store().ack(
            message_id,
            recipient_session_id=recipient_session_id,
            recipient_project=recipient_project,
            recipient_run_id=recipient_run_id,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _error(exc, as_json)
        return
    output = {"ok": bool(result.get("acked")), **result}
    _emit(output, as_json)
    if not result.get("acked"):
        raise click.exceptions.Exit(2)


@cmd_coordination.command("withdraw")
@click.argument("message_id")
@click.option("--reason", required=True)
@click.option("--json", "as_json", is_flag=True)
def cmd_coordination_withdraw(message_id: str, reason: str, as_json: bool) -> None:
    """Withdraw one's own pending request without delivery or recipient acknowledgement."""
    from omnicompany.core.session_messages import source_identity

    try:
        sender = source_identity(from_topic="withdraw obsolete coordination request")
        result = _store().withdraw(message_id, source_session_id=str(sender.get("trace_id") or ""), reason=reason)
    except Exception as exc:
        _error(exc, as_json)
        return
    _emit(result, as_json)


@cmd_coordination.command("deliver-action")
@click.argument("message_id")
def cmd_coordination_deliver_action(message_id: str) -> None:
    """Retry the same explicit parent action; busy workers keep it in their inbox."""
    from omnicompany.core.parent_action_delivery import _native
    from omnicompany.core.session_messages import _post_input, get_message, source_identity

    original = get_message(message_id)
    if not original or original.get("relevance", {}).get("intent") != "action":
        raise click.ClickException("expected an existing explicit action message")
    sender = source_identity(from_topic="redeliver existing parent action")
    if _native(str(sender.get("trace_id") or "")) != _native(str(original.get("source", {}).get("trace_id") or "")):
        raise click.ClickException("only the original sender may redeliver this action")
    pty = str(original.get("target", {}).get("pty_id") or "")
    if not pty:
        raise click.ClickException("the original message has no exact PTY target")
    _emit(_post_input(pty, original["envelope"]), True)


@cmd_coordination.command("sweep-wakes")
@click.option("--deliver", is_flag=True, help="真实重投未送达的终态唤醒(调用 control-plane send_agent_input)。默认关闭。")
@click.option(
    "--user-authorized",
    is_flag=True,
    help="确认用户已显式授权真实重投；--deliver 时必需(重投会触发对父会话的 send_agent_input)。",
)
@click.option("--target", "target_session_id", default="", help="仅扫这个精确 parent session；默认全部。")
@click.option("--limit", type=click.IntRange(1, 500), default=100, show_default=True)
@click.option("--min-age", "min_age_seconds", type=float, default=None, help="覆盖 min_age 秒；默认用模块策略值。")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_coordination_sweep_wakes(
    deliver: bool,
    user_authorized: bool,
    target_session_id: str,
    limit: int,
    min_age_seconds: float | None,
    as_json: bool,
) -> None:
    """扫描仍 pending、未 claim 的子会话终态唤醒。

    默认 dry-run：只读发现 + 纯函数分桶(eligible/skipped_young/skipped_backoff/
    synthetic_fixture/exhausted)，既不 claim 也不发送，可安全挂到 cron/心跳做可观测面
    与 exhausted 告警。`synthetic_fixture` 显式隔离 cli-* 冒烟占位绑定：dry-run 只读地
    把它们单列出来(不隐藏、仍计入 scanned)，且真实重投永不对其投递，除非其父确实是一个
    live 原生绑定。真实重投必须显式同时给 `--deliver --user-authorized`，才绑定
    control-plane 发送器并走既有 claim CAS + 退避；届时 cli-* 行仅在父命中 live 原生会话时才合格。
    """
    from datetime import datetime, timezone

    from omnicompany.core.terminal_wake_sweep import (
        DEFAULT_MAX_ATTEMPTS,
        DEFAULT_MIN_AGE_SECONDS,
        classify_wakes,
        run_terminal_wake_sweep,
    )

    policy: dict[str, Any] = {"target_session_id": target_session_id, "limit": limit}
    if min_age_seconds is not None:
        policy["min_age_seconds"] = min_age_seconds
    try:
        store = _store()
        if deliver:
            if not user_authorized:
                raise click.UsageError("--deliver 需要 --user-authorized(真实重投会触发 send_agent_input)")
            report = run_terminal_wake_sweep(store, **policy)
            report = {"ok": True, "mode": "deliver", **report}
        else:
            now = datetime.now(timezone.utc)
            discovered = store.pending_terminal_wakes(target_session_id=target_session_id, limit=limit)
            buckets = classify_wakes(
                discovered,
                now=now,
                min_age_seconds=DEFAULT_MIN_AGE_SECONDS if min_age_seconds is None else min_age_seconds,
                max_attempts=DEFAULT_MAX_ATTEMPTS,
            )
            report = {
                "ok": True,
                "mode": "dry-run",
                "scanned": len(discovered),
                "eligible": [str(r.get("message_id") or "") for r in buckets["eligible"]],
                "skipped_young": [str(r.get("message_id") or "") for r in buckets["skipped_young"]],
                "skipped_backoff": [str(r.get("message_id") or "") for r in buckets["skipped_backoff"]],
                "synthetic_fixture": [str(r.get("message_id") or "") for r in buckets["synthetic_fixture"]],
                "exhausted": [str(r.get("message_id") or "") for r in buckets["exhausted"]],
                "observed_at": now.isoformat(),
            }
    except click.ClickException:
        raise
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _error(exc, as_json)
        return
    _emit(report, as_json)


@cmd_coordination.command("child-terminal")
@click.option("--child", "child_session_id", required=True)
@click.option("--parent", "parent_session_id", required=True)
@click.option("--task", "parent_task_id", required=True)
@click.option("--run", "run_id", required=True)
@click.option("--outcome", required=True, help="completed / blocked / failed 等终态")
@click.option("--payload", default=None, help="附加 JSON object")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_coordination_child_terminal(
    child_session_id: str,
    parent_session_id: str,
    parent_task_id: str,
    run_id: str,
    outcome: str,
    payload: str | None,
    as_json: bool,
) -> None:
    """写入 parent-only child terminal 事件；不会默认唤醒 parent。"""
    try:
        result = _store().record_child_terminal(
            child_session_id=child_session_id,
            parent_session_id=parent_session_id,
            parent_task_id=parent_task_id,
            run_id=run_id,
            outcome=outcome,
            payload=_payload(payload),
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _error(exc, as_json)
        return
    _emit({"ok": True, **result}, as_json)


@cmd_coordination.command("blocker")
@click.option("--source", "source_session_id", required=True)
@click.option("--target", "target_session_id", required=True)
@click.option("--task", "parent_task_id", required=True)
@click.option("--run", "run_id", required=True)
@click.option("--reason", required=True)
@click.option("--priority", type=click.Choice(["low", "normal", "high", "critical"]), default="normal", show_default=True)
@click.option("--source-project", default="")
@click.option("--target-project", default="")
@click.option("--payload", default=None, help="附加 JSON object")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_coordination_blocker(
    source_session_id: str,
    target_session_id: str,
    parent_task_id: str,
    run_id: str,
    reason: str,
    priority: str,
    source_project: str,
    target_project: str,
    payload: str | None,
    as_json: bool,
) -> None:
    """写入有限优先级 blocker；默认不唤醒接收方。"""
    try:
        result = _store().record_blocker(
            source_session_id=source_session_id,
            target_session_id=target_session_id,
            parent_task_id=parent_task_id,
            run_id=run_id,
            reason=reason,
            priority=priority,
            source_project=source_project,
            target_project=target_project,
            payload=_payload(payload),
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _error(exc, as_json)
        return
    _emit({"ok": True, **result}, as_json)


@cmd_coordination.group("goal")
def cmd_coordination_goal() -> None:
    """独立于协调收件箱的人类原始目标账本。"""


@cmd_coordination_goal.command("get")
@click.argument("session_id")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_coordination_goal_get(session_id: str, as_json: bool) -> None:
    """读取 original_goal/current_goal。"""
    try:
        result = _store().get_user_goal(session_id)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _error(exc, as_json)
        return
    if result is None:
        _error(ValueError(f"未找到 session 的原始目标: {session_id}"), as_json)
        return
    _emit({"ok": True, "goal": result}, as_json)


@cmd_coordination_goal.command("record")
@click.argument("session_id")
@click.option("--text", "goal_text", required=True, help="真实人类目标")
@click.option("--source", default="human", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_coordination_goal_record(session_id: str, goal_text: str, source: str, as_json: bool) -> None:
    """记录真实 user prompt；agent 信封不会走这个入口。"""
    try:
        result = _store().record_user_goal(session_id, goal_text, source=source)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _error(exc, as_json)
        return
    _emit({"ok": True, **result}, as_json)


@cmd_coordination_goal.command("fork")
@click.argument("source_session_id")
@click.argument("target_session_id")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_coordination_goal_fork(source_session_id: str, target_session_id: str, as_json: bool) -> None:
    """复制目标历史到 fork；不复制收件箱、订阅或 wake 权限。"""
    try:
        result = _store().fork_goal(source_session_id, target_session_id)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _error(exc, as_json)
        return
    if result is None:
        _error(ValueError(f"未找到源 session 的目标: {source_session_id}"), as_json)
        return
    _emit({"ok": True, "goal": result}, as_json)


__all__ = ["cmd_coordination"]
