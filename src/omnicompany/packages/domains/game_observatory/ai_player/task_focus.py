from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .sanguo_daily_continuity import SANGUO_GAME_ID, SanguoDailyContinuityLedger
from .store import AIPlayerStore


OPEN_DEVICE_ACTION_GATES = frozenset(
    {
        "not_applicable",
        "open",
        "open_post_seal",
        "open_post_continuity",
    }
)


@dataclass(frozen=True)
class AuthoritativeTaskFocus:
    """Execution focus derived from a canonical programme ledger, when one exists."""

    preferred_task_ids: tuple[str, ...] = ()
    deferred_task_ids: tuple[str, ...] = ()
    restrict_to_preferred: bool = False
    basis: str = "task_board"
    continuity_schedule: dict[str, Any] | None = None
    daily_generated_task_ids: tuple[str, ...] = ()
    daily_generated_task_basis: dict[str, Any] | None = None
    daily_generated_task_gate: str = "not_applicable"
    daily_generated_task_gate_reason: str = "当前没有次日生成任务。"
    device_action_gate: str = "not_applicable"
    device_action_gate_reason: str = "当前环境没有自然日连续经营门。"


def resolve_authoritative_task_focus(
    player: AIPlayerStore,
    *,
    environment_id: str,
) -> AuthoritativeTaskFocus:
    """Resolve the latest ledger-owned task focus without touching a device.

    Raw task scores remain useful for generic exploration.  Once a continuity
    ledger explicitly names the next task, however, silently falling back to an
    older high-score task would violate the programme order.  This projection is
    therefore both a preference and a restriction for that execution context.
    """

    environment = player.get_environment(environment_id)
    if environment is None:
        raise KeyError(f"unknown AI-player environment: {environment_id}")
    game_ids = {
        getattr(environment, "game_id", None),
        *getattr(environment, "game_id_aliases", ()),
    }
    if SANGUO_GAME_ID not in game_ids:
        return AuthoritativeTaskFocus()

    ledger = SanguoDailyContinuityLedger(player)
    run_ids = ledger.list_run_ids(environment_id)
    if not run_ids:
        return AuthoritativeTaskFocus(
            basis="sanguo_continuity_not_started",
            device_action_gate="not_started",
            device_action_gate_reason="尚未建立自然日连续经营批次。",
        )

    continuity_run_id = run_ids[0]
    schedule = ledger.schedule(environment_id, continuity_run_id)
    schedule_payload = schedule.model_dump(mode="json", by_alias=True)
    generated_task_ids: tuple[str, ...] = ()
    generated_natural_day = None
    generated_basis: dict[str, Any] | None = None
    for day in reversed(ledger.list_days(environment_id, continuity_run_id)):
        generated = next(
            (
                duty
                for duty in reversed(day.duties)
                if duty.duty == "next_day_task_generation"
            ),
            None,
        )
        if generated is None:
            continue
        generated_task_ids = tuple(item.id for item in generated.task_snapshots)
        generated_natural_day = day.natural_day
        generated_basis = {
            "continuity_run_id": continuity_run_id,
            "natural_day": day.natural_day.isoformat(),
            "day_index": day.day_index,
            "duty": generated.duty,
            "completed_at": generated.completed_at,
        }
        break

    generated_task_is_due = bool(
        generated_task_ids
        and generated_natural_day is not None
        and generated_natural_day < schedule.natural_day
    )
    preferred_task_ids = generated_task_ids if generated_task_is_due else ()
    deferred_task_ids = generated_task_ids if generated_task_ids and not generated_task_is_due else ()
    if generated_task_is_due:
        generated_task_gate = "eligible"
        generated_task_gate_reason = "次日生成任务已进入目标自然日，可以作为权威执行焦点。"
    elif generated_task_ids:
        generated_task_gate = "deferred_until_next_natural_day"
        generated_task_gate_reason = (
            "任务由本自然日的次日生成职责产出；保留在任务板中，但本自然日不得提前执行。"
        )
    else:
        generated_task_gate = "not_applicable"
        generated_task_gate_reason = "当前没有次日生成任务。"

    if schedule.status in {"not_started", "in_progress", "interrupted"}:
        device_action_gate = "open"
        gate_reason = "当前自然日可以开始或继续。"
    elif schedule.status == "ready_to_seal":
        device_action_gate = "seal_only"
        gate_reason = "当日六项职责均已记录；继续设备工作前先封账。"
    elif schedule.status == "sealed":
        device_action_gate = "open_post_seal"
        gate_reason = (
            "当日六项职责已经封账；开放世界玩家推进仍可继续，"
            "但次日生成任务保持延期。"
        )
    elif schedule.status == "completed":
        device_action_gate = "open_post_continuity"
        gate_reason = "七日连续经营已经完成；开放世界玩家推进不受该账本阻断。"
    else:
        device_action_gate = "blocked"
        gate_reason = "; ".join(schedule.reasons)

    if preferred_task_ids:
        basis = "daily_next_day_task_generation"
    elif schedule.status == "sealed":
        basis = "sanguo_post_seal_task_board"
    elif schedule.status == "completed":
        basis = "sanguo_post_continuity_task_board"
    else:
        basis = "task_board"

    return AuthoritativeTaskFocus(
        preferred_task_ids=preferred_task_ids,
        deferred_task_ids=deferred_task_ids,
        restrict_to_preferred=bool(preferred_task_ids),
        basis=basis,
        continuity_schedule=schedule_payload,
        daily_generated_task_ids=generated_task_ids,
        daily_generated_task_basis=generated_basis,
        daily_generated_task_gate=generated_task_gate,
        daily_generated_task_gate_reason=generated_task_gate_reason,
        device_action_gate=device_action_gate,
        device_action_gate_reason=gate_reason,
    )


__all__ = [
    "AuthoritativeTaskFocus",
    "OPEN_DEVICE_ACTION_GATES",
    "resolve_authoritative_task_focus",
]
