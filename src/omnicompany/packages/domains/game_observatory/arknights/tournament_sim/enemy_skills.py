# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""Enemy skills that charge off their own normal attacks.

The shipped ``skills`` entries carry ``spCost`` and ``cooldown``, and for this
family the **spCost is a count of normal attacks**, not a pool of energy.  PRTS
「狙击步兵」 states it outright -- 「攻击2次后，下一次攻击会晕眩」 against a shipped
``spCost`` of 2, cooldown 0 -- and 深池弩炮队长's 「攻击2次后，下一次攻击造成溅射伤害」
sits on the same value.  One SP per normal attack, and the attack after the
count is reached carries the skill.

That distinction is the whole reason this is a separate contract rather than a
few more coefficients: folding these blackboards into ``mechanic_coefficients``
would make them fire on *every* attack, turning "every third hit" into "always"
-- plausible numbers, green tests, and wrong.

A carrier whose ``cooldown`` is set and ``spCost`` is zero charges on time
instead (迷路的巨像: 13 s), so both are represented and exactly one is used.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .is3_description_gated import strip_description_markup
from .model_primitives import _Contract


class ChargedEnemySkill(_Contract):
    """One enemy skill, with the trigger it actually uses."""

    prefab_key: str = Field(min_length=1)
    #: Normal attacks needed to charge it.  Zero means it runs on the cooldown.
    sp_cost: int = Field(default=0, ge=0, le=20)
    #: Ticks between uses when the skill is not attack-charged.
    cooldown_ticks: int = Field(default=0, ge=0)
    #: Ticks before the *first* use.  Shipped separately from the recharge and
    #: often shorter -- PRTS 「哈兰杜汗，大地之鞭」 gives its gun skill
    #: 「10s初始 / 30s循环」 -- so seeding the recharge would delay the opening
    #: use by twenty seconds.
    initial_cooldown_ticks: int = Field(default=0, ge=0)
    #: Multiplier applied to the attack the skill rides on.
    attack_scale_micros: int = Field(default=1_000_000, gt=0)
    #: Seconds of stun the skill inflicts, in micros; zero means none.
    stun_seconds_micros: int = Field(default=0, ge=0)
    #: 蓄力: ticks between the skill firing and its damage landing.  Zero means
    #: the skill rides the attack it fires on, which is what every other member
    #: of this family does.
    windup_ticks: int = Field(default=0, ge=0)
    #: Radius of the delayed strike, in milli-tiles.  Only meaningful with a
    #: wind-up: 散华骑士团精锐 hits 「目标周围所有单位」 rather than its target.
    splash_radius_milli_tiles: int = Field(default=0, ge=0)
    #: Seconds of 寒冷 the skill inflicts, in micros; zero means none.
    #: 雪怪术师 ships `coldattack` with `spCost` 2 and `freeze` 10.0, described as
    #: 「每攻击2次后下次攻击会对目标施加寒冷」 -- so the shipped key is named
    #: `freeze` and the state it applies is **寒冷**, which this engine already
    #: keeps distinct from 冻结 (cold is attack speed -30; freeze is a hold).
    #: The description decides which, not the key name, exactly as it does for
    #: every other ambiguous key in this data.
    cold_seconds_micros: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def exactly_one_trigger_and_some_effect(self) -> "ChargedEnemySkill":
        if self.initial_cooldown_ticks and not self.cooldown_ticks:
            raise ValueError(
                "an initial cooldown only means something for a timed skill"
            )
        if bool(self.sp_cost) == bool(self.cooldown_ticks):
            raise ValueError(
                "a charged skill is either attack-charged (sp_cost) or timed "
                "(cooldown_ticks), never both and never neither"
            )
        if bool(self.windup_ticks) != bool(self.splash_radius_milli_tiles):
            raise ValueError(
                "a delayed strike needs both its wind-up and its radius; one "
                "without the other is half a mechanism"
            )
        if self.stun_seconds_micros and self.cold_seconds_micros:
            raise ValueError(
                "a charged skill applying both a stun and cold is not a shape "
                "this data ships; one of the two was misread"
            )
        if (
            self.attack_scale_micros == 1_000_000
            and not self.stun_seconds_micros
            and not self.cold_seconds_micros
        ):
            raise ValueError(
                "a charged skill with neither a damage scale nor a stun has no "
                "represented effect; leave it unimplemented rather than arming "
                "an empty skill"
            )
        return self


class VictorySelfBuff(_Contract):
    """The permanent buff a 决胜 carrier gains once its partner is gone.

    PRTS on both carriers: 「自身防御力最终+2000，法术抗性最终+50，移动速度+100%，
    攻击速度+100」.  All four are additive finals, so they are stored as the
    shipped figures rather than as multipliers.
    """

    #: Ticks of the skill's own cooldown before it may fire.  Shipped as
    #: initCooldown: 5 s on 哈兰杜汗, 0 on 路加萨尔古斯.
    delay_ticks: int = Field(default=0, ge=0)
    #: 「攻击速度+100」 -- points against a 100 baseline, like `buff.attack_speed`.
    attack_speed_points: int = Field(gt=0)
    #: 「移动速度+100%」 -- a bonus fraction in micros, NOT a resulting multiple.
    move_speed_bonus_micros: int = Field(gt=0)
    #: 「防御力最终+2000」 / 「法术抗性最终+50」.
    defence_bonus: int = Field(ge=0)
    magic_resistance_bonus: int = Field(ge=0)

    @model_validator(mode="after")
    def some_stat_actually_moves(self) -> "VictorySelfBuff":
        if not (self.defence_bonus or self.magic_resistance_bonus):
            raise ValueError(
                "决胜 without either defensive final is not the shipped shape; "
                "leave it unimplemented rather than arming a partial buff"
            )
        return self


#: PRTS 「喷气人」 天赋, and none of it is in the shipped blackboard:
#: 「切换为飞行模式后，**1.5秒**内移动速度最终降低90%」 and 「结束后，**1.333秒**内
#: 不可阻挡，移动速度最终降低90%」.
TAKEOFF_SLOW_TICKS = 45
LANDING_SLOW_TICKS = 40
SLOW_MOVE_MULTIPLIER_MICROS = 100_000


class TakeOffSkill(_Contract):
    """喷气人's 升空: it flies out of a block for a while.

    Shipped on ``enemy_2004_balloon`` as a ``TakeOff`` skill entry --
    ``initCooldown`` 1 s, ``cooldown`` 25 s, ``duration`` 7 s and
    ``balloon_s[fly].move_speed`` 0.5 -- and described as 「被阻挡后会飞起来一段
    时间」.  PRTS 「喷气人」 adds the parts the description leaves out:
    「被阻挡时获取升空检测器」 (checked every 0.33 s), 「飞行模式持续7秒」 and
    「近地悬浮期间**无法被阻挡或近战攻击**，行动方式变为飞行」.

    The other ``TakeOff`` carrier, 弧光突击队, is deliberately *not* represented
    here: it ships ``spCost``/``cooldown``/``initCooldown`` all at 9999 -- a
    never-fires sentinel -- because its trigger is a condition
    (「移动速度高于初始速度时」) rather than a timer.
    """

    duration_ticks: int = Field(gt=0)
    cooldown_ticks: int = Field(gt=0)
    initial_cooldown_ticks: int = Field(default=0, ge=0)
    #: 「飞行模式：……**移动速度+50%**」 -- the shipped
    #: `balloon_s[fly].move_speed` **0.5** is that **bonus**, not a resulting
    #: multiple.  It was read as a multiple once, which halved the carrier
    #: instead of speeding it up: a jet-pack man that flies away at 150% and one
    #: that crawls at 50% are opposite threats.
    flight_move_speed_bonus_micros: int = Field(gt=0)
    #: 「**切换为飞行模式后，1.5秒内**移动速度最终降低90%」
    takeoff_slow_ticks: int = Field(default=TAKEOFF_SLOW_TICKS, ge=0)
    #: 「飞行模式持续7秒；**结束后，1.333秒内不可阻挡，移动速度最终降低90%**」
    landing_slow_ticks: int = Field(default=LANDING_SLOW_TICKS, ge=0)
    #: 「移动速度**最终降低90%**」 -- a resulting figure, so it replaces whatever
    #: else applied rather than stacking with it.
    slow_move_multiplier_micros: int = Field(default=SLOW_MOVE_MULTIPLIER_MICROS, gt=0)

class EnergyChargeSkill(_Contract):
    """匪帮射手's 充能, on the schedule the skill actually ships.

    ``enemy_1356_egun`` (IS3 3-7) carries an ``AddEnergy`` skill entry with
    ``initCooldown`` 5 s, ``cooldown`` 28 s and a blackboard of ``duration`` 5,
    ``interval`` 1, ``add_count`` 1 and ``range_radius`` 2.2 -- which is also its
    own ``rangeRadius``, so the description's 「攻击范围内」 and the skill's
    radius are the same circle.

    The engine already granted energy for this carrier, but **continuously**:
    one point a second, forever, from the tick the battle started.  PRTS
    「匪帮射手」 is where the +1/s came from -- 「周期性停止移动，并为攻击
    范围内所有敌人持续提供能量」 -- and the two halves of that sentence
    are the two halves of this contract: 周期性 is the 28-second cycle, and
    持续 is the +1/s *inside* the 5-second window.  Reading only the rate made
    the charger roughly five times as strong as the data says.

    「停止移动」 is the other half PRTS supplies: the carrier holds still while
    it channels, which is what makes the window a real opening rather than a
    bookkeeping detail.
    """

    #: Ticks before the first window opens (`initCooldown`).
    initial_cooldown_ticks: int = Field(ge=0)
    #: Ticks from one window opening to the next (`cooldown`).
    cooldown_ticks: int = Field(gt=0)
    #: How long a window lasts (`duration`).
    duration_ticks: int = Field(gt=0)
    #: Ticks between grants inside a window (`interval`).
    interval_ticks: int = Field(gt=0)
    #: Energy per grant (`add_count`).
    amount: int = Field(gt=0)
    #: `range_radius`, in milli-tiles.
    radius_milli_tiles: int = Field(gt=0)

    @model_validator(mode="after")
    def the_window_fits_inside_its_cycle(self) -> "EnergyChargeSkill":
        if self.duration_ticks > self.cooldown_ticks:
            raise ValueError(
                "an energy-charge window longer than its own cycle would never "
                "close; that is not the shipped shape"
            )
        if self.interval_ticks > self.duration_ticks:
            raise ValueError(
                "an interval longer than the window would grant nothing"
            )
        return self


# --------------------------------------------------------------------------- #
# readers -- moved verbatim out of `stage_compiler` so that file fits the
# 2000-line budget.  They belong next to the contracts they build anyway; the
# compiler re-exports them, so importers are unchanged.
# --------------------------------------------------------------------------- #

#: Skill prefabs whose effect this engine can represent: a scaled hit, a stun,
#: or both.  Others stay unimplemented rather than being armed with no effect.
#:
#: `Cannon` is deliberately absent even though it fits the shape (timed,
#: atk_scale only).  Its only IS3 carrier, `enemy_1273_stmgun`, already has a
#: bespoke barrage implementation in the engine keyed off `Cannon.initCooldown`
#: and `Cannon.atk_scale`, so admitting the prefab here would give that enemy
#: both the barrage and a generic scaled hit.
CHARGED_SKILL_PREFABS = frozenset(
    {
        "StunAttack",
        "stunattack",
        "stuncombat",
        "coldattack",
        "ChargeAttack",
        "PowerAttack",
        "Gun",
    }
)
#: `stuncombat` is the **third** spelling of the attack-charged stun, after
#: `StunAttack` and `stunattack`, and it was the one left out.  Its carriers ship
#: the identical shape -- 码头水手 `spCost` 3 / `stun` 7, 粉碎攻坚手 `spCost` 2 /
#: `stun` 7 -- against 狙击步兵's `stunattack` `spCost` 2 / `stun` 5.
#:
#: The ledger had recorded this one as unimplementable because 「数次攻击后晕眩」
#: names no count and PRTS gives none either.  **The count is shipped**: it is
#: `spCost`, exactly as PRTS 「狙击步兵」 pins for the sibling spelling
#: (「攻击2次后，下一次攻击会晕眩」 against a shipped `spCost` 2).  The
#: reading was there; the field had not been looked at.


def charged_enemy_skill(
    data: dict, tps: int, enemy_id: str = "?"
) -> ChargedEnemySkill | None:
    """Read the first representable attack-charged skill off an enemy record.

    PRTS 「狙击步兵」 pins the reading: 「攻击2次后，下一次攻击会晕眩」 against a
    shipped ``spCost`` of 2 with cooldown 0.  So spCost counts *normal attacks*,
    and a carrier with spCost 0 but a cooldown charges on time instead.

    A prefab whose blackboard carries neither a scale nor a stun is skipped --
    深池弩炮队长's PowerAttack ships an empty blackboard because its effect is
    splash, which has no representation here, and arming an empty skill would
    look implemented while doing nothing.
    """

    representable: list[ChargedEnemySkill] = []
    for skill in data.get("skills") or []:
        prefab = skill.get("prefabKey")
        if prefab not in CHARGED_SKILL_PREFABS:
            continue
        blackboard = {
            entry["key"]: entry.get("value")
            for entry in (skill.get("blackboard") or [])
            if isinstance(entry, dict) and entry.get("key")
        }
        scale = blackboard.get("atk_scale")
        stun = blackboard.get("stun")
        # `coldattack` names its duration `freeze`, and its carriers' own
        # descriptions say 寒冷 -- so the key is read as cold rather than as the
        # hold the name suggests.  A carrier that ever spells it the other way
        # round would be caught by the contract's both-at-once refusal.
        cold = blackboard.get("freeze") if prefab == "coldattack" else None
        # 蓄力: `ChargeAttack` ships a `duration` wind-up and a `range_radius`,
        # and lands on 「目标周围所有单位」 instead of on the target it fired at.
        # Only read when **both** are present -- 「妒」 ships the same prefab with
        # a duration and no radius, which is a different shape this engine has
        # no representation for.
        windup = blackboard.get("duration") if prefab == "ChargeAttack" else None
        radius = blackboard.get("range_radius") if prefab == "ChargeAttack" else None
        if prefab == "ChargeAttack" and (windup is None or radius is None):
            continue
        if scale is None and stun is None and cold is None:
            continue
        sp_cost = int(skill.get("spCost") or 0)
        cooldown = float(skill.get("cooldown") or 0.0)
        initial_cooldown = float(skill.get("initCooldown") or 0.0)
        if sp_cost <= 0 and cooldown <= 0:
            continue
        representable.append(
            ChargedEnemySkill(
                prefab_key=prefab,
                sp_cost=sp_cost,
                cooldown_ticks=0 if sp_cost > 0 else max(1, round(cooldown * tps)),
                initial_cooldown_ticks=(
                    0 if sp_cost > 0 else max(1, round(initial_cooldown * tps))
                ),
                attack_scale_micros=(
                    1_000_000
                    if scale is None
                    else max(1, round(float(scale) * 1_000_000))
                ),
                stun_seconds_micros=(
                    0 if stun is None else max(0, round(float(stun) * 1_000_000))
                ),
                cold_seconds_micros=(
                    0 if cold is None else max(0, round(float(cold) * 1_000_000))
                ),
                windup_ticks=(
                    0 if windup is None else max(1, round(float(windup) * tps))
                ),
                splash_radius_milli_tiles=(
                    0 if radius is None else max(1, round(float(radius) * 1000))
                ),
            )
        )
    if not representable:
        return None
    if len(representable) > 1:
        # No IS3 enemy carries two of these today (checked across all 12
        # carriers), so there is no selection rule to state -- and picking one
        # by list order would be a silent choice the moment that changes.
        # Refusing keeps the decision visible instead.
        raise ValueError(
            "enemy %r carries %d representable charged skills (%s); the "
            "selection rule is undecided, so this must be settled rather than "
            "resolved by list order"
            % (
                enemy_id,
                len(representable),
                ", ".join(item.prefab_key for item in representable),
            )
        )
    return representable[0]



def energy_charge_skill(data: dict, tps: int) -> "EnergyChargeSkill | None":
    """Read 匪帮射手's 充能 schedule off an enemy record.

    Every figure is required: a window with no duration, no interval or no
    amount is not a schedule, and a charger without its radius would have to
    borrow one.  A partial entry is left unread so the caller reports it rather
    than the engine inventing a cadence -- which is exactly what the previous
    always-on implementation amounted to.
    """

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "AddEnergy":
            continue
        blackboard = {
            entry["key"]: entry.get("value")
            for entry in (skill.get("blackboard") or [])
            if isinstance(entry, dict) and entry.get("key")
        }
        duration = blackboard.get("duration")
        interval = blackboard.get("interval")
        amount = blackboard.get("add_count")
        radius = blackboard.get("range_radius")
        cooldown = float(skill.get("cooldown") or 0.0)
        if None in (duration, interval, amount, radius) or cooldown <= 0:
            continue
        if cooldown >= 9999:
            continue
        return EnergyChargeSkill(
            initial_cooldown_ticks=max(
                0, round(float(skill.get("initCooldown") or 0.0) * tps)
            ),
            cooldown_ticks=max(1, round(cooldown * tps)),
            duration_ticks=max(1, round(float(duration) * tps)),
            interval_ticks=max(1, round(float(interval) * tps)),
            amount=max(1, round(float(amount))),
            radius_milli_tiles=max(1, round(float(radius) * 1000)),
        )
    return None


def takeoff_skill(data: dict, tps: int) -> "TakeOffSkill | None":
    """Read 喷气人's 升空 off an enemy record.

    Requires the whole shape: a positive duration, a real recharge, and the
    flight speed.  弧光突击队 also carries a ``TakeOff`` entry but ships
    ``spCost``/``cooldown``/``initCooldown`` all at 9999 -- a never-fires
    sentinel, because its trigger is 「移动速度高于初始速度时」 rather than a
    timer -- and it carries no ``duration``, so it is filtered out here rather
    than by an id allow-list.
    """

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "TakeOff":
            continue
        blackboard = {
            entry["key"]: entry.get("value")
            for entry in (skill.get("blackboard") or [])
            if isinstance(entry, dict) and entry.get("key")
        }
        duration = blackboard.get("duration")
        flight_speed = next(
            (
                value
                for key, value in blackboard.items()
                if key.endswith("move_speed") and key != "move_speed"
            ),
            None,
        )
        cooldown = float(skill.get("cooldown") or 0.0)
        if duration is None or flight_speed is None or cooldown <= 0:
            continue
        if cooldown >= 9999:
            # The sentinel: this carrier's takeoff is not on a timer at all.
            continue
        return TakeOffSkill(
            duration_ticks=max(1, round(float(duration) * tps)),
            cooldown_ticks=max(1, round(cooldown * tps)),
            initial_cooldown_ticks=max(
                0, round(float(skill.get("initCooldown") or 0.0) * tps)
            ),
            flight_move_speed_bonus_micros=max(
                1, round(float(flight_speed) * 1_000_000)
            ),
        )
    return None


def victory_self_buff(data: dict, tps: int) -> VictorySelfBuff | None:
    """Read the 决胜 entry off an enemy record.

    Kept apart from `charged_enemy_skill` on purpose: `Victory` carries no
    atk_scale and no stun, so the allow-list there rejects it, and folding it in
    would arm it as an attack rider instead of a one-shot self-buff.
    """

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "Victory":
            continue
        blackboard = {
            entry["key"]: entry.get("value")
            for entry in (skill.get("blackboard") or [])
            if isinstance(entry, dict) and entry.get("key")
        }
        needed = ("attack_speed", "move_speed", "def", "magic_resistance")
        if any(blackboard.get(key) is None for key in needed):
            # A partial shape is one this reader has never seen; refuse it
            # rather than defaulting the missing halves to zero.
            continue
        return VictorySelfBuff(
            delay_ticks=max(0, round(float(skill.get("initCooldown") or 0.0) * tps)),
            attack_speed_points=round(float(blackboard["attack_speed"])),
            move_speed_bonus_micros=round(float(blackboard["move_speed"]) * 1_000_000),
            defence_bonus=round(float(blackboard["def"])),
            magic_resistance_bonus=round(float(blackboard["magic_resistance"])),
        )
    return None


#: Summon fields a `char`-scoped stage rune can move, named as the shipped
#: blackboards name them.

#: PRTS 「弧光突击队」 gives the detector's cadence and the flight's shape; the
#: shipped data gives the two magnitudes.  Kept as module constants rather than
#: contract fields because they are the same for both variants and neither is in
#: the tables.
CONDITIONAL_TAKEOFF_POLL_SECONDS = 0.2
CONDITIONAL_TAKEOFF_REQUIRED_SECONDS = 1.0
CONDITIONAL_TAKEOFF_LANDING_DAZE_SECONDS = 0.5
#: 「攻击范围扩大至基础半径的 179%」.
CONDITIONAL_TAKEOFF_RANGE_MULTIPLIER_MILLI = 1790


class ConditionalTakeOff(_Contract):
    """弧光突击队's 助跑起飞, which no timer ever fires.

    `enemy_1327_cbrokt` and `_2` (both IS3 2-2) ship a `TakeOff` skill whose
    `spCost`/`cooldown`/`initCooldown` are all **9999** -- a never-fires
    sentinel -- because the trigger is a *condition*:
    「移动速度高于初始速度时，近地悬浮，助跑起飞！」.

    PRTS 「弧光突击队」 states the rule the description compresses:

        「升空检测器每 0.2 秒检测一次：当前移动速度超过登场基础速度或 0.9，
    且处于自主移动中，计数器 +0.2；计数器 ≥ 1.0 且未被沉默时强制进入飞行」,
        and while airborne: 「无法被阻挡或被近战攻击」, 「行动方式变为飞行」,
        「移动速度 +10%」, 「攻击范围扩大至 179%」, and it lands on silence /
        stun / daze with a 0.5-second daze.

    The two shipped numbers land exactly on that sentence: `passive.fly_speed`
    **0.9** is the threshold's second half, and the sentinel skill's
    `move_speed` **0.1** is the +10% while airborne.  Neither is a duration --
    this flight has none, which is why it cannot reuse 喷射人's timer contract.

    In IS3 2-2 the thing that raises its speed is on the same map: the four
    源石流发生装置 blow same-direction enemies +80%, well past 0.9.
    """

    #: 「超过登场基础速度或 0.9」, in milli-tiles per second.
    speed_threshold_milli_tiles_per_second: int = Field(gt=0)
    #: 「移动速度 +10%」 while airborne, as a bonus fraction in micros.
    airborne_move_speed_bonus_micros: int = Field(gt=0)
    detector_interval_ticks: int = Field(gt=0)
    detector_required_ticks: int = Field(gt=0)
    landing_daze_ticks: int = Field(ge=0)
    attack_range_multiplier_milli: int = Field(gt=0)

    @model_validator(mode="after")
    def the_counter_needs_more_than_one_poll(self) -> "ConditionalTakeOff":
        if self.detector_required_ticks < self.detector_interval_ticks:
            raise ValueError(
                "a takeoff that triggers on its first poll is not the shipped "
                "shape; PRTS gives 0.2s polls and a 1.0s counter"
            )
        return self

def conditional_takeoff_skill(data: dict, tps: int) -> "ConditionalTakeOff | None":
    """Read 弧光突击队's condition-triggered flight off an enemy record.

    Requires the whole shape, because each half alone means something else:
    the `passive.fly_speed` threshold, a `TakeOff` entry whose cooldowns are the
    **9999 sentinel** (a real cooldown is 喷射人's timer takeoff, already
    handled elsewhere), and that entry's `move_speed` bonus.  A carrier missing
    any of the three is left unread.
    """

    values = {
        entry["key"]: entry.get("value")
        for entry in (data.get("talentBlackboard") or [])
        if isinstance(entry, dict) and entry.get("key")
    }
    threshold = values.get("passive.fly_speed")
    if not isinstance(threshold, (int, float)) or threshold <= 0:
        return None
    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "TakeOff":
            continue
        cooldown = float(skill.get("cooldown") or 0.0)
        if cooldown < 9999:
            # A real recharge: that is the timer takeoff, not this one.
            continue
        blackboard = {
            entry["key"]: entry.get("value")
            for entry in (skill.get("blackboard") or [])
            if isinstance(entry, dict) and entry.get("key")
        }
        bonus = blackboard.get("move_speed")
        if not isinstance(bonus, (int, float)) or bonus <= 0:
            continue
        return ConditionalTakeOff(
            speed_threshold_milli_tiles_per_second=max(
                1, round(float(threshold) * 1000)
            ),
            airborne_move_speed_bonus_micros=max(1, round(float(bonus) * 1_000_000)),
            detector_interval_ticks=max(
                1, round(CONDITIONAL_TAKEOFF_POLL_SECONDS * tps)
            ),
            detector_required_ticks=max(
                1, round(CONDITIONAL_TAKEOFF_REQUIRED_SECONDS * tps)
            ),
            landing_daze_ticks=max(
                0, round(CONDITIONAL_TAKEOFF_LANDING_DAZE_SECONDS * tps)
            ),
            attack_range_multiplier_milli=CONDITIONAL_TAKEOFF_RANGE_MULTIPLIER_MILLI,
        )
    return None

class OverloadSkill(_Contract):
    """R-31重型动力装甲's 【超负载】: ten seconds of punching, once unblocked.

    `enemy_1255_lybgpa` (IS3 4-2 and 5-8, atk 1600, `baseAttackTime` **4.0**)
    ships `Punch` with `cooldown` 20, `initCooldown` 0, `stun` 2.0,
    `atk_scale` 0.75, `duration` 10.0 and `unmovable_duration` 1.0.

    PRTS 「R-31重型动力装甲」 states the rule verbatim, and it is worth quoting
    in full because a paraphrase of it got two things backwards:

        「仅**被阻挡**、且半径0.8范围内存在非飞行我方单位时可触发：
          以阻挡单位为目标，**解除自身阻挡**，10s内对目标连续出拳
          （每拳间隔1s），每次造成攻击力75%的物理伤害，附加2s晕眩
          （技能期间不会改变攻击目标）
          ※技能结束动画持续1s；**目标退场后立刻中止技能**」

    So: **被阻挡** (it is held, not holding), and the skill **releases that
    block** when it starts -- the blocker stops holding it and takes the whole
    ten seconds of punches instead.  Neither of those is in the shipped
    blackboard, and neither is the 1-second punch cadence: 「大幅提升攻击速度」
    is a **fixed 1s interval** against a 4-second base, which is why no
    attack-speed figure ships with it.
    """

    duration_ticks: int = Field(gt=0)
    punch_interval_ticks: int = Field(gt=0)
    cooldown_ticks: int = Field(gt=0)
    initial_cooldown_ticks: int = Field(default=0, ge=0)
    attack_scale_micros: int = Field(gt=0)
    stun_seconds_micros: int = Field(gt=0)
    #: 「半径0.8范围内存在非飞行我方单位」.
    trigger_radius_milli_tiles: int = Field(gt=0)
    #: 「技能结束动画持续1s」 -- the shipped `unmovable_duration`.
    recovery_unmovable_ticks: int = Field(ge=0)

    @model_validator(mode="after")
    def the_window_fits_at_least_one_punch(self) -> "OverloadSkill":
        if self.punch_interval_ticks > self.duration_ticks:
            raise ValueError(
                "an overload window shorter than one punch interval would land "
                "nothing; that is not the shipped shape"
            )
        if self.duration_ticks > self.cooldown_ticks:
            raise ValueError(
                "a window longer than its own cooldown would never close"
            )
        return self


#: 「半径0.8范围内」 -- the trigger's second condition, in milli-tiles.  Not in
#: the blackboard; PRTS only.
OVERLOAD_TRIGGER_RADIUS_MILLI_TILES = 800


#: 「生命值降至一半时」 -- 蒸汽战车's trigger, and the only place the **50%** is
#: written down: its blackboard carries the two bonuses and no threshold.
SELF_RAGE_HALF_HEALTH_PHRASE = "生命值降至一半时"

#: 「首次受到伤害后」 -- 烈酒级醒酒助手's, which PRTS states as 「自身首次生命值
#: 低于100%后」.  Any damage at all, so the threshold is the whole bar.
SELF_RAGE_FIRST_DAMAGE_PHRASE = "首次受到伤害后"


class SelfRage(_Contract):
    """A one-shot buff a carrier gives **itself** on crossing a health line.

    `Rage.*` is not `triggerrage.*`.  The paired mechanic already in this engine
    fires when a *partner* leaves the board; this one fires on the carrier's own
    health, and the two carriers do not even agree on where the line is:

        蒸汽战车 `enemy_1274_stmram` (4-5)   Rage.attack_speed 100  move_speed 1.0
        烈酒级醒酒助手 `enemy_1264_durgrd_2` (5-8)             150             1.5
                                                              Rage.duration 30.0

    PRTS gives both in full:

        「生命值**首次低于50%**时，移动速度**+100%**，攻击速度**+100**，获得：
          晕眩免疫，冻结免疫，浮空免疫，沉睡免疫」          (蒸汽战车, no duration)
        「自身**首次生命值低于100%**后，攻击速度**+150**，移动速度**+150%**，
          持续**30s**」                                    (烈酒级醒酒助手)

    Which settles the unit question this data keeps posing: `move_speed` here is
    a **bonus fraction** (1.0 = +100%), not the resulting multiple 山海密语仪's
    identically-named key carries.  Reading 1.0 as a multiple would have made
    the steam ram's rage do nothing at all.

    The threshold is not shipped by either carrier, so it comes from the
    description -- the same route `atkup.atk` and `Evade.prob` take.
    """

    #: Attack-speed **points** against the 100 baseline, as 「攻击速度+100」.
    attack_speed_bonus: int = Field(gt=0, le=1000)
    #: 「移动速度+100%」 as a bonus fraction in micros.
    move_speed_bonus_micros: int = Field(gt=0, le=10_000_000)
    #: The health line, in milli of max HP: 500 for 「降至一半」, 1000 for
    #: 「首次受到伤害」.
    hp_threshold_milli: int = Field(gt=0, le=1000)
    #: 0 when the buff is permanent, as 蒸汽战车's is.
    duration_ticks: int = Field(default=0, ge=0)
    #: 「获得：晕眩免疫，冻结免疫，浮空免疫，沉睡免疫」 -- gained with the rage
    #: rather than shipped in the attribute frame, so it cannot ride
    #: `EnemyImmunities` at compile time.
    grants_control_immunity: bool = False


def self_rage_talent(data: dict, tps: int) -> "SelfRage | None":
    """Read `Rage.*` off an enemy record, with its threshold from the text."""

    rows = data.get("talentBlackboard")
    while isinstance(rows, dict) and "m_value" in rows:
        rows = rows["m_value"]
    blackboard = {
        entry["key"]: entry.get("value")
        for entry in (rows or [])
        if isinstance(entry, dict) and entry.get("key")
    }
    points = blackboard.get("Rage.attack_speed")
    move = blackboard.get("Rage.move_speed")
    if points is None or move is None:
        return None
    # Shipped scalars arrive wrapped (`m_defined`/`m_value`), and 烈酒级醒酒助手
    # ships 「<@eb.key>首次受到伤害</>后」 -- so the phrase is also split by the
    # colour tag, the same trap the stealth reader fell into.
    raw_description = data.get("description")
    while isinstance(raw_description, dict) and "m_value" in raw_description:
        raw_description = raw_description["m_value"]
    description = strip_description_markup(
        raw_description if isinstance(raw_description, str) else ""
    )
    if SELF_RAGE_HALF_HEALTH_PHRASE in description:
        threshold = 500
    elif SELF_RAGE_FIRST_DAMAGE_PHRASE in description:
        threshold = 1000
    else:
        # Both shipped carriers state their line.  A third that does not would
        # need its own reading, and defaulting to either of these would give it
        # the wrong one.
        raise ValueError("self rage carries no health line in its description")
    duration = blackboard.get("Rage.duration")
    return SelfRage(
        attack_speed_bonus=round(float(points)),
        move_speed_bonus_micros=round(float(move) * 1_000_000),
        hp_threshold_milli=threshold,
        duration_ticks=0 if duration is None else max(1, round(float(duration) * tps)),
        grants_control_immunity="免疫" in description,
    )


#: 「**捕食**硬甲爪兽后」 -- the prey is named in the description and nowhere else,
#: so it is resolved against the stage's own name table at compile time, the same
#: way 家族专用轿车's unloaded soldier is.
PREDATION_PHRASE = "捕食"

#: 「可以攻击自身**半径0.8范围**内的硬甲爪兽/刚甲爪兽（不可对空），每次造成自身
#: 攻击力**1500%**的真实伤害」 -- PRTS 「狡兽」.  Neither figure is shipped: the
#: blackboard carries `Shield.max_hp_ratio`, `Mode.mode` and the weakness, and
#: says nothing about reach or damage.
PREDATION_REACH_MILLI_TILES = 800
PREDATION_ATTACK_SCALE_MICROS = 15_000_000


class Predation(_Contract):
    """狡兽 `enemy_1311_mhkryk` / 恶狡兽 `_2` (IS3 **4-4**): 【形态差分】.

    PRTS 「狡兽」 天赋, verbatim:

        「【形态差分】：通常（默认为该形态）/持有<甲壳>
          ''通常状态''时：可以攻击自身半径0.8范围内的硬甲爪兽/刚甲爪兽（不可对空），
            每次造成自身攻击力1500%的真实伤害
          击倒硬甲爪兽/刚甲爪兽后获得<甲壳>（仅触发1次）
          ''持有<甲壳>''时：①获得晕眩反制
            ②获得【弱点】：受到来自正面的物理/法术伤害-80%
            ③未被阻挡时，始终面向我方干员数量较多的方向（相同数量则保持当前方向）
            ④受到眩光手雷效果影响后失去<甲壳>」

    Three things this is the first of in this engine:

    * an enemy that **attacks another enemy** -- and one-shots it, since 1090 x
      15 is 16350 against 硬甲爪兽's 14000 (1260 x 15 = 18900 against 18000);
    * a **weakness that is not always on**: the -80% arrives with the shell, so
      the same carrier is soft before it has eaten and armoured afterwards;
    * 异常反制 rather than 异常免疫.  PRTS distinguishes them: 反制 「会**清除**掉
      包含对应异常效果的 Buff，且阻止此类 Buff 被添加」, where immunity merely
      suppresses.  So taking the shell mid-stun ends the stun.
    """

    prey_enemy_ids: tuple[str, ...] = Field(min_length=1)
    reach_milli_tiles: int = Field(default=PREDATION_REACH_MILLI_TILES, gt=0)
    attack_scale_micros: int = Field(default=PREDATION_ATTACK_SCALE_MICROS, gt=0)
    #: 「①获得晕眩反制」
    shell_counters_stun: bool = True
    #: 「③未被阻挡时，始终面向我方干员数量较多的方向」
    shell_faces_operator_majority: bool = True
    #: The bite's own clock.  【形态差分】 is a talent and carries every figure
    #: except this one: the two skill prefabs 狡兽 ships, `DropShield` and
    #: `ShieldUp`, have **empty blackboards** and only a `cooldown` **5.0** with
    #: `initCooldown` **5.0**.  PRTS lists no separate skill for either -- they
    #: are the talent's two halves wearing skill clothing -- so the five seconds
    #: is the whole of what they add, and it is what stops a 狡兽 that spawns
    #: next to its prey from eating it on the first tick.
    bite_cooldown_ticks: int = Field(default=0, ge=0)
    bite_initial_cooldown_ticks: int = Field(default=0, ge=0)


def predation_talent(
    data: dict,
    names_to_ids: "dict[str, str] | None" = None,
    tps: int = 30,
) -> "Predation | None":
    """Read 【形态差分】, resolving the prey from the description's name.

    The marker is the shipped pair (`Shield.max_hp_ratio` with `Mode.mode`);
    the prey is a **name** in the description, which is why this reader needs
    the stage's id-to-name table the way the unloaded-soldier reader does.
    """

    rows = data.get("talentBlackboard")
    while isinstance(rows, dict) and "m_value" in rows:
        rows = rows["m_value"]
    keys = {
        entry.get("key")
        for entry in (rows or [])
        if isinstance(entry, dict)
    }
    if not {"Shield.max_hp_ratio", "Mode.mode"} <= keys:
        return None
    raw_description = data.get("description")
    while isinstance(raw_description, dict) and "m_value" in raw_description:
        raw_description = raw_description["m_value"]
    description = strip_description_markup(
        raw_description if isinstance(raw_description, str) else ""
    )
    if PREDATION_PHRASE not in description:
        raise ValueError("predation carrier names no prey in its description")
    tail = description.split(PREDATION_PHRASE, 1)[1]
    prey_name = tail.split("后", 1)[0]
    # The table is name -> id, the same one the unloaded-soldier reader takes.
    matches = tuple(
        sorted({(names_to_ids or {})[prey_name]})
        if prey_name in (names_to_ids or {})
        else ()
    )
    if not matches:
        # The prey is on the same stage in both shipped cases.  A carrier whose
        # prey is absent would be a different mechanism -- one that can never
        # take the shell -- and guessing an id would arm it against the wrong
        # enemy.
        raise ValueError("predation prey %r is not on this stage" % prey_name)
    cooldown = 0.0
    initial = 0.0
    for skill in data.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        if skill.get("prefabKey") not in {"DropShield", "ShieldUp"}:
            continue
        # Both prefabs ship the same pair, so the larger wins rather than the
        # last one read: the two are one gate, not two.
        cooldown = max(cooldown, float(skill.get("cooldown") or 0.0))
        initial = max(initial, float(skill.get("initCooldown") or 0.0))
    return Predation(
        prey_enemy_ids=matches,
        bite_cooldown_ticks=max(0, round(cooldown * tps)),
        bite_initial_cooldown_ticks=max(0, round(initial * tps)),
    )


#: 「**在场时**令全场敌方单位……」 -- the giver's phrase.
TACTICAL_COMMAND_GIVER_PHRASE = "在场时"

#: 「**受到**传令兵或爱国者**强化**时……」 -- every receiver's, word for word.
TACTICAL_COMMAND_RECEIVER_PHRASE = "强化"


class TacticalCommand(_Contract):
    """`strength.*`: 【战术命令】, one key family with two opposite roles.

    PRTS 「游击队传令兵组长」 天赋: 「**在场时**令**全场敌方单位**（无视其可选性）
    攻击力**+10%**、防御力**+100**，并获得**<战术命令>**效果」.

    PRTS on the four receivers, each stating the same trigger:

        游击队突袭战士      「受到<战术命令>影响时，攻击力+50%」   strength.atk 0.5
        游击队突袭战士组长                        攻击力+80%                   0.8
        游击队战士          「……移动速度提升」          strength.move_speed 0.3
        游击队重装步兵      「……攻击速度大幅提升」    strength.attack_speed 50

    So `strength.atk` is **both** what a herald hands out (0.1) and what a raider
    gains for being handed it (0.5) -- the same one-key-two-mechanisms shape as
    `atkup.atk`, told apart by the description: 「在场时」 gives, 「受到…强化时」
    receives.

    The units differ inside the family and have to be read one at a time:
    `atk` and `move_speed` are **fractions** (+10%, +30%), `def` is a **flat**
    +100, and `attack_speed` is **points** against the 100 baseline.
    """

    role: Literal["giver", "receiver"]
    #: Giver: what every enemy on the field gains.  Receiver: what this carrier
    #: gains while the command is up.
    attack_bonus_micros: int = Field(default=0, ge=0)
    defense_bonus: int = Field(default=0, ge=0)
    move_speed_bonus_micros: int = Field(default=0, ge=0)
    attack_speed_bonus: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def carries_something(self) -> "TacticalCommand":
        if not (
            self.attack_bonus_micros
            or self.defense_bonus
            or self.move_speed_bonus_micros
            or self.attack_speed_bonus
        ):
            raise ValueError("tactical command carries no bonus at all")
        return self


def tactical_command_talent(data: dict) -> "TacticalCommand | None":
    """Read `strength.*`, with the role decided by the description."""

    rows = data.get("talentBlackboard")
    while isinstance(rows, dict) and "m_value" in rows:
        rows = rows["m_value"]
    blackboard = {
        entry["key"]: entry.get("value")
        for entry in (rows or [])
        if isinstance(entry, dict) and str(entry.get("key", "")).startswith("strength.")
    }
    if not blackboard:
        return None
    raw_description = data.get("description")
    while isinstance(raw_description, dict) and "m_value" in raw_description:
        raw_description = raw_description["m_value"]
    description = strip_description_markup(
        raw_description if isinstance(raw_description, str) else ""
    )
    if TACTICAL_COMMAND_RECEIVER_PHRASE in description and "受到" in description:
        role = "receiver"
    elif TACTICAL_COMMAND_GIVER_PHRASE in description:
        role = "giver"
    else:
        raise ValueError("tactical command carrier states neither role")
    return TacticalCommand(
        role=role,
        attack_bonus_micros=round(float(blackboard.get("strength.atk") or 0) * 1_000_000),
        defense_bonus=round(float(blackboard.get("strength.def") or 0)),
        move_speed_bonus_micros=round(
            float(blackboard.get("strength.move_speed") or 0) * 1_000_000
        ),
        attack_speed_bonus=round(float(blackboard.get("strength.attack_speed") or 0)),
    )


class EnvironmentalFragile(_Contract):
    """`Weak.weak[limit]` + `Weak.damage_scale`: hurt by the ground it stands on.

    纠缠藤蔓 `enemy_2052_smgia` (IS3 **b-3**) and 古老藤蔓 `enemy_2053_smgia2`
    (**b-3-b**), both shipping `weak[limit]` **10.0** and `damage_scale` **2.0**:

        「数次攻击后晕眩我方单位，由于**藤蔓结构不稳定**，很容易受到**火焰**影响」
        「数次攻击后晕眩我方单位，由于诞生于源石浓度极低的环境，很容易受到**高纯
          源石**影响」

    PRTS 「纠缠藤蔓」 天赋 gives the rule the descriptions only gesture at:
    「受到**环境伤害**时，获得 **10s 100%的脆弱**」.  So the two figures are the
    window and the multiplier: 100% 脆弱 is damage taken x2, and
    `weak[limit]` 10.0 is the ten seconds rather than a stack ceiling.

    **`Weak.*` is a prefix over two unrelated mechanisms** -- the eighth time
    this data does that.  改进型量产巨像 ships `Weak.def` -1200 and
    `Weak.magic_resistance` -50 for 「处于**重整束流**上时」, a different trigger
    and a different effect entirely.  Here the two are told apart by their keys
    rather than their text, which is the cheaper half of the same lesson.

    Each carrier names its own hazard and each stage supplies exactly that one:
    b-3 has nine `tile_volcano`, b-3-b has two `tile_infection`.  The engine
    does not need the naming, because a hazard tile is the only environmental
    damage there is -- but it is worth recording that the pairing is not a
    coincidence of the topic.
    """

    #: 「10s」 -- how long the fragility lasts, refreshed by each new hit.
    duration_ticks: int = Field(gt=0)
    #: 「100%的脆弱」 -- damage taken multiplier in micros (2.0 = x2).
    damage_scale_micros: int = Field(gt=1_000_000)


def environmental_fragile_talent(
    data: dict, tps: int
) -> "EnvironmentalFragile | None":
    """Read the pair, or None.  Both keys are required: one alone is not this."""

    rows = data.get("talentBlackboard")
    while isinstance(rows, dict) and "m_value" in rows:
        rows = rows["m_value"]
    blackboard = {
        entry["key"]: entry.get("value")
        for entry in (rows or [])
        if isinstance(entry, dict) and entry.get("key")
    }
    seconds = blackboard.get("Weak.weak[limit]")
    scale = blackboard.get("Weak.damage_scale")
    if seconds is None or scale is None:
        return None
    return EnvironmentalFragile(
        duration_ticks=max(1, round(float(seconds) * tps)),
        damage_scale_micros=round(float(scale) * 1_000_000),
    )


def stealth_strike_scale_micros(data: dict) -> int:
    """`InvisibleCombat.atk_scale`: 山海众秘使's mark, or 0.

    `enemy_1299_ymkilr_2`「山海众秘使」 (IS3 **5-6**) ships one skill,
    `InvisibleCombat`, with `atk_scale` **2.0** and no cooldown and no SP cost.
    Its description is 「隐匿，**隐匿效果失效后的下一次攻击**造成更高伤害」 and
    PRTS 「山海众秘使」 天赋 says how that is built:

        「隐匿，该隐匿**每次生效后**自身获得**强击标记**（**不可叠加**）」

    So the mark is earned by *being* hidden and spent by the next attack -- not
    by the moment of losing stealth, which is what the ability line reads like.
    The difference shows the first time a carrier is blocked before it ever
    hides: under the ability-line reading it would still swing twice as hard.

    No cooldown and no SP cost fit that: the mark is the gate, not a timer.
    """

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "InvisibleCombat":
            continue
        for entry in skill.get("blackboard") or []:
            if entry.get("key") == "atk_scale":
                value = entry.get("value")
                if isinstance(value, (int, float)) and value > 0:
                    return round(float(value) * 1_000_000)
    return 0


class GuidedStrike(_Contract):
    """`Guide`: 山海众司魅人 locks one of ours, charges, and then speeds up.

    `enemy_1300_ymmir_2`「山海众司魅人」 (IS3 **5-6**, atk 500, range 2.0, arts)
    ships one skill with four figures and no SP cost:

        cooldown 35.0   initCooldown 10.0
        anim_duration 5.0   atk_scale 2.0   duration 20.0   attack_speed 150.0

    PRTS 「山海众司魅人」 技能0「引导」 states all four in one sentence:
    「锁定攻击范围内**1名**我方单位，蓄力约 **5s** 后，对其造成攻击力 **200%** 的
    法术普通伤害。随后自身**攻击速度+150**，持续 **20s**」.

    Two readings the figures alone would leave open:

    * **`spCost` 0 with a cooldown means a timer**, not an attack-charged skill.
      The engine's other charged skills spend SP per swing; this one simply comes
      round, first at `initCooldown` and every `cooldown` after.
    * **The buff is on the caster, not the target.**  「随后**自身**攻击速度+150」 --
      and +150 is points against the 100 baseline, so the caster's interval goes
      to 2/5 of its shipped 2.5 s for the twenty seconds that follow.

    5-6's emergency node multiplies two of those four through
    `enemy_skill_blackb_mul`: 「duration x1.5, attack_speed x1.5」, so on the
    raid form the buff is 30 s of +225.
    """

    cooldown_ticks: int = Field(gt=0)
    initial_cooldown_ticks: int = Field(ge=0)
    #: 「蓄力约5s」 -- the lock is held this long before the blow lands.
    windup_ticks: int = Field(gt=0)
    #: 「攻击力200%的法术普通伤害」
    attack_scale_micros: int = Field(gt=0)
    #: 「自身攻击速度+150，持续20s」
    buff_attack_speed_bonus: int = Field(ge=0)
    buff_duration_ticks: int = Field(ge=0)


def guided_strike_skill(
    data: dict, tps: int, skill_factors: "dict[str, dict[str, float]] | None" = None
) -> "GuidedStrike | None":
    """Read `Guide`, with 5-6's emergency multipliers folded in if given."""

    factors = (skill_factors or {}).get("Guide") or {}
    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "Guide":
            continue
        blackboard = {
            entry["key"]: entry.get("value")
            for entry in (skill.get("blackboard") or [])
            if isinstance(entry, dict) and entry.get("key")
        }
        windup = blackboard.get("anim_duration")
        scale = blackboard.get("atk_scale")
        duration = blackboard.get("duration")
        attack_speed = blackboard.get("attack_speed")
        cooldown = float(skill.get("cooldown") or 0.0)
        if None in (windup, scale, duration, attack_speed) or cooldown <= 0:
            # Every figure is load-bearing: no windup is a different skill, no
            # scale is no damage, and no buff is half the mechanism.
            continue
        return GuidedStrike(
            cooldown_ticks=max(1, round(cooldown * tps)),
            initial_cooldown_ticks=max(
                0, round(float(skill.get("initCooldown") or 0.0) * tps)
            ),
            windup_ticks=max(1, round(float(windup) * tps)),
            attack_scale_micros=round(
                float(scale) * float(factors.get("atk_scale", 1.0)) * 1_000_000
            ),
            buff_attack_speed_bonus=round(
                float(attack_speed) * float(factors.get("attack_speed", 1.0))
            ),
            buff_duration_ticks=max(
                0,
                round(
                    float(duration) * float(factors.get("duration", 1.0)) * tps
                ),
            ),
        )
    return None


def death_life_point_cost(data: dict) -> int:
    """`Dead.value`: life points lost when this enemy is killed.

    `enemy_3003_alymot`「大奖赛运输艇」 (IS3 **3-4** and **4-3**) ships -2.0 and
    describes itself as 「**友好单位**，撤回保护目标时不扣除关卡生命值，死亡时扣除
    关卡生命值」; PRTS 「大奖赛运输艇」 天赋 confirms the figure: 「中立单位，不进行
    攻击，无法被阻挡，免疫水蚀；**死亡时扣除2目标生命值**」.

    So this is the exact inversion of the usual reading: its `lifePointReduce`
    is **0**, meaning letting it walk off the far end is free, and killing it is
    what costs.  Stored positive, because it is a cost.
    """

    rows = data.get("talentBlackboard")
    while isinstance(rows, dict) and "m_value" in rows:
        rows = rows["m_value"]
    for entry in rows or []:
        if not isinstance(entry, dict) or entry.get("key") != "Dead.value":
            continue
        value = entry.get("value")
        if isinstance(value, (int, float)):
            # Shipped negative (a deduction); the field is a magnitude.
            return abs(round(float(value)))
    return 0


class BackstabSkill(_Contract):
    """`Backstab`: 【恐惧凝集】, the boss that punishes an unwatched back.

    Three carriers, all in IS3 **ev-1** and all shipping `atk_scale` **0.85**
    with no SP cost:

        梦魇效仿者   `enemy_1185_nmekgt`    cooldown 15.0  initCooldown 10.0
        梦魇追随者   `enemy_1185_nmekgt_2`  cooldown 15.0  initCooldown 10.0
        「最后的怯薛」，拓拉 `_3`            cooldown 10.0  initCooldown 5.0

    PRTS 「梦魇效仿者」 技能0: 「对场上**所有**身后一格**没有视野**的我方**干员**
    造成攻击力 **85%** 的物理普通伤害」.

    Three details the description alone would not settle, and each changes who
    gets hit:

    * **场上所有**, not the ones in reach.  This is a board-wide sweep with no
      range of its own -- the carrier's own attack range is irrelevant.
    * **我方干员**, not summons.  Devices standing with their backs to a wall
      are not part of this.
    * **没有视野** is about the tile *behind* each operator, and 「视野」 here is
      whether **any** friendly covers that tile -- including the operator itself,
      whose range may well wrap around.
    """

    cooldown_ticks: int = Field(gt=0)
    initial_cooldown_ticks: int = Field(ge=0)
    attack_scale_micros: int = Field(gt=0)


def backstab_skill(data: dict, tps: int) -> "BackstabSkill | None":
    """Read `Backstab`, or None."""

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "Backstab":
            continue
        scale = None
        for entry in skill.get("blackboard") or []:
            if entry.get("key") == "atk_scale":
                value = entry.get("value")
                if isinstance(value, (int, float)):
                    scale = float(value)
        cooldown = float(skill.get("cooldown") or 0.0)
        if scale is None or scale <= 0 or cooldown <= 0:
            continue
        return BackstabSkill(
            cooldown_ticks=max(1, round(cooldown * tps)),
            initial_cooldown_ticks=max(
                0, round(float(skill.get("initCooldown") or 0.0) * tps)
            ),
            attack_scale_micros=round(scale * 1_000_000),
        )
    return None


class BloodHit(_Contract):
    """`BloodHit`: 饥饿啮齿兽 makes an operator bleed, and only an operator.

    `enemy_1335_bldrat_2`「饥饿啮齿兽」 (IS3 **ev-9**, atk 350) ships one skill
    with `spCost` **1** -- charged by its own attacks -- and two figures,
    `blood_value` **30** and `blood_duration` **5.0**.

    PRTS 「饥饿啮齿兽」 技能0「啮咬」, verbatim:

        「触发索敌和普通攻击相同：对目标造成攻击力100%的物理伤害
          技能期间自身成功造成伤害后，若自身未受沉默影响、且**目标为干员**，
          对目标施加 **5秒每秒流失30生命值** 的【流血】效果
          （同名效果重复施加时仅**刷新持续时间**）
          ※此技能不可沉默」

    Three readings that separate it from the bleed this engine already has
    (逐腐兽's `Bleed.*`, 「每秒100点无来源法术普通伤害(该伤害无法致死)」):

    * **流失生命值, not damage.**  No type, no resistance, and PRTS says nothing
      about it being unable to kill -- unlike 逐腐兽's, which states that outright.
    * **Operators only.**  A device or summon it bites does not bleed.
    * **Refreshes, never stacks** -- 「同名效果重复施加时仅刷新持续时间」.
    """

    #: 「每秒流失30生命值」
    hp_loss_per_second: int = Field(gt=0)
    #: 「5秒」
    duration_ticks: int = Field(gt=0)


def blood_hit_skill(data: dict, tps: int) -> "BloodHit | None":
    """Read `BloodHit`, or None."""

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "BloodHit":
            continue
        blackboard = {
            entry["key"]: entry.get("value")
            for entry in (skill.get("blackboard") or [])
            if isinstance(entry, dict) and entry.get("key")
        }
        value = blackboard.get("blood_value")
        duration = blackboard.get("blood_duration")
        if value is None or duration is None:
            continue
        return BloodHit(
            hp_loss_per_second=round(float(value)),
            duration_ticks=max(1, round(float(duration) * tps)),
        )
    return None


class FourDirectionVolley(_Contract):
    """`FourDirAttack`: 【全力吹奏】, one shot down each of the four axes.

    `enemy_1243_ltswar_2`「残党正式萨克斯手」 (IS3 **b-4-b**, atk 1000) ships
    `atk_scale` **1.5** with `cooldown` 20.0 and `initCooldown` 10.0 and no SP
    cost -- a timer, like 【引导】.

    PRTS 「残党正式萨克斯手」 技能0「全力吹奏」:

        「蓄力数秒后向自身周围 **4个正方向** 发射弹道，弹道碰撞半径 **0.2**，
          对**首个**触碰到的我方单位造成攻击力 **150%** 的物理普通伤害
          ※弹道碰撞到重整束流时延迟0.04s后消失」

    Two things that make it different from every other sweep here:

    * **First hit only, per direction.**  Not everything on the line -- the
      front rank shields whoever stands behind it, so a column of operators
      takes one hit, not four.
    * **0.2 collision radius** on a one-tile grid means the ray only touches
      what is actually on the axis.  Nothing diagonal, nothing half a tile off.

    「蓄力数秒」 is the one figure nobody states -- not the blackboard, not PRTS --
    so the volley fires when the cooldown comes round, with the windup recorded
    as an open question rather than invented.
    """

    cooldown_ticks: int = Field(gt=0)
    initial_cooldown_ticks: int = Field(ge=0)
    attack_scale_micros: int = Field(gt=0)


def four_direction_volley(data: dict, tps: int) -> "FourDirectionVolley | None":
    """Read `FourDirAttack`, or None."""

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "FourDirAttack":
            continue
        scale = None
        for entry in skill.get("blackboard") or []:
            if entry.get("key") == "atk_scale":
                value = entry.get("value")
                if isinstance(value, (int, float)):
                    scale = float(value)
        cooldown = float(skill.get("cooldown") or 0.0)
        if scale is None or scale <= 0 or cooldown <= 0:
            continue
        return FourDirectionVolley(
            cooldown_ticks=max(1, round(cooldown * tps)),
            initial_cooldown_ticks=max(
                0, round(float(skill.get("initCooldown") or 0.0) * tps)
            ),
            attack_scale_micros=round(scale * 1_000_000),
        )
    return None


class IceBomb(_Contract):
    """`bomb`: 虚幻 drops one bomb in the whole battle, and then it is spent.

    `enemy_1070_iced`「虚幻」 (IS3 **b-4-b** and **b-6-b**, atk 500, range 2.5,
    【飞行单位】) ships `bomb` with `freeze` **10.0**, `cooldown` 5.0,
    `initCooldown` 5.0 and no SP cost.

    PRTS 「虚幻」 技能0「轰炸」, verbatim:

        「**仅攻击范围内存在非飞行我方单位时可触发**：
          对目标及其**周围八格**的我方单位造成 **100%** 法术伤害，
          附加 **10秒寒冷**
          ※对主目标造成法术普通伤害，对溅射目标造成法术溅射伤害，可溅射飞行单位，
            伤害无视迷彩
          ※**此技能仅能触发一次**，不可沉默」

    and its 天赋 is 「静态刚体，**不进行普通攻击**」 -- the bomb is the only thing
    it ever does.

    Three readings, and the shipped data carries none of them:

    * 「仅…时可触发」 again -- the third carrier in this topic whose gate is a
      **condition** rather than a timer (R-31's 【超负载】 and 吞噬者's 【吞噬】
      were the first two), and here the condition is that a **non-flying**
      friendly is in range.  A squad of drones walks past it untouched.
    * **仅能触发一次**: a latch, not a cooldown.  The shipped `cooldown` 5.0 is
      the delay before the one bomb, not a cadence.
    * **周围八格**, and the splash reaches flying units even though the trigger
      does not count them.
    """

    #: 「10秒寒冷」 in ticks.
    cold_ticks: int = Field(gt=0)
    #: The shipped `cooldown` -- the wait before the single bomb.
    delay_ticks: int = Field(gt=0)
    #: 「100%法术伤害」
    attack_scale_micros: int = Field(default=1_000_000, gt=0)


def ice_bomb_skill(data: dict, tps: int) -> "IceBomb | None":
    """Read `bomb`, or None."""

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "bomb":
            continue
        freeze = None
        for entry in skill.get("blackboard") or []:
            if entry.get("key") == "freeze":
                value = entry.get("value")
                if isinstance(value, (int, float)):
                    freeze = float(value)
        cooldown = float(skill.get("cooldown") or 0.0)
        if freeze is None or freeze <= 0 or cooldown <= 0:
            continue
        return IceBomb(
            cold_ticks=max(1, round(freeze * tps)),
            delay_ticks=max(1, round(cooldown * tps)),
        )
    return None


#: 「每0.1秒降低一次」 -- the decay is stepped, not continuous.
SPAWN_DECAY_STEP_TICKS = 3


class SpawnDecay(_Contract):
    """`Weaken.*`: 净浊之焰 is worth less the longer you leave it alone.

    `enemy_1177_dufrbl_2`「净浊之焰」 (IS3 **ev-6**, atk 600, hp 100, moveSpeed
    2.5) ships `Weaken.duration` **10.0** and `Weaken.atk` **-0.7**, and its
    description is empty -- everything is on PRTS 「净浊之焰」 天赋:

        「攻击力在**登场10秒内线性降低至30%**（每0.1秒降低一次，
          **普通攻击开始时停止降低**），**登场10秒后强制击杀自身**」

    So the two shipped figures are the window and the floor: -0.7 leaves 30%,
    and the same ten seconds is also its whole life.

    Three readings that a bare 「-0.7 over 10 s」 would miss:

    * **Linear and stepped.**  「每0.1秒降低一次」 is three ticks at 30 TPS, so
      the attack falls in twenty-eight steps rather than smoothly -- and a
      carrier caught at 5.05 s is worth what it was at 5.0 s.
    * **The clock stops when it attacks.**  Whatever the explosion is worth when
      it starts, it stays worth.
    * **The ten seconds are also a fuse.**  Nothing kills it; it kills itself,
      which is why its 100 HP and 「无敌」 do not contradict each other.
    """

    duration_ticks: int = Field(gt=0)
    #: What is left at the end, in micros of the shipped attack: -0.7 -> 300000.
    floor_scale_micros: int = Field(gt=0, le=1_000_000)


def spawn_decay_talent(data: dict, tps: int) -> "SpawnDecay | None":
    """Read `Weaken.duration` + `Weaken.atk`, or None.

    Both are required, and the pair is disjoint from the other `Weak*` family in
    this data (改进型量产巨像's `Weak.def` / 藤蔓's `Weak.damage_scale`) -- a
    ninth prefix collision, and one that separates by key rather than by text.
    """

    rows = data.get("talentBlackboard")
    while isinstance(rows, dict) and "m_value" in rows:
        rows = rows["m_value"]
    blackboard = {
        entry["key"]: entry.get("value")
        for entry in (rows or [])
        if isinstance(entry, dict) and entry.get("key")
    }
    duration = blackboard.get("Weaken.duration")
    drop = blackboard.get("Weaken.atk")
    if duration is None or drop is None:
        return None
    if not -1.0 < float(drop) < 0.0:
        raise ValueError("spawn decay must lose a fraction of its attack")
    return SpawnDecay(
        duration_ticks=max(1, round(float(duration) * tps)),
        floor_scale_micros=round((1.0 + float(drop)) * 1_000_000),
    )


#: 「技能触发0.5s后」 -- the blow lands after the blink, not with it.
BLINK_STRIKE_DELAY_TICKS = 15

#: 「①获得持续1秒的不可阻挡效果」 and 「②……期间拥有不可阻挡，无敌，失衡免疫」
#: over the 1.8 s the teleport takes.  The two windows overlap and the longer one
#: is what a blocker actually experiences, so that is the one modelled.
BLINK_UNBLOCKABLE_TICKS = 54


class BlinkSkill(_Contract):
    """`Blink`: 路加萨尔古斯 answers being blocked by not being there.

    `enemy_2058_smlion`「路加萨尔古斯，历法之王」 (IS3 **b-7** and **b-7-b**,
    atk 1000, hp 180000) ships `dist` **1.5** and `atk_scale` **1.5** with
    `cooldown` 15.0, `initCooldown` 10.0 and no SP cost.

    PRTS 「路加萨尔古斯，历法之王」 技能0「瞬身」, verbatim:

        「**被阻挡时可触发**：
          ①获得持续 **1秒** 的不可阻挡效果
          ②即将瞬移的目的地位于可通行地块时：在 **1.8s** 内向下个检查点方向
            瞬移 **1.5格**（不存在下个检查点时瞬移方向变为移动方向），
            期间拥有不可阻挡，无敌，失衡免疫
          ③技能触发 **0.5s** 后，对**之前阻挡自身的单位**造成攻击力 **150%** 的
            法术普通伤害」

    Two things this shares with the pattern this topic keeps repeating, and one
    that is its own:

    * 「**被阻挡时可触发**」 -- the fourth carrier here gated on a condition
      rather than a clock (R-31's 【超负载】, 吞噬者's 【吞噬】, 虚幻's 【轰炸】).
      Holding it is what sets it off, so a squad that never blocks it never sees
      this skill at all.
    * The blow lands on **whoever was holding it**, half a second after it has
      already gone.  Blocking this boss is a decision with a delayed bill.
    * 「目的地位于可通行地块时」 needs no check here: the blink runs **along its
      own route**, and a route is walkable by construction.

    The other IS3 carrier of `Blink`, 无垠回荡克雷松 `enemy_2056_smedzi`, ships an
    **empty blackboard** and zero cooldowns -- no distance, no scale -- so the
    reader declines it rather than inventing the two figures.
    """

    cooldown_ticks: int = Field(gt=0)
    initial_cooldown_ticks: int = Field(ge=0)
    #: 「瞬移1.5格」 in milli-tiles along the route.
    distance_milli_tiles: int = Field(gt=0)
    #: 「攻击力150%的法术普通伤害」
    attack_scale_micros: int = Field(gt=0)
    strike_delay_ticks: int = Field(default=BLINK_STRIKE_DELAY_TICKS, ge=0)
    unblockable_ticks: int = Field(default=BLINK_UNBLOCKABLE_TICKS, gt=0)


def blink_skill(data: dict, tps: int) -> "BlinkSkill | None":
    """Read `Blink`, or None when the carrier ships no figures for it."""

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "Blink":
            continue
        blackboard = {
            entry["key"]: entry.get("value")
            for entry in (skill.get("blackboard") or [])
            if isinstance(entry, dict) and entry.get("key")
        }
        distance = blackboard.get("dist")
        scale = blackboard.get("atk_scale")
        cooldown = float(skill.get("cooldown") or 0.0)
        if distance is None or scale is None or cooldown <= 0:
            # 无垠回荡克雷松 ships the prefab with nothing in it; guessing a
            # distance would teleport a 180k-HP boss an invented number of tiles.
            continue
        return BlinkSkill(
            cooldown_ticks=max(1, round(cooldown * tps)),
            initial_cooldown_ticks=max(
                0, round(float(skill.get("initCooldown") or 0.0) * tps)
            ),
            distance_milli_tiles=round(float(distance) * 1000),
            attack_scale_micros=round(float(scale) * 1_000_000),
        )
    return None


class ChainBuff(_Contract):
    """`ChainBuff`: 【反自然馈赠】, an attack that helps whoever it hits.

    `enemy_2050_smsha`「陷落雪祀」 (IS3 **b-2**) and `enemy_2051_smsha2`
    「空洞雪祀」 (**b-2-b**) ship all seven figures:

        atk_scale 0.1   chain.atk_scale 1.0   chain.max_target 3 / 4
        projectile_range 1.6 / 2.1   duration 10.0
        attack_speed 100.0   move_speed 1.0

    PRTS 「陷落雪祀」 技能0「反自然馈赠」, verbatim:

        「对**敌人**进行一次攻击，造成攻击力 **10%** 的法术伤害，攻击会在
          **3个目标**间跳跃，跳跃半径 **1.6**
          受影响的目标**攻击速度+100，移动速度+100%，持续10s**
          技能本身与跳跃均**不会选择自身为目标**」

    「敌人」 is written from the player's side, so the targets are the carrier's
    **own allies**: this is an enemy that attacks its own side in order to buff
    it, and the 10% is the price they pay for the speed.

    That makes it the second enemy-on-enemy interaction in this engine after
    狡兽's predation -- and the first one that is not a kill.  「不会选择自身为
    目标」 is stated twice in one sentence, so the exclusion is deliberate.
    """

    cooldown_ticks: int = Field(gt=0)
    initial_cooldown_ticks: int = Field(ge=0)
    #: 「攻击力10%的法术伤害」 on the first link.
    attack_scale_micros: int = Field(gt=0)
    #: 「每次跳跃」's own scale.  The skill ships 1.0 -- no falloff, unlike the
    #: carrier's ordinary attack chain, which loses 15% a jump.
    chain_attack_scale_micros: int = Field(gt=0)
    #: 「3个目标」 / 「4个目标」, the carrier included in the count or not is
    #: settled by 「不会选择自身为目标」: it is not.
    max_targets: int = Field(gt=0, le=8)
    #: 「跳跃半径1.6」 in milli-tiles.
    jump_range_milli_tiles: int = Field(gt=0)
    #: 「攻击速度+100，移动速度+100%，持续10s」
    duration_ticks: int = Field(gt=0)
    attack_speed_bonus: int = Field(ge=0)
    move_speed_bonus_micros: int = Field(ge=0)


def chain_buff_skill(data: dict, tps: int) -> "ChainBuff | None":
    """Read `ChainBuff`, or None."""

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "ChainBuff":
            continue
        blackboard = {
            entry["key"]: entry.get("value")
            for entry in (skill.get("blackboard") or [])
            if isinstance(entry, dict) and entry.get("key")
        }
        needed = (
            "atk_scale",
            "chain.atk_scale",
            "chain.max_target",
            "projectile_range",
            "duration",
            "attack_speed",
            "move_speed",
        )
        if any(blackboard.get(key) is None for key in needed):
            continue
        cooldown = float(skill.get("cooldown") or 0.0)
        if cooldown <= 0:
            continue
        return ChainBuff(
            cooldown_ticks=max(1, round(cooldown * tps)),
            initial_cooldown_ticks=max(
                0, round(float(skill.get("initCooldown") or 0.0) * tps)
            ),
            attack_scale_micros=round(float(blackboard["atk_scale"]) * 1_000_000),
            chain_attack_scale_micros=round(
                float(blackboard["chain.atk_scale"]) * 1_000_000
            ),
            max_targets=round(float(blackboard["chain.max_target"])),
            jump_range_milli_tiles=round(float(blackboard["projectile_range"]) * 1000),
            duration_ticks=max(1, round(float(blackboard["duration"]) * tps)),
            attack_speed_bonus=round(float(blackboard["attack_speed"])),
            move_speed_bonus_micros=round(float(blackboard["move_speed"]) * 1_000_000),
        )
    return None


class DoomSkill(_Contract):
    """`Doom`: 【叹息】, the boss's own timer for a fight that has gone on too long.

    `enemy_2054_smdeer`「萨米的意志」 (IS3 **b-4** and **b-4-b**, atk 800,
    hp 240000) ships `value` **-30.0** and `atk_scale` **1.5** with `cooldown`
    **600.0** and `initCooldown` **600.0** -- ten minutes, and no SP cost.

    PRTS 「“萨米的意志”」 技能0「叹息」, verbatim:

        「对场上**全体**我方单位造成攻击力 **150%** 的法术伤害
          技能结束时**扣除30目标生命值**，随后**自身死亡**」

    So `value` is not damage at all: it is **thirty life points**, on a run that
    carries a handful.  This is not a move you survive and play on from -- it is
    the clock saying the fight is over, and the boss leaves with it.

    That makes the whole b-4 encounter a **damage race with a stated deadline**,
    which is the sort of thing an agent has to know about before it starts
    stalling for value.
    """

    cooldown_ticks: int = Field(gt=0)
    initial_cooldown_ticks: int = Field(ge=0)
    #: 「攻击力150%的法术伤害」 to everything the defender has on the board.
    attack_scale_micros: int = Field(gt=0)
    #: 「扣除30目标生命值」 -- stored positive, because it is a cost.
    life_point_cost: int = Field(ge=0)


def doom_skill(data: dict, tps: int) -> "DoomSkill | None":
    """Read `Doom`, or None."""

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "Doom":
            continue
        blackboard = {
            entry["key"]: entry.get("value")
            for entry in (skill.get("blackboard") or [])
            if isinstance(entry, dict) and entry.get("key")
        }
        scale = blackboard.get("atk_scale")
        value = blackboard.get("value")
        cooldown = float(skill.get("cooldown") or 0.0)
        if scale is None or value is None or cooldown <= 0:
            continue
        return DoomSkill(
            cooldown_ticks=max(1, round(cooldown * tps)),
            initial_cooldown_ticks=max(
                0, round(float(skill.get("initCooldown") or 0.0) * tps)
            ),
            attack_scale_micros=round(float(scale) * 1_000_000),
            life_point_cost=abs(round(float(value))),
        )
    return None


#: 「重生结束后攻击力+50%」 -- PRTS only; the blackboard ships the five seconds
#: and nothing else.
REBORNING_ATTACK_BONUS_MICROS = 500_000


class Reborning(_Contract):
    """`Reborning`: 无垠回荡克雷松 comes back once, whole, and harder.

    `enemy_2056_smedzi`「无垠回荡克雷松」 (IS3 **b-6** and **b-6-b**, atk 1000,
    hp 80000) ships `Reborning` with `duration` **5.0** and nothing else.

    PRTS 「无垠回荡克雷松」 天赋, verbatim:

        「**首次被击倒后**进行持续 **5s** 的重生，**恢复100%生命值**；
          重生开始时释放当前波次，**重生结束后攻击力+50%**」

    So the fight has two halves whether the defender wants them or not: the
    first kill is not a kill, it is a five-second pause after which the same
    boss stands up at full health hitting half again as hard.

    **首次** makes it a latch: killing it a second time is a kill.

    Its 天赋 also opens with 「**无敌**（可使用**空间稳定设施**令无敌短暂失效）」,
    which is the gate on this whole mechanism: the boss cannot be brought to
    zero at all until that device has switched its invincibility off.  Both b-6
    and b-6-b place `trap_107_smpow`「空间稳定设施」, so the chain exists in the
    data even though this engine does not yet run the device half.
    """

    duration_ticks: int = Field(gt=0)
    attack_bonus_micros: int = Field(default=REBORNING_ATTACK_BONUS_MICROS, ge=0)


def reborning_skill(data: dict, tps: int) -> "Reborning | None":
    """Read `Reborning`, or None."""

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "Reborning":
            continue
        for entry in skill.get("blackboard") or []:
            if entry.get("key") != "duration":
                continue
            value = entry.get("value")
            if isinstance(value, (int, float)) and value > 0:
                return Reborning(duration_ticks=max(1, round(float(value) * tps)))
    return None


#: 【献祭】's reach, in milli-tiles.  **PRTS only**: the shipped blackboard
#: carries `atk_scale` and nothing else, so the radii come from the wiki's skill
#: table -- 1.5 tiles normally, 2.5 once the pulse has washed over the caster.
IMMOLATION_RADIUS_MILLI_TILES = 1_500
IMMOLATION_EMPOWERED_RADIUS_MILLI_TILES = 2_500


class Immolation(_Contract):
    """`Immo` / `Immo[Rage]`: 【献祭】, an aura that the pulse makes bigger.

    `enemy_1085_sotiwz`「游击队萨卡兹术师」 (IS3 **b-2-b**, atk 350, hp 7000,
    def 400) ships **both** `Immo` and `Immo[Rage]` with the same blackboard --
    `atk_scale` **1.0**, `cooldown` **5.0**, `initCooldown` **0.0**, no SP cost.

    Enemy 能力, verbatim:

        「周期性施法，对周围所有单位造成相当于攻击力的法术伤害
          接触脉冲波时，**不受伤害**且攻击**范围扩大**」

    PRTS 技能【献祭】: 「攻击力**100%**的**近战途径**法术溅射伤害」, radius
    **1.5**; the 强化模式 copy is the same damage at radius **2.5**.  That is the
    whole difference between the two prefabs, and it is not in the data: the two
    blackboards are identical.

    So `[Rage]` here is not a rage bar.  Its 天赋 says 「**免疫脉冲波**，受
    【脉冲波】影响后可进入**强化模式**」 -- 源石祭坛's pulse, which kills the rest
    of the board, is what upgrades this caster instead.  Standing a shrine's
    pulse next to it is a defender's problem, not the caster's.

    Its 天赋 also says 「**不进行普通攻击**」: everything this enemy does, it does
    through this aura.
    """

    cooldown_ticks: int = Field(gt=0)
    initial_cooldown_ticks: int = Field(ge=0)
    attack_scale_micros: int = Field(gt=0)
    radius_milli_tiles: int = Field(gt=0)
    #: 「接触脉冲波时……攻击范围扩大」, from `Immo[Rage]`.
    empowered_radius_milli_tiles: int = Field(gt=0)


def immolation_skill(data: dict, tps: int) -> "Immolation | None":
    """Read `Immo` (and its `[Rage]` twin), or None."""

    base = None
    empowered = False
    for skill in data.get("skills") or []:
        key = skill.get("prefabKey")
        if key not in {"Immo", "Immo[Rage]"}:
            continue
        if key == "Immo[Rage]":
            empowered = True
            continue
        blackboard = {
            entry["key"]: entry.get("value")
            for entry in (skill.get("blackboard") or [])
            if isinstance(entry, dict) and entry.get("key")
        }
        scale = blackboard.get("atk_scale")
        cooldown = float(skill.get("cooldown") or 0.0)
        if scale is None or cooldown <= 0:
            continue
        base = (scale, cooldown, float(skill.get("initCooldown") or 0.0))
    if base is None:
        return None
    scale, cooldown, initial = base
    return Immolation(
        cooldown_ticks=max(1, round(cooldown * tps)),
        initial_cooldown_ticks=max(0, round(initial * tps)),
        attack_scale_micros=round(float(scale) * 1_000_000),
        radius_milli_tiles=IMMOLATION_RADIUS_MILLI_TILES,
        empowered_radius_milli_tiles=(
            IMMOLATION_EMPOWERED_RADIUS_MILLI_TILES
            if empowered
            else IMMOLATION_RADIUS_MILLI_TILES
        ),
    )


#: 「未激活的R系列动力装甲」, by id.  These are **tokens**, not enemies: the
#: shipped `character_table` names them 「R-31重型动力装甲」/「R-11突击动力装甲」
#: and their skill 【研究成功】 is 「吸收范围内的实验产物回复技力，技力满时启动」
#: -- they stand on the map inert until something charges them up.
#:
#: Scanned the whole `character_table` for 「动力装甲」: exactly four ids, all
#: `TRAP`.  IS3 places two of them, both in **4-2**, and the activated forms are
#: separate *enemies* (`enemy_1255_lybgpa` / `enemy_1256_lyacpa`) -- so in this
#: engine 「未激活」 needs no flag: an armour that is a summon **is** the inactive
#: one.
INACTIVE_POWER_ARMOUR_SUMMON_IDS = frozenset(
    {
        "trap_075_bgarmn",   # R-31重型动力装甲
        "trap_076_bgarms",   # R-31a重型动力装甲
        "trap_077_rmtarmn",  # R-11突击动力装甲
        "trap_078_rmtarms",  # R-11a突击动力装甲
    }
)

#: 「**每秒**治疗目标最大生命值5%」 -- PRTS only; the blackboard ships the window
#: and the fraction, never the beat.
REPAIR_INTERVAL_TICKS = 30


class RepairSkill(_Contract):
    """`Charge`: 【修复】, an enemy whose whole job is keeping a machine alive.

    `enemy_1250_lyengs`「莱茵生命工程科成员」 (IS3 **4-2**; the 高级 variant
    `_2` stands in **t-4**) ships `hit_duration` **10.0** and `hp_ratio`
    **0.05** with `cooldown` **30.0** and `initCooldown` **5.0**.

    PRTS 技能0「修复」, verbatim:

        「**仅攻击范围内存在生命值未满的未激活的<R系列动力装甲>时可触发**：
          锁定**距离自身最近**的目标持续施法，最长持续 **10秒**，每秒治疗目标
          **最大生命值5%** 的生命值。每次治疗后若目标生命值已满，**立刻打断技能**。
          施法期间**受到沉默影响后，立即结束技能**。**索敌不受阻挡影响**」

    So 4-2 is not only a fight against what walks in: four R-31 and one R-11 sit
    on the board from the first tick, and the defender's damage on them is being
    undone at **5% of 25000 = 1250 a second, for ten seconds** by anything with
    this skill still standing.  Killing the engineer is the way to make that
    damage stick.

    The trigger sentence is the fifth 「仅…时可触发」 in this theme, and like the
    other four it is **not in the shipped blackboard** -- only PRTS has it.
    """

    cooldown_ticks: int = Field(gt=0)
    initial_cooldown_ticks: int = Field(ge=0)
    #: 「最长持续10秒」
    duration_ticks: int = Field(gt=0)
    #: 「每秒治疗目标最大生命值5%」
    hp_ratio_micros: int = Field(gt=0)
    interval_ticks: int = Field(default=REPAIR_INTERVAL_TICKS, gt=0)


def repair_skill(data: dict, tps: int) -> "RepairSkill | None":
    """Read `Charge`, or None."""

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "Charge":
            continue
        blackboard = {
            entry["key"]: entry.get("value")
            for entry in (skill.get("blackboard") or [])
            if isinstance(entry, dict) and entry.get("key")
        }
        duration = blackboard.get("hit_duration")
        ratio = blackboard.get("hp_ratio")
        cooldown = float(skill.get("cooldown") or 0.0)
        if duration is None or ratio is None or cooldown <= 0:
            continue
        return RepairSkill(
            cooldown_ticks=max(1, round(cooldown * tps)),
            initial_cooldown_ticks=max(
                0, round(float(skill.get("initCooldown") or 0.0) * tps)
            ),
            duration_ticks=max(1, round(float(duration) * tps)),
            hp_ratio_micros=round(float(ratio) * 1_000_000),
            interval_ticks=max(1, round(tps)),
        )
    return None


def driver_charge_sp(data: dict) -> int:
    """`Driver.sp`: what this Dor unit is worth to a dormant power armour.

    `enemy_1251_lysyta`「Dor-1号失败品」 ships **50** and
    `enemy_1253_lysytc`「Dor-3号改造体」 ships **100** (both IS3 **4-2**).

    The ability line reads 「**接触**未激活的<R系列动力装甲>时，为其回复一定技力」,
    but the 天赋 is the precise one and it is not about touching:

        「被未激活的<R系列动力装甲>**击杀**时，为其回复 **50SP**」  (Dor-1)
        「被未激活的R系列动力装甲**击杀**时，为其回复 **100SP**」   (Dor-3)

    So the armour does not absorb what walks past -- it **kills** it.  Its own
    skill says the same thing from the other side: 「**吸收范围内的实验产物**回复
    技力，技力满时启动」, and the Dor units are 「多萝西实验」的产物.

    That is what makes 4-2 a race rather than a timer: **100 SP is one Dor-3 or
    two Dor-1**, and both of those are killable before they reach the armour.
    """

    for entry in data.get("talentBlackboard") or []:
        if not isinstance(entry, dict) or entry.get("key") != "Driver.sp":
            continue
        value = entry.get("value")
        if isinstance(value, (int, float)) and value > 0:
            return round(float(value))
    return 0


#: 「**技能期间持续造成**法术和灼燃损伤」 -- the carrier that actually sprays says
#: so in its own shipped description.  `Flame` is also shipped by 深池伙友卫队
#: (ev-6) and its elite (5-4), whose PRTS page lists **no skill at all** and
#: whose description says nothing about a skill's damage: their blackboard is a
#: leftover, so this reader refuses them rather than inventing a second flame.
FLAME_CARRIER_PHRASE = "技能期间持续造成"


class FlameSkill(_Contract):
    """`Flame`: 【焚毁】, one operator held in a jet of fire for ten seconds.

    `enemy_1173_duspfr`「深池焚毁者」 (IS3 **ev-6**, atk 500, range 1.5) ships
    `duspfr_flame[cd].duration` **10.6**, `duspfr_flame[cd].cooldown` **10.0**,
    `duspfr_flame[cd].interval` **6.0**, `hit_interval` **0.5**, `atk_scale`
    **0.12**, `ep_damage_ratio` **0.06**.

    PRTS 技能0「焚毁」, verbatim:

        「技能触发时：若未处于施法状态，则进入持续 **10.6s** 的施法状态。施法状态
          下释放此技能时立刻清空冷却。施法状态下**受到沉默/眩晕效果时，立刻退出施法
          状态**。施法状态下**持续6s后，进入冷却时间**。**退出施法状态时，中断技能
          效果并进入冷却时间**。技能效果：**锁定攻击范围内一名我方单位**，持续喷射
          火焰，**每0.5s**对目标造成攻击力 **12%** 的法术普通伤害和攻击力 **6%** 的
          灼燃损伤。※**不可对空**」

    So this is not a burst: it is a **sustained hold on one target**, 21 hits of
    60 arts damage plus 30 points of 灼燃 build-up each, and the way to stop it
    early is a stun or a silence rather than out-healing it.
    """

    cast_duration_ticks: int = Field(gt=0)
    cooldown_ticks: int = Field(gt=0)
    #: 「施法状态下持续6s后，进入冷却时间」 -- the cooldown starts *inside* the
    #: cast, not after it.  Zero when the carrier ships no such figure.
    cooldown_starts_after_ticks: int = Field(default=0, ge=0)
    hit_interval_ticks: int = Field(gt=0)
    attack_scale_micros: int = Field(gt=0)
    #: 「攻击力6%的灼燃损伤」, applied as elemental build-up.
    element_ratio_micros: int = Field(default=0, ge=0)


def flame_skill(data: dict, tps: int) -> "FlameSkill | None":
    """Read `Flame`, or None."""

    description = data.get("description")
    while isinstance(description, dict) and "m_value" in description:
        description = description["m_value"]
    if not isinstance(description, str) or FLAME_CARRIER_PHRASE not in description:
        return None
    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "Flame":
            continue
        blackboard = {
            entry["key"]: entry.get("value")
            for entry in (skill.get("blackboard") or [])
            if isinstance(entry, dict) and entry.get("key")
        }
        duration = None
        cooldown = None
        window = None
        for key, value in blackboard.items():
            if key.endswith(".duration"):
                duration = value
            elif key.endswith(".cooldown"):
                cooldown = value
            elif key.endswith(".interval"):
                window = value
        scale = blackboard.get("atk_scale")
        hit = blackboard.get("hit_interval")
        if None in (duration, cooldown, scale, hit):
            continue
        element = blackboard.get("ep_damage_ratio") or 0.0
        return FlameSkill(
            cast_duration_ticks=max(1, round(float(duration) * tps)),
            cooldown_ticks=max(1, round(float(cooldown) * tps)),
            cooldown_starts_after_ticks=(
                max(0, round(float(window) * tps)) if window is not None else 0
            ),
            hit_interval_ticks=max(1, round(float(hit) * tps)),
            attack_scale_micros=round(float(scale) * 1_000_000),
            element_ratio_micros=round(float(element) * 1_000_000),
        )
    return None


#: 「**每隔一段时间，召唤数个**<枯朽之种>」 -- the phrase that names what is
#: summoned, and the only place the name appears.
BORN_BUGS_PHRASE = "召唤数个"

#: PRTS 技能0「枯朽召唤」: 「**0.7s延迟**后，在自身位置，自身位置左1.0，自身位置
#: 右1.0，以自身路径**各召唤1个**枯朽之种」.  Neither the delay nor the count is
#: in the shipped blackboard -- it is empty.
BORN_BUGS_DELAY_SECONDS = 0.7
BORN_BUGS_COUNT = 3


class BornBugs(_Contract):
    """`BornBugs`: 【枯朽召唤】, an enemy whose whole output is more enemies.

    `enemy_1321_wdarft`「枯朽萃聚使徒」 and `_2`「腐败萃聚使徒」 (both IS3 **5-2**,
    hp 30000/35000, flying, immune to stun/silence/sleep/freeze/levitate) ship
    `BornBugs` with `cooldown` **10.0**, `initCooldown` **10.0** and an **empty
    blackboard**.

    PRTS 技能0「枯朽召唤」, verbatim:

        「**0.7s延迟**后，在**自身位置**，自身位置**左1.0**，自身位置**右1.0**，
          **以自身路径**各召唤 **1** 个枯朽之种」

    So the apostle itself never attacks (天赋: 「静态刚体，**不进行普通攻击**」) --
    it is a 30000-HP delivery van, and what matters is that **三个种子每十秒**
    join its route behind it.  「以自身路径」 is what makes this one summonable in
    this engine at all: the seeds inherit the summoner's route rather than
    needing a tile of their own.
    """

    summon_enemy_id: str = Field(min_length=1)
    cooldown_ticks: int = Field(gt=0)
    initial_cooldown_ticks: int = Field(ge=0)
    #: 「0.7s延迟后」
    delay_ticks: int = Field(ge=0)
    count: int = Field(gt=0)


def born_bugs_skill(
    data: dict, tps: int, names_to_ids: "dict[str, str] | None" = None
) -> "BornBugs | None":
    """Read `BornBugs`, resolving what it summons from its description."""

    if not any(
        isinstance(skill, dict) and skill.get("prefabKey") == "BornBugs"
        for skill in data.get("skills") or []
    ):
        return None
    skill = next(
        skill
        for skill in data["skills"]
        if isinstance(skill, dict) and skill.get("prefabKey") == "BornBugs"
    )
    description = data.get("description")
    while isinstance(description, dict) and "m_value" in description:
        description = description["m_value"]
    text = strip_description_markup(description if isinstance(description, str) else "")
    if BORN_BUGS_PHRASE not in text:
        raise ValueError("BornBugs carrier names nothing to summon")
    name = text.split(BORN_BUGS_PHRASE, 1)[1].strip("。，,. ")
    resolved = (names_to_ids or {}).get(name)
    if not resolved:
        # The summoned enemy is on the same stage in both shipped cases.  A
        # carrier whose seed is absent would be a different mechanism, and
        # guessing an id would put the wrong thing on the board.
        raise ValueError("BornBugs summons %r, which is not on this stage" % name)
    cooldown = float(skill.get("cooldown") or 0.0)
    if cooldown <= 0:
        return None
    return BornBugs(
        summon_enemy_id=resolved,
        cooldown_ticks=max(1, round(cooldown * tps)),
        initial_cooldown_ticks=max(
            0, round(float(skill.get("initCooldown") or 0.0) * tps)
        ),
        delay_ticks=max(0, round(BORN_BUGS_DELAY_SECONDS * tps)),
        count=BORN_BUGS_COUNT,
    )


#: 「接近干员时，**自毁**并对其造成物理伤害」.  Scanned all 63 IS3 stages: exactly
#: two carriers, 枯朽之种 `enemy_1269_nhfly` and 腐败之种 `_2` (5-2), and nothing
#: else in the theme uses the word.
SELF_DESTRUCT_PHRASE = "自毁"


def dies_after_attacking(description: object) -> bool:
    """Whether this enemy's one attack is also its death.

    PRTS 「枯朽之种」 天赋, verbatim:

        「静态刚体；**普通攻击结束时，若自身未持有晕眩/冻结/浮空/沉睡，强制击杀
          自身**；攻击弹道飞行速度10」

    Two things worth saying out loud.  First, **none of this is in the shipped
    record**: the seed's `talentBlackboard` is empty and it has no skills, so
    without PRTS it reads as an ordinary flying shooter that keeps firing.

    Second, the condition runs the way round nobody expects: **being stunned or
    frozen is what keeps it alive**.  A defender who freezes the swarm is not
    stopping the bombs, they are keeping the bombers on the board.
    """

    while isinstance(description, dict) and "m_value" in description:
        description = description["m_value"]
    if not isinstance(description, str):
        return False
    return SELF_DESTRUCT_PHRASE in strip_description_markup(description)


#: 「**处于重整束流上时**，防御和法术抗性大幅下降」 -- 改进型量产巨像's own
#: description, and the only thing tying `Weak.def` / `Weak.magic_resistance` to
#: the beam rather than to some other weakness.
BEAM_WEAKNESS_PHRASE = "处于重整束流上时"


class BeamWeakness(_Contract):
    """`Weak.def` + `Weak.magic_resistance`: soft while it stands in the light.

    `enemy_1240_ltgint_2`「改进型量产巨像」 (IS3 **5-1**) ships **-1200** defence
    and **-50** magic resistance, and its description says when:

        「**阻挡能力大于等于2**的作战人员可以阻止；**处于重整束流上时**，防御和
          法术抗性大幅下降」

    That is 5-1's answer to a colossus nothing else can dent: **steer the beam
    onto it**.  PRTS 「“低音号”」 gives the steering rule -- 「部署在重整束流上的
    每一位我方干员，都会令重整束流在这之后的流向变为该干员的部署方向」 -- so where
    the defender stands decides where the light goes and therefore what melts.
    """

    defence_loss: int = Field(gt=0)
    magic_resistance_loss: int = Field(gt=0)


def beam_weakness_talent(data: dict) -> "BeamWeakness | None":
    """Read the on-beam weakness, or None."""

    description = data.get("description")
    while isinstance(description, dict) and "m_value" in description:
        description = description["m_value"]
    text = strip_description_markup(description if isinstance(description, str) else "")
    if BEAM_WEAKNESS_PHRASE not in text:
        return None
    rows = data.get("talentBlackboard")
    while isinstance(rows, dict) and "m_value" in rows:
        rows = rows["m_value"]
    values = {
        entry.get("key"): entry.get("value")
        for entry in (rows or [])
        if isinstance(entry, dict) and entry.get("key")
    }
    defence = values.get("Weak.def")
    resistance = values.get("Weak.magic_resistance")
    if not isinstance(defence, (int, float)) or not isinstance(
        resistance, (int, float)
    ):
        raise ValueError("on-beam weakness needs both Weak.def and Weak.magic_resistance")
    if defence >= 0 or resistance >= 0:
        raise ValueError("on-beam weakness is shipped as a pair of losses")
    return BeamWeakness(
        defence_loss=round(-float(defence)),
        magic_resistance_loss=round(-float(resistance)),
    )


#: `AOEDamage.range_id` ships the string **"x-4"**, which `range_table.json`
#: gives as the 3x3 block around the target.  PRTS says 「目标周围**8格**内的
#: **其他**所有我方单位」, so the centre is dropped: the struck target is not in
#: its own splash.
AOE_DAMAGE_RANGE_X4 = (
    (-1, -1), (0, -1), (1, -1),
    (-1, 0), (1, 0),
    (-1, 1), (0, 1), (1, 1),
)

#: 「攻击**重整束流之外**的目标时」 -- the phrase that ties this splash to the
#: beam, and the reason the condition is a **negative** one.
BEAM_SPLASH_PHRASE = "攻击重整束流之外的目标时"


class BeamSplash(_Contract):
    """`AOEDamage`: 历战高塔术师 punishes the tiles the light does not reach.

    `enemy_1241_ltmage_2`「历战高塔术师」 (IS3 **5-1**) ships
    `AOEDamage.atk_scale` **1.0** and `AOEDamage.range_id` **"x-4"**.

    PRTS 「历战高塔术师」 天赋, verbatim:

        「可同时攻击 **2** 个目标，造成**远程途径**伤害
          在场期间，攻击击中**未处于重整束流上**的目标时，额外对目标**周围8格内的
          其他所有我方单位**造成攻击力 **100%** 的**近战途径法术普通伤害**」

    **The condition is negative**, which is what makes 5-1 a puzzle rather than a
    damage check: standing in the beam is what stops the splash, and the beam is
    steered by where the defender stands.  The same light that softens the
    colossus also shelters whoever is under it.
    """

    attack_scale_micros: int = Field(gt=0)
    range_offsets: tuple[tuple[int, int], ...] = Field(min_length=1)


def beam_splash_talent(data: dict) -> "BeamSplash | None":
    """Read the off-beam splash, or None."""

    description = data.get("description")
    while isinstance(description, dict) and "m_value" in description:
        description = description["m_value"]
    text = strip_description_markup(description if isinstance(description, str) else "")
    if BEAM_SPLASH_PHRASE not in text:
        return None
    rows = data.get("talentBlackboard")
    while isinstance(rows, dict) and "m_value" in rows:
        rows = rows["m_value"]
    scale = None
    range_id = None
    for entry in rows or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("key") == "AOEDamage.atk_scale":
            scale = entry.get("value")
        elif entry.get("key") == "AOEDamage.range_id":
            range_id = entry.get("valueStr")
    if not isinstance(scale, (int, float)) or scale <= 0:
        raise ValueError("off-beam splash needs AOEDamage.atk_scale")
    if range_id != "x-4":
        # The only shipped grid for this family.  A different one would be a
        # different reach, and inventing it would be worse than refusing.
        raise ValueError("off-beam splash ships an unknown range_id %r" % (range_id,))
    return BeamSplash(
        attack_scale_micros=round(float(scale) * 1_000_000),
        range_offsets=AOE_DAMAGE_RANGE_X4,
    )


#: 「攻击能够**沿着重整束流传导**」 -- the ability line, and the only carrier of
#: it in this theme.
BEAM_CONDUCTION_PHRASE = "沿着重整束流传导"


class BeamConduction(_Contract):
    """`EmitProjectile`: the shot runs both ways down the light.

    `enemy_1242_ltsmag_2`「残党首席小提琴家」 (IS3 **5-1**) ships
    `EmitProjectile.attack@atk_scale_2` **0.5** and
    `EmitProjectile.attack@max_target` **2**.

    PRTS 「残党首席小提琴家」 天赋, verbatim:

        「**优先攻击位于重整束流上的目标**。攻击命中重整束流上的目标后，分裂出按
          **正/反方向沿重整束流移动**的弹道，弹道对**触碰到的所有我方单位**造成
          攻击力 **50%** 的法术普通伤害」

    So `max_target` **2** is **two projectiles, forward and backward** -- not two
    extra targets, which is what this row was guessed to mean before PRTS was
    read.  The pair travels the whole beam, so everyone standing in the light
    takes the shot.

    That closes 5-1's triangle: the beam softens the colossus, shelters whoever
    stands in it from the tower mage's splash, and **hands the violinist a line
    straight through your team**.
    """

    attack_scale_micros: int = Field(gt=0)
    #: 「正/反方向」 -- two, and the shipped `max_target` says the same.
    directions: int = Field(gt=0)


def beam_conduction_talent(data: dict) -> "BeamConduction | None":
    """Read the beam conduction, or None."""

    description = data.get("description")
    while isinstance(description, dict) and "m_value" in description:
        description = description["m_value"]
    text = strip_description_markup(description if isinstance(description, str) else "")
    if BEAM_CONDUCTION_PHRASE not in text:
        return None
    rows = data.get("talentBlackboard")
    while isinstance(rows, dict) and "m_value" in rows:
        rows = rows["m_value"]
    values = {
        entry.get("key"): entry.get("value")
        for entry in (rows or [])
        if isinstance(entry, dict) and entry.get("key")
    }
    scale = values.get("EmitProjectile.attack@atk_scale_2")
    count = values.get("EmitProjectile.attack@max_target")
    if not isinstance(scale, (int, float)) or not isinstance(count, (int, float)):
        raise ValueError("beam conduction needs both its scale and its count")
    return BeamConduction(
        attack_scale_micros=round(float(scale) * 1_000_000),
        directions=round(float(count)),
    )


#: 「**每秒**执行两项操作」 and 「对 **0.7半径** 范围内」 -- PRTS only; the shipped
#: blackboard has the two scales and nothing else.
CROSSROADS_INTERVAL_TICKS = 30
CROSSROADS_CLOSE_RADIUS_MILLI_TILES = 700


class CrossroadsVolley(_Contract):
    """`AttackTrigger`: a cross of rays and a ring, once a second, forever.

    `enemy_1331_cbsisy`「“十字路口”量产型」 (IS3 **5-8**) ships
    `AttackTrigger.attack@FourDirAttack` **0.5** and
    `AttackTrigger.attack@CloseAttack` **1.0**.

    PRTS 「“十字路口”量产型」 天赋, verbatim:

        「免疫失衡，**不进行普通攻击**；十字范围内的行星碎屑无视重力且不断被推动；
          上下尖端处的行星碎屑无视重力且移动速度-70%
          **每秒执行两项操作**：①向**四个正方向**各发射弹道，造成攻击力 **50%** 的
          **远程**法术伤害；②对 **0.7半径** 范围内造成攻击力 **100%** 的**近战**
          法术伤害」

    So it has no ordinary attack at all: it walks, and once a second it throws a
    cross and a ring.  Standing next to it costs **150%** of its attack -- the
    ring and the axis ray both land -- while standing on its row or column at
    range costs half.

    The 行星碎屑 half is a separate mechanism and is registered, not implemented:
    5-8 has no debris entity for it to push.
    """

    four_direction_scale_micros: int = Field(gt=0)
    close_scale_micros: int = Field(gt=0)
    interval_ticks: int = Field(default=CROSSROADS_INTERVAL_TICKS, gt=0)
    close_radius_milli_tiles: int = Field(
        default=CROSSROADS_CLOSE_RADIUS_MILLI_TILES, gt=0
    )


def crossroads_volley_talent(data: dict) -> "CrossroadsVolley | None":
    """Read `AttackTrigger`'s pair, or None."""

    rows = data.get("talentBlackboard")
    while isinstance(rows, dict) and "m_value" in rows:
        rows = rows["m_value"]
    values = {
        entry.get("key"): entry.get("value")
        for entry in (rows or [])
        if isinstance(entry, dict) and entry.get("key")
    }
    ranged = values.get("AttackTrigger.attack@FourDirAttack")
    close = values.get("AttackTrigger.attack@CloseAttack")
    if ranged is None and close is None:
        return None
    if not isinstance(ranged, (int, float)) or not isinstance(close, (int, float)):
        raise ValueError(
            "the crossroads volley needs both halves; one alone is not the shipped shape"
        )
    return CrossroadsVolley(
        four_direction_scale_micros=round(float(ranged) * 1_000_000),
        close_scale_micros=round(float(close) * 1_000_000),
    )


#: 「对目标及其**周围八格**的我方单位造成100%物理伤害」 -- the same 3x3 the tower
#: mage's splash uses, and for the same reason: the shipped grids for this shape
#: are nine tiles and the text says eight plus the target.
BOMB_RUN_ATTACK_SCALE_MICROS = 1_000_000


class BombRun(_Contract):
    """`boomb`: 暴鸰 carries one bomb, drops it, and doubles its speed.

    `enemy_1040_bombd`「暴鸰」 (IS3 **5-3**, flying, atk 800, range 2.0) ships
    `move_speed` **2.0** with `cooldown` **1.0** and `initCooldown` **1.0** --
    the speed, and nothing about the bomb.

    PRTS 技能0「投弹」, verbatim:

        「**仅攻击范围内存在我方单位时可触发**：
          对**目标及其周围八格**的我方单位造成 **100%** 物理伤害
          ※对主目标造成物理普通伤害，对溅射目标造成物理溅射伤害，伤害无视迷彩
          技能结束后**移速最终提升至200%**
          ※此技能**仅能触发一次**，不可沉默」

    and its 天赋 is 「静态刚体，**不进行普通攻击**」.

    So the whole enemy is one delivery: it flies in, drops a single 3x3 bomb the
    moment anything is in reach, and then leaves at **twice** the speed with
    nothing left to give.  Killing it before it arrives is the only way to stop
    the bomb; killing it afterwards is pointless.

    The trigger sentence is the **sixth** 「仅…时可触发」 in this theme, and like
    the other five it is not in the shipped blackboard.
    """

    move_speed_multiple_micros: int = Field(gt=0)
    attack_scale_micros: int = Field(default=BOMB_RUN_ATTACK_SCALE_MICROS, gt=0)
    initial_cooldown_ticks: int = Field(default=0, ge=0)


def bomb_run_skill(data: dict, tps: int) -> "BombRun | None":
    """Read `boomb`, or None."""

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "boomb":
            continue
        speed = None
        for entry in skill.get("blackboard") or []:
            if isinstance(entry, dict) and entry.get("key") == "move_speed":
                speed = entry.get("value")
        if not isinstance(speed, (int, float)) or speed <= 0:
            continue
        return BombRun(
            move_speed_multiple_micros=round(float(speed) * 1_000_000),
            initial_cooldown_ticks=max(
                0, round(float(skill.get("initCooldown") or 0.0) * tps)
            ),
        )
    return None


def overload_skill(data: dict, tps: int) -> "OverloadSkill | None":
    """Read 【超负载】 off an enemy record.

    Requires the whole shipped shape -- window, damage, stun and the end
    animation -- because each figure alone describes a different mechanism and
    the engine has no way to fill a missing one.
    """

    for skill in data.get("skills") or []:
        if skill.get("prefabKey") != "Punch":
            continue
        blackboard = {
            entry["key"]: entry.get("value")
            for entry in (skill.get("blackboard") or [])
            if isinstance(entry, dict) and entry.get("key")
        }
        duration = blackboard.get("duration")
        scale = blackboard.get("atk_scale")
        stun = blackboard.get("stun")
        unmovable = blackboard.get("unmovable_duration")
        cooldown = float(skill.get("cooldown") or 0.0)
        if None in (duration, scale, stun, unmovable) or cooldown <= 0:
            continue
        return OverloadSkill(
            duration_ticks=max(1, round(float(duration) * tps)),
            # 「每拳间隔1s」 -- PRTS, not the blackboard: the cadence is a fixed
            # interval rather than a speed bonus, which is why nothing in the
            # shipped data carries it.
            punch_interval_ticks=max(1, tps),
            cooldown_ticks=max(1, round(cooldown * tps)),
            initial_cooldown_ticks=max(
                0, round(float(skill.get("initCooldown") or 0.0) * tps)
            ),
            attack_scale_micros=max(1, round(float(scale) * 1_000_000)),
            stun_seconds_micros=max(1, round(float(stun) * 1_000_000)),
            trigger_radius_milli_tiles=OVERLOAD_TRIGGER_RADIUS_MILLI_TILES,
            recovery_unmovable_ticks=max(0, round(float(unmovable) * tps)),
        )
    return None

#: 「半径0.7范围内存在非吐噬免疫的我方干员/召唤物」 -- PRTS only, like R-31's 0.8.
DEVOUR_TRIGGER_RADIUS_MILLI_TILES = 700


class DevourSkill(_Contract):
    """萨卡兹枯朽吞噬者's 吞噬: it eats the operator holding it.

    `enemy_1271_nhkodo` (IS3 4-5, defence **0**, move speed 0.6):
    「首次被阻挡时吞噬阻挡者，防御力大幅提升但移动速度下降，被击倒时干员返回」,
    shipping `Stronger.def` **700**, `Stronger.move_speed` **-0.3** and a
    `Devour` skill entry.

    PRTS 「萨卡兹枯朽吞噬者」, verbatim:

        「仅**被阻挡**、且半径0.7范围内存在非【吞噬免疫】的我方干员/召唤物
          （无视无法选择，不可对空）时可触发：【吞噬】阻挡单位
          （无法击倒拥有【吞噬免疫】的召唤物/装置）
          此技能成功命中目标后，切换至**饱腹形态**」
        天赋: 「**饱腹形态**：防御力+700，移动速度-30%」

    The same trigger shape as R-31's 【超负载】 -- **被阻挡** plus a small
    radius -- which is worth noticing: two unrelated enemies in this topic gate
    a signature skill on being held rather than on a timer, so "it only does
    this if you block it" is a real design pattern here and not a one-off.

    What makes this one different from every other mechanic in the engine is
    that it **removes one of ours from the board** and gives it back when the
    carrier dies.  Killing it is not just damage, it is a rescue.
    """

    trigger_radius_milli_tiles: int = Field(gt=0)
    #: 「防御力+700」 in the sated form.
    defence_bonus: int = Field(gt=0)
    #: 「移动速度-30%」 as a bonus fraction in micros, stored positive.
    move_speed_penalty_micros: int = Field(gt=0, le=1_000_000)


def devour_skill(data: dict, tps: int) -> "DevourSkill | None":
    """Read 吞噬 off an enemy record.

    Needs the `Devour` entry **and** both halves of the sated form: a carrier
    with the skill but no stat change would swallow an operator and gain
    nothing, which is not the shipped shape.
    """

    del tps  # every figure here is a stat, not a duration
    if not any(
        skill.get("prefabKey") == "Devour" for skill in data.get("skills") or []
    ):
        return None
    values = {
        entry["key"]: entry.get("value")
        for entry in (data.get("talentBlackboard") or [])
        if isinstance(entry, dict) and entry.get("key")
    }
    defence = values.get("Stronger.def")
    speed = values.get("Stronger.move_speed")
    if not isinstance(defence, (int, float)) or defence <= 0:
        return None
    if not isinstance(speed, (int, float)) or speed >= 0:
        return None
    return DevourSkill(
        trigger_radius_milli_tiles=DEVOUR_TRIGGER_RADIUS_MILLI_TILES,
        defence_bonus=round(float(defence)),
        move_speed_penalty_micros=min(1_000_000, round(-float(speed) * 1_000_000)),
    )

