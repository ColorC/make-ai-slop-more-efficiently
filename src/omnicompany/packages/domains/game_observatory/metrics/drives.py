"""Turn each unattended-driver event stream into one metrics row.

``rr_drive`` plays segment after segment and sleeps between them; the metrics
package used to see only the segments, each as an unrelated run.  This module
reads the driver's own event stream and restores the drive as an entity: how
long it played, how long it slept, why each segment ended, and what the play it
did manage was worth per hour of clock.

Everything is derived from the log the driver wrote.  Nothing here calls the
driver, the device or the reports it produced, and a log still being appended
to is read as far as it is written.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, get_args

from ..delegation.exhaustion import (
    DRIVER_EXIT_CODES,
    DriverStopReason,
    classify_segment_stop,
)
from .action_validity import ROUNDTRIP_SHARE_RED_LINE
from .sources import DRIVE_SCHEMA, DriveLog, Inputs

DRIVE_ROW_SCHEMA = "omnicompany.game-observatory.rr-drive-metrics.v1"

# The complete stop vocabulary, taken from the driver's own type so a reason
# added there cannot silently drop out of the histogram here.
STOP_REASONS: tuple[str, ...] = tuple(get_args(DriverStopReason))
LEGAL_WAIT_REASONS = ("legal_wait_energy", "legal_wait_timer")

# The resume gate from F7-02 section 3: after sleeping to energy_enough_at, the
# first generation of the next segment has to land.
RESUME_GATE_RATE = 0.95

# F4-08 section 3.3 as revised by its section 8: "playing is only waiting" is a
# run of consecutive segments that each ended on a legal energy wait and each
# spent at most this much energy.  Merges are free, so counting confirmed
# actions threw out exactly the segments the state describes; energy spent is
# what the revision put in its place.  A drive report that states its own
# thresholds wins over these; they are the fallback for a log with no report.
ENERGY_BOUND_SEGMENTS = 6
ENERGY_BOUND_MAX_ENERGY = 2

# The advance gate from F7-03 section 1: an energy-bound drive is judged on how
# much of the recovery clock it managed to spend, not on its action rate.
ADVANCE_GATE_ENERGY_SHARE = 0.90


def _epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iso(value: Any) -> str | None:
    """The log's own timestamps, in the one UTC spelling the rows use."""

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.isoformat().replace("+00:00", "Z")


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _run_row_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """What the segments' own metrics rows add up to for one drive."""

    generates = 0
    deliveries = 0
    main_tasks = 0
    run_bytes = 0
    bytes_known = 0
    for row in rows:
        business = row.get("business") or {}
        generates += _int((business.get("confirmed_actions") or {}).get("generate"))
        deliveries += _int(business.get("confirmed_deliveries"))
        main_tasks += _int(business.get("main_tasks_completed"))
        value = (row.get("data") or {}).get("run_dir_bytes")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            run_bytes += int(value)
            bytes_known += 1
    return {
        "confirmed_generates": generates,
        "confirmed_deliveries": deliveries,
        "main_tasks_completed": main_tasks,
        "run_dir_bytes": run_bytes,
        "segments_with_bytes": bytes_known,
    }


def _rates(counts: dict[str, Any], seconds: float | None) -> dict[str, float] | None:
    if seconds is None or seconds <= 0:
        return None
    hours = seconds / 3600.0
    return {
        "confirmed_generates": counts["confirmed_generates"] / hours,
        "confirmed_deliveries": counts["confirmed_deliveries"] / hours,
        "main_tasks": counts["main_tasks_completed"] / hours,
    }


class _DriveReader:
    """One pass over a drive log, keeping only what a row needs."""

    def __init__(self, log: DriveLog) -> None:
        self.log = log
        self.started_at: str | None = None
        self.last_at: str | None = None
        self.max_wall_clock_s: float | None = None
        self.segments: list[dict[str, Any]] = []
        self.failures: list[dict[str, Any]] = []
        self.waits: list[dict[str, Any]] = []
        self.open_wait: dict[str, Any] | None = None
        self.terminal: dict[str, Any] | None = None
        self.stop_taxonomy: dict[str, int] = {reason: 0 for reason in STOP_REASONS}
        # Segments that started after a legal energy wait, in log order.
        self.energy_resume_run_ids: list[str] = []

    def read(self) -> None:
        awaiting_energy_resume = False
        for event in self.log.events:
            name = event.get("event")
            timestamp = _iso(event.get("ts"))
            if timestamp is not None:
                self.last_at = timestamp
                if self.started_at is None:
                    self.started_at = timestamp
            if name == "drive_started":
                self.max_wall_clock_s = _float(event.get("max_wall_clock_s")) or None
            elif name == "segment_finished":
                self._segment_finished(event, awaiting_energy_resume)
                awaiting_energy_resume = False
            elif name in ("segment_failed", "segment_error"):
                self._segment_failed(event)
                awaiting_energy_resume = False
            elif name == "wait_started":
                self.open_wait = {
                    "reason": event.get("reason"),
                    "requested_s": _float(event.get("requested_s")),
                    "started_at": timestamp,
                    "until": _iso(event.get("until")),
                }
            elif name == "wait_finished":
                self.open_wait = None
                self.waits.append(
                    {
                        "reason": event.get("reason"),
                        "requested_s": _float(event.get("requested_s")),
                        "slept_s": _float(event.get("slept_s")),
                        "truncated_by_wall_clock": bool(
                            event.get("truncated_by_wall_clock")
                        ),
                    }
                )
                awaiting_energy_resume = event.get("reason") == "legal_wait_energy"
            elif name == "drive_stopped":
                self.terminal = {
                    "reason": event.get("reason"),
                    "at": timestamp,
                    "segment": event.get("segment"),
                }
                # rr_drive counts a segment's verdict where the segment ends;
                # these two it counts only here, so the histogram matches its
                # own report rather than double-counting.
                if event.get("reason") in ("no_progress", "max_wall_clock"):
                    self._count(str(event.get("reason")))

    def _count(self, reason: str) -> None:
        if reason in self.stop_taxonomy:
            self.stop_taxonomy[reason] += 1

    def _segment_finished(self, event: dict[str, Any], after_energy_wait: bool) -> None:
        stop_reason = event.get("stop_reason")
        verdict = (
            classify_segment_stop(str(stop_reason))
            if isinstance(stop_reason, str)
            else None
        )
        if verdict is not None:
            self._count(verdict)
        run_dir = event.get("run_dir")
        run_id = str(run_dir).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] if run_dir else None
        segment = {
            "segment": event.get("segment"),
            "run_id": run_id,
            "stop_reason": stop_reason,
            "driver_stop_reason": verdict,
            "rounds": _int(event.get("rounds")),
            "game_actions": _int(event.get("game_actions")),
            "confirmed": _int(event.get("confirmed")),
            "refuted": _int(event.get("refuted")),
            "wall_seconds": _float(event.get("wall_seconds")),
            "frames_written": _int(event.get("frames_written")),
            "frames_lost": _int(event.get("frames_lost")),
            "redline_violations": _int(event.get("redline_violations")),
            "input_restart_recoveries": _int(event.get("input_restart_recoveries")),
            "resumed_after_energy_wait": after_energy_wait,
            # rr_drive only started logging the energy a segment paid with the
            # F4-08 threshold work.  An older log has no answer, and 0 would
            # make every one of its segments look energy-bound by default.
            "energy_spent": (
                _int(event.get("energy_spent")) if "energy_spent" in event else None
            ),
            "energy_collected": (
                _int(event.get("energy_collected"))
                if "energy_collected" in event
                else None
            ),
            "confirmed_deliveries": _int(event.get("confirmed_deliveries")),
            "state_before": event.get("state_before")
            if isinstance(event.get("state_before"), dict)
            else None,
            "state_after": event.get("state_after")
            if isinstance(event.get("state_after"), dict)
            else None,
        }
        self.segments.append(segment)
        if after_energy_wait and run_id:
            self.energy_resume_run_ids.append(run_id)

    def _segment_failed(self, event: dict[str, Any]) -> None:
        if event.get("event") != "segment_error":
            # segment_failed is the diagnostic line; segment_error is the one
            # the driver acts on, and counting both would double the verdict.
            self.failures.append({"kind": "segment_failed", "segment": event.get("segment")})
            return
        self.failures.append(
            {
                "kind": "segment_error",
                "segment": event.get("segment"),
                "error": event.get("error"),
            }
        )
        self._count("blocked_unknown_surface")


def energy_bound_spans(
    segments: list[dict[str, Any]],
    *,
    segments_threshold: int = ENERGY_BOUND_SEGMENTS,
    max_energy_per_segment: int = ENERGY_BOUND_MAX_ENERGY,
) -> dict[str, Any]:
    """Runs of segments where the account could only wait for energy.

    A segment qualifies when the driver ended it on a legal energy wait and it
    spent no more than ``max_energy_per_segment`` points.  A span is a maximal
    run of qualifying segments; the state is entered once a span reaches
    ``segments_threshold``, and the span keeps growing after that.

    The second condition is energy, not confirmed actions: merges cost nothing,
    so a segment that paid one point to generate and then merged twice reads as
    three confirmed actions while being exactly the state this is looking for.
    """

    spans: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def close() -> None:
        if len(current) >= segments_threshold:
            spans.append(
                {
                    "from": current[0].get("segment"),
                    "to": current[-1].get("segment"),
                    "segments": len(current),
                    "from_index": current[0]["index"],
                    "to_index": current[-1]["index"],
                    "energy_spent": sum(item["energy_spent"] or 0 for item in current),
                }
            )
        current.clear()

    for index, segment in enumerate(segments, start=1):
        spent = segment["energy_spent"]
        qualifies = (
            segment["driver_stop_reason"] == "legal_wait_energy"
            and spent is not None
            and spent <= max_energy_per_segment
        )
        if qualifies:
            current.append({**segment, "index": index})
        else:
            close()
    close()
    bound_segments = sum(span["segments"] for span in spans)
    unmeasured = sum(1 for segment in segments if segment["energy_spent"] is None)
    return {
        "segments_without_energy_field": unmeasured,
        "criterion": (
            f"{segments_threshold} 段以上连续、每段以合法等体力结束且花掉的体力 "
            f"≤ {max_energy_per_segment} 点"
        ),
        "segments_threshold": segments_threshold,
        "max_energy_per_segment": max_energy_per_segment,
        "spans": spans,
        "segments": bound_segments,
        "entered": bool(spans),
        "entered_at_segment": spans[0]["from_index"] + segments_threshold - 1
        if spans
        else None,
        "share_of_segments": bound_segments / len(segments) if segments else None,
    }


def _energy_bound_thresholds(report_payload: dict[str, Any] | None) -> tuple[int, int]:
    """The thresholds the drive's own report declares, when it declares them."""

    declared = (report_payload or {}).get("energy_bound")
    if not isinstance(declared, dict):
        return ENERGY_BOUND_SEGMENTS, ENERGY_BOUND_MAX_ENERGY
    segments = declared.get("segments_threshold")
    energy = declared.get("max_confirmed_per_segment")
    return (
        int(segments) if isinstance(segments, int) and segments > 0 else ENERGY_BOUND_SEGMENTS,
        int(energy) if isinstance(energy, int) and energy >= 0 else ENERGY_BOUND_MAX_ENERGY,
    )


def _energy_economy(
    segments: list[dict[str, Any]], seconds: float | None, ceiling: dict[str, Any]
) -> dict[str, Any]:
    """What the drive paid for its progress, in the only currency that buys it.

    Coins are read from the first and last segment state the driver logged, so a
    drive whose segments never carried a state answers ``null`` rather than 0.
    """

    measured = [
        segment for segment in segments if segment["energy_spent"] is not None
    ]
    spent = sum(segment["energy_spent"] for segment in measured)
    collected = sum(segment["energy_collected"] or 0 for segment in measured)
    states = [
        segment[key]
        for segment in segments
        for key in ("state_before", "state_after")
        if isinstance(segment.get(key), dict) and "coins" in segment[key]
    ]
    coins_delta = None
    if states:
        first = states[0].get("coins")
        last = states[-1].get("coins")
        if isinstance(first, (int, float)) and isinstance(last, (int, float)):
            coins_delta = int(last) - int(first)
    hours = seconds / 3600.0 if isinstance(seconds, (int, float)) and seconds > 0 else None
    per_hour = spent / hours if hours and measured else None
    limit = ceiling.get("energy_per_hour")
    return {
        "energy_spent": spent if measured else None,
        "energy_collected": collected if measured else None,
        "segments_with_energy_field": len(measured),
        "segments_without_energy_field": len(segments) - len(measured),
        "energy_spent_per_hour": per_hour,
        "energy_ceiling_per_hour": limit,
        "energy_share_of_ceiling": (
            per_hour / limit
            if per_hour is not None and isinstance(limit, (int, float)) and limit
            else None
        ),
        "coins_delta": coins_delta,
        "coins_per_energy": (
            coins_delta / spent if coins_delta is not None and spent > 0 else None
        ),
    }


def _recovery(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """How often the input-dead ladder fired, and how often it held.

    A segment that restarted input and still finished on some other reason
    recovered; one that ended ``input_dead_unrecovered`` did not.
    """

    attempts = sum(segment["input_restart_recoveries"] for segment in segments)
    with_attempt = [
        segment for segment in segments if segment["input_restart_recoveries"] > 0
    ]
    unrecovered = [
        segment
        for segment in segments
        if segment["driver_stop_reason"] == "input_dead_unrecovered"
    ]
    succeeded = [
        segment
        for segment in with_attempt
        if segment["driver_stop_reason"] != "input_dead_unrecovered"
    ]
    return {
        "restart_attempts": attempts,
        "segments_with_attempt": len(with_attempt),
        "segments_recovered": len(succeeded),
        "segments_unrecovered": len(unrecovered),
        "success_rate": (
            len(succeeded) / len(with_attempt) if with_attempt else None
        ),
    }


def _resume_gate(
    run_ids: list[str], rows_by_run: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Did the first generation after each energy sleep actually land?"""

    confirmed = 0
    refuted = 0
    without_generate = 0
    without_row = 0
    for run_id in run_ids:
        row = rows_by_run.get(run_id)
        if row is None:
            without_row += 1
            continue
        verdict = (row.get("business") or {}).get("first_generate_confirmed")
        if verdict is True:
            confirmed += 1
        elif verdict is False:
            refuted += 1
        else:
            without_generate += 1
    judged = confirmed + refuted
    return {
        "resumes": len(run_ids),
        "confirmed": confirmed,
        "refuted": refuted,
        "resumes_without_generate": without_generate,
        "resumes_without_run_row": without_row,
        "rate": confirmed / judged if judged else None,
    }


def _action_validity(
    segments: list[dict[str, Any]],
    rows_by_run: dict[str, dict[str, Any]],
    wall_seconds: float | None,
) -> dict[str, Any]:
    """F7-05: the drive's segments added up in the two-way action taxonomy.

    Per-segment shares come from the run rows; a segment whose run never
    wrote a report carries no counts and is listed with nulls rather than
    dropped, so the row still says how many segments went unclassified.  The
    red flag is spec section 3 item 3: a share strictly above the line.
    """

    per_segment = []
    state_change = 0
    roundtrip = 0
    unclassified = 0
    for segment in segments:
        row = rows_by_run.get(segment["run_id"] or "")
        validity = (row or {}).get("action_validity") or {}
        sc = validity.get("state_change_actions")
        rt = validity.get("interface_roundtrip_actions")
        share = validity.get("roundtrip_share")
        if not isinstance(sc, (int, float)) or not isinstance(rt, (int, float)):
            unclassified += 1
        else:
            state_change += int(sc)
            roundtrip += int(rt)
        per_segment.append(
            {
                "segment": segment.get("segment"),
                "run_id": segment.get("run_id"),
                "stop_reason": segment.get("stop_reason"),
                "state_change_actions": sc,
                "interface_roundtrip_actions": rt,
                "roundtrip_share": share,
                "state_change_per_hour": validity.get("state_change_per_hour"),
                "roundtrip_red": (
                    share is not None and share > ROUNDTRIP_SHARE_RED_LINE
                ),
            }
        )
    total = state_change + roundtrip
    hours = wall_seconds / 3600.0 if isinstance(wall_seconds, (int, float)) else None
    return {
        "state_change_actions": state_change,
        "interface_roundtrip_actions": roundtrip,
        "roundtrip_share": roundtrip / total if total else None,
        "state_change_per_hour": (
            state_change / hours if hours and hours > 0 else None
        ),
        "segments_unclassified": unclassified,
        "segments_with_red_roundtrip": sum(
            1 for item in per_segment if item["roundtrip_red"]
        ),
        "segments": per_segment,
    }


def build_drive_row(
    log: DriveLog,
    rows_by_run: dict[str, dict[str, Any]],
    ceiling: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reader = _DriveReader(log)
    reader.read()

    started = _epoch(reader.started_at)
    ended = _epoch(reader.last_at)
    span = None if started is None or ended is None else round(ended - started, 3)
    waited = round(sum(wait["slept_s"] for wait in reader.waits), 3)
    # The accounting window closes at the start of a wait that has not finished:
    # a drive asleep right now has not been playing since that line was written.
    open_started = _epoch((reader.open_wait or {}).get("started_at"))
    window_end = open_started if open_started is not None else ended
    accounted = None if started is None or window_end is None else round(window_end - started, 3)
    play = None if accounted is None else round(accounted - waited, 3)

    segment_rows = [
        rows_by_run[segment["run_id"]]
        for segment in reader.segments
        if segment["run_id"] and segment["run_id"] in rows_by_run
    ]
    totals = _run_row_totals(segment_rows)
    segments = len(reader.segments)
    frames_written = sum(segment["frames_written"] for segment in reader.segments)
    frames_lost = sum(segment["frames_lost"] for segment in reader.segments)

    terminal_reason = (reader.terminal or {}).get("reason")
    report_path = log.report_path
    has_report = report_path.is_file()
    report_stop_taxonomy = None
    report_stop_reason = None
    payload: dict[str, Any] = {}
    if has_report:
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        if isinstance(payload, dict) and payload.get("schema") == DRIVE_SCHEMA:
            report_stop_taxonomy = payload.get("stop_taxonomy")
            report_stop_reason = payload.get("stop_reason")
            terminal_reason = terminal_reason or report_stop_reason

    counted = {
        reason: count for reason, count in reader.stop_taxonomy.items() if count
    }
    payload_or_none = payload if has_report and isinstance(payload, dict) else None
    threshold_segments, threshold_energy = _energy_bound_thresholds(payload_or_none)
    bound = energy_bound_spans(
        reader.segments,
        segments_threshold=threshold_segments,
        max_energy_per_segment=threshold_energy,
    )
    declared_bound = (payload_or_none or {}).get("energy_bound")
    progress_curve = (payload_or_none or {}).get("progress_curve")
    return {
        "schema": DRIVE_ROW_SCHEMA,
        "drive_id": log.drive_id,
        "task": log.task,
        "log": log.path.name,
        "report": report_path.name if has_report else None,
        # A drive killed from outside never writes its report.  The task column
        # says so rather than leaving the row looking like an ordinary one.
        "report_state": "written" if has_report else "externally_stopped_no_report",
        # A drive is still running when it neither logged a stop nor wrote the
        # report it writes on its way out.  The 134615Z drive stopped on a
        # segment error, which breaks the loop without a drive_stopped line.
        "running": reader.terminal is None and not has_report,
        "started_at": reader.started_at,
        "last_event_at": reader.last_at,
        "max_wall_clock_s": reader.max_wall_clock_s,
        "observed_span_s": span,
        "wall_seconds": accounted,
        "waited_s": waited,
        "play_seconds": play,
        "segments": segments,
        "segments_with_run_row": len(segment_rows),
        "rounds": sum(segment["rounds"] for segment in reader.segments),
        "game_actions": sum(segment["game_actions"] for segment in reader.segments),
        "confirmed": sum(segment["confirmed"] for segment in reader.segments),
        "refuted": sum(segment["refuted"] for segment in reader.segments),
        "redline_violations": sum(
            segment["redline_violations"] for segment in reader.segments
        ),
        "stop_taxonomy": dict(reader.stop_taxonomy),
        "stop_taxonomy_counted": counted,
        "terminal_stop_reason": terminal_reason,
        "exit_code": DRIVER_EXIT_CODES.get(str(terminal_reason)),
        "report_stop_reason": report_stop_reason,
        "report_stop_taxonomy": report_stop_taxonomy,
        "report_agrees": (
            None
            if report_stop_taxonomy is None
            else report_stop_taxonomy == counted and report_stop_reason == terminal_reason
        ),
        "waits": {
            "count": len(reader.waits),
            "requested_s": round(sum(wait["requested_s"] for wait in reader.waits), 3),
            "slept_s": waited,
            "truncated_by_wall_clock": sum(
                1 for wait in reader.waits if wait["truncated_by_wall_clock"]
            ),
            "by_reason": {
                reason: sum(1 for wait in reader.waits if wait["reason"] == reason)
                for reason in LEGAL_WAIT_REASONS
            },
            # F7-04 section 2.3.  Counting the waits was never enough to
            # attribute idle time: one energy wait can be worth twenty timer
            # waits, so the seconds are split by reason as well.
            "slept_s_by_reason": {
                reason: round(
                    sum(
                        wait["slept_s"]
                        for wait in reader.waits
                        if wait["reason"] == reason
                    ),
                    3,
                )
                for reason in LEGAL_WAIT_REASONS
            },
            "open": reader.open_wait,
        },
        "recovery": _recovery(reader.segments),
        "energy_bound": bound,
        # What the drive report declared under its own criterion, kept beside
        # the recomputation rather than overwritten: the two disagree by
        # construction after the F4-08 section 8 revision.
        "energy_bound_declared": declared_bound if isinstance(declared_bound, dict) else None,
        "energy_economy": _energy_economy(reader.segments, accounted, ceiling or {}),
        "progress_curve": progress_curve if isinstance(progress_curve, list) else None,
        "energy_resume": _resume_gate(reader.energy_resume_run_ids, rows_by_run),
        "action_validity": _action_validity(reader.segments, rows_by_run, accounted),
        "progress": totals,
        "per_hour_play": _rates(totals, play),
        "per_hour_wall": _rates(totals, accounted),
        "data": {
            "frames_written": frames_written,
            "frames_lost": frames_lost,
            "frames_per_segment": frames_written / segments if segments else None,
            "run_dir_bytes": totals["run_dir_bytes"],
            "bytes_per_segment": (
                totals["run_dir_bytes"] / totals["segments_with_bytes"]
                if totals["segments_with_bytes"]
                else None
            ),
        },
        "log_health": {
            "events": len(log.events),
            "malformed_lines": log.malformed_lines,
            "segment_failures": reader.failures,
        },
    }


def build_drives(rows: list[dict[str, Any]], inputs: Inputs) -> list[dict[str, Any]]:
    rows_by_run = {row["run_id"]: row for row in rows}
    ceiling = energy_ceiling(inputs)
    return [build_drive_row(log, rows_by_run, ceiling) for log in inputs.drive_logs]


def energy_ceiling(inputs: Inputs) -> dict[str, Any]:
    """The most generations an hour of clock can pay for, from the config.

    ``EnergyModel.DefaultRecoveryDuration`` is the whole ceiling: one point per
    that many seconds, and the cheapest generation costs one point.  The number
    is read from the design extraction's energy product, never restated here.
    """

    payload = inputs.energy_constants or {}
    constants = payload.get("constants") or {}
    seconds = constants.get("DefaultRecoveryDuration")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        return {
            "recovery_seconds_per_energy": None,
            "generates_per_hour": None,
            "energy_per_hour": None,
            "source": None,
            "product": payload.get("product"),
            "missing": "EnergyModel 恢复秒数读不到（design/energy-items.json 缺 facts.constants）",
        }
    return {
        "recovery_seconds_per_energy": seconds,
        "generates_per_hour": 3600.0 / float(seconds),
        # The same clock read as income: one point per recovery period is all
        # an hour of wall clock can ever hand over, whatever it is spent on.
        "energy_per_hour": 3600.0 / float(seconds),
        "source": payload.get("source"),
        "product": payload.get("product"),
        "missing": None,
    }


def _median(values: list[float]) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def advance_gate(drives: list[dict[str, Any]], pooled: dict[str, Any]) -> dict[str, Any]:
    """The energy-bound advance gate: did the drive spend the clock it was given?

    A run-level action rate says nothing once the account can only wait, so the
    gate asks two questions of each drive instead.  The first is absolute: of
    the energy an hour of recovery hands over, how much did the drive manage to
    spend.  The second is relative: what each spent point bought in coins,
    against the median of every drive that spent any, so a policy that burns the
    clock on nothing cannot pass on the first question alone.
    """

    economy = pooled.get("energy_economy") or {}
    median = economy.get("coins_per_energy_median")
    rows = []
    for drive in drives:
        values = drive["energy_economy"]
        share = values["energy_share_of_ceiling"]
        coins_per_energy = values["coins_per_energy"]
        rows.append(
            {
                "drive_id": drive["drive_id"],
                "energy_spent": values["energy_spent"],
                "energy_spent_per_hour": values["energy_spent_per_hour"],
                "energy_ceiling_per_hour": values["energy_ceiling_per_hour"],
                "energy_share_of_ceiling": share,
                "energy_share_met": (
                    None if share is None else share >= ADVANCE_GATE_ENERGY_SHARE
                ),
                "coins_per_energy": coins_per_energy,
                "coins_per_energy_median": median,
                "coins_per_energy_met": (
                    None
                    if coins_per_energy is None or median is None
                    else coins_per_energy >= median
                ),
                "energy_bound_segments": drive["energy_bound"]["segments"],
                "energy_bound_share": drive["energy_bound"]["share_of_segments"],
            }
        )
    judged = [row for row in rows if row["energy_share_met"] is not None]
    latest = judged[-1] if judged else None
    return {
        "line_energy_share": ADVANCE_GATE_ENERGY_SHARE,
        "rows": rows,
        "drives_considered": len(judged),
        "drives_meeting_energy_share": sum(
            1 for row in judged if row["energy_share_met"]
        ),
        "latest": latest,
        "coins_per_energy_median": median,
    }


def _pooled_energy_bound(drives: list[dict[str, Any]]) -> dict[str, Any]:
    """Energy-bound share over the segments the criterion could be applied to.

    Segments from a log written before the driver recorded ``energy_spent`` are
    not counted either way; the share names its own denominator so a library
    half of which cannot be judged does not read as half unbound.
    """

    bound = sum(drive["energy_bound"]["segments"] for drive in drives)
    total = sum(drive["segments"] for drive in drives)
    unmeasured = sum(
        drive["energy_bound"]["segments_without_energy_field"] for drive in drives
    )
    judged = total - unmeasured
    return {
        "segments": bound,
        "segments_total": total,
        "segments_judged": judged,
        "segments_without_energy_field": unmeasured,
        "drives_entered": sum(1 for drive in drives if drive["energy_bound"]["entered"]),
        "spans": sum(len(drive["energy_bound"]["spans"]) for drive in drives),
        "share_of_judged_segments": bound / judged if judged else None,
        "share_of_all_segments": bound / total if total else None,
    }


def _pooled_energy_economy(
    drives: list[dict[str, Any]], wall: float, ceiling: dict[str, Any]
) -> dict[str, Any]:
    measured = [
        drive for drive in drives if drive["energy_economy"]["energy_spent"] is not None
    ]
    spent = sum(drive["energy_economy"]["energy_spent"] for drive in measured)
    collected = sum(drive["energy_economy"]["energy_collected"] for drive in measured)
    return {
        "energy_spent": spent if measured else None,
        "energy_collected": collected if measured else None,
        "drives_with_energy_field": len(measured),
        "energy_spent_per_hour": (
            spent / (wall / 3600.0) if wall > 0 and measured else None
        ),
        "energy_ceiling_per_hour": ceiling.get("energy_per_hour"),
        "coins_per_energy_median": _median(
            [
                drive["energy_economy"]["coins_per_energy"]
                for drive in drives
                if drive["energy_economy"]["coins_per_energy"] is not None
            ]
        ),
    }


def _pooled_resume(drives: list[dict[str, Any]]) -> dict[str, Any]:
    confirmed = sum(drive["energy_resume"]["confirmed"] for drive in drives)
    refuted = sum(drive["energy_resume"]["refuted"] for drive in drives)
    judged = confirmed + refuted
    return {
        "resumes": sum(drive["energy_resume"]["resumes"] for drive in drives),
        "confirmed": confirmed,
        "refuted": refuted,
        "resumes_without_generate": sum(
            drive["energy_resume"]["resumes_without_generate"] for drive in drives
        ),
        "resumes_without_run_row": sum(
            drive["energy_resume"]["resumes_without_run_row"] for drive in drives
        ),
        "rate": confirmed / judged if judged else None,
    }


def _pooled_action_validity(drives: list[dict[str, Any]], wall: float) -> dict[str, Any]:
    """The two-way taxonomy added up over every drive in the library."""

    state_change = sum(drive["action_validity"]["state_change_actions"] for drive in drives)
    roundtrip = sum(
        drive["action_validity"]["interface_roundtrip_actions"] for drive in drives
    )
    total = state_change + roundtrip
    return {
        "state_change_actions": state_change,
        "interface_roundtrip_actions": roundtrip,
        "roundtrip_share": roundtrip / total if total else None,
        "state_change_per_hour": (
            state_change / (wall / 3600.0) if wall > 0 else None
        ),
        "segments_with_red_roundtrip": sum(
            drive["action_validity"]["segments_with_red_roundtrip"] for drive in drives
        ),
    }


def pool_drives(drives: list[dict[str, Any]], ceiling: dict[str, Any]) -> dict[str, Any]:
    """Every drive added together, plus the share of the ceiling reached."""

    totals = {
        "confirmed_generates": sum(
            drive["progress"]["confirmed_generates"] for drive in drives
        ),
        "confirmed_deliveries": sum(
            drive["progress"]["confirmed_deliveries"] for drive in drives
        ),
        "main_tasks_completed": sum(
            drive["progress"]["main_tasks_completed"] for drive in drives
        ),
        "run_dir_bytes": sum(drive["progress"]["run_dir_bytes"] for drive in drives),
        "segments_with_bytes": sum(
            drive["progress"]["segments_with_bytes"] for drive in drives
        ),
    }
    wall = sum(drive["wall_seconds"] or 0.0 for drive in drives)
    waited = sum(drive["waited_s"] for drive in drives)
    play = sum(drive["play_seconds"] or 0.0 for drive in drives)
    taxonomy = {reason: 0 for reason in STOP_REASONS}
    for drive in drives:
        for reason, count in drive["stop_taxonomy"].items():
            taxonomy[reason] = taxonomy.get(reason, 0) + count
    per_hour_play = _rates(totals, play or None)
    per_hour_wall = _rates(totals, wall or None)
    limit = ceiling.get("generates_per_hour")
    return {
        "drives": len(drives),
        "segments": sum(drive["segments"] for drive in drives),
        "wall_seconds": round(wall, 3),
        "waited_s": round(waited, 3),
        "play_seconds": round(play, 3),
        "waited_share": waited / wall if wall > 0 else None,
        "play_share": play / wall if wall > 0 else None,
        "stop_taxonomy": taxonomy,
        "totals": totals,
        "per_hour_play": per_hour_play,
        "per_hour_wall": per_hour_wall,
        "ceiling": ceiling,
        "ceiling_share_play": (
            per_hour_play["confirmed_generates"] / limit
            if per_hour_play and isinstance(limit, (int, float)) and limit
            else None
        ),
        "ceiling_share_wall": (
            per_hour_wall["confirmed_generates"] / limit
            if per_hour_wall and isinstance(limit, (int, float)) and limit
            else None
        ),
        "frames_written": sum(drive["data"]["frames_written"] for drive in drives),
        "frames_lost": sum(drive["data"]["frames_lost"] for drive in drives),
        "frames_per_segment": (
            sum(drive["data"]["frames_written"] for drive in drives)
            / sum(drive["segments"] for drive in drives)
            if sum(drive["segments"] for drive in drives)
            else None
        ),
        "bytes_per_segment": (
            totals["run_dir_bytes"] / totals["segments_with_bytes"]
            if totals["segments_with_bytes"]
            else None
        ),
        "energy_resume": _pooled_resume(drives),
        "action_validity": _pooled_action_validity(drives, wall),
        "energy_bound": _pooled_energy_bound(drives),
        "energy_economy": _pooled_energy_economy(drives, wall, ceiling),
        "recovery": {
            "restart_attempts": sum(
                drive["recovery"]["restart_attempts"] for drive in drives
            ),
            "segments_with_attempt": sum(
                drive["recovery"]["segments_with_attempt"] for drive in drives
            ),
            "segments_recovered": sum(
                drive["recovery"]["segments_recovered"] for drive in drives
            ),
            "segments_unrecovered": sum(
                drive["recovery"]["segments_unrecovered"] for drive in drives
            ),
        },
        "malformed_log_lines": sum(
            drive["log_health"]["malformed_lines"] for drive in drives
        ),
    }


__all__ = [
    "ADVANCE_GATE_ENERGY_SHARE",
    "DRIVE_ROW_SCHEMA",
    "ENERGY_BOUND_MAX_ENERGY",
    "ENERGY_BOUND_SEGMENTS",
    "LEGAL_WAIT_REASONS",
    "RESUME_GATE_RATE",
    "STOP_REASONS",
    "advance_gate",
    "build_drive_row",
    "build_drives",
    "energy_bound_spans",
    "energy_ceiling",
    "pool_drives",
]
