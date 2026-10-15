"""Where the account stands, and how long the reports say the rest will take.

The drivers write one ``progress_curve`` row per wall-clock hour and F4-08
writes a projection from the configuration constants; this module collects both
without recomputing either.  The projection's formula, its inputs and the
configuration rows it read are carried through verbatim, so the dashboard states
the estimate and the arithmetic behind it rather than a number of its own.
"""

from __future__ import annotations

import re
from typing import Any

from .sources import Inputs, Report

# The capture stamp every product carries in its file name.
_STAMP = re.compile(r"(\d{8}T\d{6}Z)")

PROGRESS_SCHEMA = "omnicompany.game-observatory.rr-progress-view.v1"

_CURVE_FIELDS = (
    "hour",
    "segments",
    "rounds",
    "confirmed",
    "energy_spent",
    "energy_collected",
    "energy_recovered",
    "deliveries",
    "main_tasks_completed",
    "played_s",
    "waited_s",
    "coins_end",
    "level_end",
    "xp_end",
    "main_task_end",
    "open_orders_end",
    "energy_end",
    "wall_seconds",
)


def _projection_payload(inputs: Inputs) -> tuple[dict[str, Any] | None, str | None]:
    """The newest projection, whether it stands alone or sits in the F4-08 report."""

    if inputs.projection is not None:
        return inputs.projection.payload, inputs.projection.name
    if inputs.multi_day is not None:
        embedded = inputs.multi_day.payload.get("progress_projection")
        if isinstance(embedded, dict):
            return embedded, f"{inputs.multi_day.name} · progress_projection"
    return None, None


def _newest_projection(inputs: Inputs) -> tuple[dict[str, Any] | None, str | None]:
    """Prefer whichever of the two carries the later view of the account.

    The standalone projection file and the one embedded in the multi-day report
    are separate products written at different times; the embedded one is newer
    whenever the F4-08 report was rewritten after it.
    """

    standalone = inputs.projection
    embedded = None
    if inputs.multi_day is not None:
        candidate = inputs.multi_day.payload.get("progress_projection")
        if isinstance(candidate, dict):
            embedded = candidate
    if standalone is None:
        return _projection_payload(inputs)
    if embedded is None or inputs.multi_day is None:
        return standalone.payload, standalone.name
    name = f"{inputs.multi_day.name} · progress_projection"
    if _stamp(inputs.multi_day.name) > _stamp(standalone.name):
        return embedded, name
    return standalone.payload, standalone.name


def _stamp(name: str) -> str:
    """The capture stamp inside a product's file name, for ordering.

    The names differ before the stamp (``F4-08-<ts>`` against
    ``F4-08-projection-<ts>``), so comparing whole names orders them
    alphabetically rather than in time.
    """

    match = _STAMP.search(name)
    return match.group(1) if match else ""


def _curve_rows(drives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for drive in drives:
        curve = drive.get("progress_curve")
        if not isinstance(curve, list):
            continue
        for entry in curve:
            if not isinstance(entry, dict):
                continue
            rows.append(
                {
                    "drive_id": drive["drive_id"],
                    "started_at": drive.get("started_at"),
                    **{field: entry.get(field) for field in _CURVE_FIELDS},
                }
            )
    rows.sort(key=lambda row: (str(row["started_at"] or ""), row["drive_id"], row["hour"] or 0))
    return rows


def _drives_without_curve(drives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "drive_id": drive["drive_id"],
            "segments": drive["segments"],
            "report_state": drive.get("report_state"),
            # A drive with neither a stop line nor a report is either still
            # playing or was killed; the products cannot tell which, so the
            # reason says what is missing instead of guessing why.
            "reason": (
                "驱动没有写出 .json 报告（无终止记录：还在跑，或被外部终止），因此没有每小时曲线"
                if drive.get("report_state") == "externally_stopped_no_report"
                else "报告里没有 progress_curve"
            ),
        }
        for drive in drives
        if not isinstance(drive.get("progress_curve"), list)
    ]


def _milestones(projection: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The projection's own milestone rows, next task first."""

    if not isinstance(projection, dict):
        return []
    rows = []
    for key in ("next_task", "target"):
        entry = projection.get(key)
        if not isinstance(entry, dict):
            continue
        rows.append(
            {
                "milestone": key,
                "task": entry.get("task"),
                "cost": entry.get("cost"),
                "tasks_inclusive": entry.get("tasks_inclusive"),
                "coins_needed": entry.get("coins_needed"),
                "coins_short": entry.get("coins_short"),
                "days": entry.get("days"),
                "level_on_arrival": entry.get("level_on_arrival"),
            }
        )
    return rows


def build_progress(
    drives: list[dict[str, Any]], inputs: Inputs, multi_day: Report | None = None
) -> dict[str, Any]:
    projection, source = _newest_projection(inputs)
    report = multi_day or inputs.multi_day
    long_run = (report.payload.get("long_run") if report is not None else None) or {}
    return {
        "schema": PROGRESS_SCHEMA,
        "curve": _curve_rows(drives),
        "curve_drives": sum(
            1 for drive in drives if isinstance(drive.get("progress_curve"), list)
        ),
        "drives_without_curve": _drives_without_curve(drives),
        "projection": projection,
        "projection_source": source,
        "milestones": _milestones(projection),
        "current_main_task": (projection or {}).get("inputs", {}).get("from_task")
        if isinstance(projection, dict)
        else None,
        "long_run_status": long_run.get("status_at_report_time")
        if isinstance(long_run, dict)
        else None,
        "multi_day_report": report.name if report is not None else None,
    }


__all__ = ["PROGRESS_SCHEMA", "build_progress"]
