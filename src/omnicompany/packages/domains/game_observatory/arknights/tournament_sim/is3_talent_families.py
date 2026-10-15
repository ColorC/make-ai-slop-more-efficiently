# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""IS3 enemy talent families: their key sets, cited constants, validators.

Moved verbatim out of ``enemy_coefficients`` to keep that file under the
R4 line budget.  Every citation -- PRTS page or unpacked field path --
travels with the family it justifies.
"""

from __future__ import annotations

# Re-exported: these readers moved to `is3_description_gated` so this file fits
# the 2000-line budget.  Every one of them decides a mechanism from the shipped
# description because the key alone cannot; see that module's docstring for the
# six ways this data is ambiguous by key.
from .is3_description_gated import (  # noqa: F401
    BLEED_ON_ATTACK_KEYS,
    ELEMENTAL_TYPE_PHRASES,
    ENERGY_CHARGE_PHRASES,
    WEAKNESS_FRONT_PROTECTED_PHRASE,
    WEAKNESS_REAR_PROTECTED_PHRASE,
    bleed_on_attack,
    carries_bleed_on_attack,
    elemental_damage_type,
    frozen_target_attack_scale,
    enemy_is_stealthed,
    is_energy_charger,
    pulse_empowered_damage_type,
    pulse_immune,
    ranged_while_unblocked,
    unblocked_physical_evade,
    weakness_protected_side_from_description,
)

from .coefficient_provenance import (
    _authorize_family_rows,
    coefficient_value,
    patterned_coefficient_leaf,
    rush_pattern_leaf,
    rush_acceleration_rows,
    RUSH_REQUIRED_LEAVES,
)
from .models import ContentBundle, EnemyDefinition

# Re-exported: the two contact-damage families moved to `is3_contact_families`
# for the same line-budget reason as the readers above.
from .is3_lost_hp import (  # noqa: F401
    QUILL_SPRAY_KEYS,
    lost_hp_check_passes,
    lost_hp_next_stage,
    quill_spray_rows,
    validate_quill_spray_coefficients,
)
from .is3_contact_families import (  # noqa: F401
    CRASH_CONTACT_KEYS,
    CRASH_CONTACT_RADIUS_MILLI_TILES,
    RUSH_CONTACT_COEFFICIENTS,
    RUSH_CONTACT_KEYS,
    RUSH_CONTACT_RADIUS_MILLI_TILES,
    crash_contact_rows,
    rush_contact_rows,
    validate_crash_contact_coefficients,
    validate_rush_contact_coefficients,
)


# Incoming-damage scaling carried by the enemy's own talent data.  Unlike the
# families above this one has **no frozen identity matrix**: any enemy whose
# enemy_database entry carries one of these keys gets the mechanism, authorized
# on provenance (see `_authorize_family_rows`).  That is the S2-b lift applied
# to a new mechanism class rather than another allow-list.
#
# `damage_resistance` is read as a *resistance fraction*: the incoming damage
# multiplier is `1 - value`.  `DamageReduce` pins that reading numerically --
# its description says 「受到的物理或法术伤害减少50%」 and its value is 0.5.
INCOMING_DAMAGE_SCALE_KEYS = frozenset(
    {
        "DamageReduce.damage_resistance",
        "Madness.damage_resistance",
    }
)


# `atkup.atk` is three different mechanics sharing one key name.  Two carriers
# describe a one-off boost at half health (enemy_1025_reveng_2「低体力时…提升作战
# 意志」, enemy_1113_empace(_2)「生命值降至一半时攻击力大幅提升」); the third,
# enemy_2005_axetro, describes 「连续攻击时会提高攻击力和攻击速度，4秒不攻击后清空」
# -- a stacking on-attack buff.  The discriminator is in the data: only the
# stacking variant carries `atkup.max_stack_cnt`, so that key is made derivable
# purely so the engine can tell the two apart instead of guessing from the id.
LOW_HP_ATTACK_KEYS = frozenset({"atkup.max_stack_cnt"})


INCOMING_DAMAGE_SCALE_COEFFICIENTS: dict[str, dict[str, int]] = {}


# Control states an enemy's ordinary attack inflicts.  The `attack@` infix is
# the shipped convention for "this modifies my own attack", so these are read
# per attack rather than per skill.  `Combat.attack@stun` was already executed
# for one enemy; the freeze keys are the same shape on other carriers.
#
# NOTE ON FREEZE: `ControlEffect` has STUN/BIND/SILENCE/DISARM and no frozen
# state, so freeze is executed as "cannot act for N seconds" using the same
# stun countdown.  The duration is source-exact; the difference between frozen
# and stunned (frozen units also interact with damage differently in the real
# game) is registered as a pending alignment rather than modelled.
ATTACK_CONTROL_KEYS: dict[str, str] = {
    "Combat.attack@stun": "stun",
    "Attack.attack@freeze": "freeze",
    "combat.attack@freeze": "freeze",
}


ATTACK_CONTROL_COEFFICIENTS: dict[str, dict[str, int]] = {}


# Declared invulnerability.  `Doom.duration` ships 600 seconds on
# enemy_2056_smedzi, whose description is literally 「无敌」.  600 s is 18000
# ticks, longer than the engine's own maximum simulated horizon
# (MAX_FORECAST_HORIZON_TICKS = 7200), so within any battle this engine can run
# the unit is simply invulnerable -- no countdown state is required.
#
# A *shorter* declared invulnerability (invincible.duration = 40 s) genuinely
# needs a per-enemy countdown and is therefore NOT in this set.
INVULNERABILITY_KEYS = frozenset({"Doom.duration"})


INVULNERABILITY_COEFFICIENTS: dict[str, dict[str, int]] = {}


# Rear-hit resistance.  Six IS3 enemies carry `Weakness.damage_resistance`
# (0.8 / 0.75) with the text 「弱点部位位于正面，背面所受伤害大幅降低」: the weak
# point faces forward, so a hit from behind is reduced by that fraction.
#
# `WeaknessUI.value` is the display marker for the same talent and carries no
# combat effect, so it is deliberately not executable.
REAR_HIT_RESISTANCE_KEYS = frozenset({"Weakness.damage_resistance"})


REAR_HIT_RESISTANCE_COEFFICIENTS: dict[str, dict[str, int]] = {}


#: The protected side is stated in the shipped description, and only there --
#: `WeaknessUI.value` is 0.0 on every carrier and encodes nothing.  All six IS3
#: carriers match one of these two phrasings:
#:   「弱点部位位于正面，背面所受伤害大幅降低」  -> the rear is protected
#:   「弱点部位位于背面，正面所受伤害大幅降低」  -> the front is protected
#:   「总是面向我方干员较多的一侧并使正面所受伤害大幅降低」 -> the front
# Periodic freeze aura.  `enemy_2055_smlead` (IS3 stages b-5 / b-5-b) ships the
# whole triple, and the three keys are exactly the three quantities a periodic
# area effect needs, so no semantic has to be invented:
#
#   FreezeAura.interval     = 5.0 s  -- how often it pulses
#   FreezeAura.range_radius = 2.0    -- tiles from the carrier
#   FreezeAura.freeze       = 4.5 s  -- how long each pulse freezes
#
# Unlike a bare `invincible.duration`, whose trigger is nowhere stated in the
# data, this family carries its own trigger in `interval`.
FREEZE_AURA_KEYS = frozenset(
    {"FreezeAura.freeze", "FreezeAura.interval", "FreezeAura.range_radius"}
)


FREEZE_AURA_COEFFICIENTS: dict[str, dict[str, int]] = {}


# Additive defence auras.  Two families that look alike and stack oppositely,
# which is exactly why the rule is cited rather than assumed:
#
# * `defup.def` -- PRTS 「御4」: 「使2.5半径范围内实体类型为默认类或装置类的**其他敌方
#   单位**防御力+300（**不可叠加**，可被沉默）」.  The carrier buffs its own side, not
#   itself, and a second carrier adds nothing.
# * `auraDefup.def` -- PRTS 「深池方阵战士」: +200 defence for **each** nearby phalanx
#   warrior or phalanx commander, radius **1.5**, 「**叠加无上限**」.
#
# The unpacked data carries only the magnitudes: `defup` ships its radius but
# says nothing about stacking or which side it targets, and `auraDefup` ships
# neither radius nor stacking.  Both gaps are filled from PRTS and neither
# conflicts with the unpacked text.
DEFENCE_AURA_KEYS = frozenset({"defup.def", "defup.range_radius", "auraDefup.def"})


DEFENCE_AURA_COEFFICIENTS: dict[str, dict[str, int]] = {}


#: PRTS-only value: `auraDefup` ships no radius field at all.
AURA_DEFUP_RADIUS_MILLI_TILES = 1500


# The other half of the charge mechanic.  PRTS 「萨卡兹穿刺手」 states the whole
# cycle, and it is one mechanism rather than two:
#
#   「如果未被阻挡: 每 0.5 秒获得最多 1 层 移动速度 +50% buff, 最多 25 层
#     (总上限 +1250%)」            -- the ramp (RUSH_ACCELERATION_PATTERN)
#   「被阻挡后的首次攻击根据移速造成额外伤害」, 额外伤害 = 「当前移动速度x600」
#   「加速在任何眩晕/束缚效果时停止」
#   「所有 buff 在触发攻击后重置」  -- and the ramp starts again next block cycle
#
# That last line **falsifies** the default this repo registered when only the
# unpacked data was available ("being blocked neither advances nor resets the
# ramp"): the ramp is spent and cleared by the attack it powers.
#
# **Correction, and the reason this family is no longer named for blocking.**
# The 天赋 body -- as opposed to the one-line 能力 summary quoted above -- words
# 穿刺手 and 凶豕兽 identically:
#
#   「加速时，……同时普通攻击附加的Buff会额外造成一次**当前移动速度×600**的预计算
#     近战途径物理伤害并停止加速」            (`firstattack.atk_scale`, dlancer)
#   「冲撞时，……同时普通攻击附加的Buff会额外造成一次**当前移动速度×500**的预计算
#     近战途径物理伤害，成功造成该额外伤害后停止冲撞」 (`FirstAttack.atk_scale`, mhboar)
#
# So the trigger is 「加速时的普通攻击」, not "the first attack after being
# blocked" -- that summary is a *consequence* for a melee carrier, which only
# ever attacks something blocking it.  Reading the summary as the rule is what
# made the two spellings look like two mechanisms; they are one, and the capital
# is nothing but a capital.  (Six spelling splits in this data really were two
# mechanisms apiece; this is the first that is not.  The lesson survives with a
# tighter edge: check the 天赋 body, not the 能力 line, before deciding.)
CHARGE_BURST_KEYS = frozenset({"firstattack.atk_scale", "FirstAttack.atk_scale"})

#: The two spellings, most specific first, for a reader that wants the value.
CHARGE_BURST_KEY_ORDER = ("firstattack.atk_scale", "FirstAttack.atk_scale")


CHARGE_BURST_COEFFICIENTS: dict[str, dict[str, int]] = {}


# Attack-speed suppression aura.  IS3 carrier is `enemy_1042_frostd` (寒霜):
# unpacked 「【飞行单位】防御力较高，会使周围我方单位的攻击速度会大幅度削减」 with
# `atkSpeedDown.attack_speed` -0.5, and PRTS 「寒霜」 gives the whole rule --
# 「使2.5半径范围内的所有我方单位攻击速度-50%（不可叠加；可被沉默）」.
#
# Two traps live in this one key.
#
# 1. **The radius comes from `defup.range_radius`.**  寒霜 ships that key with no
#    `defup.def`, and PRTS gives it no defence aura at all, so the radius belongs
#    to *this* mechanic.  The key prefix does not decide the effect.
# 2. **The value is a fraction, while the engine's attack-speed convention is
#    points against a 100 baseline.**  The same quantity ships on both scales in
#    the same file: `BlackFog.attack_speed` is -50.0 (points, consumed as-is)
#    and `buff.attack_speed` is 100.0 (points, matching PRTS 霜牡's
#    「攻击速度增加100」), but `atkSpeedDown.attack_speed` is -0.5 / -0.8 --
#    fractions, i.e. -50% / -80%.  Feeding the fraction straight into the points
#    formula yields a 0.5% slowdown instead of 50%: a 100x error that still
#    looks like a plausible number.
ATTACK_SPEED_AURA_KEYS = frozenset({"atkSpeedDown.attack_speed"})


ATTACK_SPEED_AURA_COEFFICIENTS: dict[str, dict[str, int]] = {}


#: Fraction -> the engine's points-against-100 attack-speed scale.
ATTACK_SPEED_FRACTION_TO_POINTS = 100


# 寒冷 / 冻结, per PRTS 异常效果图鉴.  Every key in the data is *named* `freeze`
# (`Attack.attack@freeze`, `combat.attack@freeze`, `FreezeAura.freeze`,
# `boom.freeze`), but what those apply is **寒冷 (cold)**, and the enemy
# descriptions say so in words: 雪牝 「攻击会对目标施加寒冷」, 冰爆源石虫
# 「死亡后对周围我方单位造成物理伤害且施加寒冷」, 萨米首领 「攻击...附带寒冷」.
#
#   寒冷: attack speed -30.  Not a hold -- the target keeps acting, slower.
#   冻结: cannot move, attack or use skills, and magic resistance -15.
#
# The escalation is what ties them together: a further 敌方寒冷 applied while the
# target is already cold (or already frozen) lands as 敌方冻结 instead.  So a
# single hit slows, and the second one locks.  Treating one hit as a hold
# overstates a lone attacker by a wide margin.
COLD_ATTACK_SPEED_POINTS = -30


FROZEN_MAGIC_RESISTANCE_PENALTY = 15


# Death explosions.  IS3 carriers: `enemy_1021_bslime` / `_2` (高能源石虫, stages
# 2-5 / b-2-b / b-3) at `boom.atk_scale` 4.0, and `enemy_1067_snslime_2`
# (冰爆源石虫·α, stages 4-8 / b-4-b / b-5) at 2.0 with `boom.freeze` 10.0.
# Descriptions: 「死亡后会产生爆炸」 and 「死亡后对周围我方单位造成物理伤害且施加寒冷」.
#
# The magnitudes are in the data; **the radius and the fuse are not**, for these
# carriers.  PRTS supplies both:
#   高能源石虫   -- 「造成攻击力400%的无途径物理溅射伤害，无视迷彩」, radius 1.25, 1 s delay
#   冰爆源石虫   -- 200% attack as physical splash, radius 1.65, 1 s delay,
#                  plus 敌方寒冷 for 10 s
#
# Other carriers elsewhere in the database *do* ship a radius
# (`boom.projectile_range` 1.4 / 1.2, `boom.range_radius`), so data is preferred
# whenever present and the cited table is the fallback, not the rule.
BOOM_KEYS = frozenset(
    {
        "boom.atk_scale",
        "boom.freeze",
        "boom.projectile_range",
        "boom.range_radius",
    }
)


BOOM_COEFFICIENTS: dict[str, dict[str, int]] = {}


#: PRTS-only radii, in milli-tiles, for carriers whose data omits one.
BOOM_RADIUS_MILLI_TILES_BY_ENEMY = {
    "enemy_1021_bslime": 1250,
    "enemy_1021_bslime_2": 1250,
    "enemy_1067_snslime": 1650,
    "enemy_1067_snslime_2": 1650,
    # PRTS 「辐能源石虫」: 「爆炸半径 1.25，延迟 0.5 秒，造成攻击力 400% 的
    # 法术溅射伤害，不可对空；给予全场敌人 1 点能量」.  The 400% is the
    # shipped `Boom.atk_scale` 4.0, which is what makes the rest of that
    # sentence usable rather than merely plausible.
    "enemy_1352_eslime": 1250,
    "enemy_1352_eslime_2": 1250,
    # PRTS 「无人爆破艇」: 「当该敌人死亡…释放一个延时爆炸弹道」, detonating
    # 1 second later for 「200%攻击力物理溅射 + 170%蚀伤」 within radius
    # **1.25**.  The two multipliers are the shipped `Boom.atk_scale` 2.0 and
    # `Boom.ep_damage_ratio` 1.7, so only the radius comes from PRTS alone.
    #
    # This carrier was **invisible to the boom family until the capitalisation
    # alias landed**, and the self-decay batch wrote down that it "ships no boom
    # key at all" -- true only of the lowercase spelling, which is exactly the
    # blind spot the alias exists to close.  Corrected there.
    "enemy_1164_bmbmot": 1250,
    "enemy_1164_bmbmot_2": 1250,
    "enemy_1164_bmbmot_3": 1250,
}


#: PRTS: 「延迟 1 秒」 on the 源石虫 pair, at the engine's 30 TPS.
BOOM_FUSE_TICKS = 30

#: … but not on every carrier: 辐能源石虫's is 「延迟 0.5 秒」.  A shared
#: constant would have given it twice the warning the game gives, which is the
#: difference between a blast a defender can retreat out of and one it cannot.
BOOM_FUSE_TICKS_BY_ENEMY = {
    "enemy_1352_eslime": 15,
    "enemy_1352_eslime_2": 15,
}


# Resistance that erodes as the carrier is hit.  IS3 carriers:
# `enemy_1329_cbshld` (弧光镜卫, stage 2-2) and `_2` (弧光镜卫长, stage b-6),
# 「初始防御力与法术抗性较高，但每次受到伤害后降低」.
#
# PRTS 「弧光镜卫」: 「每次受到伤害时，（于防御/法抗计算后）获得1层【抗性降低】」,
# 「每层使自身防御力-50，法术抗性-2」, 「最多叠加80层」.  The parenthetical matters:
# the stack lands *after* the hit that caused it has been resolved, so the first
# hit meets the full defence.
#
# The shipped values are the **totals at maximum stacks**, and they divide
# exactly: -4000 def over 80 stacks and -170 mres over 85 are both the per-stack
# figures PRTS quotes.  So the per-stack amount is derived from the data and the
# wiki only confirms it -- which is why the validator insists on exact division
# rather than rounding a remainder away.
DEF_REDUCE_KEYS = frozenset(
    {
        "def_reduce.def",
        "def_reduce.magic_resistance",
        "def_reduce.max_stack_cnt",
        "def_reduce.change1_stack_cnt",
        "def_reduce.change2_stack_cnt",
    }
)


DEF_REDUCE_COEFFICIENTS: dict[str, dict[str, int]] = {}


# Self-inflicted damage over time.  IS3 carriers: `enemy_1062_rager` (330) /
# `_2` (500) and `enemy_1063_rageth` (250) / `_2` (350), 「会持续损失生命」.
#
# PRTS 「狂暴宿主士兵」 天赋&能力: 「自身每秒受到330无来源真实伤害」.  Three things
# come from that sentence and none of them are in the data:
#   * the period is **one second**, so the tick count is the bundle's own TPS
#     rather than a constant written here;
#   * the damage is **真实伤害** -- true damage, so no defence, resistance or
#     declared invulnerability applies to it;
#   * it is **无来源** -- sourceless, so it is not an attack by anybody and
#     nothing that reacts to being attacked should fire.
# The carrier can therefore kill itself, which is the point of the mechanic.
PERIODIC_SELF_DAMAGE_KEYS = frozenset({"periodic_damage.damage"})

# The same mechanic with the magnitude written the other way.  无人爆破艇
# `enemy_1164_bmbmot` (IS3 3-4, 8000 hp) ships `Bleed.hp_ratio` 0.04, and PRTS
# 「无人爆破艇」 states it as 「每秒流失最大生命值×4%的生命值」 -- so a
# **ratio of max HP** per second where 狂暴宿主士兵 ships a flat 330.  Same
# cadence, same sourceless true damage, same self-killing consequence; only the
# magnitude form differs, so it rides the same runtime timer rather than a
# second one.
#
# PRTS also supplies the two clauses the in-stage description omits:
# 「不进行攻击」 (its `rangeRadius` ships 0.0, which is how the compiler already
# reads it) and 「免疫水蚀」.  The death explosion it is named for --
# 「200%攻击力物理溅射 + 170%蚀伤」 in radius 1.25 after a 1-second fuse -- has
# **no** shipped key on this carrier at all and stays unimplemented.
SELF_DECAY_RATIO_KEYS = frozenset({"Bleed.hp_ratio"})


PERIODIC_SELF_DAMAGE_COEFFICIENTS: dict[str, dict[str, int]] = {}


# Attack-leech.  IS3 carriers `enemy_1114_rgrdmn` (萨卡兹宿主百夫长, stage 4-6) and
# `_2` (卫巢百夫长, stages 4-6 / b-4): `vampire.heal_scale` 1.5 and
# `vampire.attack@max_target` 3.0, 「同时攻击三个目标;每次攻击回复自身生命值」.
#
# PRTS 「萨卡兹宿主百夫长」: 「成功造成伤害后，治疗自身相当于伤害量150%的生命值」 --
# so the heal is a multiple of **damage actually dealt**, not of the carrier's
# attack, and it lands once per attack cycle rather than once per target.
#
# `attack@max_target` is deliberately **not** in this family: hitting three
# targets at once is an enemy-side multi-target attack, which this engine does
# not have (`normal_attack_max_targets` exists on operators only).  Folding it
# in here would let the leech look complete while the carrier still hits one
# target and therefore heals for a third of what it should.
VAMPIRE_KEYS = frozenset({"vampire.heal_scale"})


VAMPIRE_COEFFICIENTS: dict[str, dict[str, int]] = {}


# Enemy-side multi-target attacks.  `enemy_1114_rgrdmn` / `_2` ship
# `vampire.attack@max_target` 3.0 and read 「同时攻击三个目标」.
#
# The engine's enemy attack path was already plumbed for this end to end -- it
# builds per-target damage with each target's own defence and emits one intent
# each -- but the count was pinned at 1 unless the carrier appeared in the
# `STANDBY_REVIVE_RANGED_MULTI_TARGET_IDS` allow-list.  This family drives the
# count from the data instead, which is the same lift as S2-b: an id list
# becomes a declared number.
MULTI_TARGET_KEYS = frozenset({"vampire.attack@max_target"})


MULTI_TARGET_COEFFICIENTS: dict[str, dict[str, int]] = {}


# The Deep Pool companion pair.  Two carriers that switch each other on:
#   深池伙友卫队   `enemy_1174_duholy` / `_2` (stages ev-6 / 5-4)
#       data: `traitAbility.range_radius` 1.1, `traitAbility.attack_speed` -30
#       PRTS: 「自身1.4半径范围内存在[深池伙友影刃]/[深池伙友影刃精英]时，使自身
#             半径1.1范围内所有我方单位攻击速度-30」
#   深池伙友影刃   `enemy_1175_dushdo` / `_2` (stages ev-6 / 5-4)
#       data: `traitAbility.base_attack_time` -1.2 / -1.3
#       PRTS: 「自身1.4半径范围内存在[深池伙友卫队]/[深池伙友卫队精英]时，自身
#             攻击间隔减少1.2s」
#
# Same trigger, different effects, and **the 1.4 pairing radius is nowhere in
# the data** -- only the effect radius 1.1 is.
#
# Mind the units: `attack_speed` is points against a 100 baseline (the same
# scale as 寒冷's -30), while `base_attack_time` is **seconds**.  One family,
# two scales -- the same shape of trap as `atkSpeedDown`'s fraction.
#
# Pairing needs no id table: the guard carries `range_radius`/`attack_speed` and
# the blade carries `base_attack_time`, so each is simply "an enemy carrying the
# complementary key" within the pairing radius.
TRAIT_ABILITY_GUARD_KEYS = frozenset(
    {"traitAbility.range_radius", "traitAbility.attack_speed"}
)


TRAIT_ABILITY_BLADE_KEYS = frozenset({"traitAbility.base_attack_time"})


TRAIT_ABILITY_KEYS = TRAIT_ABILITY_GUARD_KEYS | TRAIT_ABILITY_BLADE_KEYS


TRAIT_ABILITY_COEFFICIENTS: dict[str, dict[str, int]] = {}


#: PRTS-only: the radius within which the pair activates each other.
TRAIT_ABILITY_PAIRING_RADIUS_MILLI_TILES = 1400


# Elevated aggro.  `DeathRattle.taunt_level` = 1.0 on `enemy_1279_sgbdg`
# (家族恫吓者, stages 3-5 / t-3) and `_2` (强暴恫吓者, stage ev-5), whose
# descriptions open with 「能吸引敌人攻击自己」 -- it draws fire.
#
# The engine already orders targets by taunt, but only read the level from
# `createGhost.taunt_level`, i.e. from one id-specific mechanic.  This family
# generalises the same ordering to any carrier that declares a level.
#
# The `DeathRattle` prefix covers three unrelated mechanics -- see the ledger --
# and only the taunt half is wired here.
TAUNT_LEVEL_KEYS = frozenset({"DeathRattle.taunt_level"})


TAUNT_LEVEL_COEFFICIENTS: dict[str, dict[str, int]] = {}


# Death transform.  `enemy_1162_magmot` (术师快艇, stage 3-4) declares
# `DeathRattle.enemy_key` = "enemy_1161_tidmag" and reads
# 「搭载着控潮术师的快艇，...载具损毁后术师会继续作战」 -- the vehicle dies and the
# mage it carried keeps fighting from the same spot.
#
# The successor is named as a **string**, so it never becomes a numeric
# coefficient; the stage compiler lifts it into a typed field.  Stage 3-4 ships
# `enemy_1161_tidmag` in its own `enemyDbRefs`, so the successor's stat block is
# present and nothing has to be invented.
# Paired rage.  `enemy_2014_csicer` (雪牝) and `enemy_2015_csicem` (霜牡), stage
# ev-2, both ship `triggerrage.attack_speed` 100.0 and `triggerrage.move_speed`
# 2.0, and each description names the *other* as the trigger:
#   雪牝 「"霜牡"死亡时，自身攻击速度、移动速度大幅提升并继承其能力」
#   霜牡 「"雪牝"死亡时，...」
#
# PRTS 「霜牡」 gives the numbers and, importantly, a second trigger the unpacked
# text omits: 「'雪牝'**死亡/进入终点时**：自身攻击速度增加100，移动速度最终提升至
# 200%」 -- the survivor rages when its partner *leaves the board*, whether it
# died or reached the goal.  Letting a leak go unnoticed would leave the
# survivor permanently weak in exactly the run where the player let one through.
#
# Units differ again: `attack_speed` is points against 100, `move_speed` is an
# absolute multiplier (「提升至200%」, not "+200%").
#
# The pair needs no id table: both carriers ship identical `triggerrage` rows,
# so the partner is simply the other carrier of the same family.
RAGE_KEYS = frozenset({"triggerrage.attack_speed", "triggerrage.move_speed"})


RAGE_COEFFICIENTS: dict[str, dict[str, int]] = {}


DEATH_TRANSFORM_BLACKBOARD_KEY = "DeathRattle.enemy_key"


def death_transform_enemy_id(blackboard: object) -> str | None:
    """Read the successor's id out of a shipped ``talentBlackboard`` list."""

    if not isinstance(blackboard, list):
        return None
    for entry in blackboard:
        if not isinstance(entry, dict):
            continue
        if entry.get("key") != DEATH_TRANSFORM_BLACKBOARD_KEY:
            continue
        successor = entry.get("valueStr")
        if isinstance(successor, str) and successor:
            return successor
    return None


#: 「首次受伤时制造一个**全息幻影**」 -- 「红标」渗透者 ships the phantom's own id
#: as a **string** under this key, which is why it never appears in the numeric
#: coefficient projection and why the engine reproduced it from a hand-written
#: map instead.  The map and the data agreed, but only by hand.
HOLOGRAM_BLACKBOARD_KEY = "createGhost.enemy_key"


def hologram_enemy_id(blackboard: object) -> str | None:
    """Read the phantom's id out of a shipped ``talentBlackboard`` list.

    Same shape as `death_transform_enemy_id`, and for the same reason: a string
    value cannot become a number, so it is lifted to a named field at compile
    time rather than guessed from the carrier's own id.
    """

    if not isinstance(blackboard, list):
        return None
    for entry in blackboard:
        if not isinstance(entry, dict):
            continue
        if entry.get("key") != HOLOGRAM_BLACKBOARD_KEY:
            continue
        phantom = entry.get("valueStr")
        if isinstance(phantom, str) and phantom:
            return phantom
    return None


#: 「首次受伤时制造一个**全息幻影**」 -- 「红标」渗透者 ships the phantom's own id
#: as a **string** under this key, which is why it never appears in the numeric
#: coefficient projection and why the engine reproduced it from a hand-written
#: map instead.  The map and the data agreed, but only by hand.
HOLOGRAM_BLACKBOARD_KEY = "createGhost.enemy_key"


def hologram_enemy_id(blackboard: object) -> str | None:
    """Read the phantom's id out of a shipped ``talentBlackboard`` list.

    Same shape as `death_transform_enemy_id`, and for the same reason: a string
    value cannot become a number, so it is lifted to a named field at compile
    time rather than guessed from the carrier's own id.
    """

    if not isinstance(blackboard, list):
        return None
    for entry in blackboard:
        if not isinstance(entry, dict):
            continue
        if entry.get("key") != HOLOGRAM_BLACKBOARD_KEY:
            continue
        phantom = entry.get("valueStr")
        if isinstance(phantom, str) and phantom:
            return phantom
    return None


#: The two 国度 grids IS3 ships, frozen from `range_table.json` because a range
#: name cannot become a number and the compiler has no range table to hand.
#: Keyed by the **shipped range id** rather than by carrier, which is the whole
#: point: 「邪魔的利刃」 (b-1) ships `x-4` and its 游荡者 (b-1-b) ships `x-3`, and
#: its own description says why -- 「持续在自身中心**大范围**生成【国度】」.
BLACK_FOG_RANGE_OFFSETS: dict[str, tuple[tuple[int, int], ...]] = {
    # x-4: the 3x3 block.
    "x-4": tuple(
        (col, row) for col in (-1, 0, 1) for row in (-1, 0, 1)
    ),
    # x-3: the 25-tile diamond of radius 3 -- nearly three times the area.
    "x-3": tuple(
        (col, row)
        for col in range(-3, 4)
        for row in range(-3, 4)
        if abs(col) + abs(row) <= 3
    ),
}

#: 「持续在自身中心一定范围生成【国度】」 -- the key that names which grid.
BLACK_FOG_RANGE_KEY = "BlackFog.range_id"


def black_fog_range_offsets(blackboard: object) -> tuple[tuple[int, int], ...]:
    """Read the 国度's grid out of a shipped ``talentBlackboard`` list.

    An unknown range name is refused rather than defaulted: guessing a grid for
    a boss whose whole mechanic *is* that grid would be worse than saying so.
    """

    if not isinstance(blackboard, list):
        return ()
    for entry in blackboard:
        if not isinstance(entry, dict) or entry.get("key") != BLACK_FOG_RANGE_KEY:
            continue
        name = entry.get("valueStr")
        if not isinstance(name, str) or not name:
            continue
        offsets = BLACK_FOG_RANGE_OFFSETS.get(name)
        if offsets is None:
            raise ValueError("black fog ships an unknown range id %r" % (name,))
        return offsets
    return ()


def validate_death_transform(content: ContentBundle) -> bool:
    """Require the successor's definition to travel with the carrier.

    A transform whose successor is missing would look implemented and quietly
    do nothing, so an absent definition is refused rather than skipped.
    """

    known = {enemy.enemy_id for enemy in content.enemies}
    handled = False
    for enemy in content.enemies:
        successor = enemy.death_transform_enemy_id
        if successor is None:
            continue
        if successor not in known:
            raise ValueError(
                "death transform names %r, which this bundle does not define; "
                "%s would die into nothing" % (successor, enemy.enemy_id)
            )
        if successor == enemy.enemy_id:
            raise ValueError(
                "death transform on %s names itself, which would never end"
                % enemy.enemy_id
            )
        handled = True
    return handled


# Damage bonus against frozen targets.  IS3 carriers `enemy_2014_csicer` (雪牝)
# and `enemy_2015_csicem` (霜牡), stage ev-2, both at 2.5.
# PRTS 「霜牡」 天赋&能力: 「自身对冻结目标造成伤害时攻击倍率提升至250%」 -- so 2.5
# is the resulting multiplier, not a bonus to add, and the condition is being
# **frozen** specifically rather than merely held: hence the separate
# `coefficient_frozen_remaining_ticks` marker.
FROZEN_TARGET_DAMAGE_KEYS = frozenset({"damagescaletofrozen.atk_scale"})


FROZEN_TARGET_DAMAGE_COEFFICIENTS: dict[str, dict[str, int]] = {}


# Refraction's magic-resistance bonus.  9 IS3 enemy variants ship
# `refracting.magic_resistance` = 70.0, and 深池方阵步兵/指挥官 say so in words:
# 「折射生效时，法术抗性增加70」.
#
# PRTS 「深池方阵战士」 settles the trigger that the unpacked text leaves open:
# refraction is 「自动触发」 and lasts 「生效期间永久」 (interruptible only by
# silence).  So an unconditional bonus is the faithful reading, not an
# approximation -- this repo had previously registered the missing "is
# refraction active" switch as a fidelity gap, which was a misreading.
#
# What *was* genuinely missing: ``EnemyDefinition`` carries a typed
# ``refracting_magic_resistance_bonus`` that the stage compiler never fills, so
# every compiled IS3 bundle left it at 0 and the bonus never applied to the
# carriers that ship it.  Wiring the key here makes the value source-derived.
REFRACTING_KEYS = frozenset({"refracting.magic_resistance"})


REFRACTING_COEFFICIENTS: dict[str, dict[str, int]] = {}


def validate_incoming_damage_scale_coefficients(content: ContentBundle) -> bool:
    """Authorize incoming-damage-scale rows on provenance alone."""

    handled = False
    for enemy in content.enemies:
        if _authorize_family_rows(
            enemy,
            INCOMING_DAMAGE_SCALE_KEYS,
            INCOMING_DAMAGE_SCALE_COEFFICIENTS,
            "incoming damage scale",
        ):
            handled = True
    return handled




def validate_attack_control_coefficients(content: ContentBundle) -> bool:
    """Authorize attack-borne control rows on provenance alone."""

    keys = frozenset(ATTACK_CONTROL_KEYS)
    handled = False
    for enemy in content.enemies:
        if _authorize_family_rows(
            enemy, keys, ATTACK_CONTROL_COEFFICIENTS, "attack-borne control"
        ):
            handled = True
    return handled


def validate_invulnerability_coefficients(content: ContentBundle) -> bool:
    """Authorize declared-invulnerability rows on provenance alone."""

    handled = False
    for enemy in content.enemies:
        if _authorize_family_rows(
            enemy, INVULNERABILITY_KEYS, INVULNERABILITY_COEFFICIENTS, "declared invulnerability"
        ):
            handled = True
    return handled


def validate_rear_hit_resistance_coefficients(content: ContentBundle) -> bool:
    """Authorize directional damage resistance, and require its side.

    The magnitude is in the data; the protected side is only in the description,
    and it is not the same side for every carrier.  A carrier that arrives
    without a side is refused rather than defaulted, because defaulting to one
    side silently inverts the mechanic for the carriers protected on the other.
    """

    handled = False
    for enemy in content.enemies:
        if _authorize_family_rows(
            enemy,
            REAR_HIT_RESISTANCE_KEYS,
            REAR_HIT_RESISTANCE_COEFFICIENTS,
            "rear-hit resistance",
        ):
            if enemy.weakness_protected_side is None:
                raise ValueError(
                    "directional damage resistance needs its protected side; "
                    "%s carries the magnitude alone" % enemy.enemy_id
                )
            handled = True
    return handled


def validate_freeze_aura_coefficients(content: ContentBundle) -> bool:
    """Authorize freeze-aura rows on provenance, and require the full triple.

    A partial triple is rejected rather than defaulted: without ``interval``
    there is no trigger, and without ``range_radius`` there is no extent, so a
    guessed value would be an invented mechanism rather than a calibration gap.
    """

    handled = False
    for enemy in content.enemies:
        if _authorize_family_rows(
            enemy,
            FREEZE_AURA_KEYS,
            FREEZE_AURA_COEFFICIENTS,
            "freeze aura",
        ):
            carried = {
                coefficient.key
                for coefficient in enemy.mechanic_coefficients
                if coefficient.key in FREEZE_AURA_KEYS
            }
            if carried != FREEZE_AURA_KEYS:
                raise ValueError(
                    "freeze aura requires interval, range_radius and freeze; "
                    "%s carries only %s" % (enemy.enemy_id, sorted(carried))
                )
            handled = True
    return handled


def validate_rush_acceleration_coefficients(content: ContentBundle) -> bool:
    """Authorize charge-acceleration ramps, requiring the whole ramp.

    Provenance is checked the same way as for the fixed-key families; the keys
    are matched by shape because they carry the carrier's own name.
    """

    handled = False
    for enemy in content.enemies:
        rows = {
            coefficient.key: coefficient
            for coefficient in enemy.mechanic_coefficients
            # The rush family specifically: `patterned_coefficient_leaf`
            # answers for every patterned family, and the splash key's leaf is
            # also `atk_scale`, so using it here made this validator demand the
            # ramp's three leaves from 爆破攻坚手 and refuse three real stages.
            if rush_pattern_leaf(coefficient.key) is not None
        }
        if not rows:
            continue
        prefix = "enemy_database.json#/"
        for key, coefficient in sorted(rows.items()):
            pointer = coefficient.source_pointer
            if not pointer.startswith(prefix):
                continue
            owner = pointer[len(prefix) :].split("/", 1)[0]
            if owner != enemy.enemy_id:
                raise ValueError(
                    f"rush acceleration coefficient {key!r} on {enemy.enemy_id!r} cites "
                    f"another enemy's database entry ({owner!r})"
                )
        leaves = {rush_pattern_leaf(key) for key in rows} - {"predelay"}
        if leaves != RUSH_REQUIRED_LEAVES:
            raise ValueError(
                "rush acceleration requires move_speed, interval and trig_cnt; "
                "%s carries only %s" % (enemy.enemy_id, sorted(leaves))
            )
        handled = True
    return handled


def validate_defence_aura_coefficients(content: ContentBundle) -> bool:
    """Authorize additive defence auras on provenance.

    ``defup.range_radius`` is accepted on its own: ``enemy_1042_frostd``
    carries the radius with no ``defup.def``, and PRTS 「寒霜」 states it has no
    defence aura at all -- the radius there belongs to its attack-speed debuff
    (「使2.5半径范围内的所有我方单位攻击速度-50%」).  So the key prefix does not
    decide the effect, and requiring the pair would reject shipped data.
    A carrier that *does* ship ``defup.def`` must ship the radius with it,
    because a guessed radius would be an invented mechanism.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy,
            DEFENCE_AURA_KEYS,
            DEFENCE_AURA_COEFFICIENTS,
            "defence aura",
        ):
            continue
        carried = {
            coefficient.key
            for coefficient in enemy.mechanic_coefficients
            if coefficient.key in DEFENCE_AURA_KEYS
        }
        if "defup.def" in carried and "defup.range_radius" not in carried:
            raise ValueError(
                "defup defence aura requires its range_radius; "
                "%s ships the bonus alone" % enemy.enemy_id
            )
        handled = True
    return handled


def validate_charge_burst_coefficients(content: ContentBundle) -> bool:
    """Authorize the charge's extra blow, which needs the ramp to read a speed.

    PRTS gives the payload as 「当前移动速度x600」, so the coefficient is only
    meaningful alongside an acceleration ramp: without one there is no "current
    move speed" distinct from the base, and the burst would silently become a
    flat multiple of the base speed.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy,
            CHARGE_BURST_KEYS,
            CHARGE_BURST_COEFFICIENTS,
            "charge burst",
        ):
            continue
        if not rush_acceleration_rows(enemy):
            raise ValueError(
                "first blocked attack scales with the acceleration ramp; "
                "%s carries the scale without a ramp" % enemy.enemy_id
            )
        handled = True
    return handled


def validate_attack_speed_aura_coefficients(content: ContentBundle) -> bool:
    """Authorize the attack-speed aura, which needs a radius to be an aura.

    ``enemy_1138_tumor`` carries the same key at -0.8 with **no** radius, and its
    text is 「被阻挡时使阻挡单位攻击速度极大幅降低」 -- a blocker-only debuff, not a
    radius aura.  That delivery shape is not implemented and is not in IS3, so a
    radius-less carrier is refused rather than silently treated as an aura.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy,
            ATTACK_SPEED_AURA_KEYS,
            ATTACK_SPEED_AURA_COEFFICIENTS,
            "attack-speed aura",
        ):
            continue
        radius = {
            coefficient.key
            for coefficient in enemy.mechanic_coefficients
            if coefficient.key == "defup.range_radius"
        }
        if not radius:
            raise ValueError(
                "attack-speed aura requires defup.range_radius; %s carries the "
                "suppression without a radius (the blocker-only form is not "
                "implemented)" % enemy.enemy_id
            )
        handled = True
    return handled


def validate_frozen_target_damage_coefficients(content: ContentBundle) -> bool:
    """Authorize the frozen-target damage multiplier.

    Refuses a multiplier below 1.0: PRTS states 「攻击倍率提升至250%」, an increase,
    so a value under one would mean the key had been misread as a bonus fraction
    rather than the resulting multiplier.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy,
            FROZEN_TARGET_DAMAGE_KEYS,
            FROZEN_TARGET_DAMAGE_COEFFICIENTS,
            "frozen-target damage",
        ):
            continue
        for coefficient in enemy.mechanic_coefficients:
            if coefficient.key not in FROZEN_TARGET_DAMAGE_KEYS:
                continue
            if coefficient.value_micros < 1_000_000:
                raise ValueError(
                    "frozen-target damage is a resulting multiplier and cannot be "
                    "below 1.0; %s ships %d micros"
                    % (enemy.enemy_id, coefficient.value_micros)
                )
        handled = True
    return handled


def validate_refracting_coefficients(content: ContentBundle) -> bool:
    """Authorize refraction's magic-resistance bonus.

    Magic resistance is a percentage, so a bonus outside 0..100 points would
    mean the value had been read on the wrong scale.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, REFRACTING_KEYS, REFRACTING_COEFFICIENTS, "refraction"
        ):
            continue
        for coefficient in enemy.mechanic_coefficients:
            if coefficient.key not in REFRACTING_KEYS:
                continue
            if not 0 < coefficient.value_micros <= 100_000_000:
                raise ValueError(
                    "refraction magic resistance must be 0..100 points; "
                    "%s ships %d micros"
                    % (enemy.enemy_id, coefficient.value_micros)
                )
        handled = True
    return handled


def validate_boom_coefficients(content: ContentBundle) -> bool:
    """Authorize death explosions, refusing one whose extent is unknown.

    A blast needs a radius.  It may come from the enemy's own data
    (``boom.projectile_range`` / ``boom.range_radius``) or from the cited PRTS
    table; with neither, the carrier is refused rather than given a guessed
    radius, because the radius decides who is hit at all.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, BOOM_KEYS, BOOM_COEFFICIENTS, "death explosion"
        ):
            continue
        carried = {
            coefficient.key
            for coefficient in enemy.mechanic_coefficients
            if coefficient.key in BOOM_KEYS
        }
        if "boom.atk_scale" not in carried:
            raise ValueError(
                "death explosion requires boom.atk_scale; %s carries only %s"
                % (enemy.enemy_id, sorted(carried))
            )
        has_radius = bool(
            carried & {"boom.projectile_range", "boom.range_radius"}
        ) or enemy.enemy_id in BOOM_RADIUS_MILLI_TILES_BY_ENEMY
        if not has_radius:
            raise ValueError(
                "death explosion needs a radius, from the data or the cited PRTS "
                "table; %s supplies neither" % enemy.enemy_id
            )
        handled = True
    return handled


def validate_def_reduce_coefficients(content: ContentBundle) -> bool:
    """Authorize eroding resistance, and derive its per-stack step exactly.

    ``change1_stack_cnt`` / ``change2_stack_cnt`` are **cosmetic**: PRTS says
    「持有不低于15/45层时，出现不同特效」 -- different visual effects, no
    mechanical change -- so they are accepted and ignored rather than reported
    as an unimplemented gap.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, DEF_REDUCE_KEYS, DEF_REDUCE_COEFFICIENTS, "eroding resistance"
        ):
            continue
        total_def = coefficient_value(enemy, "def_reduce.def")
        total_mres = coefficient_value(enemy, "def_reduce.magic_resistance")
        max_stacks = coefficient_value(enemy, "def_reduce.max_stack_cnt")
        if total_def is None or total_mres is None or max_stacks is None:
            raise ValueError(
                "eroding resistance requires def, magic_resistance and "
                "max_stack_cnt; %s is missing one" % enemy.enemy_id
            )
        if total_def > 0 or total_mres > 0:
            raise ValueError(
                "eroding resistance reduces both stats; %s ships a positive "
                "value" % enemy.enemy_id
            )
        stacks = max_stacks // 1_000_000
        if stacks <= 0 or max_stacks % 1_000_000:
            raise ValueError(
                "eroding resistance stack count must be a whole number; "
                "%s ships %d micros" % (enemy.enemy_id, max_stacks)
            )
        for label, total in (("def", total_def), ("magic_resistance", total_mres)):
            if (-total // 1_000_000) % stacks:
                raise ValueError(
                    "eroding resistance %s total %d does not divide into %d "
                    "stacks for %s -- the per-stack step would be a rounded "
                    "guess" % (label, -total // 1_000_000, stacks, enemy.enemy_id)
                )
        handled = True
    return handled


def validate_self_decay_ratio_coefficients(content: ContentBundle) -> bool:
    """Authorize the ratio form of self-inflicted decay.

    A ratio of max HP, so it must be a real fraction: zero would be no decay
    and anything at or above one would kill the carrier on its first tick,
    either of which means the key was read on the wrong scale.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy,
            SELF_DECAY_RATIO_KEYS,
            {},
            "self decay ratio",
        ):
            continue
        for coefficient in enemy.mechanic_coefficients:
            if coefficient.key not in SELF_DECAY_RATIO_KEYS:
                continue
            if not 0 < coefficient.value_micros < 1_000_000:
                raise ValueError(
                    "self decay must be a fraction of max HP; %s ships %d micros"
                    % (enemy.enemy_id, coefficient.value_micros)
                )
            handled = True
    return handled


def validate_periodic_self_damage_coefficients(content: ContentBundle) -> bool:
    """Authorize self-inflicted damage over time.

    The value is a whole number of HP per second; a fractional one would mean
    the key had been read on the wrong scale.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy,
            PERIODIC_SELF_DAMAGE_KEYS,
            PERIODIC_SELF_DAMAGE_COEFFICIENTS,
            "periodic self damage",
        ):
            continue
        for coefficient in enemy.mechanic_coefficients:
            if coefficient.key not in PERIODIC_SELF_DAMAGE_KEYS:
                continue
            if coefficient.value_micros <= 0 or coefficient.value_micros % 1_000_000:
                raise ValueError(
                    "periodic self damage must be a whole positive amount; "
                    "%s ships %d micros"
                    % (enemy.enemy_id, coefficient.value_micros)
                )
        handled = True
    return handled


def validate_vampire_coefficients(content: ContentBundle) -> bool:
    """Authorize attack-leech, refusing a scale that cannot be a multiple.

    PRTS states 「相当于伤害量150%」, so the value is a multiplier on damage
    dealt; a non-positive one would mean the key had been misread.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, VAMPIRE_KEYS, VAMPIRE_COEFFICIENTS, "attack leech"
        ):
            continue
        for coefficient in enemy.mechanic_coefficients:
            if coefficient.key in VAMPIRE_KEYS and coefficient.value_micros <= 0:
                raise ValueError(
                    "attack leech multiplies damage dealt and must be positive; "
                    "%s ships %d micros"
                    % (enemy.enemy_id, coefficient.value_micros)
                )
        handled = True
    return handled


def validate_trait_ability_coefficients(content: ContentBundle) -> bool:
    """Authorize the companion pair, insisting each half is whole.

    A guard needs both its effect radius and its attack-speed figure; a blade
    needs its interval reduction.  A carrier that mixes the two halves is
    refused, because the pairing rule reads one half as the other's partner and
    a hybrid would pair with itself.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, TRAIT_ABILITY_KEYS, TRAIT_ABILITY_COEFFICIENTS, "companion pair"
        ):
            continue
        carried = {
            coefficient.key
            for coefficient in enemy.mechanic_coefficients
            if coefficient.key in TRAIT_ABILITY_KEYS
        }
        is_guard = bool(carried & TRAIT_ABILITY_GUARD_KEYS)
        is_blade = bool(carried & TRAIT_ABILITY_BLADE_KEYS)
        if is_guard and is_blade:
            raise ValueError(
                "companion pair halves are mutually exclusive; %s carries both"
                % enemy.enemy_id
            )
        if is_guard and carried & TRAIT_ABILITY_GUARD_KEYS != TRAIT_ABILITY_GUARD_KEYS:
            raise ValueError(
                "companion guard needs both range_radius and attack_speed; "
                "%s carries only %s" % (enemy.enemy_id, sorted(carried))
            )
        handled = True
    return handled


def validate_taunt_level_coefficients(content: ContentBundle) -> bool:
    """Authorize declared aggro levels.

    A taunt level is a whole tier; a fractional one would mean the key had been
    read on the wrong scale, and tiers are compared rather than summed so a
    rounding error would silently reorder every target choice.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, TAUNT_LEVEL_KEYS, TAUNT_LEVEL_COEFFICIENTS, "taunt level"
        ):
            continue
        for coefficient in enemy.mechanic_coefficients:
            if coefficient.key not in TAUNT_LEVEL_KEYS:
                continue
            if coefficient.value_micros < 0 or coefficient.value_micros % 1_000_000:
                raise ValueError(
                    "taunt level must be a whole non-negative tier; %s ships "
                    "%d micros" % (enemy.enemy_id, coefficient.value_micros)
                )
        handled = True
    return handled


def validate_rage_coefficients(content: ContentBundle) -> bool:
    """Authorize paired rage, requiring both halves of its payload.

    A carrier with only one of the two would rage into a half-applied state, so
    the pair of keys is required together; the move multiplier must also be at
    least 1.0, since 「提升至200%」 is an increase and a value below one would
    mean the key had been read as a bonus rather than the resulting multiple.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, RAGE_KEYS, RAGE_COEFFICIENTS, "paired rage"
        ):
            continue
        carried = {
            coefficient.key
            for coefficient in enemy.mechanic_coefficients
            if coefficient.key in RAGE_KEYS
        }
        if carried != RAGE_KEYS:
            raise ValueError(
                "paired rage requires both attack_speed and move_speed; %s "
                "carries only %s" % (enemy.enemy_id, sorted(carried))
            )
        multiplier = coefficient_value(enemy, "triggerrage.move_speed")
        if multiplier is None or multiplier < 1_000_000:
            raise ValueError(
                "paired rage move speed is a resulting multiple and cannot be "
                "below 1.0; %s ships %s" % (enemy.enemy_id, multiplier)
            )
        handled = True
    return handled


def validate_multi_target_coefficients(content: ContentBundle) -> bool:
    """Authorize a declared target count.

    The count indexes a list of targets, so a fractional or non-positive value
    would either truncate silently or disarm the attack altogether.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, MULTI_TARGET_KEYS, MULTI_TARGET_COEFFICIENTS, "multi-target attack"
        ):
            continue
        for coefficient in enemy.mechanic_coefficients:
            if coefficient.key not in MULTI_TARGET_KEYS:
                continue
            if coefficient.value_micros < 1_000_000 or coefficient.value_micros % 1_000_000:
                raise ValueError(
                    "multi-target count must be a whole number of at least one; "
                    "%s ships %d micros"
                    % (enemy.enemy_id, coefficient.value_micros)
                )
        handled = True
    return handled


# The Blacksteel energy system.  Three key families, one mechanism, and all of
# it on the enemy side -- which is the part the unpacked descriptions obscure,
# because they are written from the player's viewpoint and so call the enemy's
# own allies 「敌人」.
#
# Carriers, both stage 3-7:
#   `enemy_1356_egun` (匪帮射手) 「周期性为攻击范围内所有敌人充能。能量上限：20」
#   `enemy_1357_erob` (匪帮斧手) 「每2秒夺取附近所有地面敌人1点能量。能量上限：20」
#
# PRTS 「匪帮射手」: 「周期性停止移动，并为攻击范围内所有敌人持续提供能量」, charging
# **+1 per second**, cap **20**, and on reaching the cap the unit enters
# 【过载】 for **20 s** with 「攻击速度+120、防御力最终提升至200%、法术抗性+30」,
# after which the energy resets to zero.  Those three numbers are exactly the
# `over_energy` rows, and the cap is `blacksteel_energy.max_stack_cnt`.
#
# Note the mixed units once more: `attack_speed` and `magic_resistance` are
# points, while `def` is a **resulting multiple** (「提升至200%」).
ENERGY_KEYS = frozenset(
    {
        "blacksteel_energy.max_stack_cnt",
        "over_energy.duration",
        "over_energy.attack_speed",
        "over_energy.def",
        "over_energy.magic_resistance",
    }
)
ENERGY_COEFFICIENTS: dict[str, dict[str, int]] = {}

STEAL_ENERGY_KEYS = frozenset(
    {
        "steal_energy.interval",
        "steal_energy.range_radius",
        "steal_energy.steal_count",
    }
)
STEAL_ENERGY_COEFFICIENTS: dict[str, dict[str, int]] = {}

#: PRTS-only: the charger grants one energy per second.
ENERGY_CHARGE_PER_SECOND = 1

#: The charger role is stated only in the description -- both carriers hold the
#: same `over_energy` rows, so the data cannot tell charger from thief.  These
#: are the shipped phrasings; 「夺取」 (the thief) deliberately does not match.
def validate_energy_coefficients(content: ContentBundle) -> bool:
    """Authorize the energy pool and its overload payload.

    The overload payload is required whole: a carrier with a cap but no
    duration would fill its pool and then sit in an overload that never ends,
    which is worse than not modelling it at all.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, ENERGY_KEYS, ENERGY_COEFFICIENTS, "energy pool"
        ):
            continue
        carried = {
            coefficient.key
            for coefficient in enemy.mechanic_coefficients
            if coefficient.key in ENERGY_KEYS
        }
        if carried != ENERGY_KEYS:
            raise ValueError(
                "energy pool requires the cap and the whole overload payload; "
                "%s carries only %s" % (enemy.enemy_id, sorted(carried))
            )
        cap = coefficient_value(enemy, "blacksteel_energy.max_stack_cnt")
        if cap is None or cap <= 0 or cap % 1_000_000:
            raise ValueError(
                "energy cap must be a whole positive count; %s ships %s"
                % (enemy.enemy_id, cap)
            )
        defence = coefficient_value(enemy, "over_energy.def")
        if defence is None or defence < 1_000_000:
            raise ValueError(
                "overload defence is a resulting multiple and cannot be below "
                "1.0; %s ships %s" % (enemy.enemy_id, defence)
            )
        handled = True
    return handled


def validate_steal_energy_coefficients(content: ContentBundle) -> bool:
    """Authorize energy theft, which needs its period, reach and amount."""

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, STEAL_ENERGY_KEYS, STEAL_ENERGY_COEFFICIENTS, "energy theft"
        ):
            continue
        carried = {
            coefficient.key
            for coefficient in enemy.mechanic_coefficients
            if coefficient.key in STEAL_ENERGY_KEYS
        }
        if carried != STEAL_ENERGY_KEYS:
            raise ValueError(
                "energy theft requires interval, range_radius and steal_count; "
                "%s carries only %s" % (enemy.enemy_id, sorted(carried))
            )
        count = coefficient_value(enemy, "steal_energy.steal_count")
        if count is None or count <= 0 or count % 1_000_000:
            raise ValueError(
                "energy theft amount must be a whole positive count; %s ships %s"
                % (enemy.enemy_id, count)
            )
        handled = True
    return handled


# Two-mode attack: melee when blocked, a weaker four-target volley when not.
# Carriers `enemy_1256_lyacpa` (R-11突击动力装甲, stages 4-2 / 5-8) and `_2`
# (R-11a, stage t-4), whose unpacked description is only 「同时攻击4个目标」 and
# whose single row is `Passive.attack@ranged.atk_scale` 0.5.
#
# PRTS 「R-11突击动力装甲」 supplies the whole rule and the condition the key name
# only hints at: 「未被阻挡时进行远程途径攻击，造成攻击力50%的物理伤害，可同时攻击
# 4个目标，不会攻击飞行单位」 -- and, when blocked, an ordinary 100% melee hit.
#
# So the 0.5 and the four targets are **both** conditional on being unblocked;
# applying either unconditionally would misstate the carrier in the half of its
# life it spends held.  The count of four is PRTS-only, hence the cited table.
RANGED_MODE_KEYS = frozenset({"Passive.attack@ranged.atk_scale"})
RANGED_MODE_COEFFICIENTS: dict[str, dict[str, int]] = {}

#: PRTS-only: how many targets the unblocked volley covers.
RANGED_MODE_TARGETS_BY_ENEMY = {
    "enemy_1256_lyacpa": 4,
    "enemy_1256_lyacpa_2": 4,
}


def validate_ranged_mode_coefficients(content: ContentBundle) -> bool:
    """Authorize the unblocked volley, refusing a carrier with no known count.

    The scale alone would make the carrier weaker with no compensating breadth,
    so a carrier whose target count is neither in the data nor in the cited
    table is refused rather than quietly nerfed.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, RANGED_MODE_KEYS, RANGED_MODE_COEFFICIENTS, "ranged volley"
        ):
            continue
        scale = coefficient_value(enemy, "Passive.attack@ranged.atk_scale")
        if scale is None or not 0 < scale <= 1_000_000:
            raise ValueError(
                "ranged volley scale is a fraction of attack at most 1.0; "
                "%s ships %s" % (enemy.enemy_id, scale)
            )
        if enemy.enemy_id not in RANGED_MODE_TARGETS_BY_ENEMY:
            raise ValueError(
                "ranged volley needs a target count; %s has none in the data "
                "and none in the cited table" % enemy.enemy_id
            )
        handled = True
    return handled


# Elemental damage.  Ten IS3 carriers across five spellings of one concept:
#   `EpDamage.attack@ep_damage_ratio`   控潮术师 0.8 / 术师快艇 0.8 (3-4),
#                                       无胄盟第三小队 0.35 / 清扫小队 0.35 /
#                                       清扫小队组长 0.35 (b-7-b, ev-7)
#   `epdamage.attack@ep_damage_ratio`   净浊之焰 1.0 (ev-6)   -- lower case
#   `Boom.ep_damage_ratio`              无人爆破艇 1.7 (3-4)  -- on its blast
#   `aura.ep_damage_ratio`              深池逐火精锐战士/护卫 1.0 (5-5)
#   `combat.attack@ep_damage_ratio`     骇笑看客 0.2 (ev-2)
#
# PRTS 「元素」: each element has its own gauge, default maximum **1000**, and
# the amount that lands is 「数值 x (1 - 元素抗性 x 0.01)」.  Filling the gauge
# bursts it, applies the payload, and starts a cooldown.  Player-side payloads:
#   神经损伤  1000 true damage + stun 10 s
#   侵蚀损伤  permanent -100 defence + 800 physical damage
#   灼燃损伤  magic resistance -20 for 10 s + 1200 arts damage
#
# The ratio says how much of a hit converts; the **type is only in the
# description**, so it is read from there.
ELEMENTAL_RATIO_KEYS = frozenset(
    {
        "EpDamage.attack@ep_damage_ratio",
        "epdamage.attack@ep_damage_ratio",
        "Boom.ep_damage_ratio",
        "aura.ep_damage_ratio",
        "combat.attack@ep_damage_ratio",
    }
)
ELEMENTAL_RATIO_COEFFICIENTS: dict[str, dict[str, int]] = {}

#: PRTS: the default gauge maximum shared by every element type.
ELEMENTAL_GAUGE_MAX = 1000

#: Ticks of burst cooldown before a gauge may fill again.  PRTS states the
#: cooldown for the *enemy-facing* direction (8 s for erosion, 10 s for neural
#: and burn) but does not give a number for bursts landing on our own units, so
#: 10 s is a registered default rather than a quoted figure.
ELEMENTAL_BURST_COOLDOWN_TICKS = 300

#: Player-side burst payloads, per PRTS.  The resistance and defence modifiers
#: are separate from the damage because they outlive the instant of the burst.
ELEMENTAL_BURST_TRUE_DAMAGE = {"neural": 1000, "erosion": 0, "burn": 0}
ELEMENTAL_BURST_PHYSICAL_DAMAGE = {"neural": 0, "erosion": 800, "burn": 0}
ELEMENTAL_BURST_ARTS_DAMAGE = {"neural": 0, "erosion": 0, "burn": 1200}
ELEMENTAL_BURST_STUN_SECONDS = {"neural": 10, "erosion": 0, "burn": 0}
ELEMENTAL_PERMANENT_DEFENCE_LOSS = {"neural": 0, "erosion": 100, "burn": 0}

#: The phrases that name each element type in a carrier's description.
def validate_elemental_ratio_coefficients(content: ContentBundle) -> bool:
    """Authorize elemental conversion, and require a known element type.

    A ratio with no type would accumulate into no gauge at all -- the mechanism
    would read as implemented and do nothing -- so the type is required.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy,
            ELEMENTAL_RATIO_KEYS,
            ELEMENTAL_RATIO_COEFFICIENTS,
            "elemental conversion",
        ):
            continue
        for coefficient in enemy.mechanic_coefficients:
            if coefficient.key not in ELEMENTAL_RATIO_KEYS:
                continue
            if coefficient.value_micros <= 0:
                raise ValueError(
                    "elemental ratio must be positive; %s ships %d micros"
                    % (enemy.enemy_id, coefficient.value_micros)
                )
        if enemy.element_damage_type is None:
            raise ValueError(
                "elemental conversion needs its element type, which is stated "
                "only in the description; %s has none" % enemy.enemy_id
            )
        handled = True
    return handled





# 歪曲施法者 `enemy_2044_smwiz`: 「遭邪魔置换的萨米萨满，一定时间不攻击后移动速度
# **大幅提升**」 with `SpeedUp.attack@duration` 5.0 and `SpeedUp.attack@move_speed`
# 4.0 (it also ships the bare `SpeedUp.duration`/`move_speed` at the same two
# values -- the shipped table says it twice).
#
# The bare keys already carry a *different* mechanism on `enemy_2043_smsbr`
# (「受到伤害后移动速度短暂大幅提升」, duration 2.0 / move_speed 3.0 / cooldown 5.0),
# and the two are told apart by what accompanies them: the damage trigger
# requires `SpeedUp.cooldown`, which 歪曲施法者 does not ship.  Same key name,
# two mechanisms, discriminated by provenance -- the same shape as `atkup.atk`.
IDLE_SPEED_KEYS = frozenset(
    {"SpeedUp.attack@duration", "SpeedUp.attack@move_speed"}
)

IDLE_SPEED_COEFFICIENTS: dict[str, dict[str, int]] = {}


def idle_speed_rows(enemy) -> dict[str, int] | None:
    """The idle-speed pair, or None when this carrier has no idle boost."""

    from .coefficient_provenance import coefficient_value
    from .engine import UnsupportedMechanicError

    duration = coefficient_value(enemy, "SpeedUp.attack@duration")
    multiple = coefficient_value(enemy, "SpeedUp.attack@move_speed")
    if duration is None and multiple is None:
        return None
    if duration is None or multiple is None:
        raise UnsupportedMechanicError(
            "the idle speed boost needs both SpeedUp.attack@duration and "
            "SpeedUp.attack@move_speed; one alone is not the shipped shape"
        )
    return {"duration": duration, "multiple": multiple}


def validate_idle_speed_coefficients(content: ContentBundle) -> bool:
    """Authorize the idle-speed boost, requiring both halves.

    The figure is a **bonus fraction**: 歪曲施法者's PRTS says 「移动速度**+400%**」
    and the key ships **4.0**.  It was read as a resulting multiple once, which
    left the carrier at four fifths of its real speed; the sibling
    `SpeedUp.move_speed` had the same error and the same correction.

    A bonus therefore has no floor at 1.0 -- only a positive one is required,
    since a zero or negative bonus would not be a speed *boost* at all.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, IDLE_SPEED_KEYS, IDLE_SPEED_COEFFICIENTS, "idle speed boost"
        ):
            continue
        rows = idle_speed_rows(enemy)
        assert rows is not None
        if rows["multiple"] <= 0:
            raise ValueError(
                "the idle speed bonus must be positive; %s ships %d micros"
                % (enemy.enemy_id, rows["multiple"])
            )
        if rows["duration"] <= 0:
            raise ValueError(
                "the idle speed boost needs a positive idle window; %s ships "
                "%d micros" % (enemy.enemy_id, rows["duration"])
            )
        handled = True
    return handled


#: 家族专用轿车 「被击倒时若已被阻挡，将卸下三个家族士兵」.  `DeathRattle.attack@cnt`
#: carries only the count (3); the passenger's id is not in the key, so it is
#: resolved from the **name written in the description**.  That is exact rather
#: than a guess: 「家族士兵」 matches one enemy name in the whole shipped table
#: (`enemy_1277_sgman`), and the one IS3 stage carrying the car (t-3) also
#: carries that enemy.
DEATH_UNLOAD_COUNT_KEY = "DeathRattle.attack@cnt"


def death_unload_count(blackboard: object) -> int:
    """How many passengers this carrier unloads when killed while blocked."""

    if not isinstance(blackboard, list):
        return 0
    for entry in blackboard:
        if not isinstance(entry, dict) or entry.get("key") != DEATH_UNLOAD_COUNT_KEY:
            continue
        value = entry.get("value")
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return 0


def death_unload_enemy_id(
    description: object, names_to_ids: dict[str, str]
) -> str | None:
    """The passenger named in the description, resolved to an enemy id.

    The description marks the unit with rich-text emphasis
    (「将卸下三个<@eb.key>家族士兵</>」), which is exactly the span that names it.
    A name matching no enemy, or more than one, returns None rather than a
    guess -- the mechanism then stays unimplemented instead of unloading the
    wrong unit.
    """

    if not isinstance(description, str) or not description:
        return None
    import re

    matches = {
        names_to_ids[name]
        for name in re.findall(r"<@eb\.key>([^<]+)</>", description)
        if name in names_to_ids
    }
    if len(matches) != 1:
        return None
    return next(iter(matches))


# 深池逐火精锐战士 / 精锐护卫 (`enemy_1288_duskls_2`, `enemy_1292_duskld_2`, stage
# 5-5).  The shipped description only says 「倒下后变为暴怒的余烬，一段时间后重生」;
# PRTS 「深池逐火精锐战士」 gives the whole rule:
#
#   战士形态 「被击倒后重生，持续1s，随后变为暴怒的余烬，1s内不移动且持有[无敌]」
#   暴怒的余烬 「基础最大生命值临时变为5（退出此模式后恢复）」
#              「[特殊生命值机制] 成功受到伤害，应用生命值变化时令生命值只降低1点
#                （不论伤害多少）」
#              「不进行攻击，获得隐匿」
#              「10s后若未被击倒则变回战士形态并恢复所有生命」
#
# So `Revive[Trigger].prop_max_hp` is the ember's **absolute** max HP -- 5, not a
# ratio and not a percentage of 6000.  With one point lost per hit, 5 means
# **five separate hits inside ten seconds**; reading 5.0 as 500% or 5% gives a
# different and equally plausible-looking mechanism.
EMBER_REVIVE_KEYS = frozenset(
    {"Revive[Trigger].interval", "Revive[Trigger].prop_max_hp"}
)

EMBER_REVIVE_COEFFICIENTS: dict[str, dict[str, int]] = {}

#: 「被击倒后重生，持续1s」 -- the invincible, motionless transition.
EMBER_TRANSITION_SECONDS = 1


def ember_revive_rows(enemy) -> dict[str, int] | None:
    """The ember form's window and hit count, or None."""

    from .coefficient_provenance import coefficient_value
    from .engine import UnsupportedMechanicError

    interval = coefficient_value(enemy, "Revive[Trigger].interval")
    hits = coefficient_value(enemy, "Revive[Trigger].prop_max_hp")
    if interval is None and hits is None:
        return None
    if interval is None or hits is None:
        raise UnsupportedMechanicError(
            "the ember revive needs both Revive[Trigger].interval and "
            "Revive[Trigger].prop_max_hp; one alone is not the shipped shape"
        )
    return {"interval": interval, "hits": hits}


def validate_ember_revive_coefficients(content: ContentBundle) -> bool:
    """Authorize the ember revive, requiring both halves and whole figures."""

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, EMBER_REVIVE_KEYS, EMBER_REVIVE_COEFFICIENTS, "ember revive"
        ):
            continue
        rows = ember_revive_rows(enemy)
        assert rows is not None
        if rows["hits"] % 1_000_000 or rows["hits"] <= 0:
            raise ValueError(
                "the ember's max HP is a whole number of points; %s ships %s "
                "micros" % (enemy.enemy_id, rows["hits"])
            )
        if rows["interval"] <= 0:
            raise ValueError(
                "the ember revive needs a positive window; %s ships %s micros"
                % (enemy.enemy_id, rows["interval"])
            )
        handled = True
    return handled


# 特工 / 特工组长 (`enemy_1049_eagent`, `_2`): 「手持的武器在攻击时会短暂**减少我方
# 单位的阻挡上限**，可以冲破我方防线。」  `ReduceBlockCnt.block_cnt` is -1 and
# `ReduceBlockCnt.duration` is 5 s (10 s on the leader).
#
# `EnemyDefinition` already declared `on_attack_block_count_delta` and
# `on_attack_block_count_duration_ticks` for this -- and **nothing anywhere read
# either one**, nor were the keys in the coefficient registry, so the mechanism
# was absent while looking provided for.  The reader below fills those fields
# from the shipped keys so the shell gets a body.
REDUCE_BLOCK_KEYS = frozenset(
    {"ReduceBlockCnt.block_cnt", "ReduceBlockCnt.duration"}
)


def reduce_block_rows(blackboard: object) -> tuple[int, float] | None:
    """``(block_cnt, duration_seconds)`` from a shipped talentBlackboard.

    Read straight off the source list rather than through the coefficient path,
    because the destination is a typed field on the definition: the engine
    consumes the field, and the key never needs to become a coefficient.
    """

    if not isinstance(blackboard, list):
        return None
    delta: float | None = None
    duration: float | None = None
    for entry in blackboard:
        if not isinstance(entry, dict):
            continue
        if entry.get("key") == "ReduceBlockCnt.block_cnt":
            value = entry.get("value")
            if isinstance(value, (int, float)):
                delta = float(value)
        elif entry.get("key") == "ReduceBlockCnt.duration":
            value = entry.get("value")
            if isinstance(value, (int, float)):
                duration = float(value)
    if delta is None and duration is None:
        return None
    if delta is None or duration is None or delta >= 0 or duration <= 0:
        # A half shape, or a "reduction" that does not reduce.  Left alone: a
        # guessed second half would silently change how many enemies slip past.
        return None
    return int(delta), duration


# 食腐使徒 `enemy_1266_nhapos`: 「从汲营枯枝出现时，被击倒过的敌人越多，生命、攻击和
# 防御会越强；释放<@eb.key>污染秽蚀</>，使周围我方持续损失生命」.  **Two mechanisms
# in one description**, and the five `PollutedArea.*` keys split between them:
#
#   the aura      -- `Polluted_damage_low` 50, `Polluted_damage_high` 25,
#                    `range_Radius` 1.1
#   the scaling   -- `guaranteed_percentage` 0.2, `max_percentage` 0.8
#                    (「被击倒过的敌人越多…越强」, not implemented here)
#
# `low`/`high` are **ground heights, not a damage range**: the already-shipped
# `PollutedZoneState` calls the same two shipped names `low_ground_damage` and
# `high_ground_damage`, so this reuses that reading rather than inventing a
# second one.  The casing differs from the death variant's
# `PollutedDie.polluted_damage_low` -- capital P and capital R here -- which is
# why this needs its own reader rather than the shared prefix helper.
POLLUTION_AURA_KEYS = frozenset(
    {
        "PollutedArea.Polluted_damage_low",
        "PollutedArea.Polluted_damage_high",
        "PollutedArea.range_Radius",
    }
)

POLLUTION_AURA_COEFFICIENTS: dict[str, dict[str, int]] = {}


def pollution_aura_rows(enemy) -> dict[str, int] | None:
    """The aura's radius and its two ground-height damages, or None."""

    from .coefficient_provenance import coefficient_value
    from .engine import UnsupportedMechanicError

    radius = coefficient_value(enemy, "PollutedArea.range_Radius")
    low = coefficient_value(enemy, "PollutedArea.Polluted_damage_low")
    high = coefficient_value(enemy, "PollutedArea.Polluted_damage_high")
    if radius is None and low is None and high is None:
        return None
    if radius is None or low is None or high is None:
        raise UnsupportedMechanicError(
            "the pollution aura needs a radius and both ground-height damages; "
            "a partial set is not the shipped shape"
        )
    return {"radius": radius, "low": low, "high": high}


def validate_pollution_aura_coefficients(content: ContentBundle) -> bool:
    """Authorize the pollution aura, requiring whole HP figures."""

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, POLLUTION_AURA_KEYS, POLLUTION_AURA_COEFFICIENTS, "pollution aura"
        ):
            continue
        rows = pollution_aura_rows(enemy)
        assert rows is not None
        for name in ("low", "high"):
            if rows[name] <= 0 or rows[name] % 1_000_000:
                raise ValueError(
                    "pollution aura damage must be whole positive HP; %s ships "
                    "%s micros for %s" % (enemy.enemy_id, rows[name], name)
                )
        if rows["radius"] <= 0:
            raise ValueError(
                "the pollution aura needs a positive radius; %s ships %s micros"
                % (enemy.enemy_id, rows["radius"])
            )
        handled = True
    return handled


# 「遗弃者」 `enemy_2005_axetro`: 「一位被源石严重侵蚀的保安，<@eb.key>连续攻击</>时会
# 提高攻击力和攻击速度，4秒不攻击后<@eb.key>清空</>」.  Four keys, all four needed:
#
#   atkup.atk            0.07  -- +7% attack per stack
#   atkup.attack_speed   5.0   -- +5 attack-speed points per stack
#   atkup.max_stack_cnt  28.0  -- the ceiling (so x2.96 attack, +140 points)
#   checker.delay        4.0   -- seconds of not attacking that clears it
#
# `atkup.atk` is shared with a *different* mechanism -- 「该目标处于低体力时似乎会
# 大幅度提升作战意志」, a half-health boost carried by four other enemies at 1.8
# and 1.0 -- and the engine already tells the two apart by whether
# `atkup.max_stack_cnt` accompanies it.  That discriminator is reused here
# rather than invented: a carrier with the ceiling stacks, one without does not.
ATTACK_STACK_KEYS = frozenset(
    {
        "atkup.atk",
        "atkup.attack_speed",
        "atkup.max_stack_cnt",
        "checker.delay",
    }
)

ATTACK_STACK_COEFFICIENTS: dict[str, dict[str, int]] = {}


def attack_stack_rows(enemy) -> dict[str, int] | None:
    """The stacking buff's four figures, or None when this is not that carrier.

    Returns None -- not an error -- when `atkup.max_stack_cnt` is absent, since
    that is exactly the half-health carrier's shape and it must keep working.
    """

    from .coefficient_provenance import coefficient_value
    from .engine import UnsupportedMechanicError

    ceiling = coefficient_value(enemy, "atkup.max_stack_cnt")
    if ceiling is None:
        return None
    attack = coefficient_value(enemy, "atkup.atk")
    attack_speed = coefficient_value(enemy, "atkup.attack_speed")
    delay = coefficient_value(enemy, "checker.delay")
    if attack is None or attack_speed is None or delay is None:
        raise UnsupportedMechanicError(
            "the stacking attack buff needs atkup.atk, atkup.attack_speed, "
            "atkup.max_stack_cnt and checker.delay together; a partial set is "
            "not the shipped shape"
        )
    return {
        "attack": attack,
        "attack_speed": attack_speed,
        "ceiling": ceiling,
        "delay": delay,
    }


def validate_attack_stack_coefficients(content: ContentBundle) -> bool:
    """Authorize the stacking buff, requiring a whole positive ceiling."""

    handled = False
    for enemy in content.enemies:
        rows = attack_stack_rows(enemy)
        if rows is None:
            continue
        if rows["ceiling"] % 1_000_000 or rows["ceiling"] <= 0:
            raise ValueError(
                "the stack ceiling is a whole positive count; %s ships %s micros"
                % (enemy.enemy_id, rows["ceiling"])
            )
        if rows["delay"] <= 0:
            raise ValueError(
                "the stacking buff needs a positive clear delay; %s ships %s"
                % (enemy.enemy_id, rows["delay"])
            )
        handled = True
    return handled


def validate_splash_coefficients(content: ContentBundle) -> bool:
    """Authorize 爆破攻坚手's splash.

    The key is patterned (`aoe.attack@<carrier>.atk_scale`), so it can never
    appear in the exact `RUNTIME_COEFFICIENT_KEYS` set that the block-constraint
    allow-list checks against -- a patterned family passes that gate only by
    being *handled* here.  Without this the bundle was refused outright.

    PRTS gives the figure as 「攻击力50%」, a fraction of attack, so a value at or
    above one would mean the key had been read as a multiplier.
    """

    from .coefficient_provenance import SPLASH_PATTERN

    handled = False
    prefix = "enemy_database.json#/"
    for enemy in content.enemies:
        rows = [
            coefficient
            for coefficient in enemy.mechanic_coefficients
            if SPLASH_PATTERN.match(coefficient.key)
        ]
        if not rows:
            continue
        handled = True
        for coefficient in rows:
            if not 0 < coefficient.value_micros <= 1_000_000:
                raise ValueError(
                    "splash damage is a fraction of attack and must sit in "
                    "(0, 1]; %s ships %d micros"
                    % (enemy.enemy_id, coefficient.value_micros)
                )
            pointer = coefficient.source_pointer
            if not pointer.startswith(prefix):
                continue
            owner = pointer[len(prefix) :].split("/", 1)[0]
            if owner != enemy.enemy_id:
                raise ValueError(
                    f"splash coefficient {coefficient.key!r} on "
                    f"{enemy.enemy_id!r} cites another enemy's entry ({owner!r})"
                )
    return handled

# Two more families that belonged to an id rather than to their data, found by
# sweeping every one-element id gate for a second shipped carrier after the
# 逐腐兽 bleed turned out to have one.
#
# 萨卡兹枯朽战士 `enemy_1267_nhpbr` (IS3 2-6) and 组长 `_2` (**6-2**):
# 「被击倒时释放污染秧蚀，使周围我方持续损失生命」, four `PollutedDie.*` keys.
# 萨卡兹枯朽战车 `enemy_1272_nhtank` (2-6 / 4-5) and 尖端 `_2` (**6-2**):
# 「只攻击地面单位，数次攻击后，下次攻击释放污染秧蚀，近战攻击造成更高伤害」,
# `Empty.sp` + `Empty.attack@chuang_atk_scale` and a `PollutedRangedAtk` skill.
#
# Both elite variants ship **identical mechanism keys and identical values** to
# their base -- only the stat block differs -- and both stand in 6-2, which
# neither base carrier reaches.  So stage 6-2 was running two enemies with none
# of their mechanisms: no death pollution, no charged polluted attack, no
# doubled melee hit.
POLLUTED_DEATH_KEYS = frozenset(
    {
        "PollutedDie.polluted_damage_low",
        "PollutedDie.polluted_damage_high",
        "PollutedDie.projectile_life_time",
        "PollutedDie.projectile_range",
    }
)

POLLUTED_TANK_KEYS = frozenset({"Empty.sp", "Empty.attack@chuang_atk_scale"})


def _carries_all(definition: object, keys: frozenset[str]) -> bool:
    from .enemy_coefficients import coefficient_value

    return all(coefficient_value(definition, key) is not None for key in sorted(keys))


def carries_polluted_death(definition: object) -> bool:
    """Whether this enemy's own keys arm the on-death pollution burst."""

    return _carries_all(definition, POLLUTED_DEATH_KEYS)


def carries_polluted_tank(definition: object) -> bool:
    """Whether this enemy's own keys arm the charged polluted attack."""

    return _carries_all(definition, POLLUTED_TANK_KEYS)

#: 辐能源石虫's death grant, deliberately **not** aliased into the `boom.*`
#: family even though it is spelled `Boom.add_count`: the explosion and the
#: energy are two different consequences of the same death, and the lowercase
#: family has no member for a point count.  See PRTS 「辐能源石虫」 --
#: 「给予全场敌人 1 点能量」.
DEATH_ENERGY_KEYS = frozenset({"Boom.add_count"})


def validate_death_energy_coefficients(content: ContentBundle) -> bool:
    """Authorize the on-death energy grant.

    A whole positive number of points: energy is counted in points against a
    shipped cap (20), so a fraction would mean the key was read on the wrong
    scale, and zero would be no grant at all.
    """

    handled = False
    for enemy in content.enemies:
        if not _authorize_family_rows(
            enemy, DEATH_ENERGY_KEYS, {}, "death energy grant"
        ):
            continue
        for coefficient in enemy.mechanic_coefficients:
            if coefficient.key not in DEATH_ENERGY_KEYS:
                continue
            if coefficient.value_micros <= 0 or coefficient.value_micros % 1_000_000:
                raise ValueError(
                    "death energy must be a whole positive point count; %s ships "
                    "%d micros" % (enemy.enemy_id, coefficient.value_micros)
                )
            handled = True
    return handled
