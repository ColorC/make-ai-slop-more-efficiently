"""Derive one metrics row per live run from its report and replay index entry.

Every number here is read or computed from the run's own products. A field the
sources cannot answer is written as ``null`` and its dotted path is recorded in
the row's ``missing`` map together with the reason.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from ..delegation.exhaustion import classify_segment_stop
from .action_validity import claim_diffs, report_action_validity
from .sources import (
    Citation,
    DriveAttribution,
    IndexEntry,
    Inputs,
    Report,
    write_if_changed,
)

ROW_SCHEMA = "omnicompany.game-observatory.rr-run-metrics.v1"

# F7-03 section 1.1: the action-rate gate only means anything on a segment that
# started with energy to spend and ran long enough to average.  A report may
# declare itself an APM window; none does yet, so these two derived conditions
# are what actually decide.
APM_WINDOW_MIN_ENERGY = 60
APM_WINDOW_MIN_ROUNDS = 30

# Round timing is recorded per layer; seconds are the dashboard's unit.
_LAYER_FIELDS: tuple[tuple[str, str, float], ...] = (
    ("screenshot", "screenshot_ms", 1000.0),
    ("read_before", "read_before_s", 1.0),
    ("plan", "plan_ms", 1000.0),
    ("act", "act_ms", 1000.0),
    ("read_after", "read_after_s", 1.0),
    ("verify", "verify_ms", 1000.0),
    ("settle", "foreground_settle_ms", 1000.0),
)
GUARD_LAYER = "guard"
LAYER_NAMES = tuple(name for name, _, _ in _LAYER_FIELDS) + (GUARD_LAYER,)

# Action buckets plan.md asks the dashboard to report, keyed by the intent
# prefix the actuator writes; the executed action kind is the fallback.
_INTENT_BUCKETS = {
    "merge": "merge",
    "generate": "generate",
    "select-generator": "select_generator",
    "deliver": "deliver",
    "start-main-task": "start_main_task",
    "select-chest": "open_chest",
    "open-chest": "open_chest",
    "clear-cobweb": "clear_cobweb",
    "sell": "sell",
    "collect": "collect",
    "wait": "wait",
}
ACTION_BUCKETS = (
    "merge",
    "generate",
    "select_generator",
    "deliver",
    "start_main_task",
    "open_chest",
    "clear_cobweb",
    "sell",
    "collect",
    "wait",
    "popup",
    "navigation",
    "other",
)


def percentile(values: Iterable[float], quantile: float) -> float | None:
    """Linear-interpolated percentile, the convention the run reports use."""

    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def normalise_time(value: Any) -> str | None:
    """One UTC spelling for timestamps the reports and the index disagree on.

    Reports write ``+00:00`` offsets, the replay index writes ``Z``; rows are
    sorted and compared on this field, so both become the same ``Z`` form.
    """

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


class _RowBuilder:
    """Collects the reasons behind every null the row carries."""

    def __init__(self) -> None:
        self.missing: dict[str, str] = {}

    def absent(self, path: str, reason: str) -> None:
        self.missing[path] = reason


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _rounds(report: Report) -> list[dict[str, Any]]:
    loop = report.payload.get("loop")
    rounds = loop.get("rounds") if isinstance(loop, dict) else None
    return [item for item in rounds or [] if isinstance(item, dict)]


def _round_metrics(report: Report) -> list[dict[str, Any]]:
    rows = report.payload.get("round_metrics")
    return [item for item in rows or [] if isinstance(item, dict)]


def _executed_actions(report: Report) -> list[dict[str, Any]]:
    actions = []
    for round_payload in _rounds(report):
        for item in round_payload.get("executed") or []:
            if isinstance(item, dict) and isinstance(item.get("action"), dict):
                actions.append(item)
    return actions


def _guard_results(report: Report) -> tuple[list[list[dict[str, Any]]], bool]:
    """Guard action results per round, and whether the schema records them."""

    per_round: list[list[dict[str, Any]]] = []
    recorded = False
    for round_payload in _rounds(report):
        if "screen_guard_action_results" not in round_payload:
            per_round.append([])
            continue
        recorded = True
        per_round.append(
            [item for item in round_payload["screen_guard_action_results"] or [] if isinstance(item, dict)]
        )
    return per_round, recorded


def _action_bucket(action: dict[str, Any]) -> str:
    intent = str(action.get("intent") or "")
    head = intent.split(":", 1)[0]
    bucket = _INTENT_BUCKETS.get(head)
    if bucket:
        return bucket
    kind = str(action.get("kind") or "")
    return _INTENT_BUCKETS.get(kind.replace("_", "-"), "other")


def _action_composition(report: Report, guard_counts: int) -> dict[str, int]:
    composition = {bucket: 0 for bucket in ACTION_BUCKETS}
    for item in _executed_actions(report):
        composition[_action_bucket(item["action"])] += 1
    composition["popup"] = guard_counts
    return composition


def _predicted_buckets(round_payload: dict[str, Any]) -> dict[str, str]:
    """prediction_id -> action bucket, for one round's executed actions."""

    buckets: dict[str, str] = {}
    for item in round_payload.get("executed") or []:
        action = item.get("action") if isinstance(item, dict) else None
        if not isinstance(action, dict):
            continue
        prediction = action.get("prediction")
        prediction_id = prediction.get("prediction_id") if isinstance(prediction, dict) else None
        if isinstance(prediction_id, str):
            buckets[prediction_id] = _action_bucket(action)
    return buckets


def _adjudications(round_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in round_payload.get("adjudication") or [] if isinstance(item, dict)]


def _confirmed_actions(report: Report) -> dict[str, int]:
    """Actions whose prediction the save channel confirmed, by bucket.

    ``action_composition`` counts what the loop executed; this counts what the
    game agreed happened.  The energy-bound progress rate is a confirmed-only
    figure, so the two are kept apart rather than conflated.
    """

    counts = {bucket: 0 for bucket in ACTION_BUCKETS}
    for round_payload in _rounds(report):
        buckets = _predicted_buckets(round_payload)
        for adjudication in _adjudications(round_payload):
            # F4-11 section 2: a deviation is an action the game agreed
            # happened, in a shape the plan did not name.  This counts what the
            # game did, so it belongs here; the deviation count that says how
            # often the plan was wrong about the shape is reported separately.
            if adjudication.get("status") not in {"confirmed", "confirmed_with_deviation"}:
                continue
            bucket = buckets.get(adjudication.get("prediction_id"))
            if bucket is not None:
                counts[bucket] += 1
    return counts


def first_generate_confirmed(report: Report) -> bool | None:
    """Was the run's first generation confirmed?  ``None`` if it never generated.

    After a wait for energy this is the question that decides whether the
    resume was real: the driver slept to ``energy_enough_at`` and the very next
    generation either landed or the recovery clock was wrong.
    """

    for round_payload in _rounds(report):
        statuses = {
            item.get("prediction_id"): item.get("status")
            for item in _adjudications(round_payload)
        }
        for item in round_payload.get("executed") or []:
            action = item.get("action") if isinstance(item, dict) else None
            if not isinstance(action, dict) or _action_bucket(action) != "generate":
                continue
            prediction = action.get("prediction")
            prediction_id = (
                prediction.get("prediction_id") if isinstance(prediction, dict) else None
            )
            if prediction_id is None or prediction_id not in statuses:
                continue
            return statuses[prediction_id] in {"confirmed", "confirmed_with_deviation"}
    return None


def layer_seconds(report: Report) -> dict[str, list[float]]:
    samples: dict[str, list[float]] = {name: [] for name in LAYER_NAMES}
    for metrics in _round_metrics(report):
        timing = metrics.get("timing")
        if not isinstance(timing, dict):
            continue
        for name, key, divisor in _LAYER_FIELDS:
            value = _number(timing.get(key))
            if value is not None:
                samples[name].append(value / divisor)
    guard_rounds, guard_recorded = _guard_results(report)
    if guard_recorded:
        # A round with no guard action really did spend nothing on the guard
        # layer, so it contributes a zero rather than dropping out.
        for results in guard_rounds:
            total = sum(
                _number((item.get("timing") or {}).get("total_ms")) or 0.0 for item in results
            )
            samples[GUARD_LAYER].append(total / 1000.0)
    return samples


def _layer_percentiles(report: Report, builder: _RowBuilder) -> dict[str, dict[str, float] | None]:
    samples = layer_seconds(report)
    _, guard_recorded = _guard_results(report)
    layers: dict[str, dict[str, float] | None] = {}
    for name in LAYER_NAMES:
        values = samples[name]
        if not values:
            layers[name] = None
            if name == GUARD_LAYER and not guard_recorded:
                builder.absent(
                    "efficiency.layers_s.guard",
                    "报告 schema 不记录 screen_guard_action_results，无守卫层耗时",
                )
            elif name == "settle":
                builder.absent(
                    "efficiency.layers_s.settle",
                    "该 run 的回合计时里没有 foreground_settle_ms 字段",
                )
            else:
                builder.absent(
                    f"efficiency.layers_s.{name}",
                    "报告没有 round_metrics 分层计时",
                )
            continue
        layers[name] = {"p50": percentile(values, 0.5), "p95": percentile(values, 0.95)}
    return layers


def _coins(report: Report) -> tuple[int | None, int | None]:
    acceptance = report.payload.get("acceptance") or {}
    initial = _number(acceptance.get("coins_initial"))
    final = _number(acceptance.get("coins_final"))
    if initial is not None and final is not None:
        return int(initial), int(final)
    metrics = _round_metrics(report)
    pairs = [row.get("coins") for row in metrics if isinstance(row.get("coins"), list)]
    pairs = [pair for pair in pairs if len(pair) == 2]
    if not pairs:
        return None, None
    return int(pairs[0][0]), int(pairs[-1][1])


def _energy(report: Report) -> tuple[int | None, int | None]:
    pairs = [
        row.get("energy")
        for row in _round_metrics(report)
        if isinstance(row.get("energy"), list) and len(row.get("energy")) == 2
    ]
    if not pairs:
        return None, None
    return int(pairs[0][0]), int(pairs[-1][1])


def _level(report: Report) -> tuple[int | None, int | None]:
    levels = []
    for round_payload in _rounds(report):
        for key in ("state_before", "state_after"):
            state = round_payload.get(key)
            if isinstance(state, dict):
                value = _number(state.get("level"))
                if value is not None:
                    levels.append(int(value))
    if not levels:
        return None, None
    return levels[0], levels[-1]


def _names_a_main_task(record: dict[str, Any]) -> bool:
    """Whether a status record is about a main task at all."""

    if "status" not in record:
        return False
    return any(
        isinstance(record.get(key), dict) and "main_task_id" in record[key]
        for key in ("before", "after")
    )


def main_task_evidence(report: Report) -> tuple[list[dict[str, Any]], str | None]:
    """Every main-task attempt the report records, and where it was read from.

    The live loop writes a list under ``main_task_evidence``; the recovery
    schema writes a single attempt of the same shape under ``result``.  Both
    are main-task evidence, so the count is taken from whichever the report
    actually carries instead of from the loop rounds alone.
    """

    payload = report.payload
    evidence = payload.get("main_task_evidence")
    if isinstance(evidence, dict):
        return [evidence], "main_task_evidence"
    if isinstance(evidence, list):
        # An empty list is the report saying the run attempted no main task,
        # which is an answer; only a report carrying neither field is a gap.
        return [item for item in evidence if isinstance(item, dict)], "main_task_evidence"
    result = payload.get("result")
    if isinstance(result, dict) and _names_a_main_task(result):
        return [result], "result"
    return [], None


def _main_tasks_completed(report: Report) -> int:
    items, _ = main_task_evidence(report)
    return sum(1 for item in items if item.get("status") == "completed")


def _safety(report: Report, builder: _RowBuilder, popup_actions: int) -> dict[str, Any]:
    payload = report.payload
    acceptance = payload.get("acceptance") or {}
    audit = payload.get("tap_preframe_redline_audit")
    forbidden: int | None = None
    violations: int | None = None
    if isinstance(audit, dict):
        forbidden = _number(audit.get("forbidden_surface_pre_frames"))
        forbidden = int(forbidden) if forbidden is not None else None
        if isinstance(audit.get("violations"), list):
            violations = len(audit["violations"])
    if violations is None and isinstance(acceptance.get("redline_violations"), list):
        violations = len(acceptance["redline_violations"])
    declared_clear = acceptance.get("redline_clear")
    declared_clear = declared_clear if isinstance(declared_clear, bool) else None
    if violations is None and declared_clear is True:
        violations = 0
    if forbidden is None:
        builder.absent(
            "safety.forbidden_surface_pre_frames",
            "报告没有 tap_preframe_redline_audit，无禁入界面前帧计数",
        )
    if violations is None:
        builder.absent("safety.redline_violations", "报告既无审计违规清单也无 redline_clear 判定")
    loop = payload.get("loop") or {}
    return {
        "forbidden_surface_pre_frames": forbidden,
        "redline_violations": violations,
        "redline_clear": declared_clear if violations is None else violations == 0,
        "popup_actions": popup_actions,
        "unknown_screen_stop": loop.get("stop_reason") == "unknown_screen",
    }


_EMPTY_INTEGRITY = {
    "records": None,
    "written": None,
    "lost": None,
    "files": None,
    "index_frames": None,
    "consistent": None,
}


def _frames_integrity(entry: IndexEntry | None, builder: _RowBuilder) -> dict[str, Any]:
    """Check the frame records against the files the run actually left behind.

    ``frames.ndjson`` records one line per frame the run attempted, each marked
    ``written`` or ``lost``, and the index counts every record. So the file
    count answers to the written records alone; comparing it against the record
    total would report a run that honestly logged a dropped frame as damaged.
    """

    if entry is None:
        builder.absent("data.frames_ndjson", "没有 replay 索引条目，无法核对帧记录")
        return dict(_EMPTY_INTEGRITY)
    index_frames = _number((entry.payload.get("counts") or {}).get("frames"))
    index_count = int(index_frames) if index_frames is not None else None
    run_dir = entry.run_dir
    ndjson = run_dir / "frames.ndjson"
    if not ndjson.is_file():
        builder.absent("data.frames_ndjson", "run 目录已不在磁盘上（可能已被配额回收）")
        return {**_EMPTY_INTEGRITY, "index_frames": index_count}
    records = 0
    written = 0
    lost = 0
    for line in ndjson.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records += 1
        try:
            status = json.loads(line).get("status")
        except json.JSONDecodeError:
            continue
        if status == "written":
            written += 1
        elif status == "lost":
            lost += 1
    frames_dir = run_dir / "frames"
    files = sum(1 for _ in frames_dir.iterdir()) if frames_dir.is_dir() else 0
    return {
        "records": records,
        "written": written,
        "lost": lost,
        "files": files,
        "index_frames": index_count,
        "consistent": files == written and index_count == records,
    }


def _data(report: Report | None, entry: IndexEntry | None, builder: _RowBuilder) -> dict[str, Any]:
    integrity = _frames_integrity(entry, builder)
    written: int | None = None
    lost: int | None = None
    if report is not None:
        frames = (report.payload.get("replay") or {}).get("frames")
        if isinstance(frames, dict):
            written = _number(frames.get("written"))
            lost = _number(frames.get("lost"))
    # The frame records are the run's own account of what it captured, so they
    # stand in wherever the report stayed silent.
    if written is None:
        written = integrity["written"]
    if lost is None:
        lost = integrity["lost"]
    if lost is None:
        builder.absent(
            "data.frames_lost", "报告没有 replay.frames.lost，帧记录也读不到，无法判断丢帧"
        )
    run_bytes = entry.bytes_total if entry is not None else None
    if run_bytes is None:
        builder.absent("data.run_dir_bytes", "没有 replay 索引条目，无法读取 run 目录大小")
    return {
        "frames_written": int(written) if written is not None else None,
        "frames_lost": int(lost) if lost is not None else None,
        "run_dir_bytes": run_bytes,
        "replay_root": str(entry.root.name) if entry is not None else None,
        "frames_ndjson": integrity,
    }


def resolve_task(
    report: Report | None,
    attribution: DriveAttribution | None,
) -> tuple[str | None, str | None]:
    """The task a run belongs to, and the evidence that says so.

    In order: what the report calls itself, then the drive that ran it as a
    segment, then the report's file name.  rr_play names its file after the
    policy, so an unattended F4-07 drive fills the directory with files called
    ``F4-04-<ts>.json``; the file name is a last resort, never the first word.
    """

    if report is not None:
        declared, source = report.declared_task
        if declared is not None:
            return declared, source
    if attribution is not None and attribution.task is not None:
        return attribution.task, "drive"
    if report is not None:
        return report.file_task, "report_filename"
    return None, None


def _identity(
    report: Report | None,
    entry: IndexEntry | None,
    citations: list[Citation],
    attribution: DriveAttribution | None,
    builder: _RowBuilder,
) -> dict[str, Any]:
    payload = report.payload if report is not None else {}
    loop = payload.get("loop") or {}
    index_payload = entry.payload if entry is not None else {}
    started = normalise_time(loop.get("started_at") or index_payload.get("started_at"))
    ended = normalise_time(loop.get("ended_at") or index_payload.get("ended_at"))
    rounds = len(_rounds(report)) if report is not None else None
    if rounds is None:
        summary_rounds = _number((index_payload.get("summary") or {}).get("rounds"))
        rounds = int(summary_rounds) if summary_rounds is not None else None
        if rounds is None:
            builder.absent("identity.rounds", "既无 run 报告也无索引摘要记录回合数")
    stop_reason = loop.get("stop_reason")
    if stop_reason is None:
        builder.absent("identity.stop_reason", "报告没有 loop.stop_reason")
    task, task_source = resolve_task(report, attribution)
    if attribution is not None and stop_reason is None:
        builder.absent(
            "identity.driver_stop_reason",
            "该 run 是驱动的一段，但报告没有 loop.stop_reason，无法翻译成驱动停机原因",
        )
    return {
        "task": task,
        "task_source": task_source,
        "report": report.name if report is not None else None,
        "report_schema": payload.get("schema"),
        "producer": index_payload.get("producer"),
        "replay_store": entry.root.name if entry is not None else None,
        "drive_id": attribution.drive_id if attribution is not None else None,
        "segment_index": attribution.segment_index if attribution is not None else None,
        "segment_label": attribution.segment_label if attribution is not None else None,
        "driver_stop_reason": (
            classify_segment_stop(str(stop_reason))
            if attribution is not None and isinstance(stop_reason, str)
            else None
        ),
        "backend": payload.get("backend"),
        "policy": payload.get("policy"),
        "started_at": started,
        "ended_at": ended,
        "wall_seconds": _number(loop.get("wall_seconds")),
        "rounds": rounds,
        "requested_rounds": _number(payload.get("requested_rounds")),
        "stop_reason": stop_reason,
        "end_reason": index_payload.get("end_reason"),
        "cited_by": [
            {"report": citation.report, "role": citation.role, "label": citation.label}
            for citation in citations
        ],
    }


def apm_window(
    report: Report | None,
    energy_start: int | None,
    rounds: int | None,
    stop_reason: Any = None,
    retest_declared_by: str | None = None,
) -> dict[str, Any]:
    """Whether a run's action rate is evidence about the ceiling or about energy.

    A run that started on an empty bar spends most of its clock waiting and
    reports an action rate that measures the recovery clock, not the loop.  The
    gate therefore only counts runs that began satiated, ran long enough to
    average, and did not end on a legal wait: a segment the driver stopped
    because the bar ran out spent part of its own clock unable to act, so its
    rate is about energy however it started.

    ``retest_declared_by`` is the aggregate report that named this run as its
    formal APM retest.  That is the same statement ``apm_window: true`` would
    make, one level up, and it is recorded rather than merged into the derived
    verdict so the gate can show the designated retest as its own row.
    """

    declared = report.payload.get("apm_window") if report is not None else None
    declared = declared if isinstance(declared, bool) else None
    legal_wait = isinstance(stop_reason, str) and str(
        classify_segment_stop(stop_reason) or ""
    ).startswith("legal_wait")
    satiated = (
        isinstance(energy_start, int)
        and energy_start >= APM_WINDOW_MIN_ENERGY
        and isinstance(rounds, int)
        and rounds >= APM_WINDOW_MIN_ROUNDS
    )
    qualifies = declared if declared is not None else (satiated and not legal_wait)
    if declared is not None:
        basis = "report.apm_window"
    elif not satiated:
        basis = "起点体力或回合数不足"
    elif legal_wait:
        basis = "该段以合法等待结束，速率量的是回复时钟"
    else:
        basis = f"起点体力 ≥ {APM_WINDOW_MIN_ENERGY}、回合 ≥ {APM_WINDOW_MIN_ROUNDS}、非合法等待结束"
    return {
        "declared": declared,
        "energy_start": energy_start,
        "rounds": rounds,
        "stop_reason": stop_reason if isinstance(stop_reason, str) else None,
        "ended_on_legal_wait": legal_wait,
        "satiated_start": satiated,
        "retest_declared_by": retest_declared_by,
        "qualifies": qualifies,
        "basis": basis,
    }


def _efficiency(report: Report | None, builder: _RowBuilder) -> dict[str, Any]:
    if report is None:
        builder.absent("efficiency", "该 run 只有索引条目，没有报告可以推导效率指标")
        return {"effective_apm": None, "round_wall_s": None, "layers_s": None}
    loop = report.payload.get("loop") or {}
    apm = _number(loop.get("effective_apm"))
    if apm is None:
        builder.absent("efficiency.effective_apm", "报告没有 loop.effective_apm")
    totals = [
        _number((row.get("timing") or {}).get("total_s"))
        for row in _round_metrics(report)
    ]
    totals = [value for value in totals if value is not None]
    if totals:
        round_wall = {"p50": percentile(totals, 0.5), "p95": percentile(totals, 0.95)}
    else:
        round_wall = None
        builder.absent("efficiency.round_wall_s", "报告没有 round_metrics 回合墙钟")
    return {
        "effective_apm": apm,
        "round_wall_s": round_wall,
        "layers_s": _layer_percentiles(report, builder),
    }


def _business(report: Report | None, builder: _RowBuilder) -> dict[str, Any]:
    if report is None:
        builder.absent("business", "该 run 只有索引条目，没有报告可以推导业务指标")
        return {}
    loop = report.payload.get("loop") or {}
    acceptance = report.payload.get("acceptance") or {}
    guard_rounds, guard_recorded = _guard_results(report)
    popup_actions = sum(len(results) for results in guard_rounds)
    coins_initial, coins_final = _coins(report)
    energy_start, energy_end = _energy(report)
    level_start, level_end = _level(report)
    deliveries = _number(acceptance.get("confirmed_deliveries"))
    if deliveries is None:
        builder.absent("business.confirmed_deliveries", "报告没有 acceptance.confirmed_deliveries")
    if coins_initial is None:
        builder.absent("business.coins", "报告既无 acceptance 金币起止也无 round_metrics 金币")
    if energy_start is None:
        builder.absent("business.energy", "报告没有 round_metrics 体力起止")
    if level_start is None:
        builder.absent("business.level", "报告的回合状态里没有等级字段")
    if not guard_recorded:
        builder.absent(
            "business.action_composition.popup",
            "报告 schema 不记录 screen_guard_action_results，弹窗动作数按 0 计不成立",
        )
    evidence, evidence_source = main_task_evidence(report)
    if evidence_source is None and (report.payload.get("acceptance") or {}).get(
        "main_task_enabled"
    ):
        builder.absent(
            "business.main_task_evidence",
            "该 run 开了主线但报告既无 main_task_evidence 也无 result，完成数按 0 计不成立",
        )
    return {
        "action_composition": _action_composition(report, popup_actions),
        "confirmed_actions": _confirmed_actions(report),
        "first_generate_confirmed": first_generate_confirmed(report),
        "game_actions": _number(loop.get("game_actions")),
        "confirmed_predictions": _number(loop.get("confirmed_predictions")),
        "deviation_predictions": _number(loop.get("deviation_predictions")),
        "deviation_rate": _number(loop.get("deviation_rate")),
        "refuted_predictions": _number(loop.get("refuted_predictions")),
        "refuted_rate": _number(loop.get("refuted_rate")),
        "confirmed_deliveries": int(deliveries) if deliveries is not None else None,
        "coins": {
            "initial": coins_initial,
            "final": coins_final,
            "delta": None if coins_initial is None or coins_final is None else coins_final - coins_initial,
        },
        "energy": {"start": energy_start, "end": energy_end},
        "level": {"start": level_start, "end": level_end},
        "main_tasks_completed": _main_tasks_completed(report),
        "main_task_attempts": len(evidence),
        "main_task_evidence_source": evidence_source,
    }


def _crossref(report: Report | None, inputs: Inputs, builder: _RowBuilder) -> dict[str, Any]:
    if report is None:
        builder.absent("crossref", "该 run 没有报告，事实流无法归属")
        return {"facts_added": None, "coverage_rate_delta": None}
    delta = report.payload.get("crossref_delta")
    coverage_delta = None
    if isinstance(delta, dict):
        coverage_delta = _number(
            (delta.get("coverage") or {}).get("field_coverage_rate_delta")
        )
    if coverage_delta is None:
        builder.absent(
            "crossref.coverage_rate_delta",
            "报告没有被 crossref run-hook 盖过 crossref_delta，对账率变化无法逐 run 还原",
        )
    facts_added: int | None = inputs.fact_counts.get(report.name, 0)
    captured_at = report.payload.get("captured_at")
    if (
        not facts_added
        and isinstance(captured_at, str)
        and inputs.fact_horizon
        and captured_at > inputs.fact_horizon
    ):
        facts_added = None
        builder.absent(
            "crossref.facts_added",
            f"事实流最新只索引到 {inputs.fact_horizon}，这份报告更晚，0 不能当作没有事实",
        )
    return {
        "facts_added": facts_added,
        "coverage_rate_delta": coverage_delta,
    }


def _per_hour(
    identity: dict[str, Any],
    business: dict[str, Any],
    data: dict[str, Any],
    builder: _RowBuilder,
) -> dict[str, Any] | None:
    """plan.md asks for progress and data growth per hour, not just per run."""

    wall = identity.get("wall_seconds")
    if not isinstance(wall, (int, float)) or wall <= 0:
        builder.absent("efficiency.per_hour", "报告没有 loop.wall_seconds，无法折算每小时速率")
        return None
    hours = wall / 3600.0
    coins_delta = (business.get("coins") or {}).get("delta")
    run_bytes = data.get("run_dir_bytes")
    return {
        "main_tasks": (business.get("main_tasks_completed") or 0) / hours,
        "game_actions": (business.get("game_actions") or 0) / hours,
        "confirmed_deliveries": (business.get("confirmed_deliveries") or 0) / hours,
        "coins_delta": None if coins_delta is None else coins_delta / hours,
        "replay_mb": None if run_bytes is None else run_bytes / 2**20 / hours,
    }


def _action_validity(
    report: Report | None,
    identity: dict[str, Any],
    inputs: Inputs,
    builder: _RowBuilder,
) -> dict[str, Any]:
    """F7-05: the segment's actions split into state changes and round-trips.

    The counts come from the report alone; the per-hour rate additionally
    needs the wall clock, so a run whose report omits it keeps the counts and
    carries the rate as null with the reason in ``missing``.
    """

    if report is None:
        builder.absent(
            "action_validity", "该 run 只有索引条目，没有报告可以分类动作有效性"
        )
        return {
            "state_change_actions": None,
            "interface_roundtrip_actions": None,
            "classified_actions": None,
            "roundtrip_share": None,
            "state_change_per_hour": None,
            "unlisted_confirmed_intents": None,
        }
    diffs = claim_diffs(inputs.crossref_facts.get("free_claim_delta") or [])
    validity = report_action_validity(report.payload, diffs)
    wall = identity.get("wall_seconds")
    per_hour: float | None = None
    if isinstance(wall, (int, float)) and not isinstance(wall, bool) and wall > 0:
        per_hour = validity["state_change_actions"] / (wall / 3600.0)
    else:
        builder.absent(
            "action_validity.state_change_per_hour",
            "报告没有 loop.wall_seconds，每小时状态变更动作数无法折算",
        )
    return {**validity, "state_change_per_hour": per_hour}


def build_row(
    run_id: str,
    report: Report | None,
    entry: IndexEntry | None,
    inputs: Inputs,
) -> dict[str, Any]:
    builder = _RowBuilder()
    business = _business(report, builder)
    popup_actions = (business.get("action_composition") or {}).get("popup", 0)
    attribution = inputs.drive_attribution.get(run_id)
    identity = _identity(
        report, entry, inputs.citations.get(run_id, []), attribution, builder
    )
    row = {
        "schema": ROW_SCHEMA,
        "run_id": run_id,
        "identity": identity,
        "efficiency": _efficiency(report, builder),
        "business": business,
        "action_validity": _action_validity(report, identity, inputs, builder),
        "safety": _safety(report, builder, popup_actions)
        if report is not None
        else {
            "forbidden_surface_pre_frames": None,
            "redline_violations": None,
            "redline_clear": None,
            "popup_actions": None,
            "unknown_screen_stop": None,
        },
        "data": _data(report, entry, builder),
        "model": {"llm_calls": None, "llm_tokens": None},
        "crossref": _crossref(report, inputs, builder),
    }
    row["efficiency"]["per_hour"] = _per_hour(
        row["identity"], business, row["data"], builder
    )
    row["efficiency"]["apm_window"] = apm_window(
        report,
        (business.get("energy") or {}).get("start"),
        row["identity"].get("rounds"),
        row["identity"].get("stop_reason"),
        inputs.apm_retests.get(run_id),
    )
    if report is None:
        builder.absent("safety", "该 run 只有索引条目，没有报告可以推导安全指标")
    builder.absent(
        "model.llm_calls",
        "本管线的规划器不调用模型，报告也不记录调用数，因此不写 0 冒充实测",
    )
    builder.absent("model.llm_tokens", "同上：没有任何报告记录 token 用量")
    row["missing"] = dict(sorted(builder.missing.items()))
    return row


def _sort_key(row: dict[str, Any]) -> tuple[str, str]:
    started = row["identity"].get("started_at") or ""
    return (str(started), row["run_id"])


def collect_runs(inputs: Inputs) -> list[dict[str, Any]]:
    """One row per run, ordered by start time."""

    reports_by_run: dict[str, Report] = {}
    for report in inputs.reports:
        run_id = report.run_id
        if run_id and run_id not in reports_by_run:
            reports_by_run[run_id] = report
    run_ids = set(reports_by_run) | set(inputs.index_entries)
    rows = [
        build_row(run_id, reports_by_run.get(run_id), inputs.index_entries.get(run_id), inputs)
        for run_id in sorted(run_ids)
    ]
    return sorted(rows, key=_sort_key)


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    """One sorted JSON object per line, rewritten only when the bytes change."""

    text = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )
    write_if_changed(path, text)
