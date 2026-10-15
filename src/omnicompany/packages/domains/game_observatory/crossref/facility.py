"""Facility-side counts derived from the F6-04 fact families.

These four families say nothing about a configuration row, so none of them
belongs in the field ledger: what a guard claim moved, where the deliverable
order was drawn, how long a save read took stage by stage, and which guards and
stop reasons fired.  They are counted here and reported beside the ledger, and
the dashboard reads the same counts out of ``summary.v1.json``.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Iterable

from ..delegation.exhaustion import CONTINUING_STOP_REASONS, SEGMENT_STOP_TAXONOMY
from .evidence_index import (
    CLAIM_ACTION_KINDS,
    DISPLAY_ORDER_CANDIDATES,
    NON_GRANTING_ACTION_KINDS,
    FactV1,
)
from .rules import CLAIM_DECLARING_TABLES

# The segment stop reasons the driver knows how to classify.  A reason outside
# this set is reported rather than mapped: `classify_segment_stop` falls back to
# `blocked_unknown_surface` for anything it does not know, which would send an
# operator looking for a surface to register.
DECLARED_SEGMENT_STOP_REASONS = set(CONTINUING_STOP_REASONS) | set(
    SEGMENT_STOP_TAXONOMY
)

# The save-read stages the dashboard charts.  ``total_s`` and ``flush_s`` are
# seconds; the rest are milliseconds, as ``SaveReadTimingV1`` records them.
TIMING_STAGES = (
    "flush_s",
    "pull_ms",
    "decrypt_ms",
    "decode_ms",
    "cleanup_ms",
    "total_s",
    "pull_adb_calls",
    "flush_adb_calls",
)


def _bump(bucket: dict[str, int], key: Any) -> None:
    name = str(key)
    bucket[name] = bucket.get(name, 0) + 1


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "p50": None, "min": None, "max": None}
    return {
        "n": len(values),
        "p50": round(float(median(values)), 6),
        "min": round(float(min(values)), 6),
        "max": round(float(max(values)), 6),
    }


def _claim_group_key(fact: FactV1) -> str:
    return str(fact.before.get("group_id") or fact.before.get("action_id") or fact.fact_id)


def _free_claim_view(facts: list[FactV1]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    claims = [fact for fact in facts if fact.kind == "free_claim_delta"]
    by_screen: dict[str, dict[str, Any]] = {}
    measured = 0
    for fact in claims:
        screen = str(fact.before.get("screen") or "")
        entry = by_screen.setdefault(
            screen, {"actions": 0, "measured": 0, "kinds": {}, "declaration": ""}
        )
        entry["actions"] += 1
        _bump(entry["kinds"], fact.before.get("action_kind"))
        if fact.before.get("measured"):
            entry["measured"] += 1
            measured += 1
        entry["declaration"] = (
            f"config:{CLAIM_DECLARING_TABLES[screen]}"
            if screen in CLAIM_DECLARING_TABLES
            else "code/server-declared"
        )
    groups: dict[str, list[FactV1]] = {}
    for fact in claims:
        if fact.before.get("measured"):
            groups.setdefault(_claim_group_key(fact), []).append(fact)
    attributable = 0
    granted: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda item: item.fact_id)
        head = members[0]
        is_attributable = bool(head.before.get("attributable"))
        attributable += int(is_attributable)
        if is_attributable:
            for field, value in head.delta.items():
                if isinstance(value, (int, float)) and field != "level":
                    granted[field] = granted.get(field, 0) + int(value)
        rows.append(
            {
                "group_id": key,
                "screens": sorted({str(item.before.get("screen")) for item in members}),
                "kinds": sorted({str(item.before.get("action_kind")) for item in members}),
                "elements": sorted(
                    {str(item.before.get("element") or "") for item in members}
                ),
                "actions": len(members),
                "window": head.before.get("window"),
                "attributable": is_attributable,
                "guard_actions_in_window": head.before.get("guard_actions_in_window")
                or [],
                "delta": {
                    field: value
                    for field, value in sorted(head.delta.items())
                    if isinstance(value, (int, float))
                },
                "board_items_delta": head.after.get("board_items_delta") or {},
                "fact_ids": [item.fact_id for item in members],
            }
        )
    view = {
        "actions": len(claims),
        "actions_with_measurement": measured,
        "measurement_groups": len(groups),
        "attributable_groups": attributable,
        "unattributable_groups": len(groups) - attributable,
        "granted_totals_over_attributable_groups": dict(sorted(granted.items())),
        "action_kinds_tracked": sorted(CLAIM_ACTION_KINDS),
        "non_granting_kinds": sorted(NON_GRANTING_ACTION_KINDS),
        "by_screen": dict(sorted(by_screen.items())),
    }
    return view, rows


def _order_display_view(
    facts: list[FactV1],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    samples = [fact for fact in facts if fact.kind == "order_display_rank"]
    with_positions = [
        fact for fact in samples if fact.after.get("button_display_positions") is not None
    ]
    judged = [
        fact for fact in with_positions if fact.after.get("deliverable_leftmost") is not None
    ]
    hits = sum(1 for fact in judged if fact.after["deliverable_leftmost"])
    candidates = {
        name: {"hits": 0, "misses": 0, "unjudged": 0} for name in DISPLAY_ORDER_CANDIDATES
    }
    for fact in samples:
        for name, entry in (fact.after.get("candidates") or {}).items():
            bucket = candidates.setdefault(
                name, {"hits": 0, "misses": 0, "unjudged": 0}
            )
            hit = entry.get("hit")
            if hit is None:
                bucket["unjudged"] += 1
            elif hit:
                bucket["hits"] += 1
            else:
                bucket["misses"] += 1
    alerts = [
        {
            "run": fact.before.get("run"),
            "round": fact.before.get("round"),
            "frame_sha256": fact.before.get("frame_sha256"),
            "button_display_positions": fact.after.get("button_display_positions"),
            "fulfillable_slots": fact.after.get("fulfillable_slots"),
            "fact_id": fact.fact_id,
        }
        for fact in with_positions
        if any(
            isinstance(position, int) and position >= 2
            for position in fact.after.get("button_display_positions") or []
        )
    ]
    view = {
        "samples": len(samples),
        "samples_with_button_positions": len(with_positions),
        "judged_samples": len(judged),
        "deliverable_leftmost_hits": hits,
        "deliverable_leftmost_misses": len(judged) - hits,
        "save_screen_disagreement_samples": sum(
            1
            for fact in with_positions
            if fact.after.get("save_and_screen_agree_on_count") is False
        ),
        "delivery_evidence_samples": sum(
            1 for fact in samples if fact.before.get("source") == "delivery-evidence"
        ),
        "candidates": {name: dict(value) for name, value in sorted(candidates.items())},
        "candidates_indistinguishable_samples": sum(
            1 for fact in samples if fact.after.get("candidates_agree")
        ),
        "button_at_display_position_2_or_3": len(alerts),
        "alerts": alerts[:20],
    }
    return view, alerts


def _save_read_timing_view(facts: list[FactV1]) -> dict[str, Any]:
    samples = [fact for fact in facts if fact.kind == "save_read_timing"]
    stages: dict[str, list[float]] = {name: [] for name in TIMING_STAGES}
    flags: dict[str, dict[str, int]] = {
        "pull_fast_path": {},
        "derived_key_cached": {},
        "flush_profile": {},
        "flush_focus_command": {},
    }
    for fact in samples:
        payload = fact.after
        for name in TIMING_STAGES:
            value = payload.get(name)
            if isinstance(value, (int, float)):
                stages[name].append(float(value))
        _bump(flags["pull_fast_path"], payload.get("pull_fast_path"))
        _bump(flags["derived_key_cached"], payload.get("derived_key_cached"))
        _bump(flags["flush_profile"], fact.before.get("flush_profile"))
        _bump(flags["flush_focus_command"], payload.get("flush_focus_command") or "")
    return {
        "samples": len(samples),
        "stages": {name: _distribution(values) for name, values in stages.items()},
        "flags": {name: dict(sorted(value.items())) for name, value in flags.items()},
        "runs": sorted({str(fact.before.get("run_id")) for fact in samples}),
    }


def _guard_events_view(facts: list[FactV1]) -> dict[str, Any]:
    events = [fact for fact in facts if fact.kind == "guard_event"]
    by_event: dict[str, int] = {}
    guard_by_screen: dict[str, int] = {}
    guard_by_kind: dict[str, int] = {}
    segment_stops: dict[str, int] = {}
    driver_stops: dict[str, int] = {}
    driver_taxonomy: dict[str, int] = {}
    refusals_by_screen: dict[str, int] = {}
    states: dict[str, dict[str, int]] = {}
    violations: list[dict[str, Any]] = []
    for fact in events:
        detail = fact.before
        event = str(detail.get("event") or "")
        _bump(by_event, event)
        if event == "guard_action":
            _bump(guard_by_screen, detail.get("screen"))
            _bump(guard_by_kind, detail.get("action_kind"))
        elif event == "segment_stop":
            _bump(segment_stops, detail.get("reason"))
        elif event == "driver_stop":
            _bump(driver_stops, detail.get("reason"))
            for reason, count in (detail.get("taxonomy") or {}).items():
                if isinstance(count, int):
                    driver_taxonomy[str(reason)] = (
                        driver_taxonomy.get(str(reason), 0) + count
                    )
        elif event == "pre_frame_refusal":
            _bump(refusals_by_screen, detail.get("screen"))
        elif event == "guard_state":
            entry = states.setdefault(
                str(detail.get("state")), {"entered": 0, "left": 0}
            )
            entry[str(detail.get("transition") or "entered")] = (
                entry.get(str(detail.get("transition") or "entered"), 0) + 1
            )
        elif event == "redline_violation":
            violations.append(
                {
                    # The earliest audits recorded only the screen they
                    # identified; the forbidden-surface field came later.
                    "screen": detail.get("screen"),
                    "forbidden_surface": detail.get("forbidden_surface")
                    or detail.get("screen"),
                    "intent": detail.get("intent"),
                    "action_id": detail.get("action_id"),
                    "source_report": fact.source_report,
                    "fact_id": fact.fact_id,
                }
            )
    return {
        "total": len(events),
        "by_event": dict(sorted(by_event.items())),
        "guard_actions_by_screen": dict(sorted(guard_by_screen.items())),
        "guard_actions_by_kind": dict(sorted(guard_by_kind.items())),
        "segment_stop_reasons": dict(sorted(segment_stops.items())),
        "segment_stop_reasons_outside_vocabulary": {
            reason: count
            for reason, count in sorted(segment_stops.items())
            if reason not in DECLARED_SEGMENT_STOP_REASONS
        },
        "driver_stop_reasons": dict(sorted(driver_stops.items())),
        "driver_stop_taxonomy": dict(sorted(driver_taxonomy.items())),
        "pre_frame_refusals_by_screen": dict(sorted(refusals_by_screen.items())),
        "guard_states": {name: dict(sorted(value.items())) for name, value in sorted(states.items())},
        "redline_violations": sorted(
            violations, key=lambda item: str(item.get("action_id"))
        ),
    }


def _trigger_plane_view(facts: list[FactV1]) -> dict[str, Any]:
    """The energy-party activity as the save time series shows it.

    F7-04 section 1.1.  A day where the score never moved is a real reading and
    is reported as one: the sample count for the score claim is zero and the
    daily status says why, rather than a rate computed over nothing.
    """

    rounds = [fact for fact in facts if fact.kind == "trigger_plane_round"]
    scored = [
        fact
        for fact in rounds
        if isinstance(fact.delta.get("score"), (int, float))
        and fact.delta["score"] > 0
    ]
    daily_status: dict[str, int] = {}
    max_out: dict[str, int] = {}
    consumed_total = 0
    cost_total = 0
    for fact in rounds:
        _bump(daily_status, fact.after.get("dailyStatus"))
        _bump(max_out, fact.after.get("isMaxOut"))
        value = fact.delta.get("energyConsumed")
        cost = fact.delta.get("expected_generate_cost")
        if isinstance(value, (int, float)):
            consumed_total += value
        if isinstance(cost, (int, float)):
            cost_total += cost
    levels = [
        fact.after.get("level")
        for fact in rounds
        if isinstance(fact.after.get("level"), int)
    ]
    return {
        "round_samples": len(rounds),
        "score_samples": len(scored),
        "reward_samples": sum(
            1 for fact in facts if fact.kind == "trigger_plane_reward"
        ),
        "daily_status": dict(sorted(daily_status.items())),
        "is_max_out": dict(sorted(max_out.items())),
        "level_range": [min(levels), max(levels)] if levels else [],
        "energy_consumed_total": consumed_total,
        "expected_generate_cost_total": cost_total,
    }


def build_facility_view(facts: Iterable[FactV1]) -> dict[str, Any]:
    """Aggregate the four facility fact families into report and summary blocks."""

    facts = list(facts)
    free_claim, free_claim_rows = _free_claim_view(facts)
    order_display, order_alerts = _order_display_view(facts)
    # F7-04 sections 1.3 to 1.5.  Three families that count rather than judge:
    # story advances, the merges an order reserve refused, and the relaxed
    # generation verdict.  They belong beside the ledger for the same reason
    # the F6-04 families do -- none of them settles a configuration field.
    from .rules import (
        generate_deviation_observation,
        order_reserve_observation,
        story_advance_observation,
    )

    return {
        "facility_facts": {
            "free_claim": free_claim,
            "order_display_rank": order_display,
            "save_read_timing": _save_read_timing_view(facts),
            "story_advance": story_advance_observation(facts),
            "order_reserve": order_reserve_observation(facts),
            "generate_deviation": generate_deviation_observation(facts),
            "trigger_plane": _trigger_plane_view(facts),
        },
        "guard_events": _guard_events_view(facts),
        "details": {
            "free_claim_rows": free_claim_rows,
            "order_display_alerts": order_alerts,
        },
    }


__all__ = ["TIMING_STAGES", "build_facility_view"]
