# [OMNI] origin=claude domain=game_observatory/arknights type=enemy-mechanism-layer status=active
"""Families whose blackboard key cannot decide the mechanism, so the text does.

Moved verbatim out of :mod:`is3_talent_families` so that file fits the
2000-line budget; that module re-exports every name, so importers are
unchanged.

The grouping is not arbitrary.  Every reader here exists because the shipped
data is **ambiguous by key** and the enemy's own description is what resolves
it, which is the single most repeated lesson of this line:

* ``atkup.atk_scale`` carries 狂徒's facility priority *and* 雪怪小队's
  frozen-target damage -- and 1.5 is a shipped value of both, so even the number
  cannot separate them.
* ``Evade.prob`` carries five different dodges across twelve enemies, two of
  them at the same 0.4.
* ``move_speed`` / ``attack_speed`` buff their own carrier everywhere except
  山海密语仪, which buffs the enemy side.
* The energy carriers hold identical ``over_energy`` rows whether they give
  energy or steal it.
* Elemental carriers hold identical conversion rows whatever element they
  inflict.
* The weak-point carriers hold no key at all for *which side* is protected.

So each of these reads the description, and each refuses rather than guessing
when the description does not say.  A key-only reading of any of them would
give some carrier a mechanism it does not have -- which is exactly the failure
these readers were written to stop.
"""

from __future__ import annotations

import re

# `enemy_coefficients` imports this package's family tables, so the one reader
# that needs `coefficient_value` imports it lazily inside the function -- a
# top-level import here is a cycle.

WEAKNESS_REAR_PROTECTED_PHRASE = "背面所受伤害"


WEAKNESS_FRONT_PROTECTED_PHRASE = "正面所受伤害"


def weakness_protected_side_from_description(description: str | None) -> str | None:
    """Read which side resists damage out of the enemy's own description."""

    if not description:
        return None
    rear = WEAKNESS_REAR_PROTECTED_PHRASE in description
    front = WEAKNESS_FRONT_PROTECTED_PHRASE in description
    if rear and front:
        # Both phrases in one description would be contradictory; refusing beats
        # picking whichever was checked first.
        return None
    if rear:
        return "rear"
    if front:
        return "front"
    return None

ENERGY_CHARGE_PHRASES = ("充能", "提供能量")


def is_energy_charger(description: str | None) -> bool:
    """Whether this enemy grants energy to its own side, per its description."""

    if not description:
        return False
    return any(phrase in description for phrase in ENERGY_CHARGE_PHRASES)

ELEMENTAL_TYPE_PHRASES = (
    ("erosion", "侵蚀"),
    ("burn", "灼燃"),
    ("neural", "神经"),
)


def elemental_damage_type(description: str | None) -> str | None:
    """Read which element a carrier inflicts out of its own description."""

    if not description:
        return None
    found = [name for name, phrase in ELEMENTAL_TYPE_PHRASES if phrase in description]
    if len(found) != 1:
        # Two named elements, or none, is not something to guess at.
        return None
    return found[0]

def frozen_target_attack_scale(description: object, blackboard: object) -> int:
    """The frozen-target attack multiple, from whichever key carries it.

    Two spellings ship the same mechanic:

        `damagescaletofrozen.atk_scale`  -- 雪牝 / 霜牡, 2.5
        `atkup.atk_scale`                -- 雪怪小队凿冰人 2.5 / 破冰者 3.0

    The base variants agree at 2.5, which is what makes the second spelling
    identifiable rather than assumed.  But `atkup.atk_scale` **also** carries
    「会优先摧毁一切可见的设施」 on 狂徒 / 末路狂徒 at 1.5 / 1.8, so the key alone
    cannot decide: the discriminator is the description saying 「冻结」.
    """

    if not isinstance(blackboard, list):
        return 0
    values = {
        entry.get("key"): entry.get("value")
        for entry in blackboard
        if isinstance(entry, dict) and entry.get("key")
    }
    canonical = values.get("damagescaletofrozen.atk_scale")
    if isinstance(canonical, (int, float)) and canonical > 0:
        return round(float(canonical) * 1_000_000)
    shared = values.get("atkup.atk_scale")
    if (
        isinstance(shared, (int, float))
        and shared > 0
        and isinstance(description, str)
        and "冻结" in description
    ):
        return round(float(shared) * 1_000_000)
    return 0

#: 不法分子 / 不法分子头目: 「未被阻挡时有几率闪避物理攻击」, `Evade.prob` 0.4.
#: 头目 `enemy_1159_swfmob_2` is the only IS3 carrier (stage 3-4).
#:
#: `Evade.prob` is carried by twelve enemies across the whole database and they
#: do **not** share a mechanism -- 深溟猎食者 dodges 「物理与法术」 while standing
#: on a 溟痕 tile, 酒类爱好者 dodges 100% for a shipped `Evade.duration`, 湖畔
#: 志愿者 gains it below an `Evade.hp_ratio`, 裹骸死士 has it permanently.  The
#: probability alone cannot tell them apart, so the reader requires the IS3
#: shape whole: the probability, no duration and no health gate, and a
#: description that names both the condition (未被阻挡) and the damage type
#: (物理).  Anything else is left unread rather than given this behaviour.
UNBLOCKED_PHYSICAL_EVADE_KEYS = frozenset({"Evade.prob"})

def unblocked_physical_evade(description: object, blackboard: object) -> int:
    """Probability in milli that an unblocked carrier dodges a physical hit."""

    values: dict[str, object] = {}
    for entry in blackboard or ():
        if isinstance(entry, dict) and entry.get("key"):
            values[str(entry["key"])] = entry.get("value")
    probability = values.get("Evade.prob")
    if not isinstance(probability, (int, float)) or probability <= 0:
        return 0
    if "Evade.duration" in values or "Evade.hp_ratio" in values:
        # A timed or health-gated dodge is a different mechanism on the same key.
        return 0
    if not isinstance(description, str):
        return 0
    if "闪避" not in description or "物理" not in description:
        return 0
    if "未被阻挡" not in description:
        return 0
    return round(float(probability) * 1000)

# 逐腐兽 `enemy_1270_nhstlk` (IS3 2-6 / ev-3) and 疯狂的逐腐兽 `_2` (ev-9):
# 「行动速度较快，攻击使目标在一段时间内持续受到法术伤害，目标接受治疗时解除
# 此效果」, with `Bleeding.attack@bleeding_damage` 100 / 150 and
# `Bleeding.attack@duration` 10.0.
#
# PRTS 「逐腐兽」 supplies every part the description leaves vague:
#
#   「受到自身普通攻击的目标在10秒内**每秒**受到100点**无来源**法术普通伤害
#     (该伤害**无法致死**)，**重复触发刷新持续时间**；目标受到治疗效果后该效果
#     **立刻解除**」
#
# Five statements, each of which changes what the mechanic does: once a
# **second**; **无来源** so nothing that reacts to being attacked fires;
# **无法致死** which the engine's `nonlethal` intent flag already means; a
# second hit **refreshes** rather than stacking; and **any** healing clears it.
#
# Not to be confused with `Bleed.hp_ratio` (无人爆破艇's self decay) despite the
# shared prefix: that one is the carrier hurting *itself* with no target at all.
BLEED_ON_ATTACK_KEYS = frozenset(
    {"Bleeding.attack@bleeding_damage", "Bleeding.attack@duration"}
)


def bleed_on_attack(description: object, blackboard: object) -> tuple[int, int]:
    """``(damage_per_second, duration_seconds_micros)``, or ``(0, 0)``.

    Both keys are required: a bleed with no duration would never end and a
    duration with no damage would do nothing, so a partial pair is left unread
    for the caller to report.
    """

    values: dict[str, object] = {}
    for entry in blackboard or ():
        if isinstance(entry, dict) and entry.get("key"):
            values[str(entry["key"])] = entry.get("value")
    damage = values.get("Bleeding.attack@bleeding_damage")
    duration = values.get("Bleeding.attack@duration")
    if not isinstance(damage, (int, float)) or damage <= 0:
        return 0, 0
    if not isinstance(duration, (int, float)) or duration <= 0:
        return 0, 0
    if not isinstance(description, str):
        return 0, 0
    if "法术伤害" not in description:
        return 0, 0
    return round(float(damage)), round(float(duration) * 1_000_000)


def carries_bleed_on_attack(definition: object) -> bool:
    """Whether this enemy's own keys arm the on-attack bleed.

    The mechanism was already implemented -- per-second arts pulses, nonlethal,
    cleared by any heal -- but gated on ``RETURN_BODY_BLEEDING_IDS``, a
    one-element id set naming 逐腐兽.  疯狂的逐腐兽 ships the same two keys at
    150 instead of 100 and stands in ev-9, and it got **nothing**: the same
    S2-b defect this line keeps unwinding, where a mechanism belongs to an id
    rather than to the data.
    """

    from .enemy_coefficients import coefficient_value

    return all(
        coefficient_value(definition, key) is not None
        for key in sorted(BLEED_ON_ATTACK_KEYS)
    )


#: 「接触脉冲波时，不受伤害……」 -- the phrase both 脉冲波 carriers in IS3 ship,
#: word for word.  PRTS names the effect 「免疫脉冲波」 and lists six enemies;
#: reading the description instead of that list keeps the two facts (immunity
#: and what the pulse then does for them) in the data the engine actually
#: compiles from.
PULSE_IMMUNE_PHRASE = "接触脉冲波时"

#: 游击队萨卡兹战士: 「……不受伤害且攻击**切换成法术伤害**」.  Its caster sibling
#: says 「攻击**范围扩大**」 with no figure on the page or in the data, which is
#: why only this one half of 强化模式 is representable.
PULSE_ARTS_SWITCH_PHRASE = "攻击切换成"


#: 隐匿 -- 迷彩 (CAMOUFLAGE) as it is worded for enemies.  PRTS 异常效果:
#: 「无法被对立阵营的部分能力和部分弹道选中；会因**隐匿免疫**（反隐）而失效」,
#: with the note that 「迷彩与"阻挡时解除"没有直接关系，该机制通常由其他效果实现」
#: -- so the block interaction is per carrier, and 流泪小子 states its own:
#: 「隐匿（**解除阻挡0秒后恢复**）」, i.e. a blocked one is visible and it is
#: hidden again the instant the block ends.
#:
#: It is the widest-reaching enemy trait in this topic that had no model:
#: 流泪小子 alone stands in **38 of the 63** stages.
STEALTH_PHRASE = "隐匿"

#: Two carriers ship the same two characters and are **not** hidden by them.
#: Both were caught by reading PRTS rather than the description alone:
#:
#: * 技术侦察兵 「会使周围我方的**隐匿效果失效**」 -- that is about *our* units'
#:   stealth, and reading it flat would have made the anti-stealth unit stealthy.
#: * 散华骑士团精锐 「蓄力时**失去隐匿**」 -- PRTS 「散华骑士团精锐」 gives it one
#:   talent, 「普通攻击不会攻击飞行单位」, and no 隐匿 at all.  Its camouflage
#:   comes from 4-8's emergency rune; the description only says when it drops.
#:   Reading it flat would have hidden the knight in the *ordinary* 4-8 and in
#:   ev-7, where nothing grants it.
STEALTH_NEGATION_PHRASES = ("隐匿效果失效", "失去隐匿")


#: Shipped descriptions colour their key words -- 「蓄力时失去<@eb.key>隐匿</>」 --
#: so a phrase can straddle a tag.  That is how 散华骑士团精锐 slipped past the
#: 「失去隐匿」 check the first time: the two halves are never adjacent in the raw
#: string.
_DESCRIPTION_MARKUP = re.compile(r"<@[^>]*>|</>")


def strip_description_markup(description: str) -> str:
    """The description with its colour tags removed."""

    return _DESCRIPTION_MARKUP.sub("", description)


def enemy_is_stealthed(description: str | None) -> bool:
    """Whether this enemy carries 隐匿 as a trait of its own."""

    if not description:
        return False
    remainder = strip_description_markup(description)
    for phrase in STEALTH_NEGATION_PHRASES:
        remainder = remainder.replace(phrase, "")
    return STEALTH_PHRASE in remainder



#: 「**未阻挡时远程攻击**」 -- 帝国前锋精锐 and its 百战 elite say it word for word,
#: and they are the only two carriers of the phrase in IS3 (four in the whole
#: database).  The mechanism was gated on an id set holding **only the base**,
#: so the elite -- which stands in **b-6** -- swung at nothing it could not
#: reach.
UNBLOCKED_RANGED_PHRASE = "未阻挡时远程攻击"


def ranged_while_unblocked(description: object) -> bool:
    """Whether this enemy shoots as long as nothing is holding it."""

    while isinstance(description, dict) and "m_value" in description:
        description = description["m_value"]
    if not isinstance(description, str):
        return False
    return UNBLOCKED_RANGED_PHRASE in strip_description_markup(description)


def pulse_immune(description: str | None) -> bool:
    """Whether the pulse passes this enemy by."""

    return bool(description) and PULSE_IMMUNE_PHRASE in description


def pulse_empowered_damage_type(description: str | None) -> str | None:
    """What 强化模式 changes, when the description says something measurable."""

    if not pulse_immune(description):
        return None
    if description and PULSE_ARTS_SWITCH_PHRASE in description:
        return "arts"
    return None
