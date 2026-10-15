"""Index authoritative report and replay evidence as stable facts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


_REPORT_RE = re.compile(r"^(?:F2-0[34]|F4-0[1-4]|F4-03a|F4-03-recovery).+\.json$")

# EnergyModel.lua:7-8 — the recovery clock the save-time series is judged against.
ENERGY_MAX = 100
ENERGY_RECOVERY_SECONDS = 120
# ShopModel.lua:275-291 — the free refill grants GetMaxEnergy() and costs no gem.
FREE_REFILL_ENERGY = ENERGY_MAX
_RESOURCE_FIELDS = ("energy", "coins", "gems", "xp", "level")
_SEMANTIC_KINDS = {"collect", "deliver", "generate", "merge", "open_chest", "sell"}
_NUMERIC_TYPE_RE = re.compile(r"^\d+$")


@dataclass(frozen=True)
class FactV1:
    """One evidence-addressed before/action/after observation."""

    fact_id: str
    kind: str
    before: dict[str, Any]
    after: dict[str, Any]
    delta: dict[str, Any]
    evidence_refs: list[str]
    source_report: str
    captured_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FactV1:
        return cls(**{name: payload[name] for name in cls.__dataclass_fields__})


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _fact_id(kind: str, source_report: str, evidence_id: str) -> str:
    material = f"{kind}\0{source_report}\0{evidence_id}".encode()
    return "fact_" + hashlib.sha256(material).hexdigest()[:20]


def _make_fact(
    *,
    kind: str,
    before: dict[str, Any],
    after: dict[str, Any],
    delta: dict[str, Any],
    evidence_refs: Iterable[str],
    source_report: str,
    captured_at: str,
    evidence_id: str,
) -> FactV1:
    return FactV1(
        fact_id=_fact_id(kind, source_report, evidence_id),
        kind=kind,
        before=before,
        after=after,
        delta=delta,
        evidence_refs=sorted(set(evidence_refs)),
        source_report=source_report,
        captured_at=captured_at,
    )


def _resource_snapshot(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}
    return {key: payload.get(key) for key in _RESOURCE_FIELDS if key in payload}


def _resource_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in _RESOURCE_FIELDS:
        left, right = before.get(key), after.get(key)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            result[key] = right - left
    return result


def _task_id(payload: dict[str, Any] | None) -> str | None:
    payload = payload or {}
    nested = payload.get("main_task")
    if isinstance(nested, dict):
        return nested.get("task_id") or nested.get("curId")
    return payload.get("main_task_id")


def _task_cost(payload: dict[str, Any] | None) -> int | None:
    payload = payload or {}
    nested = payload.get("main_task")
    value: Any = payload.get("main_task_cost")
    if isinstance(nested, dict):
        value = nested.get("cost", nested.get("curCost", value))
    if isinstance(value, str) and "##" in value:
        value = value.rsplit("##", 1)[-1]
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _task_snapshot(payload: dict[str, Any] | None) -> dict[str, Any]:
    result = _resource_snapshot(payload)
    result["main_task_id"] = _task_id(payload)
    result["main_task_cost"] = _task_cost(payload)
    if payload and payload.get("snapshot_sha256"):
        result["snapshot_sha256"] = payload["snapshot_sha256"]
    return result


def _board_diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    left = before.get("board_matrix_7x9")
    right = after.get("board_matrix_7x9")
    if not isinstance(left, list) or not isinstance(right, list):
        return []
    changes = []
    for row_index, (left_row, right_row) in enumerate(zip(left, right), start=1):
        for col_index, (old, new) in enumerate(zip(left_row, right_row), start=1):
            if old != new:
                changes.append({"row": row_index, "col": col_index, "before": old, "after": new})
    return changes


def _delivery_facts(
    report: dict[str, Any], report_ref: str, captured_at: str
) -> list[FactV1]:
    facts: list[FactV1] = []
    for item in report.get("delivery_evidence") or []:
        adjudication = item.get("adjudication")
        if isinstance(adjudication, dict) and adjudication.get("status") != "confirmed":
            continue
        action_id = str(item.get("action_id") or f"round-{item.get('round')}")
        orders = item.get("orders_before") or []
        selected = next(
            (order for order in orders if str(order.get("order_id")) == str(item.get("order_before"))),
            {},
        )
        required = selected.get("required_items") or [
            {"type": change.get("before"), "count": 1}
            for change in item.get("board_diff") or []
            if change.get("before")
        ]
        before = {
            "order_id": item.get("order_before"),
            "order_slot": item.get("slot"),
            "required_items": required,
            "reward_coins": selected.get("reward_coins", item.get("predicted_coins_delta")),
        }
        after = {"orders": item.get("orders_after") or []}
        delta = {
            "coins": item.get("actual_coins_delta"),
            "board": item.get("board_diff") or [],
        }
        facts.append(
            _make_fact(
                kind="deliver",
                before=before,
                after=after,
                delta=delta,
                evidence_refs=[f"{report_ref}#round={item.get('round')};action_id={action_id}"],
                source_report=report_ref,
                captured_at=captured_at,
                evidence_id=action_id,
            )
        )
    return facts


def _main_task_fact(
    before_payload: dict[str, Any],
    after_payload: dict[str, Any],
    *,
    report_ref: str,
    captured_at: str,
    evidence_id: str,
    evidence_refs: list[str],
) -> FactV1 | None:
    before = _task_snapshot(before_payload)
    after = _task_snapshot(after_payload)
    if not before["main_task_id"] or before["main_task_id"] == after["main_task_id"]:
        return None
    return _make_fact(
        kind="main_task",
        before=before,
        after=after,
        delta=_resource_delta(before, after),
        evidence_refs=evidence_refs,
        source_report=report_ref,
        captured_at=captured_at,
        evidence_id=evidence_id,
    )


def _checkpoint_facts(
    report: dict[str, Any], report_ref: str, captured_at: str
) -> list[FactV1]:
    checkpoints = report.get("save_checkpoints") or []
    facts = []
    for before, after in zip(checkpoints, checkpoints[1:]):
        left_sha = str(before.get("snapshot_sha256") or before.get("sequence"))
        right_sha = str(after.get("snapshot_sha256") or after.get("sequence"))
        fact = _main_task_fact(
            before,
            after,
            report_ref=report_ref,
            captured_at=captured_at,
            evidence_id=f"snapshot:{left_sha}:{right_sha}",
            evidence_refs=[f"{report_ref}#snapshot_sha256={left_sha}", f"{report_ref}#snapshot_sha256={right_sha}"],
        )
        if fact:
            facts.append(fact)
    return facts


def _direct_main_task_facts(
    report: dict[str, Any], report_ref: str, captured_at: str
) -> list[FactV1]:
    candidates: list[dict[str, Any]] = []
    evidence = report.get("main_task_evidence")
    # The live loop reports one entry per main-task attempt, so the field is a
    # list; the early probes wrote a single object.
    if isinstance(evidence, dict):
        candidates.append(evidence)
    elif isinstance(evidence, list):
        candidates.extend(item for item in evidence if isinstance(item, dict))
    result = report.get("result")
    if isinstance(result, dict):
        candidates.append(result)
    facts = []
    for index, item in enumerate(candidates):
        before, after = item.get("before"), item.get("after")
        if not isinstance(before, dict) or not isinstance(after, dict):
            continue
        action_ids = [
            str(action.get("action_id"))
            for action in item.get("action_results") or []
            if action.get("action_id")
        ]
        evidence_id = action_ids[-1] if action_ids else f"main-task-{index}"
        fact = _main_task_fact(
            before,
            after,
            report_ref=report_ref,
            captured_at=captured_at,
            evidence_id=evidence_id,
            evidence_refs=[f"{report_ref}#action_id={value}" for value in action_ids]
            or [f"{report_ref}#main_task_evidence"],
        )
        if fact:
            facts.append(fact)
    return facts


def _survey_facts(report: dict[str, Any], report_ref: str, captured_at: str) -> list[FactV1]:
    findings = report.get("survey_findings")
    if not isinstance(findings, dict):
        return []
    selected = ("commercial_touchpoint", "forced_shop_tutorial", "daily_free_gift_835_780")
    return [
        _make_fact(
            kind="gameplay_observation",
            before={},
            after={"observation": findings[key], "observation_key": key},
            delta={},
            evidence_refs=[f"{report_ref}#survey_findings.{key}"],
            source_report=report_ref,
            captured_at=captured_at,
            evidence_id=f"survey:{key}",
        )
        for key in selected
        if findings.get(key)
    ]


def _replay_path(report: dict[str, Any]) -> Path | None:
    replay = report.get("replay")
    value = replay.get("run_dir") if isinstance(replay, dict) else None
    value = value or report.get("replay_dir")
    return Path(value) if isinstance(value, str) and value else None


def _planner_facts(
    states_path: Path,
    *,
    report_ref: str,
    report_captured_at: str,
    evidence_root: Path,
) -> list[FactV1]:
    if not states_path.is_file():
        return []
    replay_ref = _relative(states_path, evidence_root)
    facts: list[FactV1] = []
    with states_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            payload = record.get("payload") or {}
            if record.get("source") != "planner" or not isinstance(payload, dict):
                continue
            before_state = payload.get("state_before") or {}
            after_state = payload.get("state_after") or {}
            adjudications = {
                entry.get("prediction_id"): entry.get("status")
                for entry in payload.get("adjudication") or []
            }
            for executed in payload.get("executed") or []:
                action = executed.get("action") or {}
                prediction = action.get("prediction") or {}
                kind = prediction.get("kind")
                prediction_id = prediction.get("prediction_id")
                if kind not in _SEMANTIC_KINDS or adjudications.get(prediction_id) != "confirmed":
                    continue
                result = executed.get("result") or {}
                action_id = str(result.get("action_id") or prediction_id)
                before = _resource_snapshot(before_state)
                after = _resource_snapshot(after_state)
                before.update(
                    {
                        "source_type": prediction.get("source_before"),
                        "target_type": prediction.get("target_before"),
                        "required_items": [
                            {"type": value, "count": 1}
                            for value in prediction.get("required_item_types") or []
                        ],
                        "order_id": prediction.get("order_before"),
                        "order_slot": prediction.get("order_slot"),
                    }
                )
                after.update(
                    {
                        "source_type": prediction.get("source_after"),
                        "target_type": prediction.get("target_after"),
                        "possible_target_types": prediction.get("possible_target_types") or [],
                    }
                )
                delta = _resource_delta(before, after)
                delta["board"] = _board_diff(before_state, after_state)
                for resource in ("energy", "coins"):
                    predicted = prediction.get(f"{resource}_delta")
                    if isinstance(predicted, (int, float)):
                        delta[resource] = predicted
                facts.append(
                    _make_fact(
                        kind=str(kind),
                        before=before,
                        after=after,
                        delta=delta,
                        evidence_refs=[
                            f"{report_ref}#round={payload.get('round')};action_id={action_id}",
                            f"{replay_ref}#t_mono_ns={record.get('t_mono_ns')};action_id={action_id}",
                        ],
                        source_report=report_ref,
                        captured_at=str(record.get("t_wall") or report_captured_at),
                        evidence_id=action_id,
                    )
                )
    return facts


def _generator_output_sample(payload: dict[str, Any]) -> tuple[str, list[str]] | None:
    """Return the generator and the item types it demonstrably produced this round.

    Only rounds whose executed actions are all generations from a single
    generator qualify, and the count of items that appeared in previously empty
    cells has to match the energy the round spent. That balance check discards
    rounds where the game itself dropped a chest or gift onto the board, which
    would otherwise contaminate the output distribution.
    """

    executed = payload.get("executed") or []
    if not executed:
        return None
    predictions = [(item.get("action") or {}).get("prediction") or {} for item in executed]
    if any(prediction.get("kind") != "generate" for prediction in predictions):
        return None
    sources = {prediction.get("source_before") for prediction in predictions}
    generator = next(iter(sources)) if len(sources) == 1 else None
    before_state = payload.get("state_before") or {}
    after_state = payload.get("state_after") or {}
    if not generator or not isinstance(before_state.get("board_matrix_7x9"), list):
        return None
    produced = [
        str(change["after"])
        for change in _board_diff(before_state, after_state)
        if not change["before"] and change["after"]
    ]
    energy_delta = _resource_delta(before_state, after_state).get("energy")
    if not isinstance(energy_delta, (int, float)) or energy_delta >= 0:
        return None
    if len(produced) != -energy_delta:
        return None
    if any(not _NUMERIC_TYPE_RE.match(value) for value in produced):
        return None
    return str(generator), sorted(produced)


# TriggerPlaneModel.lua:8-18 — the five save columns the energy-party activity
# keeps.  ``score`` and ``level`` are what the tier rewards are paid against,
# ``energyConsumed`` is the activation counter, and the last two say whether the
# day is still open.
TRIGGER_PLANE_FIELDS = ("energyConsumed", "score", "level", "dailyStatus", "isMaxOut")

# TriggerPlaneModel.lua:353-362 — the counter moves on every energy decrease, so
# a generation the strict verdict refused still shows up in it.
GENERATE_COST_PER_ITEM = 1


def _trigger_plane_row(state: dict[str, Any] | None) -> dict[str, Any] | None:
    row = (state or {}).get("activity_trigger_plane")
    if not isinstance(row, dict):
        return None
    return {name: row.get(name) for name in TRIGGER_PLANE_FIELDS}


def _adjudication_counts(payload: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in payload.get("adjudication") or []:
        if not isinstance(entry, dict):
            continue
        key = f"{entry.get('kind')}:{entry.get('status')}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _produced_types(payload: dict[str, Any]) -> list[str]:
    """Item types that appeared in cells the round started with empty."""

    before_state = payload.get("state_before") or {}
    after_state = payload.get("state_after") or {}
    if not isinstance(before_state.get("board_matrix_7x9"), list):
        return []
    return sorted(
        str(change["after"])
        for change in _board_diff(before_state, after_state)
        if not change["before"] and change["after"]
    )


def _pb_layer(value: Any) -> bool:
    """True when a board code still carries its ``pb`` (paperbox) layer."""

    return isinstance(value, str) and "pb" in value.split("#")[:-1]


def _paperbox_clear_samples(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Attribute each paperbox strip in one round to the merge that caused it.

    F4-08 section 4.3.  F4-07 could say what a cleared sand cell pays but not
    what clears it.  Within a single round the answer is readable: the round
    carries its own before and after board, its plan and the adjudication, so a
    strip can be paired with the merge that landed in the same round and the
    geometric relation between the two can be stated rather than guessed.  This
    records the relation; whether it holds is the rule's job, not the index's.
    """

    before_matrix = (payload.get("state_before") or {}).get("board_matrix_7x9")
    after_matrix = (payload.get("state_after") or {}).get("board_matrix_7x9")
    if not isinstance(before_matrix, list) or not isinstance(after_matrix, list):
        return []
    confirmed = {
        entry.get("prediction_id")
        for entry in payload.get("adjudication") or []
        if isinstance(entry, dict) and entry.get("status") == "confirmed"
    }
    merges = [
        prediction
        for prediction in (payload.get("plan") or {}).get("predictions") or []
        if isinstance(prediction, dict)
        and prediction.get("kind") == "merge"
        and prediction.get("prediction_id") in confirmed
        and isinstance(prediction.get("target_cell"), dict)
    ]
    # More than one confirmed merge in a round makes the strip unattributable,
    # so the sample says so instead of picking one.
    merge = merges[0] if len(merges) == 1 else None
    target = merge.get("target_cell") if merge else None
    target_row = target.get("row") if isinstance(target, dict) else None
    target_col = target.get("col") if isinstance(target, dict) else None
    # F6-04 section 1.2: the attribution has to name the action that performed
    # the merge, not only the prediction, so a reader can open the same action
    # in actions.ndjson.  The executed list carries the pairing.
    merge_action_id = None
    if merge is not None:
        merge_action_id = next(
            (
                str((item.get("result") or {}).get("action_id"))
                for item in payload.get("executed") or []
                if isinstance(item, dict)
                and ((item.get("action") or {}).get("prediction") or {}).get(
                    "prediction_id"
                )
                == merge.get("prediction_id")
                and (item.get("result") or {}).get("action_id")
            ),
            None,
        )
    samples: list[dict[str, Any]] = []
    for row_index, (before_row, after_row) in enumerate(
        zip(before_matrix, after_matrix), start=1
    ):
        if not isinstance(before_row, list) or not isinstance(after_row, list):
            continue
        for col_index, (before, after) in enumerate(zip(before_row, after_row), start=1):
            if not _pb_layer(before) or _pb_layer(after):
                continue
            adjacent = (
                isinstance(target_row, int)
                and isinstance(target_col, int)
                and abs(row_index - target_row) + abs(col_index - target_col) == 1
            )
            samples.append(
                {
                    "row": row_index,
                    "col": col_index,
                    "cell_before": before,
                    "cell_after": after,
                    "merge_target_row": target_row,
                    "merge_target_col": target_col,
                    "merge_result_type": merge.get("target_after") if merge else None,
                    "merge_action_id": merge_action_id,
                    "merge_prediction_id": merge.get("prediction_id") if merge else None,
                    "adjacent": adjacent,
                    "cleared_with": (
                        "single-confirmed-merge"
                        if merge is not None
                        else f"{len(merges)}-confirmed-merges"
                    ),
                }
            )
    return samples


# F6-05 section 1.3.  ``spreadStartTimer`` is the server second the generator's
# recovery timer was armed (ItemSpread.lua:75-85); the refill lands at that
# second plus the duration ``GetTimerDuration`` selects (ItemSpread.lua:375-389).
# ``ItemManager.lua:80-83`` writes -1 on every item without an ItemSpread
# component, so -1 is "no timer", not "armed at -1".
SPREAD_TIMER_ABSENT = -1
SPREAD_STATE_OPENED = 3


def _spread_cells(payload: dict[str, Any]) -> dict[tuple[str, int, int], dict[str, Any]]:
    """Pull the four persisted spread columns out of one decoded save board."""

    board = payload.get("board")
    if not isinstance(board, list):
        return {}
    cells: dict[tuple[str, int, int], dict[str, Any]] = {}
    for row_index, row in enumerate(board, start=1):
        if not isinstance(row, list):
            continue
        for col_index, cell in enumerate(row, start=1):
            if not isinstance(cell, dict):
                continue
            extra = cell.get("extra")
            if not isinstance(extra, dict):
                continue
            state = extra.get("spreadState")
            if state in (None, SPREAD_TIMER_ABSENT):
                continue
            timer = extra.get("spreadStartTimer")
            cells[(str(cell.get("type")), row_index, col_index)] = {
                "state": state,
                "timer": None if timer in (None, SPREAD_TIMER_ABSENT) else timer,
                "item_rest": extra.get("spreadItemRestNumber"),
                "storage_rest": extra.get("spreadStorageRestNumber"),
            }
    return cells


def _epoch(wall: str | None) -> float | None:
    if not wall:
        return None
    try:
        return datetime.fromisoformat(str(wall).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _cooldown_refills(
    previous: dict[tuple[str, int, int], dict[str, Any]],
    current: dict[tuple[str, int, int], dict[str, Any]],
    *,
    previous_epoch: float | None,
    current_epoch: float | None,
) -> list[dict[str, Any]]:
    """One entry per generator whose per-tap stock went up between two reads.

    The earlier read has to carry a running timer, otherwise there is no armed
    moment to check the refill against.
    """

    if previous_epoch is None or current_epoch is None:
        return []
    samples: list[dict[str, Any]] = []
    for key, before in previous.items():
        after = current.get(key)
        if after is None:
            continue
        rest_before, rest_after = before.get("item_rest"), after.get("item_rest")
        if not isinstance(rest_before, int) or not isinstance(rest_after, int):
            continue
        if rest_after <= rest_before or before.get("timer") is None:
            continue
        if before.get("state") != SPREAD_STATE_OPENED:
            continue
        item_type, row, col = key
        samples.append(
            {
                "item_type": item_type,
                "row": row,
                "col": col,
                "armed_at_epoch": before["timer"],
                "storage_rest_before": before.get("storage_rest"),
                "item_rest_before": rest_before,
                "item_rest_after": rest_after,
                "read_before_epoch": previous_epoch,
                "read_after_epoch": current_epoch,
            }
        )
    return samples


def _runtime_key(entry: dict[str, Any]) -> tuple[str, str, str] | None:
    item_type = entry.get("item_type")
    if not item_type:
        return None
    return str(item_type), str(entry.get("row")), str(entry.get("col"))


def _replay_aggregate_facts(
    states_path: Path,
    *,
    report_ref: str,
    report_captured_at: str,
    evidence_root: Path,
) -> list[FactV1]:
    """Derive per-round and per-run aggregates that F6-02 rules consume."""

    if not states_path.is_file():
        return []
    replay_ref = _relative(states_path, evidence_root)
    facts: list[FactV1] = []
    runtime: dict[tuple[str, str, str], dict[str, Any]] = {}
    orders: dict[str, dict[str, Any]] = {}
    board: dict[str, Any] | None = None
    cooldown: dict[tuple[str, int, int], dict[str, Any]] = {}
    previous_spread: dict[tuple[str, int, int], dict[str, Any]] = {}
    previous_spread_epoch: float | None = None
    previous_spread_wall: str = report_captured_at
    with states_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            payload = record.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            if record.get("source") == "save_diff":
                # F6-05: only the decoded save carries the raw spread columns
                # with a per-read wall clock, which is what the cooldown check
                # needs.  Round payloads keep a summarised copy without one.
                wall = str(record.get("t_wall") or report_captured_at)
                current_spread = _spread_cells(payload)
                current_epoch = _epoch(wall)
                for sample in _cooldown_refills(
                    previous_spread,
                    current_spread,
                    previous_epoch=previous_spread_epoch,
                    current_epoch=current_epoch,
                ):
                    key = (sample["item_type"], sample["row"], sample["col"])
                    bucket = cooldown.setdefault(
                        key,
                        {
                            "refills": [],
                            "first_seen_at": previous_spread_wall,
                            "last_seen_at": wall,
                        },
                    )
                    bucket["refills"].append(sample)
                    bucket["last_seen_at"] = wall
                if current_spread:
                    previous_spread = current_spread
                    previous_spread_epoch = current_epoch
                    previous_spread_wall = wall
                continue
            if record.get("source") != "planner":
                continue
            wall = str(record.get("t_wall") or report_captured_at)
            before_state = payload.get("state_before") or {}
            matrix = before_state.get("board_matrix_7x9")
            if board is None and isinstance(matrix, list):
                board = {"matrix": matrix, "captured_at": wall, "round": payload.get("round")}
            for entry in before_state.get("generator_runtime") or []:
                key = _runtime_key(entry)
                if key is None:
                    continue
                state = runtime.setdefault(
                    key,
                    {
                        "samples": 0,
                        "item_rest_max": None,
                        "storage_rest_max": None,
                        "recovery_wait_seconds": set(),
                        "first_seen_at": wall,
                        "last_seen_at": wall,
                    },
                )
                state["samples"] += 1
                state["last_seen_at"] = wall
                for source, target in (
                    ("spreadItemRestNumber", "item_rest_max"),
                    ("spreadStorageRestNumber", "storage_rest_max"),
                ):
                    value = entry.get(source)
                    if isinstance(value, (int, float)):
                        current = state[target]
                        state[target] = value if current is None else max(current, value)
                wait = entry.get("recovery_wait_seconds")
                if isinstance(wait, (int, float)):
                    state["recovery_wait_seconds"].add(wait)
            for order in before_state.get("orders") or []:
                order_id = str(order.get("order_id") or "")
                if not order_id or order_id in orders:
                    continue
                orders[order_id] = {
                    "order_id": order_id,
                    "order_type": order.get("order_type"),
                    "required_items": order.get("required_items") or [],
                    "reward_coins": order.get("reward_coins"),
                    "captured_at": wall,
                    "round": payload.get("round"),
                }
            for clear in _paperbox_clear_samples(payload):
                facts.append(
                    _make_fact(
                        kind="paperbox_clear",
                        before={
                            "row": clear["row"],
                            "col": clear["col"],
                            "cell_before": clear["cell_before"],
                        },
                        after={
                            "cell_after": clear["cell_after"],
                            "merge_target_row": clear["merge_target_row"],
                            "merge_target_col": clear["merge_target_col"],
                            "merge_result_type": clear["merge_result_type"],
                            "merge_action_id": clear["merge_action_id"],
                            "merge_prediction_id": clear["merge_prediction_id"],
                            "adjacent_to_merge_target": clear["adjacent"],
                            "cleared_with": clear["cleared_with"],
                        },
                        delta={},
                        evidence_refs=[
                            f"{report_ref}#round={payload.get('round')};"
                            f"paperbox_clear={clear['row']},{clear['col']}",
                            f"{replay_ref}#t_mono_ns={record.get('t_mono_ns')}",
                        ],
                        source_report=report_ref,
                        captured_at=wall,
                        evidence_id=(
                            f"paperbox-clear:{payload.get('round')}:"
                            f"{clear['row']}:{clear['col']}"
                        ),
                    )
                )
            sample = _generator_output_sample(payload)
            if sample:
                generator, produced = sample
                facts.append(
                    _make_fact(
                        kind="generator_output_sample",
                        before={"source_type": generator},
                        after={"produced_types": produced},
                        delta={"energy": -len(produced)},
                        evidence_refs=[
                            f"{report_ref}#round={payload.get('round')};generator={generator}",
                            f"{replay_ref}#t_mono_ns={record.get('t_mono_ns')}",
                        ],
                        source_report=report_ref,
                        captured_at=wall,
                        evidence_id=f"generator-output:{payload.get('round')}",
                    )
                )
            round_number = payload.get("round")
            after_state = payload.get("state_after") or {}
            plane_before = _trigger_plane_row(before_state)
            plane_after = _trigger_plane_row(after_state)
            if plane_before is not None and plane_after is not None:
                counts = _adjudication_counts(payload)
                produced = _produced_types(payload)
                confirmed_generates = counts.get("generate:confirmed", 0) + counts.get(
                    "generate:confirmed_with_deviation", 0
                )
                energy_before = before_state.get("energy")
                energy_after = after_state.get("energy")
                visible_spend = (
                    energy_before - energy_after
                    if isinstance(energy_before, (int, float))
                    and isinstance(energy_after, (int, float))
                    else None
                )
                plane_delta = {
                    name: plane_after[name] - plane_before[name]
                    for name in ("energyConsumed", "score", "level")
                    if isinstance(plane_before.get(name), (int, float))
                    and isinstance(plane_after.get(name), (int, float))
                }
                facts.append(
                    _make_fact(
                        kind="trigger_plane_round",
                        before={
                            **plane_before,
                            "round": round_number,
                            "energy": energy_before,
                        },
                        after={
                            **plane_after,
                            "energy": energy_after,
                            "produced_types": produced,
                            "adjudications": counts,
                        },
                        delta={
                            **plane_delta,
                            "confirmed_generates": confirmed_generates,
                            "expected_generate_cost": (
                                confirmed_generates * GENERATE_COST_PER_ITEM
                            ),
                            "visible_energy_spend": visible_spend,
                        },
                        evidence_refs=[
                            f"{report_ref}#round={round_number};"
                            "activity_trigger_plane",
                            f"{replay_ref}#t_mono_ns={record.get('t_mono_ns')}",
                        ],
                        source_report=report_ref,
                        captured_at=wall,
                        evidence_id=f"trigger-plane-round:{round_number}",
                    )
                )
                level_gain = plane_delta.get("level")
                if isinstance(level_gain, (int, float)) and level_gain > 0:
                    facts.append(
                        _make_fact(
                            kind="trigger_plane_reward",
                            before={
                                "level": plane_before.get("level"),
                                "score": plane_before.get("score"),
                                "round": round_number,
                                "resources": _resource_snapshot(before_state),
                            },
                            after={
                                "level": plane_after.get("level"),
                                "score": plane_after.get("score"),
                                "resources": _resource_snapshot(after_state),
                                "board_items_gained": produced,
                            },
                            delta=_resource_delta(before_state, after_state),
                            evidence_refs=[
                                f"{report_ref}#round={round_number};"
                                "trigger_plane_reward",
                                f"{replay_ref}#t_mono_ns={record.get('t_mono_ns')}",
                            ],
                            source_report=report_ref,
                            captured_at=wall,
                            evidence_id=f"trigger-plane-reward:{round_number}",
                        )
                    )
            for index, blocked in enumerate(
                (payload.get("plan") or {}).get("blocked_actions") or []
            ):
                if not isinstance(blocked, dict):
                    continue
                reason = str(blocked.get("reason") or "")
                if "reserve" not in reason.lower():
                    continue
                prediction = blocked.get("prediction") or {}
                facts.append(
                    _make_fact(
                        kind="order_reserve_block",
                        before={
                            "round": round_number,
                            "action_kind": blocked.get("kind"),
                            "intent": blocked.get("intent"),
                            "reason": reason,
                            "prediction_id": prediction.get("prediction_id"),
                        },
                        after={
                            "open_orders": len(before_state.get("orders") or []),
                            "mergeable_pairs": before_state.get("mergeable_pairs"),
                        },
                        delta={},
                        evidence_refs=[
                            f"{report_ref}#round={round_number};"
                            f"blocked_action={index}",
                            f"{replay_ref}#t_mono_ns={record.get('t_mono_ns')}",
                        ],
                        source_report=report_ref,
                        captured_at=wall,
                        evidence_id=f"order-reserve-block:{round_number}:{index}",
                    )
                )
            for entry in payload.get("adjudication") or []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("status") != "confirmed_with_deviation":
                    continue
                prediction_id = str(entry.get("prediction_id") or "")
                facts.append(
                    _make_fact(
                        kind="generate_deviation",
                        before={
                            "round": round_number,
                            "prediction_id": prediction_id,
                            "action_kind": entry.get("kind"),
                            "origin": "replay",
                        },
                        after={
                            "reason": entry.get("reason"),
                            "deviation": entry.get("deviation") or {},
                        },
                        delta={},
                        evidence_refs=[
                            f"{report_ref}#round={round_number};"
                            f"prediction_id={prediction_id}",
                            f"{replay_ref}#t_mono_ns={record.get('t_mono_ns')}",
                        ],
                        source_report=report_ref,
                        captured_at=wall,
                        evidence_id=(
                            f"generate-deviation:{round_number}:{prediction_id}"
                        ),
                    )
                )
    for (item_type, row, col), state in runtime.items():
        facts.append(
            _make_fact(
                kind="generator_runtime",
                before={"source_type": item_type, "row": row, "col": col},
                after={
                    "item_rest_max": state["item_rest_max"],
                    "storage_rest_max": state["storage_rest_max"],
                    "recovery_wait_seconds": sorted(state["recovery_wait_seconds"]),
                    "samples": state["samples"],
                    "first_seen_at": state["first_seen_at"],
                    "last_seen_at": state["last_seen_at"],
                },
                delta={},
                evidence_refs=[
                    f"{report_ref}#generator_runtime={item_type}@{row},{col}",
                    f"{replay_ref}#generator_runtime={item_type}@{row},{col}",
                ],
                source_report=report_ref,
                captured_at=state["last_seen_at"],
                evidence_id=f"generator-runtime:{item_type}:{row}:{col}",
            )
        )
    for (item_type, row, col), bucket in cooldown.items():
        refills = bucket["refills"]
        windows = [
            {
                "armed_at_epoch": entry["armed_at_epoch"],
                "storage_empty": entry["storage_rest_before"] == 0,
                "read_before_epoch": round(entry["read_before_epoch"], 3),
                "read_after_epoch": round(entry["read_after_epoch"], 3),
                "elapsed_at_read_after_s": round(
                    entry["read_after_epoch"] - entry["armed_at_epoch"], 3
                ),
                "item_rest_before": entry["item_rest_before"],
                "item_rest_after": entry["item_rest_after"],
            }
            for entry in refills
        ]
        facts.append(
            _make_fact(
                kind="generator_cooldown",
                before={"source_type": item_type, "row": str(row), "col": str(col)},
                after={"refill_windows": windows, "samples": len(windows)},
                delta={},
                evidence_refs=[
                    f"{report_ref}#generator_cooldown={item_type}@{row},{col}",
                    f"{replay_ref}#generator_cooldown={item_type}@{row},{col}",
                ],
                source_report=report_ref,
                captured_at=bucket["last_seen_at"],
                evidence_id=f"generator-cooldown:{item_type}:{row}:{col}",
            )
        )
    for order in orders.values():
        facts.append(
            _make_fact(
                kind="order_appearance",
                before={},
                after={
                    "order_id": order["order_id"],
                    "order_type": order["order_type"],
                    "required_items": order["required_items"],
                    "reward_coins": order["reward_coins"],
                },
                delta={},
                evidence_refs=[
                    f"{report_ref}#round={order['round']};order_id={order['order_id']}",
                    f"{replay_ref}#order_id={order['order_id']}",
                ],
                source_report=report_ref,
                captured_at=order["captured_at"],
                evidence_id=f"order-appearance:{order['order_id']}",
            )
        )
    if board is not None:
        facts.append(
            _make_fact(
                kind="board_snapshot",
                before={},
                after={"board_matrix_7x9": board["matrix"], "round": board["round"]},
                delta={},
                evidence_refs=[
                    f"{report_ref}#round={board['round']};board_matrix_7x9",
                    f"{replay_ref}#board_matrix_7x9",
                ],
                source_report=report_ref,
                captured_at=board["captured_at"],
                evidence_id="board-snapshot",
            )
        )
    return facts


def _parse_wall(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _save_states(
    states_path: Path, *, report_ref: str, evidence_root: Path
) -> list[dict[str, Any]]:
    """Read the authoritative save reads one run wrote into its replay.

    ``SaveChannel`` stores every decoded state under the ``save_diff`` source,
    so these are decrypted save snapshots with a wall clock, not screen reads.
    Chained across runs they carry the settlements a single run report cannot
    show: the run that starts a main task is not the run that observes its
    result.
    """

    if not states_path.is_file():
        return []
    replay_ref = _relative(states_path, evidence_root)
    run_id = states_path.parent.name
    states: list[dict[str, Any]] = []
    with states_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            payload = record.get("payload") or {}
            if record.get("source") != "save_diff" or not isinstance(payload, dict):
                continue
            captured_at = str(record.get("t_wall") or "")
            epoch = _parse_wall(captured_at)
            if epoch is None:
                continue
            state = {key: payload.get(key) for key in _RESOURCE_FIELDS}
            state.update(
                {
                    "captured_at": captured_at,
                    "epoch": epoch,
                    "run_id": run_id,
                    "main_task_id": _task_id(payload),
                    "main_task_cost": _task_cost(payload),
                    "snapshot_sha256": payload.get("snapshot_sha256"),
                    "report_ref": report_ref,
                    "replay_ref": replay_ref,
                }
            )
            states.append(state)
    return states


def _state_marker(state: dict[str, Any]) -> str:
    return str(state.get("snapshot_sha256") or state.get("captured_at") or "")


def _state_refs(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return [
        f"{state['report_ref']}#save_diff_t_wall={state['captured_at']}"
        for state in (before, after)
    ] + [
        f"{state['replay_ref']}#t_wall={state['captured_at']}"
        for state in (before, after)
    ]


def _resources_held(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Whether nothing but energy moved between two save reads."""

    return all(
        before.get(key) is not None and before.get(key) == after.get(key)
        for key in ("coins", "gems", "xp", "level")
    ) and before.get("main_task_id") == after.get("main_task_id")


def _save_timeline_facts(
    states: list[dict[str, Any]], *, known_transitions: set[tuple[str, str]]
) -> list[FactV1]:
    """Derive settlement, idle-recovery and free-refill facts from save reads.

    Main-task transitions a run report already carries are left to that report;
    only the ones no single report saw are filled in here.
    """

    ordered = sorted(states, key=lambda state: (state["epoch"], _state_marker(state)))
    facts: list[FactV1] = []
    for before, after in zip(ordered, ordered[1:]):
        elapsed = after["epoch"] - before["epoch"]
        refs = _state_refs(before, after)
        marker = f"{_state_marker(before)}:{_state_marker(after)}"
        left_task = str(before.get("main_task_id") or "")
        right_task = str(after.get("main_task_id") or "")
        if left_task and right_task and left_task != right_task:
            if (left_task, right_task) not in known_transitions:
                fact = _main_task_fact(
                    before,
                    after,
                    report_ref=before["report_ref"],
                    captured_at=after["captured_at"],
                    evidence_id=f"save-timeline:{marker}",
                    evidence_refs=refs,
                )
                if fact:
                    facts.append(fact)
            continue
        energy_before, energy_after = before.get("energy"), after.get("energy")
        if not isinstance(energy_before, int) or not isinstance(energy_after, int):
            continue
        gained = energy_after - energy_before
        if not _resources_held(before, after):
            continue
        if gained == FREE_REFILL_ENERGY:
            facts.append(
                _make_fact(
                    kind="energy_refill_free",
                    before={"energy": energy_before, "gems": before.get("gems")},
                    after={"energy": energy_after, "gems": after.get("gems")},
                    delta={"energy": gained, "gems": 0, "elapsed_seconds": elapsed},
                    evidence_refs=refs,
                    source_report=before["report_ref"],
                    captured_at=after["captured_at"],
                    evidence_id=f"energy-refill:{marker}",
                )
            )
            continue
        # An idle gap between two runs is the only interval where nothing but
        # the recovery clock can move energy; a capped reading measures the
        # ceiling, not the clock.
        if (
            gained <= 0
            or elapsed < ENERGY_RECOVERY_SECONDS
            or before["run_id"] == after["run_id"]
            or energy_after >= ENERGY_MAX
        ):
            continue
        facts.append(
            _make_fact(
                kind="energy_recovery",
                before={"energy": energy_before, "captured_at": before["captured_at"]},
                after={"energy": energy_after, "captured_at": after["captured_at"]},
                delta={"energy": gained, "elapsed_seconds": elapsed},
                evidence_refs=refs,
                source_report=before["report_ref"],
                captured_at=after["captured_at"],
                evidence_id=f"energy-recovery:{marker}",
            )
        )
    return facts


# --------------------------------------------------------------------------
# F6-04.  Five evidence families the earlier indexers never read: the save
# difference a guard claim or close actually moved, the order display rank, the
# per-stage save read timing, and the guard refusals and stop reasons.
# --------------------------------------------------------------------------

# main_task_flow.CLAIM_SAVE_EVIDENCE_KINDS plus the dismiss vocabulary F6-04
# names beside close.  These are the guard actions whose effect on the account
# is measured against the save rather than asserted from the whitelist.
CLAIM_ACTION_KINDS = frozenset({"free-claim", "close", "dismiss"})
# Of those, the two whose declared effect is nothing at all.
NON_GRANTING_ACTION_KINDS = frozenset({"close", "dismiss"})

# F6-04 section 1.3.  The two candidate display orders F4-09 could not tell
# apart: both put the fulfillable orders first and differ only in how they
# break ties among the rest.  Recorded, never judged — the corpus has never
# shown a green button anywhere but the leftmost tray, so the tie-break has
# never been observable.
DISPLAY_ORDER_CANDIDATES = (
    "fulfillable_then_slot_descending",
    "fulfillable_then_created_descending",
)

# F6-04 section 1.5.  Timeline notes and alerts that mark a guard state being
# entered or left, keyed by the token the message starts with.  Only the states
# the guard itself enters and leaves belong here; the reasons a segment stopped
# on are already counted from ``loop.stop_reason``, and counting their alerts as
# a state as well would double them.
GUARD_STATE_TOKENS = (
    "board_full_under_guide",
    "tutorial_follow_stalled",
    "save_channel_failed",
    "forbidden_surface_recurring",
)


def _load_json(path: Path) -> Any:
    """Read one JSON document, or ``None`` when it cannot be parsed.

    The gameplay line writes reports into the same tree while this indexer
    runs, so a file can be caught half-written.  A report that does not parse
    is skipped and counted rather than failing the whole index.
    """

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _iter_ndjson(path: Path) -> Iterable[dict[str, Any]]:
    """Yield the records of an NDJSON file, skipping lines that do not parse."""

    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(record, dict):
                yield record


def _claim_records(report: dict[str, Any]) -> Iterable[tuple[Any, str, dict[str, Any]]]:
    """Yield ``(round, source, record)`` for every guard and flow popup action."""

    for round_item in ((report.get("loop") or {}).get("rounds") or []):
        if not isinstance(round_item, dict):
            continue
        number = round_item.get("round")
        for record in round_item.get("screen_guard_actions") or []:
            if isinstance(record, dict):
                yield number, "screen_guard", record
        for result in round_item.get("main_task_results") or []:
            if not isinstance(result, dict):
                continue
            for record in result.get("popup_actions") or []:
                if isinstance(record, dict):
                    yield number, "main_task_flow", record


def _window_action_kinds(delta: dict[str, Any]) -> set[str]:
    """The action kinds named inside one measurement window."""

    kinds = set()
    for entry in delta.get("guard_actions_in_window") or []:
        parts = str(entry).split(":")
        if len(parts) >= 2:
            kinds.add(parts[1])
    return kinds


def _free_claim_facts(
    report: dict[str, Any], report_ref: str, captured_at: str
) -> list[FactV1]:
    """One fact per claim-class guard action, carrying the save it moved.

    F6-04 section 1.1.  ``PopupSaveDeltaV1`` bounds its measurement by two save
    reads, so the difference belongs to the whole window, not to one tap.  The
    fact says so: ``attributable`` is true only when every guard action inside
    that window is itself a claim-class action, which is the only case where
    the difference can be read as what the claim granted.
    """

    facts: list[FactV1] = []
    for number, source, record in _claim_records(report):
        action_kind = str(record.get("kind") or "")
        if action_kind not in CLAIM_ACTION_KINDS:
            continue
        action_id = str(record.get("action_id") or f"round-{number}-{source}")
        raw = record.get("save_delta")
        delta_payload: dict[str, Any] = raw if isinstance(raw, dict) else {}
        measured = bool(delta_payload.get("available"))
        window_kinds = _window_action_kinds(delta_payload)
        attributable = bool(
            measured and window_kinds and window_kinds <= CLAIM_ACTION_KINDS
        )
        board_items = delta_payload.get("board_items_delta") or {}
        gained = sum(
            value for value in board_items.values() if isinstance(value, int) and value > 0
        )
        lost = -sum(
            value for value in board_items.values() if isinstance(value, int) and value < 0
        )
        resource_delta = {
            key: value
            for key, value in (delta_payload.get("delta") or {}).items()
            if isinstance(value, (int, float))
        }
        resource_delta["board_items_gained"] = gained
        resource_delta["board_items_lost"] = lost
        facts.append(
            _make_fact(
                kind="free_claim_delta",
                before={
                    "screen": record.get("screen"),
                    "action_kind": action_kind,
                    "element": record.get("element"),
                    "source": source,
                    "round": number,
                    "action_id": action_id,
                    "measured": measured,
                    "attributable": attributable,
                    "window": delta_payload.get("window"),
                    "group_id": delta_payload.get("group_id"),
                    "group_action_ids": delta_payload.get("group_action_ids") or [],
                    "guard_actions_in_window": (
                        delta_payload.get("guard_actions_in_window") or []
                    ),
                    "unavailable_reason": delta_payload.get("reason"),
                    "resources": delta_payload.get("before") or {},
                },
                after={
                    "resources": delta_payload.get("after") or {},
                    "board_items_delta": board_items,
                    "read_seconds": delta_payload.get("read_s"),
                },
                delta=resource_delta,
                evidence_refs=[f"{report_ref}#round={number};action_id={action_id}"],
                source_report=report_ref,
                captured_at=captured_at,
                evidence_id=f"free-claim:{action_id}",
            )
        )
    return facts


def _created_rank(order: dict[str, Any]) -> int:
    """Creation order of one order, approximated by its numeric id.

    Generated orders carry an epoch-millisecond id; the seed orders the account
    started with carry small integers.  Ascending numeric order is therefore
    creation order for both.  A non-numeric id sorts first and is reported as
    such rather than guessed at.
    """

    text = str(order.get("order_id") or "")
    return int(text) if text.isdigit() else 0


def _candidate_display_order(
    orders: list[dict[str, Any]], candidate: str
) -> list[dict[str, Any]]:
    """Order the tray the way one candidate rule says the game does."""

    if candidate == "fulfillable_then_slot_descending":
        key = lambda order: (  # noqa: E731 - a sort key reads better inline here
            not bool(order.get("fulfillable_now")),
            -int(order.get("slot") or 0),
        )
    else:
        key = lambda order: (  # noqa: E731
            not bool(order.get("fulfillable_now")),
            -_created_rank(order),
        )
    return sorted(orders, key=key)


def _order_display_fact(
    *,
    orders: list[dict[str, Any]],
    button_positions: list[int] | None,
    delivered_display_position: int | None,
    delivered_save_slot: Any,
    delivered_order_type: Any,
    coins_delta: Any,
    counts_agree: bool | None,
    source: str,
    run: Any,
    round_number: Any,
    frame_sha256: Any,
    report_ref: str,
    captured_at: str,
    evidence_id: str,
) -> FactV1:
    """Record one tray reading: what the save held and where the buttons were."""

    trimmed = [
        {
            "slot": order.get("slot"),
            "order_id": order.get("order_id"),
            "order_type": order.get("order_type"),
            "fulfillable_now": bool(order.get("fulfillable_now")),
            "reward_coins": order.get("reward_coins"),
        }
        for order in orders
        if isinstance(order, dict)
    ]
    fulfillable = [order for order in trimmed if order["fulfillable_now"]]
    fulfillable_slots = [order["slot"] for order in fulfillable]
    leftmost: bool | None = None
    if button_positions is not None and counts_agree and fulfillable:
        # "Deliverable always leftmost" says the buttons occupy exactly the
        # first positions of the tray, one per fulfillable order.
        leftmost = sorted(button_positions) == list(range(1, len(fulfillable) + 1))
    candidates: dict[str, Any] = {}
    predicted_orders: dict[str, list[Any]] = {}
    for candidate in DISPLAY_ORDER_CANDIDATES:
        ordered = _candidate_display_order(trimmed, candidate)
        predicted_orders[candidate] = [order.get("order_id") for order in ordered]
        predicted_positions = sorted(
            index
            for index, order in enumerate(ordered, start=1)
            if order["fulfillable_now"]
        )
        hit: bool | None = None
        if button_positions is not None and counts_agree and fulfillable:
            hit = predicted_positions == sorted(button_positions)
        candidates[candidate] = {
            "predicted_button_positions": predicted_positions,
            "hit": hit,
        }
    distinct = {tuple(value) for value in predicted_orders.values()}
    return _make_fact(
        kind="order_display_rank",
        before={
            "source": source,
            "run": run,
            "round": round_number,
            "frame_sha256": frame_sha256,
            "orders": trimmed,
        },
        after={
            "button_display_positions": button_positions,
            "fulfillable_slots": fulfillable_slots,
            "fulfillable_count": len(fulfillable),
            "save_and_screen_agree_on_count": counts_agree,
            "delivered_display_position": delivered_display_position,
            "delivered_save_slot": delivered_save_slot,
            "delivered_order_type": delivered_order_type,
            "deliverable_leftmost": leftmost,
            "candidates": candidates,
            "candidates_agree": len(distinct) == 1,
        },
        delta={"coins": coins_delta} if isinstance(coins_delta, (int, float)) else {},
        evidence_refs=[f"{report_ref}#{evidence_id}"],
        source_report=report_ref,
        captured_at=captured_at,
        evidence_id=evidence_id,
    )


def _order_display_facts_from_forensics(
    report: dict[str, Any], report_ref: str, captured_at: str
) -> list[FactV1]:
    """Read the F4-09 display-order forensics corpus into the fact stream."""

    patterns = report.get("button_patterns")
    if not isinstance(patterns, list):
        return []
    pairings = {
        str(entry.get("frame_id")): entry
        for entry in report.get("confirmed_pairings") or []
        if isinstance(entry, dict) and entry.get("frame_id")
    }
    facts = []
    for entry in patterns:
        if not isinstance(entry, dict):
            continue
        frame_id = str(entry.get("frame_id") or "")
        pairing = pairings.get(frame_id) or {}
        delivered_slot = pairing.get("save_slot")
        delivered_type = next(
            (
                order.get("order_type")
                for order in entry.get("orders") or []
                if isinstance(order, dict) and order.get("slot") == delivered_slot
            ),
            None,
        )
        facts.append(
            _order_display_fact(
                orders=entry.get("orders") or [],
                button_positions=sorted(entry.get("observed_button_trays") or []),
                delivered_display_position=pairing.get("display_position"),
                delivered_save_slot=delivered_slot,
                delivered_order_type=delivered_type,
                coins_delta=pairing.get("coins_delta"),
                counts_agree=entry.get("save_and_screen_agree_on_count"),
                source="order-display-forensics",
                run=entry.get("run"),
                round_number=entry.get("round"),
                frame_sha256=entry.get("frame_sha256"),
                report_ref=report_ref,
                captured_at=captured_at,
                evidence_id=f"order-display:{frame_id or entry.get('frame_sha256')}",
            )
        )
    return facts


def _order_display_facts_from_delivery(
    report: dict[str, Any], report_ref: str, captured_at: str
) -> list[FactV1]:
    """Read the tray a confirmed delivery was taken from out of a run report.

    A run report records which save slot was delivered, not which tray the
    green button sat in, so these samples carry no button position and take no
    part in the leftmost count.  They still say which orders were fulfillable
    and which one the run delivered.
    """

    facts = []
    for item in report.get("delivery_evidence") or []:
        if not isinstance(item, dict):
            continue
        adjudication = item.get("adjudication")
        if isinstance(adjudication, dict) and adjudication.get("status") != "confirmed":
            continue
        orders = [order for order in item.get("orders_before") or [] if isinstance(order, dict)]
        delivered = next(
            (
                order
                for order in orders
                if str(order.get("order_id")) == str(item.get("order_before"))
            ),
            {},
        )
        action_id = str(item.get("action_id") or f"round-{item.get('round')}")
        facts.append(
            _order_display_fact(
                orders=orders,
                button_positions=None,
                delivered_display_position=None,
                delivered_save_slot=delivered.get("slot", item.get("slot")),
                delivered_order_type=delivered.get("order_type"),
                coins_delta=item.get("actual_coins_delta"),
                counts_agree=None,
                source="delivery-evidence",
                run=None,
                round_number=item.get("round"),
                frame_sha256=None,
                report_ref=report_ref,
                captured_at=captured_at,
                evidence_id=f"order-display-delivery:{action_id}",
            )
        )
    return facts


def _save_read_timing_facts(
    states_path: Path,
    *,
    report_ref: str,
    report_captured_at: str,
    evidence_root: Path,
) -> list[FactV1]:
    """Turn every ``save_read_timing`` payload in a replay into a facility fact.

    F6-04 section 1.4.  ``flush_profile`` is not stored anywhere, but only the
    fast profile installs a ``FlushPlan`` and only a ``FlushPlan`` records a
    focus command, so a recorded command proves the fast profile.  An empty one
    proves nothing, and says so rather than claiming the verified profile.
    """

    if not states_path.is_file():
        return []
    replay_ref = _relative(states_path, evidence_root)
    run_id = states_path.parent.name
    facts: list[FactV1] = []
    for record in _iter_ndjson(states_path):
        payload = record.get("payload") or {}
        if record.get("source") != "save_diff" or not isinstance(payload, dict):
            continue
        timing = payload.get("save_read_timing")
        if not isinstance(timing, dict):
            continue
        focus = str(timing.get("flush_focus_command") or "")
        marker = str(record.get("t_mono_ns") or record.get("t_wall") or "")
        facts.append(
            _make_fact(
                kind="save_read_timing",
                before={
                    "run_id": run_id,
                    "flush_profile": "fast" if focus else "unknown",
                    "flush_profile_basis": (
                        "flush_focus_command recorded, which only the fast profile does"
                        if focus
                        else "no flush_focus_command recorded; the profile is not provable"
                    ),
                },
                after={key: value for key, value in sorted(timing.items())},
                delta={},
                evidence_refs=[
                    f"{report_ref}#save_read_timing_t_mono_ns={marker}",
                    f"{replay_ref}#t_mono_ns={marker}",
                ],
                source_report=report_ref,
                captured_at=str(record.get("t_wall") or report_captured_at),
                evidence_id=f"save-read-timing:{run_id}:{marker}",
            )
        )
    return facts


def _guard_event_fact(
    *,
    event: str,
    detail: dict[str, Any],
    report_ref: str,
    captured_at: str,
    evidence_id: str,
    evidence_refs: list[str] | None = None,
) -> FactV1:
    return _make_fact(
        kind="guard_event",
        before={"event": event, **detail},
        after={},
        delta={},
        evidence_refs=evidence_refs or [f"{report_ref}#{evidence_id}"],
        source_report=report_ref,
        captured_at=captured_at,
        evidence_id=evidence_id,
    )


def _guard_event_facts(
    report: dict[str, Any], report_ref: str, captured_at: str
) -> list[FactV1]:
    """Guard actions, red-line refusals and stop reasons carried by one report."""

    facts: list[FactV1] = []
    for number, source, record in _claim_records(report):
        action_id = str(record.get("action_id") or f"round-{number}-{source}")
        facts.append(
            _guard_event_fact(
                event="guard_action",
                detail={
                    "screen": record.get("screen"),
                    "action_kind": record.get("kind"),
                    "element": record.get("element"),
                    "round": number,
                    "source": source,
                    "action_id": action_id,
                },
                report_ref=report_ref,
                captured_at=captured_at,
                evidence_id=f"guard-action:{action_id}",
            )
        )
    audit = report.get("tap_preframe_redline_audit")
    if isinstance(audit, dict):
        for violation in audit.get("violations") or []:
            if not isinstance(violation, dict):
                continue
            action_id = str(violation.get("action_id") or "")
            facts.append(
                _guard_event_fact(
                    event="redline_violation",
                    detail={
                        "screen": violation.get("identified_screen"),
                        "forbidden_surface": violation.get("forbidden_surface"),
                        "intent": violation.get("intent"),
                        "action_id": action_id,
                        "basis": violation.get("basis"),
                    },
                    report_ref=report_ref,
                    captured_at=captured_at,
                    evidence_id=f"redline-violation:{action_id}",
                )
            )
    for index, refusal in enumerate(report.get("guard_refusals") or []):
        if not isinstance(refusal, dict):
            continue
        facts.append(
            _guard_event_fact(
                event="pre_frame_refusal",
                detail={
                    "screen": refusal.get("screen"),
                    "step": refusal.get("step"),
                    "frame_id": refusal.get("frame_id"),
                    "intent": refusal.get("intent"),
                },
                report_ref=report_ref,
                captured_at=captured_at,
                evidence_id=f"pre-frame-refusal:{refusal.get('frame_id') or index}",
            )
        )
    loop_stop = ((report.get("loop") or {}).get("stop_reason"))
    if isinstance(loop_stop, str) and loop_stop:
        facts.append(
            _guard_event_fact(
                event="segment_stop",
                detail={"reason": loop_stop},
                report_ref=report_ref,
                captured_at=captured_at,
                evidence_id=f"segment-stop:{report_ref}",
            )
        )
    driver_stop = report.get("stop_reason")
    taxonomy = report.get("stop_taxonomy")
    if isinstance(driver_stop, str) and driver_stop:
        facts.append(
            _guard_event_fact(
                event="driver_stop",
                detail={
                    "reason": driver_stop,
                    "taxonomy": taxonomy if isinstance(taxonomy, dict) else {},
                    "exit_code": report.get("exit_code"),
                },
                report_ref=report_ref,
                captured_at=captured_at,
                evidence_id=f"driver-stop:{report_ref}",
            )
        )
    return facts


def _replay_guard_event_facts(
    replay_dir: Path,
    *,
    report_ref: str,
    report_captured_at: str,
    evidence_root: Path,
) -> list[FactV1]:
    """Refusals and guard-state transitions that only the replay records."""

    facts: list[FactV1] = []
    actions_path = replay_dir / "actions.ndjson"
    if actions_path.is_file():
        actions_ref = _relative(actions_path, evidence_root)
        for record in _iter_ndjson(actions_path):
            outcome = str(record.get("outcome") or "")
            if not outcome.startswith("refused_by_pre_frame"):
                continue
            action_id = str(record.get("action_id") or "")
            surface = outcome.split(":", 1)[1] if ":" in outcome else ""
            facts.append(
                _guard_event_fact(
                    event="pre_frame_refusal",
                    detail={
                        "screen": surface,
                        "intent": record.get("intent"),
                        "action_id": action_id,
                        "frame_id": record.get("pre_frame_id"),
                    },
                    report_ref=report_ref,
                    captured_at=str(record.get("t_wall") or report_captured_at),
                    evidence_id=f"pre-frame-refusal:{action_id}",
                    evidence_refs=[
                        f"{report_ref}#action_id={action_id}",
                        f"{actions_ref}#action_id={action_id}",
                    ],
                )
            )
    timeline_path = replay_dir / "timeline.ndjson"
    if timeline_path.is_file():
        timeline_ref = _relative(timeline_path, evidence_root)
        for record in _iter_ndjson(timeline_path):
            if record.get("kind") not in {"note", "alert"}:
                continue
            text = str(record.get("note") or "")
            token = next(
                (name for name in GUARD_STATE_TOKENS if text.startswith(name)), None
            )
            if token is None:
                continue
            marker = str(record.get("t_mono_ns") or record.get("t_wall") or "")
            facts.append(
                _guard_event_fact(
                    event="guard_state",
                    detail={
                        "state": token,
                        "severity": record.get("kind"),
                        "transition": "left" if " left because " in text else "entered",
                        "note": text,
                        "run_id": record.get("run_id"),
                    },
                    report_ref=report_ref,
                    captured_at=str(record.get("t_wall") or report_captured_at),
                    evidence_id=f"guard-state:{token}:{record.get('run_id')}:{marker}",
                    evidence_refs=[
                        f"{report_ref}#guard_state={token}",
                        f"{timeline_ref}#t_mono_ns={marker}",
                    ],
                )
            )
    return facts


# The two ways a story dialogue is advanced.  The guard taps it as a popup
# action, the main-task flow taps it as a numbered step of its own; a reader
# that counts story progress has to know which stream a tap came from or it
# counts the same moment twice.
STORY_ADVANCE_PREFIXES = {
    "popup:story-advance:": "screen_guard",
    "main-task:story:": "main_task_flow",
}


def _frame_digests(frames_path: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    if not frames_path.is_file():
        return digests
    for record in _iter_ndjson(frames_path):
        frame_id = record.get("frame_id")
        digest = record.get("sha256")
        if isinstance(frame_id, str) and isinstance(digest, str):
            digests[frame_id] = digest
    return digests


def _replay_main_task_id(states_path: Path) -> str | None:
    """The main task the replay was on, read from the first round that had one."""

    if not states_path.is_file():
        return None
    for record in _iter_ndjson(states_path):
        if record.get("source") != "planner":
            continue
        payload = record.get("payload") or {}
        task = (payload.get("state_before") or {}).get("main_task")
        if isinstance(task, dict):
            task_id = task.get("task_id") or task.get("curId")
            if task_id:
                return str(task_id)
    return None


def _story_advance_facts(
    replay_dir: Path,
    *,
    report_ref: str,
    report_captured_at: str,
    evidence_root: Path,
) -> list[FactV1]:
    """One fact per story-dialogue advance, stamped with the frame it acted on.

    F7-04 section 1.3.  The planner ruling of 2026-09-07 made advancing a story
    dialogue legitimate play and gave it its own popup kind, which means the
    guard and the main-task flow can both be advancing story at different
    moments of the same run.  Each tap therefore carries the stream it came
    from and the digest of the frame it was taken against, so the two counts
    can be added without any moment being counted twice.
    """

    actions_path = replay_dir / "actions.ndjson"
    if not actions_path.is_file():
        return []
    actions_ref = _relative(actions_path, evidence_root)
    digests = _frame_digests(replay_dir / "frames.ndjson")
    main_task_id = _replay_main_task_id(replay_dir / "states.ndjson")
    facts: list[FactV1] = []
    sequence: dict[str, int] = {}
    for record in _iter_ndjson(actions_path):
        intent = str(record.get("intent") or "")
        stream = next(
            (
                name
                for prefix, name in STORY_ADVANCE_PREFIXES.items()
                if intent.startswith(prefix)
            ),
            None,
        )
        if stream is None:
            continue
        sequence[stream] = sequence.get(stream, 0) + 1
        action_id = str(record.get("action_id") or "")
        pre_frame_id = record.get("pre_frame_id")
        facts.append(
            _make_fact(
                kind="story_advance",
                before={
                    "stream": stream,
                    "sequence": sequence[stream],
                    "intent": intent,
                    "main_task_id": main_task_id,
                    "action_id": action_id,
                    "run_id": record.get("run_id"),
                    "t_mono_ns": record.get("t_mono_ns"),
                },
                after={
                    "pre_frame_id": pre_frame_id,
                    "pre_frame_sha256": digests.get(str(pre_frame_id)),
                    "outcome": record.get("outcome"),
                    "duration_ms": record.get("duration_ms"),
                },
                delta={},
                evidence_refs=[
                    f"{report_ref}#action_id={action_id}",
                    f"{actions_ref}#action_id={action_id}",
                ],
                source_report=report_ref,
                captured_at=str(record.get("t_wall") or report_captured_at),
                evidence_id=f"story-advance:{action_id or intent}:{sequence[stream]}",
            )
        )
    return facts


def _relaxation_deviation_facts(
    report: dict[str, Any], report_ref: str, captured_at: str
) -> list[FactV1]:
    """The deviations a whole-library re-adjudication found, one fact each.

    F4-11 section 2 relaxed the generation verdict and re-scored every report
    already on disk.  Those rounds keep their original ``refuted`` status in
    the replay they were written from, so the only place the new category can
    be read is the re-adjudication report itself.
    """

    relaxation = report.get("verdict_relaxation")
    if not isinstance(relaxation, dict):
        return []
    facts: list[FactV1] = []
    for entry in relaxation.get("deviations") or []:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("report") or "")
        round_number = entry.get("round")
        prediction_id = str(entry.get("prediction_id") or "")
        facts.append(
            _make_fact(
                kind="generate_deviation",
                before={
                    "round": round_number,
                    "prediction_id": prediction_id,
                    "action_kind": "generate",
                    "origin": "verdict_relaxation",
                    "was": entry.get("was"),
                    "rescored_by": report_ref,
                },
                after={
                    "reason": entry.get("reason"),
                    "deviation": entry.get("deviation") or {},
                },
                delta={},
                evidence_refs=[
                    f"{report_ref}#verdict_relaxation.deviations",
                    f"{source}#round={round_number};prediction_id={prediction_id}",
                ],
                # The round belongs to the report it was played in.  Attributing
                # it there is what keeps one re-adjudicated round from becoming
                # as many facts as there are re-adjudication reports.
                source_report=source or report_ref,
                captured_at=captured_at,
                evidence_id=f"generate-deviation:{round_number}:{prediction_id}",
            )
        )
    return facts


def extract_new_kind_facts(
    report: dict[str, Any],
    *,
    report_ref: str,
    captured_at: str,
    evidence_root: Path,
) -> list[FactV1]:
    """The F6-04 fact families, readable from any report that carries them."""

    facts = _free_claim_facts(report, report_ref, captured_at)
    facts.extend(_guard_event_facts(report, report_ref, captured_at))
    facts.extend(_order_display_facts_from_forensics(report, report_ref, captured_at))
    facts.extend(_order_display_facts_from_delivery(report, report_ref, captured_at))
    facts.extend(_relaxation_deviation_facts(report, report_ref, captured_at))
    replay_dir = _replay_path(report)
    if replay_dir is not None:
        facts.extend(
            _save_read_timing_facts(
                replay_dir / "states.ndjson",
                report_ref=report_ref,
                report_captured_at=captured_at,
                evidence_root=evidence_root,
            )
        )
        facts.extend(
            _replay_guard_event_facts(
                replay_dir,
                report_ref=report_ref,
                report_captured_at=captured_at,
                evidence_root=evidence_root,
            )
        )
        facts.extend(
            _story_advance_facts(
                replay_dir,
                report_ref=report_ref,
                report_captured_at=captured_at,
                evidence_root=evidence_root,
            )
        )
    return facts


def extract_facts_from_report(
    report_path: str | Path, *, feasibility_root: str | Path | None = None
) -> list[FactV1]:
    """Extract facts from one allowed report and its referenced replay."""

    path = Path(report_path)
    root = Path(feasibility_root) if feasibility_root else path.parent
    report = json.loads(path.read_text(encoding="utf-8"))
    report_ref = _relative(path, root)
    captured_at = str(
        report.get("captured_at")
        or report.get("updated_at")
        or report.get("ended_at")
        or report.get("started_at")
        or ""
    )
    facts = _delivery_facts(report, report_ref, captured_at)
    facts.extend(_direct_main_task_facts(report, report_ref, captured_at))
    facts.extend(_checkpoint_facts(report, report_ref, captured_at))
    facts.extend(_survey_facts(report, report_ref, captured_at))
    facts.extend(
        extract_new_kind_facts(
            report, report_ref=report_ref, captured_at=captured_at, evidence_root=root
        )
    )
    replay_dir = _replay_path(report)
    if replay_dir:
        facts.extend(
            _planner_facts(
                replay_dir / "states.ndjson",
                report_ref=report_ref,
                report_captured_at=captured_at,
                evidence_root=root,
            )
        )
        facts.extend(
            _replay_aggregate_facts(
                replay_dir / "states.ndjson",
                report_ref=report_ref,
                report_captured_at=captured_at,
                evidence_root=root,
            )
        )
    return list({fact.fact_id: fact for fact in facts}.values())


def iter_report_paths(feasibility_root: str | Path) -> Iterable[Path]:
    """Yield only the report families authorized by F6-01."""

    root = Path(feasibility_root)
    for path in sorted(root.rglob("*.json")):
        relative = path.relative_to(root)
        if (
            relative.parts[0] == "F1-04-live-acceptance"
            and path.name == "live_acceptance.json"
        ) or (len(relative.parts) == 1 and _REPORT_RE.match(path.name)):
            yield path


def iter_supplementary_report_paths(feasibility_root: str | Path) -> Iterable[Path]:
    """Yield the later report families that only the F6-04 extractors read.

    F4-05 onwards — the long runs, the drivers, the display-order forensics and
    the merge-latency probe — carry guard, stop and display evidence but not
    the round-level shape the F6-01 rules were written against.  Keeping them
    in their own iterator means the existing fact families cannot change
    because a new report family appeared.
    """

    root = Path(feasibility_root)
    for path in sorted(root.glob("F4-0*.json")):
        if not _REPORT_RE.match(path.name):
            yield path


def iter_aggregate_report_paths(feasibility_root: str | Path) -> Iterable[Path]:
    """Yield the aggregate task reports that re-score evidence already on disk.

    F7-04 section 1.5.  A re-adjudication is not a run: it names rounds in
    other reports and says what their verdict should now be.  It gets its own
    iterator so that reading it can never change what a run report contributes.
    """

    root = Path(feasibility_root)
    for path in sorted(root.glob("F4-1*.json")):
        if path.name.endswith(".drive.jsonl") or "-long-" in path.name:
            continue
        yield path


def _recovery_bridge_facts(
    reports: list[tuple[Path, dict[str, Any]]], root: Path
) -> list[FactV1]:
    anchors = []
    for path, report in reports:
        if not path.name.startswith("F4-03-recovery"):
            continue
        result = report.get("result") or {}
        before = result.get("before")
        if isinstance(before, dict) and _task_id(before):
            anchors.append((str(report.get("captured_at") or ""), path, report, before))
    anchors.sort(key=lambda item: (item[0], item[1].name))
    facts = []
    for left, right in zip(anchors, anchors[1:]):
        _, left_path, left_report, before = left
        captured_at, right_path, _, after = right
        cost = _task_cost(before)
        coin_delta = _resource_delta(before, after).get("coins")
        if _task_id(before) == _task_id(after) or cost is None or coin_delta != -cost:
            continue
        left_ref, right_ref = _relative(left_path, root), _relative(right_path, root)
        left_actions = (left_report.get("result") or {}).get("action_results") or []
        action_id = next(
            (str(item.get("action_id")) for item in reversed(left_actions) if item.get("action_id")),
            f"bridge:{left_path.stem}:{right_path.stem}",
        )
        fact = _main_task_fact(
            before,
            after,
            report_ref=left_ref,
            captured_at=captured_at,
            evidence_id=action_id,
            evidence_refs=[f"{left_ref}#action_id={action_id}", f"{right_ref}#result.before"],
        )
        if fact:
            facts.append(fact)
    return facts


def build_facts(
    feasibility_root: str | Path, *, skipped: list[str] | None = None
) -> list[FactV1]:
    root = Path(feasibility_root)
    reports: list[tuple[Path, dict[str, Any]]] = []
    indexed: dict[str, FactV1] = {}
    states: list[dict[str, Any]] = []
    seen_replays: set[str] = set()
    for path in iter_report_paths(root):
        report = _load_json(path)
        if not isinstance(report, dict):
            if skipped is not None:
                skipped.append(_relative(path, root))
            continue
        reports.append((path, report))
        for fact in extract_facts_from_report(path, feasibility_root=root):
            indexed.setdefault(fact.fact_id, fact)
        replay_dir = _replay_path(report)
        if replay_dir is None or str(replay_dir) in seen_replays:
            continue
        seen_replays.add(str(replay_dir))
        states.extend(
            _save_states(
                replay_dir / "states.ndjson",
                report_ref=_relative(path, root),
                evidence_root=root,
            )
        )
    for path in iter_supplementary_report_paths(root):
        report = _load_json(path)
        if not isinstance(report, dict):
            if skipped is not None:
                skipped.append(_relative(path, root))
            continue
        report_ref = _relative(path, root)
        captured_at = str(
            report.get("captured_at")
            or report.get("generated_at")
            or report.get("ended_at")
            or report.get("started_at")
            or ""
        )
        for fact in extract_new_kind_facts(
            report, report_ref=report_ref, captured_at=captured_at, evidence_root=root
        ):
            indexed.setdefault(fact.fact_id, fact)
    for path in iter_aggregate_report_paths(root):
        report = _load_json(path)
        if not isinstance(report, dict):
            if skipped is not None:
                skipped.append(_relative(path, root))
            continue
        report_ref = _relative(path, root)
        captured_at = str(
            report.get("captured_at")
            or report.get("generated_at")
            or report.get("ended_at")
            or ""
        )
        for fact in _relaxation_deviation_facts(report, report_ref, captured_at):
            indexed.setdefault(fact.fact_id, fact)
    for fact in _recovery_bridge_facts(reports, root):
        indexed.setdefault(fact.fact_id, fact)
    known_transitions = {
        (str(fact.before.get("main_task_id")), str(fact.after.get("main_task_id")))
        for fact in indexed.values()
        if fact.kind == "main_task"
    }
    for fact in _save_timeline_facts(states, known_transitions=known_transitions):
        indexed.setdefault(fact.fact_id, fact)
    return sorted(indexed.values(), key=lambda item: (item.captured_at, item.fact_id))


def read_facts(path: str | Path) -> list[FactV1]:
    result = []
    target = Path(path)
    if not target.is_file():
        return result
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                result.append(FactV1.from_dict(json.loads(line)))
    return result


def index_evidence(
    feasibility_root: str | Path,
    output_path: str | Path,
    *,
    skipped: list[str] | None = None,
) -> list[FactV1]:
    """Append new evidence facts, de-duplicated by stable fact id."""

    target = Path(output_path)
    existing = read_facts(target)
    root = Path(feasibility_root)
    active_sources = {_relative(path, root) for path in iter_report_paths(root)}
    active_sources |= {
        _relative(path, root) for path in iter_supplementary_report_paths(root)
    }
    active_sources |= {
        _relative(path, root) for path in iter_aggregate_report_paths(root)
    }
    indexed = {
        fact.fact_id: fact for fact in existing if fact.source_report not in active_sources
    }
    for fact in build_facts(root, skipped=skipped):
        indexed[fact.fact_id] = fact
    facts = list(indexed.values())
    rendered = "".join(
        json.dumps(fact.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for fact in facts
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.read_text(encoding="utf-8") != rendered:
        target.write_text(rendered, encoding="utf-8", newline="\n")
    return facts
