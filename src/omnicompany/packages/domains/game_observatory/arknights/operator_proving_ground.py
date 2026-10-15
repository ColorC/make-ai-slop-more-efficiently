# [OMNI] origin=codex created_by=codex intent="run deterministic recommendation-free Arknights operator experiments"
"""Deterministic, recommendation-free operator experiments for the simulator."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .skill_prototype_bridge import CompiledSkillPrototype, compile_operator_skill_prototype
from .stage_simulation_bridge import TICKS_PER_SECOND
from .stage_world_model import OperatorCombatProfile
from .tournament_sim import (
    ActivateSkillAction, BattleEngine, ContentBundle, DeployAction, DeploymentClass,
    Direction, EnemyDefinition, FidelityLevel, GridPoint, MapDefinition,
    MechanismFidelity, MovementLayer, OperatorDefinition, RouteDefinition,
    SpawnDefinition, WaitAction,
)
from .tournament_sim.models import canonical_sha256

ARTIFACT_SCHEMA = "omnicompany.arknights.operator-understanding.v1"


@dataclass(frozen=True, slots=True)
class ProvingTemplate:
    """Fixed geometry and waves; it deliberately contains no operator advice."""

    template_id: str
    geometry: tuple[tuple[int, int], ...]
    waves: tuple[tuple[int, str], ...]
    enemies: tuple[tuple[str, int, int, int, bool], ...]
    horizon_ticks: int

    @property
    def template_hash(self) -> str:
        return canonical_sha256({
            "template_id": self.template_id, "geometry": self.geometry,
            "waves": self.waves, "enemies": self.enemies,
            "horizon_ticks": self.horizon_ticks,
        })


TEMPLATES = (
    ProvingTemplate("standard_first_battle", ((0, 0), (1, 0), (2, 0), (3, 0)),
                    ((1, "scout"), (45, "scout"), (90, "scout")),
                    (("scout", 550, 75, 20, False),), 180),
    ProvingTemplate("anti_air_wave", ((0, 0), (1, 0), (2, 0), (3, 0)),
                    ((1, "drone"), (25, "drone"), (50, "drone"), (75, "drone")),
                    (("drone", 420, 60, 0, True),), 180),
    ProvingTemplate("high_pressure_elite_wave", ((0, 0), (1, 0), (2, 0), (3, 0)),
                    ((1, "elite"), (65, "elite")),
                    (("elite", 1800, 330, 180, False),), 240),
    ProvingTemplate("sustained_attrition", ((0, 0), (1, 0), (2, 0), (3, 0)),
                    tuple((1 + number * 25, "raider") for number in range(8)),
                    (("raider", 700, 130, 55, False),), 300),
)
_TEMPLATE_BY_ID = {item.template_id: item for item in TEMPLATES}


def templates() -> tuple[ProvingTemplate, ...]:
    return TEMPLATES


def _operator(profile: OperatorCombatProfile, skills: Iterable[CompiledSkillPrototype]) -> OperatorDefinition:
    supported = tuple(item.definition for item in skills if item.definition is not None)
    return OperatorDefinition(
        operator_id=profile.operator_id, max_hp=max(1, profile.hp),
        attack=0 if profile.damage_kind == "healing" else max(0, profile.atk),
        damage_type="arts" if profile.damage_kind == "arts" else "physical",
        defense=max(0, profile.defence),
        attack_interval_ticks=max(1, round(profile.attack_interval_seconds * TICKS_PER_SECOND)),
        attack_range_tiles=max((abs(col) + abs(row) for col, row in profile.range_offsets), default=0),
        block_count=max(0, profile.block), deploy_cost=max(0, profile.cost),
        healing_power=max(0, profile.atk) if profile.damage_kind == "healing" else 0,
        healing_interval_ticks=max(1, round(profile.attack_interval_seconds * TICKS_PER_SECOND)),
        healing_range_tiles=max((abs(col) + abs(row) for col, row in profile.range_offsets), default=0),
        deployment_class=DeploymentClass.GROUND if profile.position == "MELEE" else DeploymentClass.HIGH_GROUND,
        can_target_flying=profile.can_target_flying, skills=supported,
    )


def _bundle(template: ProvingTemplate, profile: OperatorCombatProfile, skills: Iterable[CompiledSkillPrototype]) -> ContentBundle:
    points = tuple(GridPoint(col=col, row=row) for col, row in template.geometry)
    enemy_rows = {row[0]: row for row in template.enemies}
    enemies = tuple(EnemyDefinition(
        enemy_id=name, max_hp=hp, attack=attack, defense=defense,
        move_milli_tiles_per_tick=100, attack_interval_ticks=10,
        movement_layer=MovementLayer.FLYING if flying else MovementLayer.GROUND,
    ) for name, hp, attack, defense, flying in template.enemies)
    required_mechanisms = (
        "map", "route", "spawn", "movement", "cost", "deployment", "deployment_classes", "blocking", "flying",
        "basic_physical_attack", "damage", "death", "terminal", "skills", "buffs_debuffs",
        *(("healing",) if profile.damage_kind == "healing" else ()),
    )
    return ContentBundle(
        bundle_id=f"operator-proving-ground-{template.template_id}-v1", client_version="fixed-v1",
        source=f"operator-proving-ground:{template.template_hash}",
        map=MapDefinition(width=4, height=1, deployable_tiles=(points[1], points[2]),
                          ground_tiles=(points[1],), high_ground_tiles=(points[2],)),
        routes=(
            RouteDefinition(route_id="ground", waypoints=points),
            RouteDefinition(route_id="flying", waypoints=points, movement_layer=MovementLayer.FLYING),
        ),
        spawns=tuple(SpawnDefinition(
            spawn_id=f"{name}-{index}", tick=tick, enemy_id=name,
            route_id="flying" if enemy_rows[name][4] else "ground",
        ) for index, (tick, name) in enumerate(template.waves)), 
        enemies=enemies, operators=(_operator(profile, skills),), ticks_per_second=TICKS_PER_SECOND,
        initial_cost=max(99, profile.cost), max_cost=99, cost_recovery_interval_ticks=10,
        initial_life_points=99,
        required_mechanisms=required_mechanisms,
        mechanism_fidelity=tuple(MechanismFidelity(mechanism=name, level=FidelityLevel.C,
            note="fixed proving-ground approximation") for name in required_mechanisms),
    )


def run_experiment(profile: OperatorCombatProfile, template_id: str, *, database: Path | str | None = None,
                   skill_index: int | None = None, skill_level: int | None = None) -> dict[str, Any]:
    """Run one reproducible single-operator experiment; unknown mechanics stay visible."""
    template = _TEMPLATE_BY_ID[template_id]
    prototype: CompiledSkillPrototype | None = None
    if database is not None and skill_index is not None:
        prototype = compile_operator_skill_prototype(database, profile.operator_id, skill_index, level=skill_level)
    skills = () if prototype is None else (prototype,)
    bundle = _bundle(template, profile, skills)
    engine = BattleEngine(bundle)
    position = GridPoint(col=1 if profile.position == "MELEE" else 2, row=0)
    steps = []

    def step_if_running(action: Any) -> bool:
        """Record every real simulator step; never invent a post-terminal one."""
        if engine.status.value != "running":
            return False
        steps.append(engine.step(action))
        return engine.status.value == "running"

    step_if_running(DeployAction(operator_id=profile.operator_id, position=position, direction=Direction.RIGHT))
    skill_cycle: dict[str, Any] | None = None
    # Activation evidence is emitted only for an EffectVM prototype that was
    # actually supplied, reached readiness, and was stepped by the engine.
    if prototype is not None and prototype.supported and prototype.definition is not None:
        definition = prototype.definition
        missing_sp = max(0, definition.sp_cost - definition.initial_sp)
        ready_ticks = (missing_sp + definition.sp_per_tick - 1) // definition.sp_per_tick
        if engine.tick + ready_ticks < template.horizon_ticks:
            if ready_ticks:
                step_if_running(WaitAction(ticks=ready_ticks))
            if engine.status.value == "running":
                activated = step_if_running(ActivateSkillAction(
                    instance_id="op-000001", skill_id=definition.skill_id,
                ))
                if activated or steps[-1].events:
                    skill_cycle = {
                        "prototype": prototype.prototype,
                        "skill_id": definition.skill_id,
                        "activated": any(event.kind == "skill_activated" for event in steps[-1].events),
                    }
    if engine.status.value == "running":
        remaining = template.horizon_ticks - engine.tick
        if remaining > 0:
            step_if_running(WaitAction(ticks=remaining))

    # StepResult is deliberately incremental.  Aggregating only the final
    # WaitAction used to discard deployment/skill-window damage and early kills.
    events = tuple(event for step in steps for event in step.events)
    tick_digests = tuple(digest for step in steps for digest in step.tick_digests)
    observation = steps[-1].observation
    damage_taken = sum(event.details.get("amount", 0) for event in events
                       if event.kind == "damage" and event.actor_id == "op-000001")
    death_ticks = [event.tick for event in events if event.kind == "death" and event.actor_id.startswith("enemy-")]
    source_hash = None if database is None else hashlib.sha256(Path(database).read_bytes()).hexdigest()
    not_modeled = ["talents", "traits", "potential", "trust", "collectibles", "exact directional range shape"]
    if prototype is None:
        not_modeled.append("selected skill was not supplied")
    elif not prototype.supported:
        not_modeled.append(f"skill: {prototype.unsupported_reason}")
    return {
        "template_id": template.template_id, "template_hash": template.template_hash,
        "bundle_hash": bundle.content_hash, "model_source_hash": source_hash,
        "operator_id": profile.operator_id, "zero_leak": observation.leaks == 0,
        "leaks": observation.leaks, "damage_taken": damage_taken,
        "kill_time_ticks": max(death_ticks) if death_ticks else None,
        "deployment_cost_efficiency": round(observation.kills / max(1, profile.cost), 6),
        "kills": observation.kills, "terminal_status": observation.status.value,
        "event_count": len(events), "event_hash": canonical_sha256([event.model_dump(mode="json") for event in events]),
        "tick_digest_hash": canonical_sha256([digest.model_dump(mode="json") for digest in tick_digests]),
        "fidelity": "C", "not_modeled": not_modeled,
        "skill_cycle": skill_cycle,
        "skill_prototype": None if prototype is None else {"supported": prototype.supported,
            "prototype": prototype.prototype, "source_pointer": prototype.source_pointer,
            "unsupported_reason": prototype.unsupported_reason},
    }


def serialize_slot_artifact(slot_id: str, version: int, understanding: dict[str, Any]) -> bytes:
    """Serialize contestant-created understanding without supplying rankings."""
    if not slot_id or version < 1:
        raise ValueError("slot_id and positive version are required")
    return json.dumps({"schema": ARTIFACT_SCHEMA, "slot_id": slot_id, "version": version,
                       "understanding": understanding}, sort_keys=True, separators=(",", ":")).encode()


__all__ = ["ARTIFACT_SCHEMA", "ProvingTemplate", "run_experiment", "serialize_slot_artifact", "templates"]
