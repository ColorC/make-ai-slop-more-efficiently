# [OMNI] origin=claude domain=game_observatory/arknights type=enemy-mechanism-layer status=active
"""Derive enemy mechanics from shipped enemy data instead of an id allow-list.

This is the S2-b lift.  Until now a mechanism only ran for enemy ids named in a
frozen table in :mod:`enemy_coefficients`, so *the same mechanism carried by a
different enemy did nothing* -- 24 of the 249 IS3 enemy ids had any contract at
all.  Here the coefficients are read off the enemy's own ``talentBlackboard``
and ``skills`` blackboards, with a source pointer, for whichever enemy carries
them.

Two things this module deliberately does **not** do:

* It does not invent a mechanism.  Only keys the engine already consumes
  (:data:`enemy_coefficients.RUNTIME_COEFFICIENT_KEYS`) become coefficients;
  everything else is returned as :class:`DeclaredMechanism` so the caller can
  report an honest denominator rather than a silent drop.
* It does not decide *semantics*.  A coefficient is a number plus provenance;
  the trigger logic stays in the engine.

Measured scope on IS3 (see ``S2B-MECHANISM-REPORT.json``): the 44 implemented
coefficient keys are carried by only 37 of 249 enemy ids, so data-driving them
is a correctness fix rather than a coverage breakthrough -- the bulk of the gap
is 76 talent groups the engine has no family for at all.
"""

from __future__ import annotations

from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from .enemy_coefficients import (
    INVULNERABILITY_KEYS,
    RUNTIME_COEFFICIENT_KEYS,
    is_runtime_coefficient_key,
)
from .models import EnemyMechanicCoefficient

#: ``minimum_block_count_to_stop`` is cross-checked against a typed field on
#: :class:`EnemyDefinition`, so it is never synthesised from blackboard data.
DERIVABLE_COEFFICIENT_KEYS = frozenset(RUNTIME_COEFFICIENT_KEYS) - {
    "minimum_block_count_to_stop"
}

#: Some families cannot be a fixed set because their keys embed the carrier's
#: name, so membership is a predicate rather than a lookup.
def is_derivable_coefficient_key(key: str) -> bool:
    if key == "minimum_block_count_to_stop":
        return False
    return is_runtime_coefficient_key(key)

#: Fixed-point scale shared with the engine: coefficients are millionths.
MICROS = 1_000_000


class DeclaredMechanism(BaseModel):
    """A mechanism the enemy carries that the engine has no family for."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enemy_id: str = Field(min_length=1)
    group: str = Field(min_length=1)
    keys: tuple[str, ...] = Field(min_length=1)
    origin: str = Field(min_length=1)
    reason: str = Field(min_length=1)


def _group_of(key: str) -> str:
    """Return the mechanism name a blackboard key belongs to.

    Keys look like ``SpeedUp.move_speed``, ``Revive[Trigger].interval`` or
    ``attack@atk_scale``; the mechanism is the part before the first ``.``,
    with any ``[...]`` qualifier stripped.
    """

    head = key.split(".", 1)[0]
    return head.split("[", 1)[0] or key


def _as_micros(value: Any) -> int | None:
    """Convert a blackboard number to exact millionths.

    Returns ``None`` for string-valued blackboard entries (range ids, enemy
    keys and similar), which are references rather than magnitudes and must not
    be coerced into a number.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value) * MICROS)


def _iter_source_keys(data: dict[str, Any]) -> Iterable[tuple[str, Any, str]]:
    """Yield ``(key, value, source_pointer_suffix)`` for one enemy's data."""

    for index, entry in enumerate(data.get("talentBlackboard") or []):
        key = entry.get("key")
        if not key:
            continue
        value = entry.get("valueStr")
        yield key, (entry.get("value") if value is None else value), f"talentBlackboard/{index}"

    for skill_index, skill in enumerate(data.get("skills") or []):
        prefab = skill.get("prefabKey")
        if not prefab:
            continue
        for entry_index, entry in enumerate(skill.get("blackboard") or []):
            inner = entry.get("key")
            if not inner:
                continue
            value = entry.get("valueStr")
            yield (
                f"{prefab}.{inner}",
                (entry.get("value") if value is None else value),
                f"skills/{skill_index}/blackboard/{entry_index}",
            )


#: Two shipped spellings of one mechanism, normalised to the canonical name the
#: engine already reads.
#:
#: 「初始处于休憩状态，受攻击后与周围其他角兽一同进入临战状态」 -- the standby /
#: wake mechanism.  Four enemies ship it lowercase (`revive.hp_ratio`,
#: `revive.move_speed`, `revive.unmove_duration`) and the engine implements
#: those; two more -- `enemy_1303_mhshep_b` and `enemy_1304_mhwolf` -- ship the
#: **same three figures** (0.999 / 2.0 / 150.0) under `Revive.hp_ratio`,
#: `Revive.move_speed` and `Revive.unmovable_duration`, and got nothing at all.
#:
#: Same shape as `StunAttack` / `stunattack` before it: a capitalisation (and,
#: for the third key, an abbreviation) is not a different mechanism.  The
#: source pointer keeps the **shipped** spelling, so provenance still says
#: truthfully what the data contains.
COEFFICIENT_KEY_ALIASES = {
    "Revive.hp_ratio": "revive.hp_ratio",
    "Revive.move_speed": "revive.move_speed",
    "Revive.unmovable_duration": "revive.unmove_duration",
    # 辐能源石虫 `enemy_1352_eslime` (IS3 3-7) ships its death explosion under
    # **capitalised** `Boom.*` while 高能源石虫 ships `boom.*`, so the engine
    # consumed one and dropped the other -- the third capitalisation split in
    # this topic after `StunAttack`/`stunattack` and `Revive`/`revive`.
    #
    # Only the four keys that have a lowercase counterpart are aliased.  The
    # database ships plenty of other `Boom.*` rows (`ep_damage_ratio`,
    # `enable`, `stun`, `atk_value`, `Boom[ddlamb].enemy_key`) that the
    # lowercase family has no member for; folding those in would claim a
    # mechanism the engine does not have.
    "Boom.atk_scale": "boom.atk_scale",
    "Boom.freeze": "boom.freeze",
    "Boom.projectile_range": "boom.projectile_range",
    "Boom.range_radius": "boom.range_radius",
    # 哈兰杜汗，大地之鞭 `enemy_2057_smkght` (IS3 b-7 / b-7-b) ships the stacking
    # attack buff **capitalised** while 「暴食者」 `enemy_2005_axetro` (ev-9)
    # ships it lowercase.  Checked the way the `firstattack`/`FirstAttack`
    # correction says to -- against the 天赋 body, not the 能力 summary -- and
    # the two really are one mechanism:
    #
    #   axetro  「造成伤害时，提高攻击力和攻击速度，4秒不攻击后清空」
    #   smkght  「每次成功造成伤害后，自身攻击力+30%，攻击速度+30，最多叠加10层，
    #             脱离攻击状态4s后重置」
    #
    # Same trigger (dealing damage), same reset (four seconds idle), same four
    # figures.  Only the magnitudes differ: 0.07/5/28 against 0.3/30/10.
    "AttackUp.atk": "atkup.atk",
    "AttackUp.attack_speed": "atkup.attack_speed",
    "AttackUp.max_stack_cnt": "atkup.max_stack_cnt",
    "Checker.delay": "checker.delay",
}


def compile_enemy_mechanics(
    enemy_id: str,
    data: dict[str, Any],
    *,
    interpretations: dict[str, str] | None = None,
) -> tuple[tuple[EnemyMechanicCoefficient, ...], tuple[DeclaredMechanism, ...]]:
    """Split one enemy's blackboards into engine coefficients and the rest.

    ``interpretations`` optionally maps a coefficient key to a one-line meaning
    (the IS3 ledger supplies these); a generic provenance note is used when a
    key has none, because ``EnemyMechanicCoefficient.interpretation`` is
    required and must never be left blank.
    """

    interpretations = interpretations or {}
    coefficients: list[EnemyMechanicCoefficient] = []
    seen: dict[str, int] = {}
    unsupported: dict[str, list[str]] = {}
    unsupported_origin: dict[str, str] = {}

    for shipped_key, value, suffix in _iter_source_keys(data):
        key = COEFFICIENT_KEY_ALIASES.get(shipped_key, shipped_key)
        if is_derivable_coefficient_key(key):
            micros = _as_micros(value)
            if micros is None:
                # A string where the engine expects a magnitude: report rather
                # than coerce.
                bucket = unsupported.setdefault(_group_of(key), [])
                if key not in bucket:
                    bucket.append(key)
                unsupported_origin[_group_of(key)] = suffix
                continue
            if key in seen:
                # The same coefficient can arrive twice from two namespaces: a
                # talent key `AOEAttack.atk_scale` and the skill `AOEAttack`
                # with blackboard `atk_scale` compose to the same name.  That
                # is redundant, not contradictory, so identical values collapse
                # -- but a genuine disagreement must not be silently resolved
                # to whichever came last.
                if seen[key] != micros:
                    raise ValueError(
                        f"enemy {enemy_id!r} declares coefficient {key!r} twice with "
                        f"different values ({seen[key]} vs {micros} millionths)"
                    )
                continue
            seen[key] = micros
            coefficients.append(
                EnemyMechanicCoefficient(
                    key=key,
                    value_micros=micros,
                    # The identity-checked families expect exactly this shape.
                    # The shipped spelling, not the canonical one: provenance
                    # must say what the data actually contains.
                    source_pointer=f"enemy_database.json#/{enemy_id}/{shipped_key}",
                    interpretation=interpretations.get(
                        key,
                        f"source-derived coefficient read from {suffix} of {enemy_id}"
                        + (
                            f" (shipped as {shipped_key})"
                            if shipped_key != key
                            else ""
                        ),
                    ),
                    pending_live_readback=True,
                )
            )
            continue

        group = _group_of(key)
        bucket = unsupported.setdefault(group, [])
        if key not in bucket:
            bucket.append(key)
        unsupported_origin.setdefault(group, suffix)

    declared = tuple(
        DeclaredMechanism(
            enemy_id=enemy_id,
            group=group,
            keys=tuple(keys),
            origin=unsupported_origin.get(group, "talentBlackboard"),
            reason=(
                "no engine coefficient family consumes these keys; the mechanism has "
                "no runtime path and is reported so the coverage denominator stays honest"
            ),
        )
        for group, keys in sorted(unsupported.items())
    )
    return tuple(coefficients), declared


# --------------------------------------------------------------------------- #
# which talent keys a compile actually consumed, and why the rest are gaps
# --------------------------------------------------------------------------- #

#: A key consumed by a **compile-time reader** rather than by a coefficient
#: family: it lands on a typed :class:`EnemyDefinition` field, so it never
#: appears in ``mechanic_coefficients`` and cannot be recognised by registry
#: membership.  Only readers whose consumption is *observable from the compiled
#: definition* belong here -- the caller passes the field's value and the key
#: counts as consumed exactly when the field is set.  Guessing from the key's
#: mere presence would report a mechanism as covered on carriers where the
#: reader declined it, which is the whole distinction these two entries exist
#: to make.
FROZEN_TARGET_SCALE_SPELLINGS = (
    "damagescaletofrozen.atk_scale",
    "atkup.atk_scale",
)


#: Talent keys no coefficient registry carries, because a **compile-time
#: contract** reads them instead: the value ends up on a typed field of
#: :class:`EnemyDefinition` rather than in `mechanic_coefficients`.  Consumption
#: is decided from that field being set, never from the key being present -- a
#: carrier whose reader declined still reports the gap.
#:
#: Without this table the report counts a landed mechanism as missing, which is
#: the same overstatement it was rebuilt to remove.
CONTRACT_TALENT_KEY_FIELDS: dict[str, str] = {
    "Rage.attack_speed": "self_rage",
    "Rage.move_speed": "self_rage",
    "Rage.duration": "self_rage",
    "Shield.max_hp_ratio": "predation",
    "Mode.mode": "predation",
    "strength.atk": "tactical_command",
    "strength.def": "tactical_command",
    "strength.move_speed": "tactical_command",
    "strength.attack_speed": "tactical_command",
    "Weak.weak[limit]": "environmental_fragile",
    "Weak.damage_scale": "environmental_fragile",
    "Dead.value": "death_life_point_cost",
    "Driver.sp": "armour_charge_sp",
}


def consumed_talent_keys(
    data: dict[str, Any],
    *,
    frozen_target_scale_micros: int = 0,
    definition: Any = None,
) -> set[str]:
    """The shipped ``talentBlackboard`` spellings this enemy's compile used.

    Registry (and patterned) keys are recognised by the same predicate the
    coefficient emitter uses, through the same alias table, so the answer
    cannot drift from what was actually emitted.  Compile-time readers are
    added from their **result**, not from the key's presence.
    """

    consumed: set[str] = set()
    present: set[str] = set()
    for entry in data.get("talentBlackboard") or []:
        shipped = entry.get("key")
        if not shipped:
            continue
        present.add(shipped)
        if is_derivable_coefficient_key(COEFFICIENT_KEY_ALIASES.get(shipped, shipped)):
            consumed.add(shipped)
    if frozen_target_scale_micros:
        for spelling in FROZEN_TARGET_SCALE_SPELLINGS:
            if spelling in present:
                consumed.add(spelling)
                break
    if definition is not None:
        for shipped, field in CONTRACT_TALENT_KEY_FIELDS.items():
            # Truthiness rather than `is not None`: a couple of these fields are
            # ints whose zero means "this carrier declined", not "read as 0".
            if shipped in present and getattr(definition, field, None):
                consumed.add(shipped)
    return consumed


#: Why a particular talent key has no runtime path, when the answer is known.
#:
#: The stage report used to print one blanket line for **every** talent key --
#: 「no mechanism layer consumes enemy talentBlackboard keys」 -- which was true
#: when it was written and became false as the families landed: a dozen of them
#: consume talent keys now.  Keys are no longer reported at all once something
#: consumes them (see :func:`consumed_talent_keys`), and the ones that remain
#: gaps say *why* here whenever the question has actually been researched.
TALENT_KEY_GAP_REASONS: dict[str, str] = {
    # 狂徒 `enemy_1059_buster` 1.5 / 末路狂徒 `_2` 1.8, the latter in IS3 4-1.
    #
    # PRTS 「狂徒」 天赋, verbatim: 「目标嘲讽等级相同时，优先攻击[[指挥终端]]」
    # and 「计算伤害时，若目标为[[指挥终端]]、且自身未受沉默影响，攻击倍率提高至
    # 150%」.  The page's building-class popup names its whole membership --
    # 「包含以下单位：指挥终端」 -- so the in-stage blurb's 「一切可见的设施」
    # is that one unit, and the talent is a **taunt-tier tiebreaker plus a damage
    # multiplier against it**, not a targeting override that ignores blocking.
    #
    # IS3 does ship the command terminal: `trap_008_farm`, whose
    # `character_table` name is 指挥终端 and whose description is
    # 「被摧毁时扣除 1 点关卡生命值」 (hp 5000, def 100, block 0).  But it
    # stands in **2-6 alone**, and the 狂徒 family appears in **4-1 alone**: the
    # carrier and its only valid target never share a stage, so the multiplier
    # cannot fire anywhere in the topic.  That is the trigger being absent, which
    # is a different thing from an unimplemented mechanism -- and it is why this
    # is written down rather than queued.
    #
    # (Do not restate this as 「IS3 has no command terminal」: it has one, in 2-6.
    # The reachability argument is per stage, and it is checked by a test.)
    #
    # The other clause, 「能够攻击在高台地形的作战单位」, already holds by
    # default: nothing height-restricts an enemy's targets except the one id set
    # that is explicitly restricted.
    #
    # The same key on 雪怪小队 / 凿冰人 / 破冰者 is a *different* mechanism and is
    # implemented; it is resolved by description at compile time and so never
    # reaches this table.
    "atkup.atk_scale": (
        "狂徒/末路狂徒's facility priority: PRTS 「狂徒」 scopes 「设施」 to the "
        "command terminal alone (building-class membership is 「包含以下单位："
        "指挥终端」), IS3 places that terminal (`trap_008_farm`) in 2-6 only and "
        "the carrier in 4-1 only, so the multiplier has no reachable target in "
        "this topic rather than the mechanism being unimplemented"
    ),
}

#: What to say when the question has not been researched.  Deliberately narrow:
#: it claims only that *this* compile found no consumer.
DEFAULT_TALENT_GAP_REASON = (
    "no coefficient family and no compile-time reader consumes this key, so the "
    "mechanism it belongs to has no runtime path in this bundle"
)


def talent_key_gap_reason(key: str) -> str:
    return TALENT_KEY_GAP_REASONS.get(key, DEFAULT_TALENT_GAP_REASON)


# --------------------------------------------------------------------------- #
# lowering catalogue entries onto surfaces the engine already executes
# --------------------------------------------------------------------------- #

#: Data attribute name -> the EnemyDefinition field it lands on.  Only stats the
#: engine actually reads appear here; anything else is reported, not guessed.
BASE_STAT_FIELDS = {
    "atk": "attack",
    "def": "defense",
    "magic_resistance": "magic_resistance",
    "max_hp": "max_hp",
}

#: Attributes that need a conversion rather than a direct write.
DERIVED_STAT_FIELDS = {
    # Shipped `attack_speed` is a percentage of a 100 baseline and the engine
    # stores an interval, so a speed bonus *shortens* the interval:
    #   interval' = interval * 100 / (100 + bonus)
    "attack_speed": "attack_interval_ticks",
    "move_speed": "move_milli_tiles_per_tick",
}


class LoweringResult(BaseModel):
    """What a catalogue entry could and could not be turned into."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    applied: dict[str, int] = Field(default_factory=dict)
    skipped: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def lower_always_on_self_stats(
    enemy_id: str,
    base_stats: dict[str, int],
    mechanisms: Iterable[dict[str, Any]],
) -> tuple[dict[str, int], LoweringResult]:
    """Fold ``always`` + ``self_stat`` mechanisms into the enemy's base stats.

    An always-on self modifier is indistinguishable from a different base stat,
    so it needs no engine support at all -- it is applied when the stage is
    compiled.  Triggered mechanisms are deliberately *not* handled here: baking
    a conditional bonus into the base is exactly the mistake that gave
    ``enemy_1168_dumage`` a permanent +70 magic resistance.

    Only entries marked ``clear`` are applied; ambiguous ones are reported so
    the caller can keep them on the pending-alignment list.
    """

    stats = dict(base_stats)
    applied: dict[str, int] = {}
    skipped: list[str] = []
    notes: list[str] = []

    for entry in mechanisms:
        group = entry.get("group") or "?"
        if entry.get("trigger") != "always" or entry.get("effect") != "self_stat":
            continue
        if entry.get("clarity") != "clear":
            skipped.append(f"{group}: ambiguous ({entry.get('ambiguity_kind')})")
            continue
        for param in entry.get("params") or []:
            stat = param.get("stat") or param.get("key", "").split(".")[-1]
            kind = param.get("kind")
            values = [v for v in (param.get("values") or []) if isinstance(v, (int, float))]
            if not values:
                skipped.append(f"{group}.{stat}: no numeric value")
                continue
            # A mechanism can ship several magnitudes across carriers; without a
            # per-enemy binding the compiler must not pick one.
            if len(set(values)) != 1:
                skipped.append(f"{group}.{stat}: {len(set(values))} distinct values, not per-enemy bound")
                continue
            value = float(values[0])

            # Plausibility guard.  A catalogue entry can classify a magnitude
            # under the wrong `kind` -- e.g. `Crash.atk_scale = 800` is typed
            # multiplicative but is plainly not an 800x attack buff.  Applying
            # such a value literally would silently produce a monster, so an
            # implausible factor is refused rather than baked in.
            if kind == "multiplicative" and not 0.0 < value <= 10.0:
                skipped.append(f"{group}.{stat}: multiplier {value} is implausible, refused")
                continue
            if kind == "ratio" and not -1.0 <= value <= 5.0:
                skipped.append(f"{group}.{stat}: ratio {value} is out of range, refused")
                continue

            field_name = BASE_STAT_FIELDS.get(stat)
            if field_name and field_name in stats:
                before = stats[field_name]
                if kind == "additive":
                    stats[field_name] = max(0, int(before + value))
                elif kind in {"multiplicative", "ratio"}:
                    factor = value if kind == "multiplicative" else 1.0 + value
                    stats[field_name] = max(0, int(before * factor))
                else:
                    skipped.append(f"{group}.{stat}: unsupported kind {kind}")
                    continue
                applied[field_name] = stats[field_name]
                notes.append(f"{group}.{stat} {kind} {value}: {field_name} {before} -> {stats[field_name]}")
                continue

            derived = DERIVED_STAT_FIELDS.get(stat)
            if derived == "attack_interval_ticks" and derived in stats:
                if kind != "additive":
                    skipped.append(f"{group}.{stat}: attack_speed expects an additive percentage")
                    continue
                before = stats[derived]
                # +bonus speed shortens the interval; guard the pole at -100.
                if value <= -100:
                    skipped.append(f"{group}.{stat}: attack_speed {value} is out of range")
                    continue
                stats[derived] = max(1, int(round(before * 100.0 / (100.0 + value))))
                applied[derived] = stats[derived]
                notes.append(
                    f"{group}.{stat} additive {value}%: interval {before} -> {stats[derived]} ticks"
                )
                continue

            skipped.append(f"{group}.{stat}: no engine field carries this attribute")

    return stats, LoweringResult(
        applied=applied, skipped=tuple(skipped), notes=tuple(notes)
    )


# --------------------------------------------------------------------------- #
# incoming-damage scaling (implemented mechanism class)
# --------------------------------------------------------------------------- #

#: Condition under which an incoming-damage-scale coefficient applies.  Only
#: conditions the engine can already evaluate from existing enemy state are
#: listed; a mechanism needing facing, zones or RNG is not in this table and
#: stays reported as unrepresented rather than approximated.
INCOMING_SCALE_CONDITIONS = {
    "DamageReduce.damage_resistance": "while_unblocked",
    "Madness.damage_resistance": "below_half_hp",
}

#: Fixed-point scale for damage multipliers (per mille).
MILLI = 1000

#: The engine's own maximum simulated horizon (see engine.MAX_FORECAST_HORIZON_TICKS).
#: A declared invulnerability at least this long cannot expire inside a battle,
#: so it needs no countdown state.
BATTLE_HORIZON_TICKS = 7200
TICKS_PER_SECOND = 30


def incoming_damage_multiplier_milli(
    coefficients: Iterable[Any],
    *,
    is_blocked: bool,
    hp: int,
    max_hp: int,
) -> int:
    """Return the incoming-damage multiplier in per-mille for one enemy.

    ``damage_resistance`` is a *resistance fraction*, so a value of 0.5 means
    the enemy takes half damage.  That reading is pinned by ``DamageReduce``,
    whose description says 「受到的物理或法术伤害减少50%」 against a stored 0.5.

    Multiple applicable resistances multiply, which is the conservative
    composition: it cannot make an enemy take *more* damage than any single
    resistance would allow, and it never reaches zero from finite fractions.
    The alternative (summing fractions) can exceed 1.0 and produce immunity out
    of two partial resistances, so it is not used -- registered as a pending
    alignment in the ledger.
    """

    multiplier = MILLI

    for coefficient in coefficients:
        if getattr(coefficient, "key", None) not in INVULNERABILITY_KEYS:
            continue
        seconds = getattr(coefficient, "value_micros", 0) / MICROS
        if seconds * TICKS_PER_SECOND < BATTLE_HORIZON_TICKS:
            # A shorter window needs a real countdown; refuse rather than
            # silently grant invulnerability for the whole battle.
            raise ValueError(
                f"declared invulnerability of {seconds}s is shorter than the battle "
                "horizon and needs a countdown, which this path does not model"
            )
        return 0

    for coefficient in coefficients:
        key = getattr(coefficient, "key", None)
        condition = INCOMING_SCALE_CONDITIONS.get(key)
        if condition is None:
            continue
        if condition == "while_unblocked" and is_blocked:
            continue
        if condition == "while_blocked" and not is_blocked:
            continue
        if condition == "below_half_hp" and not (max_hp > 0 and hp * 2 <= max_hp):
            continue
        micros = getattr(coefficient, "value_micros", 0)
        # A resistance outside [0, 1) would mean immunity or damage
        # amplification; neither is expressible here, so refuse rather than
        # clamp silently into something plausible.
        if not 0 <= micros < MICROS:
            raise ValueError(
                f"incoming damage resistance {key!r} = {micros} millionths is outside [0, 1)"
            )
        remaining = MICROS - micros
        multiplier = multiplier * remaining // MICROS
    return multiplier
