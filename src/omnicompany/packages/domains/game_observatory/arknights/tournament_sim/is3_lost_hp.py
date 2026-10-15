# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""「已损失生命值判定」 and the families that fire on it.

Moved out of ``is3_talent_families`` for the R4 line budget, but they belong
together on their own account: the check is a **shared** mechanic with a
published formula (PRTS 特殊机制, illustrated there with 囊海爬行者), and
箭棘兽's spray is simply the first carrier in this topic to use it.  A second
carrier should land here rather than growing a copy of the arithmetic.
"""

from __future__ import annotations

from .coefficient_provenance import _authorize_family_rows
from .models import ContentBundle

# 箭棘兽 `enemy_1310_mhprpn_2` (IS3 **4-4**, atk 720, hp 35000, moveSpeed 0.3):
# 「弱点部位位于正面，背面所受伤害降低；**每损失一定生命值向背面半圆范围造成
# 伤害**」, shipping `QuillSpray.hp_ratio` 0.1, `QuillSpray.atk_scale` 1.1 and
# `QuillSpray.range_radius` 1.8.
#
# PRTS 「箭棘兽」 天赋, verbatim: 「受到伤害后进行**10%的已损失生命值判定**，若通过
# 判定则对自身**背面180°半径1.8范围**的扇形区域内的所有我方单位造成攻击力**110%**
# 的物理普通伤害」.
#
# So all three figures are shipped and the reach is shipped too -- unusual here,
# where the radius normally has to come from PRTS.  What PRTS supplies instead is
# the **shape** (a 180° sector, not a circle) and the name of the trigger, which
# is a mechanic of its own with a formula (see `lost_hp_check_passes`).
QUILL_SPRAY_KEYS = frozenset(
    {"QuillSpray.hp_ratio", "QuillSpray.atk_scale", "QuillSpray.range_radius"}
)


def quill_spray_rows(enemy) -> dict[str, int] | None:
    """The three figures, or None when this carrier has no spray.

    All three are required.  A spray with no step would fire on every hit, one
    with no reach would fire at nothing, and one with no scale would deal
    nothing -- each of which is a different mechanism from the shipped one.
    """

    from .coefficient_provenance import coefficient_value

    rows = {
        name: coefficient_value(enemy, key)
        for name, key in (
            ("hp_ratio", "QuillSpray.hp_ratio"),
            ("atk_scale", "QuillSpray.atk_scale"),
            ("range_radius", "QuillSpray.range_radius"),
        )
    }
    if any(value is None for value in rows.values()):
        return None
    return rows  # type: ignore[return-value]


def validate_quill_spray_coefficients(content: ContentBundle) -> bool:
    """Authorize the spray, and refuse the two readings that look plausible."""

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(enemy, QUILL_SPRAY_KEYS, {}, "quill spray"):
            continue
        rows = quill_spray_rows(enemy)
        if rows is None:
            raise ValueError(
                "quill spray needs its step, its scale and its reach together; "
                "%s carries only part" % enemy.enemy_id
            )
        if not 0 < rows["hp_ratio"] < 1_000_000:
            # A step at or above the whole health bar could never come round
            # twice, which is the opposite of 「每损失一定生命值」.
            raise ValueError(
                "quill spray step must be a fraction of max HP; %s ships %d micros"
                % (enemy.enemy_id, rows["hp_ratio"])
            )
        if rows["atk_scale"] <= 0 or rows["range_radius"] <= 0:
            raise ValueError(
                "quill spray scale and reach must both be positive; %s ships "
                "%d and %d" % (
                    enemy.enemy_id, rows["atk_scale"], rows["range_radius"]
                )
            )
        handled = True
    return handled


#: 「已损失生命值判定」, PRTS 特殊机制, verbatim:
#:
#:   「当前已损失生命值的比例高于阶段比例的n倍时通过判定（n即当前阶段数）。
#:     具体逻辑为：若 初始比例-阶段比例×当前阶段数-当前生命值比例>阶段比例，
#:     且 当前生命值比例>0%，则通过判定，并将当前阶段数改为
#:     ⌊(初始比例-当前生命值比例)÷阶段比例+1⌋（当前阶段数初始为1）」
#:
#: Two things in that are easy to get wrong by paraphrase, and both change the
#: answer:
#:
#: * The comparison is **strictly greater than one more step**, with the stage
#:   counter already subtracted.  With a 10% step and the counter at its initial
#:   1, the first pass is at **80%** health, not 90% -- the formula builds in a
#:   step of lag, and thereafter the carrier sprays every 20%.
#: * A single big hit advances the counter to wherever the health now is
#:   (`⌊…⌋`), so it passes **once**, not once per step crossed.
#:
#: Written in cross-multiplied integers so no ratio is ever truncated.
def lost_hp_check_passes(hp: int, max_hp: int, step_micros: int, stage: int) -> bool:
    """Whether 「<step>的已损失生命值判定」 passes right now."""

    if hp <= 0 or max_hp <= 0 or step_micros <= 0:
        return False
    threshold_micros = 1_000_000 - step_micros * (stage + 1)
    return hp * 1_000_000 < threshold_micros * max_hp


def lost_hp_next_stage(hp: int, max_hp: int, step_micros: int) -> int:
    """The counter's new value: ⌊(初始比例-当前生命值比例)÷阶段比例+1⌋."""

    lost_micros = (max_hp - hp) * 1_000_000 // max_hp
    return lost_micros // step_micros + 1

