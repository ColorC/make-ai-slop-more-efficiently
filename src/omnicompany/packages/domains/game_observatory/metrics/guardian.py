"""Multi-day unattended guardian: one row per calendar day.

F6-05 section 3.2.  The guardian (F4-10) runs sessions back to back, adopts a
driver report someone else started, writes a daily report per calendar day, and
stops without restarting whenever the stop reason needs a person.  Three
questions follow from that and none of them had a place on the dashboard: how
much of each day the driver was actually up, what stopped it, and which days
have no guardian report at all.

Everything here is read, never inferred:

* the day's sessions, wall clock, playing and waiting time come from the drive
  rows the dashboard already builds, grouped by the UTC date the drive started;
* escalations -- the stops the guardian refused to restart through -- come from
  the guardian reports themselves;
* a day with drive activity but no file under ``records/daily`` is reported as
  an absence, because "the guardian was not watching" is a finding.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .sources import Inputs

GUARDIAN_SCHEMA = "omnicompany.game-observatory.rr-multi-day-guardian.v1"

# F6-05 section 3.3.  Twenty hours of driver uptime out of twenty-four is the
# bar the unattended line is aiming at.  A day before the guardian existed is
# not measured against it: nothing was supposed to restart the driver then.
DAILY_EFFECTIVE_HOURS_GATE = 20.0

# The header line rr_guardian writes at the top of every daily report.  It is
# the only line parsed out of the markdown, and a report that does not match it
# still counts as present -- the number simply reads as unknown.
_DAILY_HEADER = re.compile(
    r"生成于\s*(?P<generated_at>\S+?)；会话\s*(?P<sessions>\d+)\s*段，"
    r"墙钟\s*(?P<wall_h>[\d.]+)\s*h（其中等待\s*(?P<wait_h>[\d.]+)\s*h）"
)


def _day(timestamp: Any) -> str | None:
    if not isinstance(timestamp, str) or not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _read_daily_reports(directory: Path) -> dict[str, dict[str, Any]]:
    """Every ``<date>.md`` the guardian wrote, with its own header read back."""

    if not directory.is_dir():
        return {}
    reports: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.md")):
        day = path.stem
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        entry: dict[str, Any] = {
            "path": path.as_posix(),
            "generated_at": None,
            "sessions_declared": None,
            "wall_hours_declared": None,
            "wait_hours_declared": None,
        }
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            reports[day] = entry
            continue
        match = _DAILY_HEADER.search(text)
        if match is not None:
            entry["generated_at"] = match.group("generated_at")
            entry["sessions_declared"] = int(match.group("sessions"))
            entry["wall_hours_declared"] = float(match.group("wall_h"))
            entry["wait_hours_declared"] = float(match.group("wait_h"))
        entry["cold_start_arms"] = _read_cold_start_arms(text)
        reports[day] = entry
    return reports


# F7-04 section 2.4.  The cold-start comparison rides on the guardian: one arm
# per session, tabulated on the day page.  A day page written before the arms
# existed simply has no such section, which reads as "not rotated" and not as
# a zero.
_ARM_SECTION = re.compile(r"^## 冷启臂\s*$(?P<body>.*?)(?=^## |\Z)", re.M | re.S)
_ARM_ROW = re.compile(
    r"^\|\s*(?P<arm>[^|]+?)\s*\|\s*(?P<sessions>\d+)\s*\|\s*(?P<predictions>\d+)\s*\|"
    r"\s*(?P<refused>\d+)\s*\|",
    re.M,
)


def _read_cold_start_arms(text: str) -> dict[str, Any]:
    """The per-arm table from one day page, or an explicit absence."""

    section = _ARM_SECTION.search(text)
    if section is None:
        return {"present": False, "rotated": False, "arms": {}}
    body = section.group("body")
    arms: dict[str, dict[str, Any]] = {}
    for row in _ARM_ROW.finditer(body):
        name = row.group("arm")
        if name in {"臂", "---"}:
            continue
        predictions = int(row.group("predictions"))
        refused = int(row.group("refused"))
        arms[name] = {
            "sessions": int(row.group("sessions")),
            "predictions": predictions,
            "refused": refused,
            "first_round_refused_rate": (
                refused / predictions if predictions else None
            ),
        }
    return {"present": True, "rotated": bool(arms), "arms": arms}


def _guardian_sessions(inputs: Inputs) -> tuple[list[dict[str, Any]], list[str]]:
    """Flatten every guardian report into per-session rows, newest last."""

    sessions: list[dict[str, Any]] = []
    report_paths: list[str] = []
    for report in inputs.reports_by_schema(GUARDIAN_SCHEMA):
        payload = report.payload
        report_paths.append(report.name)
        for session in payload.get("sessions") or []:
            if not isinstance(session, dict):
                continue
            detail = session.get("detail") or {}
            sessions.append(
                {
                    "label": session.get("label"),
                    "shape": session.get("shape"),
                    "adopted": bool(session.get("adopted")),
                    "disposition": session.get("disposition"),
                    "drive_stop_reason": session.get("drive_stop_reason"),
                    "segment_stop_reason": (
                        detail.get("segment_stop_reason")
                        if isinstance(detail, dict)
                        else None
                    ),
                    "started_at": payload.get("started_at"),
                }
            )
    return sessions, [name for name in report_paths if name]


def build_guardian(inputs: Inputs, drives: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-day unattended coverage, plus the days no guardian report covers."""

    daily_dir = inputs.paths.get("daily")
    reports = _read_daily_reports(daily_dir) if isinstance(daily_dir, Path) else {}
    sessions, guardian_reports = _guardian_sessions(inputs)

    days: dict[str, dict[str, Any]] = {}
    for drive in drives:
        day = _day(drive.get("started_at"))
        if day is None:
            continue
        entry = days.setdefault(
            day,
            {
                "day": day,
                "drives": 0,
                "wall_seconds": 0.0,
                "play_seconds": 0.0,
                "waited_seconds": 0.0,
                "stop_reasons": {},
                "terminal_stop_reasons": {},
                "first_started_at": drive.get("started_at"),
                "last_started_at": drive.get("started_at"),
                "state_change_actions": 0,
                "interface_roundtrip_actions": 0,
                "roundtrip_red_segments": [],
            },
        )
        entry["drives"] += 1
        entry["wall_seconds"] += float(drive.get("wall_seconds") or 0.0)
        entry["play_seconds"] += float(drive.get("play_seconds") or 0.0)
        entry["waited_seconds"] += float(drive.get("waited_s") or 0.0)
        # F7-05 section 3 items 2-3: the day's two-way action split, and the
        # segments whose round-trip share crosses the red line -- one such
        # segment flags the whole day, and the day names them.
        validity = drive.get("action_validity") or {}
        entry["state_change_actions"] += int(
            validity.get("state_change_actions") or 0
        )
        entry["interface_roundtrip_actions"] += int(
            validity.get("interface_roundtrip_actions") or 0
        )
        for segment in validity.get("segments") or []:
            if not segment.get("roundtrip_red"):
                continue
            entry["roundtrip_red_segments"].append(
                {
                    "drive_id": drive.get("drive_id"),
                    "segment": segment.get("segment"),
                    "roundtrip_share": segment.get("roundtrip_share"),
                    "stop_reason": segment.get("stop_reason"),
                }
            )
        # The per-drive histogram the driver itself counted, segment by
        # segment, plus the reason the whole drive ended on.
        for reason, count in (drive.get("stop_taxonomy_counted") or {}).items():
            entry["stop_reasons"][str(reason)] = entry["stop_reasons"].get(
                str(reason), 0
            ) + int(count or 0)
        terminal = drive.get("terminal_stop_reason")
        if terminal:
            entry["terminal_stop_reasons"][str(terminal)] = (
                entry["terminal_stop_reasons"].get(str(terminal), 0) + 1
            )
        started = drive.get("started_at")
        if isinstance(started, str):
            if not entry["first_started_at"] or started < entry["first_started_at"]:
                entry["first_started_at"] = started
            if not entry["last_started_at"] or started > entry["last_started_at"]:
                entry["last_started_at"] = started

    # Escalations are the stops the guardian refused to drive through.  They are
    # counted on the day the guardian session that raised them started.
    for session in sessions:
        day = _day(session.get("started_at"))
        if day is None:
            continue
        entry = days.setdefault(
            day,
            {
                "day": day,
                "drives": 0,
                "wall_seconds": 0.0,
                "play_seconds": 0.0,
                "waited_seconds": 0.0,
                "stop_reasons": {},
                "terminal_stop_reasons": {},
                "first_started_at": None,
                "last_started_at": None,
            },
        )
        entry.setdefault("guardian_sessions", 0)
        entry["guardian_sessions"] += 1
        if session.get("disposition") == "escalate":
            entry.setdefault("escalations", []).append(
                {
                    "label": session.get("label"),
                    "drive_stop_reason": session.get("drive_stop_reason"),
                    "segment_stop_reason": session.get("segment_stop_reason"),
                }
            )

    rows: list[dict[str, Any]] = []
    for day in sorted(days):
        entry = days[day]
        wall_hours = round(entry["wall_seconds"] / 3600.0, 3)
        report = reports.get(day)
        state_change = int(entry.get("state_change_actions") or 0)
        roundtrip = int(entry.get("interface_roundtrip_actions") or 0)
        classified = state_change + roundtrip
        red_segments = entry.get("roundtrip_red_segments") or []
        rows.append(
            {
                **entry,
                "wall_seconds": round(entry["wall_seconds"], 3),
                "play_seconds": round(entry["play_seconds"], 3),
                "waited_seconds": round(entry["waited_seconds"], 3),
                "effective_hours": wall_hours,
                "play_hours": round(entry["play_seconds"] / 3600.0, 3),
                "action_validity": {
                    "state_change_actions": state_change,
                    "interface_roundtrip_actions": roundtrip,
                    "roundtrip_share": (
                        roundtrip / classified if classified else None
                    ),
                    "state_change_per_hour": (
                        state_change / wall_hours if wall_hours > 0 else None
                    ),
                },
                "roundtrip_red_segments": red_segments,
                # Spec section 3 item 3: one segment over the line reddens the
                # day; the segment list above is the name list the rule asks
                # for.
                "roundtrip_red": bool(red_segments),
                "guardian_sessions": entry.get("guardian_sessions", 0),
                "escalations": entry.get("escalations", []),
                "daily_report": report,
                "guardian_absent": report is None,
                # F7-04 section 2.4.  The twenty-hour bar, scored on the day
                # itself.  A day with no guardian report is not scored at all:
                # nothing was supposed to restart the driver then.
                "effective_hours_gate": (
                    None if report is None else wall_hours >= DAILY_EFFECTIVE_HOURS_GATE
                ),
                "cold_start_arms": (report or {}).get("cold_start_arms")
                or {"present": False, "rotated": False, "arms": {}},
                "stop_reasons": dict(sorted(entry["stop_reasons"].items())),
                "terminal_stop_reasons": dict(
                    sorted(entry["terminal_stop_reasons"].items())
                ),
            }
        )

    # A day between the first and the last with driver activity but no daily
    # report is a gap in coverage, not a missing row.
    covered = {row["day"] for row in rows}
    gaps: list[str] = []
    if rows:
        first = date.fromisoformat(rows[0]["day"])
        last = date.fromisoformat(rows[-1]["day"])
        step = first
        while step <= last:
            key = step.isoformat()
            if key not in covered and key not in reports:
                gaps.append(key)
            step += timedelta(days=1)

    first_report_day = min(reports) if reports else None
    measured = [
        row
        for row in rows
        if first_report_day is not None and row["day"] >= first_report_day
    ]
    passing = [
        row for row in measured if row["effective_hours"] >= DAILY_EFFECTIVE_HOURS_GATE
    ]
    return {
        "gate_effective_hours": DAILY_EFFECTIVE_HOURS_GATE,
        "guardian_reports": guardian_reports,
        "first_guardian_report_day": first_report_day,
        "days": rows,
        "days_with_no_daily_report": [row["day"] for row in rows if row["guardian_absent"]],
        "days_with_no_driver_activity": gaps,
        "measured_days": len(measured),
        "days_meeting_effective_hours": len(passing),
        # The guardian only started on ``first_guardian_report_day``; days
        # before it had nobody to restart the driver, so they are not scored.
        "unmeasured_days_before_guardian": len(rows) - len(measured),
    }


__all__ = [
    "DAILY_EFFECTIVE_HOURS_GATE",
    "GUARDIAN_SCHEMA",
    "build_guardian",
]
