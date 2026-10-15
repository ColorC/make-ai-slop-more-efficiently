# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""Closed numeric effect payloads a skill or tile may emit.

Moved verbatim out of ``models.py`` so that file fits the 2000-line
budget; ``models.py`` re-exports every name, so importers are unchanged.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, StrictInt, model_validator

from .model_primitives import (
    MAX_EFFECT_RANGE_OFFSET,
    ControlEffect,
    DeploymentClass,
    Direction,
    EffectRangeOrigin,
    EffectRangeRotation,
    EffectStat,
    EffectTargetSelector,
    GridPoint,
    SkillActivationPolicy,
    SkillSpEvent,
    SkillSpRecoveryMode,
    VisibilityClass,
    _Contract,
)


def _validate_effect_geometry(
    *,
    range_tiles: int,
    range_offsets: tuple[tuple[int, int], ...],
    range_origin: EffectRangeOrigin,
    range_rotation: EffectRangeRotation,
    range_source_sha256: str | None,
) -> None:
    if range_origin is EffectRangeOrigin.SELECTED_TARGET:
        raise ValueError("selected_target effect range origin is not implemented")
    if not range_offsets:
        if range_rotation is not EffectRangeRotation.NONE:
            raise ValueError("effect range rotation requires discrete offsets")
        if range_source_sha256 is not None:
            raise ValueError("effect range source hash requires discrete offsets")
        return
    if range_tiles > 0:
        raise ValueError("scalar range_tiles and range_offsets are mutually exclusive")
    if len(range_offsets) != len(set(range_offsets)):
        raise ValueError("effect range offsets must be unique")
    if range_source_sha256 is None:
        raise ValueError("discrete effect geometry requires a source hash")
    if any(
        abs(delta_col) > MAX_EFFECT_RANGE_OFFSET or abs(delta_row) > MAX_EFFECT_RANGE_OFFSET
        for delta_col, delta_row in range_offsets
    ):
        raise ValueError("effect range offset exceeds compiler bounds")


class HealEffectDefinition(_Contract):
    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    effect_id: str = Field(min_length=1)
    kind: Literal["heal"] = "heal"
    target: EffectTargetSelector
    amount: int = Field(gt=0)
    range_tiles: int = Field(default=0, ge=0)
    range_offsets: tuple[tuple[StrictInt, StrictInt], ...] = ()
    range_origin: EffectRangeOrigin = EffectRangeOrigin.SOURCE
    range_rotation: EffectRangeRotation = EffectRangeRotation.NONE
    range_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def target_is_an_ally_selector(self) -> "HealEffectDefinition":
        if self.target in {
            EffectTargetSelector.BLOCKED_ENEMIES,
            EffectTargetSelector.ALL_ENEMIES_IN_RANGE,
        }:
            raise ValueError("healing effects require an ally target selector")
        _validate_effect_geometry(
            range_tiles=self.range_tiles,
            range_offsets=self.range_offsets,
            range_origin=self.range_origin,
            range_rotation=self.range_rotation,
            range_source_sha256=self.range_source_sha256,
        )
        return self


class ScaledHealEffectDefinition(_Contract):
    """Heal by an exact fixed-point multiple of one current source stat."""

    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    effect_id: str = Field(min_length=1)
    kind: Literal["scaled_heal"] = "scaled_heal"
    target: EffectTargetSelector
    source_stat: EffectStat
    scale_milli: int = Field(gt=0, le=10000)
    range_tiles: int = Field(default=0, ge=0)
    range_offsets: tuple[tuple[StrictInt, StrictInt], ...] = ()
    range_origin: EffectRangeOrigin = EffectRangeOrigin.SOURCE
    range_rotation: EffectRangeRotation = EffectRangeRotation.NONE
    range_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def source_and_target_are_heal_compatible(self) -> "ScaledHealEffectDefinition":
        if self.source_stat not in {EffectStat.ATTACK, EffectStat.HEALING_POWER}:
            raise ValueError("scaled healing requires source attack or healing power")
        if self.target in {
            EffectTargetSelector.BLOCKED_ENEMIES,
            EffectTargetSelector.ALL_ENEMIES_IN_RANGE,
            EffectTargetSelector.SELECTED_ENEMY,
            EffectTargetSelector.ATTACKED_ENEMY,
        }:
            raise ValueError("scaled healing effects require an ally target selector")
        _validate_effect_geometry(
            range_tiles=self.range_tiles,
            range_offsets=self.range_offsets,
            range_origin=self.range_origin,
            range_rotation=self.range_rotation,
            range_source_sha256=self.range_source_sha256,
        )
        return self


class DamageEffectDefinition(_Contract):
    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    effect_id: str = Field(min_length=1)
    kind: Literal["damage"] = "damage"
    target: EffectTargetSelector
    amount: int = Field(gt=0)
    damage_type: Literal["physical", "true"] = "physical"
    range_tiles: int = Field(default=0, ge=0)
    range_offsets: tuple[tuple[StrictInt, StrictInt], ...] = ()
    range_origin: EffectRangeOrigin = EffectRangeOrigin.SOURCE
    range_rotation: EffectRangeRotation = EffectRangeRotation.NONE
    range_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def target_is_an_enemy_selector(self) -> "DamageEffectDefinition":
        if self.target not in {
            EffectTargetSelector.BLOCKED_ENEMIES,
            EffectTargetSelector.ALL_ENEMIES_IN_RANGE,
            EffectTargetSelector.SELECTED_ENEMY,
        }:
            raise ValueError("damage effects require an enemy target selector")
        _validate_effect_geometry(
            range_tiles=self.range_tiles,
            range_offsets=self.range_offsets,
            range_origin=self.range_origin,
            range_rotation=self.range_rotation,
            range_source_sha256=self.range_source_sha256,
        )
        return self


class AttackScaleEffectDefinition(_Contract):
    """Replace one normal attack packet with source-scaled repeated hits."""

    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    effect_id: str = Field(min_length=1)
    kind: Literal["attack_scale"] = "attack_scale"
    target: Literal[EffectTargetSelector.ATTACKED_ENEMY] = EffectTargetSelector.ATTACKED_ENEMY
    scale_milli: int = Field(gt=0)
    hit_count: int = Field(default=1, ge=1, le=64)
    packet_interval_ticks: int = Field(default=0, ge=0, le=300)
    projectile_travel_ticks: int | None = Field(default=None, ge=1, le=300)

    @model_validator(mode="after")
    def packet_timing_is_representable(self) -> "AttackScaleEffectDefinition":
        if self.hit_count == 1 and self.packet_interval_ticks:
            raise ValueError("a single-hit packet cannot have an inter-packet interval")
        if self.packet_interval_ticks and self.projectile_travel_ticks is None:
            raise ValueError("inter-packet timing requires positive projectile travel")
        if (
            self.projectile_travel_ticks is not None
            and self.projectile_travel_ticks
            + self.packet_interval_ticks * (self.hit_count - 1)
            > 300
        ):
            raise ValueError("last packet arrival exceeds the projectile timer bound")
        return self


class RandomDamageEffectDefinition(_Contract):
    """Bounded random damage from a named, snapshot-backed RNG stream.

    A draw is made once per selected target in stable target order.  Named
    streams isolate unrelated mechanics: adding draws to one stream cannot
    perturb another stream's sequence.
    """

    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    effect_id: str = Field(min_length=1)
    kind: Literal["random_damage"] = "random_damage"
    target: EffectTargetSelector
    minimum: int = Field(ge=0)
    maximum: int = Field(ge=0)
    rng_stream: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_.:-]+$")
    damage_type: Literal["physical", "true"] = "physical"
    range_tiles: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_random_damage(self) -> "RandomDamageEffectDefinition":
        if self.maximum < self.minimum:
            raise ValueError("random damage maximum cannot be less than minimum")
        if self.target not in {
            EffectTargetSelector.BLOCKED_ENEMIES,
            EffectTargetSelector.ALL_ENEMIES_IN_RANGE,
            EffectTargetSelector.SELECTED_ENEMY,
        }:
            raise ValueError("random damage effects require an enemy target selector")
        return self


class StatModifierEffectDefinition(_Contract):
    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    effect_id: str = Field(min_length=1)
    kind: Literal["stat_modifier"] = "stat_modifier"
    target: EffectTargetSelector
    stat: EffectStat
    additive: int = 0
    multiplier_milli: int = Field(default=1000, ge=0, le=10000)
    duration_ticks: int = Field(gt=0)
    range_tiles: int = Field(default=0, ge=0)
    range_offsets: tuple[tuple[StrictInt, StrictInt], ...] = ()
    range_origin: EffectRangeOrigin = EffectRangeOrigin.SOURCE
    range_rotation: EffectRangeRotation = EffectRangeRotation.NONE
    range_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def modifier_is_not_a_noop(self) -> "StatModifierEffectDefinition":
        if self.additive == 0 and self.multiplier_milli == 1000:
            raise ValueError("stat modifier must change the selected stat")
        enemy_target = self.target in {
            EffectTargetSelector.BLOCKED_ENEMIES,
            EffectTargetSelector.ALL_ENEMIES_IN_RANGE,
            EffectTargetSelector.SELECTED_ENEMY,
            EffectTargetSelector.ATTACKED_ENEMY,
        }
        if enemy_target and self.stat not in {
            EffectStat.ATTACK,
            EffectStat.DEFENSE,
            EffectStat.ATTACK_INTERVAL_TICKS,
            EffectStat.MOVE_MILLI_TILES_PER_TICK,
        }:
            raise ValueError("selected stat is invalid for an enemy target")
        if not enemy_target and self.stat not in {
            EffectStat.ATTACK,
            EffectStat.DEFENSE,
            EffectStat.HEALING_POWER,
            EffectStat.ATTACK_INTERVAL_TICKS,
            EffectStat.ATTACK_RANGE_TILES,
            EffectStat.BLOCK_COUNT,
        }:
            raise ValueError("selected stat is invalid for an operator target")
        _validate_effect_geometry(
            range_tiles=self.range_tiles,
            range_offsets=self.range_offsets,
            range_origin=self.range_origin,
            range_rotation=self.range_rotation,
            range_source_sha256=self.range_source_sha256,
        )
        return self


class SpMutationEffectDefinition(_Contract):
    """One explicit, event-bound SP delta to one explicit target skill."""

    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    effect_id: str = Field(min_length=1)
    kind: Literal["sp_mutation"] = "sp_mutation"
    event: SkillSpEvent
    target: Literal["self", "event_target"]
    target_skill_id: str = Field(min_length=1)
    amount: int

    @model_validator(mode="after")
    def mutation_is_closed(self) -> "SpMutationEffectDefinition":
        if self.amount == 0:
            raise ValueError("SP mutation amount cannot be zero")
        if self.target == "event_target" and self.event is not SkillSpEvent.HEAL_COMMITTED:
            raise ValueError("event_target SP mutation requires heal_committed")
        return self


class DeploymentPointsEffectDefinition(_Contract):
    """One explicit positive mutation of the battle deployment-point pool."""

    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    effect_id: str = Field(min_length=1)
    kind: Literal["deployment_points"] = "deployment_points"
    target: Literal["battle_cost_pool"]
    delta: int = Field(strict=True)
    overflow_policy: Literal["clamp"] = "clamp"
    underflow_policy: Literal["reject_activation"] = "reject_activation"

    @model_validator(mode="after")
    def mutation_is_authorized(self) -> "DeploymentPointsEffectDefinition":
        if self.delta == 0:
            raise ValueError("deployment-point mutation delta cannot be zero")
        if self.delta < 0:
            raise ValueError("negative deployment-point mutation is not authorized")
        return self


class SummonEffectDefinition(_Contract):
    """Spawn one declared summon at one declared tile."""

    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    effect_id: str = Field(min_length=1)
    kind: Literal["summon"] = "summon"
    summon_id: str = Field(min_length=1)
    position: GridPoint


EffectDefinition = Annotated[
    Union[
        HealEffectDefinition,
        ScaledHealEffectDefinition,
        DamageEffectDefinition,
        AttackScaleEffectDefinition,
        RandomDamageEffectDefinition,
        StatModifierEffectDefinition,
        SpMutationEffectDefinition,
        DeploymentPointsEffectDefinition,
        SummonEffectDefinition,
    ],
    Field(discriminator="kind"),
]
