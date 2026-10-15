# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""Non-operator friendly entities: summons and healing devices.

Moved verbatim out of ``models.py`` so that file fits the 2000-line
budget; ``models.py`` re-exports every name, so importers are unchanged.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, StrictInt, model_validator

from .model_primitives import (
    ControlEffect,
    DeploymentClass,
    Direction,
    EffectRangeOrigin,
    EffectRangeRotation,
    EffectStat,
    EffectTargetSelector,
    GridPoint,
    SkillActivationPolicy,
    SkillSpEvent,
    SkillSpRecoveryMode,
    VisibilityClass,
    _Contract,
)

from .skill_definitions import SkillDefinition  # noqa: F401


class SummonDefinition(_Contract):
    """A deliberately small friendly combat-entity contract."""

    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    summon_id: str = Field(min_length=1)
    max_hp: int = Field(gt=0)
    attack: int = Field(ge=0)
    defense: int = Field(ge=0)
    magic_resistance: int = Field(default=0, ge=0, le=100)
    attack_interval_ticks: int = Field(gt=0)
    attack_range_tiles: int = Field(default=1, ge=0)
    block_count: int = Field(default=0, ge=0)
    can_target_flying: bool = False
    blocks_routes: bool = False
    healing_power: int = Field(default=0, ge=0)
    healing_interval_ticks: int = Field(default=1, gt=0)
    healing_range_tiles: int = Field(default=0, ge=0)
    receives_external_healing: bool = True
    #: 「更容易受到敌方的攻击」 -- how strongly enemies prefer this unit as a
    #: target.  盾卫's 列阵！ ships taunt_level 1 with duration -1 (permanent),
    #: and it stands in all 63 IS3 stages, so this is the most widespread
    #: device behaviour in the topic.  Tiers are compared, never summed.
    #:
    #: Negative tiers are shipped too, and they are not a degenerate zero: the
    #: attribute frame gives `tauntLevel: -1` to every device an enemy has no
    #: business attacking -- 宝箱 / 尖刺宝箱 / 巨壳藤蔼, 山海密语仪,
    #: 汲营枯枝, 街头应急储备 and the two 动力装甲 -- while the devices a
    #: defender actually fields (盾卫, 指挥终端, 城防路障, the hazards) ship 0.
    #: Read from the frame, so the split is the shipped one rather than a list.
    taunt_level: int = Field(default=0, ge=-1, le=3)
    #: 【研究成功】's 「技力满时启动」 threshold -- `sktok_bgarmn_1` /
    #: `sktok_rmtarmn_1` ship `spCost` **100**, `initSp` **0**.  Zero for every
    #: device that has no such skill.
    activation_sp_cost: int = Field(default=0, ge=0)
    #: 「“低音号”」: 「向**部署方向**持续发出重整束流」, and 「L-7 “回音壁”」:
    #: 「**改变重整束流的流向**」.  Neither ships a single figure -- the whole
    #: mechanism is the rule, which lives in the engine.
    #: 尖刺宝箱's 「**反弹部分受到的伤害**」: the fraction of damage taken that
    #: goes straight back to whoever dealt it.  The chest ships `atk_scale`
    #: **0.4** on a talent with no name and no description of its own -- the
    #: word 「反弹」 is in its **skill** text, and the figure is in its talent, so
    #: reading either half alone gets nothing.
    damage_reflect_micros: int = Field(default=0, ge=0)
    beam_emitter: bool = False
    beam_redirector: bool = False
    #: 汲营枯枝 (`trap_086_larva`, IS3 5-2 and ev-3, seventeen instances):
    #: 「释放【污染秧蚀】，使周围的我方单位持续流失生命」, with
    #: `polluted_damage_low` 50 and `polluted_damage_high` 25 in its skill.
    #: PRTS 「汲营枯枝」 confirms both figures as per-second true damage and
    #: supplies the one number the data omits -- 「1.1半径范围」.
    #:
    #: Same mechanic as 食腐使徒's `PollutedArea.*`, so it rides the same
    #: `PollutedZoneState` -- but sourced from a **device**, which is why the
    #: zone's source is no longer required to be an enemy.
    pollution_aura_radius_milli_tiles: int = Field(default=0, ge=0)
    pollution_aura_low_damage: int = Field(default=0, ge=0)
    pollution_aura_high_damage: int = Field(default=0, ge=0)
    #: 源石流发生装置 (`trap_013_blower`, IS3 2-2 and 6-2, eleven instances):
    #: 「向前方3格吹出气流；与气流同向/逆向部署的干员攻击提升/降低；
    #: 同向/逆向移动的敌人移速提升/降低」.  The airflow runs along the
    #: instance's own facing, so the geometry is per instance and the figures
    #: are per definition.  Signed, because the shipped blackboard is signed
    #: (`[equal] +0.3` against `[opposite] -0.3`) -- and that sign is what
    #: proves these are bonus **fractions** rather than resulting multiples.
    airflow_range_tiles: int = Field(default=0, ge=0)
    airflow_operator_attack_same_micros: int = 0
    airflow_operator_attack_opposite_micros: int = 0
    airflow_enemy_move_same_micros: int = 0
    airflow_enemy_move_opposite_micros: int = 0
    #: What this device does to **every enemy on the field** while it stands.
    #: 山海密语仪 (`trap_096_gflag`, IS3 5-6, six instances):
    #: 「在场时，强化所有敌军的攻击速度与移动速度」, blackboard
    #: `attack_speed: 25.0` and `move_speed: 1.15`.  PRTS 「山海密语仪」 gives
    #: the units the blackboard cannot: 「所有敌方单位的攻击速度+25，
    #: 移动速度最终提升至115%（可无限叠加）」 -- so the attack speed is
    #: **points** and the move speed is a **resulting multiple**, not a bonus
    #: fraction.  Stored as milli so ×1.15 is 1150.
    enemy_attack_speed_bonus: int = Field(default=0, ge=0)
    enemy_move_speed_multiple_milli: int = Field(default=0, ge=0)
    #: What the battle loses when this device is destroyed.  指挥终端
    #: (`trap_008_farm`) ships a hidden talent whose description is
    #: 「摧毁时扣除 1 点关卡生命值」 and whose blackboard is `value: -1.0`.
    #: A device the defender must keep alive is a different thing from one that
    #: merely helps, so this is a field rather than a note.
    death_life_point_cost: int = Field(default=0, ge=0)
    #: What the battle gains.  街头应急储备 (`trap_111_wdfarm`) ships
    #: `max_cost: 5.0` and `cost: 5.0` for 「被击败后恢复 5 点费用上限及
    #: 费用」 -- the cap and the pool move together, which is why they are two
    #: shipped keys and two fields.
    death_max_cost_gain: int = Field(default=0, ge=0)
    death_cost_gain: int = Field(default=0, ge=0)
    #: 源石祭坛 (`trap_011_ore`, IS3 **3-2**, five instances) and its skill
    #: `sktok_ore`「脉冲波」: 「向四周喷发脉冲波，对周围所有**我方与敌方**单位造成
    #: **500**真实伤害。并使特殊敌人获得强化」.
    #:
    #: Three things make this unlike every other device field here.  It is the
    #: only device effect that hurts **both sides** -- four of these stand in
    #: 3-2 and each one costs the defender 500 true damage every seven seconds
    #: inside its range.  It is 真实伤害, so neither defence nor resistance
    #: applies.  And PRTS 「源石祭坛」 adds two conditions the blackboard cannot:
    #: 「※无视无法选择」「可对空」 and 「不会对**免疫脉冲波**的单位造成伤害」.
    #:
    #: The cadence is not shipped as a period: `spRecoveryPerSec` 1.0 with
    #: `spCost` 7 and `initSp` 0 is what makes it seven seconds.
    pulse_damage: int = Field(default=0, ge=0)
    pulse_interval_ticks: int = Field(default=0, ge=0)
    #: The 13 tiles of `x-1`, frozen from `range_table.json` rather than
    #: approximated by a radius -- the shipped grid is a diamond two tiles
    #: across each axis, which no single radius reproduces.
    pulse_range_offsets: tuple[tuple[int, int], ...] = ()
    #: 源石冰晶's 【寒潮】: 「发射一道寒霜冲击波，使**周围我方单位**陷入
    #: **寒冷**状态 **10** 秒」.  Same 13-tile `x-1` grid as the shrine's pulse
    #: and the same derived cadence (`spCost` **15** over `spRecoveryPerSec`
    #: **1.0** = fifteen seconds), but the payload is a **state**, not damage --
    #: so it needs its own triple rather than borrowing the pulse's.
    cold_pulse_ticks: int = Field(default=0, ge=0)
    cold_pulse_interval_ticks: int = Field(default=0, ge=0)
    cold_pulse_range_offsets: tuple[tuple[int, int], ...] = ()
    #: 「复仇黑手」`trap_092_vgctrl` 手里的**血债账款**。
    #:
    #: PRTS 装置页: 「该装置作为调控环境<血债账簿>使用，在存有复仇黑手的地图中，
    #: 屏幕正上方会显示<血债账款>的积累进度」「一般配置中总进度为 **500**，每秒
    #: 自然积累速度为 **3/秒**，从零开始积累需约 **166.7** 秒」「**不同的敌人可以
    #: 以其特有的方式影响血债账款的进度**」「进度达到最大后进入**清算时刻**」
    #: 「一般情况下持续 **35s**，持续期间**所有敌人攻击力+20%，防御力+15%**，
    #: 部分敌人激活额外的能力」「清算时刻持续期间，血债账款不再继续累积」，结束后
    #: 「从0开始重新积累」。
    #:
    #: **这条改正了台账上一个错判。** `DeathRattle.sp`(家族士兵 20 / 恫吓者 35 /
    #: 80)与 `Passive.speople_dead_vg.sp`(叙拉古平民, ev-5 改写成 200)此前被记成
    #: 「run 层计数器, 战斗引擎里没有可对齐的行为」——**有**: 它们充的就是这台装置
    #: 的 SP, 满了就是 35 秒全场敌人强化。找不到消费方不等于没有消费方。
    #:
    #: 四个数全部**从出货的技能里读**, 一个都不写死: PRTS 明说「装置携带的不同技能
    #: 代表不同地图中血债账款的初始值、最大上限与清算时刻的持续时间」。
    #: 积累速率是**两张表的乘积**(属性帧 `spRecoveryPerSec` **1.0** × 技能
    #: `spData.increment` **3.0** = 3/秒), 与源石祭坛的脉冲周期同一种取法。
    blood_debt_ledger: "BloodDebtLedger | None" = None
    skills: tuple["SkillDefinition", ...] = ()

    @model_validator(mode="after")
    def route_obstacle_is_not_a_blocker(self) -> "SummonDefinition":
        if self.blocks_routes and self.block_count:
            raise ValueError("route-blocking summons cannot also block enemies")
        if not self.healing_power and self.healing_range_tiles:
            raise ValueError("summon healing range requires positive healing power")
        if self.death_life_point_cost and (
            self.death_max_cost_gain or self.death_cost_gain
        ):
            # 指挥终端 costs a life point; 街头应急储备 pays out.  A device
            # that did both would be a reading of two talents at once, which is
            # how the two got merged in the ledger in the first place.
            raise ValueError(
                "a device cannot both cost a life point and pay out on death"
            )
        pulse_parts = (
            bool(self.pulse_damage),
            bool(self.pulse_interval_ticks),
            bool(self.pulse_range_offsets),
        )
        if any(pulse_parts) and not all(pulse_parts):
            # A pulse with no reach, no cadence or no payload is a half-read
            # skill rather than a device that does less.
            raise ValueError(
                "a pulse needs its damage, its interval and its range together"
            )
        if len(self.pulse_range_offsets) != len(set(self.pulse_range_offsets)):
            raise ValueError("pulse range offsets must be unique")
        cold_parts = (
            bool(self.cold_pulse_ticks),
            bool(self.cold_pulse_interval_ticks),
            bool(self.cold_pulse_range_offsets),
        )
        if any(cold_parts) and not all(cold_parts):
            raise ValueError(
                "a cold pulse needs its duration, its interval and its range together"
            )
        if len(self.cold_pulse_range_offsets) != len(
            set(self.cold_pulse_range_offsets)
        ):
            raise ValueError("cold pulse range offsets must be unique")
        skill_ids = [skill.skill_id for skill in self.skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("skill_id values must be unique within a summon")
        return self


class BloodDebtLedger(_Contract):
    """一张地图上的血债账款: 攒满就进「清算时刻」。"""

    #: 账款上限, 出货在技能的 `spData.spCost`(ev-5 是 **500**)。
    max_charge_milli: int = Field(gt=0)
    #: 开局账款, 出货在 `spData.initSp`(ev-5 是 **0**)。
    initial_charge_milli: int = Field(default=0, ge=0)
    #: 每 tick 自然积累。属性帧 `spRecoveryPerSec` × 技能 `spData.increment`,
    #: 再摊到 tick 上; ev-5 是 3/秒 = 每 tick **100** 千分。
    charge_per_tick_milli: int = Field(gt=0)
    #: 清算时刻持续多少 tick, 出货在技能的 `duration`(ev-5 是 **35** 秒)。
    reckoning_ticks: int = Field(gt=0)
    #: 清算时刻期间**全场敌人**的加成, 出货在技能黑板(`atk` 0.2 / `def` 0.15)。
    reckoning_attack_bonus_micros: int = Field(default=0, ge=0)
    reckoning_defense_bonus_micros: int = Field(default=0, ge=0)
    #: `max_hp` 出货成 **0.0**——**声明的空操作**, 与凶豕兽 `Crash.stun` 的 0 同类:
    #: 数据把它写出来了, 所以它不是漏填。读进来是为了「读全了」, 不是为了用。
    reckoning_max_hp_bonus_micros: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def charge_must_be_reachable(self) -> "BloodDebtLedger":
        if self.initial_charge_milli > self.max_charge_milli:
            raise ValueError("blood debt cannot start above its own ceiling")
        if not (
            self.reckoning_attack_bonus_micros
            or self.reckoning_defense_bonus_micros
        ):
            # 一个既不加攻也不加防的清算时刻只是一个计时器, 那说明黑板没读全。
            raise ValueError("a reckoning must buff attack or defence")
        return self


class InitialSummonDefinition(_Contract):
    """One source-proved friendly entity already present at battle start."""

    instance_id: str = Field(min_length=1)
    summon_id: str = Field(min_length=1)
    position: GridPoint
    source_instance_id: str = Field(default="stage", min_length=1)
    #: Which way it faces, for the devices whose effect has a direction.
    facing: Direction | None = None



class PredefinedTokenDefinition(_Contract):
    """A friendly unit placed on the map before the battle, entering play later.

    The alias is the shipped one (``trap_024_npcsld#1``), because that is what
    the wave action names and what a report has to be traceable to.
    """

    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    alias: str = Field(min_length=1)
    summon_id: str = Field(min_length=1)
    position: GridPoint
    #: The branch this device fires when it is destroyed, if any.  Shipped in
    #: the token's ``overrideSkillBlackboard`` as
    #: ``{"key": "branch_id", "valueStr": "badbox0"}``; 72 of the巨壳藤蔓
    #: instances carry one, plus 汲营枯枝, and two armoured devices.
    branch_id: str | None = Field(default=None, min_length=1)
    #: Tick the activating wave action fires on.  Zero means the token is
    #: already in play at battle start -- the shipped ``hidden: false`` case,
    #: which is 123 of the 535 IS3 token instances.
    activation_tick: int = Field(default=0, ge=0)
    #: The shipped ``direction`` on the token instance.  Dropped until now,
    #: which was fine while no device's effect had a direction; 源石流发生
    #: 装置's airflow is one, and the eleven instances ship three distinct
    #: values, so it is the payload rather than decoration.
    facing: Direction | None = None

class HealingDeviceDefinition(_Contract):
    """A finite, declarative pulse-healing battlefield device contract."""

    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    device_id: str = Field(min_length=1)
    max_hp: int = Field(gt=0)
    pulse_heal: int = Field(gt=0)
    pulse_interval_ticks: int = Field(gt=0)
    healing_range_tiles: int = Field(ge=0)


class BranchSpawnDefinition(_Contract):
    """One enemy spawn inside an event branch.

    Branches are not part of the wave schedule: they fire when the device that
    names them is destroyed, so their spawns carry their own delay relative to
    that moment rather than an absolute tick.
    """

    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    branch_id: str = Field(min_length=1)
    enemy_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    count: int = Field(default=1, ge=1, le=16)
    pre_delay_ticks: int = Field(default=0, ge=0)
    interval_ticks: int = Field(default=0, ge=0)
