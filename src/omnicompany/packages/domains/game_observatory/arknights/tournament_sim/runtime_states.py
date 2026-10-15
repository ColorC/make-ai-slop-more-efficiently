# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""Per-entity mutable battle state carried in the snapshot.

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


class ProjectileState(_Contract):
    instance_id: str = Field(min_length=1)
    source_instance_id: str = Field(min_length=1)
    target_instance_id: str = Field(min_length=1)
    amount: int = Field(gt=0)
    remaining_ticks: int = Field(gt=0, le=300)
    packet_index: int = Field(default=0, ge=0, le=63)
    packet_count: int = Field(default=1, ge=1, le=64)
    splash_attack: int = Field(default=0, ge=0)
    splash_radius_tiles: int = Field(default=0, ge=0, le=20)

    @model_validator(mode="after")
    def splash_payload_is_consistent(self) -> "ProjectileState":
        if self.packet_index >= self.packet_count:
            raise ValueError("projectile packet index must be below packet count")
        if not self.splash_radius_tiles and self.splash_attack:
            raise ValueError("projectile splash attack requires a positive radius")
        return self


class SkillState(_Contract):
    skill_id: str = Field(min_length=1)
    sp: int = Field(default=0, ge=0)
    cooldown_remaining: int = Field(default=0, ge=0)
    active_remaining: int = Field(default=0, ge=0)
    activation_pending: bool = False
    selected_target_id: str | None = Field(default=None, min_length=1)
    armed: bool = False
    armed_event_sequence: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def armed_state_is_consistent(self) -> "SkillState":
        if self.armed != (self.armed_event_sequence is not None):
            raise ValueError("armed state and event sequence are inconsistent")
        if self.armed and self.activation_pending:
            raise ValueError("a skill cannot be armed and activation-pending together")
        return self


class ActiveEffectState(_Contract):
    instance_id: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)
    source_instance_id: str = Field(min_length=1)
    source_skill_id: str = Field(min_length=1)
    target_instance_id: str = Field(min_length=1)
    remaining_ticks: int = Field(gt=0)
    geometry_source_position: GridPoint | None = None
    geometry_source_direction: Direction | None = None
    geometry_cells: tuple[GridPoint, ...] = ()
    geometry_target_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def geometry_snapshot_is_consistent(self) -> "ActiveEffectState":
        has_geometry = bool(self.geometry_cells)
        if has_geometry != (self.geometry_source_position is not None):
            raise ValueError("effect geometry cells and source position must coexist")
        if has_geometry != bool(self.geometry_target_ids):
            raise ValueError("effect geometry cells and target ids must coexist")
        if self.geometry_source_direction is not None and not has_geometry:
            raise ValueError("effect geometry direction requires a geometry snapshot")
        if has_geometry and self.target_instance_id not in self.geometry_target_ids:
            raise ValueError("active effect target is absent from its geometry snapshot")
        if len(self.geometry_cells) != len(set(self.geometry_cells)):
            raise ValueError("effect geometry snapshot cells must be unique")
        if len(self.geometry_target_ids) != len(set(self.geometry_target_ids)):
            raise ValueError("effect geometry snapshot targets must be unique")
        return self


class HealingDeviceState(_Contract):
    instance_id: str = Field(min_length=1)
    device_id: str = Field(min_length=1)
    position: GridPoint
    hp: int = Field(gt=0)
    pulse_cooldown: int = Field(default=0, ge=0)


class RNGStreamState(_Contract):
    stream: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_.:-]+$")
    draws: int = Field(default=0, ge=0)


class SummonState(_Contract):
    instance_id: str = Field(min_length=1)
    summon_id: str = Field(min_length=1)
    source_instance_id: str = Field(min_length=1)
    position: GridPoint
    #: Which way this instance faces.  Only the devices whose effect has a
    #: direction need it -- 源石流发生装置's airflow runs along its own
    #: facing, and the shipped instances carry three distinct values -- so it
    #: is None for everything that does not turn.
    facing: Direction | None = None
    hp: int = Field(ge=0)
    #: 「复仇黑手」手里的血债账款, 千分位。只有那台装置会用到它。
    blood_debt_charge_milli: int = Field(default=0, ge=0)
    #: 清算时刻还剩多少 tick。非零即「全场敌人 +20% 攻 / +15% 防」生效中。
    reckoning_remaining_ticks: int = Field(default=0, ge=0)
    #: 上一击没扣满一点血的那部分, 千分位。伤害的精确值带小数(atk 816 打 def
    #: 800 是 **40.8** 不是 40), 血量是整数, 于是余数必须存下来——否则每击下取整
    #: 会系统性少算, M 线实测约 2%(白板 §2.9)。与 `segment_progress_milli` 同
    #: 一个写法: 精确量在累加器里, 读出来的那个量取整。
    damage_remainder_milli: int = Field(default=0, ge=0, lt=1000)
    attack_cooldown: int = Field(default=0, ge=0)
    heal_cooldown: int = Field(default=0, ge=0)
    blocked_enemy_ids: tuple[str, ...] = ()
    #: Ticks until this device's 脉冲波 goes off.  Seeded from the definition
    #: when the device enters play (`initSp` 0, so the first pulse is a whole
    #: interval away) and reset to the interval each time it fires.
    pulse_remaining_ticks: int = Field(default=0, ge=0)
    #: 【寒潮】's fifteen seconds between shocks, kept apart from the shrine's
    #: pulse clock so a device could in principle carry both.
    cold_pulse_remaining_ticks: int = Field(default=0, ge=0)
    #: 【研究成功】: 「吸收范围内的实验产物回复技力，**技力满时启动**」.  Filled by
    #: killing Dor units (`Driver.sp` 50 / 100) against
    #: :attr:`SummonDefinition.activation_sp_cost` (100).
    activation_sp: int = Field(default=0, ge=0)
    skills: tuple["SkillState", ...] = ()
    coefficient_stun_remaining_ticks: int = Field(default=0, ge=0)
    # Freeze is a *kind* of "cannot act", not a separate timer: every freeze
    # source also writes ``coefficient_stun_remaining_ticks`` so the actor is
    # held, and this field records how much of that hold is specifically
    # freezing.  It exists because PRTS 「霜牡」 scales damage 「对冻结目标」 --
    # against frozen targets, not merely stunned ones -- so the two must be
    # distinguishable.  Invariant: never longer than the hold itself.
    coefficient_frozen_remaining_ticks: int = Field(default=0, ge=0)
    # 敌方寒冷 (enemy cold).  PRTS 异常效果图鉴: cold is **not** a hold -- it is
    # attack speed -30.  A further cold applied while the actor is already cold
    # or frozen becomes 冻结 instead, which is the hold.  Keeping cold separate
    # is what makes that escalation expressible.
    coefficient_cold_remaining_ticks: int = Field(default=0, ge=0)
    # Elemental gauges, one per type, per PRTS 「元素」.  Each fills to
    # ELEMENTAL_GAUGE_MAX and bursts; the cooldown that follows is per element,
    # because two elements can be running against the same actor at once.
    element_neural: int = Field(default=0, ge=0)
    element_erosion: int = Field(default=0, ge=0)
    element_burn: int = Field(default=0, ge=0)
    element_neural_cooldown_ticks: int = Field(default=0, ge=0)
    element_erosion_cooldown_ticks: int = Field(default=0, ge=0)
    element_burn_cooldown_ticks: int = Field(default=0, ge=0)
    # Erosion's burst takes defence away permanently, so it cannot ride the
    # decaying reduction `def_reduce` uses.
    permanent_defence_loss: int = Field(default=0, ge=0)
    enemy_coefficient_defense_reduction: int = Field(default=0, ge=0)
    # 斗志昂扬无人机's death buff, per PRTS 「对我方/召唤物施加…攻击速度+100、
    # 防御力+50%的增益」.  It lands on *this* side, not on the enemies: the
    # engine used to write the mirror-image fields on the enemy runtime.
    # 特工 「攻击时会短暂减少我方单位的阻挡上限」: how much this unit's block cap
    # is currently reduced by, and for how long.  Dropping below what it is
    # already holding is what lets an enemy 「冲破我方防线」.
    block_reduction: int = Field(default=0, ge=0)
    block_reduction_remaining_ticks: int = Field(default=0, ge=0)
    death_buff_remaining_ticks: int = Field(default=0, ge=0)
    death_buff_attack_speed_points_micros: int = Field(default=0, ge=0)
    death_buff_defense_multiplier_milli: int = Field(default=1000, ge=1000)
    coefficient_bleeding_remaining_ticks: int = Field(default=0, ge=0)
    coefficient_bleeding_pulse_remaining_ticks: int = Field(default=0, ge=0, le=30)
    coefficient_bleeding_damage: int = Field(default=0, ge=0)
    #: Whether the bleed running on this actor is **HP loss** rather than the
    #: 「无来源法术普通伤害」 逐腐兽 inflicts.  饥饿啮齿兽's 【流血】 states no
    #: type and no non-lethal clause, so it lands as shipped and can finish an
    #: operator off; 逐腐兽's cannot, and says so.
    coefficient_bleeding_is_hp_loss: bool = False

    @model_validator(mode="after")
    def coefficient_bleeding_state_is_closed(self) -> "SummonState":
        active = self.coefficient_bleeding_remaining_ticks > 0
        if active != bool(
            self.coefficient_bleeding_pulse_remaining_ticks
            and self.coefficient_bleeding_damage
        ):
            raise ValueError("summon bleeding timer and payload are inconsistent")
        return self


class EnemyCoefficientRuntimeState(_Contract):
    """Deterministic mutable state for source-closed enemy coefficient families."""

    lifetime_remaining_ticks: int | None = Field(default=None, ge=0)
    speed_duration_remaining_ticks: int = Field(default=0, ge=0)
    speed_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    post_hit_speed_active: bool = False
    friendly_buff_remaining_ticks: int = Field(default=0, ge=0)
    friendly_attack_speed_additive_micros: int = Field(default=0, ge=0)
    friendly_defense_multiplier_milli: int = Field(default=1000, ge=0, le=10000)
    initial_guard_remaining_ticks: int = Field(default=0, ge=0)
    attacks_committed: int = Field(default=0, ge=0)
    ghost_created: bool = False
    ghost_owner_instance_id: str | None = Field(default=None, min_length=1)
    identity_detection_phase: Literal[
        "phase1", "phase2", "switching", "revealed"
    ] | None = None
    identity_detection_stacks: int = Field(default=0, ge=0, le=300)
    identity_detection_trigger_remaining_ticks: int = Field(default=0, ge=0, le=3)
    enhanced_attack_pending: bool = False
    revealed_invisible: bool = False
    polluted_attack_charge: int = Field(default=0, ge=0, le=2)
    cannon_skill_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    cannon_barrage_remaining_shots: int = Field(default=0, ge=0, le=10)
    cannon_barrage_next_shot_ticks: int = Field(default=0, ge=0, le=45)
    cannon_barrage_center: GridPoint | None = None
    # Ticks until the carrier's next freeze-aura pulse.  Zero means the enemy
    # declares no aura; it is never zero while an aura is live, because the
    # counter is reset to a full interval on the pulse tick itself.
    freeze_aura_pulse_remaining_ticks: int = Field(default=0, ge=0)
    # Charge acceleration: how many increments of base speed have accrued, the
    # countdown to the next one, and the initial stand-still delay before the
    # ramp starts (only one of the two carriers ships a predelay).
    rush_acceleration_stacks: int = Field(default=0, ge=0, le=100)
    rush_interval_remaining_ticks: int = Field(default=0, ge=0)
    rush_predelay_remaining_ticks: int = Field(default=0, ge=0)
    # Armed for the burst that PRTS calls 「被阻挡后的首次攻击」: set while the
    # carrier is free, spent by the attack it powers, and re-armed the next time
    # the carrier is unblocked.
    #: Whether the carrier is **currently charging**.  PRTS 「萨卡兹穿刺手」/
    #: 「凶豕兽」 poll for it: 「①若受晕眩/束缚影响，停止加速 ②若未被阻挡，开始
    #: 加速」.  Being blocked only stops a charge from *starting*, so this cannot
    #: be derived from ``blocked_by`` -- a carrier blocked mid-charge keeps
    #: accelerating, which is precisely why its blocked attack hurts.
    #: 「已损失生命值判定」's 当前阶段数, which PRTS says starts at **1**.
    #: It is a counter rather than a threshold because a single large hit jumps
    #: it to wherever the health now is, so the carrier sprays once for that
    #: hit however many steps it crossed.
    lost_hp_stage: int = Field(default=1, ge=1)
    #: 狡兽's 【形态差分】: whether it has eaten a 爪兽 and is wearing the
    #: shell.  `spent` is separate because PRTS says 「仅触发1次」 -- a second
    #: kill does not re-arm it, and losing the shell (which needs 眩光手雷, a
    #: friendly item IS3 does not have) would not either.
    #: What is left of 「10s 100%的脆弱」 after the ground last hit it.
    #: Refreshed rather than stacked: PRTS states one window, and a second
    #: hit inside it restarts the clock.
    environmental_fragile_remaining_ticks: int = Field(default=0, ge=0)
    #: 「该隐匿每次生效后自身获得强击标记（**不可叠加**）」 -- a flag, not a
    #: counter, and earned by *being* hidden rather than by losing stealth.
    #: 【引导】's three clocks: when it next comes round, how much of the
    #: five-second lock is left, and what remains of the speed it earns.
    #: The locked target is remembered because 「锁定」 picks once, at the
    #: start, and the blow lands on that one.
    guide_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    guide_windup_remaining_ticks: int = Field(default=0, ge=0)
    guide_target_id: str | None = None
    guide_buff_remaining_ticks: int = Field(default=0, ge=0)
    #: When 【恐惧凝集】 next comes round.  Seeded from `initCooldown`, which
    #: differs between the three carriers (10 s, 10 s, 5 s).
    backstab_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    #: When 【全力吹奏】 next fires.  Seeded from its `initCooldown` (10 s).
    volley_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    #: 【轰炸】's wait, and whether the one bomb has already been dropped.
    #: 「此技能**仅能触发一次**」, so `spent` is what ends it, not the clock.
    bomb_delay_remaining_ticks: int = Field(default=0, ge=0)
    bomb_spent: bool = False
    #: How long 净浊之焰 has been on the board, and whether its decay has been
    #: stopped by its own attack.  「普通攻击开始时停止降低」, so the elapsed
    #: count is frozen rather than reset.
    spawn_decay_elapsed_ticks: int = Field(default=0, ge=0)
    spawn_decay_frozen: bool = False
    #: 【瞬身】's three clocks: when it may trigger again, how long it stays
    #: unblockable, and the half second before the blow lands on whoever was
    #: holding it -- remembered by id, because by then it is somewhere else.
    blink_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    blink_unblockable_remaining_ticks: int = Field(default=0, ge=0)
    blink_strike_remaining_ticks: int = Field(default=0, ge=0)
    blink_strike_target_id: str | None = None
    #: 【反自然馈赠】's clock, and what a target it reached is carrying.  The
    #: bonuses are stored with the buff because two carriers ship different
    #: figures and a target may be reached by either.
    chain_buff_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    chain_buff_remaining_ticks: int = Field(default=0, ge=0)
    chain_buff_attack_speed: int = Field(default=0, ge=0)
    chain_buff_move_speed_micros: int = Field(default=0, ge=0)
    #: 【叹息】's ten-minute clock.  Seeded from `initCooldown`, which is the
    #: same ten minutes: the first firing is the deadline, not a repeat.
    doom_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    #: 【重生】's five seconds, whether it has already been spent, and whether
    #: the attack bonus it leaves behind is running.  「**首次**被击倒后」 makes
    #: `spent` the latch: the second kill is a kill.
    #: 【献祭】's five seconds between castings.  `initCooldown` is 0, so the
    #: first one lands the tick the caster arrives.
    #: 【修复】's thirty seconds between castings, the ten-second cast, the
    #: locked-on target (「锁定距离自身最近的目标」) and the beat between heals.
    #: 【焚毁】's casting state, the wait after it, the locked target
    #: (「锁定攻击范围内一名我方单位」) and the half-second between jets.
    #: 【枯朽召唤】's ten seconds, and the 0.7s that follows each firing.
    born_bugs_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    born_bugs_delay_remaining_ticks: int = Field(default=0, ge=0)
    #: 「每秒执行两项操作」 -- the one clock both halves share.
    #: 【投弹】's one-second wind-up and the latch behind 「仅能触发一次」.
    bomb_run_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    bomb_run_spent: bool = False
    crossroads_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    flame_cast_remaining_ticks: int = Field(default=0, ge=0)
    flame_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    flame_target_id: str = ""
    flame_pulse_remaining_ticks: int = Field(default=0, ge=0)
    repair_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    repair_remaining_ticks: int = Field(default=0, ge=0)
    repair_target_id: str = ""
    repair_pulse_remaining_ticks: int = Field(default=0, ge=0)
    immolation_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    reborning_remaining_ticks: int = Field(default=0, ge=0)
    reborning_spent: bool = False
    reborning_empowered: bool = False
    stealth_strike_armed: bool = False
    #: `DropShield`/`ShieldUp`'s five seconds, which is the only figure those
    #: two prefabs carry.  Seeded from `initCooldown`, so a 狡兽 that arrives
    #: beside its prey still waits before biting.
    predation_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    predation_shell: bool = False
    predation_spent: bool = False
    #: Which way the shell makes it face: -1 left, +1 right, 0 not yet decided.
    #: 「相同数量则保持当前方向」, so a tie has to remember the last answer.
    predation_facing_col: int = Field(default=0, ge=-1, le=1)
    #: Whether the carrier's own `Rage.*` is running, and what is left of it.
    #: `spent` is separate from `active` because both carriers say 「**首次**」:
    #: once the buff has fired, crossing the line again -- or healing back over
    #: it and falling below a second time -- does nothing.
    self_rage_active: bool = False
    self_rage_spent: bool = False
    self_rage_remaining_ticks: int = Field(default=0, ge=0)
    rush_charging: bool = False
    rush_first_attack_armed: bool = True
    # Stacks of 【抗性降低】 accumulated by being hit.  PRTS bounds it at 80/85
    # depending on the carrier, and the bound is read from the carrier's own
    # `def_reduce.max_stack_cnt` rather than hardcoded.
    damage_taken_stacks: int = Field(default=0, ge=0, le=200)
    # Ticks until this carrier next damages itself.  Seeded to one second of
    # the bundle's own clock at spawn, so the first loss lands a second in
    # rather than on the spawn tick.
    self_damage_remaining_ticks: int = Field(default=0, ge=0)
    # Normal attacks banked toward the carrier's charged skill, and the
    # timer used by the variant that charges on time instead.
    # 决胜's own cooldown, counting down from spawn, and the latch it
    # sets.  Once raised the buff is permanent -- 「可触发1次」.
    victory_delay_remaining_ticks: int = Field(default=0, ge=0)
    victory_active: bool = False
    # Targets already hit by this carrier's contact damage and still inside
    # its radius.  PRTS states no cooldown for 「肇事者」's contact hit, and
    # firing it every tick would be 30 hits a second, so the registered
    # default is once per target per contact episode: an id is dropped when
    # the target leaves the radius, which re-arms it.
    # Ticks since this carrier last attacked.  歪曲施法者 「一定时间不攻击后
    # 移动速度大幅提升」 -- the window is its own `SpeedUp.attack@duration`.
    # 暴怒的余烬: ticks left in the ember form, and the invincible motionless
    # 1 s transition into it.  While the ember stands, every successful hit
    # removes exactly one point -- PRTS 「不论伤害多少」.
    # 喷气人 airborne window and the 25 s recharge behind it.  While airborne it
    # 「无法被阻挡或近战攻击」.
    airborne_remaining_ticks: int = Field(default=0, ge=0)
    takeoff_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    #: The two 「移动速度最终降低90%」 windows PRTS puts around the flight: 1.5s
    #: after taking off and 1.333s after coming down.  The landing one also
    #: keeps 「不可阻挡」 running, so it is not only a speed window.
    takeoff_slow_remaining_ticks: int = Field(default=0, ge=0)
    landing_slow_remaining_ticks: int = Field(default=0, ge=0)
    ember_remaining_ticks: int = Field(default=0, ge=0)
    ember_transition_remaining_ticks: int = Field(default=0, ge=0)
    # 「遗弃者」 「连续攻击时会提高攻击力和攻击速度，4秒不攻击后清空」: stacks banked
    # so far, capped by the carrier's own `atkup.max_stack_cnt`.
    attack_stacks: int = Field(default=0, ge=0, le=64)
    no_attack_idle_ticks: int = Field(default=0, ge=0)
    rush_contact_hit_ids: tuple[str, ...] = ()
    charged_skill_sp: int = Field(default=0, ge=0, le=20)
    charged_skill_cooldown_ticks: int = Field(default=0, ge=0)
    # Set when this carrier's `triggerrage` partner leaves the board -- by death
    # or by reaching the goal.  Permanent once raised: PRTS describes no way
    # back, and the partner cannot return.
    rage_active: bool = False
    # Blacksteel energy: the pool, the overload it triggers, and the two
    # countdowns that feed it.  The cap lives in the carrier's own
    # `blacksteel_energy.max_stack_cnt`, so it is not repeated here.
    energy_stacks: int = Field(default=0, ge=0, le=200)
    overload_remaining_ticks: int = Field(default=0, ge=0)
    energy_charge_remaining_ticks: int = Field(default=0, ge=0)
    # 匪帮射手 charges in windows, not continuously: PRTS 「周期性停止移动，
    # 并为攻击范围内所有敌人持续提供能量」.  These two count the window
    # itself and the wait for the next one; the field above counts the grants
    # inside a window.  A zero window with a positive cycle is resting.
    energy_charge_window_remaining_ticks: int = Field(default=0, ge=0)
    # 升空检测器: ticks of qualifying self-movement accrued so far, and
    # whether the carrier is currently in the condition-triggered flight.
    # Separate from `airborne_remaining_ticks` because that one counts a
    # duration down and this flight has none -- it ends on control only.
    takeoff_detector_progress_ticks: int = Field(default=0, ge=0)
    # 【超负载】: the window, the countdown to the next punch, the target it
    # locked when it started (「技能期间不会改变攻击目标」), the recharge and
    # the closing animation.  Named apart from `overload_remaining_ticks`,
    # which belongs to the Blacksteel energy overload and is a different
    # mechanism with the same word in its name.
    punch_window_remaining_ticks: int = Field(default=0, ge=0)
    punch_next_ticks: int = Field(default=0, ge=0)
    punch_target_id: str | None = None
    punch_cooldown_remaining_ticks: int = Field(default=0, ge=0)
    punch_recovery_remaining_ticks: int = Field(default=0, ge=0)
    # 吞噬: who was swallowed (「被击倒时干员返回」 needs the identity back)
    # and whether it has fed, since the ability is 「首次被阻挡时」 only.
    devoured_operator_id: str | None = None
    devour_used: bool = False
    conditional_airborne: bool = False
    energy_charge_cycle_remaining_ticks: int = Field(default=0, ge=0)
    energy_steal_remaining_ticks: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def inactive_buff_has_no_payload(self) -> "EnemyCoefficientRuntimeState":
        if self.friendly_buff_remaining_ticks == 0 and (
            self.friendly_attack_speed_additive_micros
            or self.friendly_defense_multiplier_milli != 1000
        ):
            raise ValueError("expired enemy coefficient buff retains a payload")
        if self.ghost_created and self.ghost_owner_instance_id is not None:
            raise ValueError("a ghost lifecycle state cannot be both owner and child")
        phase = self.identity_detection_phase
        if phase is None and (
            self.identity_detection_stacks
            or self.identity_detection_trigger_remaining_ticks
            or self.enhanced_attack_pending
            or self.revealed_invisible
        ):
            raise ValueError("inactive identity detection retains runtime payload")
        if phase in {"phase1", "phase2"}:
            if not 1 <= self.identity_detection_trigger_remaining_ticks <= 3:
                raise ValueError("active identity detection lacks its 0.1-second trigger")
            if self.identity_detection_stacks >= 300:
                raise ValueError("active identity detection retained a completed stack meter")
        elif phase == "switching":
            if self.identity_detection_stacks != 0:
                raise ValueError("identity switch trigger retains detection stacks")
            if not 1 <= self.identity_detection_trigger_remaining_ticks <= 2:
                raise ValueError("identity switch trigger lacks its source duration")
        elif phase == "revealed":
            if self.identity_detection_stacks or self.identity_detection_trigger_remaining_ticks:
                raise ValueError("revealed identity retains a pending detection trigger")
        if (self.enhanced_attack_pending or self.revealed_invisible) and phase != "revealed":
            raise ValueError("revealed-mode payload exists before identity reveal")
        barrage_active = self.cannon_barrage_remaining_shots > 0
        if barrage_active != bool(
            self.cannon_barrage_next_shot_ticks and self.cannon_barrage_center is not None
        ):
            raise ValueError("cannon barrage timer and target center are inconsistent")
        return self
