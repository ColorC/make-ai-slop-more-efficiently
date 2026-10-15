"""Aggregate the per-run rows into the six dashboard views plan.md asks for."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any

from ..replay.quota import QuotaManager
from .action_validity import (
    ROUNDTRIP_SHARE_RED_LINE,
    STATE_CHANGE_PER_HOUR_GATE,
    STATE_CHANGE_PER_HOUR_GATE_SOURCE,
)
from .drives import (
    ADVANCE_GATE_ENERGY_SHARE,
    RESUME_GATE_RATE,
    STOP_REASONS,
    advance_gate,
    build_drives,
    energy_ceiling,
    pool_drives,
)
from .free_claims import build_free_claim_audit
from .guardian import DAILY_EFFECTIVE_HOURS_GATE, build_guardian
from .progress import build_progress
from .runs import (
    APM_WINDOW_MIN_ENERGY,
    APM_WINDOW_MIN_ROUNDS,
    LAYER_NAMES,
    layer_seconds,
    percentile,
)
from .save_read import STAGES, build_save_read_trend
from .sources import Inputs, write_if_changed

DASHBOARD_SCHEMA = "omnicompany.game-observatory.rr-run-metrics-dashboard.v1"

# The quota the replay store enforces; read from the store rather than restated
# so the dashboard cannot drift away from the limit that is actually applied.
REPLAY_QUOTA_BYTES = int(
    inspect.signature(QuotaManager.__init__).parameters["limit_bytes"].default
)

GATE_APM = 40.0
GATE_REFUTED_RATE = 0.05
RECENT_RUNS = 3

# F7-04 section 2.2.  The guard-event families the ledger records.  Naming them
# here rather than reading whichever keys happen to be present is what makes a
# family that never fired print as zero instead of vanishing from the table.
GUARD_EVENT_FAMILIES = (
    "guard_action",
    "pre_frame_refusal",
    "guard_state",
    "redline_violation",
    "segment_stop",
    "driver_stop",
)

# plan.md 第二节 F7 的旧设施基线，逐字引用，右侧一列由本仪表推导。
BASELINES: tuple[tuple[str, str, str], ...] = (
    ("旧设施 · 高重复链", "有效 APM", "20–25"),
    ("旧设施 · 半固定链", "有效 APM", "5"),
    ("旧设施 · 每动作 LLM 介入", "占比", "100%"),
)


# F7-05 section 3 item 4.  The segments the spec names for the old-vs-new
# comparison table, by the drive that played them: the healthiest segment on
# record (the 2026-09-07 02:46Z longrun3 restart's segment A), the F4-11
# guardian's S1 drive in full, and both F4-08 A/B arms.  The original 09-06
# longrun3 drive rides along as the known idle reference: its refuted_streak
# segments are what the new metric has to separate from the healthy ones.
COMPARISON_DRIVES: tuple[tuple[str, str], ...] = (
    ("F4-09-longrun3-20260907T024620Z", "09-07 02:46Z longrun3 重启（点名最健康段 A）"),
    ("F4-08-longrun3-20260906T205301Z", "09-06 longrun3 原始驱动（含 refuted_streak 空转段）"),
    ("F4-11-long-S1-20260907T090557Z", "F4-11 守护 S1 全部段"),
    ("F4-08-armA-20260906T185637Z", "09-06 F4-08 A/B · 臂 A"),
    ("F4-08-armB-20260906T193145Z", "09-06 F4-08 A/B · 臂 B"),
)


def _sum(rows: list[dict[str, Any]], *path: str) -> int:
    total = 0
    for row in rows:
        value: Any = row
        for key in path:
            value = (value or {}).get(key) if isinstance(value, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += int(value)
    return total


def _sum_float(rows: list[dict[str, Any]], *path: str) -> float:
    """Same walk as ``_sum`` but keeps the fractional part of a duration."""

    total = 0.0
    for row in rows:
        value: Any = row
        for key in path:
            value = (value or {}).get(key) if isinstance(value, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value)
    return total


def _has_actions(row: dict[str, Any]) -> bool:
    actions = (row.get("business") or {}).get("game_actions")
    return isinstance(actions, (int, float)) and actions > 0


def build_overview(rows: list[dict[str, Any]]) -> dict[str, Any]:
    played = [row for row in rows if _has_actions(row)]
    return {
        "runs": len(rows),
        "runs_with_actions": len(played),
        "game_actions": _sum(rows, "business", "game_actions"),
        "rounds": _sum(rows, "identity", "rounds"),
        "confirmed_deliveries": _sum(rows, "business", "confirmed_deliveries"),
        "main_tasks_completed": _sum(rows, "business", "main_tasks_completed"),
        # F7-05: the same wall clock read in the two-way taxonomy.
        "state_change_actions": _sum(rows, "action_validity", "state_change_actions"),
        "interface_roundtrip_actions": _sum(
            rows, "action_validity", "interface_roundtrip_actions"
        ),
        "frames_written": _sum(rows, "data", "frames_written"),
        "frames_lost": _sum(rows, "data", "frames_lost"),
        "redline_violations": _sum(rows, "safety", "redline_violations"),
        "forbidden_surface_pre_frames": _sum(rows, "safety", "forbidden_surface_pre_frames"),
        "unknown_screen_stops": sum(
            1 for row in rows if (row.get("safety") or {}).get("unknown_screen_stop") is True
        ),
        "run_dir_bytes": _sum(rows, "data", "run_dir_bytes"),
        "wall_seconds": _sum_float(rows, "identity", "wall_seconds"),
        "per_hour": _pooled_rates(rows),
    }


def _pooled_rates(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    """Rates over the summed wall clock, not the mean of per-run rates.

    A run that lasted four minutes would otherwise extrapolate its single main
    task into a double-digit hourly figure and drown the long runs out.
    """

    wall = _sum_float(rows, "identity", "wall_seconds")
    if wall <= 0:
        return None
    hours = wall / 3600.0
    coins = sum(
        value
        for row in rows
        for value in [((row.get("business") or {}).get("coins") or {}).get("delta")]
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    )
    return {
        "game_actions": _sum(rows, "business", "game_actions") / hours,
        "state_change_actions": _sum(rows, "action_validity", "state_change_actions")
        / hours,
        "main_tasks": _sum(rows, "business", "main_tasks_completed") / hours,
        "confirmed_deliveries": _sum(rows, "business", "confirmed_deliveries") / hours,
        "coins_delta": coins / hours,
        "replay_mb": _sum(rows, "data", "run_dir_bytes") / 2**20 / hours,
    }


def build_timeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline = []
    for row in rows:
        efficiency = row.get("efficiency") or {}
        business = row.get("business") or {}
        identity = row.get("identity") or {}
        validity = row.get("action_validity") or {}
        wall = efficiency.get("round_wall_s") or {}
        timeline.append(
            {
                "run_id": row["run_id"],
                "task": identity.get("task") or identity.get("producer"),
                "started_at": identity.get("started_at"),
                "rounds": identity.get("rounds"),
                "stop_reason": identity.get("stop_reason"),
                "effective_apm": efficiency.get("effective_apm"),
                "state_change_per_hour": validity.get("state_change_per_hour"),
                "roundtrip_share": validity.get("roundtrip_share"),
                "refuted_rate": business.get("refuted_rate"),
                "deviation_rate": business.get("deviation_rate"),
                "round_p50_s": wall.get("p50"),
                "confirmed_deliveries": business.get("confirmed_deliveries"),
            }
        )
    return timeline


def build_bottleneck(inputs: Inputs) -> dict[str, Any]:
    """Pool every round of every run and rank the layers by median cost."""

    pooled: dict[str, list[float]] = {name: [] for name in LAYER_NAMES}
    for report in inputs.reports:
        if report.run_id is None:
            continue
        for name, values in layer_seconds(report).items():
            pooled[name].extend(values)
    medians = {
        name: percentile(values, 0.5) for name, values in pooled.items() if values
    }
    total = sum(value for value in medians.values() if value is not None)
    layers = {
        name: {
            "samples": len(pooled[name]),
            "p50_s": medians.get(name),
            "p95_s": percentile(pooled[name], 0.95) if pooled[name] else None,
            "share_of_median_round": (
                medians[name] / total if name in medians and total > 0 else None
            ),
        }
        for name in LAYER_NAMES
    }
    largest = max(medians, key=lambda name: medians[name]) if medians else None
    return {"layers": layers, "median_round_s": total or None, "largest_layer": largest}


def _gate_state(row: dict[str, Any]) -> dict[str, Any]:
    efficiency = row.get("efficiency") or {}
    business = row.get("business") or {}
    data = row.get("data") or {}
    safety = row.get("safety") or {}
    apm = efficiency.get("effective_apm")
    refuted = business.get("refuted_rate")
    lost = data.get("frames_lost")
    violations = safety.get("redline_violations")
    # A report may declare the run clear without listing violations; the
    # declaration still answers the gate even when the count is unknown.
    clear = safety.get("redline_clear")
    if clear is None and violations is not None:
        clear = violations == 0
    return {
        "run_id": row["run_id"],
        "task": (row.get("identity") or {}).get("task"),
        "apm_at_least_40": None if apm is None else apm >= GATE_APM,
        "effective_apm": apm,
        "refuted_below_5_percent": None if refuted is None else refuted < GATE_REFUTED_RATE,
        "refuted_rate": refuted,
        # F4-11 section 2.  Beside the refuted rate, never inside the gate: a
        # deviation is a confirmation the plan mis-shaped, and folding it into
        # a pass/fail would hide either the landing or the mis-shaping.
        "deviation_predictions": business.get("deviation_predictions"),
        "deviation_rate": business.get("deviation_rate"),
        "frames_lost_zero": None if lost is None else lost == 0,
        "frames_lost": lost,
        "redline_zero": clear,
        "redline_violations": violations,
    }


def _drive_gates(pooled: dict[str, Any]) -> list[dict[str, Any]]:
    """The two F7-02 gates, judged over every drive in the library.

    Both are conditional on the event having happened at all: a library with no
    input-death has nothing to say about the recovery rate, and saying "0 / 0
    met" would read as a failure it never had the chance to fail.
    """

    resume = pooled.get("energy_resume") or {}
    recovery = pooled.get("recovery") or {}
    judged = resume.get("confirmed", 0) + resume.get("refuted", 0)
    rate = resume.get("rate")
    attempts = recovery.get("segments_with_attempt", 0)
    recovered = recovery.get("segments_recovered", 0)
    gates = [
        {
            "gate": "energy_resume_first_generate_confirmed",
            "label": f"能量等待后续跑首个 generate confirmed 率 ≥ {int(RESUME_GATE_RATE * 100)}%",
            "value": rate,
            "considered": judged,
            "met": None if rate is None else rate >= RESUME_GATE_RATE,
            "note": (
                f"{resume.get('resumes', 0)} 次续跑中 {judged} 次真的做了生成；"
                f"另有 {resume.get('resumes_without_generate', 0)} 次整段没有生成动作，无从判定"
            ),
        }
    ]
    if attempts:
        gates.append(
            {
                "gate": "input_dead_recovery_success",
                "label": "input_dead 恢复成功率",
                "value": recovered / attempts,
                "considered": attempts,
                "met": recovered == attempts,
                "note": f"{recovered} / {attempts} 段重启输入后继续跑完",
            }
        )
    else:
        gates.append(
            {
                "gate": "input_dead_recovery_success",
                "label": "input_dead 恢复成功率",
                "value": None,
                "considered": 0,
                "met": None,
                "note": "全库没有一段触发过输入假死恢复梯，该门本次不适用",
            }
        )
    return gates


def build_apm_gate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The action-rate gate, over satiated segments only.

    F7-02 found the gate reading 0 / 3 while the account was energy-capped: the
    three most recent runs with actions were segments that woke, spent a point
    and went back to sleep, and their rate measured the recovery clock.  The
    window is therefore the runs that began with energy in hand and ran long
    enough to average, and the latest such run is reported beside the best one
    on record rather than instead of it.
    """

    window = [
        row
        for row in rows
        if ((row.get("efficiency") or {}).get("apm_window") or {}).get("qualifies")
        and isinstance((row.get("efficiency") or {}).get("effective_apm"), (int, float))
    ]

    def state(row: dict[str, Any]) -> dict[str, Any]:
        identity = row.get("identity") or {}
        efficiency = row.get("efficiency") or {}
        declared = (efficiency.get("apm_window") or {}).get("retest_declared_by")
        return {
            "run_id": row["run_id"],
            "task": identity.get("task"),
            "started_at": identity.get("started_at"),
            "rounds": identity.get("rounds"),
            "energy_start": ((row.get("business") or {}).get("energy") or {}).get("start"),
            "stop_reason": identity.get("stop_reason"),
            "effective_apm": efficiency.get("effective_apm"),
            "met": efficiency["effective_apm"] >= GATE_APM,
            "retest_declared_by": declared,
            "basis": (
                f"报告 {declared} 指名的正式复测段"
                if declared
                else (efficiency.get("apm_window") or {}).get("basis")
            ),
        }

    states = [state(row) for row in window]
    best = max(states, key=lambda item: item["effective_apm"]) if states else None
    # The formal retest is the segment an aggregate report says it ran to
    # measure the rate.  F7-03 puts the declaration first and the derived
    # conditions second, so once any report has declared one, that is what the
    # gate reports as its latest reading.  The newest segment that merely met
    # the derived conditions keeps its own row, so a slow one still shows.
    retests = [item for item in states if item["retest_declared_by"]]
    retest = retests[-1] if retests else None
    window_latest = states[-1] if states else None
    latest = retest or window_latest
    excluded = sum(1 for row in rows if _has_actions(row)) - len(window)
    excluded_legal_wait = sum(
        1
        for row in rows
        if _has_actions(row)
        and ((row.get("efficiency") or {}).get("apm_window") or {}).get("satiated_start")
        and ((row.get("efficiency") or {}).get("apm_window") or {}).get(
            "ended_on_legal_wait"
        )
    )
    return {
        "gate": "apm_full_energy_window",
        "label": f"满体力段有效 APM ≥ {int(GATE_APM)}",
        "line": GATE_APM,
        "window_rule": (
            f"报告标注 apm_window=true；或起点体力 ≥ {APM_WINDOW_MIN_ENERGY}、"
            f"回合 ≥ {APM_WINDOW_MIN_ROUNDS}、且该段不是以合法等待结束"
        ),
        "runs": states,
        "considered": len(states),
        "latest": latest,
        "latest_source": (
            "declared_retest" if retest else ("derived_window" if window_latest else None)
        ),
        "retest": retest,
        "window_latest": window_latest,
        "best": best,
        "met_latest": latest["met"] if latest else None,
        "met_retest": retest["met"] if retest else None,
        "met_window_latest": window_latest["met"] if window_latest else None,
        "met_best": best["met"] if best else None,
        "runs_with_actions_excluded": excluded,
        "excluded_for_legal_wait": excluded_legal_wait,
    }


def build_state_change_gate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """F7-05 section 3 item 5: the gate that replaces ``APM >= 40``.

    Same satiated window the APM gate uses -- a segment that began with energy
    in hand and did not end on a legal wait -- but the measured quantity is
    state-changing actions per hour: merges, generations, deliveries, chest
    openings and successful claims, with every close, follow, wait and refuted
    retry counted against the segment instead of for it.  The effective APM
    rides along as a reference column; it is no longer the gate.
    """

    window = [
        row
        for row in rows
        if ((row.get("efficiency") or {}).get("apm_window") or {}).get("qualifies")
        and isinstance(
            (row.get("action_validity") or {}).get("state_change_per_hour"),
            (int, float),
        )
    ]

    def state(row: dict[str, Any]) -> dict[str, Any]:
        identity = row.get("identity") or {}
        validity = row.get("action_validity") or {}
        per_hour = validity["state_change_per_hour"]
        return {
            "run_id": row["run_id"],
            "task": identity.get("task"),
            "started_at": identity.get("started_at"),
            "rounds": identity.get("rounds"),
            "energy_start": ((row.get("business") or {}).get("energy") or {}).get("start"),
            "stop_reason": identity.get("stop_reason"),
            "state_change_per_hour": per_hour,
            "state_change_actions": validity.get("state_change_actions"),
            "roundtrip_share": validity.get("roundtrip_share"),
            # Reference column, per spec: the old metric stays readable beside
            # the new one and decides nothing.
            "effective_apm": (row.get("efficiency") or {}).get("effective_apm"),
            "met": per_hour >= STATE_CHANGE_PER_HOUR_GATE,
        }

    states = [state(row) for row in window]
    best = (
        max(states, key=lambda item: item["state_change_per_hour"]) if states else None
    )
    latest = states[-1] if states else None
    return {
        "gate": "state_change_per_hour_full_energy_window",
        "label": f"满体力段每小时状态变更动作数 ≥ {int(STATE_CHANGE_PER_HOUR_GATE)}",
        "line": STATE_CHANGE_PER_HOUR_GATE,
        "line_source": STATE_CHANGE_PER_HOUR_GATE_SOURCE,
        "window": "与 4.1 APM 门同一满体力窗口（起点体力、回合数、非合法等待结束）",
        "runs": states,
        "considered": len(states),
        "latest": latest,
        "best": best,
        "met_latest": latest["met"] if latest else None,
        "met_best": best["met"] if best else None,
    }


def _drive_three_gates(drives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The refuted / lost / red-line gates counted over whole drives.

    A drive is the unit an unattended session is judged in; the same three
    lines applied to it answer a different question from the last three runs,
    which is why F7-03 asks for both levels rather than one.
    """

    rows = []
    for drive in drives:
        judged = drive["confirmed"] + drive["refuted"]
        rate = drive["refuted"] / judged if judged else None
        rows.append(
            {
                "drive_id": drive["drive_id"],
                "segments": drive["segments"],
                "refuted_rate": rate,
                "refuted_below_5_percent": (
                    None if rate is None else rate < GATE_REFUTED_RATE
                ),
                "frames_lost": drive["data"]["frames_lost"],
                "frames_lost_zero": drive["data"]["frames_lost"] == 0,
                "redline_violations": drive["redline_violations"],
                "redline_zero": drive["redline_violations"] == 0,
            }
        )
    return rows


def _daily_uptime_gate(guardian: dict[str, Any] | None) -> dict[str, Any]:
    """F6-05 section 3.3: driver uptime per calendar day, once a guardian exists.

    Days before the first daily report are counted separately rather than
    failed: nothing was watching them, so they carry no evidence either way.
    """

    guardian = guardian or {}
    measured = int(guardian.get("measured_days") or 0)
    met = int(guardian.get("days_meeting_effective_hours") or 0)
    return {
        "gate": "daily_effective_hours",
        "label": f"每日有效运行 >= {DAILY_EFFECTIVE_HOURS_GATE:.0f} h",
        "met": met,
        "considered": measured,
        "all_met": measured > 0 and met == measured,
        "threshold_hours": DAILY_EFFECTIVE_HOURS_GATE,
        "unmeasured_days_before_guardian": int(
            guardian.get("unmeasured_days_before_guardian") or 0
        ),
        "days_with_no_daily_report": list(
            guardian.get("days_with_no_daily_report") or []
        ),
    }


def build_gates(
    rows: list[dict[str, Any]],
    pooled: dict[str, Any] | None = None,
    drives: list[dict[str, Any]] | None = None,
    guardian: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Gate state over the most recent runs that actually executed actions.

    A run that stopped before its first action carries no APM evidence, so it
    would report a failing gate without having tried; the window therefore
    walks back over runs with at least one executed action.  The action-rate
    gate has its own window and lives in ``apm``; the other three are counted
    at both levels F7-03 asks for, the recent runs and every whole drive.
    """

    played = [row for row in rows if _has_actions(row)]
    recent = played[-RECENT_RUNS:]
    states = [_gate_state(row) for row in recent]
    gate_keys = (
        ("refuted_below_5_percent", "推翻率 < 5%"),
        ("frames_lost_zero", "丢帧 0"),
        ("redline_zero", "红线违规 0"),
    )
    summary = []
    for key, label in gate_keys:
        values = [state[key] for state in states]
        summary.append(
            {
                "gate": key,
                "label": label,
                "met": sum(1 for value in values if value is True),
                "considered": len(values),
                "all_met": bool(values) and all(value is True for value in values),
            }
        )
    drive_rows = _drive_three_gates(drives or [])
    drive_summary = []
    for key, label in gate_keys:
        values = [row[key] for row in drive_rows]
        drive_summary.append(
            {
                "gate": key,
                "label": label,
                "met": sum(1 for value in values if value is True),
                "considered": sum(1 for value in values if value is not None),
                "all_met": bool(values) and all(value is not False for value in values),
            }
        )
    return {
        "window": RECENT_RUNS,
        "runs": states,
        "summary": summary,
        "drive_three_gates": {"rows": drive_rows, "summary": drive_summary},
        "apm": build_apm_gate(rows),
        # F7-05: the gate that replaced the APM line; 4.1 renders the APM one
        # as reference only.
        "state_change": build_state_change_gate(rows),
        "advance": advance_gate(drives or [], pooled or {}),
        "drive_gates": _drive_gates(pooled or {}),
        "daily_uptime": _daily_uptime_gate(guardian),
    }


def build_baselines(rows: list[dict[str, Any]]) -> dict[str, Any]:
    played = [row for row in rows if _has_actions(row)]
    apms = [
        row["efficiency"]["effective_apm"]
        for row in played
        if isinstance((row.get("efficiency") or {}).get("effective_apm"), (int, float))
    ]
    llm_calls = [row["model"]["llm_calls"] for row in rows]
    return {
        "rows": [
            {"facility": facility, "metric": metric, "baseline": baseline}
            for facility, metric, baseline in BASELINES
        ],
        "current": {
            "runs_with_actions": len(played),
            "effective_apm_median": percentile(apms, 0.5) if apms else None,
            "effective_apm_best": max(apms) if apms else None,
            "runs_at_or_above_gate": sum(1 for value in apms if value >= GATE_APM),
            "llm_calls_recorded": sum(1 for value in llm_calls if value is not None),
        },
    }


def _directory_bytes(path: Path) -> int:
    total = 0
    for current, _, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(current, name)).st_size
            except OSError:
                continue
    return total


def build_hygiene(rows: list[dict[str, Any]], inputs: Inputs) -> dict[str, Any]:
    roots: dict[str, int] = {}
    for entry in inputs.index_entries.values():
        roots[entry.root.name] = roots.get(entry.root.name, 0) + (entry.bytes_total or 0)
    replay_bytes = sum(roots.values())
    started = [
        row["identity"]["started_at"]
        for row in rows
        if isinstance((row.get("identity") or {}).get("started_at"), str)
    ]
    integrity = [
        (row.get("data") or {}).get("frames_ndjson", {}).get("consistent") for row in rows
    ]
    inconsistent = [
        {
            "run_id": row["run_id"],
            "report": (row.get("identity") or {}).get("report"),
            **(row.get("data") or {}).get("frames_ndjson", {}),
        }
        for row in rows
        if ((row.get("data") or {}).get("frames_ndjson") or {}).get("consistent") is False
    ]
    return {
        "replay_bytes": replay_bytes,
        "replay_quota_bytes": REPLAY_QUOTA_BYTES,
        "replay_quota_used_rate": replay_bytes / REPLAY_QUOTA_BYTES
        if REPLAY_QUOTA_BYTES
        else None,
        "replay_bytes_by_root": dict(sorted(roots.items())),
        "feasibility_bytes": _directory_bytes(inputs.paths["feasibility"]),
        "oldest_run_started_at": min(started) if started else None,
        "newest_run_started_at": max(started) if started else None,
        "frames_ndjson_consistent": sum(1 for value in integrity if value is True),
        "frames_ndjson_checked": sum(1 for value in integrity if value is not None),
        "frames_ndjson_inconsistent": inconsistent,
    }


def build_missing_reasons(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Why the rows carry nulls: one line per dotted path, with an example.

    F7-01 wrote the reasons per row and nowhere aggregated them, so a source
    gap that affects a hundred runs looked the same as one that affects one.
    """

    counts: dict[str, int] = {}
    examples: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for row in rows:
        for path, reason in (row.get("missing") or {}).items():
            counts[path] = counts.get(path, 0) + 1
            reasons.setdefault(path, reason)
            examples.setdefault(path, row["run_id"])
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return {
        "rows": [
            {
                "path": path,
                "runs": count,
                "share": count / len(rows) if rows else None,
                "reason": reasons[path],
                "example_run_id": examples[path],
            }
            for path, count in ordered
        ],
        "runs": len(rows),
    }


def build_comparison(inputs: Inputs) -> dict[str, Any]:
    """The A/B comparison exactly as F4-08 wrote it.

    The table, the arms and the verdict are carried through unchanged; the
    dashboard's only contribution is saying which product it read and which
    threshold that product left as the default.
    """

    report = inputs.comparison
    verdict = None
    threshold = None
    if inputs.multi_day is not None:
        payload = inputs.multi_day.payload
        candidate = payload.get("ab_verdict")
        verdict = candidate if isinstance(candidate, dict) else None
        setting = payload.get("resume_energy_threshold")
        threshold = setting if isinstance(setting, dict) else None
    if report is None:
        return {
            "available": False,
            "reason": "feasibility 里没有 rr-drive-comparison.v1 产物",
            "verdict": verdict,
            "threshold": threshold,
        }
    payload = report.payload
    return {
        "available": True,
        "source": report.name,
        "arms": payload.get("arms") if isinstance(payload.get("arms"), dict) else None,
        "table": payload.get("table") if isinstance(payload.get("table"), list) else [],
        "verdict": verdict,
        "verdict_source": inputs.multi_day.name if inputs.multi_day is not None else None,
        "threshold": threshold,
    }


def build_dashboard(rows: list[dict[str, Any]], inputs: Inputs) -> dict[str, Any]:
    drives = build_drives(rows, inputs)
    pooled = pool_drives(drives, energy_ceiling(inputs))
    guardian = build_guardian(inputs, drives)
    return {
        "schema": DASHBOARD_SCHEMA,
        "overview": build_overview(rows),
        "timeline": build_timeline(rows),
        "bottleneck": build_bottleneck(inputs),
        "gates": build_gates(rows, pooled, drives, guardian),
        "guardian": guardian,
        "baselines": build_baselines(rows),
        "hygiene": build_hygiene(rows, inputs),
        "drives": {"rows": drives, "pooled": pooled},
        "progress": build_progress(drives, inputs),
        "comparison": build_comparison(inputs),
        "free_claims": build_free_claim_audit(inputs),
        "save_read": build_save_read_trend(rows, inputs),
        "missing_reasons": build_missing_reasons(rows),
        # F7-04 section 2.  Four blocks the cross-reference ledger already
        # computed.  They are carried over, not recomputed, so that the
        # dashboard and the ledger cannot drift into two different counts.
        "guard_events": (inputs.summary.get("guard_events") or {}),
        "save_read_timing": (
            (inputs.summary.get("facility_facts") or {}).get("save_read_timing") or {}
        ),
        "story_advance": (
            (inputs.summary.get("facility_facts") or {}).get("story_advance") or {}
        ),
        "order_reserve": (
            (inputs.summary.get("facility_facts") or {}).get("order_reserve") or {}
        ),
    }


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _pct(value: Any, digits: int = 1) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "—"
    return f"{value * 100:.{digits}f}%"


def _mib(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "—"
    return f"{value / 2**20:.2f} MiB"


def _gib(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value / 2**30:.2f} GiB"


def _short(run_id: str) -> str:
    return run_id.rsplit(".", 2)[-2] if run_id.count(".") >= 2 else run_id


def render_dashboard_md(dashboard: dict[str, Any]) -> str:
    overview = dashboard["overview"]
    lines = [
        "# 浪漫餐厅长跑效率仪表",
        "",
        "> 本文件由 `python -m omnicompany.packages.domains.game_observatory.metrics build` 写出，"
        "不要手改。每一行都来自 run 报告与 replay 索引，报告没记的字段留空并在 "
        "`runs.v1.jsonl` 的 `missing` 里写明原因。",
        "",
        "## 1 总览",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| run 数 | {overview['runs']} |",
        f"| 其中有动作的 run | {overview['runs_with_actions']} |",
        f"| 总动作 | {overview['game_actions']} |",
        f"| 其中状态变更（F7-05） | {overview['state_change_actions']} |",
        f"| 其中界面往返（F7-05） | {overview['interface_roundtrip_actions']} |",
        f"| 总回合 | {overview['rounds']} |",
        f"| 总交付 | {overview['confirmed_deliveries']} |",
        f"| 主线任务完成 | {overview['main_tasks_completed']} |",
        f"| 总帧 | {overview['frames_written']} |",
        f"| 丢帧 | {overview['frames_lost']} |",
        f"| 红线违规 | {overview['redline_violations']} |",
        f"| 禁入界面前帧 | {overview['forbidden_surface_pre_frames']} |",
        f"| 未知界面停机 | {overview['unknown_screen_stops']} |",
        f"| 总墙钟秒 | {_fmt(overview['wall_seconds'], 1)} |",
        "",
        "红线违规按各 run 报告原样求和。事后复审把其中一部分判回清白，那个结论写在做复审的那份"
        "报告里，本表不替它改写 run 自己的记录。",
        "",
        "按全部 run 的墙钟合计折算的每小时速率：",
        "",
        "| 每小时 | 值 |",
        "|---|---:|",
    ]
    rates = overview["per_hour"] or {}
    lines.extend(
        [
            f"| 动作 | {_fmt(rates.get('game_actions'), 1)} |",
            f"| 状态变更动作（F7-05） | {_fmt(rates.get('state_change_actions'), 1)} |",
            f"| 主线任务 | {_fmt(rates.get('main_tasks'), 2)} |",
            f"| 交付 | {_fmt(rates.get('confirmed_deliveries'), 2)} |",
            f"| 金币净增 | {_fmt(rates.get('coins_delta'), 1)} |",
            f"| replay 增长 MB | {_fmt(rates.get('replay_mb'), 1)} |",
        ]
    )
    lines.extend(
        [
            "",
            "## 2 时间序列",
            "",
            "有效 APM 保留为参考列；F7-05 起推进看「状态变更/h」与「往返占比」两列，口径见第 16 节。",
            "",
            "| run | 任务 | 开始 | 回合 | 停机原因 | 有效 APM | 状态变更/h | 往返占比 | 推翻率 | 回合 p50 秒 | 交付 |",
            "|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in dashboard["timeline"]:
        started = str(row["started_at"] or "")[:19].replace("T", " ")
        lines.append(
            "| `{run}` | {task} | {started} | {rounds} | {stop} | {apm} | {scph} | "
            "{rt_share} | {refuted} | {p50} | {deliveries} |".format(
                run=_short(row["run_id"]),
                task=row["task"] or "—",
                started=started or "—",
                rounds=_fmt(row["rounds"]),
                stop=row["stop_reason"] or "—",
                apm=_fmt(row["effective_apm"]),
                scph=_fmt(row.get("state_change_per_hour"), 0),
                rt_share=_pct(row.get("roundtrip_share")),
                refuted=_fmt(row["refuted_rate"], 4),
                p50=_fmt(row["round_p50_s"]),
                deliveries=_fmt(row["confirmed_deliveries"]),
            )
        )
    bottleneck = dashboard["bottleneck"]
    lines.extend(
        [
            "",
            "## 3 分层瓶颈",
            "",
            "全库逐回合样本合并后的分层中位数；占比是该层中位数在各层中位数之和里的份额。",
            "",
            "| 层 | 样本 | p50 秒 | p95 秒 | 占比 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for name in LAYER_NAMES:
        layer = bottleneck["layers"][name]
        share = layer["share_of_median_round"]
        lines.append(
            f"| {name} | {layer['samples']} | {_fmt(layer['p50_s'], 3)} | "
            f"{_fmt(layer['p95_s'], 3)} | "
            f"{'—' if share is None else f'{share * 100:.1f}%'} |"
        )
    largest = bottleneck["largest_layer"]
    lines.extend(
        [
            "",
            f"当前最大项：**{largest or '—'}**；各层中位数之和 "
            f"{_fmt(bottleneck['median_round_s'], 3)} 秒。",
        ]
    )
    lines.extend(_render_gates(dashboard))
    baselines = dashboard["baselines"]
    current = baselines["current"]
    lines.extend(
        [
            "",
            "## 5 与旧设施基线对照",
            "",
            "| 对象 | 指标 | 基线 |",
            "|---|---|---|",
        ]
    )
    for row in baselines["rows"]:
        lines.append(f"| {row['facility']} | {row['metric']} | {row['baseline']} |")
    lines.extend(
        [
            "",
            "| 本设施 | 值 |",
            "|---|---:|",
            f"| 有动作的 run | {current['runs_with_actions']} |",
            f"| 有效 APM 中位 | {_fmt(current['effective_apm_median'])} |",
            f"| 有效 APM 最好 | {_fmt(current['effective_apm_best'])} |",
            f"| 达到 {int(GATE_APM)} APM 门的 run | {current['runs_at_or_above_gate']} |",
            f"| 记录到模型调用数的 run | {current['llm_calls_recorded']} |",
            "",
            "本设施的规划器不调用模型，报告也没有模型调用字段；仪表因此不写 0，"
            "只记录「没有任何 run 记录过调用数」。",
            "",
            "## 6 数据卫生",
            "",
            "| 项 | 值 |",
            "|---|---:|",
        ]
    )
    hygiene = dashboard["hygiene"]
    used = hygiene["replay_quota_used_rate"]
    pooled = (dashboard.get("drives") or {}).get("pooled") or {}
    lines.extend(
        [
            f"| replay 总用量 | {_gib(hygiene['replay_bytes'])} |",
            f"| replay 配额 | {_gib(hygiene['replay_quota_bytes'])} |",
            f"| 配额占用 | {'—' if used is None else f'{used * 100:.1f}%'} |",
            f"| feasibility 目录用量（不计入配额） | {_gib(hygiene['feasibility_bytes'])} |",
            f"| 最旧 run | {hygiene['oldest_run_started_at'] or '—'} |",
            f"| 最新 run | {hygiene['newest_run_started_at'] or '—'} |",
            f"| frames.ndjson 一致 / 已核 | "
            f"{hygiene['frames_ndjson_consistent']} / {hygiene['frames_ndjson_checked']} |",
            f"| 驱动段：每段平均帧 | {_fmt(pooled.get('frames_per_segment'), 1)} |",
            f"| 驱动段：每段平均磁盘增量 | {_mib(pooled.get('bytes_per_segment'))} |",
            f"| 驱动段：合计帧 / 磁盘 | {pooled.get('frames_written', 0)} / "
            f"{_mib((pooled.get('totals') or {}).get('run_dir_bytes'))} |",
            "",
            "| replay 库 | 用量 |",
            "|---|---:|",
        ]
    )
    for name, value in hygiene["replay_bytes_by_root"].items():
        lines.append(f"| `{name}` | {_gib(value)} |")
    if hygiene["frames_ndjson_inconsistent"]:
        lines.extend(
            [
                "",
                "帧记录与磁盘对不上的 run：",
                "",
                "| run | 报告 | 索引帧 | 帧记录 | 已写 | 丢帧 | 磁盘文件 |",
                "|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for entry in hygiene["frames_ndjson_inconsistent"]:
            lines.append(
                f"| `{_short(entry['run_id'])}` | {entry.get('report') or '—'} | "
                f"{_fmt(entry.get('index_frames'))} | {_fmt(entry.get('records'))} | "
                f"{_fmt(entry.get('written'))} | {_fmt(entry.get('lost'))} | "
                f"{_fmt(entry.get('files'))} |"
            )
    lines.extend(_render_drives(dashboard))
    lines.extend(_render_progress_rate(dashboard))
    lines.extend(_render_progress(dashboard))
    lines.extend(_render_comparison(dashboard))
    lines.extend(_render_free_claims(dashboard))
    lines.extend(_render_save_read(dashboard))
    lines.extend(_render_missing_reasons(dashboard))
    lines.extend(_render_guardian(dashboard))
    lines.extend(_render_wait_attribution(dashboard))
    lines.extend(_render_action_validity(dashboard))
    return "\n".join(lines) + "\n"


def _render_gates(dashboard: dict[str, Any]) -> list[str]:
    """The gate section: one action-rate gate, one advance gate, three lines."""

    gates = dashboard["gates"]
    apm = gates["apm"]
    advance = gates["advance"]
    lines = [
        "",
        "## 4 门状态",
        "",
        "### 4.1 满体力段 APM 门（F7-05 起降为参考口径，不作门）",
        "",
        "F7-05 指出 APM 度量的是设备吞吐而非推进：守卫关弹窗、引导跟随重试、被拒重复预测全部计入。"
        "本节保留作新旧对照的参考列，门判定已移到 4.6 的每小时状态变更动作数。",
        "",
        f"窗口：{apm['window_rule']}。能量受限的段不进这道门——它们的动作率量的是回复时钟，"
        "不是循环的能力；那些段改由 4.2 的推进门回答。"
        "「最近一次」取声明优先：只要有聚合报告用 `apm_retest.report` 指名过专门为测速率而跑的段，"
        "就以最近一次这样的正式复测为准；没有任何声明时才退回窗口内最新的一段。"
        "另单列「窗口内最新的满体力段」，这样一次慢的满体力段不会被复测行盖住。",
        "",
        "| 口径 | run | 任务 | 开始 | 起点体力 | 回合 | 段停机原因 | 有效 APM | 达标 | 选段依据 |",
        "|---|---|---|---|---:|---:|---|---:|---|---|",
    ]
    window_latest = apm.get("window_latest")
    retest = apm.get("retest")
    extra_rows: list[tuple[str, Any]] = []
    if window_latest is not None and (
        retest is None or window_latest["run_id"] != retest["run_id"]
    ):
        extra_rows.append(("窗口内最新的满体力段", window_latest))
    for label, state in (
        ("最近一次", apm["latest"]),
        *extra_rows,
        ("历史最好", apm["best"]),
    ):
        if state is None:
            lines.append(f"| {label} | — | — | — | — | — | — | — | — | 无 |")
            continue
        lines.append(
            f"| {label} | `{_short(state['run_id'])}` | {state['task'] or '—'} | "
            f"{str(state['started_at'] or '')[:19].replace('T', ' ')} | "
            f"{_fmt(state['energy_start'])} | {_fmt(state['rounds'])} | "
            f"{state.get('stop_reason') or '—'} | "
            f"{_fmt(state['effective_apm'])} | {_fmt(state['met'])} | {state.get('basis') or '—'} |"
        )
    lines.extend(
        [
            "",
            f"进窗口的 run {apm['considered']} 个，门线 {int(apm['line'])}；"
            f"另有 {apm['runs_with_actions_excluded']} 个有动作的 run 因不满足窗口条件不计入，"
            f"其中 {apm['excluded_for_legal_wait']} 个是起点体力够、但以合法等待结束而被排除的段。",
            "",
            "### 4.2 推进门（能量受限口径）",
            "",
            "体力只买生成、合成免费，所以能量受限时唯一能衡量推进的是花掉的体力。第一问是绝对的："
            "一小时回复发下来的体力，驱动花掉了多少；第二问是相对的：每点体力换回多少金币，"
            f"对照所有花过体力的驱动的中位。门线 {_pct(advance['line_energy_share'], 0)}。",
            "",
            "| 驱动 | 花掉体力 | 每小时花掉 | 上限每小时 | 占上限 | 达标 | 每点体力换金币 | ≥ 中位 | energy_bound 段 |",
            "|---|---:|---:|---:|---:|---|---:|---|---:|",
        ]
    )
    for row in advance["rows"]:
        lines.append(
            f"| `{row['drive_id']}` | {_fmt(row['energy_spent'])} | "
            f"{_fmt(row['energy_spent_per_hour'], 2)} | "
            f"{_fmt(row['energy_ceiling_per_hour'], 2)} | "
            f"{_pct(row['energy_share_of_ceiling'])} | {_fmt(row['energy_share_met'])} | "
            f"{_fmt(row['coins_per_energy'], 3)} | {_fmt(row['coins_per_energy_met'])} | "
            f"{row['energy_bound_segments']} |"
        )
    lines.extend(
        [
            "",
            f"金币/体力的历史中位 {_fmt(advance['coins_per_energy_median'], 3)}，"
            "由所有花过体力的驱动算出，因此这一问是「没有退步」而不是「达到某个绝对值」。",
            "",
            "两条读表须知，都不改判：①「占上限」可以超过 100%——从满体力起跑的驱动烧的是存量，"
            "不是那一小时的回复，只有已进入 energy_bound 的驱动这一栏才是「等待策略有没有浪费回复」"
            "的答案；②事件流不带 `energy_spent` 的旧驱动整行留空，不按 0 计。",
            "",
            "### 4.3 推翻率 / 丢帧 / 红线（run 与 drive 两级）",
            "",
            f"run 级窗口是最近 {gates['window']} 个有动作的 run；停机在首个动作之前的 run 不进窗口。",
            "",
            "| 门 | run 级达标 / 计入 | run 级全部达标 | drive 级达标 / 计入 | drive 级全部达标 |",
            "|---|---:|---|---:|---|",
        ]
    )
    drive_summary = {
        row["gate"]: row for row in (gates["drive_three_gates"]["summary"] or [])
    }
    for gate in gates["summary"]:
        other = drive_summary.get(gate["gate"], {})
        lines.append(
            f"| {gate['label']} | {gate['met']} / {gate['considered']} | "
            f"{_fmt(gate['all_met'])} | {other.get('met', 0)} / {other.get('considered', 0)} | "
            f"{_fmt(other.get('all_met'))} |"
        )
    lines.extend(
        [
            "",
            "| run | 任务 | 有效 APM | 推翻率 | 偏差率 | 丢帧 | 红线违规 |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for state in gates["runs"]:
        lines.append(
            f"| `{_short(state['run_id'])}` | {state['task'] or '—'} | "
            f"{_fmt(state['effective_apm'])} | {_fmt(state['refuted_rate'], 4)} | "
            f"{_fmt(state.get('deviation_rate'), 4)} | "
            f"{_fmt(state['frames_lost'])} | {_fmt(state['redline_violations'])} |"
        )
    lines.extend(
        [
            "",
            "| 驱动 | 段 | 推翻率 | 丢帧 | 红线违规 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in gates["drive_three_gates"]["rows"]:
        lines.append(
            f"| `{row['drive_id']}` | {row['segments']} | {_fmt(row['refuted_rate'], 4)} | "
            f"{row['frames_lost']} | {row['redline_violations']} |"
        )
    lines.extend(
        [
            "",
            "### 4.4 无人值守驱动的两门",
            "",
            "全库口径，不受最近 3 个 run 的窗口限制。",
            "",
            "| 门 | 实测 | 判据样本 | 达标 | 说明 |",
            "|---|---:|---:|---|---|",
        ]
    )
    for gate in gates.get("drive_gates") or []:
        lines.append(
            f"| {gate['label']} | {_pct(gate['value'])} | {gate['considered']} | "
            f"{_fmt(gate['met'])} | {gate['note']} |"
        )
    uptime = gates.get("daily_uptime") or {}
    if uptime:
        absent = uptime.get("days_with_no_daily_report") or []
        lines.extend(
            [
                "",
                "### 4.5 每日有效运行",
                "",
                "有效运行 = 当日全部驱动会话的墙钟之和（含合法等待，不含停机）。守护上线之前"
                "的日子没有任何设施负责重启驱动，单独计数，不判达标。逐日明细见第 14 节。",
                "",
                "| 门 | 达标日 | 判据日 | 达标 | 守护上线前未计 | 无日报的日子 |",
                "|---|---:|---:|---|---:|---|",
                f"| {uptime.get('label')} | {uptime.get('met')} | "
                f"{uptime.get('considered')} | {_fmt(uptime.get('all_met'))} | "
                f"{uptime.get('unmeasured_days_before_guardian')} | "
                f"{'、'.join(absent) if absent else '—'} |",
            ]
        )
    state_change = gates.get("state_change") or {}
    lines.extend(
        [
            "",
            "### 4.6 满体力段每小时状态变更动作门（F7-05 新门）",
            "",
            f"{state_change.get('window', '')}。APM 只作参考列，不再作门。门线来源："
            f"{state_change.get('line_source', '')}",
            "",
            "| 口径 | run | 任务 | 开始 | 起点体力 | 回合 | 段停机原因 | 状态变更/h | 往返占比 | 参考 APM | 达标 |",
            "|---|---|---|---|---:|---:|---|---:|---:|---:|---|",
        ]
    )
    for label, state in (
        ("最近一次", state_change.get("latest")),
        ("历史最好", state_change.get("best")),
    ):
        if state is None:
            lines.append(f"| {label} | — | — | — | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {label} | `{_short(state['run_id'])}` | {state['task'] or '—'} | "
            f"{str(state['started_at'] or '')[:19].replace('T', ' ')} | "
            f"{_fmt(state['energy_start'])} | {_fmt(state['rounds'])} | "
            f"{state.get('stop_reason') or '—'} | "
            f"{_fmt(state['state_change_per_hour'], 0)} | "
            f"{_pct(state.get('roundtrip_share'))} | "
            f"{_fmt(state.get('effective_apm'))} | {_fmt(state['met'])} |"
        )
    lines.extend(
        [
            "",
            f"进窗口的满体力段 {state_change.get('considered', 0)} 个，"
            f"门线 {int(state_change.get('line') or 0)} 次/小时（建议值，待规划者裁决）。"
            "逐段明细见第 16 节。",
        ]
    )
    return lines


def _render_guardian(dashboard: dict[str, Any]) -> list[str]:
    guardian = dashboard.get("guardian") or {}
    days = guardian.get("days") or []
    if not days:
        return []
    lines = [
        "",
        "## 14 多日守护",
        "",
        "一行一个自然日（UTC）。会话数与时间来自当日启动的驱动行，求裁来自守护报告自己的"
        "处置，日报一列是 records/daily 下该日期的文件是否存在与它自报的生成时刻。"
        f"守护报告：{'、'.join(guardian.get('guardian_reports') or []) or '—'}；"
        f"首份日报覆盖的日子：{guardian.get('first_guardian_report_day') or '—'}。",
        "",
        "| 日期 | 驱动 | 守护会话 | 有效运行 h | 游玩 h | 等待 h | "
        f"≥ {guardian.get('gate_effective_hours')} h | 求裁 | 日报 | 日报生成于 | 往返标红 |",
        "|---|---:|---:|---:|---:|---:|---|---:|---|---|---:|",
    ]
    threshold = float(guardian.get("gate_effective_hours") or 0.0)
    first_day = guardian.get("first_guardian_report_day")
    for row in days:
        report = row.get("daily_report") or {}
        measured = first_day is not None and row["day"] >= first_day
        met = (
            "—（守护上线前）"
            if not measured
            else _fmt(row["effective_hours"] >= threshold)
        )
        red_segments = row.get("roundtrip_red_segments") or []
        red = (
            f"**红：{len(red_segments)} 段**"
            if row.get("roundtrip_red")
            else _fmt(False)
        )
        lines.append(
            f"| {row['day']} | {row['drives']} | {row.get('guardian_sessions', 0)} | "
            f"{_fmt(row['effective_hours'])} | {_fmt(row['play_hours'])} | "
            f"{_fmt(row['waited_seconds'] / 3600.0)} | {met} | "
            f"{len(row.get('escalations') or [])} | "
            f"{'有' if report else '无'} | {report.get('generated_at') or '—'} | {red} |"
        )
    lines.extend(
        [
            "",
            "### 14.1 冷启臂（读日报的「冷启臂」节）",
            "",
            "守护逐会话轮转冷启策略，比较记在日报上。日报里没有这一节的日子记「未按臂轮转」，"
            "不折算成 0。",
            "",
            "| 日期 | 臂 | 会话 | 首回合样本 | 首回合被拒 | 被拒率 |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    arm_rows = 0
    for row in days:
        arms = row.get("cold_start_arms") or {}
        if not arms.get("arms"):
            lines.append(
                f"| {row['day']} | "
                + ("未按臂轮转" if arms.get("present") else "日报无该节")
                + " | — | — | — | — |"
            )
            continue
        for name, bucket in sorted(arms["arms"].items()):
            arm_rows += 1
            lines.append(
                f"| {row['day']} | {name} | {bucket['sessions']} | "
                f"{bucket['predictions']} | {bucket['refused']} | "
                f"{_pct(bucket.get('first_round_refused_rate'), 2)} |"
            )
    if not arm_rows:
        lines.append("")
        lines.append("目前没有任何一天的日报带冷启臂表，无样本。")
    lines.extend(
        [
            "",
            f"### 14.2 每日有效运行 ≥ {guardian.get('gate_effective_hours')} h（按日）",
            "",
            "| 日期 | 有效运行 h | 判定 |",
            "|---|---:|---|",
        ]
    )
    for row in days:
        gate = row.get("effective_hours_gate")
        lines.append(
            f"| {row['day']} | {_fmt(row['effective_hours'])} | "
            + ("无日报，不判" if gate is None else _fmt(gate))
            + " |"
        )
    stops: dict[str, int] = {}
    for row in days:
        for reason, count in (row.get("stop_reasons") or {}).items():
            stops[reason] = stops.get(reason, 0) + count
    terminals: dict[str, int] = {}
    for row in days:
        for reason, count in (row.get("terminal_stop_reasons") or {}).items():
            terminals[reason] = terminals.get(reason, 0) + count
    if stops or terminals:
        lines.extend(
            [
                "",
                "段停机原因是驱动逐段计的直方图；终态是每次驱动会话最后停在哪个原因上。",
                "",
                "| 停机原因 | 段次 | 驱动终态 |",
                "|---|---:|---:|",
            ]
        )
        for reason in sorted(set(stops) | set(terminals)):
            lines.append(
                f"| {reason} | {stops.get(reason, 0)} | {terminals.get(reason, 0)} |"
            )
    escalations = [
        (row["day"], item)
        for row in days
        for item in (row.get("escalations") or [])
    ]
    if escalations:
        lines.extend(
            ["", "| 日期 | 会话 | 驱动停机原因 | 段内原因 |", "|---|---|---|---|"]
        )
        for day, item in escalations:
            lines.append(
                f"| {day} | {item.get('label') or '—'} | "
                f"{item.get('drive_stop_reason') or '—'} | "
                f"{item.get('segment_stop_reason') or '—'} |"
            )
    gaps = guardian.get("days_with_no_driver_activity") or []
    if gaps:
        lines.extend(["", f"无驱动活动也无日报的日子：{'、'.join(gaps)}。"])
    return lines


def _render_drives(dashboard: dict[str, Any]) -> list[str]:
    drives = (dashboard.get("drives") or {}).get("rows") or []
    pooled = (dashboard.get("drives") or {}).get("pooled") or {}
    recovery = pooled.get("recovery") or {}
    lines = [
        "",
        "## 7 无人值守驱动",
        "",
        "一行一次 `rr_drive` 驱动会话。墙钟是事件流首末之间的时间；一次尚未结束的等待不计入"
        "游玩，账期截到它开始那一刻。停机原因用驱动自己的全集词表，段的停机理由由 "
        "`classify_segment_stop` 翻译，不另行推断。事件流既无 `drive_stopped` 行、"
        "又没有写出 .json 报告的驱动，从产物上分不出「还在跑」和「被外部终止」，状态记「无终止记录」。",
        "",
        "| 驱动 | 任务 | 状态 | 段 | energy_bound 段 | 墙钟秒 | 睡眠秒 | 有效游玩秒 | 等待次数 | 申请秒 | "
        "被墙钟截断 | 恢复触发/成功 | 终态 | 退出码 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for drive in drives:
        waits = drive["waits"]
        ladder = drive["recovery"]
        bound = drive["energy_bound"]
        task = drive["task"] or "—"
        if drive.get("report_state") == "externally_stopped_no_report":
            task = f"{task}（外部终止、无报告）"
        lines.append(
            "| `{drive}` | {task} | {state} | {segments} | {bound} | {wall} | {slept} | {play} | "
            "{count} | {requested} | {truncated} | {trig} / {ok} | {stop} | {code} |".format(
                drive=drive["drive_id"],
                task=task,
                bound=(
                    f"{bound['segments']}"
                    + (
                        f"（{bound['segments_without_energy_field']} 段无体力字段）"
                        if bound["segments_without_energy_field"]
                        else ""
                    )
                ),
                state="无终止记录" if drive["running"] else "已结束",
                segments=drive["segments"],
                wall=_fmt(drive["wall_seconds"], 1),
                slept=_fmt(drive["waited_s"], 1),
                play=_fmt(drive["play_seconds"], 1),
                count=waits["count"],
                requested=_fmt(waits["requested_s"], 1),
                truncated=waits["truncated_by_wall_clock"],
                trig=ladder["segments_with_attempt"],
                ok=ladder["segments_recovered"],
                stop=drive["terminal_stop_reason"] or "—",
                code=_fmt(drive["exit_code"]),
            )
        )
    lines.extend(
        [
            "",
            "全库停机原因直方图（驱动停机原因全集，未发生的一律记 0）：",
            "",
            "| 停机原因 | 次数 |",
            "|---|---:|",
        ]
    )
    taxonomy = pooled.get("stop_taxonomy") or {}
    for reason in STOP_REASONS:
        lines.append(f"| `{reason}` | {taxonomy.get(reason, 0)} |")
    lines.extend(
        [
            "",
            "| 等待与游玩 | 值 |",
            "|---|---:|",
            f"| 驱动会话 | {pooled.get('drives', 0)} |",
            f"| 段 | {pooled.get('segments', 0)} |",
            f"| 墙钟秒 | {_fmt(pooled.get('wall_seconds'), 1)} |",
            f"| 等待（睡眠）秒 | {_fmt(pooled.get('waited_s'), 1)} |",
            f"| 有效游玩秒 | {_fmt(pooled.get('play_seconds'), 1)} |",
            f"| 等待占墙钟 | {_pct(pooled.get('waited_share'))} |",
            f"| 游玩占墙钟 | {_pct(pooled.get('play_share'))} |",
            f"| 每段平均帧 | {_fmt(pooled.get('frames_per_segment'), 1)} |",
            f"| 每段平均磁盘增量 | {_mib(pooled.get('bytes_per_segment'))} |",
            f"| 事件流坏行（跳过） | {pooled.get('malformed_log_lines', 0)} |",
            "",
            "energy_bound（F4-08 第 3.3 节，判据按其第 8 节修订为「每段花掉的体力」）：",
            "",
            "| energy_bound | 值 |",
            "|---|---:|",
        ]
    )
    bound = pooled.get("energy_bound") or {}
    lines.extend(
        [
            f"| 处于 energy_bound 的段 | {bound.get('segments', 0)} |",
            f"| 可判定的段（事件流带 energy_spent） | {bound.get('segments_judged', 0)} |",
            f"| 无体力字段、无法判定的段 | {bound.get('segments_without_energy_field', 0)} |",
            f"| 占可判定段 | {_pct(bound.get('share_of_judged_segments'))} |",
            f"| 占全部段 | {_pct(bound.get('share_of_all_segments'))} |",
            f"| 进入该状态的驱动 | {bound.get('drives_entered', 0)} |",
            f"| 连续段区间数 | {bound.get('spans', 0)} |",
            "",
            "| 驱动 | 判据 | 区间 | 段 | 该区间花掉体力 |",
            "|---|---|---|---:|---:|",
        ]
    )
    for drive in drives:
        entry = drive["energy_bound"]
        if not entry["spans"]:
            continue
        for span in entry["spans"]:
            lines.append(
                f"| `{drive['drive_id']}` | {entry['criterion']} | "
                f"{span['from']} → {span['to']} | {span['segments']} | {span['energy_spent']} |"
            )
    lines.extend(
        [
            "",
            "合成不花体力，所以旧判据里的「每段 confirmed ≤ 2」会把只能等体力的段判出去："
            "花 1 点生成一次再免费合成两次就是三个 confirmed。这里按修订后的口径重算，"
            "驱动报告自己的旧判据结论保留在 `drives.v1.jsonl` 的 `energy_bound_declared` 里。",
            "",
            "恢复统计：输入假死恢复梯触发 "
            f"{recovery.get('segments_with_attempt', 0)} 段、重启 "
            f"{recovery.get('restart_attempts', 0)} 次、成功 "
            f"{recovery.get('segments_recovered', 0)} 段、仍未恢复 "
            f"{recovery.get('segments_unrecovered', 0)} 段。",
        ]
    )
    lines.extend(_render_guard_event_histogram(dashboard))
    lines.extend(_render_read_back_trend_layer(dashboard))
    return lines


def _render_guard_event_histogram(dashboard: dict[str, Any]) -> list[str]:
    """The guard's own event counts, read from the cross-reference ledger.

    F7-04 section 2.2.  Pre-frame refusals, guard-state transitions and story
    advances are all recorded as facts by the ledger, so the histogram is a
    reading of that stream rather than a second walk over the reports.  An
    event family with no occurrences is printed as zero: a guard that never
    refused a frame is a result, not a missing row.
    """

    guard = dashboard.get("guard_events") or {}
    if not guard:
        return []
    lines = [
        "",
        "### 7.1 guard_event 直方图（真源 `crossref/facts.v1.ndjson#kind=guard_event`）",
        "",
        "| 事件 | 次数 |",
        "|---|---:|",
    ]
    for event in GUARD_EVENT_FAMILIES:
        lines.append(f"| `{event}` | {(guard.get('by_event') or {}).get(event, 0)} |")
    states = guard.get("guard_states") or {}
    lines.extend(["", "| 守卫状态 | 进入 | 退出 |", "|---|---:|---:|"])
    if not states:
        lines.append("| 无记录 | 0 | 0 |")
    for state, entry in sorted(states.items()):
        lines.append(
            f"| `{state}` | {entry.get('entered', 0)} | {entry.get('left', 0)} |"
        )
    refusals = guard.get("pre_frame_refusals_by_screen") or {}
    lines.extend(["", "| 前帧拒发时读到的界面 | 次数 |", "|---|---:|"])
    if not refusals:
        lines.append("| 无记录 | 0 |")
    for screen, count in sorted(refusals.items()):
        lines.append(f"| `{screen}` | {count} |")
    story = dashboard.get("story_advance") or {}
    lines.extend(
        [
            "",
            f"其中剧情推进 {story.get('samples', 0)} 次（守卫 "
            f"{(story.get('per_stream') or {}).get('screen_guard', 0)}、主线流程 "
            f"{(story.get('per_stream') or {}).get('main_task_flow', 0)}），"
            f"同一时刻双计 {len(story.get('double_counted_moments') or [])} 处。",
        ]
    )
    return lines


def _render_read_back_trend_layer(dashboard: dict[str, Any]) -> list[str]:
    """Read-back timing by stage, staged over the runs that recorded it.

    F7-04 section 2.2.  Section 12 already lists the staged reads one row per
    product; this layer sits under the drive table because that is where the
    question is asked -- whether the read-back got slower as the drives got
    longer -- and it is answered from the same facility facts.
    """

    timing = dashboard.get("save_read_timing") or {}
    stages = timing.get("stages") or {}
    if not timing.get("samples"):
        return []
    lines = [
        "",
        "### 7.2 回读分阶段趋势层（真源 `crossref/facts.v1.ndjson#kind=save_read_timing`）",
        "",
        f"{timing.get('samples', 0)} 次回读，落在 {len(timing.get('runs') or [])} 个 run 上。",
        "",
        "| 阶段 | 样本 | p50 | min | max |",
        "|---|---:|---:|---:|---:|",
    ]
    for stage, entry in stages.items():
        lines.append(
            f"| {stage} | {entry.get('n', 0)} | {_fmt(entry.get('p50'), 3)} | "
            f"{_fmt(entry.get('min'), 3)} | {_fmt(entry.get('max'), 3)} |"
        )
    flags = timing.get("flags") or {}
    lines.extend(["", "| 标志 | 取值分布 |", "|---|---|"])
    for name, values in sorted(flags.items()):
        lines.append(f"| {name} | {values} |")
    return lines


def _render_wait_attribution(dashboard: dict[str, Any]) -> list[str]:
    """Section 7.3: what each drive was waiting for, and for how long.

    F7-04 section 2.3.  Two legal waits and one planner refusal account for
    the idle time: waiting for energy to come back, waiting for a generator
    timer, and a merge an open order's reserve would not let the planner spend.
    The reserve count comes from the ledger and is zero until the planner
    records those refusals, which is stated rather than hidden.
    """

    drives = (dashboard.get("drives") or {}).get("rows") or []
    if not drives:
        return []
    reserve = dashboard.get("order_reserve") or {}
    lines = [
        "",
        "## 15 等待归因",
        "",
        "一行一次驱动会话。合法等待的两种理由由驱动事件流自己记；订单预留挡下的可合对来自"
        "台账事实，规划器尚未记录时如实为 0。占比的分母是该驱动的墙钟。",
        "",
        "| 驱动 | 墙钟秒 | legal_wait_energy 次/秒 | legal_wait_timer 次/秒 | "
        "睡眠占墙钟 | 订单预留挡下 |",
        "|---|---:|---|---|---:|---:|",
    ]
    for drive in drives:
        waits = drive.get("waits") or {}
        by_reason = waits.get("by_reason") or {}
        slept_by_reason = waits.get("slept_s_by_reason") or {}
        wall = drive.get("wall_seconds") or 0.0
        share = (drive.get("waited_s") or 0.0) / wall if wall else None
        lines.append(
            "| `{drive}` | {wall} | {energy} | {timer} | {share} | {reserve} |".format(
                drive=drive.get("drive_id"),
                wall=_fmt(wall, 1),
                energy=(
                    f"{by_reason.get('legal_wait_energy', 0)} / "
                    f"{_fmt(slept_by_reason.get('legal_wait_energy'), 1)}"
                ),
                timer=(
                    f"{by_reason.get('legal_wait_timer', 0)} / "
                    f"{_fmt(slept_by_reason.get('legal_wait_timer'), 1)}"
                ),
                share=_pct(share),
                reserve=len(
                    [
                        row
                        for row in reserve.get("rounds") or []
                        if row  # counted library-wide; per drive when recorded
                    ]
                )
                if reserve.get("samples")
                else 0,
            )
        )
    lines.extend(
        [
            "",
            f"全库订单预留挡下的可合对 {reserve.get('samples', 0)} 例"
            + (
                "（规划器目前不把被预留挡下的对写进 blocked_actions，所以这一栏为 0"
                "，不是没有发生过）。"
                if not reserve.get("samples")
                else "。"
            ),
        ]
    )
    return lines


def _render_progress_rate(dashboard: dict[str, Any]) -> list[str]:
    pooled = (dashboard.get("drives") or {}).get("pooled") or {}
    ceiling = pooled.get("ceiling") or {}
    play = pooled.get("per_hour_play") or {}
    wall = pooled.get("per_hour_wall") or {}
    seconds = ceiling.get("recovery_seconds_per_energy")
    limit = ceiling.get("generates_per_hour")
    lines = [
        "",
        "## 8 推进速率（能量受限口径）",
        "",
        "两列是同一批推进量除以两种时间：**有效游玩墙钟**排除等体力的睡眠，**全墙钟**含睡眠。"
        "体力是硬约束，所以只有全墙钟那一列可以和理论上限比。",
        "",
        "| 每小时 | 有效游玩墙钟 | 全墙钟 |",
        "|---|---:|---:|",
        f"| confirmed generate | {_fmt(play.get('confirmed_generates'), 2)} | "
        f"{_fmt(wall.get('confirmed_generates'), 2)} |",
        f"| 交付 | {_fmt(play.get('confirmed_deliveries'), 2)} | "
        f"{_fmt(wall.get('confirmed_deliveries'), 2)} |",
        f"| 主线任务 | {_fmt(play.get('main_tasks'), 2)} | {_fmt(wall.get('main_tasks'), 2)} |",
        "",
        "| 理论上限 | 值 |",
        "|---|---:|",
        f"| EnergyModel 恢复秒数 / 点 | {_fmt(seconds)} |",
        f"| 每小时可支付的生成上限 = 3600 / 恢复秒数 | {_fmt(limit, 2)} |",
        f"| 实测占上限（全墙钟） | {_pct(pooled.get('ceiling_share_wall'))} |",
        f"| 实测占上限（有效游玩墙钟） | {_pct(pooled.get('ceiling_share_play'))} |",
    ]
    if ceiling.get("missing"):
        lines.extend(["", f"上限读不到：{ceiling['missing']}。"])
    else:
        lines.extend(
            [
                "",
                f"恢复秒数取自 `{ceiling.get('product')}` 的 "
                "`facts.constants.DefaultRecoveryDuration`（真源 "
                f"`{ceiling.get('source')}`），本包不写死。有效游玩墙钟那一列会超过上限："
                "睡眠期间攒下的体力都在恢复后的短段里花掉，所以只有全墙钟口径回答得了"
                "「能量到底限住了多少」。",
            ]
        )
    return lines


def _render_progress(dashboard: dict[str, Any]) -> list[str]:
    """Section 9: the hourly curve and the days the projection says are left."""

    progress = dashboard.get("progress") or {}
    projection = progress.get("projection") or {}
    inputs_used = projection.get("inputs") or {}
    observed = projection.get("observed") or {}
    lines = [
        "",
        "## 9 进度与预计",
        "",
        f"当前主线 **{progress.get('current_main_task') or '—'}**。曲线的每一行是驱动报告的 "
        "`progress_curve`，预计天数是 F4-08 projection 的原样转载：公式、输入和它读的配置行都在下面，"
        "本仪表不另算一个数。",
        "",
        "| 驱动 | 小时 | 段 | 回合 | confirmed | 体力支出 | 体力回复 | 交付 | 主线 | 游玩秒 | 睡眠秒 | 金币 | 等级 | xp | 主线终态 | 在手订单 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for row in progress.get("curve") or []:
        lines.append(
            f"| `{row['drive_id']}` | {_fmt(row.get('hour'))} | {_fmt(row.get('segments'))} | "
            f"{_fmt(row.get('rounds'))} | {_fmt(row.get('confirmed'))} | "
            f"{_fmt(row.get('energy_spent'))} | {_fmt(row.get('energy_recovered'), 1)} | "
            f"{_fmt(row.get('deliveries'))} | {_fmt(row.get('main_tasks_completed'))} | "
            f"{_fmt(row.get('played_s'), 1)} | {_fmt(row.get('waited_s'), 1)} | "
            f"{_fmt(row.get('coins_end'))} | {_fmt(row.get('level_end'))} | "
            f"{_fmt(row.get('xp_end'))} | {row.get('main_task_end') or '—'} | "
            f"{_fmt(row.get('open_orders_end'))} |"
        )
    missing = progress.get("drives_without_curve") or []
    if missing:
        lines.extend(
            [
                "",
                "没有每小时曲线的驱动：",
                "",
                "| 驱动 | 段 | 原因 |",
                "|---|---:|---|",
            ]
        )
        for row in missing:
            lines.append(f"| `{row['drive_id']}` | {row['segments']} | {row['reason']} |")
    if not projection:
        lines.extend(["", "预计天数：feasibility 里没有 projection 产物，本节留空。"])
        return lines
    lines.extend(
        [
            "",
            f"预计天数（来源 `{progress.get('projection_source')}`）：",
            "",
            "| 里程碑 | 主线 | 任务数（含） | 需要金币 | 还差金币 | 预计天数 | 到达时等级 |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in progress.get("milestones") or []:
        lines.append(
            f"| {row['milestone']} | {row['task'] or '—'} | {_fmt(row.get('tasks_inclusive'))} | "
            f"{_fmt(row.get('coins_needed') or row.get('cost'))} | {_fmt(row.get('coins_short'))} | "
            f"{_fmt(row.get('days'), 3)} | {_fmt(row.get('level_on_arrival'))} |"
        )
    lines.extend(
        [
            "",
            "| 公式与输入 | 值 |",
            "|---|---|",
            f"| 公式 | `{projection.get('formula')}` |",
            f"| 体力回复秒数 | {_fmt(inputs_used.get('energy_recovery_seconds'))} |",
            f"| 每天体力 | {_fmt(projection.get('energy_per_day'), 1)} |",
            f"| 每点体力换金币（实测） | {_fmt(observed.get('coins_per_energy'), 3)} |",
            f"| 每天金币 | {_fmt(projection.get('coins_per_day'), 2)} |",
            f"| 实测样本 | {_fmt(observed.get('reports_used'))} 份报告、"
            f"花掉体力 {_fmt(observed.get('energy_spent_confirmed'))}、"
            f"交付金币 {_fmt(observed.get('delivery_coins_confirmed'))} |",
            f"| 配置真源 | {'、'.join(str(item) for item in inputs_used.get('config_refs') or []) or '—'} |",
            "",
            "长跑状态："
            f"{progress.get('long_run_status') or '多日曲线尚未产出'}。",
        ]
    )
    return lines


def _render_comparison(dashboard: dict[str, Any]) -> list[str]:
    """Section 10: the A/B table, verbatim."""

    comparison = dashboard.get("comparison") or {}
    lines = ["", "## 10 等待粒度 A/B 对比", ""]
    if not comparison.get("available"):
        lines.append(str(comparison.get("reason") or "没有可用的 A/B 产物。"))
        return lines
    arms = comparison.get("arms") or {}
    names = sorted(arms)
    lines.extend(
        [
            f"下表逐行转载 `{comparison['source']}` 的对比表，不重算。"
            "主口径是**花掉的体力**：体力只买生成、合成免费，只看 confirmed 会得出相反结论。",
            "",
            "| 臂 | 阈值 | 墙钟秒 | 段 | 花掉体力 | confirmed | 交付 | 睡眠占比 | replay MiB |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for name in names:
        arm = arms[name] if isinstance(arms[name], dict) else {}
        lines.append(
            f"| {name} | {_fmt(arm.get('resume_energy_threshold')) if arm.get('resume_energy_threshold') is not None else '默认'} | "
            f"{_fmt(arm.get('wall_seconds'), 1)} | {_fmt(arm.get('segments'))} | "
            f"{_fmt(arm.get('energy_spent'))} | {_fmt(arm.get('confirmed'))} | "
            f"{_fmt(arm.get('confirmed_deliveries'))} | {_pct(arm.get('sleep_share'))} | "
            f"{_fmt(arm.get('replay_mib'), 2)} |"
        )
    lines.extend(
        [
            "",
            "| 指标 | " + " | ".join(names) + " | 后/前 |",
            "|---|" + "---:|" * (len(names) + 1),
        ]
    )
    for row in comparison.get("table") or []:
        if not isinstance(row, dict):
            continue
        cells = " | ".join(_fmt(row.get(name), 4) for name in names)
        lines.append(
            f"| {row.get('metric')} | {cells} | "
            f"{_fmt(row.get('ratio_second_over_first'), 4)} |"
        )
    verdict = comparison.get("verdict") or {}
    threshold = comparison.get("threshold") or {}
    lines.extend(
        [
            "",
            "| 裁定与默认阈值 | 值 |",
            "|---|---|",
            f"| 胜出 | {verdict.get('winner') or '—'} |",
            f"| 各臂墙钟秒 | {_fmt(verdict.get('wall_clock_each_s'))} |",
            f"| 各臂体力收入 | {_fmt(verdict.get('energy_income_each_arm'), 1)} |",
            f"| 当前默认阈值 | {_fmt(threshold.get('default_today'))} |",
            f"| 阈值公式 | `{threshold.get('default_formula') or '—'}` |",
            f"| 阈值输入来源 | {threshold.get('both_read_from') or '—'} |",
            f"| 唤醒规则 | {threshold.get('wake_rule') or '—'} |",
            f"| 裁定来源 | `{comparison.get('verdict_source') or '—'}` |",
        ]
    )
    if verdict.get("caveat"):
        lines.extend(["", f"保留意见：{verdict['caveat']}"])
    return lines


def _render_free_claims(dashboard: dict[str, Any]) -> list[str]:
    """Section 11: every claim and close on record, judged."""

    audit = dashboard.get("free_claims") or {}
    lines = [
        "",
        "## 11 free-claim 审计",
        "",
        f"判据：{audit.get('criterion')}。登记真源 `{audit.get('navigation_product')}`，"
        f"共 {audit.get('surfaces_registered', 0)} 个已登记界面。"
        "close 只按登记的关闭点执行，不进这道判据，列出来是为了让每次弹窗动作都可追。"
        "时刻由报告自己的观测时间戳把帧计数标定到 UTC 得出，报告没有观测时留空。",
        "",
        "| 事件 | 合规 | 标红 | 其中口径生效前 / 生效后 | 带存档差分的领取 |",
        "|---|---:|---:|---:|---:|",
        f"| {audit.get('events', 0)}（领取 {audit.get('claims', 0)} · 关闭 {audit.get('closes', 0)}） | "
        f"{audit.get('compliant', 0)} | {audit.get('flagged', 0)} | "
        f"{audit.get('flagged_before_policy', 0)} / {audit.get('flagged_after_policy', 0)} | "
        f"{audit.get('claims_with_save_diff', 0)} / {audit.get('claims', 0)} |",
        "",
        f"存档差分改读台账事实（`{audit.get('ledger_source')}`）；旧的报告扫描保留为对照列，"
        "两边逐条比对，差异非 0 就逐条列出，不做调和。",
        "",
        "| 来源 | 事件 | 有测量窗口 | 可归因 | 与另一边的差异 |",
        "|---|---:|---:|---:|---:|",
        f"| 台账事实 | {audit.get('ledger_events', 0)} | {audit.get('ledger_measured', 0)} | "
        f"{audit.get('ledger_attributable', 0)} | {audit.get('ledger_scan_difference_count', 0)} |",
        f"| 旧报告扫描（对照） | {audit.get('scan_events', 0)} | "
        f"{audit.get('claims_with_save_diff', 0)} | — | "
        f"{audit.get('ledger_scan_difference_count', 0)} |",
        "",]
    differences = audit.get("ledger_scan_differences") or []
    if differences:
        lines.extend(
            [
                f"两边不一致 {len(differences)} 条：",
                "",
                "| 动作 id | 界面 | 差异 | 台账差分 | 旧扫描差分 |",
                "|---|---|---|---|---|",
            ]
        )
        for entry in differences:
            lines.append(
                f"| `{entry.get('action_id')}` | `{entry.get('screen')}` | "
                f"{entry.get('reason')} | {entry.get('ledger_delta')} | "
                f"{entry.get('scan_delta')} |"
            )
        lines.append("")
    else:
        lines.extend(["两边逐条一致，差异 0 条。", ""])
    lines.extend(
        [
            "| 时刻 | 类型 | 界面 | 元素 | 存档差分（台账） | 判定 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in audit.get("rows") or []:
        verdict = row.get("verdict")
        diff = row.get("save_diff") or {}
        ledger = row.get("ledger") or {}
        if ledger.get("measured"):
            # F7-04 section 2.1: the ledger fact is the source; the scan column
            # above says whether the two ever disagree.
            delta = ledger.get("delta") or {}
            shown = "、".join(f"{name} {value:+d}" for name, value in sorted(delta.items()))
            diff_text = shown or "无变化"
        elif diff.get("available"):
            delta = diff.get("delta") or {}
            shown = "、".join(
                f"{name} {value:+d}" if isinstance(value, int) else f"{name} {value:+.2f}"
                for name, value in delta.items()
            )
            diff_text = shown or "无变化"
        else:
            diff_text = f"缺：{diff.get('reason') or '未记录'}"
        if verdict is None:
            judged = "close-only，不适用领取判据"
        elif verdict["compliant"]:
            judged = "合规"
        else:
            judged = "**标红** " + "；".join(verdict["reasons"])
        lines.append(
            f"| {row.get('at') or '—'} | {row.get('kind')} | `{row.get('screen')}` | "
            f"`{row.get('element')}` | {diff_text} | {judged} |"
        )
    rows = audit.get("flagged_rows") or []
    if rows:
        lines.extend(
            [
                "",
                f"标红逐条（{len(rows)} 条）。口径要求这一栏为 0；不为 0 就是登记或策略的缺口，"
                "按判据如实标，不改判。"
                f"「口径生效前」= 发生在 {audit.get('policy_effective_at')} 之前，"
                f"即 {audit.get('policy_source')} 之前的 F4-03 / F4-06 常驻登记时期；"
                "那一栏为「是」的不算违规，只是说明它按当时的登记执行、事后才被新口径覆盖。",
                "",
                "| 时刻 | 界面 | 元素 | 标红原因 | 口径生效前 | 时期判定依据 | 报告 |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        for row in rows:
            era = row.get("before_policy")
            lines.append(
                f"| {row.get('at') or '—'} | `{row.get('screen')}` | `{row.get('element')}` | "
                f"{'；'.join((row.get('verdict') or {}).get('reasons') or [])} | "
                f"{'是' if era is True else ('否' if era is False else '未知')} | "
                f"{row.get('era_basis') or '—'} | `{row.get('report')}` |"
            )
        lines.extend(
            [
                "",
                f"其中口径生效前 {audit.get('flagged_before_policy', 0)} 条、"
                f"生效后 {audit.get('flagged_after_policy', 0)} 条、"
                f"时期不可判定 {audit.get('flagged_era_unknown', 0)} 条。",
            ]
        )
    counts = audit.get("flagged_by_reason") or {}
    if counts:
        lines.extend(["", "| 标红原因 | 条数 |", "|---|---:|"])
        for reason, count in counts.items():
            lines.append(f"| {reason} | {count} |")
    return lines


def _render_save_read(dashboard: dict[str, Any]) -> list[str]:
    """Section 12: the read-back, staged and per run."""

    trend = dashboard.get("save_read") or {}
    lines = [
        "",
        "## 12 存档回读时延",
        "",
        "只有少数产物记过分阶段（flush / pull / decrypt / decode / cleanup）；每个有回合计时的 run "
        "都记了它们加起来的 `read_after`。两张表分开列，缺的阶段留空。",
        "",
        "### 12.1 分阶段（含 F1-04 实机探针）",
        "",
        "| 时间 | 来源 | 类型 | 剖面 | 样本 | flush 秒 | pull 毫秒 | decrypt 毫秒 | decode 毫秒 | cleanup 毫秒 | 合计秒 | pull adb 调用 | 快路径 | 派生密钥缓存 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in trend.get("staged") or []:
        stages = row.get("stages") or {}
        flags = row.get("flags") or {}
        lines.append(
            f"| {str(row.get('at') or '')[:19].replace('T', ' ') or '—'} | `{row.get('source')}` | "
            f"{row.get('kind')} | {row.get('profile') or '—'} | {row.get('samples')} | "
            f"{_fmt(stages.get('flush_s'), 3)} | {_fmt(stages.get('pull_ms'), 1)} | "
            f"{_fmt(stages.get('decrypt_ms'), 1)} | {_fmt(stages.get('decode_ms'), 2)} | "
            f"{_fmt(stages.get('cleanup_ms'), 2)} | {_fmt(row.get('total_s_p50'), 3)} | "
            f"{_flag(flags.get('pull_adb_calls'))} | {_flag(flags.get('pull_fast_path'))} | "
            f"{_flag(flags.get('derived_key_cached'))} |"
        )
    lines.extend(
        [
            "",
            f"其中 F1-04 实机探针 {trend.get('live_probe_rows', 0)} 行，按剖面分开列，不与常规 run 合并。",
            "",
            "### 12.2 逐 run 的 read_after 趋势",
            "",
            "| 时间 | run | 任务 | 驱动 | 回合 | read_after p50 秒 | p95 秒 |",
            "|---|---|---|---|---:|---:|---:|",
        ]
    )
    for row in trend.get("trend") or []:
        lines.append(
            f"| {str(row.get('at') or '')[:19].replace('T', ' ')} | `{_short(row['run_id'])}` | "
            f"{row.get('task') or '—'} | {row.get('drive_id') or '—'} | {_fmt(row.get('rounds'))} | "
            f"{_fmt(row.get('read_after_p50_s'), 4)} | {_fmt(row.get('read_after_p95_s'), 4)} |"
        )
    lines.extend(
        [
            "",
            "| 对照 | 值 | 来源 |",
            "|---|---:|---|",
            f"| 当前中位（最近 {trend.get('recent_window', 0)} 个有回合计时的 run） | "
            f"{_fmt(trend.get('read_after_median_recent_s'), 3)} 秒 | 本仪表逐 run 中位 |",
            f"| 全库中位 | {_fmt(trend.get('read_after_median_all_s'), 3)} 秒 | 本仪表逐 run 中位 |",
            f"| F4-05 基线 | {_fmt(trend.get('f4_05_baseline_s'), 3)} 秒 | "
            f"`{trend.get('f4_05_baseline_source') or '—'}` |",
            f"| F1-04 目标 | {_fmt(trend.get('f1_04_target_s'), 3)} 秒 | "
            f"`{trend.get('f1_04_target_source') or '—'}` |",
            f"| 当前中位 − F4-05 基线 | {_fmt(trend.get('recent_vs_baseline'), 3)} 秒 | 差值 |",
            f"| 当前中位达到 F1-04 目标 | {_fmt(trend.get('recent_meets_target'))} | 判定 |",
        ]
    )
    if trend.get("missing"):
        lines.extend(
            ["", f"读不到的对照线：{'、'.join(str(item) for item in trend['missing'])}。"]
        )
    return lines


def _flag(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, list):
        return "/".join(_fmt(item) for item in value)
    return _fmt(value)


def _render_action_validity(dashboard: dict[str, Any]) -> list[str]:
    """Section 16: the F7-05 two-way taxonomy, at segment, drive and day grain.

    This section is the full-library recompute the spec asks for: every drive
    and every day is re-derived from the run rows on each build, and the named
    segments of spec section 3 item 4 are listed one row each so the old
    (APM) and new (state-change rate, round-trip share) readings sit side by
    side.
    """

    drives = (dashboard.get("drives") or {}).get("rows") or []
    days = (dashboard.get("guardian") or {}).get("days") or []
    lines = [
        "",
        "## 16 动作有效性：状态变更 vs 界面往返（F7-05）",
        "",
        "口径（分类器是 `metrics/action_validity.py` 的纯函数，逐动作判定）："
        "① 裁定为 confirmed / confirmed_with_deviation 的 merge / generate / deliver / "
        "open_chest / free_claim / place_queue_item 算状态变更；② 动作前后存档差分非空"
        "（台账 `free_claim_delta` 的逐动作测量窗口）也算状态变更；③ 其余一律为界面往返——"
        "close / advance / follow / settle / navigation / wait 类意图、守卫关弹窗，"
        "以及一切 refuted 动作。confirmed 但意图不在 ① 列表内的动作（如 select-generator）"
        "按字面口径计入往返，逐段的计数留在 `runs.v1.jsonl` 的 "
        "`action_validity.unlisted_confirmed_intents` 里备查。",
        "",
        f"日报标红线：任一段界面往返占比 > {ROUNDTRIP_SHARE_RED_LINE * 100:.0f}% "
        "即标红该日并列出段名。APM 保留为参考列，不再是门；"
        f"新门为满体力段每小时状态变更动作数 ≥ {int(STATE_CHANGE_PER_HOUR_GATE)}"
        f"（{STATE_CHANGE_PER_HOUR_GATE_SOURCE}），见 4.6。",
        "",
        "### 16.1 每驱动",
        "",
        "| 驱动 | 任务 | 段 | 状态变更 | 界面往返 | 往返占比 | 状态变更/h（全墙钟） | 标红段 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for drive in drives:
        validity = drive.get("action_validity") or {}
        lines.append(
            f"| `{drive['drive_id']}` | {drive.get('task') or '—'} | "
            f"{drive.get('segments', 0)} | "
            f"{_fmt(validity.get('state_change_actions'))} | "
            f"{_fmt(validity.get('interface_roundtrip_actions'))} | "
            f"{_pct(validity.get('roundtrip_share'))} | "
            f"{_fmt(validity.get('state_change_per_hour'), 0)} | "
            f"{validity.get('segments_with_red_roundtrip', 0)} |"
        )
    pooled = (dashboard.get("drives") or {}).get("pooled") or {}
    pooled_validity = pooled.get("action_validity") or {}
    lines.extend(
        [
            "",
            f"驱动段合计：状态变更 {_fmt(pooled_validity.get('state_change_actions'))}、"
            f"界面往返 {_fmt(pooled_validity.get('interface_roundtrip_actions'))}、"
            f"往返占比 {_pct(pooled_validity.get('roundtrip_share'))}、"
            f"标红段 {pooled_validity.get('segments_with_red_roundtrip', 0)}。"
            "（仅含驱动段；含独立 run 的全库合计见第 1 节。）",
            "",
            "### 16.2 每日（含标红段名）",
            "",
            "| 日期 | 状态变更 | 界面往返 | 往返占比 | 状态变更/h | 标红 | 标红段 |",
            "|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for day in days:
        validity = day.get("action_validity") or {}
        flagged = day.get("roundtrip_red_segments") or []
        names = "、".join(
            f"`{item.get('drive_id')}` 段 {item.get('segment')}"
            f"（{_pct(item.get('roundtrip_share'))}）"
            for item in flagged
        )
        lines.append(
            f"| {day['day']} | {_fmt(validity.get('state_change_actions'))} | "
            f"{_fmt(validity.get('interface_roundtrip_actions'))} | "
            f"{_pct(validity.get('roundtrip_share'))} | "
            f"{_fmt(validity.get('state_change_per_hour'), 0)} | "
            f"{'**红**' if day.get('roundtrip_red') else _fmt(False)} | "
            f"{names or '—'} |"
        )
    lines.extend(
        [
            "",
            "### 16.3 点名段新旧口径对照",
            "",
            "spec 第 3.4 条点名的段逐行列出：02:46Z longrun3 重启段 A（历史最健康段）、"
            "F4-11 守护 S1 全部段、09-06 F4-08 A/B 两臂；另附 09-06 longrun3 原始驱动"
            "作已知空转参照（其 refuted_streak 段是新旧口径必须区分开的对照组）。"
            "每段的参考 APM 读自该段 run 报告。",
            "",
            "| 驱动 | 段 | 停机原因 | 参考 APM | 状态变更 | 界面往返 | 往返占比 | 状态变更/h |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    by_drive = {drive["drive_id"]: drive for drive in drives}
    apm_by_run = {
        row["run_id"]: row.get("effective_apm")
        for row in dashboard.get("timeline") or []
    }
    for drive_id, label in COMPARISON_DRIVES:
        drive = by_drive.get(drive_id)
        if drive is None:
            lines.append(f"| `{drive_id}`（{label}） | — | 驱动不在库中 | — | — | — | — | — |")
            continue
        lines.append(f"| **{label}** | | | | | | | |")
        for segment in (drive.get("action_validity") or {}).get("segments") or []:
            lines.append(
                f"| `{drive_id}` | {segment.get('segment') or '—'} | "
                f"{segment.get('stop_reason') or '—'} | "
                f"{_fmt(apm_by_run.get(segment.get('run_id')))} | "
                f"{_fmt(segment.get('state_change_actions'))} | "
                f"{_fmt(segment.get('interface_roundtrip_actions'))} | "
                f"{_pct(segment.get('roundtrip_share'))} | "
                f"{_fmt(segment.get('state_change_per_hour'), 0)} |"
            )
    return lines


def _render_missing_reasons(dashboard: dict[str, Any]) -> list[str]:
    payload = dashboard.get("missing_reasons") or {}
    rows = payload.get("rows") or []
    lines = [
        "",
        "## 13 缺失字段的原因分布",
        "",
        f"`runs.v1.jsonl` 共 {payload.get('runs', 0)} 行；下表按受影响的 run 数排序。"
        "每一项都是源头缺口，不是本仪表算不出来。",
        "",
        "| 字段路径 | run 数 | 占比 | 原因 | 例 |",
        "|---|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['path']}` | {row['runs']} | {_pct(row['share'])} | {row['reason']} | "
            f"`{_short(row['example_run_id'])}` |"
        )
    return lines


def dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
