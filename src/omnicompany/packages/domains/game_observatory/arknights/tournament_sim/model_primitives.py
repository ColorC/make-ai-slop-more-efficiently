"""Shared immutable primitives for tournament simulator contracts."""

from __future__ import annotations

import hashlib
import json
from enum import Enum, IntEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ENGINE_VERSION = "arknights-tournament-sim/0.10.0"
CONTENT_SCHEMA = "omnicompany.arknights.tournament-sim.content.v3"
INITIAL_STATE_SCHEMA = "omnicompany.arknights.tournament-sim.initial-state.v4"
SNAPSHOT_SCHEMA = "omnicompany.arknights.tournament-sim.snapshot.v8"
REPLAY_SCHEMA = "omnicompany.arknights.tournament-sim.replay.v8"
CHECKPOINT_SCHEMA = "omnicompany.arknights.tournament-sim.checkpoint.v2"
TICK_DIGEST_SCHEMA = "omnicompany.arknights.tournament-sim.tick-digest.v2"

_ADDITIVE_LEGACY_DEFAULTS: dict[str, object] = {
    "sp_recovery_mode": "time",
    "sp_per_recovery_event": 0,
    "activation_policy": "manual",
    "activation_hp_ratio_max_milli": None,
    "packet_interval_ticks": 0,
    "projectile_travel_ticks": None,
    "packet_index": 0,
    "packet_count": 1,
    "normal_attack_target_priority": "objective",
    "max_charges": 1,
    "consume_charges": 1,
    "armed": False,
    "armed_event_sequence": None,
    "initial_deployment_points_if_rostered": 0,
    "initial_deployment_points_bonus": 0,
    "max_cost_bonus": 0,
    "cost_recovery_slowdown_debt_milli": 0,
    "cost_recovery_multiplier_milli": 1000,
    "deployment_tick": 0,
    "periodic_self_stat_interval_ticks": 0,
    "periodic_self_stat_max_stacks": 0,
    "periodic_self_attack_per_stack_milli": 0,
    "periodic_self_defense_per_stack_milli": 0,
    "periodic_self_stat_source_semantic_id": None,
    "range_offsets": [],
    "range_origin": "source",
    "range_rotation": "none",
    "range_source_sha256": None,
    "move_milli_tiles_per_second": None,
    "movement_phase_remainder": 0,
    "route_teleport_destination_index": None,
    "source_skill_ids": [],
    "geometry_source_position": None,
    "geometry_source_direction": None,
    "geometry_cells": [],
    "geometry_target_ids": [],
    "initial_timed_modifiers": [],
    "minimum_block_count_to_stop": 1,
    "source_sha256": None,
    "content_version": None,
    "stage_id": None,
    "source_package_hash": None,
    "block_constraint": None,
    "source_wave_package_hash": None,
    "source_wave_scenario_id": None,
    "source_wave_scenario_assignments": [],
    "spawn_id": None,
    "spawn_ids": [],
    "source_action_ids": [],
    "source_wave_index": None,
    "enabled": None,
    "coefficient_runtime": None,
    "identity_detection_phase": None,
    "identity_detection_stacks": 0,
    "identity_detection_trigger_remaining_ticks": 0,
    "enhanced_attack_pending": False,
    "revealed_invisible": False,
    "coefficient_stun_remaining_ticks": 0,
    "coefficient_bleeding_remaining_ticks": 0,
    "coefficient_bleeding_pulse_remaining_ticks": 0,
    "coefficient_bleeding_damage": 0,
    "polluted_attack_charge": 0,
    "cannon_skill_cooldown_remaining_ticks": 0,
    "cannon_barrage_remaining_shots": 0,
    "cannon_barrage_next_shot_ticks": 0,
    "cannon_barrage_center": None,
    "polluted_zones": [],
    "visible_polluted_zones": [],
    "herd_resting_remaining_ticks": 0,
}


def _without_additive_legacy_defaults(value: Any) -> Any:
    """Keep hashes of older packages and traces byte-stable."""

    if isinstance(value, dict):
        return {
            key: _without_additive_legacy_defaults(child)
            for key, child in value.items()
            if key not in _ADDITIVE_LEGACY_DEFAULTS or child != _ADDITIVE_LEGACY_DEFAULTS[key]
        }
    if isinstance(value, (list, tuple)):
        return [_without_additive_legacy_defaults(child) for child in value]
    return value


def canonical_sha256(value: Any) -> str:
    """Return a platform-independent SHA-256 for a JSON-compatible value."""

    payload = json.dumps(
        _without_additive_legacy_defaults(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VisibilityClass(str, Enum):
    BASE_PUBLIC = "base_public"
    IS_RESTRICTED = "is_restricted"
    JUDGE_PRIVATE = "judge_private"


class FidelityLevel(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    U = "U"


class BattleStatus(str, Enum):
    RUNNING = "running"
    VICTORY = "victory"
    DEFEAT = "defeat"


class Direction(str, Enum):
    UP = "up"
    RIGHT = "right"
    DOWN = "down"
    LEFT = "left"


class EffectRangeOrigin(str, Enum):
    SOURCE = "source"
    SELECTED_TARGET = "selected_target"


class EffectRangeRotation(str, Enum):
    NONE = "none"
    SOURCE_FACING = "source_facing"


class MovementLayer(str, Enum):
    GROUND = "ground"
    FLYING = "flying"


class DeploymentClass(str, Enum):
    GROUND = "ground"
    HIGH_GROUND = "high_ground"
    EITHER = "either"


class EventPhase(IntEnum):
    ACTION = 10
    COST = 20
    SPAWN = 30
    BLOCK = 40
    ATTACK = 50
    DAMAGE = 60
    DEATH = 70
    MOVE = 80
    LEAK = 90
    TERMINAL = 100


class EffectTargetSelector(str, Enum):
    SELF = "self"
    LOWEST_HP_ALLY_IN_RANGE = "lowest_hp_ally_in_range"
    ALL_ALLIES_IN_RANGE = "all_allies_in_range"
    BLOCKED_ENEMIES = "blocked_enemies"
    ALL_ENEMIES_IN_RANGE = "all_enemies_in_range"
    SELECTED_ENEMY = "selected_enemy"
    ATTACKED_ENEMY = "attacked_enemy"


class EffectStat(str, Enum):
    ATTACK = "attack"
    DEFENSE = "defense"
    MAGIC_RESISTANCE = "magic_resistance"
    HEALING_POWER = "healing_power"
    ATTACK_INTERVAL_TICKS = "attack_interval_ticks"
    ATTACK_RANGE_TILES = "attack_range_tiles"
    BLOCK_COUNT = "block_count"
    MOVE_MILLI_TILES_PER_TICK = "move_milli_tiles_per_tick"


class ControlEffect(str, Enum):
    STUN = "stun"
    BIND = "bind"
    SILENCE = "silence"
    DISARM = "disarm"


class SkillSpRecoveryMode(str, Enum):
    TIME = "time"
    ATTACK = "attack"
    DAMAGED = "damaged"
    EVENT = "event"
    NONE = "none"


class SkillActivationPolicy(str, Enum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"
    NEXT_ATTACK = "next_attack"
    AUTO_NEXT_ATTACK = "auto_next_attack"
    NEXT_HEAL = "next_heal"
    AUTO_NEXT_HEAL = "auto_next_heal"


class SkillSpEvent(str, Enum):
    NORMAL_ATTACK_COMMITTED = "normal_attack_committed"
    DAMAGE_RECEIVED = "damage_received"
    HEAL_COMMITTED = "heal_committed"


__all__ = [
    "BattleStatus",
    "CHECKPOINT_SCHEMA",
    "CONTENT_SCHEMA",
    "ControlEffect",
    "DeploymentClass",
    "Direction",
    "ENGINE_VERSION",
    "EffectRangeOrigin",
    "EffectRangeRotation",
    "EffectStat",
    "EffectTargetSelector",
    "EventPhase",
    "FidelityLevel",
    "INITIAL_STATE_SCHEMA",
    "MovementLayer",
    "REPLAY_SCHEMA",
    "SNAPSHOT_SCHEMA",
    "SkillActivationPolicy",
    "SkillSpEvent",
    "SkillSpRecoveryMode",
    "TICK_DIGEST_SCHEMA",
    "VisibilityClass",
    "_Contract",
    "canonical_sha256",
]


# Moved from models.py: the most primitive shape in the contract set,
# needed by every definition family that was split out of it.
class GridPoint(_Contract):
    col: int = Field(ge=0)
    row: int = Field(ge=0)


# Moved from models.py alongside the effect definitions that are its only
# consumer; kept here so effect_definitions need not import models (cycle).
MAX_EFFECT_RANGE_OFFSET = 50
