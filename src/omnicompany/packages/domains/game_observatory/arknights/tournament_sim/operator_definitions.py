# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""The operator contract.

Moved verbatim out of ``models.py`` so that file fits the 2000-line budget;
``models.py`` re-exports the name, so importers are unchanged.  It sits in its
own module rather than beside the summons in ``entity_definitions`` because
that module is explicitly the *non-operator* entities.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .effect_definitions import StatModifierEffectDefinition
from .model_primitives import (
    DeploymentClass,
    EffectTargetSelector,
    VisibilityClass,
    _Contract,
)
from .skill_definitions import SkillDefinition  # noqa: F401


class OperatorDefinition(_Contract):
    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    operator_id: str = Field(min_length=1)
    max_hp: int = Field(gt=0)
    attack: int = Field(ge=0)
    damage_type: Literal["physical", "arts"] = "physical"
    defense: int = Field(ge=0)
    magic_resistance: int = Field(default=0, ge=0, le=100)
    attack_interval_ticks: int = Field(gt=0)
    attack_interval_robust_ticks: tuple[int, int] | None = None
    attack_interval_pending_live_readback: bool = False
    attack_interval_source_semantic_id: str | None = None
    attack_range_tiles: int = Field(default=1, ge=0)
    attack_range_offsets: tuple[tuple[int, int], ...] = ()
    normal_attack_max_targets: int = Field(default=1, ge=1, le=16)
    normal_attack_target_priority: Literal["objective", "highest_defense"] = "objective"
    normal_attack_effects: tuple[StatModifierEffectDefinition, ...] = ()
    normal_attack_scale_milli: int = Field(default=1000, gt=0, le=10000)
    normal_attack_aftershock_count: int = Field(default=0, ge=0, le=16)
    normal_attack_aftershock_scale_milli: int = Field(default=0, ge=0, le=10000)
    normal_attack_aftershock_radius_milli_tiles: int = Field(
        default=0, ge=0, le=20000
    )
    normal_attack_aftershock_ground_only: bool = False
    normal_attack_aftershock_source_semantic_id: str | None = Field(
        default=None, min_length=1
    )
    normal_attack_random_scale_probability_milli: int = Field(
        default=0, ge=0, le=1000
    )
    normal_attack_random_scale_milli: int = Field(default=0, ge=0, le=10000)
    normal_attack_random_scale_rng_stream: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[a-z0-9_.:-]+$"
    )
    normal_attack_random_scale_source_semantic_id: str | None = Field(
        default=None, min_length=1
    )
    normal_attack_aftershock_proc_probability_milli: int = Field(
        default=0, ge=0, le=1000
    )
    normal_attack_aftershock_proc_scale_milli: int = Field(
        default=0, ge=0, le=10000
    )
    normal_attack_aftershock_proc_radius_milli_tiles: int = Field(
        default=0, ge=0, le=20000
    )
    normal_attack_aftershock_proc_stun_ticks: int = Field(default=0, ge=0, le=300)
    normal_attack_aftershock_proc_rng_stream: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[a-z0-9_.:-]+$"
    )
    normal_attack_aftershock_proc_source_semantic_id: str | None = Field(
        default=None, min_length=1
    )
    incoming_damage_resistance_probability_milli: int = Field(default=0, ge=0, le=1000)
    incoming_damage_resistance_rng_stream: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[a-z0-9_.:-]+$"
    )
    incoming_damage_resistance_source_semantic_id: str | None = Field(default=None, min_length=1)
    incoming_attack_retaliation_scale_milli: int = Field(default=0, ge=0, le=10000)
    incoming_attack_retaliation_on_resisted: bool = False
    incoming_attack_retaliation_source_semantic_id: str | None = Field(default=None, min_length=1)
    attack_bonus_milli: int = Field(default=0, ge=0)
    defense_bonus_milli: int = Field(default=0, ge=0)
    attack_bonus_per_extra_blocked_milli: int = Field(default=0, ge=0)
    defense_bonus_per_extra_blocked_milli: int = Field(default=0, ge=0)
    periodic_self_stat_interval_ticks: int = Field(default=0, ge=0)
    periodic_self_stat_max_stacks: int = Field(default=0, ge=0, le=100)
    periodic_self_attack_per_stack_milli: int = Field(default=0, ge=0, le=10000)
    periodic_self_defense_per_stack_milli: int = Field(default=0, ge=0, le=10000)
    periodic_self_stat_source_semantic_id: str | None = Field(default=None, min_length=1)
    block_count: int = Field(default=1, ge=0)
    deploy_cost: int = Field(ge=0)
    redeploy_ticks: int = Field(default=0, ge=0)
    retreat_refund_cost: int = Field(default=0, ge=0)
    initial_deployment_points_if_rostered: int = Field(default=0, ge=0, strict=True)
    healing_power: int = Field(default=0, ge=0)
    healing_interval_ticks: int = Field(default=1, gt=0)
    healing_range_tiles: int = Field(default=0, ge=0)
    healing_range_offsets: tuple[tuple[int, int], ...] = ()
    source_skill_ids: tuple[str, ...] = ()
    skills: tuple[SkillDefinition, ...] = ()
    deployment_class: DeploymentClass = DeploymentClass.EITHER
    can_target_flying: bool = False
    projectile_travel_ticks: int = Field(default=0, ge=0, le=300)
    projectile_splash_radius_tiles: int = Field(default=0, ge=0, le=20)

    @model_validator(mode="after")
    def skill_ids_are_unique(self) -> "OperatorDefinition":
        if self.attack_interval_robust_ticks is not None:
            lower, upper = self.attack_interval_robust_ticks
            if lower <= 0 or upper < lower:
                raise ValueError("attack interval robust bounds must be positive and ordered")
            if not lower <= self.attack_interval_ticks <= upper:
                raise ValueError("attack interval point value must lie within robust bounds")
            if not self.attack_interval_pending_live_readback:
                raise ValueError("robust attack interval requires pending live readback")
            if not self.attack_interval_source_semantic_id:
                raise ValueError("robust attack interval requires a source semantic id")
        elif self.attack_interval_pending_live_readback or self.attack_interval_source_semantic_id:
            raise ValueError("attack interval readback metadata requires robust bounds")
        skill_ids = [skill.skill_id for skill in self.skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("skill_id values must be unique within an operator")
        normal_effect_ids = [effect.effect_id for effect in self.normal_attack_effects]
        if len(normal_effect_ids) != len(set(normal_effect_ids)):
            raise ValueError("normal attack effect ids must be unique within an operator")
        if any(
            effect.target is not EffectTargetSelector.ATTACKED_ENEMY
            for effect in self.normal_attack_effects
        ):
            raise ValueError("normal attack effects must target attacked_enemy")
        if len(self.attack_range_offsets) != len(set(self.attack_range_offsets)):
            raise ValueError("attack range offsets must be unique")
        if len(self.healing_range_offsets) != len(set(self.healing_range_offsets)):
            raise ValueError("healing range offsets must be unique")
        if len(self.source_skill_ids) != len(set(self.source_skill_ids)):
            raise ValueError("source skill ids must be unique")
        if self.source_skill_ids and not set(skill_ids) <= set(self.source_skill_ids):
            raise ValueError("compiled skills must be declared by source_skill_ids")
        if self.healing_range_offsets and not self.healing_power:
            raise ValueError("healing range offsets require positive healing power")
        if self.projectile_splash_radius_tiles and not self.projectile_travel_ticks:
            raise ValueError("projectile splash requires a positive projectile travel time")
        aftershock_shape = bool(
            self.normal_attack_aftershock_count
            or self.normal_attack_aftershock_scale_milli
            or self.normal_attack_aftershock_radius_milli_tiles
            or self.normal_attack_aftershock_ground_only
            or self.normal_attack_aftershock_source_semantic_id
        )
        if aftershock_shape and not (
            self.normal_attack_aftershock_count
            and self.normal_attack_aftershock_scale_milli
            and self.normal_attack_aftershock_radius_milli_tiles
            and self.normal_attack_aftershock_source_semantic_id
        ):
            raise ValueError(
                "normal attack aftershock requires count, scale, radius, and source semantics"
            )
        resistance_shape = bool(
            self.incoming_damage_resistance_probability_milli
            or self.incoming_damage_resistance_rng_stream
            or self.incoming_damage_resistance_source_semantic_id
        )
        if resistance_shape and not (
            self.incoming_damage_resistance_probability_milli
            and self.incoming_damage_resistance_rng_stream
            and self.incoming_damage_resistance_source_semantic_id
        ):
            raise ValueError(
                "incoming damage resistance requires probability, RNG stream, and source semantics"
            )
        if bool(self.incoming_attack_retaliation_scale_milli) != bool(
            self.incoming_attack_retaliation_source_semantic_id
        ):
            raise ValueError("incoming attack retaliation requires scale and source semantics")
        if self.incoming_attack_retaliation_on_resisted and not self.incoming_attack_retaliation_scale_milli:
            raise ValueError("retaliation on resisted attacks requires a retaliation definition")
        random_scale_shape = bool(
            self.normal_attack_random_scale_probability_milli
            or self.normal_attack_random_scale_milli
            or self.normal_attack_random_scale_rng_stream
            or self.normal_attack_random_scale_source_semantic_id
        )
        if random_scale_shape and not (
            self.normal_attack_random_scale_probability_milli
            and self.normal_attack_random_scale_milli
            and self.normal_attack_random_scale_rng_stream
            and self.normal_attack_random_scale_source_semantic_id
        ):
            raise ValueError(
                "random normal attack scale requires probability, scale, RNG stream, and source semantics"
            )
        aftershock_proc_shape = bool(
            self.normal_attack_aftershock_proc_probability_milli
            or self.normal_attack_aftershock_proc_scale_milli
            or self.normal_attack_aftershock_proc_radius_milli_tiles
            or self.normal_attack_aftershock_proc_stun_ticks
            or self.normal_attack_aftershock_proc_rng_stream
            or self.normal_attack_aftershock_proc_source_semantic_id
        )
        if aftershock_proc_shape and not (
            self.normal_attack_aftershock_count
            and self.normal_attack_aftershock_proc_probability_milli
            and self.normal_attack_aftershock_proc_scale_milli
            and self.normal_attack_aftershock_proc_radius_milli_tiles
            and self.normal_attack_aftershock_proc_rng_stream
            and self.normal_attack_aftershock_proc_source_semantic_id
        ):
            raise ValueError(
                "random aftershock proc requires an aftershock, probability, damage scale, radius, RNG stream, and source semantics"
            )
        periodic_payload = (
            self.periodic_self_attack_per_stack_milli
            or self.periodic_self_defense_per_stack_milli
        )
        periodic_shape = self.periodic_self_stat_interval_ticks and self.periodic_self_stat_max_stacks
        if bool(periodic_payload) != bool(periodic_shape):
            raise ValueError("periodic self stat stacks require both timing and a stat payload")
        if bool(periodic_payload) != bool(self.periodic_self_stat_source_semantic_id):
            raise ValueError("periodic self stat stacks require a source semantic id")
        return self
