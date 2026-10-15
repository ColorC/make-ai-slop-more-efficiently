# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""Shared primitives for authorising enemy mechanic coefficients.

Moved verbatim out of ``enemy_coefficients`` so the families that use
them can live in their own module without an import cycle.
"""

from __future__ import annotations

import re

from .models import EnemyDefinition


# Progressive charge acceleration.  Unlike every family above, these keys embed
# the carrier's own name -- `rush.dlancer_t[trigger].move_speed`,
# `Rush.mhboar_t[trigger].move_speed` -- so a fixed key set cannot express the
# family and it is matched by shape instead.
#
# The increment is a *fraction of base speed*, not absolute tiles per second.
# That is settled by magnitude, not preference: dlancer moves at 0.25 tiles/s
# with increment 0.5 and trig_cnt 25, mhboar_2 at 0.4 with 0.333 and 35.  Read
# as absolute the end speeds are 12.75 and 12.06 tiles/s -- 51x and 30x base,
# crossing a whole map in about a second.  Read as a fraction they are 3.38 and
# 5.06 tiles/s, the right order for a charging unit at full speed.  A positive
# tell points the same way: for both carriers the increment equals the
# interval (0.5/0.5 and 0.333/0.333), i.e. both accelerate at exactly +100% of
# base per second -- a design constant under the fractional reading and a
# coincidence under the absolute one.
RUSH_ACCELERATION_PATTERN = re.compile(
    r"^[Rr]ush\.[A-Za-z0-9_]+\[trigger\]\.(?P<leaf>move_speed|interval|trig_cnt)$"
)


RUSH_PREDELAY_PATTERN = re.compile(
    r"^[Rr]ush\.[A-Za-z0-9_]+_predelay\.(?P<leaf>predelay)$"
)


RUSH_PATTERNS = (RUSH_ACCELERATION_PATTERN, RUSH_PREDELAY_PATTERN)


#: 爆破攻坚手's splash: the key embeds the carrier's own name, the way the rush
#: keys do -- `aoe.attack@bsthmr_range.atk_scale`.  Matching the shape rather
#: than the one shipped spelling means a second carrier of the same mechanic is
#: picked up without editing an allow-list.
SPLASH_PATTERN = re.compile(
    r"^aoe\.attack@[A-Za-z0-9_]+\.(?P<leaf>atk_scale)$"
)


#: Every patterned family, so one predicate can answer "is this key wired up?".
PATTERNED_COEFFICIENT_PATTERNS = RUSH_PATTERNS + (SPLASH_PATTERN,)


#: The three leaves an acceleration ramp needs; `predelay` is optional because
#: only one of the two carriers ships it.
RUSH_REQUIRED_LEAVES = frozenset({"move_speed", "interval", "trig_cnt"})


def rush_pattern_leaf(key: str) -> str | None:
    """The leaf name when ``key`` belongs to the **rush** family specifically.

    Separate from `patterned_coefficient_leaf` on purpose: that one answers
    "is this key wired up at all" across every patterned family, and the row
    collector below must not sweep another family's leaf into the ramp.  The
    splash key's leaf is also `atk_scale`, and letting it through made the rush
    validator demand `move_speed`/`interval`/`trig_cnt` from 爆破攻坚手 and
    refuse three real stages.
    """

    for pattern in RUSH_PATTERNS:
        match = pattern.match(key)
        if match is not None:
            return match.group("leaf")
    return None


def patterned_coefficient_leaf(key: str) -> str | None:
    """Return the leaf name when ``key`` belongs to any patterned family."""

    for pattern in PATTERNED_COEFFICIENT_PATTERNS:
        match = pattern.match(key)
        if match is not None:
            return match.group("leaf")
    return None


def rush_acceleration_rows(enemy: "EnemyDefinition") -> dict[str, int]:
    """Return ``{leaf: value_micros}`` for one enemy's acceleration ramp."""

    rows: dict[str, int] = {}
    for coefficient in enemy.mechanic_coefficients:
        leaf = rush_pattern_leaf(coefficient.key)
        if leaf is not None:
            rows[leaf] = coefficient.value_micros
    return rows


def coefficient_value(enemy: EnemyDefinition, key: str) -> int | None:
    """Return one exact coefficient value without float conversion."""

    return next(
        (
            coefficient.value_micros
            for coefficient in enemy.mechanic_coefficients
            if coefficient.key == key
        ),
        None,
    )


def _authorize_family_rows(
    enemy: EnemyDefinition,
    family_keys: frozenset[str],
    frozen: dict[str, dict[str, int]],
    family_name: str,
) -> bool:
    """Authorize one enemy's rows for a coefficient family.

    Two regimes, deliberately:

    * An enemy **named in the frozen matrix** is still checked for exact
      equality, so every existing fixture keeps its guarantee.
    * An enemy **outside** the matrix is authorized on *provenance* instead of
      identity: its rows must point at its own ``enemy_database`` entry.

    The second regime is the S2-b lift.  Before it, ``frozen.get(id, {})``
    returned an empty matrix for any unlisted enemy, so a mechanism carried by
    a different enemy raised instead of running -- which is why only 24 of the
    249 IS3 enemy ids had a working contract.  Identity checks are a
    migration-period compatibility layer: **delete the frozen matrices once the
    engine's trigger semantics are covered by tests per mechanism rather than
    per enemy id**, and this helper collapses to the provenance branch.

    Returns whether this family handled the enemy.
    """

    actual = {
        coefficient.key: (coefficient.value_micros, coefficient.source_pointer)
        for coefficient in enemy.mechanic_coefficients
        if coefficient.key in family_keys
    }
    if not actual:
        return False

    if enemy.enemy_id in frozen:
        expected_rows = {
            key: (value_micros, f"enemy_database.json#/{enemy.enemy_id}/{key}")
            for key, value_micros in frozen[enemy.enemy_id].items()
        }
        # A matrix pins the rows it **names**.  A family key the matrix omits
        # falls through to the provenance check below instead of making the
        # comparison fail: some figures are shipped per variant
        # (`revive.unmove_duration` is 85 s in two stages and 150 s elsewhere),
        # so a single frozen number for them would refuse real stages.
        pinned = {key: row for key, row in actual.items() if key in expected_rows}
        if pinned:
            # Some of the pinned rows are here, so all of them must be, at their
            # frozen values: that is what keeps a fixture from quietly dropping
            # one.
            if pinned != expected_rows:
                raise ValueError(
                    f"{family_name} coefficient differs from the frozen source"
                )
            if set(actual) == set(expected_rows):
                return True
        # Either none of the pinned rows are present -- which real data does:
        # on 3-6's raid node `enemy_1075_dmgswd` ships only
        # `revive.unmove_duration` -- or there are extra rows beyond them.
        # Both fall through to provenance rather than being refused.

    # Provenance, not one exact string.  A row legitimately comes from either
    # the enemy database or the stage's own `overwrittenData` (a level may
    # redefine an enemy inline), so the rule is: whenever the pointer uses the
    # `enemy_database.json#/<id>/` form it must name *this* enemy -- which is
    # what stops one enemy's numbers being reused on another.  Any other
    # pointer shape is a level-file path and is accepted as-is.
    prefix = "enemy_database.json#/"
    for key, (_value, pointer) in sorted(actual.items()):
        if not pointer.startswith(prefix):
            continue
        owner = pointer[len(prefix) :].split("/", 1)[0]
        if owner != enemy.enemy_id:
            raise ValueError(
                f"{family_name} coefficient {key!r} on {enemy.enemy_id!r} cites another "
                f"enemy's database entry ({owner!r}); coefficients may not be borrowed"
            )
    return True


def authorize_family_outside_frozen_stage(
    content: "ContentBundle", family_keys: frozenset[str], family_name: str
) -> bool:
    """Authorize a stage-bound family on a bundle it was not frozen to.

    The frozen stage keeps its exact identity check; every other stage is
    checked the way `_authorize_family_rows` checks an enemy outside a frozen
    matrix -- on provenance.  A carrier must cite its own `enemy_database`
    entry, which is what stops one enemy's numbers being reused on another,
    while no longer refusing a stage merely for not being *the* stage.

    Returns whether the family handled this bundle.
    """

    handled = False
    prefix = "enemy_database.json#/"
    for enemy in content.enemies:
        rows = [
            coefficient
            for coefficient in enemy.mechanic_coefficients
            if coefficient.key in family_keys
        ]
        if not rows:
            continue
        handled = True
        for coefficient in sorted(rows, key=lambda item: item.key):
            pointer = coefficient.source_pointer
            if not pointer.startswith(prefix):
                continue
            owner = pointer[len(prefix) :].split("/", 1)[0]
            if owner != enemy.enemy_id:
                raise ValueError(
                    f"{family_name} coefficient {coefficient.key!r} on "
                    f"{enemy.enemy_id!r} cites another enemy's database entry "
                    f"({owner!r}); coefficients may not be borrowed"
                )
    return handled


def splash_attack_scale(enemy: "EnemyDefinition") -> int | None:
    """The splash multiple this carrier applies, or None.

    PRTS 「爆破攻坚手」: 「攻击对目标造成攻击力100%的物理伤害，对目标周围四格的
    **其他**我方单位造成攻击力50%的近战途径物理溅射伤害（格子判定，不可对空，不受
    迷彩影响）」.  So the shipped 0.5 is a fraction of attack applied to the
    neighbours, and the target itself is explicitly excluded.
    """

    for coefficient in enemy.mechanic_coefficients:
        if SPLASH_PATTERN.match(coefficient.key):
            return coefficient.value_micros
    return None
