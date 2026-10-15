# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""Contact damage: the two families that hurt by *arriving*, not by attacking.

Moved verbatim out of ``is3_talent_families`` to keep that file under the R4
line budget.  They belong together for a reason beyond arithmetic: 肇事者's
`Combat.*` and 凶豕兽's `Crash.*` are the same shape -- 「自主移动时」 gates
both, neither has a cooldown, and both scale on the mover's *current* speed --
so they share the engine's per-contact bookkeeping and differ only in reach and
in how many blows land.
"""

from __future__ import annotations

from .coefficient_provenance import _authorize_family_rows, coefficient_value
from .models import ContentBundle


# 「肇事者」 contact damage.  Carriers `enemy_1330_cbrush` / `_2`, 40 of the 63
# IS3 stages.  The shipped description is only a gesture --「经过干员时对其造成与
# 自身移动速度相关的物理伤害」-- and PRTS 「"肇事者"量产型」 supplies the rule:
#
#   「自主移动时，对进入自身半径0.3范围内的位于低地的我方单位（包括飞行单位）造成
#     攻击力40%的无途径物理伤害，成功造成伤害后再造成当前移动速度×800的预计算近战
#     途径物理伤害」
#
# **The key names are the wrong way round.**  `Combat.rush_damage` (0.4 / 0.5) is
# the fraction of *attack*; `Combat.atk_scale` (800 / 1000) is the coefficient on
# *move speed*.  Reading `atk_scale` the way its name suggests -- an attack
# multiplier, which is what it means on every other carrier here -- yields
# 1000 x 800 = 800000 damage instead of 1.1 x 800 = 880.
RUSH_CONTACT_KEYS = frozenset({"Combat.atk_scale", "Combat.rush_damage"})

RUSH_CONTACT_COEFFICIENTS: dict[str, dict[str, int]] = {}

#: 「半径0.3范围内」 -- the contact radius, in milli-tiles.
RUSH_CONTACT_RADIUS_MILLI_TILES = 300


def rush_contact_rows(enemy) -> dict[str, int] | None:
    """The two figures, or None when this carrier has no contact damage.

    Both keys are required: one alone is a shape this reader has never seen, and
    guessing the other would put the magnitude off by a factor of a thousand.
    """

    # Imported here rather than at module scope: the exception lives in
    # `engine`, which imports this module.
    from .coefficient_provenance import coefficient_value
    from .engine import UnsupportedMechanicError

    speed_coefficient = coefficient_value(enemy, "Combat.atk_scale")
    attack_fraction = coefficient_value(enemy, "Combat.rush_damage")
    if speed_coefficient is None and attack_fraction is None:
        return None
    if speed_coefficient is None or attack_fraction is None:
        raise UnsupportedMechanicError(
            "contact damage needs both Combat.atk_scale (the move-speed "
            "coefficient) and Combat.rush_damage (the attack fraction); "
            "one alone is not the shipped shape"
        )
    return {
        "speed_coefficient": speed_coefficient,
        "attack_fraction": attack_fraction,
    }


def validate_rush_contact_coefficients(content: ContentBundle) -> bool:
    """Authorize 「肇事者」's contact damage, requiring both halves.

    The two keys mean the opposite of their names -- `Combat.atk_scale` is the
    coefficient on **move speed**, `Combat.rush_damage` the fraction of
    **attack** -- so a carrier with only one of them cannot be completed by
    guessing: the missing half would be wrong by three orders of magnitude.

    The speed coefficient is also required to be a whole number of units, since
    PRTS states it as 「当前移动速度x800」.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, RUSH_CONTACT_KEYS, RUSH_CONTACT_COEFFICIENTS, "contact damage"
        ):
            continue
        carried = {
            coefficient.key
            for coefficient in enemy.mechanic_coefficients
            if coefficient.key in RUSH_CONTACT_KEYS
        }
        if carried != RUSH_CONTACT_KEYS:
            raise ValueError(
                "contact damage requires both Combat.atk_scale (the move-speed "
                "coefficient) and Combat.rush_damage (the attack fraction); %s "
                "carries only %s" % (enemy.enemy_id, sorted(carried))
            )
        speed_coefficient = coefficient_value(enemy, "Combat.atk_scale")
        if speed_coefficient is None or speed_coefficient % 1_000_000:
            raise ValueError(
                "contact damage speed coefficient must be whole units; %s "
                "ships %s micros" % (enemy.enemy_id, speed_coefficient)
            )
        fraction = coefficient_value(enemy, "Combat.rush_damage")
        if fraction is None or not 0 < fraction <= 1_000_000:
            raise ValueError(
                "contact damage attack fraction must sit in (0, 1]; %s ships "
                "%s micros -- a value above one would mean the key had been "
                "read as a multiplier" % (enemy.enemy_id, fraction)
            )
        handled = True
    return handled


# 凶豕兽 `enemy_1309_mhboar` and 狂暴凶豕兽 `_2` (IS3 **4-4**, atk 700,
# moveSpeed 0.4): 「占用2阻挡数；冲撞时逐渐加速，并对经过与阻挡自身的目标
# 根据移动速度造成伤害」, shipping `Crash.atk_scale` 800 and `Crash.stun` 0.0.
#
# PRTS 「凶豕兽」, verbatim: 「自主移动时，对进入自身**半径0.5范围**内的位于
# 低地的我方单位（包括飞行单位）造成**当前移动速度×700**的预计算近战途径
# 物理伤害与**0秒**晕眩」 -- so `Crash.stun` 0.0 is not a missing figure, it is
# a **declared zero**, and the engine already treats a zero duration as a no-op.
#
# Same shape as 肇事者's contact damage (`Combat.*`), which is why it rides the
# same episode bookkeeping: 「自主移动时」 gates both, and neither has a cooldown,
# so a target is hit once per contact rather than thirty times a second.
# The differences are the reach (0.5 against 0.3) and that this one lands a
# single hit where 肇事者 lands two.
CRASH_CONTACT_KEYS = frozenset({"Crash.atk_scale", "Crash.stun"})

#: 「半径0.5范围内」 -- PRTS only; the blackboard carries no reach.
CRASH_CONTACT_RADIUS_MILLI_TILES = 500


def crash_contact_rows(enemy) -> int | None:
    """The speed coefficient, or None when this carrier has no charge damage.

    Both keys are required.  `Crash.stun` shipping 0.0 looks like an omission
    and is not: PRTS states the stun as **0 seconds**, so a carrier with the
    scale and no stun row would be a shape this data has never produced.
    """

    from .enemy_coefficients import coefficient_value

    scale = coefficient_value(enemy, "Crash.atk_scale")
    stun = coefficient_value(enemy, "Crash.stun")
    if scale is None or stun is None or scale <= 0:
        return None
    return scale


def validate_crash_contact_coefficients(content: ContentBundle) -> bool:
    """Authorize the charge's contact damage.

    The coefficient is a whole multiplier of speed (700 / 800 across the pair),
    so a fractional one would mean the key had been read on the wrong scale --
    the same check its sibling family applies.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, CRASH_CONTACT_KEYS, {}, "charge contact damage"
        ):
            continue
        for coefficient in enemy.mechanic_coefficients:
            if coefficient.key != "Crash.atk_scale":
                continue
            if coefficient.value_micros <= 0 or coefficient.value_micros % 1_000_000:
                raise ValueError(
                    "charge contact scale must be a whole speed multiplier; %s "
                    "ships %d micros" % (enemy.enemy_id, coefficient.value_micros)
                )
            handled = True
    return handled
