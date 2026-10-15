# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""Folding run-layer and stage-rune modifiers into compiled definitions.

Moved verbatim out of ``stage_compiler.py`` so that file fits the 2000-line
budget; the compiler imports these back, so behaviour is unchanged.

Three folds, all reporting rather than dropping what they cannot apply:
relics onto the operator roster, a `char`-scoped stage rune onto one device's
summon contract, and rune/relic modifiers onto an enemy's *source* attributes.
"""

from __future__ import annotations

import math

from typing import Any

from .entity_definitions import SummonDefinition
from .model_primitives import SkillSpRecoveryMode


RUNE_ATTRIBUTE_TO_SOURCE = {
    "max_hp": "maxHp",
    "atk": "atk",
    "def": "def",
    "magic_resistance": "magicResistance",
    "move_speed": "moveSpeed",
    "attack_speed": "attackSpeed",
}


RELIC_ATTRIBUTE_TO_OPERATOR_FIELD = {
    "atk": "attack",
    "def": "defense",
    "max_hp": "max_hp",
}


RUNE_ATTRIBUTE_TO_SUMMON_FIELD = {
    "max_hp": "max_hp",
    "atk": "attack",
    "def": "defense",
}


def _apply_relics_to_operators(
    operators: Any,
    modifiers: Any,
) -> tuple[tuple[Any, ...], list[str]]:
    """Fold operator-side relic modifiers into the roster.

    Applied to the finished definitions rather than to source attributes:
    operators arrive already built, so there is no shipped attribute block to
    patch underneath them the way there is for enemies.
    """

    if modifiers is None or not operators:
        return tuple(operators), []
    notes: list[str] = []
    for name in sorted(modifiers.attributes()):
        if name not in RELIC_ATTRIBUTE_TO_OPERATOR_FIELD:
            notes.append(f"{name}: no operator field carries it")
    adjusted: list[Any] = []
    for operator in operators:
        base = {
            source: float(getattr(operator, field))
            for source, field in RELIC_ATTRIBUTE_TO_OPERATOR_FIELD.items()
        }
        result = modifiers.apply(base)
        update = {}
        for source, field in RELIC_ATTRIBUTE_TO_OPERATOR_FIELD.items():
            after = int(result[source])
            if after != int(base[source]):
                update[field] = after
                notes.append(
                    f"{operator.operator_id}.{field}: "
                    f"{int(base[source])} -> {after}"
                )
        adjusted.append(operator.model_copy(update=update) if update else operator)
    return tuple(adjusted), notes


def _apply_runes_to_summon(
    summon: SummonDefinition, modifiers: Any
) -> tuple[SummonDefinition, list[str]]:
    """Fold a `char`-scoped rune into one device's stats.

    Reported, never silently dropped: an attribute with no field on the summon
    contract is a visible gap, the same rule the relic folding already uses.
    """

    notes: list[str] = []
    base = {
        source: float(getattr(summon, field))
        for source, field in RUNE_ATTRIBUTE_TO_SUMMON_FIELD.items()
    }
    for name in sorted(modifiers.attributes()):
        if name not in RUNE_ATTRIBUTE_TO_SUMMON_FIELD:
            notes.append(f"{summon.summon_id}: rune attribute {name} has no summon field")
    result = modifiers.apply(base)
    update = {}
    for source, field in RUNE_ATTRIBUTE_TO_SUMMON_FIELD.items():
        after = max(1 if field == "max_hp" else 0, int(result[source]))
        if after != int(base[source]):
            update[field] = after
            notes.append(
                f"{summon.summon_id}.{field}: {int(base[source])} -> {after}"
            )
    return (summon.model_copy(update=update) if update else summon), notes


#: Rune attributes the *caller* applies, so this folder must not report them as
#: having nowhere to land.
APPLIED_ELSEWHERE = frozenset({"rangeRadius"})


def _apply_rune_modifiers(
    attributes: dict[str, Any],
    modifiers: Any,
) -> tuple[dict[str, Any], list[str]]:
    """Fold stage rune modifiers into the shipped attribute values.

    Runes are static per node variant, so applying them while compiling is
    exact -- there is nothing for the engine to evaluate at runtime.  They are
    applied to the *source* attributes rather than to the finished
    EnemyDefinition so that everything derived from them (attack interval from
    baseAttackTime/attackSpeed, milli-tiles per tick from moveSpeed) follows
    automatically instead of being patched afterwards.
    """

    if modifiers is None:
        return attributes, []
    touched = attributes
    notes: list[str] = []
    for rune_name in sorted(modifiers.attributes()):
        if rune_name in APPLIED_ELSEWHERE:
            # Not a gap: `rangeRadius` is a top-level field rather than a member
            # of the attribute block, and it is a real fraction that must not be
            # floored, so the compiler applies it where it reads that field.
            # Reporting it here as unattached said the opposite of the truth.
            continue
        source_name = RUNE_ATTRIBUTE_TO_SOURCE.get(rune_name)
        if source_name is None:
            notes.append(f"{rune_name}: no shipped attribute carries it")
            continue
        current = touched.get(source_name)
        if not isinstance(current, (int, float)):
            notes.append(f"{rune_name}: enemy has no {source_name} to modify")
            continue
        # Key the probe by the *rune* attribute name: ModifierSet.apply skips
        # attributes it does not recognise, so probing under the shipped name
        # (maxHp vs max_hp) silently applied nothing.
        single = modifiers.apply({rune_name: float(current)})
        updated = single[rune_name]
        if updated != current:
            if touched is attributes:
                touched = dict(attributes)
            touched[source_name] = updated
            notes.append(f"{rune_name}: {source_name} {current} -> {updated}")
    return touched, notes

def _apply_skill_cooldown_scale(
    operators: Any,
    scale: float | None,
) -> tuple[tuple[Any, ...], list[str]]:
    """Stretch how long our own skills take to come back.

    `char_skill_cd_mul` ships once in IS3 -- 2-6's emergency node, `scale` 1.3 --
    with no `char`, so it is the squad-wide reading.

    **Which knob 「冷却」 turns is genuinely unstated**, and the two candidates
    are not the same thing in this engine.  A skill becomes ready either after
    `cooldown_ticks` or by filling `sp_cost` at `sp_per_tick`, so 1.3x longer
    means scaling whichever of those the skill actually uses.  Both are scaled
    here: `cooldown_ticks` when it is nonzero, and `sp_cost` for a skill that
    recovers by time -- where readiness is `sp_cost / sp_per_tick`, so scaling
    the cost scales the wait exactly.

    Rounding is **up**, which is the direction the authored ledger row already
    chose: the emergency node exists to be harder, and rounding down would let
    a 1.3x penalty vanish on a short cooldown.

    Skills that recover on an event rather than on time keep their cost: for
    those the wait is however long the event takes, and scaling the cost would
    silently change how many events are needed instead of how long they take.
    """

    if not scale or scale == 1.0 or not operators:
        return tuple(operators), []
    notes: list[str] = []
    adjusted: list[Any] = []
    for operator in operators:
        skills = getattr(operator, "skills", ()) or ()
        if not skills:
            adjusted.append(operator)
            continue
        rebuilt = []
        changed = False
        for skill in skills:
            update: dict[str, int] = {}
            if skill.cooldown_ticks:
                after = math.ceil(skill.cooldown_ticks * scale)
                if after != skill.cooldown_ticks:
                    update["cooldown_ticks"] = after
            if skill.sp_recovery_mode is SkillSpRecoveryMode.TIME and skill.sp_per_tick:
                after = math.ceil(skill.sp_cost * scale)
                if after != skill.sp_cost:
                    update["sp_cost"] = after
            if update:
                changed = True
                rebuilt.append(skill.model_copy(update=update))
                notes.append(
                    "%s.%s: %s (x%g)"
                    % (
                        operator.operator_id,
                        skill.skill_id,
                        ", ".join(
                            "%s %d -> %d" % (field, getattr(skill, field), value)
                            for field, value in sorted(update.items())
                        ),
                        scale,
                    )
                )
            else:
                rebuilt.append(skill)
        adjusted.append(
            operator.model_copy(update={"skills": tuple(rebuilt)})
            if changed
            else operator
        )
    return tuple(adjusted), notes

#: Which shipped **skill blackboard** key lands on which `SummonDefinition`
#: field, matched case-insensitively.
#:
#: `char_skill_blackb_mul` names its targets by blackboard key, and the two
#: spellings in this data disagree: the rune says `Polluted_damage_low` while
#: 汲营枯枝's own skill says `polluted_damage_low`.  Matching by exact string
#: would have applied nothing while reporting success -- the fourth
#: capitalisation trap in this topic.
SKILL_BLACKBOARD_KEY_TO_SUMMON_FIELD = {
    "polluted_damage_low": "pollution_aura_low_damage",
    "polluted_damage_high": "pollution_aura_high_damage",
}


def _apply_skill_blackboard_multipliers(
    summon: Any,
    factors: Any,
) -> tuple[Any, list[str]]:
    """Multiply a device's compiled skill figures by a rune's named factors.

    Only keys this engine actually landed somewhere can be multiplied; a factor
    naming a key with no compiled home is **reported**, because "the rune
    applied" and "the rune did anything" are different claims and only the
    report can tell them apart.  2-6's `cost` x3 for 指挥终端's 「启动！」 is
    exactly that case: the skill itself is unimplemented, so the factor has
    nowhere to go and says so.
    """

    if not factors:
        return summon, []
    notes: list[str] = []
    update: dict[str, int] = {}
    for key, factor in sorted(factors.items()):
        field_name = SKILL_BLACKBOARD_KEY_TO_SUMMON_FIELD.get(str(key).lower())
        if field_name is None:
            notes.append(
                "%s: %s x%g has no compiled home on this device"
                % (summon.summon_id, key, factor)
            )
            continue
        before = getattr(summon, field_name, 0)
        if not before:
            notes.append(
                "%s: %s x%g -- device carries no such figure"
                % (summon.summon_id, key, factor)
            )
            continue
        after = int(round(before * factor))
        if after != before:
            update[field_name] = after
            notes.append(
                "%s.%s: %d -> %d (x%g)"
                % (summon.summon_id, field_name, before, after, factor)
            )
    if not update:
        return summon, notes
    return summon.model_copy(update=update), notes

