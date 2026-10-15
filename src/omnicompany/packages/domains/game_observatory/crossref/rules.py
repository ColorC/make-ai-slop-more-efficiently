"""Explainable mappings from observed facts to configuration fields."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .evidence_index import (
    ENERGY_RECOVERY_SECONDS,
    FREE_REFILL_ENERGY,
    NON_GRANTING_ACTION_KINDS,
    FactV1,
)
from .stats import GoodnessOfFit, goodness_of_fit

MIN_WEIGHT_SAMPLE = 30
WEIGHT_ALPHA = 0.05
# One recovery tick may land inside any interval an action also spans.
ENERGY_TOLERANCE = 1


@dataclass(frozen=True)
class RuleResult:
    table: str
    field: str
    status: str
    fact_id: str
    reason: str
    row_keys: dict[str, str] = field(default_factory=dict)
    supporting_fact_ids: tuple[str, ...] = ()
    stats: dict[str, Any] | None = None

    @property
    def all_fact_ids(self) -> tuple[str, ...]:
        return (self.fact_id,) + tuple(self.supporting_fact_ids)


def _load_rows(config_root: Path, table: str) -> list[dict[str, Any]]:
    path = config_root / f"{table}.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("rows") or []


def _lookup(rows: list[dict[str, Any]], key: str, value: Any) -> dict[str, Any] | None:
    return next((row for row in rows if str(row.get(key)) == str(value)), None)


def _result(
    fact: FactV1,
    table: str,
    field_name: str,
    matches: bool,
    reason: str,
    **row_keys: Any,
) -> RuleResult:
    return RuleResult(
        table=table,
        field=field_name,
        status="observed" if matches else "contradicted",
        fact_id=fact.fact_id,
        reason=reason,
        row_keys={key: str(value) for key, value in row_keys.items()},
    )


def _deliver_rules(fact: FactV1, rows: list[dict[str, Any]]) -> list[RuleResult]:
    required = fact.before.get("required_items") or []
    actual = fact.delta.get("coins")
    selected = []
    for requirement in required:
        item_type = requirement.get("type") or requirement.get("item_type")
        row = _lookup(rows, "Code", item_type)
        if row is None or not isinstance(row.get("Price"), (int, float)):
            return []
        selected.append((row, int(requirement.get("count", 1))))
    if not selected or not isinstance(actual, (int, float)):
        return []
    expected = sum(row["Price"] * count for row, count in selected)
    matches = actual == expected
    reason = f"delivery coins delta {actual}; configured item-price sum {expected}"
    return [
        _result(fact, "OrderItemConfig", "Price", matches, reason, Code=row["Code"])
        for row, _ in selected
    ]


def _chain_position(value: Any) -> tuple[int, int] | None:
    """Split a numeric item Type into its chain id and its level on that chain."""

    text = str(value)
    if not text.isdigit() or len(text) < 3:
        return None
    return int(text[:-2]), int(text[-2:])


def _merge_rules(fact: FactV1, rows: list[dict[str, Any]]) -> list[RuleResult]:
    source = fact.before.get("target_type") or fact.before.get("source_type")
    target = fact.after.get("target_type")
    row = _lookup(rows, "Type", source)
    if not row or target is None:
        return []
    expected = row.get("MergedType")
    results = [
        _result(
            fact,
            "ItemModelConfig",
            "MergedType",
            str(expected) == str(target),
            f"merge {source}+{source} produced {target}; configured MergedType is {expected}",
            Type=source,
        )
    ]
    left, right = _chain_position(source), _chain_position(target)
    if left and right and _lookup(rows, "Type", target):
        matches = right[0] == left[0] and right[1] == left[1] + 1
        results.append(
            _result(
                fact,
                "ItemModelConfig",
                "Type",
                matches,
                f"merge {source}+{source} produced {target}; chain {right[0]} level {right[1]} "
                f"should be chain {left[0]} level {left[1] + 1}",
                Type=target,
            )
        )
    return results


def _spread_rules(fact: FactV1, rows: list[dict[str, Any]]) -> list[RuleResult]:
    generator = fact.before.get("source_type")
    produced = fact.after.get("target_type")
    row = _lookup(rows, "Type", generator)
    if not row or produced is None:
        return []
    results = []
    energy = fact.delta.get("energy")
    if fact.kind == "generate" and isinstance(energy, (int, float)):
        expected_cost = row.get("Spread_CostEnergy")
        if expected_cost is not None:
            results.append(
                _result(
                    fact,
                    "ItemModelConfig",
                    "Spread_CostEnergy",
                    -energy == expected_cost,
                    f"generation energy delta {energy}; configured cost {expected_cost}",
                    Type=generator,
                )
            )
    weights = row.get("Spread_Weight") or []
    configured_types = {str(value).split("-", 1)[0] for value in weights}
    if weights:
        results.append(
            _result(
                fact,
                "ItemModelConfig",
                "Spread_Weight",
                str(produced) in configured_types,
                f"{fact.kind} produced {produced}; configured outputs {sorted(configured_types)}",
                Type=generator,
            )
        )
    duration = fact.delta.get("duration_seconds")
    if fact.kind == "open_chest" and duration is not None and row.get("Spread_OpenDuration") is not None:
        results.append(
            _result(
                fact,
                "ItemModelConfig",
                "Spread_OpenDuration",
                duration == row["Spread_OpenDuration"],
                f"chest open duration {duration}; configured duration {row['Spread_OpenDuration']}",
                Type=generator,
            )
        )
    return results


def _parse_task_id(value: Any) -> tuple[str, str] | None:
    parts = str(value).split("_", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else None


def _main_task_rules(
    fact: FactV1,
    task_rows: list[dict[str, Any]],
    level_rows: list[dict[str, Any]],
) -> list[RuleResult]:
    parsed = _parse_task_id(fact.before.get("main_task_id"))
    if not parsed:
        return []
    day, task_id = parsed
    row = next(
        (
            item
            for item in task_rows
            if str(item.get("day")) == day and str(item.get("id")) == task_id
        ),
        None,
    )
    if not row:
        return []
    actual = fact.delta.get("coins")
    results = []
    if isinstance(actual, (int, float)):
        results.append(
            _result(
                fact,
                "MainTaskConfig",
                "cost",
                -actual == row.get("cost"),
                f"main-task coins delta {actual}; configured cost {row.get('cost')}",
                day=day,
                id=task_id,
            )
        )
    before_level, after_level = fact.before.get("level"), fact.after.get("level")
    before_xp, after_xp = fact.before.get("xp"), fact.after.get("xp")
    exp_rewards = [
        int(str(value).split("-", 1)[1])
        for value in row.get("rewards") or []
        if str(value).startswith("exp-")
    ]
    if (
        isinstance(before_level, int)
        and after_level == before_level + 1
        and isinstance(before_xp, (int, float))
        and isinstance(after_xp, (int, float))
        and exp_rewards
    ):
        level_row = _lookup(level_rows, "level", before_level)
        if level_row and isinstance(level_row.get("cost"), (int, float)):
            implied_exp = level_row["cost"] - before_xp + after_xp
            matches = implied_exp == sum(exp_rewards)
            results.append(
                _result(
                    fact,
                    "MainTaskConfig",
                    "rewards",
                    matches,
                    f"level/xp transition implies {implied_exp} exp; configured rewards {sum(exp_rewards)}",
                    day=day,
                    id=task_id,
                )
            )
            results.append(
                _result(
                    fact,
                    "LevelConfig",
                    "cost",
                    matches,
                    f"xp wrapped at {level_row['cost']} for level {before_level}",
                    level=before_level,
                )
            )
    return results


def _sell_rules(fact: FactV1, rows: list[dict[str, Any]]) -> list[RuleResult]:
    item_type = fact.before.get("source_type")
    row = _lookup(rows, "Type", item_type)
    actual = fact.delta.get("coins")
    if not row or not isinstance(actual, (int, float)) or row.get("SellingPrice") is None:
        return []
    return [
        _result(
            fact,
            "ItemModelConfig",
            "SellingPrice",
            actual == row["SellingPrice"],
            f"sale coins delta {actual}; configured SellingPrice {row['SellingPrice']}",
            Type=item_type,
        )
    ]


def _reward_amount(rewards: Any, prefix: str) -> int | None:
    for value in rewards or []:
        text = str(value)
        if text.startswith(prefix):
            try:
                return int(text.split("-", 1)[1])
            except (IndexError, ValueError):
                return None
    return None


def _collect_rules(fact: FactV1, rows: list[dict[str, Any]]) -> list[RuleResult]:
    item_type = fact.before.get("source_type")
    row = _lookup(rows, "Type", item_type)
    rewards = row.get("CollectRewards") if row else None
    if not rewards:
        return []
    results: list[RuleResult] = []
    coins = fact.delta.get("coins")
    expected_coins = _reward_amount(rewards, "gold-")
    if expected_coins is not None and isinstance(coins, (int, float)):
        results.append(
            _result(
                fact,
                "ItemModelConfig",
                "CollectRewards",
                coins == expected_coins,
                f"collecting {item_type} added {coins} coins; configured reward "
                f"{expected_coins}",
                Type=item_type,
            )
        )
    energy = fact.delta.get("energy")
    expected_energy = _reward_amount(rewards, "energy-")
    if expected_energy is not None and isinstance(energy, (int, float)):
        # One natural recovery tick can land inside the same round, so the
        # energy gain is judged with the same one-point tolerance the recovery
        # rule uses.
        results.append(
            _result(
                fact,
                "ItemModelConfig",
                "CollectRewards",
                abs(energy - expected_energy) <= ENERGY_TOLERANCE,
                f"collecting {item_type} added {energy} energy; configured reward "
                f"{expected_energy} (one natural recovery tick tolerated)",
                Type=item_type,
            )
        )
    return results


def _energy_recovery_rules(fact: FactV1) -> list[RuleResult]:
    """Check an idle save-to-save interval against EnergyModel's recovery clock."""

    elapsed = fact.delta.get("elapsed_seconds")
    gained = fact.delta.get("energy")
    if not isinstance(elapsed, (int, float)) or not isinstance(gained, (int, float)):
        return []
    expected = int(elapsed // ENERGY_RECOVERY_SECONDS)
    deviation = abs(gained - expected)
    return [
        RuleResult(
            table="EnergyModel",
            field="DefaultRecoveryDuration",
            status="observed" if deviation <= ENERGY_TOLERANCE else "contradicted",
            fact_id=fact.fact_id,
            reason=(
                f"idle save interval {elapsed:.0f}s moved energy "
                f"{fact.before.get('energy')}→{fact.after.get('energy')} (+{gained}); "
                f"one point per {ENERGY_RECOVERY_SECONDS}s predicts +{expected}, "
                f"deviation {deviation}"
            ),
            row_keys={"type": "Main"},
        )
    ]


def _energy_refill_rules(fact: FactV1) -> list[RuleResult]:
    """Check the free energy refill against ShopModel's free branch."""

    gained = fact.delta.get("energy")
    gems = fact.delta.get("gems")
    if not isinstance(gained, (int, float)) or not isinstance(gems, (int, float)):
        return []
    matches = gained == FREE_REFILL_ENERGY and gems == 0
    return [
        RuleResult(
            table="ShopModel",
            field="BuyEnergy",
            status="observed" if matches else "contradicted",
            fact_id=fact.fact_id,
            reason=(
                f"free refill moved energy {fact.before.get('energy')}→"
                f"{fact.after.get('energy')} (+{gained}) with gem delta {gems}; "
                f"the free branch grants GetMaxEnergy()={FREE_REFILL_ENERGY} at no gem cost"
            ),
            row_keys={"type": "Main"},
        )
    ]


_RUNTIME_FIELDS = (
    ("item_rest_max", "Spread_ItemMaxNumber", "per-tap inventory"),
    ("storage_rest_max", "Spread_StorageMaxNumber", "storage inventory"),
)


def _generator_runtime_rules(fact: FactV1, rows: list[dict[str, Any]]) -> list[RuleResult]:
    """Confirm generator stock ceilings and recovery timing from the save time series."""

    generator = fact.before.get("source_type")
    row = _lookup(rows, "Type", generator)
    if not row:
        return []
    samples = fact.after.get("samples")
    results: list[RuleResult] = []
    for source, field_name, label in _RUNTIME_FIELDS:
        seen = fact.after.get(source)
        configured = row.get(field_name)
        if not isinstance(seen, (int, float)) or not isinstance(configured, (int, float)):
            continue
        if seen > configured:
            results.append(
                _result(
                    fact,
                    "ItemModelConfig",
                    field_name,
                    False,
                    f"{label} reached {seen} over {samples} save samples; configured "
                    f"ceiling is {configured}",
                    Type=generator,
                )
            )
        elif seen == configured:
            results.append(
                _result(
                    fact,
                    "ItemModelConfig",
                    field_name,
                    True,
                    f"{label} held at the configured ceiling {configured} across "
                    f"{samples} save samples",
                    Type=generator,
                )
            )
    waits = [value for value in fact.after.get("recovery_wait_seconds") or []]
    configured_wait = row.get("Spread_ItemRecoveryDuration")
    if waits and isinstance(configured_wait, (int, float)) and configured_wait in waits:
        results.append(
            _result(
                fact,
                "ItemModelConfig",
                "Spread_ItemRecoveryDuration",
                True,
                f"save reported recovery waits {sorted(waits)}; configured duration "
                f"{configured_wait} appears among them",
                Type=generator,
            )
        )
    return results


# F6-05 section 1.3.  A read of the save can be stale -- the app stops ticking
# in the background, so a timer can still be armed after it was due.  That makes
# "the refill was late" unfalsifiable, but "the refill was early" is not: a
# generator whose stock came back *before* the configured duration had elapsed
# would contradict the table.  This rule only asserts the falsifiable half, and
# says how many windows also closed inside a single read interval.
COOLDOWN_TOLERANCE_S = 1.5


def _generator_cooldown_rules(fact: FactV1, rows: list[dict[str, Any]]) -> list[RuleResult]:
    """Check refill timing against Spread_Item/StorageRecoveryDuration."""

    generator = fact.before.get("source_type")
    row = _lookup(rows, "Type", generator)
    if not row:
        return []
    windows = fact.after.get("refill_windows") or []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for window in windows:
        if not isinstance(window, dict):
            continue
        field_name = (
            "Spread_StorageRecoveryDuration"
            if window.get("storage_empty")
            else "Spread_ItemRecoveryDuration"
        )
        grouped.setdefault(field_name, []).append(window)
    results: list[RuleResult] = []
    for field_name, group in sorted(grouped.items()):
        duration = row.get(field_name)
        if not isinstance(duration, (int, float)) or duration <= 0:
            continue
        early = []
        bracketed = 0
        for window in group:
            armed = window.get("armed_at_epoch")
            after = window.get("read_after_epoch")
            before = window.get("read_before_epoch")
            if not isinstance(armed, (int, float)) or not isinstance(after, (int, float)):
                continue
            due = armed + duration
            if due > after + COOLDOWN_TOLERANCE_S:
                early.append(round(after - armed, 1))
            elif isinstance(before, (int, float)) and due >= before - COOLDOWN_TOLERANCE_S:
                bracketed += 1
        total = len(group)
        if not total:
            continue
        matches = not early
        if matches:
            reason = (
                f"{total} observed refills; none completed before the configured "
                f"{duration}s had elapsed, and {bracketed} closed inside a single "
                f"read interval"
            )
        else:
            reason = (
                f"{len(early)} of {total} observed refills completed after only "
                f"{sorted(early)}s, short of the configured {duration}s"
            )
        results.append(
            _result(fact, "ItemModelConfig", field_name, matches, reason, Type=generator)
        )
    return results


def generator_weight_fits(
    facts: Iterable[FactV1], rows: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Accumulate produced-type frequencies per generator and test them."""

    counts: dict[str, dict[str, int]] = {}
    contributors: dict[str, list[str]] = {}
    for fact in facts:
        if fact.kind != "generator_output_sample":
            continue
        generator = str(fact.before.get("source_type") or "")
        if not generator:
            continue
        bucket = counts.setdefault(generator, {})
        for produced in fact.after.get("produced_types") or []:
            bucket[str(produced)] = bucket.get(str(produced), 0) + 1
        contributors.setdefault(generator, []).append(fact.fact_id)
    fits: dict[str, dict[str, Any]] = {}
    for generator, observed in sorted(counts.items()):
        row = _lookup(rows, "Type", generator)
        weights: dict[str, float] = {}
        for entry in (row or {}).get("Spread_Weight") or []:
            name, _, weight = str(entry).partition("-")
            try:
                weights[name] = float(weight)
            except ValueError:
                continue
        fit = goodness_of_fit(observed, weights) if weights else None
        fits[generator] = {
            "fact_ids": sorted(contributors.get(generator, [])),
            "declared_weights": weights,
            "fit": fit,
        }
    return fits


def _weight_verdict(fit: GoodnessOfFit) -> tuple[str | None, str]:
    if fit.off_list:
        return (
            "contradicted",
            f"generator produced undeclared types {dict(sorted(fit.off_list.items()))} "
            f"over {fit.sample_size + sum(fit.off_list.values())} samples",
        )
    if fit.sample_size < MIN_WEIGHT_SAMPLE:
        return (
            None,
            f"sample size {fit.sample_size} below the {MIN_WEIGHT_SAMPLE} needed to judge "
            "the declared weights",
        )
    reason = (
        f"n={fit.sample_size}, chi2={fit.statistic:.4f}, df={fit.degrees_of_freedom}, "
        f"p={fit.p_value:.6f} against declared Spread_Weight"
    )
    return ("observed" if fit.p_value >= WEIGHT_ALPHA else "contradicted", reason)


def generator_weight_results(
    facts: list[FactV1], rows: list[dict[str, Any]]
) -> list[RuleResult]:
    """Turn the generator output distribution into Spread_Weight verdicts."""

    results: list[RuleResult] = []
    for generator, entry in generator_weight_fits(facts, rows).items():
        fit: GoodnessOfFit | None = entry["fit"]
        fact_ids: list[str] = entry["fact_ids"]
        if fit is None or not fact_ids:
            continue
        status, reason = _weight_verdict(fit)
        if status is None:
            continue
        results.append(
            RuleResult(
                table="ItemModelConfig",
                field="Spread_Weight",
                status=status,
                fact_id=fact_ids[-1],
                reason=reason,
                row_keys={"Type": generator},
                supporting_fact_ids=tuple(fact_ids[:-1]),
                stats=fit.to_dict(),
            )
        )
    return results


def board_results(facts: list[FactV1], board_rows: list[Any]) -> list[RuleResult]:
    """Confirm configured board cells against the earliest observed live board."""

    snapshots = sorted(
        (fact for fact in facts if fact.kind == "board_snapshot"),
        key=lambda item: (item.captured_at, item.fact_id),
    )
    matched: dict[tuple[str, str], RuleResult] = {}
    for fact in snapshots:
        matrix = fact.after.get("board_matrix_7x9") or []
        for row_index, (configured_row, live_row) in enumerate(zip(board_rows, matrix), start=1):
            if not isinstance(configured_row, list) or not isinstance(live_row, list):
                continue
            for col_index, (configured, live) in enumerate(
                zip(configured_row, live_row), start=1
            ):
                key = (str(row_index), str(col_index))
                if key in matched or configured is None or str(configured) != str(live):
                    continue
                matched[key] = _result(
                    fact,
                    "BoardModelConfig",
                    "cell",
                    True,
                    f"live board cell ({row_index},{col_index}) holds the configured "
                    f"{configured}",
                    row=row_index,
                    col=col_index,
                )
    return list(matched.values())


PAPERBOX_REWARD_TABLE = "MainPaperboxExtraRewardConfig"
BOARD_MODEL_TABLE = "BoardModelConfig"


def _paperbox_stripped(before: Any, after: Any) -> bool:
    """True when a cell lost its ``pb`` (paperbox) layer and kept its item."""

    if not isinstance(before, str) or not isinstance(after, str):
        return False
    before_parts = before.split("#")
    after_parts = after.split("#")
    return (
        "pb" in before_parts[:-1]
        and "pb" not in after_parts[:-1]
        and before_parts[-1] == after_parts[-1]
    )


def paperbox_clear_results(
    facts: list[FactV1],
    paperbox_rows: list[Any],
    board_rows: list[Any],
) -> list[RuleResult]:
    """Check the paperbox reward grid against live board clears.

    F4-07 open item 4.  The lightning number on a sand cell is what clearing it
    pays, not a price: F4-06 segment F round 9 stripped the paperbox at (4,7)
    and energy went 97 -> 127, matching ``MainPaperboxExtraRewardConfig`` row 4
    column 7 = ``energy-30``.  The claim this rule adjudicates is the structural
    one the amount rests on: the reward grid is parallel to ``BoardModelConfig``
    so that a strip observed at (row, col) is priced by the same (row, col).
    The configured reward is carried in ``stats`` for the amount to be judged
    against an energy fact separately.
    """

    if not paperbox_rows:
        return []
    # One report contributes a single board snapshot, so the strips have to be
    # found across the whole corpus, in capture order.  A paperbox never comes
    # back once it is gone, so consecutive snapshots are a sound pairing even
    # when they come from different runs.
    ordered_all = sorted(
        (fact for fact in facts if fact.kind == "board_snapshot"),
        key=lambda item: (item.captured_at, item.after.get("round") or 0, item.fact_id),
    )
    results: dict[tuple[str, str], RuleResult] = {}
    for previous, current in zip(ordered_all, ordered_all[1:]):
        before_matrix = previous.after.get("board_matrix_7x9") or []
        after_matrix = current.after.get("board_matrix_7x9") or []
        for row_index, (before_row, after_row) in enumerate(
            zip(before_matrix, after_matrix), start=1
        ):
            if not isinstance(before_row, list) or not isinstance(after_row, list):
                continue
            for col_index, (before, after) in enumerate(
                zip(before_row, after_row), start=1
            ):
                if not _paperbox_stripped(before, after):
                    continue
                key = (str(row_index), str(col_index))
                if key in results:
                    continue
                reward_row = (
                    paperbox_rows[row_index - 1]
                    if row_index - 1 < len(paperbox_rows)
                    else None
                )
                configured_board = None
                if row_index - 1 < len(board_rows):
                    board_row = board_rows[row_index - 1]
                    if isinstance(board_row, list) and col_index - 1 < len(board_row):
                        configured_board = board_row[col_index - 1]
                priced = isinstance(reward_row, list) and col_index - 1 < len(reward_row)
                reward = reward_row[col_index - 1] if priced else None
                board_declares_paperbox = (
                    isinstance(configured_board, str)
                    and "pb" in configured_board.split("#")[:-1]
                )
                matches = priced and board_declares_paperbox
                result = _result(
                    current,
                    PAPERBOX_REWARD_TABLE,
                    "cell_reward",
                    matches,
                    (
                        f"paperbox cleared at ({row_index},{col_index}) "
                        f"{before} -> {after}; the reward grid prices that same "
                        f"cell {reward!r} and BoardModelConfig declares it a paperbox"
                        if matches
                        else (
                            f"paperbox cleared at ({row_index},{col_index}) "
                            f"{before} -> {after}, but the reward grid or "
                            "BoardModelConfig does not cover that cell"
                        )
                    ),
                    row=row_index,
                    col=col_index,
                )
                results[key] = RuleResult(
                    table=result.table,
                    field=result.field,
                    status=result.status,
                    fact_id=result.fact_id,
                    reason=result.reason,
                    row_keys=result.row_keys,
                    supporting_fact_ids=(previous.fact_id,),
                    stats={
                        "configured_reward": reward,
                        "configured_board_cell": configured_board,
                    },
                )
    return list(results.values())


def order_frequency_observation(
    facts: Iterable[FactV1], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Record how often each order Code was seen against its declared Weight.

    F6-02 asks for the comparison to be recorded, not judged: order rotation
    depends on unlock level and repeat-decay terms this evidence cannot isolate.
    """

    counts: dict[str, int] = {}
    fact_ids: list[str] = []
    for fact in facts:
        if fact.kind != "order_appearance":
            continue
        fact_ids.append(fact.fact_id)
        for requirement in fact.after.get("required_items") or []:
            code = str(requirement.get("type") or "")
            if code:
                counts[code] = counts.get(code, 0) + 1
    total = sum(counts.values())
    weights = {
        str(row.get("Code")): row.get("Weight")
        for row in rows
        if isinstance(row.get("Weight"), (int, float))
    }
    weight_total = sum(weights.values()) or 0
    entries = [
        {
            "Code": code,
            "observed": count,
            "observed_share": count / total if total else 0.0,
            "declared_weight": weights.get(code),
            "declared_share": (
                weights[code] / weight_total if code in weights and weight_total else None
            ),
        }
        for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "judged": False,
        "note": "订单出现频次仅记录，不判定 Weight",
        "sample_size": total,
        "distinct_order_instances": len(fact_ids),
        "entries": entries,
    }


def _paperbox_attribution_reward_result(
    fact: FactV1,
    paperbox_rows: list[Any],
    *,
    row: int,
    col: int,
) -> RuleResult | None:
    """Price an attributed strip against the reward grid, naming the action.

    F6-04 section 1.2.  ``paperbox_clear_results`` already prices a strip found
    between two board snapshots, but that pairing cannot say which action did
    it.  A strip attributed inside its own round can, so this result carries
    ``attributed_action_id`` and adjudicates the same claim with a stronger
    evidence chain: the reward grid prices the very cell the merge stripped.
    """

    action_id = fact.after.get("merge_action_id")
    if not action_id:
        return None
    reward_row = paperbox_rows[row - 1] if row - 1 < len(paperbox_rows) else None
    priced = isinstance(reward_row, list) and col - 1 < len(reward_row)
    reward = reward_row[col - 1] if priced else None
    return RuleResult(
        table=PAPERBOX_REWARD_TABLE,
        field="cell_reward",
        status="observed" if priced else "contradicted",
        fact_id=fact.fact_id,
        reason=(
            f"action {action_id} merged at "
            f"({fact.after.get('merge_target_row')},{fact.after.get('merge_target_col')}) "
            f"and stripped the paperbox at ({row},{col}); the reward grid prices that "
            f"same cell {reward!r}"
            if priced
            else (
                f"action {action_id} stripped the paperbox at ({row},{col}), which the "
                "reward grid does not cover"
            )
        ),
        row_keys={"row": str(row), "col": str(col)},
        stats={
            "configured_reward": reward,
            "attributed_action_id": str(action_id),
            "attributed_prediction_id": fact.after.get("merge_prediction_id"),
            "merge_target": [
                fact.after.get("merge_target_row"),
                fact.after.get("merge_target_col"),
            ],
        },
    )


def _paperbox_clear_trigger_rules(
    fact: FactV1,
    board_rows: list[Any],
    paperbox_rows: list[Any] | None = None,
) -> list[RuleResult]:
    """Adjudicate what clears a sand cell, not just what clearing it pays.

    F4-08 section 4.3.  The claim is that a confirmed merge strips the paperbox
    layer from the cells orthogonally adjacent to where the merged item landed.
    Every strip observed so far sits on one of those four cells and every
    paperbox-covered neighbour of a merge target was stripped, so the rule
    judges exactly that: adjacency to the merge target, and a BoardModelConfig
    entry that declares the stripped cell a paperbox in the first place.
    """

    row = fact.before.get("row")
    col = fact.before.get("col")
    target_row = fact.after.get("merge_target_row")
    target_col = fact.after.get("merge_target_col")
    if not isinstance(row, int) or not isinstance(col, int):
        return []
    declared = None
    if row - 1 < len(board_rows):
        board_row = board_rows[row - 1]
        if isinstance(board_row, list) and col - 1 < len(board_row):
            declared = board_row[col - 1]
    board_declares_paperbox = isinstance(declared, str) and "pb" in declared.split("#")[:-1]
    if target_row is None or target_col is None:
        return [
            _result(
                fact,
                BOARD_MODEL_TABLE,
                "paperbox_clear_trigger",
                False,
                (
                    f"paperbox cleared at ({row},{col}) with "
                    f"{fact.after.get('cleared_with')} in the round, so no single "
                    "merge can be named as the trigger"
                ),
                row=row,
                col=col,
            )
        ]
    adjacent = bool(fact.after.get("adjacent_to_merge_target"))
    matches = adjacent and board_declares_paperbox
    reward_result = (
        _paperbox_attribution_reward_result(fact, paperbox_rows, row=row, col=col)
        if paperbox_rows
        else None
    )
    return ([reward_result] if reward_result is not None else []) + [
        _result(
            fact,
            BOARD_MODEL_TABLE,
            "paperbox_clear_trigger",
            matches,
            (
                f"paperbox cleared at ({row},{col}) "
                f"{fact.before.get('cell_before')} -> {fact.after.get('cell_after')}; "
                f"the round's one confirmed merge landed at "
                f"({target_row},{target_col}), orthogonally adjacent, and "
                f"BoardModelConfig declares ({row},{col}) a paperbox"
                if matches
                else (
                    f"paperbox cleared at ({row},{col}); the round's confirmed "
                    f"merge landed at ({target_row},{target_col}), which is "
                    f"{'not orthogonally adjacent' if not adjacent else 'adjacent'}"
                    f"{'' if board_declares_paperbox else ' and BoardModelConfig does not declare that cell a paperbox'}"
                )
            ),
            row=row,
            col=col,
        )
    ]


# F6-04 section 1.1.  The pseudo-table the red-line assertion is written
# against: "a close or a dismiss grants nothing" is a claim about the guard,
# not about a configuration row, so it is reported and never enters the field
# ledger.
GUARD_REDLINE_TABLE = "GuardRedline"
CLAIM_NEUTRALITY_FIELD = "claim_grants_nothing"
LEVEL_CONFIG_TABLE = "LevelConfig"

# Which configuration table declares what a claim on a given surface grants.
# Every other claim surface was searched for in the imported configuration and
# has no row that declares it, so it is reported as code- or server-declared
# rather than judged.  Reproduce the search with:
#   ls data/domains/game_observatory/game_models/romantic-restaurant/config \
#     | grep -iE 'gift|newbie|reward|claim'
CLAIM_DECLARING_TABLES = {"level-up-reward": LEVEL_CONFIG_TABLE}

# The resource fields a claim can move.  ``level`` is excluded: a level-up
# panel is shown after the level was credited, so the level moving inside the
# window is the game settling, not the claim granting.
_CLAIM_RESOURCE_FIELDS = ("energy", "coins", "gems", "xp")


def _claim_group_key(fact: FactV1) -> str:
    return str(fact.before.get("group_id") or fact.before.get("action_id") or fact.fact_id)


def _claim_nonzero(fact: FactV1) -> dict[str, Any]:
    """Every measured movement a claim's window is not allowed to have."""

    moved = {
        field: fact.delta[field]
        for field in _CLAIM_RESOURCE_FIELDS
        if isinstance(fact.delta.get(field), (int, float)) and fact.delta[field]
    }
    for field in ("board_items_gained", "board_items_lost"):
        value = fact.delta.get(field)
        if isinstance(value, (int, float)) and value:
            moved[field] = value
    return moved


def _declared_level_reward_count(rows: list[dict[str, Any]], level: Any) -> int | None:
    row = _lookup(rows, "level", level)
    if row is None:
        return None
    total = 0
    for entry in row.get("rewards") or []:
        _, _, amount = str(entry).rpartition("-")
        try:
            total += int(amount)
        except ValueError:
            return None
    return total


def free_claim_results(
    facts: Iterable[FactV1], level_rows: list[dict[str, Any]]
) -> list[RuleResult]:
    """Adjudicate what the save says each guard claim or close actually moved.

    F6-04 section 1.1.  Two claims are checkable.  A close or a dismiss must
    move nothing at all: that is the numeric form of the red line, and a
    non-zero difference contradicts it.  A claim on a surface whose grant a
    configuration table declares is checked against that declaration.  Every
    other claim surface has no declaring table, so it is recorded in the report
    and no verdict is invented for it.

    A measurement window is shared by every claim-class action inside it, so
    the verdict is taken once per group and the other members of the group are
    carried as supporting evidence.
    """

    groups: dict[str, list[FactV1]] = {}
    for fact in facts:
        if fact.kind != "free_claim_delta" or not fact.before.get("attributable"):
            continue
        groups.setdefault(_claim_group_key(fact), []).append(fact)
    results: list[RuleResult] = []
    for _, members in sorted(groups.items()):
        members = sorted(members, key=lambda item: item.fact_id)
        head = members[0]
        supporting = tuple(item.fact_id for item in members[1:])
        kinds = {str(item.before.get("action_kind")) for item in members}
        screens = sorted({str(item.before.get("screen")) for item in members})
        moved = _claim_nonzero(head)
        if kinds <= NON_GRANTING_ACTION_KINDS:
            results.append(
                RuleResult(
                    table=GUARD_REDLINE_TABLE,
                    field=CLAIM_NEUTRALITY_FIELD,
                    status="observed" if not moved else "contradicted",
                    fact_id=head.fact_id,
                    reason=(
                        f"{'/'.join(sorted(kinds))} on {', '.join(screens)} measured "
                        + (
                            "an all-zero save difference"
                            if not moved
                            else f"a non-zero save difference {dict(sorted(moved.items()))}"
                        )
                    ),
                    row_keys={
                        "screen": screens[0],
                        "element": str(head.before.get("element") or ""),
                    },
                    supporting_fact_ids=supporting,
                    stats={"delta": dict(sorted(moved.items()))},
                )
            )
            continue
        table = next(
            (
                CLAIM_DECLARING_TABLES[screen]
                for screen in screens
                if screen in CLAIM_DECLARING_TABLES
            ),
            None,
        )
        if table != LEVEL_CONFIG_TABLE:
            continue
        # The panel is shown once the level has been credited, so the save read
        # taken before the claim already carries the level the reward belongs
        # to.  LevelConfig rewards are item grants only, never resources, so
        # both halves are checkable: the item count, and that no resource moved.
        level = (head.before.get("resources") or {}).get("level")
        declared = _declared_level_reward_count(level_rows, level)
        if declared is None:
            continue
        gained = head.delta.get("board_items_gained")
        resource_moves = {
            field: value
            for field, value in moved.items()
            if field in _CLAIM_RESOURCE_FIELDS
        }
        matches = gained == declared and not resource_moves
        results.append(
            RuleResult(
                table=LEVEL_CONFIG_TABLE,
                field="rewards",
                status="observed" if matches else "contradicted",
                fact_id=head.fact_id,
                reason=(
                    f"level-up claim at level {level} placed {gained} new board items "
                    f"and moved {dict(sorted(resource_moves.items())) or 'no resources'}; "
                    f"LevelConfig row {level} declares {declared} item grants and no "
                    "resource grant"
                ),
                row_keys={"level": str(level)},
                supporting_fact_ids=supporting,
                stats={"declared_item_count": declared, "observed_item_count": gained},
            )
        )
    return results


TRIGGER_PLANE_SCORE_TABLE = "ItemGenerateScore"

# TriggerPlaneModel.lua:370-386 — the score a produced item is worth is the
# base score of its chain doubled once per level above the level the table
# entry names.  Every entry in the shipped table is a level-one item.
def _score_config(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    config: dict[int, dict[str, Any]] = {}
    for row in rows:
        position = _chain_position(row.get("Type"))
        base = row.get("BaseScore")
        if position is None or not isinstance(base, (int, float)):
            continue
        chain, level = position
        config[chain] = {"Level": level, "BaseScore": base, "Type": str(row.get("Type"))}
    return config


def _expected_plane_score(
    produced: Iterable[Any], config: dict[int, dict[str, Any]]
) -> tuple[int, list[str]]:
    """The score the produced items are worth, and the ones no entry covers."""

    total = 0
    unscored: list[str] = []
    for value in produced:
        position = _chain_position(value)
        entry = config.get(position[0]) if position else None
        if position is None or entry is None:
            unscored.append(str(value))
            continue
        if position[1] < entry["Level"]:
            continue
        total += int(entry["BaseScore"] * 2 ** (position[1] - entry["Level"]) + 0.5)
    return total, unscored


def trigger_plane_results(
    facts: Iterable[FactV1], rows: list[dict[str, Any]]
) -> list[RuleResult]:
    """Score the energy-party round against the table, and its spend counter.

    F7-04 section 1.1.  Two claims live on the same fact.  The score claim is
    only checkable on a round where the score actually moved, so a corpus whose
    score never leaves zero yields no score result at all rather than a run of
    vacuous confirmations.  The spend claim is checkable on every round: the
    counter is fed by every energy decrease, so it is the one number that still
    sees a generation the recovery tick hid from the visible balance.
    """

    config = _score_config(rows)
    results: list[RuleResult] = []
    for fact in facts:
        if fact.kind != "trigger_plane_round":
            continue
        gained = fact.delta.get("score")
        if isinstance(gained, (int, float)) and gained > 0:
            expected, unscored = _expected_plane_score(
                fact.after.get("produced_types") or [], config
            )
            note = (
                f"; {len(unscored)} produced item(s) have no table entry"
                if unscored
                else ""
            )
            scoring_types = sorted(
                {
                    config[position[0]]["Type"]
                    for position in (
                        _chain_position(value)
                        for value in fact.after.get("produced_types") or []
                    )
                    if position is not None and position[0] in config
                }
            )
            results.append(
                RuleResult(
                    table=TRIGGER_PLANE_SCORE_TABLE,
                    field="BaseScore",
                    status="observed" if expected == gained else "contradicted",
                    fact_id=fact.fact_id,
                    reason=(
                        f"round {fact.before.get('round')} produced "
                        f"{fact.after.get('produced_types')} and the activity score "
                        f"moved {fact.before.get('score')}→{fact.after.get('score')} "
                        f"(+{gained}); the table and the doubling rule predict "
                        f"+{expected}{note}"
                    ),
                    row_keys=(
                        {"Type": scoring_types[0]} if len(scoring_types) == 1 else {}
                    ),
                    stats={
                        "expected_score": expected,
                        "observed_score": gained,
                        "scoring_types": scoring_types,
                        "unscored_types": unscored,
                    },
                )
            )
        consumed = fact.delta.get("energyConsumed")
        cost = fact.delta.get("expected_generate_cost")
        if isinstance(consumed, (int, float)) and isinstance(cost, (int, float)):
            deviation = abs(consumed - cost)
            results.append(
                RuleResult(
                    table="TriggerPlaneModel",
                    field="EnergyConsumed",
                    status="observed" if deviation <= ENERGY_TOLERANCE else "contradicted",
                    fact_id=fact.fact_id,
                    reason=(
                        f"round {fact.before.get('round')} moved energyConsumed "
                        f"{fact.before.get('energyConsumed')}→"
                        f"{fact.after.get('energyConsumed')} (+{consumed}) against "
                        f"{fact.delta.get('confirmed_generates')} confirmed "
                        f"generation(s) costing {cost}; visible energy spend was "
                        f"{fact.delta.get('visible_energy_spend')}, deviation "
                        f"{deviation}"
                    ),
                    row_keys={"type": "Main"},
                    stats={
                        "energy_consumed_delta": consumed,
                        "expected_generate_cost": cost,
                        "visible_energy_spend": fact.delta.get("visible_energy_spend"),
                    },
                )
            )
    return results


def trigger_plane_reward_results(facts: Iterable[FactV1]) -> list[RuleResult]:
    """Record the save difference a tier payout left behind.

    F7-04 section 1.2.  ``triggerPlaneReward`` is a server table, not one of
    the decompiled local ones, so its amounts cannot be looked up here.  What
    is checkable offline is that the payout arrives with no control touched:
    the level rose between two reads and the resources moved by themselves.
    """

    results: list[RuleResult] = []
    for fact in facts:
        if fact.kind != "trigger_plane_reward":
            continue
        granted = {
            name: value
            for name, value in fact.delta.items()
            if isinstance(value, (int, float)) and value > 0
        }
        results.append(
            RuleResult(
                table="TriggerPlaneModel",
                field="AcquireRewards",
                status="observed" if granted else "contradicted",
                fact_id=fact.fact_id,
                reason=(
                    f"round {fact.before.get('round')} raised the activity level "
                    f"{fact.before.get('level')}→{fact.after.get('level')} and the "
                    f"save moved {granted or 'nothing'} with no control touched; "
                    "the tier amounts themselves are server-declared and are not "
                    "in the decompiled tables"
                ),
                row_keys={"type": "Main"},
                stats={"granted": granted},
            )
        )
    return results


def story_advance_observation(facts: Iterable[FactV1]) -> dict[str, Any]:
    """Count story advances per stream and prove no moment is counted twice.

    F7-04 section 1.3.  The guard and the main-task flow both advance story
    dialogue.  Two taps at the same monotonic instant would mean one moment
    entered the ledger through both doors, so the check is an identity check on
    the action, not a comparison of totals.
    """

    per_stream: dict[str, int] = {}
    moments: dict[tuple[Any, Any], set[str]] = {}
    fact_ids: list[str] = []
    for fact in facts:
        if fact.kind != "story_advance":
            continue
        fact_ids.append(fact.fact_id)
        stream = str(fact.before.get("stream") or "unknown")
        per_stream[stream] = per_stream.get(stream, 0) + 1
        key = (fact.before.get("run_id"), fact.before.get("t_mono_ns"))
        moments.setdefault(key, set()).add(stream)
    double_counted = [
        {"run_id": run_id, "t_mono_ns": marker, "streams": sorted(streams)}
        for (run_id, marker), streams in sorted(
            moments.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
        )
        if len(streams) > 1
    ]
    digests = sum(
        1
        for fact in facts
        if fact.kind == "story_advance" and fact.after.get("pre_frame_sha256")
    )
    return {
        "samples": len(fact_ids),
        "per_stream": dict(sorted(per_stream.items())),
        "with_frame_digest": digests,
        "double_counted_moments": double_counted,
        "consistent": not double_counted,
    }


def order_reserve_observation(facts: Iterable[FactV1]) -> dict[str, Any]:
    """Count the merges an open order's reserve refused.

    F7-04 section 1.4.  The reserve is a planner rule, not a configuration
    field, so the ledger records how often it fired and on what, and judges
    nothing.
    """

    samples = [fact for fact in facts if fact.kind == "order_reserve_block"]
    by_reason: dict[str, int] = {}
    for fact in samples:
        reason = str(fact.before.get("reason") or "")
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "judged": False,
        "samples": len(samples),
        "by_reason": dict(sorted(by_reason.items())),
        "rounds": sorted(
            {
                str(fact.before.get("round"))
                for fact in samples
                if fact.before.get("round") is not None
            }
        ),
    }


def generate_deviation_observation(facts: Iterable[FactV1]) -> dict[str, Any]:
    """Count the relaxed generation verdicts as their own category.

    F7-04 section 1.5.  A deviation is neither an observation of the predicted
    outcome nor a contradiction of it: production happened, the prediction of
    what or where did not.  It is listed on its own so that neither of the two
    verdict columns absorbs it.
    """

    samples = [fact for fact in facts if fact.kind == "generate_deviation"]
    by_origin: dict[str, int] = {}
    matched = 0
    energy_checks: dict[str, int] = {}
    for fact in samples:
        origin = str(fact.before.get("origin") or "unknown")
        by_origin[origin] = by_origin.get(origin, 0) + 1
        deviation = fact.after.get("deviation") or {}
        if deviation.get("matched_prediction"):
            matched += 1
        check = str(deviation.get("energy_check") or "unknown")
        energy_checks[check] = energy_checks.get(check, 0) + 1
    return {
        "judged": False,
        "samples": len(samples),
        "by_origin": dict(sorted(by_origin.items())),
        "matched_prediction": matched,
        "energy_check": dict(sorted(energy_checks.items())),
    }


def apply_rules(facts: Iterable[FactV1], config_root: str | Path) -> list[RuleResult]:
    """Apply every F6-01 and F6-02 rule without mutating evidence or configuration."""

    root = Path(config_root)
    facts = list(facts)
    item_rows = _load_rows(root, "ItemModelConfig")
    order_rows = _load_rows(root, "OrderItemConfig")
    task_rows = _load_rows(root, "MainTaskConfig")
    level_rows = _load_rows(root, "LevelConfig")
    board_rows = _load_rows(root, "BoardModelConfig")
    paperbox_rows = _load_rows(root, PAPERBOX_REWARD_TABLE)
    score_rows = _load_rows(root, TRIGGER_PLANE_SCORE_TABLE)
    results: list[RuleResult] = []
    results.extend(trigger_plane_results(facts, score_rows))
    results.extend(trigger_plane_reward_results(facts))
    results.extend(generator_weight_results(facts, item_rows))
    results.extend(board_results(facts, board_rows))
    results.extend(paperbox_clear_results(facts, paperbox_rows, board_rows))
    results.extend(free_claim_results(facts, level_rows))
    for fact in facts:
        if fact.kind == "deliver":
            results.extend(_deliver_rules(fact, order_rows))
        elif fact.kind == "merge":
            results.extend(_merge_rules(fact, item_rows))
        elif fact.kind in {"generate", "open_chest"}:
            results.extend(_spread_rules(fact, item_rows))
        elif fact.kind == "main_task":
            results.extend(_main_task_rules(fact, task_rows, level_rows))
        elif fact.kind == "sell":
            results.extend(_sell_rules(fact, item_rows))
        elif fact.kind == "collect":
            results.extend(_collect_rules(fact, item_rows))
        elif fact.kind == "generator_runtime":
            results.extend(_generator_runtime_rules(fact, item_rows))
        elif fact.kind == "generator_cooldown":
            results.extend(_generator_cooldown_rules(fact, item_rows))
        elif fact.kind == "energy_recovery":
            results.extend(_energy_recovery_rules(fact))
        elif fact.kind == "energy_refill_free":
            results.extend(_energy_refill_rules(fact))
        elif fact.kind == "paperbox_clear":
            results.extend(
                _paperbox_clear_trigger_rules(fact, board_rows, paperbox_rows)
            )
    return sorted(
        results,
        key=lambda item: (item.table, item.field, item.fact_id, tuple(item.row_keys.items())),
    )
