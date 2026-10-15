"""F7-05: the two-way action taxonomy -- state-changing vs interface round-trip.

The old ``effective_apm`` counts every action the loop emitted over wall clock:
guard popup closes, tutorial follows, refuted retries and settle probes all
inflate it.  It measures device throughput, not progress.  This module is the
classifier the corrected rates are built from: one pure function deciding, from
an action's intent label, its adjudication and its save diff, whether the
action changed the game state or only walked the interface.

The decision rules are spec section 2 verbatim, in priority order:

1. an action adjudicated ``confirmed`` / ``confirmed_with_deviation`` whose
   intent is a business intent (merge / generate / deliver / open_chest /
   free_claim / place_queue_item) is a state change -- a deviation is an
   action the game agreed happened, in a shape the plan did not name;
2. any action whose before/after save diff is non-empty is a state change
   (this is how an un-adjudicated successful free-claim qualifies: the
   cross-reference ledger's ``free_claim_delta`` facts carry the per-action
   save window);
3. everything else is an interface round-trip: close / advance / follow /
   settle / navigation / wait intents, guard closes, and every refuted action
   -- a refuted action changed nothing by definition, whatever it intended.
"""

from __future__ import annotations

from typing import Any, Literal

ActionClass = Literal["state_change", "interface_roundtrip"]

# Spec section 2, criterion 1: the business intents a confirmation promotes to
# a state change.  Both the dashed intent heads the actuator writes and the
# underscored spellings the spec names are accepted.  A confirmed action whose
# intent is not in this set -- a generator selection, a cobweb clearing, a wait
# -- is not promoted by criterion 1 and falls through to criterion 2.
STATE_CHANGE_INTENTS = frozenset(
    {
        "merge",
        "generate",
        "deliver",
        "open-chest",
        "select-chest",
        "open_chest",
        "free-claim",
        "free_claim",
        "place-queue-item",
        "place_queue_item",
    }
)

CONFIRMED_STATUSES = frozenset({"confirmed", "confirmed_with_deviation"})

# Spec section 3 item 3: a segment above this interface round-trip share flags
# its whole day red in the daily view, with the segment named.
ROUNDTRIP_SHARE_RED_LINE = 0.5

# Spec section 3 item 5: the replacement for the APM >= 40 gate, in
# state-changing actions per hour over satiated (full-energy) segments.
#
# Suggested value, derived from the 2026-09-08 full-library recompute over the
# twelve segments that qualify for the satiated window (energy start >= 60,
# rounds >= 30, not ended on a legal wait):
#   * best healthy named segment -- the 2026-09-07 02:46Z longrun3 restart's
#     segment A (report F4-04-20260907T024621Z.json): 115 state changes over
#     186.3 s of wall = 2222/h;
#   * the two healthier satiated segments on record: 2391/h
#     (F4-04-20260907T003051Z.json) and 2722/h (F4-01b-20260905T220449Z.json);
#   * every other qualifying segment is at or below 1711/h, and the known
#     idle (refuted_streak) segments sit at 0-224/h.
# 2000 is the best healthy named segment's actual rate rounded down to a clean
# hundred: a bar the reference healthy segment itself passes, and one no
# energy-capped or spinning segment approaches.  The planner rules the final
# number; this constant records the suggestion and its provenance.
STATE_CHANGE_PER_HOUR_GATE = 2000
STATE_CHANGE_PER_HOUR_GATE_SOURCE = (
    "F7-05 全库重算：2026-09-07 02:46Z longrun3 重启段 A"
    "（报告 F4-04-20260907T024621Z.json）实测 2222 次/小时，向下取整；"
    "次优满体力段 2391/h（F4-04-20260907T003051Z.json）。建议值，待规划者裁决。"
)


def intent_head(intent: Any, kind: Any) -> str:
    """The classification key of an action: its intent head, else its kind.

    Popup intents carry the deciding kind one level in --
    ``popup:free-claim:<screen>:<element>`` classifies as ``free-claim`` --
    and guard actions record no intent at all, only a ``kind``.
    """

    head = str(intent or "").split(":", 1)[0]
    if head == "popup":
        parts = str(intent).split(":")
        if len(parts) > 1 and parts[1]:
            return parts[1]
    return head or str(kind or "")


def classify_action(
    *,
    intent: Any = None,
    kind: Any = None,
    status: Any = None,
    save_diff_nonempty: bool | None = None,
) -> ActionClass:
    """State change or interface round-trip: spec section 2, pure."""

    if status == "refuted":
        return "interface_roundtrip"
    head = intent_head(intent, kind)
    if status in CONFIRMED_STATUSES and head in STATE_CHANGE_INTENTS:
        return "state_change"
    if save_diff_nonempty:
        return "state_change"
    return "interface_roundtrip"


def claim_diffs(facts: list[dict[str, Any]]) -> dict[str, bool]:
    """action id -> whether its measured save window moved, from the ledger.

    The cross-reference ledger's ``free_claim_delta`` facts are the per-action
    save diff criterion 2 reads.  A fact that was not measured, or measured a
    window where nothing moved, is not a state change; a claim whose window
    never reached the ledger simply has no entry and gets no diff.
    """

    diffs: dict[str, bool] = {}
    for fact in facts:
        before = fact.get("before")
        if not isinstance(before, dict):
            continue
        action_id = before.get("action_id")
        if not isinstance(action_id, str) or not action_id:
            continue
        moved = bool(before.get("measured")) and any(
            isinstance(value, (int, float)) and not isinstance(value, bool) and value
            for value in (fact.get("delta") or {}).values()
        )
        # A later fact about the same tap only ever adds a window the first
        # one lacked, so a True is never overwritten back to False.
        diffs[action_id] = diffs.get(action_id, False) or moved
    return diffs


def report_action_validity(
    payload: dict[str, Any], diffs: dict[str, bool] | None = None
) -> dict[str, Any]:
    """Classify every action one run report records, and total the classes.

    Two populations are counted: the loop's executed actions (adjudicated by
    prediction id) and the guard's screen actions (never adjudicated; a
    free-claim among them is judged by its ledger save diff alone).  Confirmed
    actions whose intent is outside the criterion-1 set are totalled apart --
    they are round-trips by the letter of the taxonomy, and the count keeps
    that choice visible instead of hiding it inside the share.
    """

    diffs = diffs or {}
    state_change = 0
    roundtrip = 0
    unlisted_confirmed: dict[str, int] = {}
    loop = payload.get("loop")
    rounds = loop.get("rounds") if isinstance(loop, dict) else None
    for round_payload in rounds or []:
        if not isinstance(round_payload, dict):
            continue
        statuses = {
            item.get("prediction_id"): item.get("status")
            for item in round_payload.get("adjudication") or []
            if isinstance(item, dict)
        }
        for item in round_payload.get("executed") or []:
            action = item.get("action") if isinstance(item, dict) else None
            if not isinstance(action, dict):
                continue
            prediction = action.get("prediction")
            prediction_id = (
                prediction.get("prediction_id") if isinstance(prediction, dict) else None
            )
            status = statuses.get(prediction_id)
            result = item.get("result")
            action_id = (
                result.get("action_id") if isinstance(result, dict) else None
            )
            verdict = classify_action(
                intent=action.get("intent"),
                kind=action.get("kind"),
                status=status,
                save_diff_nonempty=diffs.get(str(action_id or "")),
            )
            if verdict == "state_change":
                state_change += 1
            else:
                roundtrip += 1
                if status in CONFIRMED_STATUSES:
                    head = intent_head(action.get("intent"), action.get("kind"))
                    unlisted_confirmed[head] = unlisted_confirmed.get(head, 0) + 1
        for guard_action in round_payload.get("screen_guard_actions") or []:
            if not isinstance(guard_action, dict):
                continue
            verdict = classify_action(
                intent=guard_action.get("intent"),
                kind=guard_action.get("kind"),
                status=None,
                save_diff_nonempty=diffs.get(str(guard_action.get("action_id") or "")),
            )
            if verdict == "state_change":
                state_change += 1
            else:
                roundtrip += 1
    total = state_change + roundtrip
    return {
        "state_change_actions": state_change,
        "interface_roundtrip_actions": roundtrip,
        "classified_actions": total,
        "roundtrip_share": roundtrip / total if total else None,
        "unlisted_confirmed_intents": dict(sorted(unlisted_confirmed.items())),
    }


__all__ = [
    "ActionClass",
    "CONFIRMED_STATUSES",
    "ROUNDTRIP_SHARE_RED_LINE",
    "STATE_CHANGE_INTENTS",
    "STATE_CHANGE_PER_HOUR_GATE",
    "STATE_CHANGE_PER_HOUR_GATE_SOURCE",
    "claim_diffs",
    "classify_action",
    "intent_head",
    "report_action_validity",
]
