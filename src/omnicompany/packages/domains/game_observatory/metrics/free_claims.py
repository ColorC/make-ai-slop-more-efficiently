"""Audit every popup claim, dismiss and close the live runs ever performed.

F4-08 section 7 replaced "claim nothing free" with a rule that turns on where
the tap happened: an unconditional grant may be claimed, a shop, a warehouse or
any purchase panel may not, and a claim only counts as authorised when the
surface is registered, the element it tapped carries a verified outcome, and the
report kept the save either side of it.

The events themselves are scattered: the live loop writes its popup taps into
the red-line tap audit, the main-task flow writes them under its own attempt,
and the recovery schema writes them under ``result``.  This module reads all
three, deduplicates by action id, and judges each one against the registration
that authorised it.  Nothing is inferred about a surface that is not in the
navigation whitelist -- that absence is itself the finding.
"""

from __future__ import annotations

import re
from datetime import datetime
from omnicompany.core.pycompat import UTC
from typing import Any, Iterator

from .sources import Inputs, Report

AUDIT_SCHEMA = "omnicompany.game-observatory.rr-free-claim-audit.v1"

# The popup kinds F7-03 section 1.5 asks to list.  Selections, confirmations
# and tutorial follows are popup actions too but are not claims or exits.
AUDITED_KINDS = ("free-claim", "close")
JUDGED_KINDS = ("free-claim",)

# ``popup:<kind>:<screen>:<element>`` is the intent the popup handler writes.
_POPUP_INTENT = re.compile(r"^popup:([^:]+):([^:]+):(.+)$")

# ``a.<nanoseconds>.<digest>`` / ``f.<nanoseconds>.<digest>``: the frame clock
# the capture pipeline stamps ids with.  It is a monotonic nanosecond counter,
# which the observation timestamps in the same report calibrate onto UTC.
_ID_COUNTER = re.compile(r"^[af]\.(\d+)\.")

# Registry keys that mark a surface as a catalogue of priced goods.  A claim
# inside one is forbidden by F4-08 section 7 whatever the control is labelled.
_PRICED_SURFACE_KEYS = ("priced_product_clicks", "sections")

# The save fields the ruling asks a claim to leave a difference in.
_SAVE_FIELDS = ("energy", "gems", "coins", "level", "xp")

# When F4-08 section 7 replaced "claim nothing free" with the surface-based
# rule: the planner ruling of 2026-09-07 01:55 local time, landed as commit
# 6aaafbb2c, which is 2026-09-06T17:55Z.  Claims before it were performed under
# the F4-03 / F4-06 registrations that were standing at the time.  They are
# still judged against the current criterion, because the criterion is what the
# registry has to satisfy; the column only says which era each one belongs to,
# so an old claim is not read as a fresh violation.
POLICY_EFFECTIVE_AT = "2026-09-06T17:55:00Z"
POLICY_SOURCE = "F4-08 第 7 节（规划者裁决 2026-09-07 01:55 本地，提交 6aaafbb2c）"


def _counter(identifier: Any) -> int | None:
    if not isinstance(identifier, str):
        return None
    match = _ID_COUNTER.match(identifier)
    return int(match.group(1)) if match else None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class _FrameClock:
    """Maps a report's frame counters onto UTC, from its own observations.

    Every observation carries both a frame id and the wall clock it was seen
    at, so one pair fixes the offset of a counter that is otherwise only
    monotonic.  The median offset over all pairs is used, which keeps a single
    slow capture from moving every timestamp in the report.
    """

    def __init__(self, report: Report) -> None:
        offsets: list[float] = []
        for observation in _observations(report.payload):
            counter = _counter(observation.get("frame_id"))
            seen = _parse_time(observation.get("observed_at"))
            if counter is None or seen is None:
                continue
            offsets.append(seen.timestamp() - counter / 1e9)
        self.offsets = sorted(offsets)

    @property
    def anchors(self) -> int:
        return len(self.offsets)

    def at(self, identifier: Any) -> str | None:
        counter = _counter(identifier)
        if counter is None or not self.offsets:
            return None
        middle = len(self.offsets) // 2
        offset = (
            self.offsets[middle]
            if len(self.offsets) % 2
            else (self.offsets[middle - 1] + self.offsets[middle]) / 2.0
        )
        moment = datetime.fromtimestamp(counter / 1e9 + offset, tz=UTC)
        return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _observations(payload: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    """Every ``observations`` entry anywhere in a report, at any nesting."""

    if depth > 4:
        return
    if isinstance(payload, dict):
        rows = payload.get("observations")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    yield row
        for value in payload.values():
            yield from _observations(value, depth + 1)
    elif isinstance(payload, list):
        for value in payload:
            yield from _observations(value, depth + 1)


def _state(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    kept = {
        field: payload[field]
        for field in _SAVE_FIELDS
        if isinstance(payload.get(field), (int, float))
        and not isinstance(payload.get(field), bool)
    }
    return kept or None


def _save_diff(before: Any, after: Any) -> dict[str, Any]:
    """The claim's save difference, or the reason the report cannot show one."""

    start = _state(before)
    end = _state(after)
    if start is None and end is None:
        return {"available": False, "reason": "报告没有该次尝试的存档前后状态"}
    if end is None:
        return {
            "available": False,
            "reason": "报告只有存档前状态（该次尝试停在未知界面，未写回后状态）",
            "before": start,
        }
    if start is None:
        return {"available": False, "reason": "报告只有存档后状态", "after": end}
    return {
        "available": True,
        "before": start,
        "after": end,
        "delta": {
            field: end[field] - start[field]
            for field in _SAVE_FIELDS
            if field in start and field in end
        },
    }


def _attempts(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """The containers that hold popup actions beside a save state.

    ``main_task_evidence`` is what the live loop writes, ``result`` is what the
    recovery script writes; both carry the attempt's before and after state,
    which is what a claim's save difference is read from.
    """

    evidence = payload.get("main_task_evidence")
    if isinstance(evidence, dict):
        yield evidence
    elif isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict):
                yield item
    result = payload.get("result")
    if isinstance(result, dict):
        yield result


def _events_from_attempts(report: Report, clock: _FrameClock) -> list[dict[str, Any]]:
    events = []
    for attempt in _attempts(report.payload):
        actions = attempt.get("popup_actions")
        if not isinstance(actions, list):
            continue
        diff = _save_diff(
            attempt.get("before_state_payload") or attempt.get("before"),
            attempt.get("after_state_payload") or attempt.get("after"),
        )
        for action in actions:
            if not isinstance(action, dict):
                continue
            events.append(
                {
                    "action_id": action.get("action_id"),
                    "at": clock.at(action.get("action_id")),
                    "screen": action.get("screen"),
                    "kind": action.get("kind"),
                    "element": action.get("element"),
                    "source": "popup_actions",
                    "identified_screen": None,
                    "save_diff": diff,
                    "pre_frame_allowed": None,
                    "pre_frame_basis": None,
                }
            )
    return events


def _events_from_audit(report: Report, clock: _FrameClock) -> list[dict[str, Any]]:
    audit = report.payload.get("tap_preframe_redline_audit")
    if not isinstance(audit, dict):
        return []
    events = []
    for entry in audit.get("evidence") or []:
        if not isinstance(entry, dict):
            continue
        match = _POPUP_INTENT.match(str(entry.get("intent") or ""))
        if match is None:
            continue
        kind, screen, element = match.groups()
        events.append(
            {
                "action_id": entry.get("action_id"),
                "at": clock.at(entry.get("pre_frame_id") or entry.get("action_id")),
                # The intent names the surface the handler acted on; the
                # pre-frame identification is a separate record and is kept as
                # one rather than replacing it.
                "screen": screen,
                "identified_screen": entry.get("identified_screen"),
                "kind": kind,
                "element": element,
                "source": "tap_preframe_redline_audit",
                "save_diff": {
                    "available": False,
                    "reason": "红线前帧审计逐 tap 记录，不带存档前后状态",
                },
                "pre_frame_allowed": entry.get("allowed"),
                "pre_frame_basis": entry.get("basis"),
            }
        )
    return events


def _report_moment(report: Report) -> str | None:
    """When the report itself ran, for events whose own instant is unknown."""

    payload = report.payload
    loop = payload.get("loop")
    for value in (
        (loop or {}).get("started_at") if isinstance(loop, dict) else None,
        payload.get("captured_at"),
        payload.get("generated_at"),
    ):
        if isinstance(value, str) and value:
            parsed = _parse_time(value)
            if parsed is not None:
                return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return None


def _era(moment: str | None, fallback: str | None) -> dict[str, Any]:
    """Which side of the F4-08 section 7 ruling an event happened on."""

    stamp = moment or fallback
    if stamp is None:
        return {
            "before_policy": None,
            "basis": "既无事件时刻也无报告时刻，无法判定所属时期",
        }
    return {
        "before_policy": stamp < POLICY_EFFECTIVE_AT,
        "basis": "事件时刻" if moment else "报告开始时刻（事件时刻无法标定）",
    }


def _registry(navigation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    surfaces = navigation.get("surfaces")
    safety = navigation.get("safety")
    forbidden = (safety or {}).get("forbidden_surfaces") if isinstance(safety, dict) else None
    return (
        surfaces if isinstance(surfaces, dict) else {},
        forbidden if isinstance(forbidden, dict) else {},
    )


def judge(
    event: dict[str, Any], surfaces: dict[str, Any], forbidden: dict[str, Any]
) -> dict[str, Any]:
    """Score one claim against the three conditions F4-08 section 7 names."""

    surface = surfaces.get(event["screen"]) if isinstance(event["screen"], str) else None
    elements = (surface or {}).get("elements")
    element = (
        elements.get(event["element"])
        if isinstance(elements, dict) and isinstance(event["element"], str)
        else None
    )
    outcome = element.get("verified_outcome") if isinstance(element, dict) else None
    forbidden_entry = forbidden.get(event["screen"]) if isinstance(event["screen"], str) else None
    priced_keys = [
        key for key in _PRICED_SURFACE_KEYS if isinstance(surface, dict) and key in surface
    ]
    reasons = []
    if surface is None:
        reasons.append("界面不在 navigation 白名单里")
    elif element is None:
        reasons.append("元素不在该界面的登记控件里")
    if surface is not None and element is not None:
        if not (isinstance(outcome, str) and outcome.strip()):
            reasons.append("登记控件的 verified_outcome 为空")
    purchase_context = None
    if isinstance(forbidden_entry, dict):
        purchase_context = "registered_forbidden_surface"
        reasons.append(
            "界面是登记的禁入界面（"
            + "、".join(str(item) for item in forbidden_entry.get("reasons") or [])
            + "）"
        )
    elif priced_keys:
        purchase_context = "priced_catalog"
        reasons.append(f"界面登记了带价商品目录（{'、'.join(priced_keys)}）")
    return {
        "registered_surface": surface is not None,
        "registered_element": element is not None,
        "verified_outcome": outcome if isinstance(outcome, str) else None,
        "verified_outcome_present": bool(isinstance(outcome, str) and outcome.strip()),
        "purchase_context": purchase_context,
        "authorization": element.get("authorization") if isinstance(element, dict) else None,
        "compliant": not reasons,
        "reasons": reasons,
    }


def _ledger_delta(fact: dict[str, Any]) -> dict[str, int]:
    """The resource movement one ``free_claim_delta`` fact recorded."""

    delta = fact.get("delta") or {}
    return {
        name: int(value)
        for name, value in delta.items()
        if name in _SAVE_FIELDS and isinstance(value, (int, float))
    }


def _scan_delta(row: dict[str, Any]) -> dict[str, int]:
    diff = row.get("save_diff") or {}
    if not diff.get("available"):
        return {}
    return {
        name: int(value)
        for name, value in (diff.get("delta") or {}).items()
        if name in _SAVE_FIELDS and isinstance(value, (int, float))
    }


def _ledger_reconciliation(
    rows: list[dict[str, Any]], facts: list[dict[str, Any]]
) -> dict[str, Any]:
    """Read the claim ledger, and keep the old report scan beside it.

    F7-04 section 2.1.  The cross-reference ledger already extracts a fact per
    claim-class guard action with the save window it moved, so that becomes the
    source this section is judged from.  The scan that used to walk the reports
    stays as a second column: it is what proves the change of source did not
    quietly drop or invent an event, and every disagreement is listed rather
    than reconciled away.
    """

    ledger: dict[str, dict[str, Any]] = {}
    for fact in facts:
        before = fact.get("before") or {}
        action_id = str(before.get("action_id") or "")
        if not action_id:
            continue
        ledger[action_id] = {
            "action_id": action_id,
            "screen": before.get("screen"),
            "kind": before.get("action_kind"),
            "element": before.get("element"),
            "measured": bool(before.get("measured")),
            "attributable": bool(before.get("attributable")),
            "delta": _ledger_delta(fact),
            "fact_id": fact.get("fact_id"),
            "source_report": fact.get("source_report"),
        }
    scanned = {
        str(row.get("action_id") or ""): row
        for row in rows
        if row.get("action_id")
    }
    differences: list[dict[str, Any]] = []
    for action_id in sorted(set(ledger) | set(scanned)):
        entry = ledger.get(action_id)
        row = scanned.get(action_id)
        if entry is None:
            differences.append(
                {
                    "action_id": action_id,
                    "reason": "只在旧扫描里",
                    "screen": row.get("screen") if row else None,
                    "ledger_delta": None,
                    "scan_delta": _scan_delta(row) if row else None,
                }
            )
            continue
        if row is None:
            differences.append(
                {
                    "action_id": action_id,
                    "reason": "只在台账事实里",
                    "screen": entry["screen"],
                    "ledger_delta": entry["delta"],
                    "scan_delta": None,
                }
            )
            continue
        scan_measured = bool((row.get("save_diff") or {}).get("available"))
        scan_delta = _scan_delta(row)
        # Only a window both sources measured has two numbers to compare.  A
        # window measured on one side only is a coverage difference, and saying
        # so is not the same as saying the two numbers disagree.
        if entry["measured"] and not scan_measured:
            reason = "只有台账测到该窗口"
        elif scan_measured and not entry["measured"]:
            reason = "只有旧扫描测到该窗口"
        elif entry["measured"] and scan_delta != entry["delta"]:
            reason = "存档差分不一致"
        else:
            reason = None
        if reason is not None:
            differences.append(
                {
                    "action_id": action_id,
                    "reason": reason,
                    "screen": entry["screen"],
                    "ledger_delta": entry["delta"] if entry["measured"] else None,
                    "scan_delta": scan_delta if scan_measured else None,
                }
            )
        row["ledger"] = entry
    return {
        "ledger_source": "crossref/facts.v1.ndjson#kind=free_claim_delta",
        "ledger_events": len(ledger),
        "ledger_measured": sum(1 for entry in ledger.values() if entry["measured"]),
        "ledger_attributable": sum(
            1 for entry in ledger.values() if entry["attributable"]
        ),
        "scan_events": len(scanned),
        "ledger_scan_differences": differences,
        "ledger_scan_difference_count": len(differences),
    }


def build_free_claim_audit(inputs: Inputs) -> dict[str, Any]:
    """Every claim, dismiss and close on record, judged and counted."""

    surfaces, forbidden = _registry(inputs.navigation)
    rows: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for report in inputs.reports:
        clock = _FrameClock(report)
        moment = _report_moment(report)
        for event in _events_from_attempts(report, clock) + _events_from_audit(report, clock):
            if event["kind"] not in AUDITED_KINDS:
                continue
            era = _era(event.get("at"), moment)
            row = {
                **event,
                "report": report.name,
                "report_started_at": moment,
                "before_policy": era["before_policy"],
                "era_basis": era["basis"],
                "clock_anchors": clock.anchors,
            }
            if event["kind"] in JUDGED_KINDS:
                row["verdict"] = judge(event, surfaces, forbidden)
            else:
                row["verdict"] = None
            key = str(event.get("action_id") or "")
            if key and key in seen:
                merged = rows[seen[key]]
                # The audit sees the pre-frame, the attempt sees the save; the
                # same tap recorded twice keeps whichever half each source has.
                if not (merged["save_diff"] or {}).get("available") and (
                    row["save_diff"] or {}
                ).get("available"):
                    merged["save_diff"] = row["save_diff"]
                if merged.get("pre_frame_allowed") is None:
                    merged["pre_frame_allowed"] = row["pre_frame_allowed"]
                    merged["pre_frame_basis"] = row["pre_frame_basis"]
                if merged.get("at") is None:
                    merged["at"] = row["at"]
                merged["sources"] = sorted({*merged.get("sources", []), row["source"]})
                continue
            row["sources"] = [row["source"]]
            if key:
                seen[key] = len(rows)
            rows.append(row)
    rows.sort(key=lambda row: (row["at"] or "", row["report"], str(row["action_id"])))
    claims = [row for row in rows if row["kind"] in JUDGED_KINDS]
    flagged = [row for row in claims if row["verdict"] and not row["verdict"]["compliant"]]
    by_reason: dict[str, int] = {}
    for row in flagged:
        for reason in row["verdict"]["reasons"]:
            by_reason[reason] = by_reason.get(reason, 0) + 1
    with_diff = sum(1 for row in claims if (row["save_diff"] or {}).get("available"))
    return {
        "schema": AUDIT_SCHEMA,
        "criterion": (
            "F4-08 第 7 节：界面已登记、登记控件的 verified_outcome 非空、"
            "且不在登记的禁入界面或带价商品目录内"
        ),
        "policy_effective_at": POLICY_EFFECTIVE_AT,
        "policy_source": POLICY_SOURCE,
        "flagged_rows": flagged,
        "flagged_before_policy": sum(1 for row in flagged if row["before_policy"] is True),
        "flagged_after_policy": sum(1 for row in flagged if row["before_policy"] is False),
        "flagged_era_unknown": sum(1 for row in flagged if row["before_policy"] is None),
        "navigation_product": str(inputs.paths["navigation"].name)
        if inputs.navigation
        else None,
        "surfaces_registered": len(surfaces),
        "rows": rows,
        "events": len(rows),
        "claims": len(claims),
        "closes": len(rows) - len(claims),
        "compliant": len(claims) - len(flagged),
        "flagged": len(flagged),
        "flagged_by_reason": dict(sorted(by_reason.items())),
        "claims_with_save_diff": with_diff,
        "claims_without_save_diff": len(claims) - with_diff,
        **_ledger_reconciliation(rows, inputs.crossref_facts.get("free_claim_delta") or []),
    }


__all__ = [
    "AUDITED_KINDS",
    "AUDIT_SCHEMA",
    "JUDGED_KINDS",
    "build_free_claim_audit",
    "judge",
]
