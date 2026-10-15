# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""Operator and summon skill contracts.

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

from .effect_definitions import (  # noqa: F401
    AttackScaleEffectDefinition,
    DamageEffectDefinition,
    DeploymentPointsEffectDefinition,
    EffectDefinition,
    HealEffectDefinition,
    RandomDamageEffectDefinition,
    ScaledHealEffectDefinition,
    SpMutationEffectDefinition,
    StatModifierEffectDefinition,
    SummonEffectDefinition,
)


class SkillDefinition(_Contract):
    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    skill_id: str = Field(min_length=1)
    sp_cost: int = Field(gt=0)
    initial_sp: int = Field(default=0, ge=0)
    sp_per_tick: int = Field(default=1, ge=0)
    sp_recovery_mode: SkillSpRecoveryMode = SkillSpRecoveryMode.TIME
    sp_per_recovery_event: int = Field(default=0, ge=0)
    activation_policy: SkillActivationPolicy = SkillActivationPolicy.MANUAL
    activation_hp_ratio_max_milli: int | None = Field(default=None, ge=1, le=1000)
    max_charges: int = Field(default=1, ge=1, le=16)
    consume_charges: int = Field(default=1, ge=1, le=16)
    cooldown_ticks: int = Field(default=0, ge=0)
    duration_ticks: int = Field(default=1, gt=0)
    attack_range_offsets: tuple[tuple[int, int], ...] = ()
    effects: tuple[EffectDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_skill(self) -> "SkillDefinition":
        if self.initial_sp > self.sp_cost * self.max_charges:
            raise ValueError("initial_sp cannot exceed the declared charge capacity")
        if self.consume_charges > self.max_charges:
            raise ValueError("consume_charges cannot exceed max_charges")
        if self.sp_recovery_mode is not SkillSpRecoveryMode.TIME and self.sp_per_tick > 0:
            raise ValueError("sp_per_tick must be zero outside time recovery mode")
        if (
            self.sp_recovery_mode
            in {
                SkillSpRecoveryMode.TIME,
                SkillSpRecoveryMode.NONE,
            }
            and self.sp_per_recovery_event
        ):
            raise ValueError("time/none recovery cannot declare sp_per_recovery_event")
        effect_ids = [effect.effect_id for effect in self.effects]
        if len(effect_ids) != len(set(effect_ids)):
            raise ValueError("effect_id values must be unique within a skill")
        if len(self.attack_range_offsets) != len(set(self.attack_range_offsets)):
            raise ValueError("skill attack range offsets must be unique")
        attacked_enemy_effects = tuple(
            effect
            for effect in self.effects
            if getattr(effect, "target", None) is EffectTargetSelector.ATTACKED_ENEMY
        )
        next_attack_policies = {
            SkillActivationPolicy.NEXT_ATTACK,
            SkillActivationPolicy.AUTO_NEXT_ATTACK,
        }
        if attacked_enemy_effects and (
            self.activation_policy not in next_attack_policies
            or any(
                not isinstance(effect, AttackScaleEffectDefinition)
                for effect in attacked_enemy_effects
            )
        ):
            raise ValueError("attacked_enemy attack_scale requires a next-attack policy")
        if len(attacked_enemy_effects) > 1:
            raise ValueError("a skill may declare at most one attack_scale packet")
        if self.activation_policy is SkillActivationPolicy.AUTOMATIC and any(
            getattr(effect, "target", None) is EffectTargetSelector.SELECTED_ENEMY
            for effect in self.effects
        ):
            raise ValueError("automatic activation cannot require target selection")
        if (
            any(isinstance(effect, DeploymentPointsEffectDefinition) for effect in self.effects)
            and self.activation_policy is not SkillActivationPolicy.MANUAL
        ):
            raise ValueError(
                "deployment-point effects require manual activation in this contract slice"
            )
        packet_effects = tuple(
            effect for effect in self.effects if not isinstance(effect, SpMutationEffectDefinition)
        )
        if self.activation_policy in next_attack_policies:
            if not packet_effects or any(
                isinstance(
                    effect,
                    (
                        HealEffectDefinition,
                        ScaledHealEffectDefinition,
                        SummonEffectDefinition,
                    ),
                )
                for effect in packet_effects
            ):
                raise ValueError("next_attack requires an attack-compatible payload")
        next_heal_policies = {
            SkillActivationPolicy.NEXT_HEAL,
            SkillActivationPolicy.AUTO_NEXT_HEAL,
        }
        if self.activation_policy in next_heal_policies:
            if not any(
                isinstance(effect, (HealEffectDefinition, ScaledHealEffectDefinition))
                for effect in packet_effects
            ) or any(
                isinstance(
                    effect,
                    (DamageEffectDefinition, RandomDamageEffectDefinition, SummonEffectDefinition),
                )
                for effect in packet_effects
            ):
                raise ValueError("next_heal requires a heal-compatible payload")
        if self.activation_policy is SkillActivationPolicy.AUTO_NEXT_HEAL:
            heal_packets = tuple(
                effect
                for effect in packet_effects
                if isinstance(effect, (HealEffectDefinition, ScaledHealEffectDefinition))
            )
            if (
                len(heal_packets) != 1
                or heal_packets[0].target
                is not EffectTargetSelector.LOWEST_HP_ALLY_IN_RANGE
                or self.activation_hp_ratio_max_milli is None
            ):
                raise ValueError(
                    "auto_next_heal requires one thresholded lowest-HP ally heal"
                )
        elif self.activation_hp_ratio_max_milli is not None:
            raise ValueError(
                "activation HP threshold is only valid for auto_next_heal"
            )
        return self
