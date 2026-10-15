# [OMNI] origin=codex domain=dashboard/ccdaemon/hooks ts=2026-08-09 type=infra status=active
"""Official Codex SubagentStop/Stop bridge for managed task completion."""
from __future__ import annotations

import json
from typing import Any

from . import _shared as sh
from omnicompany.packages.services._core.lifecycle.managed_agent_completion import (
    ManagedAgentCompletion,
    ManagedAgentCompletionError,
)


def _value(payload: dict[str, Any], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _parent(payload: dict[str, Any]) -> str:
    return _value(payload, "parent_session_id", "parentSessionId", "session_id", "sessionId")


def _worker(payload: dict[str, Any]) -> str:
    return _value(payload, "agent_id", "agentId", "subagent_id", "subagentId", "thread_id", "threadId")


def _task(payload: dict[str, Any]) -> str | None:
    return _value(payload, "task_id", "taskId") or None


def _emit_empty() -> None:
    print(json.dumps({}))


def _record_quiet_terminal(record: dict[str, Any] | None, *, outcome: str) -> None:
    """Project an existing completion result into the pull-only parent inbox."""
    if not record:
        return
    parent = record.get("parent") or {}
    worker = record.get("worker") or {}
    parent_session_id = _value(parent, "session_id", "sessionId")
    child_session_id = _value(worker, "session_id", "sessionId")
    if not (parent_session_id and child_session_id):
        return
    try:
        from omnicompany.core.quiet_coordination import QuietCoordinationStore

        # The managed arm is the stable run generation when the provider hook
        # does not expose a native run UUID.  It makes repeated Stop delivery
        # idempotent while preserving exact child/parent/task ownership.
        run_id = _value(record, "run_id", "runId") or str(record.get("arm_id") or "")
        if not run_id:
            return
        store = QuietCoordinationStore()
        terminal = store.record_child_terminal(
            child_session_id=child_session_id,
            parent_session_id=parent_session_id,
            parent_task_id=str(record.get("task_id") or ""),
            run_id=run_id,
            outcome=outcome,
            payload={
                "arm_id": record.get("arm_id"),
                "plan_id": record.get("plan_id"),
                "state": record.get("state"),
                "display_status": record.get("display_status"),
                "failure": record.get("failure"),
            },
        )
        # The quiet inbox remains the durable source of truth, but a parent
        # that is waiting must also receive one real input event.  Claiming
        # the token before delivery makes repeated Stop/SubagentStop hook
        # calls harmless.  This path deliberately uses the existing agent
        # control-plane input endpoint; it does not invent another transport.
        terminal_message = terminal.get("message") if isinstance(terminal, dict) else None
        message_id = str((terminal_message or {}).get("message_id") or "")
        if message_id and store.claim_wake(message_id):
            try:
                from omnicompany.packages.services._core.agent.control_plane import send_agent_input

                outcome_text = str(outcome or "terminal")
                delivery = send_agent_input(
                    parent_session_id,
                    (
                        "[OMNI CHILD TERMINAL WAKE] 子会话已结束，协调收件箱已有一条待处理回执。"
                        f" child={child_session_id} outcome={outcome_text} message_id={message_id}。"
                        " 请立即读取该回执并继续当前计划；不要等待下一次人工检查。"
                    ),
                    submit=True,
                )
                if isinstance(delivery, dict) and delivery.get("ok") is False:
                    raise RuntimeError(f"parent wake endpoint rejected delivery: {delivery}")
                sh.append_audit(
                    "quiet_coordination_parent_wake_delivered",
                    {"message_id": message_id, "parent_session_id": parent_session_id},
                )
            except Exception as exc:  # noqa: BLE001 - durable record must survive transport failure
                store.release_wake(message_id)
                sh.append_audit(
                    "quiet_coordination_parent_wake_error",
                    {"message_id": message_id, "parent_session_id": parent_session_id, "error": str(exc)},
                )
    except Exception as exc:  # noqa: BLE001 - legacy completion result remains authoritative
        sh.append_audit("quiet_coordination_terminal_error", {"error": str(exc)})


def handle_subagent_stop(payload: dict[str, Any]) -> int:
    manager = ManagedAgentCompletion()
    try:
        record = manager.subagent_stop(
            parent_session_id=_parent(payload),
            worker_session_id=_worker(payload),
            task_id=_task(payload),
            changelist=_value(payload, "changelist", "change", "cl") or None,
        )
        _record_quiet_terminal(record, outcome="needs_validation")
    except ManagedAgentCompletionError as exc:
        sh.append_audit("managed_agent_completion_hook_error", exc.to_dict())
    _emit_empty()
    return 0


def handle_stop(payload: dict[str, Any]) -> int:
    manager = ManagedAgentCompletion()
    try:
        records = manager.stop(parent_session_id=_parent(payload))
        for record in records:
            _record_quiet_terminal(record, outcome="failed")
        manager.expire()
    except ManagedAgentCompletionError as exc:
        sh.append_audit("managed_agent_completion_hook_error", exc.to_dict())
    _emit_empty()
    return 0


def main() -> int:
    payload = sh.read_stdin_json()
    event = _value(payload, "hook_event_name", "hookEventName", "event", "event_name")
    if event == "SubagentStop":
        return handle_subagent_stop(payload)
    if event == "Stop":
        return handle_stop(payload)
    sh.append_audit("managed_agent_completion_hook_ignored", {"event": event})
    _emit_empty()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
