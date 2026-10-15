# [OMNI] origin=claude domain=game_observatory/arknights type=is3-run-layer status=active
"""Compose the IS3 attribute modifiers that sit between a table value and battle.

Answers the S3 question directly: *entering the roguelike, what actually gets
multiplied and added to operator and enemy stats?*  Two independent layers feed
a battle and neither reaches the engine today:

1. **Stage runes** -- the emergency-operation buff.  `enemy_attribute_mul` sits
   on 36/36 IS3 combat stages with `difficultyMask=2`, and mask 2 was pinned to
   the raid (emergency) node variant by data: `level_hidden_group_enable`
   enables group ``n``/``normal`` under mask 1 and ``h``/``raid`` under mask 2,
   9 and 13 occurrences with no counterexample.
2. **Run relics** -- 380 IS3 relics carrying 77 buff keys, of which 20 touch
   combat attributes (`char_attribute_mul` alone is on 66 relics).

Ordering is the one genuinely undetermined part, so it is a named, overridable
policy rather than a buried constant: see :class:`CompositionPolicy`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

#: Rune keys that scale or shift enemy stats, and the side they apply to.
ENEMY_RUNE_KEYS = {"enemy_attribute_mul": "multiply", "enemy_attribute_add": "add"}

#: Runes whose blackboard carries a bare ``scale`` instead of named attributes,
#: because the attribute is fixed by the rune's own key.
#:
#: ``enemy_attackradius_mul`` ships twice in IS3, both on the emergency node:
#: 4-3 with ``scale`` 1.5 and no scope (every enemy on the map), and 6-2 with
#: ``scale`` 2.0 scoped to ``enemy_1068_snmage_2|enemy_1068_snmage`` -- 雪怪法师
#: and its elite.  Doubling the reach of the two casters is a different stage
#: from doubling everyone's, so the scope has to be read.
#:
#: The attribute is the enemy's top-level ``rangeRadius``, not a member of its
#: ``attributes`` block, and it is a genuine fraction (2.2 tiles), so it is
#: applied where the compiler reads that field and **not** floored.
SCALED_ENEMY_RUNE_ATTRIBUTES = {"enemy_attackradius_mul": "rangeRadius"}
OPERATOR_RUNE_KEYS = {"char_attribute_mul": "multiply", "char_attribute_add": "add"}

#: Relic buff keys that reach combat attributes, per the IS3 run-layer audit.
RELIC_OPERATOR_MULTIPLY = frozenset(
    {"char_attribute_mul", "char_squad_attribute_mul", "char_squad_attribute_mul_ex",
     "layer_char_attribute_mul"}
)
RELIC_OPERATOR_ADD = frozenset(
    {"char_attribute_add", "char_squad_attribute_add", "layer_char_attribute_add"}
)
RELIC_ENEMY_ADD = frozenset({"enemy_attribute_add"})

#: Blackboard keys inside a buff that are selectors/metadata, not attributes.
NON_ATTRIBUTE_KEYS = frozenset(
    {"char", "enemy", "key", "value", "skill", "tag", "cnt", "id", "count", "prob",
     "max_stack_cnt", "duration", "interval"}
)


@dataclass(frozen=True)
class CompositionPolicy:
    """How the layers combine.  Every field here is a registered open question.

    The defaults are the ones recorded in the IS3 mechanism ledger; they are
    stated so a battle can run, not because they are verified.
    """

    #: Table value -> rune multiply -> run-layer add.  The alternative (add
    #: before multiply) inflates the additive term by the rune factor.
    order: Literal["multiply_then_add", "add_then_multiply"] = "multiply_then_add"

    #: Several relics can multiply the same attribute.  Summing the bonuses and
    #: multiplying once is the conventional reading; compounding them would grow
    #: super-linearly with relic count.
    stacking: Literal["sum_then_multiply", "compound"] = "sum_then_multiply"

    #: Fractional results are truncated toward zero when the attribute is an
    #: integer quantity (hp, atk, def).
    rounding: Literal["floor", "round", "none"] = "floor"


@dataclass
class ModifierSet:
    """Accumulated multiplicative and additive terms per attribute name."""

    multipliers: dict[str, list[float]] = field(default_factory=dict)
    addends: dict[str, list[float]] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    def multiply(self, attribute: str, value: float, source: str) -> None:
        self.multipliers.setdefault(attribute, []).append(float(value))
        self.sources.append(f"{source}:{attribute}*{value}")

    def add(self, attribute: str, value: float, source: str) -> None:
        self.addends.setdefault(attribute, []).append(float(value))
        self.sources.append(f"{source}:{attribute}+{value}")

    def attributes(self) -> set[str]:
        return set(self.multipliers) | set(self.addends)

    def apply(
        self,
        base: Mapping[str, float],
        policy: CompositionPolicy | None = None,
    ) -> dict[str, float]:
        """Apply the accumulated terms to a base stat block."""

        policy = policy or CompositionPolicy()
        out: dict[str, float] = dict(base)
        for attribute in sorted(self.attributes()):
            if attribute not in out:
                # Never invent a stat the base block does not have.
                continue
            value = float(out[attribute])
            factors = self.multipliers.get(attribute) or []
            if factors:
                if policy.stacking == "sum_then_multiply":
                    # Each factor is expressed as an absolute multiplier (1.2),
                    # so its bonus part is (f - 1).
                    bonus = sum(f - 1.0 for f in factors)
                    value *= 1.0 + bonus
                else:
                    for factor in factors:
                        value *= factor
            addend = sum(self.addends.get(attribute) or [])
            if policy.order == "multiply_then_add":
                value += addend
            else:
                value = (float(out[attribute]) + addend)
                if factors:
                    bonus = sum(f - 1.0 for f in factors)
                    value *= 1.0 + bonus
            if policy.rounding == "floor":
                value = float(int(value))
            elif policy.rounding == "round":
                value = float(round(value))
            out[attribute] = value
        return out


def _blackboard(entries: Iterable[Mapping[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in entries or []:
        raw = item.get("valueStr")
        out[item["key"]] = item.get("value") if raw is None else raw
    return out


@dataclass
class StageRunes:
    """Rune modifiers for one node variant, with enemy scoping preserved."""

    enemies_global: ModifierSet = field(default_factory=ModifierSet)
    enemies_by_id: dict[str, ModifierSet] = field(default_factory=dict)
    operators: ModifierSet = field(default_factory=ModifierSet)
    #: Operator-side runes scoped to one named character by the shipped
    #: `char` blackboard entry.  Every IS3 occurrence names a *device*
    #: (trap_008_farm, trap_086_larva, trap_096_gflag, trap_032_mound),
    #: so folding these into `operators` would buff the player's squad.
    chars_by_id: dict[str, ModifierSet] = field(default_factory=dict)
    #: Predefined-token aliases a `level_predefines_enable` rune switches on.
    #: This is the **second** way a token enters play: most are named by a wave
    #: action (actionType 6), but two IS3 stages enable one by rune instead --
    #: 3-2 `trap_011_ore#5` and 4-2 `trap_075_bgarmn#3`, both raid-only.  Both
    #: are exactly the aliases the compiler reported as having no activation.
    enabled_predefines: set[str] = field(default_factory=set)
    #: `enemy_talent_blackb_add`: additions to a named enemy's **talent
    #: blackboard**, keyed by enemy id then by blackboard key.  IS3 ships one
    #: row -- 3-7 raid, `enemy_1352_eslime` with `Boom.add_count` +3 -- which
    #: turns the slime's 1 point of energy on death into 4 on the stage that
    #: also holds the energy charger and the energy thief.  Not an attribute
    #: modifier: it changes a mechanism's own figure, so it is applied where the
    #: coefficients are read rather than to the stat block.
    enemy_talent_addends: dict[str, dict[str, float]] = field(default_factory=dict)
    #: `enemy_talent_blackb_mul`: the same idea as the addends, multiplied, and
    #: the one shipped row carries **no scope** -- 5-2 emergency doubles
    #: `PollutedArea.Polluted_damage_low` and `..._high`, which is 食腐使徒's
    #: 「释放污染秽蚀，使周围我方持续损失生命」.  Unscoped is not a gap here the way
    #: it would be for the addends: a *multiplier* on a named mechanism key can
    #: only reach enemies that carry that key, so it means every carrier on the
    #: stage rather than every enemy.
    enemy_talent_multipliers: dict[str, float] = field(default_factory=dict)
    enemy_talent_multipliers_by_enemy: dict[str, dict[str, float]] = field(
        default_factory=dict
    )
    #: `char_skill_cd_mul`: how much longer our own skills take to come back.
    #: IS3 ships one row -- 2-6 raid, `scale` 1.3 -- and it carries no `char`,
    #: so it is the squad-wide reading rather than a named device.
    operator_skill_cooldown_scale: float | None = None
    #: `char_skill_blackb_mul`: multiply a friendly's **skill blackboard** entry
    #: by name, keyed by the shipped (case-preserved) key.  Two rows in IS3,
    #: both emergency-only:
    #:   2-6  `char = trap_008_farm`, `cost` x3   -- 指挥终端's 「启动！」
    #:   5-2  no scope, `Polluted_damage_low/high` x2 -- 汲营枯枝's 污染秽蚀
    #: The unscoped row is the squad-wide reading, which in IS3 means whichever
    #: placed device carries the named key.
    char_skill_blackboard_multipliers: dict[str, float] = field(default_factory=dict)
    char_skill_blackboard_by_char: dict[str, dict[str, float]] = field(
        default_factory=dict
    )
    #: `enemy_dynamic_ability_new`: an ability the **stage** gives a named
    #: enemy, keyed by enemy id.  All three IS3 rows grant the same one --
    #: `invisible` -- to 4-8's 散华骑士团精锐 (raid only) and to ev-4 / ev-9's
    #: 弩手 (every variant).  So the emergency form of 4-8 fields a knight the
    #: ordinary form does not: same enemy, hidden.
    #:
    #: Two of the three name an enemy the stage's own `enemyDbRefs` never
    #: mentions (`enemy_1003_ncbow` in ev-4 and ev-9), which is a rune aimed at
    #: something that is not there -- recorded rather than dropped, because a
    #: rune with no target is a fact about the stage.
    #: `enemy_skill_blackb_mul`: multiply one named enemy's named **skill**
    #: blackboard entry.  IS3 ships two rows, both on 5-6's emergency node:
    #:
    #:     enemy_1299_ymkilr_2  skill InvisibleCombat  atk_scale x2
    #:     enemy_1300_ymmir_2   skill Guide            duration x1.5, attack_speed x1.5
    #:
    #: So the emergency 山海众秘使 hits for **four** times its attack out of
    #: stealth rather than twice -- the rune multiplies the skill's own figure,
    #: not the carrier's stats, which is why it cannot ride `enemies_by_id`.
    #: Keyed enemy id -> skill prefab -> blackboard key -> factor.
    enemy_skill_blackboard_multipliers: dict[str, dict[str, dict[str, float]]] = field(
        default_factory=dict
    )
    enemy_granted_abilities: dict[str, set[str]] = field(default_factory=dict)
    applied: list[str] = field(default_factory=list)
    unapplied: list[tuple[str, str]] = field(default_factory=list)

    def for_char(self, character_key: str) -> ModifierSet | None:
        """The rune set scoped to this character, or None."""

        return self.chars_by_id.get(character_key)

    def for_enemy(self, enemy_id: str) -> ModifierSet:
        """Combine the stage-wide set with any set scoped to this enemy."""

        scoped = self.enemies_by_id.get(enemy_id)
        if scoped is None:
            return self.enemies_global
        combined = ModifierSet()
        for source in (self.enemies_global, scoped):
            for attribute, factors in source.multipliers.items():
                combined.multipliers.setdefault(attribute, []).extend(factors)
            for attribute, addends in source.addends.items():
                combined.addends.setdefault(attribute, []).extend(addends)
            combined.sources.extend(source.sources)
        return combined


def stage_rune_modifiers(
    stage: Mapping[str, Any],
    *,
    node_variant: Literal["normal", "raid"] = "normal",
) -> StageRunes:
    """Collect rune modifiers for one node variant.

    A rune applies when its ``difficultyMask`` includes the variant's bit: bit 0
    (value 1) is the ordinary node, bit 1 (value 2) the raid (emergency) node.
    Mask 15 means both.  That mapping is pinned by data, not assumed:
    ``level_hidden_group_enable`` enables group ``n``/``normal`` under mask 1 and
    ``h``/``raid`` under mask 2, 9 and 13 occurrences with no counterexample.

    A rune whose blackboard names an ``enemy`` applies **only** to that enemy id
    (2 such runes exist in IS3); treating those as stage-wide would buff every
    enemy on the map.
    """

    wanted_bit = 1 if node_variant == "normal" else 2
    result = StageRunes()
    for rune in stage.get("runes") or []:
        key = rune.get("key")
        mask = int(rune.get("difficultyMask") or 0)
        if not mask & wanted_bit:
            continue
        blackboard = _blackboard(rune.get("blackboard"))
        if key == "char_skill_blackb_mul":
            scope = blackboard.get("char")
            targets = (
                result.char_skill_blackboard_by_char.setdefault(str(scope), {})
                if isinstance(scope, str) and scope
                else result.char_skill_blackboard_multipliers
            )
            touched_skill = False
            for entry, value in blackboard.items():
                if entry in NON_ATTRIBUTE_KEYS or not isinstance(value, (int, float)):
                    continue
                if value <= 0:
                    continue
                targets[entry] = targets.get(entry, 1.0) * float(value)
                touched_skill = True
            if touched_skill:
                result.applied.append(str(key))
            else:
                result.unapplied.append((str(key), "no numeric factor in blackboard"))
            continue
        if key == "char_skill_cd_mul":
            scale = blackboard.get("scale")
            if not isinstance(scale, (int, float)) or scale <= 0:
                result.unapplied.append((str(key), "no positive scale in blackboard"))
                continue
            # Two rows would have to compose, and IS3 ships one; multiplying
            # them together is the reading a second row would get, so it is
            # written that way rather than overwriting.
            result.operator_skill_cooldown_scale = (
                result.operator_skill_cooldown_scale or 1.0
            ) * float(scale)
            result.applied.append(str(key))
            continue
        if key == "enemy_skill_blackb_mul":
            scope = blackboard.get("enemy")
            skill = blackboard.get("skill")
            if not isinstance(scope, str) or not scope:
                result.unapplied.append((str(key), "no enemy scope in blackboard"))
                continue
            if not isinstance(skill, str) or not skill:
                result.unapplied.append((str(key), "no skill named in blackboard"))
                continue
            factors = result.enemy_skill_blackboard_multipliers.setdefault(
                scope, {}
            ).setdefault(skill, {})
            touched_skill_blackboard = False
            for entry, value in blackboard.items():
                # Only the two scope entries are excluded here.  This rune
                # multiplies a *skill's* blackboard, where `duration` and
                # `interval` are ordinary figures rather than the attribute-set
                # bookkeeping `NON_ATTRIBUTE_KEYS` filters out -- and 山海众's
                # `Guide` row is exactly 「duration x1.5, attack_speed x1.5」.
                if entry in {"enemy", "skill"} or not isinstance(
                    value, (int, float)
                ):
                    continue
                if value <= 0:
                    continue
                factors[entry] = factors.get(entry, 1.0) * float(value)
                touched_skill_blackboard = True
            if touched_skill_blackboard:
                result.applied.append(str(key))
            else:
                result.unapplied.append((str(key), "no numeric factor in blackboard"))
            continue
        if key == "enemy_dynamic_ability_new":
            scope = blackboard.get("enemy")
            ability = blackboard.get("key")
            if not isinstance(scope, str) or not scope:
                result.unapplied.append((str(key), "no enemy scope in blackboard"))
                continue
            if not isinstance(ability, str) or not ability:
                result.unapplied.append((str(key), "no ability named in blackboard"))
                continue
            for single in scope.split("|"):
                if single:
                    result.enemy_granted_abilities.setdefault(single, set()).add(
                        ability
                    )
            result.applied.append(str(key))
            continue
        if key == "enemy_talent_blackb_mul":
            scope = blackboard.get("enemy")
            targets = (
                [single for single in scope.split("|") if single]
                if isinstance(scope, str) and scope
                else []
            )
            touched_multiplier = False
            for entry, value in blackboard.items():
                if entry in NON_ATTRIBUTE_KEYS or not isinstance(value, (int, float)):
                    continue
                if value <= 0:
                    # A multiplier of zero would silently delete the mechanism;
                    # the shipped row is 2.0, so anything else is reported.
                    continue
                if targets:
                    for single in targets:
                        by_enemy = result.enemy_talent_multipliers_by_enemy.setdefault(
                            single, {}
                        )
                        by_enemy[entry] = by_enemy.get(entry, 1.0) * float(value)
                else:
                    result.enemy_talent_multipliers[entry] = (
                        result.enemy_talent_multipliers.get(entry, 1.0) * float(value)
                    )
                touched_multiplier = True
            if touched_multiplier:
                result.applied.append(str(key))
            else:
                result.unapplied.append(
                    (str(key), "no positive multiplier in blackboard")
                )
            continue
        if key == "enemy_talent_blackb_add":
            scope = blackboard.get("enemy")
            if not isinstance(scope, str) or not scope:
                # Without a scope this would add the figure to every enemy that
                # happens to carry the key; the one shipped row names its
                # target, so an unscoped one is reported rather than guessed.
                result.unapplied.append((str(key), "no enemy scope in blackboard"))
                continue
            touched_talent = False
            for single in scope.split("|"):
                if not single:
                    continue
                target_keys = result.enemy_talent_addends.setdefault(single, {})
                for entry, value in blackboard.items():
                    if entry in NON_ATTRIBUTE_KEYS or not isinstance(
                        value, (int, float)
                    ):
                        continue
                    target_keys[entry] = target_keys.get(entry, 0.0) + float(value)
                    touched_talent = True
            if touched_talent:
                result.applied.append(str(key))
            else:
                result.unapplied.append((str(key), "no numeric addend in blackboard"))
            continue
        if key in SCALED_ENEMY_RUNE_ATTRIBUTES:
            attribute = SCALED_ENEMY_RUNE_ATTRIBUTES[key]
            scale = blackboard.get("scale")
            if not isinstance(scale, (int, float)) or scale <= 0:
                result.unapplied.append((str(key), "no positive scale in blackboard"))
                continue
            scope = blackboard.get("enemy")
            # The scope can name **several** ids, joined by `|`.  6-2 is the one
            # rune in IS3 that does, and reading it as a single id would key the
            # modifier by the joined string and match neither caster.
            targets = (
                [
                    result.enemies_by_id.setdefault(single, ModifierSet())
                    for single in str(scope).split("|")
                    if single
                ]
                if isinstance(scope, str) and scope
                else [result.enemies_global]
            )
            for target in targets:
                target.multiply(attribute, scale, f"rune:{key}")
            result.applied.append(str(key))
            continue
        if key in ENEMY_RUNE_KEYS:
            mode = ENEMY_RUNE_KEYS[key]
            scope = blackboard.get("enemy")
            if isinstance(scope, str) and scope:
                target = result.enemies_by_id.setdefault(scope, ModifierSet())
            else:
                target = result.enemies_global
        elif key == "level_predefines_enable":
            # The blackboard is a map of alias -> 1.0, not attribute values.
            for alias, value in blackboard.items():
                if isinstance(value, (int, float)) and value:
                    result.enabled_predefines.add(str(alias))
            result.applied.append(str(key))
            continue
        elif key in OPERATOR_RUNE_KEYS:
            mode = OPERATOR_RUNE_KEYS[key]
            scope = blackboard.get("char")
            if isinstance(scope, str) and scope:
                # Scoped to one named character, exactly as the enemy
                # keys scope to one enemy id.  Treating it as stage-wide
                # would buff every deployed operator.
                target = result.chars_by_id.setdefault(scope, ModifierSet())
            else:
                target = result.operators
        else:
            result.unapplied.append((str(key), "no attribute-modifier semantics"))
            continue

        touched = False
        for attribute, value in blackboard.items():
            if attribute in NON_ATTRIBUTE_KEYS or not isinstance(value, (int, float)):
                continue
            if mode == "multiply":
                target.multiply(attribute, value, f"rune:{key}")
            else:
                target.add(attribute, value, f"rune:{key}")
            touched = True
        if touched:
            result.applied.append(str(key))
        else:
            result.unapplied.append((str(key), "no numeric attribute in blackboard"))
    return result


def relic_modifiers(
    relic_ids: Iterable[str],
    relics: Mapping[str, Mapping[str, Any]],
) -> tuple[ModifierSet, ModifierSet]:
    """Return ``(enemy_modifiers, operator_modifiers)`` for a held relic set."""

    enemies = ModifierSet()
    operators = ModifierSet()
    for relic_id in relic_ids:
        record = relics.get(relic_id)
        if record is None:
            raise KeyError(f"unknown IS3 relic: {relic_id!r}")
        for buff in record.get("buffs") or []:
            key = buff.get("key")
            blackboard = buff.get("blackboard") or {}
            for attribute, value in blackboard.items():
                if attribute in NON_ATTRIBUTE_KEYS or not isinstance(value, (int, float)):
                    continue
                if key in RELIC_OPERATOR_MULTIPLY:
                    operators.multiply(attribute, 1.0 + float(value), f"relic:{relic_id}:{key}")
                elif key in RELIC_OPERATOR_ADD:
                    operators.add(attribute, value, f"relic:{relic_id}:{key}")
                elif key in RELIC_ENEMY_ADD:
                    enemies.add(attribute, value, f"relic:{relic_id}:{key}")
    return enemies, operators


def load_relics(inventory_path: str | Path) -> dict[str, dict[str, Any]]:
    """Load the relic table from IS3-ROGUE-BUFF-INVENTORY.json."""

    payload = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
    return payload["relics"]
