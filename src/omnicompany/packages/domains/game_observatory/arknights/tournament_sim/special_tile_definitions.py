# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""Special-tile payloads: periodic damage, healing, stat and control.

Moved verbatim out of ``models.py`` so that file fits the 2000-line
budget; ``models.py`` re-exports every name, so importers are unchanged.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, StrictInt, model_validator

from .model_primitives import (
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


class SpecialTileStatModifierDefinition(_Contract):
    """Finite, fixed-point stat mutation emitted by a special tile pulse."""

    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    effect_id: str = Field(min_length=1)
    kind: Literal["stat_modifier"] = "stat_modifier"
    stat: EffectStat
    additive: int = 0
    multiplier_milli: int = Field(default=1000, ge=0, le=10000)
    duration_ticks: int = Field(gt=0)

    @model_validator(mode="after")
    def modifier_is_not_a_noop(self) -> "SpecialTileStatModifierDefinition":
        if self.additive == 0 and self.multiplier_milli == 1000:
            raise ValueError("special tile stat modifier must change the selected stat")
        return self


class SpecialTileControlEffectDefinition(_Contract):
    """Finite control state emitted by a special tile pulse."""

    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    effect_id: str = Field(min_length=1)
    kind: Literal["control"] = "control"
    control: ControlEffect
    duration_ticks: int = Field(gt=0)


SpecialTileEffectDefinition = Annotated[
    Union[
        SpecialTileStatModifierDefinition,
        SpecialTileControlEffectDefinition,
    ],
    Field(discriminator="kind"),
]


class SpecialTileDefinition(_Contract):
    """Finite periodic damage, healing, stat, or control tile.

    Payloads are numeric members of a closed discriminated union.  There is no
    expression language, callback, script name, or dynamic import surface.
    """

    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    tile_id: str = Field(min_length=1)
    position: GridPoint
    damage: int = Field(default=0, ge=0)
    healing: int = Field(default=0, ge=0)
    interval_ticks: int = Field(gt=0)
    target: Literal["ground_enemies", "friendlies", "both"] = "ground_enemies"
    damage_type: Literal["physical", "true"] = "true"
    effects: tuple[SpecialTileEffectDefinition, ...] = ()

    @model_validator(mode="after")
    def payload_is_closed_and_nonempty(self) -> "SpecialTileDefinition":
        if self.damage and self.healing:
            raise ValueError(
                "special tile must define exactly one of damage or healing unless effects "
                "are used; both are invalid"
            )
        if not self.damage and not self.healing and not self.effects:
            raise ValueError("special tile must define damage, healing, or effects")
        if self.healing and self.target != "friendlies":
            raise ValueError("healing special tiles may target friendlies only")
        effect_ids = [effect.effect_id for effect in self.effects]
        if len(effect_ids) != len(set(effect_ids)):
            raise ValueError("special tile effect ids must be unique within a tile")
        for effect in self.effects:
            if isinstance(effect, SpecialTileStatModifierDefinition):
                enemy_target = self.target in {"ground_enemies", "both"}
                friendly_target = self.target in {"friendlies", "both"}
                if enemy_target and effect.stat not in {
                    EffectStat.ATTACK,
                    EffectStat.DEFENSE,
                    EffectStat.ATTACK_INTERVAL_TICKS,
                    EffectStat.MOVE_MILLI_TILES_PER_TICK,
                }:
                    raise ValueError("special tile stat is invalid for an enemy target")
                if friendly_target and effect.stat not in {
                    EffectStat.ATTACK,
                    EffectStat.DEFENSE,
                    EffectStat.HEALING_POWER,
                    EffectStat.ATTACK_INTERVAL_TICKS,
                    EffectStat.BLOCK_COUNT,
                }:
                    raise ValueError("special tile stat is invalid for a friendly target")
        return self
