# [OMNI] origin=codex created_by=codex intent="bridge evidence-backed Arknights stage models into deterministic forecasts"
"""Compile a loaded-stage text model into the deterministic battle simulator.

This is a planning aid, not a claim that the simulator reproduces the live
client.  It preserves the source map, route order, waits, physical/arts normal
attacks, blocking, cost, and the selected static random-spawn scenario.  Range
shape, skills, talents, traits, collectibles, and stage-specific scripts remain
explicit limitations in every receipt.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from .skill_prototype_bridge import CompiledSkillPrototype
from .stage_world_model import OperatorCombatProfile, StageWorldModel, StageWorldModelError
from .tournament_sim import (
    ActivateSkillAction,
    BattleEngine,
    ContentBundle,
    DeployAction,
    DeploymentClass,
    Direction,
    EffectStat,
    EffectTargetSelector,
    EnemyDefinition,
    GridPoint,
    IllegalActionError,
    MapDefinition,
    MechanismFidelity,
    MovementLayer,
    OperatorDefinition,
    RouteDefinition,
    SpawnDefinition,
    StatModifierEffectDefinition,
    SummonDefinition,
    FidelityLevel,
    WaitAction,
)
from .tournament_sim.models import InitialSummonDefinition


TICKS_PER_SECOND = 30
SCENARIOS = ("most-likely", "upper-bound")
_PREDEFINED_FRIENDLY_PROTOTYPES: dict[str, dict[str, int]] = {
    # character_table.json#/trap_097_hstone/phases/0/attributesKeyFrames/0/data
    "trap_097_hstone": {
        "phase": 0,
        "level": 1,
        "max_hp": 5000,
        "attack": 0,
        "defense": 300,
        "magic_resistance": 20,
        "block_count": 20,
        "attack_interval_seconds": 1.0,
    }
}


def _compile_predefined_friendlies(
    model: StageWorldModel,
    *,
    ticks_per_second: int,
) -> tuple[
    tuple[SummonDefinition, ...],
    tuple[InitialSummonDefinition, ...],
    tuple[dict[str, Any], ...],
]:
    definitions: dict[str, SummonDefinition] = {}
    initial: list[InitialSummonDefinition] = []
    excluded: list[dict[str, Any]] = []
    for row in model.predefined_tokens:
        if row["hidden"]:
            excluded.append({**row, "reason": "hidden"})
            continue
        prototype = _PREDEFINED_FRIENDLY_PROTOTYPES.get(str(row["character_id"]))
        if (
            prototype is None
            or row["position"] is None
            or int(row["phase"]) != prototype["phase"]
            or int(row["level"]) != prototype["level"]
        ):
            excluded.append({**row, "reason": "unsupported-visible-prototype"})
            continue
        summon_id = str(row["character_id"])
        definitions.setdefault(
            summon_id,
            SummonDefinition(
                summon_id=summon_id,
                max_hp=prototype["max_hp"],
                attack=prototype["attack"],
                defense=prototype["defense"],
                magic_resistance=prototype["magic_resistance"],
                attack_interval_ticks=max(
                    1, round(prototype["attack_interval_seconds"] * ticks_per_second)
                ),
                attack_range_tiles=0,
                block_count=prototype["block_count"],
                receives_external_healing=False,
            ),
        )
        col, top_row = row["position"]
        initial.append(
            InitialSummonDefinition(
                instance_id=f"predefine:{row['alias']}",
                summon_id=summon_id,
                position=GridPoint(col=col, row=top_row),
            )
        )
    return (
        tuple(definitions[key] for key in sorted(definitions)),
        tuple(initial),
        tuple(excluded),
    )


def _baseline_viability(
    result: Mapping[str, Any], *, current_life_points: int | None = None
) -> dict[str, Any]:
    leaks = int(result.get("leaks") or 0)
    life_points = int(result.get("life_points") or 0)
    requires_revision = (
        life_points <= 0
        if current_life_points is not None
        else leaks > 0 or life_points <= 0
    )
    return {
        "status": "revise-plan" if requires_revision else "baseline-viable",
        "requires_revision_before_live_execution": requires_revision,
        "predicted_leaks": leaks,
        "remaining_life_points": life_points,
        "reason": (
            (
                "the modeled deployment exhausts the supplied current life; revise before live execution"
                if current_life_points is not None
                else "the modeled deployment already leaks or loses all life; expand placement/order search or add an explicit modeled mitigation"
            )
            if requires_revision
            else (
                f"the modeled battle retains {life_points} life after {leaks} predicted leaks"
                if current_life_points is not None
                else "no leak is predicted in the modeled horizon"
            )
        ),
    }


def _selected_spawn_events(
    events: Sequence[Mapping[str, Any]], scenario: str, *, stage_mode: str = ""
) -> list[Mapping[str, Any]]:
    if scenario not in SCENARIOS:
        raise StageWorldModelError(f"scenario must be one of {SCENARIOS}")
    if scenario == "upper-bound":
        return [event for event in events if not event["is_empty_option"]]
    # n/h are the normal/emergency stage branches. Other hidden groups depend
    # on run-specific devices or relics and stay out of the default forecast.
    normalized_mode = stage_mode.strip().lower()
    active_hidden_group = (
        "n"
        if normalized_mode == "normal"
        else "h"
        if normalized_mode in {"hard", "emergency"}
        else None
    )
    eligible = [
        event
        for event in events
        if event.get("hidden_group") is None
        or event.get("hidden_group") == active_hidden_group
    ]
    fixed = [
        event
        for event in eligible
        if event["random_group_scope"] is None
        and float(event["selection_probability"]) > 0.5
    ]
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for event in eligible:
        scope = event["random_group_scope"]
        if scope is not None:
            groups.setdefault(str(scope), []).append(event)
    for rows in groups.values():
        choices: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            choices.setdefault(str(row["random_choice_id"]), []).append(row)
        selected_choice = min(
            choices.values(),
            key=lambda choice: (
                -float(choice[0]["selection_probability"]),
                str(choice[0]["random_choice_id"]),
            ),
        )
        if not selected_choice[0]["is_empty_option"]:
            fixed.extend(selected_choice)
    return sorted(fixed, key=lambda row: (float(row["time_seconds"]), str(row["spawn_id"])))


def _route_waits(route: Mapping[str, Any], *, ticks_per_second: int) -> tuple[int, ...]:
    cells = [tuple(cell) for cell in route["cells"]]
    anchors = [tuple(anchor) for anchor in route["anchors"]]
    anchor_cell_indexes: list[int] = []
    cursor = 0
    for anchor in anchors:
        while cursor < len(cells) and cells[cursor] != anchor:
            cursor += 1
        if cursor >= len(cells):
            raise StageWorldModelError("route anchor is absent from its compiled cell walk")
        anchor_cell_indexes.append(cursor)
    waits = [0] * len(cells)
    for item in route["waits"]:
        anchor_index = int(item["after_anchor"])
        if anchor_index < 0 or anchor_index >= len(anchor_cell_indexes):
            raise StageWorldModelError("route wait references an invalid anchor")
        waits[anchor_cell_indexes[anchor_index]] += round(
            float(item["seconds"]) * ticks_per_second
        )
    return tuple(waits)


def _movement_layer(value: str) -> MovementLayer:
    return MovementLayer.FLYING if value == "flying" else MovementLayer.GROUND


def _deployment_class(profile: OperatorCombatProfile) -> DeploymentClass:
    if profile.position == "MELEE":
        return DeploymentClass.GROUND
    if profile.position == "RANGED":
        return DeploymentClass.HIGH_GROUND
    return DeploymentClass.EITHER


def _operator_definition(
    profile: OperatorCombatProfile,
    skills: Sequence[CompiledSkillPrototype] = (),
    *,
    ticks_per_second: int,
) -> OperatorDefinition:
    maximum_range = max(
        (abs(col) + abs(row) for col, row in profile.range_offsets), default=0
    )
    healing = profile.damage_kind == "healing"
    normal_attack_effects = (
        (
            StatModifierEffectDefinition(
                effect_id=f"{profile.operator_id}:trait:sluggish",
                target=EffectTargetSelector.ATTACKED_ENEMY,
                stat=EffectStat.MOVE_MILLI_TILES_PER_TICK,
                multiplier_milli=200,
                duration_ticks=max(1, round(0.8 * ticks_per_second)),
            ),
        )
        if profile.sub_profession_id == "slower"
        else ()
    )
    return OperatorDefinition(
        operator_id=profile.operator_id,
        max_hp=max(1, profile.hp),
        attack=0 if healing else max(0, profile.atk),
        damage_type=("arts" if profile.damage_kind == "arts" else "physical"),
        defense=max(0, profile.defence),
        magic_resistance=max(0, min(100, profile.res)),
        attack_interval_ticks=max(
            1, round(profile.attack_interval_seconds * ticks_per_second)
        ),
        attack_range_tiles=maximum_range,
        attack_range_offsets=profile.range_offsets,
        normal_attack_max_targets=(
            max(1, profile.block)
            if profile.sub_profession_id == "centurion"
            else 1
        ),
        normal_attack_effects=normal_attack_effects,
        attack_bonus_milli=profile.attack_bonus_milli,
        defense_bonus_milli=profile.defense_bonus_milli,
        attack_bonus_per_extra_blocked_milli=(
            profile.attack_bonus_per_extra_blocked_milli
        ),
        defense_bonus_per_extra_blocked_milli=(
            profile.defense_bonus_per_extra_blocked_milli
        ),
        block_count=max(0, profile.block),
        deploy_cost=max(0, profile.cost),
        redeploy_ticks=70 * ticks_per_second,
        retreat_refund_cost=max(0, profile.cost // 2),
        healing_power=max(0, profile.atk) if healing else 0,
        healing_interval_ticks=max(
            1, round(profile.attack_interval_seconds * ticks_per_second)
        ),
        healing_range_tiles=maximum_range if healing else 0,
        healing_range_offsets=profile.range_offsets if healing else (),
        deployment_class=_deployment_class(profile),
        can_target_flying=profile.can_target_flying,
        skills=tuple(
            {
                item.definition.skill_id: item.definition
                for item in skills
                if item.definition is not None
            }.values()
        ),
    )


def compile_simulator_bundle(
    model: StageWorldModel,
    profiles: Sequence[OperatorCombatProfile],
    *,
    scenario: str,
    skill_prototypes: Sequence[CompiledSkillPrototype] = (),
    stage_mode: str | None = None,
    ticks_per_second: int = TICKS_PER_SECOND,
) -> tuple[ContentBundle, list[Mapping[str, Any]]]:
    if ticks_per_second <= 0:
        raise StageWorldModelError("ticks_per_second must be positive")
    predefined_definitions, initial_predefines, excluded_predefines = (
        _compile_predefined_friendlies(model, ticks_per_second=ticks_per_second)
    )
    selected = _selected_spawn_events(
        model.spawn_events,
        scenario,
        stage_mode=(
            str(stage_mode)
            if stage_mode is not None
            else str(model.loaded_stage.get("mode") or "")
        ),
    )
    selected_route_ids = {
        model.route_index_to_id[int(event["route_index"])] for event in selected
    }
    routes = tuple(
        RouteDefinition(
            route_id=str(route["route_id"]),
            waypoints=tuple(GridPoint(col=cell[0], row=cell[1]) for cell in route["cells"]),
            waypoint_wait_ticks=_route_waits(route, ticks_per_second=ticks_per_second),
            movement_layer=_movement_layer(str(route["movement_layer"])),
        )
        for route in model.routes
        if route["route_id"] in selected_route_ids
    )
    enemy_by_id = {str(row["enemy_id"]): row for row in model.enemies}
    selected_enemy_ids = {str(event["enemy_id"]) for event in selected}
    enemies = []
    for enemy_id in sorted(selected_enemy_ids):
        row = enemy_by_id.get(enemy_id)
        if row is None or not row["resolved"] or row["hp"] is None:
            raise StageWorldModelError(f"selected enemy lacks resolved stats: {enemy_id}")
        movement_layers = {
            route.movement_layer
            for route in routes
            for event in selected
            if str(event["enemy_id"]) == enemy_id
            and route.route_id
            == model.route_index_to_id[int(event["route_index"])]
        }
        if len(movement_layers) != 1:
            raise StageWorldModelError(f"enemy has ambiguous movement layer: {enemy_id}")
        speed = float(row["move_speed"] or 1.0) * float(model.options["move_multiplier"])
        apply_way = int(row["apply_way"] if row["apply_way"] is not None else 1)
        if apply_way not in {0, 1, 2}:
            raise StageWorldModelError(
                f"selected enemy has unsupported applyWay {apply_way}: {enemy_id}"
            )
        talent_values = {
            str(item.get("key") or ""): float(item.get("value") or 0)
            for item in row["talent_blackboard"]
            if isinstance(item, Mapping) and str(item.get("key") or "")
        }
        refracting_resistance = max(
            0, round(talent_values.get("refracting.magic_resistance", 0))
        )
        formation_defense_aura = max(
            0, round(talent_values.get("auraDefup.def", 0))
        )
        enemies.append(
            EnemyDefinition(
                enemy_id=enemy_id,
                max_hp=max(1, round(float(row["hp"]))),
                attack=max(0, round(float(row["atk"] or 0))),
                defense=max(0, round(float(row["def"] or 0))),
                magic_resistance=max(0, min(100, round(float(row["res"] or 0)))),
                refracting_magic_resistance_bonus=min(
                    100, refracting_resistance
                ),
                formation_defense_aura=formation_defense_aura,
                # The reverse-engineered aura contract fixes this source
                # talent at 1.5 tiles; its blackboard only carries +DEF.
                formation_aura_radius_milli_tiles=(
                    1500 if formation_defense_aura else 0
                ),
                attack_mode={0: "none", 1: "melee", 2: "ranged"}[apply_way],
                damage_type="arts" if apply_way == 2 else "physical",
                attack_range_milli_tiles=max(
                    0, round(float(row["range_radius"] or 0) * 1000)
                ),
                attack_interval_ticks=max(
                    1, round(float(row["attack_time"] or 1.0) * ticks_per_second)
                ),
                move_milli_tiles_per_tick=max(
                    1, min(1000, round(speed * 1000 / ticks_per_second))
                ),
                move_milli_tiles_per_second=max(
                    1, min(30000, round(speed * 1000))
                ),
                movement_layer=next(iter(movement_layers)),
            )
        )
    spawns = tuple(
        SpawnDefinition(
            spawn_id=str(event["spawn_id"]),
            tick=max(1, round(float(event["time_seconds"]) * ticks_per_second)),
            enemy_id=str(event["enemy_id"]),
            route_id=model.route_index_to_id[int(event["route_index"])],
        )
        for event in selected
    )
    ground = tuple(GridPoint(col=col, row=row) for col, row in model.map["deployable_ground"])
    high = tuple(GridPoint(col=col, row=row) for col, row in model.map["deployable_high"])
    required_mechanisms = (
        "map",
        "route",
        "spawn",
        "movement",
        "cost",
        "deployment",
        "deployment_classes",
        "retreat",
        "blocking",
        *(("battle_start_friendlies",) if initial_predefines else ()),
        "basic_physical_attack",
        "damage",
        "death",
        "terminal",
        *(
            ("enemy_refracting_resistance",)
            if any(enemy.refracting_magic_resistance_bonus for enemy in enemies)
            else ()
        ),
        *(
            ("formation_defense_aura",)
            if any(enemy.formation_defense_aura for enemy in enemies)
            else ()
        ),
        *(("flying",) if any(route.movement_layer is MovementLayer.FLYING for route in routes) else ()),
        *(("healing",) if any(profile.damage_kind == "healing" for profile in profiles) else ()),
        *(
            ("multi_target_normal_attack",)
            if any(profile.sub_profession_id == "centurion" for profile in profiles)
            else ()
        ),
        *(
            ("blocked_enemy_stat_talent",)
            if any(
                profile.attack_bonus_milli
                or profile.defense_bonus_milli
                or profile.attack_bonus_per_extra_blocked_milli
                or profile.defense_bonus_per_extra_blocked_milli
                for profile in profiles
            )
            else ()
        ),
        *(
            ("normal_attack_slow",)
            if any(profile.sub_profession_id == "slower" for profile in profiles)
            else ()
        ),
        *(("skills",) if any(item.supported for item in skill_prototypes) else ()),
        *(
            ("buffs_debuffs",)
            if any(item.supported for item in skill_prototypes)
            or any(profile.sub_profession_id == "slower" for profile in profiles)
            else ()
        ),
    )
    bundle = ContentBundle(
        bundle_id=f"loaded-stage-{model.model_hash[:16]}-{scenario}",
        client_version=str(model.source.get("sha256") or "unknown")[:16],
        source=str(model.source.get("path") or "loaded-stage-source"),
        generator_version="arknights-stage-simulation-bridge/1.0.0",
        map=MapDefinition(
            width=int(model.map["width"]),
            height=int(model.map["height"]),
            deployable_tiles=(*ground, *high),
            ground_tiles=ground,
            high_ground_tiles=high,
        ),
        routes=routes,
        spawns=spawns,
        enemies=tuple(enemies),
        operators=tuple(
            _operator_definition(
                profile,
                tuple(item for item in skill_prototypes if item.operator_id == profile.operator_id),
                ticks_per_second=ticks_per_second,
            )
            for profile in profiles
        ),
        summons=predefined_definitions,
        initial_summons=initial_predefines,
        ticks_per_second=ticks_per_second,
        initial_cost=max(0, int(model.options["initial_cost"])),
        max_cost=max(0, int(model.options["max_cost"])),
        cost_recovery_interval_ticks=max(
            1,
            round(
                float(model.options["cost_increase_time_seconds"])
                * ticks_per_second
            ),
        ),
        initial_life_points=max(1, int(model.options["static_max_life_point"])),
        required_mechanisms=required_mechanisms,
        mechanism_fidelity=tuple(
            MechanismFidelity(
                mechanism=name,
                level=FidelityLevel.C,
                note=(
                    "source-visible level predefines compiled as full-stat friendly blockers"
                    if name == "battle_start_friendlies"
                    else (
                        "source-attested +70 RES is assumed active because enemy silence state is not compiled"
                        if name == "enemy_refracting_resistance"
                        else (
                            "source-attested additive +DEF from each other living formation source within 1.5 tiles; ordering versus external multipliers remains unknown"
                            if name == "formation_defense_aura"
                            else "loaded-stage planning approximation; see forecast fidelity receipt"
                        )
                    )
                ),
            )
            for name in required_mechanisms
        ),
        unsupported_mechanisms=tuple(
            name
            for name, present in (
                (
                    "hidden_predefined_tokens",
                    any(row["reason"] == "hidden" for row in excluded_predefines),
                ),
                (
                    "unsupported_visible_predefined_tokens",
                    any(
                        row["reason"] == "unsupported-visible-prototype"
                        for row in excluded_predefines
                    ),
                ),
            )
            if present
        ),
    )
    return bundle, selected


def _direction(value: str) -> Direction:
    try:
        return Direction(value)
    except ValueError as exc:
        raise StageWorldModelError(f"invalid deployment facing: {value!r}") from exc


def _actions(
    plan: Mapping[str, Any],
    profile_ids: Mapping[str, str],
    skill_prototypes: Sequence[CompiledSkillPrototype] = (),
    *,
    ticks_per_second: int,
) -> tuple[Any, ...]:
    deployments = plan.get("deployments") or []
    if not isinstance(deployments, list):
        raise StageWorldModelError("plan deployments must be a list")
    scheduled: list[tuple[int, int, Any]] = []
    current_tick = 0
    def action_tick(item: Mapping[str, Any]) -> int:
        has_tick = item.get("at_tick") is not None
        has_seconds = item.get("at_seconds") is not None
        if has_tick == has_seconds:
            raise StageWorldModelError(
                "each timed action must provide exactly one of at_tick or at_seconds"
            )
        if has_tick:
            value = item["at_tick"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise StageWorldModelError("at_tick must be a non-negative integer")
            return value
        return max(0, round(float(item["at_seconds"]) * ticks_per_second))

    ordered_deployments = sorted(
        deployments,
        key=lambda item: (action_tick(item), str(item.get("operator"))),
    )
    instance_by_query: dict[str, str] = {}
    for row in ordered_deployments:
        if not isinstance(row, Mapping):
            raise StageWorldModelError("each deployment must be an object")
        query = str(row.get("operator") or "")
        operator_id = profile_ids.get(query)
        if operator_id is None:
            raise StageWorldModelError(f"deployment operator lacks a profile: {query!r}")
        target_tick = action_tick(row)
        scheduled.append(
            (
                target_tick,
                0,
                DeployAction(
                operator_id=operator_id,
                position=GridPoint(col=int(row["col"]), row=int(row["row"])),
                direction=_direction(str(row.get("facing") or "right")),
                ),
            )
        )
        # Runtime instance ids share one counter with enemies and other entities,
        # so their numeric suffix cannot be predicted from deployment order.
        instance_by_query[query] = f"operator:{operator_id}"

    prototype_by_key = {
        (item.operator_id, item.skill_index): item
        for item in skill_prototypes
        if item.definition is not None
    }
    for row in plan.get("skills") or []:
        if not isinstance(row, Mapping):
            raise StageWorldModelError("each skill activation must be an object")
        query = str(row.get("operator") or "")
        operator_id = profile_ids.get(query)
        prototype = prototype_by_key.get(
            (operator_id or "", int(row.get("skill_index") or 0))
        )
        if operator_id is None or prototype is None or prototype.definition is None:
            raise StageWorldModelError(f"skill activation lacks a compiled prototype: {query!r}")
        instance_id = instance_by_query.get(query)
        if instance_id is None:
            raise StageWorldModelError(f"skill operator is not deployed by this plan: {query!r}")
        target_tick = action_tick(row)
        scheduled.append(
            (
                target_tick,
                1,
                ActivateSkillAction(
                    instance_id=instance_id,
                    skill_id=prototype.definition.skill_id,
                ),
            )
        )

    actions: list[Any] = []
    for target_tick, _priority, action in sorted(
        scheduled, key=lambda row: (row[0], row[1])
    ):
        if target_tick > current_tick:
            remaining = target_tick - current_tick
            while remaining > 0:
                chunk = min(remaining, 300)
                actions.append(WaitAction(ticks=chunk))
                remaining -= chunk
            current_tick = target_tick
        actions.append(action)
        current_tick += 1
    return tuple(actions)


def forecast_plan(
    model: StageWorldModel,
    plan: Mapping[str, Any],
    profiles_by_query: Mapping[str, OperatorCombatProfile],
    *,
    horizon_seconds: float,
    scenario: str,
    skill_prototypes: Sequence[CompiledSkillPrototype] = (),
    ticks_per_second: int = TICKS_PER_SECOND,
) -> dict[str, Any]:
    if ticks_per_second <= 0:
        raise StageWorldModelError("ticks_per_second must be positive")
    declared_tps = plan.get("ticks_per_second")
    if declared_tps is not None and declared_tps != ticks_per_second:
        raise StageWorldModelError("plan ticks_per_second does not match runner ticks_per_second")
    if not 0 < horizon_seconds <= 240:
        raise StageWorldModelError("horizon_seconds must be within (0, 240]")
    if not profiles_by_query:
        raise StageWorldModelError("plan must reference at least one operator")
    profiles = list({row.operator_id: row for row in profiles_by_query.values()}.values())
    requested_stage_mode = plan.get("stage_mode")
    if requested_stage_mode is not None and str(requested_stage_mode) not in {
        "normal",
        "hard",
        "emergency",
    }:
        raise StageWorldModelError(
            "plan stage_mode must be normal, hard, or emergency"
        )
    stage_mode_used = str(
        requested_stage_mode or model.loaded_stage.get("mode") or "unknown"
    )
    bundle, selected = compile_simulator_bundle(
        model,
        profiles,
        scenario=scenario,
        skill_prototypes=skill_prototypes,
        stage_mode=stage_mode_used,
        ticks_per_second=ticks_per_second,
    )
    _definitions, _initial_predefines, excluded_predefines = (
        _compile_predefined_friendlies(model, ticks_per_second=ticks_per_second)
    )
    current_life_points = plan.get("current_life_points")
    if current_life_points is not None:
        if (
            isinstance(current_life_points, bool)
            or not isinstance(current_life_points, int)
            or current_life_points <= 0
        ):
            raise StageWorldModelError("plan current_life_points must be a positive integer")
        bundle = bundle.model_copy(
            update={"initial_life_points": current_life_points}
        )
    actions = _actions(
        plan,
        {query: profile.operator_id for query, profile in profiles_by_query.items()},
        skill_prototypes,
        ticks_per_second=ticks_per_second,
    )
    horizon_ticks = max(1, round(horizon_seconds * ticks_per_second))
    engine = BattleEngine(bundle)
    result = engine.forecast(actions, horizon_ticks=horizon_ticks)
    kinds = Counter(event.kind for event in result.events)
    damage = sum(
        int(event.details.get("amount") or 0)
        for event in result.events
        if event.kind == "damage"
    )
    max_normal_attack_targets = max(
        (
            len(event.details.get("targets") or ())
            for event in result.events
            if event.kind == "attack"
            and str(event.actor_id).startswith("op-")
        ),
        default=0,
    )
    timeline_kinds = {"deploy", "death", "leak", "skill_activated", "terminal"}
    critical_timeline = [
        {
            "tick": event.tick,
            "seconds": round(event.tick / ticks_per_second, 6),
            "kind": event.kind,
            "actor_id": event.actor_id,
            "details": event.details,
        }
        for event in result.events
        if event.kind in timeline_kinds
    ]
    result_payload = {
        "simulated_ticks": result.simulated_ticks,
        "status": result.observation.status.value,
        "cost": result.observation.cost,
        "life_points": result.observation.life_points,
        "kills": result.observation.kills,
        "leaks": result.observation.leaks,
        "deployed": [row.model_dump(mode="json") for row in result.observation.deployed],
        "visible_enemies": [
            row.model_dump(mode="json") for row in result.observation.visible_enemies
        ],
        "event_counts": dict(sorted(kinds.items())),
        "critical_timeline": critical_timeline,
        "damage_total": damage,
        "max_normal_attack_targets_observed": max_normal_attack_targets,
        "snapshot_hash": result.final_snapshot_hash,
    }
    return {
        "ok": True,
        "model_hash": model.model_hash,
        "bundle_hash": bundle.content_hash,
        "plan_name": plan.get("name"),
        "scenario": scenario,
        "stage_mode_used": stage_mode_used,
        "stage_mode_source": "plan" if requested_stage_mode is not None else "stage-evidence",
        "horizon_seconds": horizon_seconds,
        "horizon_ticks": horizon_ticks,
        "ticks_per_second": ticks_per_second,
        "selected_spawn_option_count": len(selected),
        "selected_spawn_ids": [str(row["spawn_id"]) for row in selected],
        "predefined_friendlies": {
            "compiled": [
                item.model_dump(mode="json") for item in bundle.initial_summons
            ],
            "excluded": list(excluded_predefines),
        },
        "actions": [action.model_dump(mode="json") for action in actions],
        "skill_prototypes": [
            {
                "operator_id": prototype.operator_id,
                "skill_index": prototype.skill_index,
                "skill_id": prototype.skill_id,
                "level": prototype.level,
                "prototype": prototype.prototype,
                "fidelity": prototype.fidelity.value,
                "source_pointer": prototype.source_pointer,
                "field_mapping": prototype.field_mapping,
                "attack_range_offsets": (
                    []
                    if prototype.definition is None
                    else [
                        list(offset)
                        for offset in prototype.definition.attack_range_offsets
                    ]
                ),
            }
            for prototype in skill_prototypes
        ],
        "baseline_viability": _baseline_viability(
            result_payload, current_life_points=current_life_points
        ),
        "result": result_payload,
        "fidelity": {
            "geometry": "source anchors plus deterministic four-neighbour diagonal supercover",
            "route_waits": "modeled at source MOVE-anchor positions",
            "spawn_randomness": (
                "highest-probability option per random group"
                if scenario == "most-likely"
                else "all concrete mutually-exclusive candidates; deliberate stress upper bound"
            ),
            "damage": "normal physical/arts attacks and resistance; healing baseline only",
            "normal_attack_targeting": (
                "centurion attacks all enemies blocked by self up to current block count; "
                "other archetype-specific target rules remain uncompiled"
            ),
            "normal_attack_slow": (
                "slower attacks apply source-attested sluggish: movement x0.2 for 0.8 seconds"
            ),
            "enemy_refracting_resistance": (
                "source-attested self RES bonus is applied once and assumed active; "
                "enemy silence state is not compiled"
            ),
            "formation_defense_aura": (
                "each other living formation source within 1.5 tiles adds its source-attested DEF; "
                "ordering versus external stat multipliers remains unknown"
            ),
            "range": (
                "base normal attacks and operator healing use exact source range offsets "
                "and deployment facing; "
                "source-addressed skill ranges use exact source offsets; other "
                "range-changing skills fall back to maximum Manhattan radius"
            ),
            "omitted": [
                "uncompiled skills",
                "other uncompiled talents",
                "uncompiled traits",
                "uncompiled potential effects",
                "trust",
                "collectibles",
                "stage scripts and special devices",
                "live enemy state",
                *(
                    ["hidden or unsupported predefined tokens; see predefined_friendlies"]
                    if excluded_predefines
                    else []
                ),
            ],
        },
        "verdict": (
            "planning comparison only; a revise-plan baseline must not be chosen merely because it is less bad than another failing plan"
        ),
    }


def compare_plans(
    model: StageWorldModel,
    plan_a: Mapping[str, Any],
    profiles_a: Mapping[str, OperatorCombatProfile],
    plan_b: Mapping[str, Any],
    profiles_b: Mapping[str, OperatorCombatProfile],
    *,
    horizon_seconds: float,
    scenario: str,
    skill_prototypes_a: Sequence[CompiledSkillPrototype] = (),
    skill_prototypes_b: Sequence[CompiledSkillPrototype] = (),
    ticks_per_second: int = TICKS_PER_SECOND,
) -> dict[str, Any]:
    a = forecast_plan(
        model,
        plan_a,
        profiles_a,
        horizon_seconds=horizon_seconds,
        scenario=scenario,
        skill_prototypes=skill_prototypes_a,
        ticks_per_second=ticks_per_second,
    )
    b = forecast_plan(
        model,
        plan_b,
        profiles_b,
        horizon_seconds=horizon_seconds,
        scenario=scenario,
        skill_prototypes=skill_prototypes_b,
        ticks_per_second=ticks_per_second,
    )
    metrics = ("life_points", "kills", "leaks", "damage_total", "cost")
    delta = {
        key: b["result"][key] - a["result"][key]
        for key in metrics
    }
    a_revision = bool(
        a["baseline_viability"]["requires_revision_before_live_execution"]
    )
    b_revision = bool(
        b["baseline_viability"]["requires_revision_before_live_execution"]
    )
    if a_revision and b_revision:
        verdict = (
            "neither plan is baseline-viable; expand placement/order search or add explicit modeled mitigation before live execution"
        )
    elif a_revision != b_revision:
        verdict = (
            "only the non-revision plan is baseline-viable; relative score does not rescue the leaking plan"
        )
    else:
        verdict = "both plans are baseline-viable in this horizon; compare numeric outcomes"
    return {
        "ok": True,
        "scenario": scenario,
        "horizon_seconds": horizon_seconds,
        "plan_a": a,
        "plan_b": b,
        "delta_b_minus_a": delta,
        "verdict": verdict,
    }


def rank_forecast_plans(
    model: StageWorldModel,
    plans: Sequence[Mapping[str, Any]],
    profiles_by_query: Mapping[str, OperatorCombatProfile],
    *,
    horizon_seconds: float,
    scenario: str,
    skill_prototypes: Sequence[CompiledSkillPrototype] = (),
    top_k: int = 10,
    ticks_per_second: int = TICKS_PER_SECOND,
) -> dict[str, Any]:
    """Forecast and rank a finite caller-supplied candidate set.

    Candidate generation deliberately stays outside this bridge.  This helper is
    only the thin deterministic ranking layer needed by bounded placement/timing
    searches; it does not invent operators, coordinates, timings, or damage
    weights.
    """

    if not plans:
        raise StageWorldModelError("plans must contain at least one candidate")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise StageWorldModelError("top_k must be a positive integer")

    ranked: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for ordinal, plan in enumerate(plans):
        try:
            receipt = forecast_plan(
                model,
                plan,
                profiles_by_query,
                horizon_seconds=horizon_seconds,
                scenario=scenario,
                skill_prototypes=skill_prototypes,
                ticks_per_second=ticks_per_second,
            )
        except IllegalActionError as exc:
            rejected.append(
                {
                    "ordinal": ordinal,
                    "plan_name": plan.get("name"),
                    "error": str(exc),
                }
            )
            continue
        result = receipt["result"]
        operator_deaths = sum(
            1
            for event in result["critical_timeline"]
            if event["kind"] == "death"
            and event["details"].get("side") == "operator"
        )
        metrics = {
            "victory": result["status"] == "victory",
            "leaks": int(result["leaks"]),
            "operator_deaths": operator_deaths,
            "surviving_operators": len(result["deployed"]),
            "life_points": int(result["life_points"]),
            "kills": int(result["kills"]),
        }
        ranked.append(
            {
                "ordinal": ordinal,
                "plan_name": receipt["plan_name"],
                "metrics": metrics,
                "forecast": receipt,
            }
        )

    ranked.sort(
        key=lambda row: (
            not row["metrics"]["victory"],
            row["metrics"]["leaks"],
            row["metrics"]["operator_deaths"],
            -row["metrics"]["surviving_operators"],
            -row["metrics"]["life_points"],
            -row["metrics"]["kills"],
            row["ordinal"],
        )
    )
    return {
        "ok": True,
        "scenario": scenario,
        "horizon_seconds": horizon_seconds,
        "candidate_count": len(ranked),
        "rejected_count": len(rejected),
        "rejected": rejected,
        "ranking_basis": [
            "victory",
            "fewer_leaks",
            "fewer_operator_deaths",
            "more_surviving_operators",
            "more_life_points",
            "more_kills",
            "stable_input_order",
        ],
        "ranked": ranked[:top_k],
    }


__all__ = [
    "SCENARIOS",
    "TICKS_PER_SECOND",
    "compare_plans",
    "compile_simulator_bundle",
    "forecast_plan",
    "rank_forecast_plans",
]
