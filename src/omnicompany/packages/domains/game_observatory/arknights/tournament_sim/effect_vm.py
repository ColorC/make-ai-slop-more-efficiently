# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-10T02:35:03+08:00 type=declarative-effect-vm status=active
"""Finite declarative effect evaluator for the tournament simulator.

This module intentionally has no script loader, expression language, dynamic
imports, ``eval`` or callbacks supplied by content.  A bundle can only select
one of the Pydantic instruction variants declared in :mod:`models`.
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import (
    ActiveEffectState,
    AttackScaleEffectDefinition,
    ContentBundle,
    ControlEffect,
    DamageEffectDefinition,
    DeploymentPointsEffectDefinition,
    EffectDefinition,
    EffectStat,
    EnemyTimedSelfModifierDefinition,
    HealEffectDefinition,
    RandomDamageEffectDefinition,
    SpecialTileControlEffectDefinition,
    SpecialTileEffectDefinition,
    SpecialTileStatModifierDefinition,
    SpMutationEffectDefinition,
    StatModifierEffectDefinition,
    SummonEffectDefinition,
    enemy_timed_modifier_effect_id,
)


class EffectVMError(ValueError):
    """Raised when a state asks the VM to evaluate an undeclared effect."""


_STAT_MINIMUMS: dict[EffectStat, int] = {
    EffectStat.ATTACK: 0,
    EffectStat.DEFENSE: 0,
    EffectStat.MAGIC_RESISTANCE: 0,
    EffectStat.HEALING_POWER: 0,
    EffectStat.ATTACK_INTERVAL_TICKS: 1,
    EffectStat.ATTACK_RANGE_TILES: 0,
    EffectStat.BLOCK_COUNT: 0,
    EffectStat.MOVE_MILLI_TILES_PER_TICK: 1,
}

_STAT_MAXIMUMS: dict[EffectStat, int] = {
    EffectStat.MAGIC_RESISTANCE: 100,
}

VMDefinition = EffectDefinition | SpecialTileEffectDefinition | EnemyTimedSelfModifierDefinition


class EffectVM:
    """Compile and evaluate the closed instruction set in a content bundle."""

    def __init__(self, content: ContentBundle) -> None:
        definitions: dict[str, VMDefinition] = {}
        for owner in (*content.operators, *content.summons):
            for skill in owner.skills:
                for effect in skill.effects:
                    if not isinstance(
                        effect,
                        (
                            HealEffectDefinition,
                            AttackScaleEffectDefinition,
                            DamageEffectDefinition,
                            DeploymentPointsEffectDefinition,
                            RandomDamageEffectDefinition,
                            StatModifierEffectDefinition,
                            SpMutationEffectDefinition,
                            SummonEffectDefinition,
                        ),
                    ):
                        raise EffectVMError(
                            f"unsupported effect instruction: {type(effect).__name__}"
                        )
                    definitions[effect.effect_id] = effect
        for operator in content.operators:
            for effect in operator.normal_attack_effects:
                definitions[effect.effect_id] = effect
        for tile in content.special_tiles:
            for effect in tile.effects:
                if not isinstance(
                    effect,
                    (
                        SpecialTileStatModifierDefinition,
                        SpecialTileControlEffectDefinition,
                    ),
                ):  # pragma: no cover - Pydantic's finite union rejects this first
                    raise EffectVMError(
                        f"unsupported special tile effect instruction: {type(effect).__name__}"
                    )
                definitions[effect.effect_id] = effect
        for enemy in content.enemies:
            for modifier in enemy.initial_timed_modifiers:
                definitions[
                    enemy_timed_modifier_effect_id(enemy.enemy_id, modifier.modifier_id)
                ] = modifier
        self._definitions = definitions

    def definition(self, effect_id: str) -> VMDefinition:
        try:
            return self._definitions[effect_id]
        except KeyError as exc:
            raise EffectVMError(f"unknown effect definition: {effect_id}") from exc

    def effective_stat(
        self,
        *,
        base: int,
        stat: EffectStat,
        target_instance_id: str,
        active_effects: Iterable[ActiveEffectState],
    ) -> int:
        """Apply matching modifiers in stable effect-instance order.

        Each instruction applies ``(value + additive) * multiplier / 1000``
        using integer floor division, then clamps to the stat's documented
        minimum.  This sequential rule makes stacking completely deterministic.
        """

        value = base
        matching = sorted(
            (
                state
                for state in active_effects
                if state.target_instance_id == target_instance_id
                and isinstance(
                    self.definition(state.effect_id),
                    (
                        StatModifierEffectDefinition,
                        SpecialTileStatModifierDefinition,
                        EnemyTimedSelfModifierDefinition,
                    ),
                )
                and self.definition(state.effect_id).stat is stat
            ),
            key=lambda state: state.instance_id,
        )
        for state in matching:
            definition = self.definition(state.effect_id)
            if not isinstance(
                definition,
                (
                    StatModifierEffectDefinition,
                    SpecialTileStatModifierDefinition,
                    EnemyTimedSelfModifierDefinition,
                ),
            ):  # pragma: no cover
                raise EffectVMError("effect type changed during deterministic evaluation")
            value = (value + definition.additive) * definition.multiplier_milli // 1000
            value = max(_STAT_MINIMUMS[stat], value)
            if maximum := _STAT_MAXIMUMS.get(stat):
                value = min(maximum, value)
        return value

    def has_control(
        self,
        *,
        control: ControlEffect,
        target_instance_id: str,
        active_effects: Iterable[ActiveEffectState],
    ) -> bool:
        """Return whether a live target carries the declared closed control state."""

        return any(
            state.target_instance_id == target_instance_id
            and isinstance(
                definition := self.definition(state.effect_id),
                SpecialTileControlEffectDefinition,
            )
            and definition.control is control
            for state in active_effects
        )


__all__ = ["EffectVM", "EffectVMError"]
