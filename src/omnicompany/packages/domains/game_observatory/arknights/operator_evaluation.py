# [OMNI] origin=codex created_by=codex intent="compare recruits from fixed simulator evidence without choosing for contestants"
"""Simulator-evidenced, affordability-first recruit comparison."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

from .operator_proving_ground import run_experiment
from .stage_world_model import OperatorCombatProfile, StageWorldModel


def _rarities(database: Path, operator_ids: Iterable[str]) -> dict[str, int | None]:
    ids = sorted(set(operator_ids))
    if not ids:
        return {}
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
        marks = ",".join("?" for _ in ids)
        return {str(row[0]): None if row[1] is None else int(row[1]) for row in connection.execute(
            f"SELECT operator_id, rarity FROM operators WHERE operator_id IN ({marks})", ids)}


def _assumed_hope_cost(rarity: int | None) -> int | None:
    return {1: 0, 2: 0, 3: 0, 4: 2, 5: 3, 6: 6}.get(rarity)


def compare_recruits(
    database: Path | str,
    candidates: Iterable[OperatorCombatProfile],
    squad: Iterable[OperatorCombatProfile],
    *,
    stage: StageWorldModel | None = None,
    candidate_hope_costs: Mapping[str, int | None] | None = None,
    current_hope: int | None = None,
    squad_complete: bool = False,
    skill_indexes: Mapping[str, int] | None = None,
    skill_levels: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Compare experiments only; this function never makes the recruit choice.

    Block is consumed by the engine's leak outcomes, skill results only by the
    EffectVM bridge, and first-battle feasibility only by its fixed template.
    """
    database = Path(database)
    rows = {row.operator_id: row for row in candidates}
    if not 3 <= len(rows) <= 24:
        raise ValueError("squad compare requires 3..24 unique candidates so two alternatives exist")
    if set(rows) & {row.operator_id for row in squad}:
        raise ValueError("candidate and current squad sets must be disjoint")
    rarities = _rarities(database, rows)
    exact_costs = dict(candidate_hope_costs or {})
    skill_indexes, skill_levels = skill_indexes or {}, skill_levels or {}
    ranking: list[dict[str, Any]] = []
    for operator_id in sorted(rows):
        profile = rows[operator_id]
        experiments = {
            template: run_experiment(profile, template, database=database,
                skill_index=skill_indexes.get(operator_id), skill_level=skill_levels.get(operator_id))
            for template in ("standard_first_battle", "anti_air_wave", "high_pressure_elite_wave", "sustained_attrition")
        }
        hope_cost = exact_costs.get(operator_id)
        basis = "current_card_exact" if operator_id in exact_costs else "standard_IS_rarity_assumption"
        if operator_id not in exact_costs:
            hope_cost = _assumed_hope_cost(rarities.get(operator_id))
        affordable = None if current_hope is None or hope_cost is None else hope_cost <= current_hope
        # This is a lexicographic ordering of observed facts, not a score or
        # weighted risk proxy.  Each template stays independently inspectable.
        template_ids = tuple(sorted(experiments))
        leak_vector = tuple(experiments[item]["leaks"] for item in template_ids)
        kill_time_vector = tuple(
            experiments[item]["kill_time_ticks"] if experiments[item]["kill_time_ticks"] is not None else 10**9
            for item in template_ids
        )
        evidence_key = (
            not experiments["standard_first_battle"]["zero_leak"],
            leak_vector,
            kill_time_vector,
        )
        not_modeled = sorted({fact for item in experiments.values() for fact in item["not_modeled"]})
        ranking.append({
            "operator_id": operator_id, "name": profile.name,
            "rank_evidence": {"simulated_leaks_by_template": dict(zip(template_ids, leak_vector)),
                "first_battle_single_core_feasible": experiments["standard_first_battle"]["zero_leak"],
                "skill_cycle": {template: experiments[template]["skill_cycle"] for template in template_ids
                                if experiments[template]["skill_cycle"] is not None},
                "kill_time_ticks_by_template": dict(zip(template_ids, kill_time_vector))},
            "experiments": experiments, "not_modeled": not_modeled,
            "economics": {"rarity": rarities.get(operator_id), "hope_cost": hope_cost,
                "hope_cost_basis": basis, "current_hope": current_hope, "affordable": affordable},
            "account_combat_profile": profile.public(), "score": None,
            "_key": evidence_key,
        })
    ranking.sort(key=lambda item: (item["economics"]["affordable"] is False, item["_key"], item["operator_id"]))
    for index, item in enumerate(ranking, 1):
        item["rank"] = index
        item.pop("_key")
    leader = ranking[0]
    alternatives = [{
        "operator_id": item["operator_id"], "name": item["name"],
        "counterfactual": "Prefer this candidate if its observed experiment outcome is the constraint: "
            f"leaks={item['rank_evidence']['simulated_leaks_by_template']}, first-battle-zero-leak="
            f"{item['rank_evidence']['first_battle_single_core_feasible']}.",
        "why_not_first": "affordability-first lexicographic simulator evidence placed it below the first row",
    } for item in ranking[1:3]]
    return {
        "ok": True, "ranking_scope": "numeric evidence and counterfactuals only; contestant makes the recruit choice",
        "blindness_contract": "rank uses affordability, simulator outcomes, and operator_id only; names, input order, and rarity strength are excluded",
        "squad_complete": squad_complete, "loaded_stage_model_hash": None if stage is None else stage.model_hash,
        "ranking": ranking, "alternatives": alternatives,
        "not_modeled": ["squad synergy", "live stage geometry", "collectible synergy"],
        "limitations": ["block-to-leak, skill cycle, and first-battle feasibility use fixed simulator experiments",
                        "rarity is not a strength feature; it is used only for an explicitly labelled Hope-cost assumption"],
    }


__all__ = ["compare_recruits"]
