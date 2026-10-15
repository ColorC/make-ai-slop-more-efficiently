# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-10T02:35:03+08:00 type=battle-kernel status=active
"""Deterministic, asset-free battle kernel for the first tournament prototype."""

from __future__ import annotations

from collections.abc import Mapping
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import os
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal, Sequence, TypeVar, cast

from pydantic import BaseModel

# Preserves the concrete state-model type through BattleEngine._fast_copy.
_ModelT = TypeVar("_ModelT", bound=BaseModel)

# Set ARKNIGHTS_SIM_FAST_COPY_AUDIT=1 to send every _fast_copy back through
# pydantic's own model_copy, for re-running any result on the fully
# library-checked path.
_FAST_COPY_AUDIT = os.environ.get("ARKNIGHTS_SIM_FAST_COPY_AUDIT") == "1"

# Measurement affordance, not a feature: ARKNIGHTS_SIM_NO_FAST_PATHS=1 restores
# the pre-optimisation behaviour of BOTH fast paths (per-tick digest always
# built, model_copy always used), so a "before" arm can be run on the SAME
# machine in the SAME session. Comparing against a historical wall time from
# another host is exactly the cross-run comparison this line forbids.
_NO_FAST_PATHS = os.environ.get("ARKNIGHTS_SIM_NO_FAST_PATHS") == "1"
if _NO_FAST_PATHS:
    _FAST_COPY_AUDIT = True

# class -> its field names, for BattleEngine._fast_copy's guard. Cached because
# pydantic 2.12 makes `model_fields` a property whose per-call cost exceeds the
# saving the fast path exists to deliver.
_FAST_COPY_FIELDS: dict[type, frozenset[str]] = {}

if TYPE_CHECKING:
    from ..battle_program_ir import BattleProgramIR
    from .program_executor import BattleProgramExecutionReceipt

from .budget import ForecastBudgetLedger
from .effect_vm import EffectVM, EffectVMError
from .coefficient_provenance import splash_attack_scale
from .enemy_immunities import CONTROL_IMMUNITY_FIELDS
from .is3_talent_families import (
    CHARGE_BURST_KEY_ORDER,
    lost_hp_check_passes,
    lost_hp_next_stage,
    quill_spray_rows,
    EMBER_TRANSITION_SECONDS,
    CRASH_CONTACT_RADIUS_MILLI_TILES,
    RUSH_CONTACT_RADIUS_MILLI_TILES,
    attack_stack_rows,
    carries_bleed_on_attack,
    crash_contact_rows,
    carries_polluted_death,
    carries_polluted_tank,
    ember_revive_rows,
    pollution_aura_rows,
    idle_speed_rows,
    rush_contact_rows,
)
from .enemy_skills import (
    AOE_DAMAGE_RANGE_X4,
    INACTIVE_POWER_ARMOUR_SUMMON_IDS,
    SPAWN_DECAY_STEP_TICKS,
)
from .pending_explosions import PendingExplosionState
from .token_ingestion import DIRECTION_STEPS
from .models import (
    ENGINE_VERSION,
    REPLAY_SCHEMA,
    TICK_DIGEST_SCHEMA,
    ActivateSkillAction,
    ActiveEffectState,
    AttackScaleEffectDefinition,
    BattleEvent,
    BattleReplay,
    BattleSnapshot,
    BattleStatus,
    CapabilityReport,
    CommandRecord,
    ContentBundle,
    ControlEffect,
    EngineCheckpoint,
    DeployAction,
    DeploymentClass,
    Direction,
    DamageEffectDefinition,
    DeploymentPointsEffectDefinition,
    EffectStat,
    EffectTargetSelector,
    EffectRangeRotation,
    EnemyBlockConstraintState,
    EnemyCoefficientRuntimeState,
    EnemyDefinition,
    EnemyState,
    EnemyTimedSelfModifierDefinition,
    EventPhase,
    FidelityLevel,
    ForecastResult,
    GridPoint,
    GlobalModifierState,
    HealEffectDefinition,
    ScaledHealEffectDefinition,
    HealingDeviceDefinition,
    HealingDeviceState,
    InitialState,
    KnownOnlyObservation,
    MechanismFidelity,
    MovementLayer,
    ObservedEnemy,
    OmniscientObservation,
    OperatorDefinition,
    OperatorState,
    PollutedZoneState,
    ProjectileState,
    PublicAction,
    ReplayRetentionPolicy,
    ReplayRetentionState,
    ReplayVerification,
    RandomDamageEffectDefinition,
    RetreatAction,
    RosterState,
    RNGStreamState,
    SourceWaveScenarioAssignment,
    SelectTargetAction,
    SetPausedAction,
    SetSpeedAction,
    SkillDefinition,
    SkillActivationPolicy,
    SkillSpEvent,
    SkillSpRecoveryMode,
    SkillState,
    SpMutationEffectDefinition,
    SpecialTileControlEffectDefinition,
    SpecialTileStatModifierDefinition,
    StatModifierEffectDefinition,
    StepResult,
    BloodDebtLedger,
    SummonDefinition,
    SummonEffectDefinition,
    SummonState,
    TickStateDigest,
    WaitAction,
    canonical_sha256,
    enemy_timed_modifier_effect_id,
)
from .source_wave_schedule import (
    SourceWaveSchedule,
    SourceWaveScheduleError,
    audit_source_wave_schedule,
)
from .enemy_mechanics import incoming_damage_multiplier_milli
from .enemy_coefficients import (
    ATTACK_CONTROL_KEYS,
    ATTACK_SPEED_FRACTION_TO_POINTS,
    BLOOD_DEBT_KEYS,
    BOOM_FUSE_TICKS,
    BOOM_FUSE_TICKS_BY_ENEMY,
    ELEMENTAL_BURST_ARTS_DAMAGE,
    ELEMENTAL_BURST_COOLDOWN_TICKS,
    ELEMENTAL_BURST_PHYSICAL_DAMAGE,
    ELEMENTAL_BURST_STUN_SECONDS,
    ELEMENTAL_BURST_TRUE_DAMAGE,
    ELEMENTAL_GAUGE_MAX,
    ELEMENTAL_PERMANENT_DEFENCE_LOSS,
    ELEMENTAL_RATIO_KEYS,
    ENERGY_CHARGE_PER_SECOND,
    RANGED_MODE_TARGETS_BY_ENEMY,
    ENERGY_KEYS,
    RAGE_KEYS,
    STEAL_ENERGY_KEYS,
    TRAIT_ABILITY_BLADE_KEYS,
    TRAIT_ABILITY_GUARD_KEYS,
    TRAIT_ABILITY_PAIRING_RADIUS_MILLI_TILES,
    BOOM_RADIUS_MILLI_TILES_BY_ENEMY,
    COLD_ATTACK_SPEED_POINTS,
    AURA_DEFUP_RADIUS_MILLI_TILES,
    FREEZE_AURA_KEYS,
    rush_acceleration_rows,
    GHOST_HOLOGRAM_IDS,
    GHOST_REDACE_IDS,
    MAINLINE12_CIVILIAN_IDS,
    MAINLINE12_BUNDLE_ID,
    MAINLINE12_DETECTION_INTERVAL_TICKS,
    MAINLINE12_DETECTION_PHASE1_BLOCKED_DELTA,
    MAINLINE12_DETECTION_PHASE1_THRESHOLD,
    MAINLINE12_DETECTION_PHASE2_BLOCKED_DELTA,
    MAINLINE12_DETECTION_PHASE2_THRESHOLD,
    MAINLINE12_DETECTION_UNBLOCKED_DELTA,
    MAINLINE12_DISGUISED_IDS,
    MAINLINE12_ENHANCED_ATTACK_IDS,
    MAINLINE12_FIRST_AOE_IDS,
    MAINLINE12_REVEALED_INVISIBLE_IDS,
    MAINLINE12_SWITCH_TRIGGER_TICKS,
    MAINLINE12_X5_RANGE_OFFSETS,
    RETURN_BODY_BLEEDING_PULSE_TICKS,
    RETURN_BODY_CANNON_BARRAGE_SHOTS,
    RETURN_BODY_CANNON_IDS,
    RETURN_BODY_CANNON_RANGE_OFFSETS,
    RETURN_BODY_CANNON_SHOT_INTERVAL_TICKS,
    RETURN_BODY_CANNON_WINDUP_TICKS,
    RETURN_BODY_POLLUTION_PULSE_TICKS,
    RUNTIME_COEFFICIENT_KEYS,
    STANDBY_REVIVE_ENEMY_IDS,
    STANDBY_REVIVE_INITIAL_GUARD_IDS,
    STANDBY_REVIVE_MELEE_ARTS_IDS,
    STANDBY_REVIVE_RANGED_MULTI_TARGET_IDS,
    WAITING_FLOCK_KEYS,
    coefficient_value,
    validate_runtime_coefficients,
)

SUPPORTED_MECHANISMS = frozenset(
    {
        "map",
        "route",
        "route_teleport_metadata",
        "spawn",
        "movement",
        "cost",
        "deployment",
        "retreat",
        "blocking",
        "battle_start_friendlies",
        "basic_physical_attack",
        "multi_target_normal_attack",
        "operator_stat_talent",
        "blocked_enemy_stat_talent",
        "periodic_self_stat_stacks",
        "normal_attack_slow",
        "bombarder_aftershock",
        "operator_random_attack_talent",
        "operator_damage_resistance_talent",
        "incoming_attack_retaliation",
        "enemy_refracting_resistance",
        "formation_defense_aura",
        "enemy_timed_self_modifier",
        "damage",
        "death",
        "terminal",
        "healing",
        "skills",
        "buffs_debuffs",
        "devices",
        "rng_effects",
        "flying",
        "deployment_classes",
        "projectiles",
        "area_projectiles",
        "special_tiles",
        "summons",
        "dynamic_obstacles",
        "global_modifiers",
        "special_tile_stat_or_control_effects",
        "summon_skills",
        "skill_sp_activation",
        "deployment_points",
        "discrete_effect_geometry",
    }
)
ENGINE_UNSUPPORTED_MECHANISMS: frozenset[str] = frozenset()
ENGINE_RESOLVED_CONTENT_MECHANISMS = frozenset({"route_teleport_runtime"})
# Public forecast calls are capped at four minutes on the canonical 30 TPS
# clock.  The former 2400-tick cap silently shortened the same wall-clock
# horizon after the stage bridge moved from 10 TPS to 30 TPS.
MAX_FORECAST_HORIZON_TICKS = 7200

_BLOCK_CONSTRAINT_MECHANISM = "enemy_block_constraint_coefficients"
_BLOCK_CONSTRAINT_KEY = "minimum_block_count_to_stop"
_BLOCK_CONSTRAINT_CONTENT_VERSION = "v17"
_BLOCK_CONSTRAINT_STAGE_ID = "ro3_n_2_4"
_BLOCK_CONSTRAINT_BUNDLE_ID = "level_rogue3_2-4"
_BLOCK_CONSTRAINT_SOURCE = (
    "var/vendor/ArknightsGameResource/gamedata/levels/obt/roguelike/ro3/level_rogue3_2-4.json"
)
_BLOCK_CONSTRAINT_PACKAGE_HASH = "c4d11553fabb4fa02ca8e055f43821ccfab23147f26f95dcda1296d273dc8ae1"
_BLOCK_CONSTRAINT_ENEMY_ID = "enemy_1320_wdrrl"
_BLOCK_CONSTRAINT_VALUE_MICROS = 3_000_000
_BLOCK_CONSTRAINT_SOURCE_POINTER = "enemy_database.json#/enemy_1320_wdrrl/description"
_BLOCK_CONSTRAINT_SOURCE_HASH = "8a3b26e3c890f140b7edc0b44ed870b26098646a5682cb21cfee7e554e005d14"
_BLOCK_CONSTRAINT_SPAWN_BINDINGS = {
    "w0-f2-a33": ("route-043", 2760, 33),
    "w0-f2-a74": ("route-084", 5340, 74),
}

_WAITING_FLOCK_OPERATOR_RANGE_OFFSETS: dict[str, tuple[tuple[int, int], ...]] = {
    "char_202_demkni": ((0, 0),),
    "char_124_kroos": (
        (0, 1),
        (1, 1),
        (2, 1),
        (3, 1),
        (0, 0),
        (1, 0),
        (2, 0),
        (3, 0),
        (0, -1),
        (1, -1),
        (2, -1),
        (3, -1),
    ),
    "char_210_stward": (
        (0, 1),
        (1, 1),
        (2, 1),
        (0, 0),
        (1, 0),
        (2, 0),
        (3, 0),
        (0, -1),
        (1, -1),
        (2, -1),
    ),
}


class TournamentSimError(RuntimeError):
    """Base error for fail-closed simulator operations."""


class InvalidInitialStateError(TournamentSimError):
    pass


class IllegalActionError(TournamentSimError):
    pass


class UnsupportedMechanicError(TournamentSimError):
    pass


class ObservationAccessError(TournamentSimError):
    pass


class IncompleteReplayHistoryError(TournamentSimError):
    """Raised when a lossless audit product is requested from bounded history."""


def _validate_narrow_block_constraint_content(content: ContentBundle) -> None:
    coefficients = [
        (enemy, coefficient)
        for enemy in content.enemies
        for coefficient in enemy.mechanic_coefficients
    ]
    if not coefficients:
        # Some source-complete v17 stages retain the broad mechanism
        # declaration while containing no coefficient rows.  There is no
        # runtime coefficient effect to execute in those packages.
        return
    try:
        if validate_runtime_coefficients(content):
            return
    except ValueError as exc:
        raise UnsupportedMechanicError(str(exc)) from exc
    unknown_keys = sorted(
        {
            coefficient.key
            for _, coefficient in coefficients
            if coefficient.key not in RUNTIME_COEFFICIENT_KEYS
            and coefficient.key != _BLOCK_CONSTRAINT_KEY
        }
    )
    if unknown_keys:
        raise UnsupportedMechanicError(
            "enemy block constraint content contains non-allowlisted coefficient keys: "
            f"{unknown_keys}"
        )
    allowed = [item for item in coefficients if item[1].key == _BLOCK_CONSTRAINT_KEY]
    if len(allowed) != 1:
        raise UnsupportedMechanicError(
            "enemy block constraint content requires exactly one allowlisted coefficient"
        )
    enemy, coefficient = allowed[0]
    expected_identity = (
        content.content_version,
        content.stage_id,
        content.bundle_id,
        content.source,
        content.source_package_hash,
        enemy.enemy_id,
        coefficient.value_micros,
        coefficient.source_pointer,
        coefficient.source_sha256,
        enemy.minimum_block_count_to_stop,
    )
    required_identity = (
        _BLOCK_CONSTRAINT_CONTENT_VERSION,
        _BLOCK_CONSTRAINT_STAGE_ID,
        _BLOCK_CONSTRAINT_BUNDLE_ID,
        _BLOCK_CONSTRAINT_SOURCE,
        _BLOCK_CONSTRAINT_PACKAGE_HASH,
        _BLOCK_CONSTRAINT_ENEMY_ID,
        _BLOCK_CONSTRAINT_VALUE_MICROS,
        _BLOCK_CONSTRAINT_SOURCE_POINTER,
        _BLOCK_CONSTRAINT_SOURCE_HASH,
        3,
    )
    if expected_identity != required_identity:
        raise UnsupportedMechanicError(
            "enemy block constraint content does not match the frozen source binding"
        )
    bound_spawns = {
        spawn.spawn_id: (spawn.route_id, spawn.tick, spawn.source_action_index, spawn.count)
        for spawn in content.spawns
        if spawn.enemy_id == _BLOCK_CONSTRAINT_ENEMY_ID
    }
    expected_spawns = {
        spawn_id: (route_id, tick, action_index, 1)
        for spawn_id, (route_id, tick, action_index) in _BLOCK_CONSTRAINT_SPAWN_BINDINGS.items()
    }
    if bound_spawns != expected_spawns:
        raise UnsupportedMechanicError(
            "enemy block constraint spawn rows do not match the frozen binding"
        )


@dataclass(frozen=True, slots=True)
class _ScheduledSpawn:
    occurrence_id: str
    tick: int
    enemy_id: str
    route_id: str


@dataclass(frozen=True, slots=True)
class _DamageIntent:
    source_id: str
    target_id: str
    target_side: Literal["operator", "summon", "enemy"]
    amount: int
    nonlethal: bool = False
    # Explicit provenance: damage-over-time and environment packets are not attacks.
    attack_packet: bool = False


@dataclass(frozen=True, slots=True)
class _HealIntent:
    source_id: str
    target_id: str
    amount: int


@dataclass(frozen=True, slots=True)
class _SummonPlan:
    state: SummonState
    enemy_updates: tuple[EnemyState, ...]


@dataclass(frozen=True, slots=True)
class _PendingSpGrant:
    source_instance_id: str
    target_instance_id: str
    target_skill_id: str
    amount: int
    effect_id: str
    trigger_event_sequence: int
    survives_source_departure: bool = False


class _Exact(int):
    """一个战斗量的整数值, 外带它自己的**精确千分值**。

    M 线在 2-1 实测(白板 §2.9): 干员 atk **816** 打护盾怪 def **800**, 走下限
    支 `attack * 5 // 100`, 引擎每击恒 **40**, 而实测血量掉 **40/41 交替、
    均值 40.846**——精确值是 **40.8**。技能击同样, 引擎恒 1974 而实测
    1974/1975 交替(`816×3.4−800 = 1974.4`)。**每击下取整系统性少算 2%**, 且随
    战斗长度累积: 6000 血护盾怪 150 击 vs 147 击。按 `enemy_database` 2025 个
    def 统计, atk 816 时 **21.2%(429 个)** 的敌人走下限支, atk 500 时 36.7%,
    高防敌人几乎必然走这条——**不是边角**。

    这个类让**伤害公式的输出把小数带上**, 而不必改动它 32 个调用点的形状: 它
    就是一个 `int`(报告、比较、求和、与字面量对拍全部照旧), 只是多带一个
    `.milli`。真正的扣血在 `_apply_damage` 里按千分位累加, 余数存在**目标**
    身上(`damage_remainder_milli`)——也就是 M 线建议的「伤害小数累计、血量取整
    读出」, 与引擎里既有的 `segment_progress_milli` / `movement_phase_remainder`
    同一个写法。

    **小数落在目标身上还是落在伤害源身上, 未对齐。** M 线明说 40/41 交替至少还
    有三种成因没排除(小数累计在伤害侧累加器 / 血量为整数但每击非恒定取整 /
    小数血量在扫描窗口之外), 单干员观测分辨不出来。多个攻击者同时打一个目标时
    两种模型会分叉, 这里取**目标侧**(「血量取整读出」那一支)作为默认实现。
    """

    # `int` 是变长类型, 子类不能有非空 `__slots__`(CPython 直接 TypeError),
    # 所以这里只声明类型。
    milli: int

    def __new__(cls, value: int, milli: int | None = None) -> "_Exact":
        # **第一个参数必须与 `int` 同义**(就是那个整点数额), 不能改成「千分值」:
        # `copy`/`pickle` 重建 int 子类时会拿**整数值**回喂构造器, 于是
        # `_Exact(800)` 会被当成 0.8 点伤害 —— 快照往返后 800 变 0。
        # 这条是实测出来的: 改成千分位入参时 17 个用例红, 其中四个正是
        # 「survives a snapshot round trip」。
        exact = super().__new__(cls, value)
        exact.milli = value * 1000 if milli is None else milli
        return exact

    def __reduce__(self):
        # 让往返把小数也带回来, 而不是悄悄退化成整点。
        return (self.__class__, (int(self), self.milli))


def _exact(milli: int) -> _Exact:
    """由精确千分值造一个战斗量。"""

    return _Exact(milli // 1000, milli)



def _split_with_remainder(
    lethal_milli: int, nonlethal_milli: int, carried_milli: int
) -> tuple[int, int, int]:
    """把精确的致命/非致命两股伤害连同上一击的余数化成整点血量。

    余数跟着**目标**走, 所以两股共用同一个累加器, 按引擎既有的落地顺序
    (致命在前)依次取整。返回 (致命点数, 非致命点数, 新的余数)。
    """

    pool = carried_milli + lethal_milli
    lethal = pool // 1000
    pool -= lethal * 1000
    pool += nonlethal_milli
    nonlethal = pool // 1000
    return lethal, nonlethal, pool - nonlethal * 1000


def _damage_milli(amount: int) -> int:
    """一个战斗量的精确千分值。

    普通 `int` 按整点算(真实伤害、按点数写死的效果本来就没有小数), 只有走过
    `_exact` 的那些带着 `.milli`。
    """

    exact = getattr(amount, "milli", None)
    return int(amount) * 1000 if exact is None else exact


def _scale_micros(value: int, multiplier_micros: int) -> _Exact:
    """按**百万分比**缩放一个战斗量, 保留精确千分值。

    敌人技能的倍率一律出货成 micros(`attack_scale_micros` 之类), 而
    `attack * micros // 1_000_000`(旧写法)每一步都把攻击力削回整点。单步误差不到 1 点,
    但 10819–10951 那一段**连着乘五六次**(强化/首发/近战/被阻挡/隐匿突袭/齐射/
    冻结目标/蓄力/国度), 每步各削一次。

    与干员侧那半的区别: **这一半没有实测签名**。干员侧能拿 M 线的 1974/1975 对
    验收, 这里只能按同一条口径推, 验收强度低一档——所以它是单独一批, 台账里也
    分开记。
    """

    return _exact(_damage_milli(value) * multiplier_micros // 1_000_000)


def _physical_damage(attack: int, defense: int) -> _Exact:
    # **攻击力自己也可能带小数**: `816 x 3.4 = 2774.4`, 而 M 线实测那一击落地是
    # 1974/1975 交替(`816×3.4−800 = 1974.4`), 引擎旧版恒 1974——小数在**攻击力
    # 那一侧**就被 `// 1_000_000` / `// 1000` 削掉了, 伤害公式拿到的已经是整数。
    attack_milli = _damage_milli(attack)
    if attack_milli <= 0:
        return _exact(0)
    # `attack * 5 // 100` 在千分位上就是 `attack_milli * 5 // 100`。
    return _exact(
        max(attack_milli - defense * 1000, max(1000, attack_milli * 5 // 100))
    )


def _arts_damage(attack: int, magic_resistance: int) -> _Exact:
    attack_milli = _damage_milli(attack)
    if attack_milli <= 0:
        return _exact(0)
    reduced = attack_milli * max(0, 100 - magic_resistance) // 100
    return _exact(max(reduced, max(1000, attack_milli * 5 // 100)))



#: The cardinal steps, and the reverse lookup used to name an enemy's current
#: walking direction.  Shared with `token_ingestion`, which derives them from
#: the shipped ``direction`` integers, so the airflow's geometry and the
#: token's facing cannot drift apart.
STEP_DIRECTIONS: dict[tuple[int, int], Direction] = {
    step: direction for direction, step in DIRECTION_STEPS.items()
}


class BattleEngine:
    """One deterministic battle session.

    The public mutation surface is :meth:`step`; arbitrary state construction is
    accepted only once through the judge-side :class:`InitialState` contract.
    """

    def __init__(
        self,
        content: ContentBundle,
        initial_state: InitialState | None = None,
        *,
        budget_ledger: ForecastBudgetLedger | None = None,
        allow_omniscient: bool = False,
        source_wave_scenario_id: str | None = None,
        source_wave_scenario_assignments: tuple[SourceWaveScenarioAssignment, ...] = (),
        source_wave_package_hash: str | None = None,
        replay_retention: ReplayRetentionPolicy | None = None,
        event_observer: Callable[[BattleEvent], None] | None = None,
        tick_digest_observer: Callable[[TickStateDigest], None] | None = None,
        command_observer: Callable[[CommandRecord], None] | None = None,
    ) -> None:
        unsupported_required = set(content.required_mechanisms) - SUPPORTED_MECHANISMS
        self._source_wave_selected_spawn_ids: frozenset[str] | None = None
        self._source_wave_spawn_ticks: dict[str, int] | None = None
        self._source_wave_unharmful_spawn_ids: frozenset[str] = frozenset()
        self.source_wave_schedule: SourceWaveSchedule | None = None
        self._source_wave_package_hash: str | None = None
        self._source_wave_scenario_id: str | None = None
        self._source_wave_scenario_assignments: tuple[SourceWaveScenarioAssignment, ...] = ()
        if _BLOCK_CONSTRAINT_MECHANISM in unsupported_required:
            _validate_narrow_block_constraint_content(content)
            unsupported_required.remove(_BLOCK_CONSTRAINT_MECHANISM)
        if "source_wave_schedule" in unsupported_required:
            try:
                audit_source_wave_schedule(content)
                package_hash = source_wave_package_hash or content.source_package_hash
                if package_hash is None:
                    raise SourceWaveScheduleError(
                        "source wave execution requires an explicit package hash"
                    )
                if source_wave_scenario_id is None:
                    raise SourceWaveScheduleError(
                        "source wave execution requires an explicit scenario id"
                    )
                schedule = SourceWaveSchedule(
                    content,
                    source_package_hash=package_hash,
                    scenario_id=source_wave_scenario_id,
                    scenario_assignments=source_wave_scenario_assignments,
                )
                schedule.advance_to(schedule.final_tick)
            except SourceWaveScheduleError as exc:
                raise UnsupportedMechanicError(str(exc)) from exc
            self.source_wave_schedule = schedule
            self._source_wave_package_hash = package_hash
            self._source_wave_scenario_id = source_wave_scenario_id
            self._source_wave_scenario_assignments = tuple(
                sorted(
                    source_wave_scenario_assignments,
                    key=lambda item: (
                        item.source_wave_index if item.source_wave_index is not None else -1,
                        item.group_kind,
                        item.group_key,
                    ),
                )
            )
            self._source_wave_selected_spawn_ids = frozenset(schedule.snapshot.emitted_spawn_ids)
            self._source_wave_spawn_ticks = schedule.selected_spawn_ticks
            self._source_wave_unharmful_spawn_ids = schedule.unharmful_spawn_ids
            unsupported_required.remove("source_wave_schedule")
        if unsupported_required:
            raise UnsupportedMechanicError(
                f"content requires unsupported mechanics: {sorted(unsupported_required)}"
            )
        self.content = content
        self._routes = {route.route_id: route for route in content.routes}
        self._route_runtime_points = {
            route.route_id: route.runtime_waypoints for route in content.routes
        }
        self._route_runtime_waits = {
            route.route_id: route.runtime_waypoint_wait_ticks for route in content.routes
        }
        self._route_teleports = {
            route.route_id: {
                entrance: (destination, transit_wait, source_entry, source_exit)
                for source_entry, source_exit, entrance, destination, transit_wait in (
                    route.runtime_teleports
                )
            }
            for route in content.routes
        }
        self._enemy_defs = {enemy.enemy_id: enemy for enemy in content.enemies}
        self._operator_defs = {operator.operator_id: operator for operator in content.operators}
        self._summon_defs = {summon.summon_id: summon for summon in content.summons}
        #: Operators currently inside 萨卡兹枯朽吞噬者.  Off the board but not
        #: gone: they come back when the carrier that ate them dies.
        self._devoured_operators: dict[str, OperatorState] = {}
        #: Whether any device in this bundle buffs the enemy side at all.
        #: `_enemy_field_aura` sits on the stat-read path, which every attack
        #: and every movement tick goes through, and exactly one device kind
        #: in the topic carries the aura -- so the scan is skipped outright
        #: rather than walked to find nothing 62 stages out of 63.
        #: The Return Body families, resolved from each enemy's **own keys**
        #: rather than from a one-element id set.  Both elite variants ship
        #: identical mechanism rows to their base and stand in 6-2, which
        #: neither base carrier reaches, so that stage was running two enemies
        #: with none of their mechanisms.
        self._polluted_death_ids = frozenset(
            enemy.enemy_id
            for enemy in content.enemies
            if carries_polluted_death(enemy)
        )
        self._polluted_tank_ids = frozenset(
            enemy.enemy_id
            for enemy in content.enemies
            if carries_polluted_tank(enemy)
        )
        self._has_enemy_field_aura = any(
            summon.enemy_attack_speed_bonus
            or summon.enemy_move_speed_multiple_milli
            for summon in content.summons
        )
        #: Same reason: the airflow lookup sits on the attack and movement
        #: paths, and two of the 63 stages place the device that has one.
        self._airflow_instances = any(
            summon.airflow_range_tiles for summon in content.summons
        )
        self._device_defs = {device.device_id: device for device in content.healing_devices}
        # Deployment terrain and the resulting action templates are immutable for a
        # content bundle.  Cache primitive coordinate keys so the per-tick public
        # observation path does not rebuild sets or hash Pydantic models millions of
        # times.  Dynamic cost, roster, redeploy and occupancy checks stay in
        # legal_actions(), so no invalidation is required.
        self._ground_tile_coords = frozenset(
            (tile.col, tile.row) for tile in content.map.ground_tiles
        )
        self._high_ground_tile_coords = frozenset(
            (tile.col, tile.row) for tile in content.map.high_ground_tiles
        )
        sorted_deployable_tiles = tuple(
            sorted(content.map.deployable_tiles, key=lambda item: (item.row, item.col))
        )
        self._deploy_action_templates = {
            operator_id: tuple(
                DeployAction(
                    operator_id=operator_id,
                    position=tile,
                    direction=direction,
                )
                for tile in sorted_deployable_tiles
                if self._tile_accepts_operator(tile, definition)
                for direction in Direction
            )
            for operator_id, definition in self._operator_defs.items()
        }
        self._skill_defs = {
            (operator.operator_id, skill.skill_id): skill
            for operator in content.operators
            for skill in operator.skills
        }
        self._summon_skill_defs = {
            (summon.summon_id, skill.skill_id): skill
            for summon in content.summons
            for skill in summon.skills
        }
        self._effect_vm = EffectVM(content)
        self._rng_stream_names = {
            effect.rng_stream
            for owner in (*content.operators, *content.summons)
            for skill in owner.skills
            for effect in skill.effects
            if isinstance(effect, RandomDamageEffectDefinition)
        }
        self._rng_stream_names.update(
            stream
            for operator in content.operators
            for stream in (
                operator.normal_attack_random_scale_rng_stream,
                operator.normal_attack_aftershock_proc_rng_stream,
                operator.incoming_damage_resistance_rng_stream,
            )
            if stream is not None
        )
        self._rng_stream_names.update(
            enemy.unblocked_physical_evade_rng_stream
            for enemy in content.enemies
            if enemy.unblocked_physical_evade_rng_stream is not None
        )
        self._scheduled = self._expand_spawns()
        self._scheduled_ids = {item.occurrence_id for item in self._scheduled}
        self._budget = budget_ledger or ForecastBudgetLedger(f"local-{content.bundle_id}")
        self._allow_omniscient = allow_omniscient
        self._replay_retention = replay_retention or ReplayRetentionPolicy()
        self._event_observer = event_observer
        self._tick_digest_observer = tick_digest_observer
        self._command_observer = command_observer
        self._events: list[BattleEvent] = []
        self._commands: list[CommandRecord] = []
        self._tick_digests: list[TickStateDigest] = []
        self._last_command_record_hash: str | None = None
        self._max_resident_events = 0
        self._max_resident_commands = 0
        self._max_resident_tick_digests = 0
        # Digests the max_tick_digests==0 fast path declined to build; folded
        # back into replay_retention_state.total_tick_digests so history
        # accounting stays truthful.
        self._skipped_tick_digest_count = 0
        self._pending_sp_grants: list[_PendingSpGrant] = []

        if initial_state is None:
            initial_bonus = sum(
                operator.initial_deployment_points_if_rostered for operator in content.operators
            )
            if content.initial_cost + initial_bonus > content.max_cost:
                raise InvalidInitialStateError(
                    "initial deployment-point aggregate exceeds bundle max_cost"
                )
            state = InitialState(
                content_hash=content.content_hash,
                cost=content.initial_cost + initial_bonus,
                initial_deployment_points_bonus=initial_bonus,
                life_points=content.initial_life_points,
                summons=tuple(
                    SummonState(
                        instance_id=item.instance_id,
                        summon_id=item.summon_id,
                        source_instance_id=item.source_instance_id,
                        position=item.position,
                        facing=item.facing,
                        hp=self._summon_defs[item.summon_id].max_hp,
                        pulse_remaining_ticks=(
                            self._summon_defs[item.summon_id].pulse_interval_ticks
                        ),
                    )
                    for item in content.initial_summons
                ),
                global_modifiers=content.initial_global_modifiers,
            )
        else:
            state = initial_state
        normalized = self._normalize_and_validate_initial(state)
        self._initial_state = normalized
        self.tick = normalized.tick
        self.cost = normalized.cost
        self.max_cost_bonus = normalized.max_cost_bonus
        self.initial_deployment_points_bonus = normalized.initial_deployment_points_bonus
        self.cost_recovery_slowdown_debt_milli = (
            normalized.cost_recovery_slowdown_debt_milli
        )
        self.battle_progress_slowdown_debt_milli = (
            normalized.battle_progress_slowdown_debt_milli
        )
        self.battle_progress_tick = max(
            0,
            (
                normalized.tick * 1000
                - normalized.battle_progress_slowdown_debt_milli
            )
            // 1000,
        )
        self.spawn_progress_slowdown_debt_milli = (
            normalized.spawn_progress_slowdown_debt_milli
        )
        self.spawn_progress_tick = max(
            0,
            (
                normalized.tick * 1000
                - normalized.spawn_progress_slowdown_debt_milli
            )
            // 1000,
        )
        self.life_points = normalized.life_points
        self.kills = normalized.kills
        self.leaks = normalized.leaks
        self.status = normalized.status
        self.spawned_spawn_ids = set(normalized.spawned_spawn_ids)
        self.activated_token_aliases = set(normalized.activated_token_aliases)
        self._tokens_by_alias = {
            token.alias: token for token in content.predefined_tokens
        }
        self.operators = {item.instance_id: item for item in normalized.operators}
        self.enemies = {item.instance_id: item for item in normalized.enemies}
        self.summons = {item.instance_id: item for item in normalized.summons}
        self.projectiles = {item.instance_id: item for item in normalized.projectiles}
        self.healing_devices = {item.instance_id: item for item in normalized.healing_devices}
        self.pending_explosions = {
            item.instance_id: item for item in normalized.pending_explosions
        }
        #: Which sources count as 「环境伤害」: the stage's own hazard tiles.
        self._special_tile_ids = {
            tile.tile_id for tile in content.special_tiles
        }
        #: Whether any enemy in this bundle carries 【战术命令】 at all.
        self._tactical_command_present = any(
            item.tactical_command is not None for item in content.enemies
        )
        self.polluted_zones = {
            item.instance_id: item for item in normalized.polluted_zones
        }
        self.active_effects = {item.instance_id: item for item in normalized.active_effects}
        self.roster = {item.operator_id: item for item in normalized.roster}
        self.entity_sequence = normalized.entity_sequence
        self.effect_sequence = normalized.effect_sequence
        self.event_sequence = normalized.event_sequence
        self.command_sequence = normalized.command_sequence
        self.rng_seed = normalized.rng_seed
        self.rng_streams = {item.stream: item.draws for item in normalized.rng_streams}
        self.paused = normalized.paused
        self.speed = normalized.speed
        self.global_modifiers = normalized.global_modifiers
        self.tick_digest_count = normalized.tick_digest_count
        self.last_tick_digest_hash = normalized.last_tick_digest_hash
        if (
            self.initial_deployment_points_bonus
            and self.tick == 0
            and self.event_sequence == 0
            and self.command_sequence == 0
        ):
            contributing = [
                operator_id
                for operator_id in sorted(self.roster)
                if self._operator_defs[operator_id].initial_deployment_points_if_rostered
            ]
            self._emit(
                EventPhase.COST,
                "initial_deployment_points_modified",
                "roster",
                {
                    "operator_ids": contributing,
                    "cost_before": self.cost - self.initial_deployment_points_bonus,
                    "cost_after": self.cost,
                    "requested_delta": self.initial_deployment_points_bonus,
                    "applied_delta": self.initial_deployment_points_bonus,
                },
                tick=0,
            )
        self._snapshot = self._make_snapshot(normalized.parent_snapshot_hash)

    @property
    def snapshot(self) -> BattleSnapshot:
        return self._snapshot

    @property
    def events(self) -> tuple[BattleEvent, ...]:
        return tuple(item.model_copy(deep=True) for item in self._events)

    @property
    def commands(self) -> tuple[CommandRecord, ...]:
        return tuple(self._commands)

    @property
    def tick_digests(self) -> tuple[TickStateDigest, ...]:
        return tuple(self._tick_digests)

    @property
    def replay_retention_state(self) -> ReplayRetentionState:
        """Expose resident and global history counts without exposing mutable rows."""

        return ReplayRetentionState(
            mode=self._replay_retention.mode,
            history_complete=self._replay_retention.mode == "complete",
            total_commands=self.command_sequence - self._initial_state.command_sequence,
            total_events=self.event_sequence - self._initial_state.event_sequence,
            total_tick_digests=(
                self.tick_digest_count
                - self._initial_state.tick_digest_count
                # Digests skipped by the max_tick_digests==0 fast path still
                # happened as far as history accounting is concerned. Counting
                # them keeps a bounded run from reporting a total of 0 and thus
                # masquerading as a complete history.
                + self._skipped_tick_digest_count
            ),
            resident_commands=len(self._commands),
            resident_events=len(self._events),
            resident_tick_digests=len(self._tick_digests),
            max_resident_commands=self._max_resident_commands,
            max_resident_events=self._max_resident_events,
            max_resident_tick_digests=self._max_resident_tick_digests,
        )

    @property
    def budget_ledger(self) -> ForecastBudgetLedger:
        return self._budget

    @property
    def effective_max_cost(self) -> int:
        """Current deployment-cost cap including confirmed civilian retreats."""

        return self.content.max_cost + self.max_cost_bonus

    @property
    def capabilities(self) -> CapabilityReport:
        declarations = {
            declaration.mechanism: declaration for declaration in self.content.mechanism_fidelity
        }
        required = tuple(
            MechanismFidelity(
                mechanism=name,
                level=(
                    declarations[name].level
                    if declarations[name].level in (FidelityLevel.C, FidelityLevel.U)
                    else FidelityLevel.C
                ),
                note=(
                    declarations[name].note
                    if declarations[name].level in (FidelityLevel.C, FidelityLevel.U)
                    else declarations[name].note
                    + "; capped at C by the uncalibrated engine implementation"
                ),
            )
            for name in sorted(self.content.required_mechanisms)
        )
        ready = all(item.level in (FidelityLevel.A, FidelityLevel.B) for item in required)
        resolved_content = (
            ENGINE_RESOLVED_CONTENT_MECHANISMS
            if any(self._route_teleports.values())
            else frozenset()
        )
        return CapabilityReport(
            engine_version=ENGINE_VERSION,
            required=required,
            unsupported=tuple(
                sorted(
                    (set(self.content.unsupported_mechanisms) - resolved_content)
                    | ENGINE_UNSUPPORTED_MECHANISMS
                )
            ),
            competition_ready=ready,
        )

    def legal_actions(self) -> tuple[PublicAction, ...]:
        if self.status is not BattleStatus.RUNNING:
            return ()
        actions: list[PublicAction] = [SetPausedAction(paused=not self.paused)]
        actions.append(SetSpeedAction(speed=2 if self.speed == 1 else 1))
        if not self.paused:
            actions.append(WaitAction())
        deployed_operator_ids = {item.operator_id for item in self.operators.values()}
        occupied = {
            (item.position.col, item.position.row)
            for item in (*self.operators.values(), *self.summons.values())
        }
        for operator_id in sorted(self.roster):
            definition = self._operator_defs[operator_id]
            roster = self.roster[operator_id]
            if (
                operator_id in deployed_operator_ids
                or roster.redeploy_remaining > 0
                or definition.deploy_cost > self.cost
            ):
                continue
            for action in self._deploy_action_templates[operator_id]:
                if (action.position.col, action.position.row) in occupied:
                    continue
                actions.append(action)
        actions.extend(
            ActivateSkillAction(instance_id=instance_id, skill_id=skill.skill_id)
            for instance_id, operator in sorted(self.operators.items())
            for skill in operator.skills
            if self._skill_can_activate(operator, skill)
        )
        actions.extend(
            ActivateSkillAction(instance_id=instance_id, skill_id=skill.skill_id)
            for instance_id, summon in sorted(self.summons.items())
            for skill in summon.skills
            if self._skill_can_activate(summon, skill)
        )
        actions.extend(
            SelectTargetAction(
                instance_id=instance_id,
                skill_id=skill.skill_id,
                target_instance_id=enemy.instance_id,
            )
            for instance_id, operator in sorted(self.operators.items())
            for skill in operator.skills
            if self._skill_has_selected_target(operator, skill.skill_id)
            for enemy in sorted(self.enemies.values(), key=lambda item: item.instance_id)
            if self._selected_target_is_valid(operator, skill.skill_id, enemy.instance_id)
        )
        actions.extend(
            SelectTargetAction(
                instance_id=instance_id,
                skill_id=skill.skill_id,
                target_instance_id=enemy.instance_id,
            )
            for instance_id, summon in sorted(self.summons.items())
            for skill in summon.skills
            if self._skill_has_selected_target(summon, skill.skill_id)
            for enemy in sorted(self.enemies.values(), key=lambda item: item.instance_id)
            if self._selected_target_is_valid(summon, skill.skill_id, enemy.instance_id)
        )
        actions.extend(
            RetreatAction(instance_id=instance_id) for instance_id in sorted(self.operators)
        )
        actions.extend(
            RetreatAction(instance_id=instance_id) for instance_id in sorted(self.summons)
        )
        return tuple(actions)

    def execute_program_ir(
        self,
        program: "BattleProgramIR | Mapping[str, object]",
        *,
        max_ticks: int = 216_000,
        deployment_input_lead_policy: Literal["nominal", "lower", "upper"] = "nominal",
        deployment_condition_observation_delay_ticks: int = 0,
        deployment_condition_observation_delay_ticks_interval: tuple[int, int] | None = None,
        deployment_input_cost_recovery_multiplier_milli: int = 500,
    ) -> "BattleProgramExecutionReceipt":
        """Execute the shared solver/simulator/MAA IR directly.

        The local import keeps the battle kernel independent of compiler-side
        modules while giving callers one public entry point instead of a second
        deployment-action input format.
        """

        from ..battle_program_ir import BattleProgramIR
        from .program_executor import execute_battle_program

        parsed = (
            program
            if isinstance(program, BattleProgramIR)
            else BattleProgramIR.model_validate(program)
        )
        return execute_battle_program(
            self,
            parsed,
            max_ticks=max_ticks,
            deployment_input_lead_policy=deployment_input_lead_policy,
            deployment_condition_observation_delay_ticks=(
                deployment_condition_observation_delay_ticks
            ),
            deployment_condition_observation_delay_ticks_interval=(
                deployment_condition_observation_delay_ticks_interval
            ),
            deployment_input_cost_recovery_multiplier_milli=(
                deployment_input_cost_recovery_multiplier_milli
            ),
        )

    def observe(
        self, view: Literal["known_only", "omniscient"] = "known_only"
    ) -> KnownOnlyObservation | OmniscientObservation:
        if view == "omniscient":
            if not self._allow_omniscient:
                raise ObservationAccessError("omniscient observation is disabled for this session")
            pending = tuple(
                item.occurrence_id
                for item in self._scheduled
                if item.occurrence_id not in self.spawned_spawn_ids
            )
            return OmniscientObservation(
                snapshot=self.snapshot,
                pending_spawn_ids=pending,
                content_hash=self.content.content_hash,
                capabilities=self.capabilities,
            )

        visible = tuple(
            ObservedEnemy(
                instance_id=enemy.instance_id,
                enemy_id=enemy.enemy_id,
                hp=enemy.hp,
                col_milli=self._enemy_position_milli(enemy)[0],
                row_milli=self._enemy_position_milli(enemy)[1],
                blocked_by=enemy.blocked_by,
            )
            for enemy in sorted(self.enemies.values(), key=lambda item: item.instance_id)
            if enemy.route_teleport_destination_index is None
        )
        revealed = tuple(sorted({item.enemy_id for item in visible}))
        visible_devices = tuple(
            sorted(self.healing_devices.values(), key=lambda item: item.instance_id)
        )
        visible_actor_ids = (
            set(self.operators) | set(self.enemies) | set(self.summons) | set(self.healing_devices)
        )
        visible_effects = tuple(
            sorted(
                (
                    effect
                    for effect in self.active_effects.values()
                    if effect.target_instance_id in visible_actor_ids
                ),
                key=lambda item: item.instance_id,
            )
        )
        return KnownOnlyObservation(
            tick=self.tick,
            status=self.status,
            cost=self.cost,
            life_points=self.life_points,
            kills=self.kills,
            leaks=self.leaks,
            paused=self.paused,
            speed=self.speed,
            global_modifiers=self.global_modifiers,
            deployed=tuple(sorted(self.operators.values(), key=lambda item: item.instance_id)),
            visible_summons=tuple(sorted(self.summons.values(), key=lambda item: item.instance_id)),
            visible_projectiles=tuple(
                sorted(self.projectiles.values(), key=lambda item: item.instance_id)
            ),
            visible_devices=visible_devices,
            visible_polluted_zones=tuple(
                sorted(self.polluted_zones.values(), key=lambda item: item.instance_id)
            ),
            visible_effects=visible_effects,
            visible_enemies=visible,
            revealed_enemy_types=revealed,
            legal_actions=self.legal_actions(),
            snapshot_hash=self.snapshot.snapshot_hash,
            capabilities=self.capabilities,
        )

    def step(
        self,
        action: PublicAction,
        *,
        tick_observer: Callable[["BattleEngine"], None] | None = None,
    ) -> StepResult:
        if self.status is not BattleStatus.RUNNING:
            raise IllegalActionError("cannot act after the battle has ended")
        before = self.snapshot
        event_count_before = self.event_sequence
        resident_event_count_before = len(self._events)
        resident_digest_count_before = len(self._tick_digests)
        was_paused = self.paused
        self._apply_action(action)
        zero_tick_action = isinstance(
            action,
            (SelectTargetAction, SetPausedAction, SetSpeedAction),
        )
        ticks = 0
        if not zero_tick_action and not was_paused:
            ticks = action.ticks if isinstance(action, WaitAction) else 1
        for _ in range(ticks):
            if self.status is not BattleStatus.RUNNING:
                break
            cost_recovery_multiplier_milli = (
                action.cost_recovery_multiplier_milli
                if isinstance(action, WaitAction)
                else 1000
            )
            battle_progress_multiplier_milli = (
                action.battle_progress_multiplier_milli
                if isinstance(action, WaitAction)
                else 1000
            )
            spawn_progress_multiplier_milli = (
                (
                    action.spawn_progress_multiplier_milli
                    if action.spawn_progress_multiplier_milli is not None
                    else action.battle_progress_multiplier_milli
                )
                if isinstance(action, WaitAction)
                else 1000
            )
            self._advance_tick(
                cost_recovery_multiplier_milli=cost_recovery_multiplier_milli,
                battle_progress_multiplier_milli=battle_progress_multiplier_milli,
                spawn_progress_multiplier_milli=spawn_progress_multiplier_milli,
            )
            if tick_observer is not None:
                tick_observer(self)
        self.command_sequence += 1
        self._snapshot = self._make_snapshot(before.snapshot_hash)
        self._append_command(action, before, event_count_before)
        complete_history = self._replay_retention.mode == "complete"
        new_events = (
            tuple(self._events[resident_event_count_before:]) if complete_history else ()
        )
        new_digests = (
            tuple(self._tick_digests[resident_digest_count_before:])
            if complete_history
            else ()
        )
        return StepResult(
            snapshot=self.snapshot,
            events=tuple(item.model_copy(deep=True) for item in new_events),
            tick_digests=new_digests,
            observation=cast(KnownOnlyObservation, self.observe("known_only")),
            events_complete=complete_history,
            tick_digests_complete=complete_history,
        )

    def forecast(
        self,
        action_sequence: Sequence[PublicAction],
        *,
        horizon_ticks: int,
    ) -> ForecastResult:
        self._require_complete_replay_history("forecast")
        if self.status is not BattleStatus.RUNNING:
            raise IllegalActionError("cannot forecast after the battle has ended")
        if horizon_ticks < 1 or horizon_ticks > MAX_FORECAST_HORIZON_TICKS:
            raise ValueError(f"horizon_ticks must be within 1..{MAX_FORECAST_HORIZON_TICKS}")
        requested_ticks = 0
        projected_paused = self.paused
        projected_speed = self.speed
        for action in action_sequence:
            if isinstance(action, SetPausedAction):
                if action.paused == projected_paused:
                    raise IllegalActionError("forecast repeats the current pause state")
                projected_paused = action.paused
                continue
            if isinstance(action, SetSpeedAction):
                if action.speed == projected_speed:
                    raise IllegalActionError("forecast repeats the current speed")
                projected_speed = action.speed
                continue
            if isinstance(action, SelectTargetAction):
                continue
            if isinstance(action, WaitAction) and projected_paused:
                raise IllegalActionError("forecast cannot advance logical ticks while paused")
            if not projected_paused:
                requested_ticks += action.ticks if isinstance(action, WaitAction) else 1
        if requested_ticks > horizon_ticks:
            raise ValueError("action sequence exceeds the forecast horizon")
        request_hash = canonical_sha256(
            {
                "base_snapshot_hash": self.snapshot.snapshot_hash,
                "actions": [action.model_dump(mode="json") for action in action_sequence],
                "horizon_ticks": horizon_ticks,
            }
        )
        receipt = self._budget.charge(request_hash)
        branch = BattleEngine(
            self.content,
            self._initial_from_snapshot(self.snapshot),
            budget_ledger=ForecastBudgetLedger(f"internal-forecast-{receipt.forecast_index}"),
            allow_omniscient=False,
        )
        start_tick = branch.tick
        for action in action_sequence:
            if branch.status is not BattleStatus.RUNNING:
                break
            branch.step(action)
        remaining = horizon_ticks - (branch.tick - start_tick)
        while remaining > 0 and branch.status is BattleStatus.RUNNING:
            chunk = min(remaining, 300)
            branch.step(WaitAction(ticks=chunk))
            remaining -= chunk
        return ForecastResult(
            base_snapshot_hash=self.snapshot.snapshot_hash,
            final_snapshot_hash=branch.snapshot.snapshot_hash,
            simulated_ticks=branch.tick - start_tick,
            observation=cast(KnownOnlyObservation, branch.observe("known_only")),
            events=branch.events,
            tick_digests=branch.tick_digests,
            receipt=receipt,
        )

    def export_replay(self) -> BattleReplay:
        self._require_complete_replay_history("replay export")
        body = {
            "schema_id": REPLAY_SCHEMA,
            "engine_version": ENGINE_VERSION,
            "content_hash": self.content.content_hash,
            "source_wave_package_hash": self._source_wave_package_hash,
            "source_wave_scenario_id": self._source_wave_scenario_id,
            "source_wave_scenario_assignments": [
                item.model_dump(mode="json") for item in self._source_wave_scenario_assignments
            ],
            "initial_state": self._initial_state.model_dump(mode="json"),
            "commands": [item.model_dump(mode="json") for item in self._commands],
            "events": [item.model_dump(mode="json") for item in self._events],
            "tick_digests": [item.model_dump(mode="json") for item in self._tick_digests],
            "final_snapshot_hash": self.snapshot.snapshot_hash,
        }
        return BattleReplay(**body, replay_hash=canonical_sha256(body))

    def export_checkpoint(self) -> EngineCheckpoint:
        """Export a judge-side, content-addressed crash-recovery image."""

        self._require_complete_replay_history("checkpoint export")

        body = {
            "engine_version": ENGINE_VERSION,
            "content_hash": self.content.content_hash,
            "original_initial_state": self._initial_state,
            "snapshot": self.snapshot,
            "commands": self.commands,
            "events": self.events,
            "tick_digests": self.tick_digests,
            "budget": self._budget.export_state(),
        }
        unhashed = EngineCheckpoint(**body, checkpoint_hash="0" * 64)
        return unhashed.model_copy(update={"checkpoint_hash": unhashed.computed_hash()})

    @classmethod
    def restore_checkpoint(
        cls,
        content: ContentBundle,
        checkpoint: EngineCheckpoint,
    ) -> "BattleEngine":
        """Verify and restore a checkpoint without trusting serialized counters."""

        if not checkpoint.verify_hash():
            raise InvalidInitialStateError("checkpoint hash mismatch")
        if checkpoint.content_hash != content.content_hash:
            raise InvalidInitialStateError("checkpoint content hash mismatch")
        if not checkpoint.snapshot.verify_hash():
            raise InvalidInitialStateError("checkpoint snapshot hash mismatch")
        replay_body = {
            "schema_id": REPLAY_SCHEMA,
            "engine_version": ENGINE_VERSION,
            "content_hash": content.content_hash,
            "initial_state": checkpoint.original_initial_state.model_dump(mode="json"),
            "commands": [item.model_dump(mode="json") for item in checkpoint.commands],
            "events": [item.model_dump(mode="json") for item in checkpoint.events],
            "tick_digests": [item.model_dump(mode="json") for item in checkpoint.tick_digests],
            "final_snapshot_hash": checkpoint.snapshot.snapshot_hash,
        }
        try:
            replay = BattleReplay(**replay_body, replay_hash=canonical_sha256(replay_body))
        except ValueError as exc:
            raise InvalidInitialStateError(
                f"checkpoint tick digest chain is invalid: {exc}"
            ) from exc
        verification = verify_replay(content, replay)
        if not verification.valid:
            raise InvalidInitialStateError(
                "checkpoint replay is invalid: " + "; ".join(verification.issues)
            )
        try:
            ledger = ForecastBudgetLedger.from_state(checkpoint.budget)
        except ValueError as exc:
            raise InvalidInitialStateError(f"checkpoint budget is invalid: {exc}") from exc
        probe = cls(
            content,
            cls._initial_from_snapshot(checkpoint.snapshot),
            budget_ledger=ledger,
            allow_omniscient=False,
        )
        if probe.snapshot != checkpoint.snapshot:
            raise InvalidInitialStateError("checkpoint snapshot cannot be reconstructed")
        probe._initial_state = checkpoint.original_initial_state
        probe._commands = list(checkpoint.commands)
        probe._events = list(checkpoint.events)
        probe._tick_digests = list(checkpoint.tick_digests)
        probe._last_command_record_hash = (
            checkpoint.commands[-1].record_hash if checkpoint.commands else None
        )
        probe._max_resident_commands = len(probe._commands)
        probe._max_resident_events = len(probe._events)
        probe._max_resident_tick_digests = len(probe._tick_digests)
        return probe

    def _require_complete_replay_history(self, operation: str) -> None:
        if self._replay_retention.mode != "complete":
            raise IncompleteReplayHistoryError(
                f"{operation} requires complete replay retention; bounded history "
                "cannot be serialized as a complete audit stream"
            )

    def _expand_spawns(self) -> tuple[_ScheduledSpawn, ...]:
        expanded = [
            _ScheduledSpawn(
                occurrence_id=f"{spawn.spawn_id}:{ordinal}",
                tick=(
                    self._source_wave_spawn_ticks.get(spawn.spawn_id, spawn.tick)
                    if self._source_wave_spawn_ticks is not None
                    else spawn.tick
                )
                + ordinal * spawn.interval_ticks,
                enemy_id=spawn.enemy_id,
                route_id=spawn.route_id,
            )
            for spawn in self.content.spawns
            if self._source_wave_selected_spawn_ids is None
            or spawn.spawn_id in self._source_wave_selected_spawn_ids
            for ordinal in range(spawn.count)
        ]
        return tuple(sorted(expanded, key=lambda item: (item.tick, item.occurrence_id)))

    def _normalize_and_validate_initial(self, state: InitialState) -> InitialState:
        if state.content_hash != self.content.content_hash:
            raise InvalidInitialStateError("initial state content hash does not match bundle")
        if state.max_cost_bonus:
            # Two sources raise the cap, and the check is the *sum* of what the
            # bundle proves.  It used to demand the bundle be Mainline 12 and
            # the bonus be an even multiple of civilian exits -- an identity
            # binding of the same kind that cost 14 stages their instantiation
            # earlier, and it would refuse 街头应急储备's 5 outright for being
            # odd and for being in the wrong topic.
            civilian_spawn_ids = {
                item.occurrence_id
                for item in self._scheduled
                if item.enemy_id in MAINLINE12_CIVILIAN_IDS
            }
            civilian_bonus = (
                2 * len(civilian_spawn_ids & set(state.spawned_spawn_ids))
                if self.content.bundle_id == MAINLINE12_BUNDLE_ID
                else 0
            )
            # Every placed device that pays out on death, whether or not it has
            # died yet: a snapshot does not record which ones did, so the
            # ceiling is what the board could have paid.
            device_bonus = self._device_max_cost_ceiling()
            maximum_bonus = civilian_bonus + device_bonus
            if state.max_cost_bonus > maximum_bonus:
                raise InvalidInitialStateError(
                    "maximum-cost bonus exceeds what this bundle's civilian exits "
                    "and destructible devices could have granted"
                )
        if state.cost > self.content.max_cost + state.max_cost_bonus:
            raise InvalidInitialStateError("initial cost exceeds bundle max_cost")
        if state.battle_progress_slowdown_debt_milli > state.tick * 1000:
            raise InvalidInitialStateError(
                "battle progress slowdown debt exceeds the elapsed wall-clock tick budget"
            )
        if state.spawn_progress_slowdown_debt_milli > state.tick * 1000:
            raise InvalidInitialStateError(
                "spawn progress slowdown debt exceeds the elapsed wall-clock tick budget"
            )
        if state.status is BattleStatus.DEFEAT and state.life_points != 0:
            raise InvalidInitialStateError("defeat state must have zero life points")
        if state.status is not BattleStatus.DEFEAT and state.life_points == 0:
            raise InvalidInitialStateError("zero life points require defeat status")
        if (
            state.global_modifiers != GlobalModifierState()
            and "global_modifiers" not in self.content.required_mechanisms
        ):
            raise InvalidInitialStateError(
                "non-default initial global modifiers require global_modifiers"
            )
        if not set(state.spawned_spawn_ids) <= self._scheduled_ids:
            unknown = sorted(set(state.spawned_spawn_ids) - self._scheduled_ids)
            raise InvalidInitialStateError(f"unknown spawned occurrence ids: {unknown}")
        if len(set(state.spawned_spawn_ids)) != len(state.spawned_spawn_ids):
            raise InvalidInitialStateError("spawned occurrence ids must be unique")
        scheduled_by_id = {item.occurrence_id: item for item in self._scheduled}
        spawned = set(state.spawned_spawn_ids)
        spawn_progress_tick = (
            state.tick * 1000 - state.spawn_progress_slowdown_debt_milli
        ) // 1000
        future_spawned = sorted(
            occurrence_id
            for occurrence_id in spawned
            if scheduled_by_id[occurrence_id].tick > spawn_progress_tick
        )
        if future_spawned:
            raise InvalidInitialStateError(
                f"future occurrences are marked spawned: {future_spawned}"
            )
        missed_due = sorted(
            item.occurrence_id
            for item in self._scheduled
            if item.tick <= spawn_progress_tick and item.occurrence_id not in spawned
        )
        if missed_due:
            raise InvalidInitialStateError(
                f"due occurrences are missing from the spawn cursor: {missed_due}"
            )

        operator_ids: set[str] = set()
        instance_ids: set[str] = set()
        permitted_constraint_overlaps = {
            (enemy.block_constraint.position.col, enemy.block_constraint.position.row): set(
                enemy.block_constraint.blocker_instance_ids
            )
            for enemy in state.enemies
            if enemy.block_constraint is not None
            and enemy.block_constraint.constraint_state == "satisfied"
        }
        occupied_tiles: dict[tuple[int, int], list[str]] = {}
        normalized_operators: list[OperatorState] = []
        for operator in state.operators:
            definition = self._operator_defs.get(operator.operator_id)
            if definition is None:
                raise InvalidInitialStateError(
                    f"unknown operator in initial state: {operator.operator_id}"
                )
            if operator.operator_id in operator_ids:
                raise InvalidInitialStateError("an operator may have only one live instance")
            operator_ids.add(operator.operator_id)
            if operator.instance_id in instance_ids:
                raise InvalidInitialStateError("actor instance ids must be unique")
            instance_ids.add(operator.instance_id)
            if operator.hp > definition.max_hp:
                raise InvalidInitialStateError("operator hp exceeds definition")
            if operator.hp <= 0:
                raise InvalidInitialStateError("initial operator hp must be positive")
            if operator.deployment_tick > state.tick:
                raise InvalidInitialStateError("operator deployment tick is in the future")
            if operator.coefficient_stun_remaining_ticks > self._max_control_ticks():
                raise InvalidInitialStateError("coefficient stun exceeds the frozen source")
            if (
                operator.coefficient_frozen_remaining_ticks
                > operator.coefficient_stun_remaining_ticks
            ):
                raise InvalidInitialStateError(
                    "frozen ticks outlast the hold they are part of"
                )
            if (
                operator.coefficient_cold_remaining_ticks
                and operator.coefficient_frozen_remaining_ticks
            ):
                raise InvalidInitialStateError(
                    "cold and frozen are successive states, not simultaneous ones"
                )
            if operator.coefficient_cold_remaining_ticks > self._max_control_ticks():
                raise InvalidInitialStateError("cold exceeds its source duration")
            if operator.enemy_coefficient_defense_reduction and not any(
                coefficient_value(enemy, "defdown.def") is not None
                for enemy in self.content.enemies
            ):
                raise InvalidInitialStateError(
                    "enemy coefficient defense reduction lacks a content source"
                )
            self._validate_map_point(operator.position)
            if operator.position not in self.content.map.deployable_tiles:
                raise InvalidInitialStateError("initial operator tile is not deployable")
            if not self._tile_accepts_operator(operator.position, definition):
                raise InvalidInitialStateError(
                    "initial operator deployment class does not match its tile"
                )
            tile = (operator.position.col, operator.position.row)
            occupants = occupied_tiles.get(tile, [])
            if occupants and not set((*occupants, operator.instance_id)) <= (
                permitted_constraint_overlaps.get(tile, set())
            ):
                raise InvalidInitialStateError("multiple operators occupy the same tile")
            occupied_tiles.setdefault(tile, []).append(operator.instance_id)
            expected_skills = {skill.skill_id: skill for skill in definition.skills}
            provided_skills = {skill.skill_id: skill for skill in operator.skills}
            if len(provided_skills) != len(operator.skills):
                raise InvalidInitialStateError("operator skill states must be unique")
            if not operator.skills:
                provided_skills = {
                    skill_id: SkillState(skill_id=skill_id, sp=skill.initial_sp)
                    for skill_id, skill in expected_skills.items()
                }
            if set(provided_skills) != set(expected_skills):
                raise InvalidInitialStateError(
                    "operator skill states must exactly match its definition"
                )
            for skill_id, skill_state in provided_skills.items():
                skill = expected_skills[skill_id]
                if skill_state.sp > skill.sp_cost * skill.max_charges:
                    raise InvalidInitialStateError("skill SP exceeds its definition")
                if skill_state.cooldown_remaining > skill.cooldown_ticks:
                    raise InvalidInitialStateError("skill cooldown exceeds its definition")
                if skill_state.active_remaining > skill.duration_ticks:
                    raise InvalidInitialStateError("skill duration exceeds its definition")
                if (
                    skill_state.activation_pending
                    and not state.paused
                    and skill.activation_policy
                    not in {
                        SkillActivationPolicy.AUTOMATIC,
                        SkillActivationPolicy.AUTO_NEXT_HEAL,
                    }
                ):
                    raise InvalidInitialStateError(
                        "pending skill activation requires a paused initial state"
                    )
                if skill_state.armed and skill.activation_policy not in {
                    SkillActivationPolicy.NEXT_ATTACK,
                    SkillActivationPolicy.AUTO_NEXT_ATTACK,
                    SkillActivationPolicy.NEXT_HEAL,
                }:
                    raise InvalidInitialStateError("only next-action skills may carry armed state")
                if skill_state.armed and skill_state.active_remaining:
                    raise InvalidInitialStateError("armed skill cannot already be active")
            normalized_operators.append(
                operator.model_copy(
                    update={
                        "skills": tuple(provided_skills[key] for key in sorted(provided_skills))
                    }
                )
            )

        for enemy in state.enemies:
            definition = self._enemy_defs.get(enemy.enemy_id)
            route = self._routes.get(enemy.route_id)
            if definition is None or route is None:
                raise InvalidInitialStateError(
                    f"unknown enemy or route for initial actor: {enemy.instance_id}"
                )
            if definition.movement_layer is not route.movement_layer:
                raise InvalidInitialStateError("enemy and route movement layers must match")
            if enemy.instance_id in instance_ids:
                raise InvalidInitialStateError("actor instance ids must be unique")
            instance_ids.add(enemy.instance_id)
            if enemy.hp > definition.max_hp:
                raise InvalidInitialStateError("enemy hp exceeds definition")
            if enemy.hp <= 0:
                raise InvalidInitialStateError("initial enemy hp must be positive")
            if enemy.movement_phase_remainder >= self.content.ticks_per_second * 1000:
                raise InvalidInitialStateError("enemy movement phase remainder is out of range")
            if enemy.herd_resting_remaining_ticks and (
                (
                    self.content.bundle_id != "level_rogue3_1-4"
                    or enemy.enemy_id
                    not in {"enemy_1303_mhshep", "enemy_1303_mhshep_b"}
                )
                and enemy.enemy_id not in STANDBY_REVIVE_ENEMY_IDS
            ):
                raise InvalidInitialStateError("enemy herd resting state differs from its source")
            standby_duration = coefficient_value(definition, "revive.unmove_duration")
            if (
                enemy.herd_resting_remaining_ticks
                and standby_duration is not None
                and enemy.herd_resting_remaining_ticks
                > self._seconds_micros_to_ticks(standby_duration)
            ):
                raise InvalidInitialStateError("enemy standby duration exceeds its source")
            self._validate_enemy_coefficient_runtime(enemy, definition)
            path = enemy.path_override or self._route_runtime_points[route.route_id]
            if enemy.waypoint_index >= len(path):
                raise InvalidInitialStateError("enemy waypoint index is outside its route")
            waits = self._route_runtime_waits[route.route_id]
            teleport = self._route_teleports[route.route_id].get(enemy.waypoint_index)
            if enemy.route_teleport_destination_index is not None:
                if enemy.path_override or teleport is None:
                    raise InvalidInitialStateError("enemy teleport transit is not on a route entry")
                destination, transit_wait, _source_entry, _source_exit = teleport
                if enemy.route_teleport_destination_index != destination:
                    raise InvalidInitialStateError("enemy teleport destination changed")
                if enemy.wait_remaining_ticks > transit_wait:
                    raise InvalidInitialStateError("enemy teleport wait exceeds its source")
            elif enemy.wait_remaining_ticks and enemy.wait_remaining_ticks > waits[
                enemy.waypoint_index
            ]:
                raise InvalidInitialStateError("enemy route wait state is inconsistent")
            if enemy.path_override and enemy.wait_remaining_ticks:
                raise InvalidInitialStateError("enemy route wait state is inconsistent")
            if enemy.spawn_occurrence_id is not None:
                occurrence = scheduled_by_id.get(enemy.spawn_occurrence_id)
                if occurrence is None or enemy.spawn_occurrence_id not in spawned:
                    raise InvalidInitialStateError(
                        "enemy spawn occurrence is unknown or not marked spawned"
                    )
                if occurrence.enemy_id != enemy.enemy_id or occurrence.route_id != enemy.route_id:
                    raise InvalidInitialStateError(
                        "enemy does not match its declared spawn occurrence"
                    )
            if enemy.path_override:
                if definition.movement_layer is not MovementLayer.GROUND:
                    raise InvalidInitialStateError("flying enemies cannot have path overrides")
                if path[-1] != route.waypoints[-1]:
                    raise InvalidInitialStateError("path override must end at the route goal")
                self._validate_path(path, context="initial enemy path override")

        enemy_state_map = {enemy.instance_id: enemy for enemy in state.enemies}
        ghost_children_by_owner: dict[str, list[str]] = defaultdict(list)
        for enemy in state.enemies:
            runtime = enemy.coefficient_runtime
            if runtime is None or runtime.ghost_owner_instance_id is None:
                continue
            owner = enemy_state_map.get(runtime.ghost_owner_instance_id)
            if owner is None or owner.enemy_id not in GHOST_REDACE_IDS:
                raise InvalidInitialStateError("hologram runtime owner is not a live Redmark")
            owner_runtime = owner.coefficient_runtime
            if owner_runtime is None or not owner_runtime.ghost_created:
                raise InvalidInitialStateError("hologram owner has not committed its first hit")
            if enemy.spawn_occurrence_id is not None:
                raise InvalidInitialStateError("created hologram cannot claim a source occurrence")
            ghost_children_by_owner[owner.instance_id].append(enemy.instance_id)
        if any(len(children) > 1 for children in ghost_children_by_owner.values()):
            raise InvalidInitialStateError("a Redmark cannot own more than one live hologram")
        enemy_occurrences = [
            enemy.spawn_occurrence_id
            for enemy in state.enemies
            if enemy.spawn_occurrence_id is not None
        ]
        if len(enemy_occurrences) != len(set(enemy_occurrences)):
            raise InvalidInitialStateError("live enemies cannot share a spawn occurrence")
        for operator in normalized_operators:
            for skill_state in operator.skills:
                has_selected_target = self._skill_has_selected_target(
                    operator, skill_state.skill_id
                )
                if skill_state.selected_target_id is None:
                    if skill_state.activation_pending and has_selected_target:
                        raise InvalidInitialStateError(
                            "pending targeted skill lacks its selected target"
                        )
                    continue
                if not has_selected_target:
                    raise InvalidInitialStateError(
                        "skill state selects a target for a non-targeted skill"
                    )
                enemy = enemy_state_map.get(skill_state.selected_target_id)
                if enemy is None or not self._selected_target_is_valid_for_state(
                    operator,
                    skill_state.skill_id,
                    enemy,
                ):
                    raise InvalidInitialStateError("selected skill target is not legal")

        normalized_summons: list[SummonState] = []
        for summon in state.summons:
            definition = self._summon_defs.get(summon.summon_id)
            if definition is None:
                raise InvalidInitialStateError(f"unknown summon: {summon.summon_id}")
            if summon.instance_id in instance_ids:
                raise InvalidInitialStateError("actor instance ids must be unique")
            instance_ids.add(summon.instance_id)
            if summon.hp > definition.max_hp:
                raise InvalidInitialStateError("summon hp exceeds definition")
            if summon.hp <= 0:
                raise InvalidInitialStateError("initial summon hp must be positive")
            if summon.coefficient_stun_remaining_ticks > self._max_control_ticks():
                raise InvalidInitialStateError("coefficient stun exceeds the frozen source")
            if (
                summon.coefficient_frozen_remaining_ticks
                > summon.coefficient_stun_remaining_ticks
            ):
                raise InvalidInitialStateError(
                    "frozen ticks outlast the hold they are part of"
                )
            if (
                summon.coefficient_cold_remaining_ticks
                and summon.coefficient_frozen_remaining_ticks
            ):
                raise InvalidInitialStateError(
                    "cold and frozen are successive states, not simultaneous ones"
                )
            if summon.coefficient_cold_remaining_ticks > self._max_control_ticks():
                raise InvalidInitialStateError("cold exceeds its source duration")
            if summon.enemy_coefficient_defense_reduction and not any(
                coefficient_value(enemy, "defdown.def") is not None
                for enemy in self.content.enemies
            ):
                raise InvalidInitialStateError(
                    "enemy coefficient defense reduction lacks a content source"
                )
            if summon.heal_cooldown >= definition.healing_interval_ticks:
                raise InvalidInitialStateError("summon healing cooldown exceeds its interval")
            self._validate_map_point(summon.position)
            tile = (summon.position.col, summon.position.row)
            occupants = occupied_tiles.get(tile, [])
            if occupants and not set((*occupants, summon.instance_id)) <= (
                permitted_constraint_overlaps.get(tile, set())
            ):
                raise InvalidInitialStateError("multiple friendlies occupy the same tile")
            occupied_tiles.setdefault(tile, []).append(summon.instance_id)
            expected_skills = {skill.skill_id: skill for skill in definition.skills}
            provided_skills = {skill.skill_id: skill for skill in summon.skills}
            if len(provided_skills) != len(summon.skills):
                raise InvalidInitialStateError("summon skill states must be unique")
            if not summon.skills:
                provided_skills = {
                    skill_id: SkillState(skill_id=skill_id, sp=skill.initial_sp)
                    for skill_id, skill in expected_skills.items()
                }
            if set(provided_skills) != set(expected_skills):
                raise InvalidInitialStateError(
                    "summon skill states must exactly match its definition"
                )
            for skill_id, skill_state in provided_skills.items():
                skill = expected_skills[skill_id]
                if skill_state.sp > skill.sp_cost * skill.max_charges:
                    raise InvalidInitialStateError("summon skill SP exceeds its definition")
                if skill_state.cooldown_remaining > skill.cooldown_ticks:
                    raise InvalidInitialStateError("summon skill cooldown exceeds its definition")
                if skill_state.active_remaining > skill.duration_ticks:
                    raise InvalidInitialStateError("summon skill duration exceeds its definition")
                if (
                    skill_state.activation_pending
                    and not state.paused
                    and skill.activation_policy is not SkillActivationPolicy.AUTOMATIC
                ):
                    raise InvalidInitialStateError(
                        "pending summon skill activation requires a paused initial state"
                    )
                if skill_state.armed and skill.activation_policy not in {
                    SkillActivationPolicy.NEXT_ATTACK,
                    SkillActivationPolicy.AUTO_NEXT_ATTACK,
                    SkillActivationPolicy.NEXT_HEAL,
                }:
                    raise InvalidInitialStateError(
                        "only next-action summon skills may carry armed state"
                    )
                if skill_state.armed and skill_state.active_remaining:
                    raise InvalidInitialStateError("armed summon skill cannot already be active")
            normalized_summons.append(
                summon.model_copy(
                    update={
                        "skills": tuple(provided_skills[key] for key in sorted(provided_skills))
                    }
                )
            )

        for summon in normalized_summons:
            for skill_state in summon.skills:
                has_selected_target = self._skill_has_selected_target(summon, skill_state.skill_id)
                if skill_state.selected_target_id is None:
                    if skill_state.activation_pending and has_selected_target:
                        raise InvalidInitialStateError(
                            "pending targeted summon skill lacks its selected target"
                        )
                    continue
                if not has_selected_target:
                    raise InvalidInitialStateError(
                        "summon skill state selects a target for a non-targeted skill"
                    )
                enemy = enemy_state_map.get(skill_state.selected_target_id)
                if enemy is None or not self._selected_target_is_valid_for_state(
                    summon,
                    skill_state.skill_id,
                    enemy,
                ):
                    raise InvalidInitialStateError("selected summon skill target is not legal")

        normalized_projectiles: list[ProjectileState] = []
        for projectile in state.projectiles:
            if projectile.instance_id in instance_ids:
                raise InvalidInitialStateError("actor and projectile instance ids must be unique")
            instance_ids.add(projectile.instance_id)
            if projectile.target_instance_id not in {item.instance_id for item in state.enemies}:
                raise InvalidInitialStateError("projectile target must be a live enemy")
            source = next(
                (
                    item
                    for item in normalized_operators
                    if item.instance_id == projectile.source_instance_id
                ),
                None,
            )
            if source is None:
                raise InvalidInitialStateError("projectile source must be a live operator")
            source_definition = self._operator_defs[source.operator_id]
            skill_projectile_limits = [
                int(effect.projectile_travel_ticks)
                + effect.packet_interval_ticks * (effect.hit_count - 1)
                for skill in source_definition.skills
                for effect in skill.effects
                if isinstance(effect, AttackScaleEffectDefinition)
                and effect.projectile_travel_ticks is not None
            ]
            maximum_projectile_ticks = max(
                [source_definition.projectile_travel_ticks, *skill_projectile_limits]
            )
            if not maximum_projectile_ticks:
                raise InvalidInitialStateError("projectile source has no delayed attack")
            if projectile.remaining_ticks > maximum_projectile_ticks:
                raise InvalidInitialStateError("projectile remaining time exceeds its source")
            if projectile.splash_radius_tiles != source_definition.projectile_splash_radius_tiles:
                raise InvalidInitialStateError("projectile splash radius differs from its source")
            normalized_projectiles.append(projectile)

        normalized_devices: list[HealingDeviceState] = []
        for device in state.healing_devices:
            definition = self._device_defs.get(device.device_id)
            if definition is None:
                raise InvalidInitialStateError(
                    f"unknown healing device in initial state: {device.device_id}"
                )
            if device.instance_id in instance_ids:
                raise InvalidInitialStateError("actor instance ids must be unique")
            instance_ids.add(device.instance_id)
            if device.hp > definition.max_hp:
                raise InvalidInitialStateError("device hp exceeds definition")
            if device.pulse_cooldown >= definition.pulse_interval_ticks:
                raise InvalidInitialStateError("device cooldown exceeds its interval")
            self._validate_map_point(device.position)
            normalized_devices.append(device)

        normalized_polluted_zones: list[PollutedZoneState] = []
        for zone in state.polluted_zones:
            if zone.instance_id in instance_ids:
                raise InvalidInitialStateError(
                    "actor and polluted-zone instance ids must be unique"
                )
            instance_ids.add(zone.instance_id)
            device = self._summon_defs.get(zone.source_enemy_id)
            if device is not None and device.pollution_aura_radius_milli_tiles:
                # A device-sourced aura (汲营枯枝).  Its figures live on the
                # summon definition, so the enemy-side radius cross-check below
                # does not apply -- but the radius still has to match what the
                # device declares, which is checked here instead.
                if zone.radius_milli_tiles != device.pollution_aura_radius_milli_tiles:
                    raise InvalidInitialStateError(
                        "device pollution zone radius differs from its source device"
                    )
                continue
            definition = self._enemy_defs.get(zone.source_enemy_id)
            if definition is None or definition.enemy_id not in (
                self._polluted_death_ids | self._polluted_tank_ids
            ):
                raise InvalidInitialStateError(
                    "polluted zone must retain a Return Body source enemy"
                )
            prefix = (
                "PollutedDie"
                if definition.enemy_id in self._polluted_death_ids
                else "PollutedRangedAtk"
            )
            expected_radius = coefficient_value(
                definition,
                f"{prefix}.projectile_range"
                if prefix == "PollutedDie"
                else f"{prefix}.range_radius",
            )
            expected_life = coefficient_value(
                definition, f"{prefix}.projectile_life_time"
            )
            expected_low = coefficient_value(
                definition, f"{prefix}.polluted_damage_low"
            )
            expected_high = coefficient_value(
                definition, f"{prefix}.polluted_damage_high"
            )
            if None in {expected_radius, expected_life, expected_low, expected_high}:
                raise InvalidInitialStateError("polluted-zone source matrix is incomplete")
            if cast(int, expected_radius) % 1000:
                raise InvalidInitialStateError(
                    "polluted-zone radius is not exactly representable in milli-tiles"
                )
            if (
                zone.radius_milli_tiles != cast(int, expected_radius) // 1000
                or zone.remaining_ticks
                > self._seconds_micros_to_ticks(cast(int, expected_life))
                or zone.low_ground_damage != cast(int, expected_low) // 1_000_000
                or zone.high_ground_damage != cast(int, expected_high) // 1_000_000
            ):
                raise InvalidInitialStateError(
                    "polluted-zone runtime differs from its source coefficients"
                )
            if not (
                0 <= zone.center_col_milli < self.content.map.width * 1000
                and 0 <= zone.center_row_milli < self.content.map.height * 1000
            ):
                raise InvalidInitialStateError("polluted-zone center is outside the map")
            normalized_polluted_zones.append(zone)

        active_effect_ids: set[str] = set()
        normalized_operator_map = {
            operator.instance_id: operator for operator in normalized_operators
        }
        normalized_summon_map = {summon.instance_id: summon for summon in normalized_summons}
        normalized_friendly_map: dict[str, OperatorState | SummonState] = (
            normalized_operator_map | normalized_summon_map
        )
        enemy_map = {enemy.instance_id: enemy for enemy in state.enemies}
        tile_by_effect_id = {
            tile_effect.effect_id: tile
            for tile in self.content.special_tiles
            for tile_effect in tile.effects
        }
        enemy_modifier_stack_keys: set[tuple[str, str]] = set()
        for effect in state.active_effects:
            if effect.instance_id in active_effect_ids:
                raise InvalidInitialStateError("active effect instance ids must be unique")
            if effect.instance_id in instance_ids:
                raise InvalidInitialStateError("effect and actor instance ids must not overlap")
            active_effect_ids.add(effect.instance_id)
            try:
                effect_definition = self._effect_vm.definition(effect.effect_id)
            except EffectVMError as exc:
                raise InvalidInitialStateError(str(exc)) from exc
            if not isinstance(
                effect_definition,
                (
                    StatModifierEffectDefinition,
                    SpecialTileStatModifierDefinition,
                    SpecialTileControlEffectDefinition,
                    EnemyTimedSelfModifierDefinition,
                ),
            ):
                raise InvalidInitialStateError(
                    "only persistent stat or control effects may appear in active_effects"
                )
            if effect.remaining_ticks > effect_definition.duration_ticks:
                raise InvalidInitialStateError("active effect duration exceeds definition")
            if isinstance(effect_definition, EnemyTimedSelfModifierDefinition):
                source_enemy = enemy_map.get(effect.source_instance_id)
                if source_enemy is None or effect.target_instance_id != source_enemy.instance_id:
                    raise InvalidInitialStateError(
                        "enemy timed self modifier must source and target one live enemy"
                    )
                expected_effect_id = enemy_timed_modifier_effect_id(
                    source_enemy.enemy_id, effect_definition.modifier_id
                )
                if (
                    effect.effect_id != expected_effect_id
                    or effect.source_skill_id != f"enemy_spawn:{source_enemy.enemy_id}"
                ):
                    raise InvalidInitialStateError(
                        "enemy timed self modifier has an invalid spawn source"
                    )
                if (
                    effect.geometry_source_position is not None
                    or effect.geometry_source_direction is not None
                    or effect.geometry_cells
                    or effect.geometry_target_ids
                ):
                    raise InvalidInitialStateError(
                        "enemy timed self modifier cannot carry effect geometry"
                    )
                stack_identity = (source_enemy.instance_id, effect_definition.stack_key)
                if stack_identity in enemy_modifier_stack_keys:
                    raise InvalidInitialStateError(
                        "enemy timed self modifier stack keys must be unique per target"
                    )
                enemy_modifier_stack_keys.add(stack_identity)
                continue
            source = normalized_friendly_map.get(effect.source_instance_id)
            source_tile = tile_by_effect_id.get(effect.effect_id)
            if source_tile is not None:
                if (
                    effect.source_instance_id != source_tile.tile_id
                    or effect.source_skill_id != f"special_tile:{source_tile.tile_id}"
                ):
                    raise InvalidInitialStateError(
                        "active special tile effect has an invalid source"
                    )
            else:
                if source is None:
                    raise InvalidInitialStateError(
                        "active skill effect source must be a live friendly"
                    )
                normal_attack_effect_ids = (
                    {
                        item.effect_id
                        for item in self._operator_defs[source.operator_id].normal_attack_effects
                    }
                    if isinstance(source, OperatorState)
                    else set()
                )
                if effect.effect_id in normal_attack_effect_ids:
                    if effect.source_skill_id != f"normal_attack:{source.operator_id}":
                        raise InvalidInitialStateError(
                            "normal attack effect has an invalid source marker"
                        )
                else:
                    try:
                        skill = self._skill_definition(source, effect.source_skill_id)
                    except KeyError as exc:
                        raise InvalidInitialStateError(
                            "active effect references an unknown source skill"
                        ) from exc
                    if effect.effect_id not in {item.effect_id for item in skill.effects}:
                        raise InvalidInitialStateError(
                            "active effect does not belong to its source skill"
                        )
            target = normalized_friendly_map.get(effect.target_instance_id)
            enemy_target = enemy_map.get(effect.target_instance_id)
            if target is None and enemy_target is None:
                raise InvalidInitialStateError("active effect target is not live")
            if isinstance(
                effect_definition,
                (StatModifierEffectDefinition, SpecialTileStatModifierDefinition),
            ):
                if target is not None and effect_definition.stat not in {
                    EffectStat.ATTACK,
                    EffectStat.DEFENSE,
                    EffectStat.HEALING_POWER,
                    EffectStat.ATTACK_INTERVAL_TICKS,
                    EffectStat.ATTACK_RANGE_TILES,
                    EffectStat.BLOCK_COUNT,
                }:
                    raise InvalidInitialStateError("effect stat is invalid for friendly target")
                if enemy_target is not None and effect_definition.stat not in {
                    EffectStat.ATTACK,
                    EffectStat.DEFENSE,
                    EffectStat.ATTACK_INTERVAL_TICKS,
                    EffectStat.MOVE_MILLI_TILES_PER_TICK,
                }:
                    raise InvalidInitialStateError("effect stat is invalid for enemy target")
            if source_tile is not None:
                if source_tile.target == "ground_enemies" and enemy_target is None:
                    raise InvalidInitialStateError("special tile effect targets the wrong side")
                if source_tile.target == "friendlies" and target is None:
                    raise InvalidInitialStateError("special tile effect targets the wrong side")
                if enemy_target is not None and (
                    self._enemy_defs[enemy_target.enemy_id].movement_layer is MovementLayer.FLYING
                ):
                    raise InvalidInitialStateError(
                        "special tile effect cannot target a flying enemy"
                    )
                continue
            if source is None:  # pragma: no cover - established above
                raise InvalidInitialStateError("active effect source disappeared")
            if not isinstance(effect_definition, StatModifierEffectDefinition):
                raise InvalidInitialStateError("skill active effect has an invalid definition")
            selector = effect_definition.target
            if effect_definition.range_offsets:
                expected_cells = self._effect_geometry_cells(source, effect_definition)
                expected_direction = source.direction if isinstance(source, OperatorState) else None
                if (
                    expected_cells is None
                    or effect.geometry_source_position != source.position
                    or effect.geometry_source_direction != expected_direction
                    or effect.geometry_cells != expected_cells
                ):
                    raise InvalidInitialStateError(
                        "active effect geometry snapshot does not match its definition"
                    )
                if selector is EffectTargetSelector.SELF:
                    if effect.target_instance_id != source.instance_id:
                        raise InvalidInitialStateError("self effect targets a different operator")
                elif selector in {
                    EffectTargetSelector.LOWEST_HP_ALLY_IN_RANGE,
                    EffectTargetSelector.ALL_ALLIES_IN_RANGE,
                }:
                    if target is None:
                        raise InvalidInitialStateError(
                            "discrete ally effect targets the wrong side"
                        )
                elif enemy_target is None:
                    raise InvalidInitialStateError("discrete enemy effect targets the wrong side")
                continue
            if effect.geometry_cells or effect.geometry_target_ids:
                raise InvalidInitialStateError(
                    "legacy scalar effect cannot carry a geometry snapshot"
                )
            if (
                selector is EffectTargetSelector.SELF
                and effect.target_instance_id != source.instance_id
            ):
                raise InvalidInitialStateError("self effect targets a different operator")
            if selector in {
                EffectTargetSelector.LOWEST_HP_ALLY_IN_RANGE,
                EffectTargetSelector.ALL_ALLIES_IN_RANGE,
            }:
                if (
                    target is None
                    or self._friendly_distance(source, target) > effect_definition.range_tiles
                ):
                    raise InvalidInitialStateError("ally effect target is outside its legal range")
            if selector is EffectTargetSelector.BLOCKED_ENEMIES and (
                enemy_target is None or effect.target_instance_id not in source.blocked_enemy_ids
            ):
                raise InvalidInitialStateError("enemy effect target is not blocked by its source")
            if selector is EffectTargetSelector.ALL_ENEMIES_IN_RANGE and (
                enemy_target is None
                or self._distance_milli(source.position, enemy_target)
                > effect_definition.range_tiles * 1000
            ):
                raise InvalidInitialStateError("enemy effect target is outside its legal range")
            if selector is EffectTargetSelector.SELECTED_ENEMY and (
                enemy_target is None
                or self._distance_milli(source.position, enemy_target)
                > effect_definition.range_tiles * 1000
            ):
                raise InvalidInitialStateError(
                    "selected enemy effect target is outside its legal range"
                )
            if selector is EffectTargetSelector.ATTACKED_ENEMY and enemy_target is None:
                raise InvalidInitialStateError("normal attack effect target must be a live enemy")

        rng_streams = {item.stream: item.draws for item in state.rng_streams}
        if len(rng_streams) != len(state.rng_streams):
            raise InvalidInitialStateError("rng stream names must be unique")
        unknown_rng_streams = set(rng_streams) - self._rng_stream_names
        if unknown_rng_streams:
            raise InvalidInitialStateError(
                f"initial state references unknown rng streams: {sorted(unknown_rng_streams)}"
            )

        generated_prefixes = (
            "op-",
            "enemy-",
            "summon-",
            "projectile-",
            "pollution-",
        )
        reserved_entity_ordinals = [
            int(instance_id.rsplit("-", 1)[-1])
            for instance_id in instance_ids
            if instance_id.startswith(generated_prefixes)
            and len(instance_id.rsplit("-", 1)[-1]) == 6
            and instance_id.rsplit("-", 1)[-1].isdigit()
        ]
        if reserved_entity_ordinals and state.entity_sequence < max(reserved_entity_ordinals):
            raise InvalidInitialStateError(
                "entity_sequence would collide with an existing generated instance"
            )

        reserved_effect_ordinals = [
            int(effect_id.removeprefix("effect-"))
            for effect_id in active_effect_ids
            if effect_id.startswith("effect-") and effect_id.removeprefix("effect-").isdigit()
        ]
        if reserved_effect_ordinals and state.effect_sequence < max(reserved_effect_ordinals):
            raise InvalidInitialStateError(
                "effect_sequence would collide with an existing effect instance"
            )

        roster_by_id = {item.operator_id: item for item in state.roster}
        if len(roster_by_id) != len(state.roster):
            raise InvalidInitialStateError("roster operator ids must be unique")
        if not set(roster_by_id) <= set(self._operator_defs):
            raise InvalidInitialStateError("roster references unknown operators")
        roster_operator_ids = set(roster_by_id) if state.roster else set(self._operator_defs)
        if not operator_ids <= roster_operator_ids:
            raise InvalidInitialStateError("deployed operator is not in the actual roster")
        expected_initial_bonus = sum(
            self._operator_defs[operator_id].initial_deployment_points_if_rostered
            for operator_id in roster_operator_ids
        )
        if state.initial_deployment_points_bonus != expected_initial_bonus:
            raise InvalidInitialStateError(
                "initial deployment-point bonus does not match the actual roster"
            )
        if (
            expected_initial_bonus
            and state.tick == 0
            and state.event_sequence == 0
            and state.command_sequence == 0
            and state.cost < expected_initial_bonus
        ):
            raise InvalidInitialStateError("pristine initial cost cannot be below its roster bonus")
        for operator_id in operator_ids:
            provided = roster_by_id.get(operator_id)
            if provided is not None and (
                provided.redeploy_remaining > 0 or provided.deploy_count == 0
            ):
                raise InvalidInitialStateError("deployed operator roster state is inconsistent")
        roster = tuple(
            roster_by_id.get(
                operator_id,
                RosterState(
                    operator_id=operator_id,
                    deploy_count=1 if operator_id in operator_ids else 0,
                ),
            )
            for operator_id in sorted(roster_operator_ids)
        )
        normalized = state.model_copy(
            update={
                "operators": tuple(normalized_operators),
                "summons": tuple(normalized_summons),
                "projectiles": tuple(normalized_projectiles),
                "healing_devices": tuple(normalized_devices),
                "polluted_zones": tuple(normalized_polluted_zones),
                "roster": roster,
                "rng_streams": tuple(
                    RNGStreamState(stream=name, draws=draws)
                    for name, draws in sorted(rng_streams.items())
                ),
            }
        )
        obstacle_keys = {
            (summon.position.col, summon.position.row)
            for summon in normalized.summons
            if self._summon_defs[summon.summon_id].blocks_routes
        }
        if obstacle_keys - self._walkable_keys():
            raise InvalidInitialStateError("route obstacle must occupy a walkable tile")
        for enemy in normalized.enemies:
            if self._enemy_defs[enemy.enemy_id].movement_layer is MovementLayer.FLYING:
                continue
            path = enemy.path_override or self._route_runtime_points[enemy.route_id]
            remaining = path[enemy.waypoint_index + (1 if enemy.segment_progress_milli else 0) :]
            if any((point.col, point.row) in obstacle_keys for point in remaining):
                raise InvalidInitialStateError("initial enemy path crosses an active obstacle")
        for scheduled in self._scheduled:
            if scheduled.occurrence_id in normalized.spawned_spawn_ids:
                continue
            route = self._routes[scheduled.route_id]
            if route.movement_layer is MovementLayer.FLYING:
                continue
            runtime_path = self._route_runtime_points[route.route_id]
            if self._route_teleports[route.route_id] and any(
                (point.col, point.row) in obstacle_keys for point in runtime_path[1:-1]
            ):
                raise InvalidInitialStateError(
                    "active obstacle intersects a future teleport route"
                )
            if not self._route_teleports[route.route_id] and self._path_around(
                runtime_path[0], runtime_path[-1], obstacle_keys
            ) is None:
                raise InvalidInitialStateError(
                    "active obstacles leave a future spawn without a path"
                )
        self._validate_block_contract(
            normalized.operators,
            normalized.summons,
            normalized.enemies,
            normalized.active_effects,
            tick=normalized.tick,
        )
        return normalized

    def _validate_map_point(self, point: GridPoint) -> None:
        if point.col >= self.content.map.width or point.row >= self.content.map.height:
            raise InvalidInitialStateError(f"point is outside map: {point}")

    def _validate_block_contract(
        self,
        operators: Iterable[OperatorState],
        summons: Iterable[SummonState],
        enemies: Iterable[EnemyState],
        active_effects: Iterable[ActiveEffectState] = (),
        *,
        tick: int,
    ) -> None:
        operator_map = {item.instance_id: item for item in operators}
        summon_map = {item.instance_id: item for item in summons}
        friendly_map: dict[str, OperatorState | SummonState] = operator_map | summon_map
        enemy_map = {item.instance_id: item for item in enemies}
        for friendly in friendly_map.values():
            if isinstance(friendly, OperatorState):
                block_count = self._effect_vm.effective_stat(
                    base=self._operator_defs[friendly.operator_id].block_count,
                    stat=EffectStat.BLOCK_COUNT,
                    target_instance_id=friendly.instance_id,
                    active_effects=active_effects,
                )
            else:
                block_count = self._summon_defs[friendly.summon_id].block_count
            if len(friendly.blocked_enemy_ids) > block_count:
                raise InvalidInitialStateError("friendly exceeds block capacity")
            for enemy_id in friendly.blocked_enemy_ids:
                enemy = enemy_map.get(enemy_id)
                constraint_blockers = (
                    set(enemy.block_constraint.blocker_instance_ids)
                    if enemy is not None
                    and enemy.block_constraint is not None
                    and enemy.block_constraint.constraint_state == "satisfied"
                    else set()
                )
                if enemy is None or (
                    enemy.blocked_by != friendly.instance_id
                    and friendly.instance_id not in constraint_blockers
                ):
                    raise InvalidInitialStateError("blocking relationship is not symmetric")
                if self._enemy_grid_point(enemy) != friendly.position:
                    raise InvalidInitialStateError("blocked actors do not share a tile")
                if self._enemy_defs[enemy.enemy_id].movement_layer is MovementLayer.FLYING:
                    raise InvalidInitialStateError("flying enemies cannot be blocked")
        for enemy in enemy_map.values():
            constraint = enemy.block_constraint
            coefficient = self._block_constraint_coefficient(enemy)
            if constraint is not None:
                if coefficient is None or coefficient.source_sha256 is None:
                    raise InvalidInitialStateError("block constraint lacks a bound coefficient")
                spawn_id = cast(str, enemy.spawn_occurrence_id).rpartition(":")[0]
                expected = (
                    cast(str, self.content.content_version),
                    cast(str, self.content.source_package_hash),
                    cast(str, self.content.stage_id),
                    spawn_id,
                    enemy.instance_id,
                    enemy.enemy_id,
                    coefficient.value_micros,
                    coefficient.value_micros // 1_000_000,
                    enemy.route_id,
                    coefficient.source_pointer,
                    coefficient.source_sha256,
                )
                actual = (
                    constraint.content_version,
                    constraint.source_package_hash,
                    constraint.stage_id,
                    constraint.spawn_id,
                    constraint.enemy_instance_id,
                    constraint.enemy_id,
                    constraint.value_micros,
                    constraint.required_block_count,
                    constraint.route_id,
                    constraint.source_pointer,
                    constraint.source_sha256,
                )
                if actual != expected:
                    raise InvalidInitialStateError("block constraint source binding changed")
                if constraint.tick > tick:
                    raise InvalidInitialStateError("block constraint evaluation is from the future")
                path = self._enemy_route_points(enemy)
                if (
                    constraint.waypoint_index != enemy.waypoint_index
                    or constraint.position != path[enemy.waypoint_index]
                ):
                    raise InvalidInitialStateError("block constraint route position changed")
                blockers = constraint.blocker_instance_ids
                if constraint.constraint_state == "satisfied":
                    if enemy.blocked_by != blockers[0]:
                        raise InvalidInitialStateError("block constraint primary blocker changed")
                    if any(
                        blocker_id not in friendly_map
                        or enemy.instance_id not in friendly_map[blocker_id].blocked_enemy_ids
                        or friendly_map[blocker_id].position != constraint.position
                        for blocker_id in blockers
                    ):
                        raise InvalidInitialStateError("block constraint blocker set changed")
                elif enemy.blocked_by is not None:
                    raise InvalidInitialStateError(
                        "unsatisfied block constraint cannot stop movement"
                    )
            elif coefficient is not None and enemy.blocked_by is not None:
                raise InvalidInitialStateError("constrained enemy lacks an evaluation snapshot")
            if enemy.blocked_by is None:
                continue
            friendly = friendly_map.get(enemy.blocked_by)
            if friendly is None or enemy.instance_id not in friendly.blocked_enemy_ids:
                raise InvalidInitialStateError("blocking relationship is not symmetric")

    def _apply_action(self, action: PublicAction) -> None:
        if isinstance(action, WaitAction):
            if self.paused:
                raise IllegalActionError("cannot advance logical ticks while paused")
            return
        if isinstance(action, DeployAction):
            self._deploy(action)
            return
        if isinstance(action, RetreatAction):
            self._retreat(action)
            return
        if isinstance(action, ActivateSkillAction):
            self._activate_skill(action)
            return
        if isinstance(action, SelectTargetAction):
            self._select_skill_target(action)
            return
        if isinstance(action, SetPausedAction):
            if action.paused == self.paused:
                raise IllegalActionError("pause state is unchanged")
            self.paused = action.paused
            self._emit(
                EventPhase.ACTION,
                "pause_changed",
                "system",
                {"paused": self.paused},
                priority=self.command_sequence * 100,
                tick=self.tick + 1,
            )
            return
        if isinstance(action, SetSpeedAction):
            if action.speed == self.speed:
                raise IllegalActionError("speed is unchanged")
            self.speed = action.speed
            self._emit(
                EventPhase.ACTION,
                "speed_changed",
                "system",
                {"speed": self.speed},
                priority=self.command_sequence * 100,
                tick=self.tick + 1,
            )
            return
        raise IllegalActionError(f"unsupported public action: {type(action)!r}")

    def _deploy(self, action: DeployAction) -> None:
        definition = self._operator_defs.get(action.operator_id)
        if definition is None:
            raise IllegalActionError(f"unknown operator: {action.operator_id}")
        if action.operator_id not in self.roster:
            raise IllegalActionError("operator is not in the actual roster")
        if any(item.operator_id == action.operator_id for item in self.operators.values()):
            raise IllegalActionError("operator is already deployed")
        if self.roster[action.operator_id].redeploy_remaining:
            raise IllegalActionError("operator is still in redeploy cooldown")
        if definition.deploy_cost > self.cost:
            raise IllegalActionError("insufficient deployment cost")
        if action.position not in self.content.map.deployable_tiles:
            raise IllegalActionError("target tile is not deployable")
        if not self._tile_accepts_operator(action.position, definition):
            raise IllegalActionError("operator deployment class does not match target tile")
        if any(
            item.position == action.position
            for item in (*self.operators.values(), *self.summons.values())
        ):
            raise IllegalActionError("target tile is occupied")

        self.entity_sequence += 1
        instance_id = f"op-{self.entity_sequence:06d}"
        self.cost -= definition.deploy_cost
        roster = self.roster[action.operator_id]
        self.roster[action.operator_id] = roster.model_copy(
            update={"deploy_count": roster.deploy_count + 1}
        )
        self.operators[instance_id] = OperatorState(
            instance_id=instance_id,
            operator_id=action.operator_id,
            position=action.position,
            direction=action.direction,
            hp=definition.max_hp,
            deployment_tick=self.tick,
            skills=tuple(
                SkillState(skill_id=skill.skill_id, sp=skill.initial_sp)
                for skill in definition.skills
            ),
        )
        self._emit(
            EventPhase.ACTION,
            "deploy",
            instance_id,
            {
                "operator_id": action.operator_id,
                "position": action.position.model_dump(mode="json"),
                "direction": action.direction.value,
                "cost": definition.deploy_cost,
            },
            priority=self.command_sequence * 100,
            tick=self.tick + 1,
        )

    def _retreat(self, action: RetreatAction) -> None:
        operator = self.operators.get(action.instance_id)
        if operator is None:
            summon = self.summons.get(action.instance_id)
            if summon is None:
                raise IllegalActionError(f"unknown deployed instance: {action.instance_id}")
            definition = self._summon_defs[summon.summon_id]
            removed_effect_ids = sorted(
                effect_id
                for effect_id, effect in self.active_effects.items()
                if effect.source_instance_id == action.instance_id
                or effect.target_instance_id == action.instance_id
            )
            for effect_id in removed_effect_ids:
                del self.active_effects[effect_id]
                self._emit(
                    EventPhase.ACTION,
                    "effect_removed",
                    effect_id,
                    {"reason": "summon_retreat"},
                    priority=self.command_sequence * 100,
                    tick=self.tick + 1,
                )
            del self.summons[action.instance_id]
            for enemy_id in summon.blocked_enemy_ids:
                enemy = self.enemies.get(enemy_id)
                if enemy is not None:
                    self.enemies[enemy_id] = enemy.model_copy(update={"blocked_by": None})
            self._emit(
                EventPhase.ACTION,
                "summon_retreat",
                action.instance_id,
                {"summon_id": summon.summon_id},
                priority=self.command_sequence * 100 + 1,
                tick=self.tick + 1,
            )
            if definition.blocks_routes:
                self._shorten_routes_after_obstacle_removal(reason="summon_retreat")
            return
        definition = self._operator_defs[operator.operator_id]
        removed_effect_ids = sorted(
            effect_id
            for effect_id, effect in self.active_effects.items()
            if effect.source_instance_id == action.instance_id
            or effect.target_instance_id == action.instance_id
        )
        for effect_id in removed_effect_ids:
            del self.active_effects[effect_id]
            self._emit(
                EventPhase.ACTION,
                "effect_removed",
                effect_id,
                {"reason": "retreat"},
                priority=self.command_sequence * 100,
                tick=self.tick + 1,
            )
        del self.operators[action.instance_id]
        self.cost = min(
            self.effective_max_cost,
            self.cost + definition.retreat_refund_cost,
        )
        roster = self.roster[operator.operator_id]
        self.roster[operator.operator_id] = roster.model_copy(
            update={"redeploy_remaining": definition.redeploy_ticks}
        )
        for enemy_id in operator.blocked_enemy_ids:
            enemy = self.enemies.get(enemy_id)
            if enemy is not None:
                self.enemies[enemy_id] = enemy.model_copy(update={"blocked_by": None})
        self._emit(
            EventPhase.ACTION,
            "retreat",
            action.instance_id,
            {
                "operator_id": operator.operator_id,
                "refund": definition.retreat_refund_cost,
            },
            priority=self.command_sequence * 100 + 1,
            tick=self.tick + 1,
        )

    def _activate_skill(self, action: ActivateSkillAction) -> None:
        instance_id = action.instance_id
        source = self.operators.get(instance_id) or self.summons.get(instance_id)
        if source is None and instance_id.startswith("operator:"):
            operator_id = instance_id.removeprefix("operator:")
            matches = [
                (candidate_id, operator)
                for candidate_id, operator in self.operators.items()
                if operator.operator_id == operator_id
            ]
            if len(matches) == 1:
                instance_id, source = matches[0]
            elif len(matches) > 1:
                raise IllegalActionError(
                    f"multiple deployed instances match operator: {operator_id}"
                )
        if source is None:
            if instance_id.startswith("operator:"):
                operator_id = instance_id.removeprefix("operator:")
                departure = next(
                    (
                        event
                        for event in reversed(self._events)
                        if event.kind in {"death", "retreat"}
                        and event.details.get("operator_id") == operator_id
                    ),
                    None,
                )
                if departure is not None:
                    raise IllegalActionError(
                        f"operator is no longer deployed: {operator_id}; "
                        f"{departure.kind} at tick {departure.tick}"
                    )
            raise IllegalActionError(f"unknown deployed instance: {action.instance_id}")
        states = {state.skill_id: state for state in source.skills}
        state = states.get(action.skill_id)
        if state is None:
            source_definition_id = (
                source.operator_id if isinstance(source, OperatorState) else source.summon_id
            )
            if (
                isinstance(source, OperatorState)
                and action.skill_id in self._operator_defs[source.operator_id].source_skill_ids
            ):
                raise UnsupportedMechanicError(
                    f"source skill is declared but not compiled: "
                    f"{source.operator_id}:{action.skill_id}"
                )
            raise IllegalActionError(
                f"actor {source_definition_id!r} has no skill {action.skill_id!r}"
            )
        definition = self._skill_definition(source, action.skill_id)
        if not self._skill_can_activate(source, state):
            raise IllegalActionError("skill is not ready")
        consumed_sp = definition.sp_cost * definition.consume_charges
        if definition.activation_policy in {
            SkillActivationPolicy.NEXT_ATTACK,
            SkillActivationPolicy.NEXT_HEAL,
        }:
            self._emit(
                EventPhase.ACTION,
                "skill_armed",
                instance_id,
                {
                    "skill_id": action.skill_id,
                    "policy": definition.activation_policy.value,
                    "sp_before": state.sp,
                    "sp_after": state.sp - consumed_sp,
                    "charge_before": state.sp // definition.sp_cost,
                    "charge_after": (state.sp - consumed_sp) // definition.sp_cost,
                },
                priority=self.command_sequence * 100,
                tick=self.tick + 1,
            )
            states[action.skill_id] = state.model_copy(
                update={
                    "sp": state.sp - consumed_sp,
                    "cooldown_remaining": definition.cooldown_ticks,
                    "armed": True,
                    "armed_event_sequence": self.event_sequence,
                }
            )
            updated = source.model_copy(
                update={"skills": tuple(states[key] for key in sorted(states))}
            )
            if isinstance(updated, OperatorState):
                self.operators[instance_id] = updated
            else:
                self.summons[instance_id] = cast(SummonState, updated)
            return
        states[action.skill_id] = state.model_copy(
            update={
                "sp": state.sp - consumed_sp,
                "cooldown_remaining": definition.cooldown_ticks,
                "active_remaining": definition.duration_ticks,
                "activation_pending": True,
            }
        )
        updated = source.model_copy(update={"skills": tuple(states[key] for key in sorted(states))})
        if isinstance(updated, OperatorState):
            self.operators[instance_id] = updated
        else:
            self.summons[instance_id] = cast(SummonState, updated)
        self._emit(
            EventPhase.ACTION,
            "skill_activated",
            instance_id,
            {"skill_id": action.skill_id, "sp_cost": consumed_sp},
            priority=self.command_sequence * 100,
            tick=self.tick + 1,
        )
        self._apply_deployment_point_effects(
            instance_id,
            definition,
            tick=self.tick + 1,
        )

    def _apply_deployment_point_effects(
        self,
        source_instance_id: str,
        definition: SkillDefinition,
        *,
        tick: int,
    ) -> None:
        effects = sorted(
            (
                effect
                for effect in definition.effects
                if isinstance(effect, DeploymentPointsEffectDefinition)
            ),
            key=lambda effect: effect.effect_id,
        )
        for effect in effects:
            before = self.cost
            after = min(self.effective_max_cost, before + effect.delta)
            self.cost = after
            self._emit(
                EventPhase.ACTION,
                "skill_effect",
                source_instance_id,
                {
                    "skill_id": definition.skill_id,
                    "effect_id": effect.effect_id,
                    "targets": ["battle_cost_pool"],
                },
                priority=self.command_sequence * 100 + 1,
                tick=tick,
            )
            self._emit(
                EventPhase.ACTION,
                "deployment_points_changed",
                source_instance_id,
                {
                    "skill_id": definition.skill_id,
                    "effect_id": effect.effect_id,
                    "target": effect.target,
                    "cost_before": before,
                    "cost_after": after,
                    "requested_delta": effect.delta,
                    "applied_delta": after - before,
                    "source_action_sequence": self.command_sequence + 1,
                },
                priority=self.command_sequence * 100 + 1,
                tick=tick,
            )

    def _select_skill_target(self, action: SelectTargetAction) -> None:
        source = self.operators.get(action.instance_id) or self.summons.get(action.instance_id)
        if source is None:
            raise IllegalActionError(f"unknown deployed instance: {action.instance_id}")
        states = {state.skill_id: state for state in source.skills}
        state = states.get(action.skill_id)
        if state is None or not self._skill_has_selected_target(source, action.skill_id):
            raise IllegalActionError("skill does not accept an explicit target")
        if not self._selected_target_is_valid(source, action.skill_id, action.target_instance_id):
            raise IllegalActionError("selected target is not currently legal")
        states[action.skill_id] = state.model_copy(
            update={"selected_target_id": action.target_instance_id}
        )
        updated = source.model_copy(update={"skills": tuple(states[key] for key in sorted(states))})
        if isinstance(updated, OperatorState):
            self.operators[action.instance_id] = updated
        else:
            self.summons[action.instance_id] = cast(SummonState, updated)
        self._emit(
            EventPhase.ACTION,
            "skill_target_selected",
            action.instance_id,
            {
                "skill_id": action.skill_id,
                "target_instance_id": action.target_instance_id,
            },
            priority=self.command_sequence * 100,
            tick=self.tick + 1,
        )

    def _advance_tick(
        self,
        *,
        cost_recovery_multiplier_milli: int = 1000,
        battle_progress_multiplier_milli: int = 1000,
        spawn_progress_multiplier_milli: int = 1000,
    ) -> None:
        self.tick += 1
        self._recover_cost_and_roster(
            cost_recovery_multiplier_milli=cost_recovery_multiplier_milli
        )
        previous_battle_clock_milli = (
            (self.tick - 1) * 1000 - self.battle_progress_slowdown_debt_milli
        )
        self.battle_progress_slowdown_debt_milli += (
            1000 - battle_progress_multiplier_milli
        )
        current_battle_clock_milli = (
            self.tick * 1000 - self.battle_progress_slowdown_debt_milli
        )
        previous_battle_progress_tick = previous_battle_clock_milli // 1000
        self.battle_progress_tick = max(0, current_battle_clock_milli // 1000)
        previous_spawn_clock_milli = (
            (self.tick - 1) * 1000 - self.spawn_progress_slowdown_debt_milli
        )
        self.spawn_progress_slowdown_debt_milli += (
            1000 - spawn_progress_multiplier_milli
        )
        current_spawn_clock_milli = (
            self.tick * 1000 - self.spawn_progress_slowdown_debt_milli
        )
        previous_spawn_progress_tick = previous_spawn_clock_milli // 1000
        self.spawn_progress_tick = max(0, current_spawn_clock_milli // 1000)
        battle_advanced = self.battle_progress_tick > previous_battle_progress_tick
        spawn_advanced = self.spawn_progress_tick > previous_spawn_progress_tick
        if not battle_advanced:
            if spawn_advanced:
                self._spawn_due()
                self._activate_due_tokens()
            self._append_tick_digest()
            return
        self._tick_blood_debt()
        self._tick_enemy_herd_lifecycle()
        self._tick_enemy_talent_controls()
        coefficient_deaths = self._tick_enemy_coefficient_runtime()
        if coefficient_deaths:
            self._resolve_deaths()
        if spawn_advanced:
            self._spawn_due()
            self._activate_due_tokens()
        self._reconcile_blocks()
        projectile_damage = self._advance_projectiles()
        skill_damage, skill_healing = self._process_pending_skill_activations()
        special_tile_damage, special_tile_healing = self._collect_special_tile_effects()
        return_body_damage = self._collect_return_body_effect_damage()
        explosion_damage = self._collect_death_explosion_damage()
        overload_damage = self._collect_overload_punches()
        pulse_damage = self._collect_device_pulse_damage()
        self._pulse_device_cold()
        quill_damage = self._collect_quill_spray_damage()
        predation_damage = self._collect_predation_damage()
        guided_damage = self._collect_guided_strike_damage()
        backstab_damage = self._collect_backstab_damage()
        volley_damage = self._collect_four_direction_volley_damage()
        bomb_damage = self._collect_ice_bomb_damage()
        blink_damage = self._collect_blink_damage()
        chain_buff_damage = self._collect_chain_buff_damage()
        doom_damage = self._collect_doom_damage()
        immolation_damage = self._collect_immolation_damage()
        flame_damage = self._collect_flame_damage()
        crossroads_damage = self._collect_crossroads_damage()
        bomb_run_damage = self._collect_bomb_run_damage()
        damage_intents = (
            projectile_damage
            + skill_damage
            + special_tile_damage
            + return_body_damage
            + explosion_damage
            + overload_damage
            + pulse_damage
            + quill_damage
            + predation_damage
            + guided_damage
            + backstab_damage
            + volley_damage
            + bomb_damage
            + blink_damage
            + chain_buff_damage
            + doom_damage
            + immolation_damage
            + flame_damage
            + crossroads_damage
            + bomb_run_damage
            + self._collect_attacks()
        )
        heal_intents = (
            skill_healing
            + special_tile_healing
            + self._collect_operator_healing()
            + self._collect_summon_healing()
            + self._collect_device_healing()
            + self._collect_repair_healing()
        )
        self._apply_damage(damage_intents)
        self._apply_healing(heal_intents)
        self._resolve_deaths()
        self._move_enemies()
        self._tick_effects_and_skills()
        self._resolve_terminal()
        self._append_tick_digest()

    @staticmethod
    def _fast_copy(model: _ModelT, update: dict[str, Any]) -> _ModelT:
        """`model.model_copy(update=...)` without pydantic's per-call overhead.

        Same result, measured field-by-field: identical `__dict__`,
        `__pydantic_fields_set__`, `__pydantic_extra__` and `__eq__` on the
        engine's real state models (MODEL-COPY-MICROBENCH.json). 1.7x on the
        copy itself; `model_copy` spends most of its time in three `copy.copy`
        calls that shallow-copy containers this engine then replaces wholesale.

        No validation is lost: pydantic's `model_copy(update=...)` does not
        validate the update values either, so this is not trading safety for
        speed. What it does cost is coupling to pydantic's instance layout --
        if that layout ever changes, this breaks loudly rather than silently,
        and the tournament_sim suite covers it.

        `model_construct` was measured too and is 3.7x SLOWER than
        `model_copy`; the obvious shortcut is the wrong one.

        Two guards, because skipping the library means nothing else will catch
        a mistake here:
          * every update key must be a real field of the model, so a typo
            cannot silently graft an unknown attribute onto battle state;
          * setting ARKNIGHTS_SIM_FAST_COPY_AUDIT=1 routes every call back
            through `model_copy`, so a run can be repeated on the fully
            library-checked path and compared.
        """
        cls = type(model)
        if _FAST_COPY_AUDIT:
            return model.model_copy(update=update)
        # The field set is cached per class deliberately. In pydantic 2.12
        # `cls.model_fields` is a property, and touching it per call costs
        # 0.316 us -- more than half of what this whole fast path saves, which
        # dropped the in-situ gain from 1.72x to 1.045x. Caching restores it.
        fields = _FAST_COPY_FIELDS.get(cls)
        if fields is None:
            fields = _FAST_COPY_FIELDS[cls] = frozenset(cls.model_fields)
        if not update.keys() <= fields:
            raise ValueError(
                f"_fast_copy got fields absent from {cls.__name__}: "
                f"{sorted(update.keys() - fields)}"
            )
        clone = object.__new__(cls)
        object.__setattr__(clone, "__dict__", {**model.__dict__, **update})
        object.__setattr__(
            clone, "__pydantic_fields_set__", model.__pydantic_fields_set__ | update.keys()
        )
        object.__setattr__(clone, "__pydantic_extra__", model.__pydantic_extra__)
        object.__setattr__(clone, "__pydantic_private__", model.__pydantic_private__)
        return clone

    def _append_tick_digest(self) -> None:
        """Commit the complete deterministic state after one logical battle tick."""

        # Building a digest means model_dump-ing the whole battle state and
        # hashing it twice, every tick. Profiling the time-prefix DFS put this
        # subtree at 44.08% of `step` (STEP-HOTSPOT-TABLE.md), and when the
        # retention policy keeps none of them, `_retain_history_row` discards
        # every one immediately after it is built. Skip the construction rather
        # than build-then-drop.
        #
        # Deliberately gated on max_tick_digests == 0, NOT on mode == "bounded":
        # with a non-zero limit the retained digests' `previous_digest_hash`
        # depends on the whole chain, so the chain still has to be computed.
        # An observer also consumes every digest, so it disables the skip.
        #
        # Skipping leaves `tick_digest_count` at 0 and `last_tick_digest_hash`
        # at None, which those two fields' invariant already allows and which
        # `_make_snapshot` therefore carries into the snapshot hash. Callers
        # that need digests or a replay export must use complete retention.
        if (
            not _NO_FAST_PATHS
            and self._tick_digest_observer is None
            and self._replay_retention.max_tick_digests == 0
        ):
            # Count it even though it is not built: `replay_retention_state`
            # adds this in, so a bounded run still reports a truthful
            # total_tick_digests and cannot be mistaken for a complete history.
            # `tick_digest_count` itself must NOT move -- it is paired with
            # `last_tick_digest_hash` by an InitialState invariant, and there is
            # no hash to pair it with here.
            self._skipped_tick_digest_count += 1
            return

        state_body = {
            "engine_version": ENGINE_VERSION,
            "content_hash": self.content.content_hash,
            "tick": self.tick,
            "cost": self.cost,
            "max_cost_bonus": self.max_cost_bonus,
            "initial_deployment_points_bonus": self.initial_deployment_points_bonus,
            "cost_recovery_slowdown_debt_milli": (
                self.cost_recovery_slowdown_debt_milli
            ),
            "battle_progress_tick": self.battle_progress_tick,
            "battle_progress_slowdown_debt_milli": (
                self.battle_progress_slowdown_debt_milli
            ),
            "spawn_progress_tick": self.spawn_progress_tick,
            "spawn_progress_slowdown_debt_milli": (
                self.spawn_progress_slowdown_debt_milli
            ),
            "life_points": self.life_points,
            "kills": self.kills,
            "leaks": self.leaks,
            "status": self.status,
            "spawned_spawn_ids": tuple(sorted(self.spawned_spawn_ids)),
            "activated_token_aliases": tuple(sorted(self.activated_token_aliases)),
            "pending_spawn_ids": tuple(
                item.occurrence_id
                for item in self._scheduled
                if item.occurrence_id not in self.spawned_spawn_ids
            ),
            "operators": [
                item.model_dump(mode="json")
                for item in sorted(self.operators.values(), key=lambda item: item.instance_id)
            ],
            "enemies": [
                item.model_dump(mode="json")
                for item in sorted(self.enemies.values(), key=lambda item: item.instance_id)
            ],
            "summons": [
                item.model_dump(mode="json")
                for item in sorted(self.summons.values(), key=lambda item: item.instance_id)
            ],
            "projectiles": [
                item.model_dump(mode="json")
                for item in sorted(self.projectiles.values(), key=lambda item: item.instance_id)
            ],
            "healing_devices": [
                item.model_dump(mode="json")
                for item in sorted(self.healing_devices.values(), key=lambda item: item.instance_id)
            ],
            "polluted_zones": [
                item.model_dump(mode="json")
                for item in sorted(self.polluted_zones.values(), key=lambda item: item.instance_id)
            ],
            "pending_explosions": [
                item.model_dump(mode="json")
                for item in sorted(
                    self.pending_explosions.values(), key=lambda item: item.instance_id
                )
            ],
            "active_effects": [
                item.model_dump(mode="json")
                for item in sorted(self.active_effects.values(), key=lambda item: item.instance_id)
            ],
            "roster": [
                item.model_dump(mode="json")
                for item in sorted(self.roster.values(), key=lambda item: item.operator_id)
            ],
            "entity_sequence": self.entity_sequence,
            "effect_sequence": self.effect_sequence,
            "event_sequence": self.event_sequence,
            "command_sequence": self.command_sequence,
            "rng_seed": self.rng_seed,
            "rng_streams": tuple(
                RNGStreamState(stream=name, draws=draws).model_dump(mode="json")
                for name, draws in sorted(self.rng_streams.items())
            ),
            "paused": self.paused,
            "speed": self.speed,
            "global_modifiers": self.global_modifiers.model_dump(mode="json"),
        }
        self.tick_digest_count += 1
        body = {
            "schema_id": TICK_DIGEST_SCHEMA,
            "digest_index": self.tick_digest_count,
            "tick": self.tick,
            "state_hash": canonical_sha256(state_body),
            "previous_digest_hash": self.last_tick_digest_hash,
        }
        digest = TickStateDigest(**body, digest_hash=canonical_sha256(body))
        if self._tick_digest_observer is not None:
            self._tick_digest_observer(digest)
        self._retain_history_row(
            self._tick_digests,
            digest,
            self._replay_retention.max_tick_digests,
        )
        self._max_resident_tick_digests = max(
            self._max_resident_tick_digests,
            len(self._tick_digests),
        )
        self.last_tick_digest_hash = digest.digest_hash

    def _skill_definition(
        self,
        source: OperatorState | SummonState,
        skill_id: str,
    ) -> SkillDefinition:
        if isinstance(source, OperatorState):
            return self._skill_defs[(source.operator_id, skill_id)]
        return self._summon_skill_defs[(source.summon_id, skill_id)]

    def _skill_is_ready(
        self,
        source: OperatorState | SummonState,
        state: SkillState,
    ) -> bool:
        definition = self._skill_definition(source, state.skill_id)
        return (
            state.sp >= definition.sp_cost * definition.consume_charges
            and state.cooldown_remaining == 0
            and state.active_remaining == 0
            and not state.activation_pending
            and not state.armed
        )

    def _skill_can_activate(
        self,
        source: OperatorState | SummonState,
        state: SkillState,
    ) -> bool:
        definition = self._skill_definition(source, state.skill_id)
        if definition.activation_policy in {
            SkillActivationPolicy.AUTOMATIC,
            SkillActivationPolicy.AUTO_NEXT_ATTACK,
        }:
            return False
        if not self._skill_is_ready(source, state) or self._has_control(
            source.instance_id, ControlEffect.STUN, ControlEffect.SILENCE
        ):
            return False
        if not self._skill_has_selected_target(source, state.skill_id):
            return True
        return bool(
            state.selected_target_id
            and self._selected_target_is_valid(
                source,
                state.skill_id,
                state.selected_target_id,
            )
        )

    def _skill_has_selected_target(
        self,
        source: OperatorState | SummonState,
        skill_id: str,
    ) -> bool:
        definition = self._skill_definition(source, skill_id)
        return any(
            getattr(effect, "target", None) is EffectTargetSelector.SELECTED_ENEMY
            for effect in definition.effects
        )

    def _queue_sp_event(
        self,
        source_instance_id: str,
        event: SkillSpEvent,
        *,
        trigger_event_sequence: int,
        event_target_id: str | None = None,
    ) -> None:
        source = self.operators.get(source_instance_id) or self.summons.get(source_instance_id)
        if source is None:
            return
        recovery_mode = {
            SkillSpEvent.NORMAL_ATTACK_COMMITTED: SkillSpRecoveryMode.ATTACK,
            SkillSpEvent.DAMAGE_RECEIVED: SkillSpRecoveryMode.DAMAGED,
        }.get(event)
        if recovery_mode is not None:
            for state in sorted(source.skills, key=lambda item: item.skill_id):
                definition = self._skill_definition(source, state.skill_id)
                if definition.sp_recovery_mode is recovery_mode:
                    self._pending_sp_grants.append(
                        _PendingSpGrant(
                            source_instance_id=source_instance_id,
                            target_instance_id=source_instance_id,
                            target_skill_id=state.skill_id,
                            amount=definition.sp_per_recovery_event,
                            effect_id=f"recovery:{state.skill_id}",
                            trigger_event_sequence=trigger_event_sequence,
                        )
                    )
        for skill in sorted(
            (self._skill_definition(source, state.skill_id) for state in source.skills),
            key=lambda item: item.skill_id,
        ):
            for effect in sorted(skill.effects, key=lambda item: item.effect_id):
                if not isinstance(effect, SpMutationEffectDefinition) or effect.event is not event:
                    continue
                target_id = source_instance_id if effect.target == "self" else event_target_id
                if target_id is None:
                    raise EffectVMError("event_target SP mutation has no committed ally")
                self._pending_sp_grants.append(
                    _PendingSpGrant(
                        source_instance_id=source_instance_id,
                        target_instance_id=target_id,
                        target_skill_id=effect.target_skill_id,
                        amount=effect.amount,
                        effect_id=effect.effect_id,
                        trigger_event_sequence=trigger_event_sequence,
                        survives_source_departure=True,
                    )
                )

    def _write_actor_skill_state(
        self,
        instance_id: str,
        source: OperatorState | SummonState,
        states: dict[str, SkillState],
    ) -> OperatorState | SummonState:
        updated = source.model_copy(update={"skills": tuple(states[key] for key in sorted(states))})
        if isinstance(updated, OperatorState):
            self.operators[instance_id] = updated
        else:
            self.summons[instance_id] = cast(SummonState, updated)
        return updated

    def _queue_automatic_activation(
        self,
        instance_id: str,
        source: OperatorState | SummonState,
        state: SkillState,
        definition: SkillDefinition,
    ) -> None:
        if definition.activation_policy not in {
            SkillActivationPolicy.AUTOMATIC,
            SkillActivationPolicy.AUTO_NEXT_ATTACK,
        }:
            return
        if not self._skill_is_ready(source, state) or self._has_control(
            instance_id, ControlEffect.STUN, ControlEffect.SILENCE
        ):
            return
        consumed_sp = definition.sp_cost * definition.consume_charges
        states = {item.skill_id: item for item in source.skills}
        if definition.activation_policy is SkillActivationPolicy.AUTO_NEXT_ATTACK:
            update = {
                "sp": state.sp - consumed_sp,
                "cooldown_remaining": definition.cooldown_ticks,
                "armed": True,
                "armed_event_sequence": self.event_sequence,
            }
            event_kind = "skill_auto_armed"
        else:
            update = {
                "sp": state.sp - consumed_sp,
                "cooldown_remaining": definition.cooldown_ticks,
                "active_remaining": definition.duration_ticks,
                "activation_pending": True,
            }
            event_kind = "skill_auto_activated"
        states[state.skill_id] = state.model_copy(update=update)
        self._write_actor_skill_state(instance_id, source, states)
        self._emit(
            EventPhase.TERMINAL,
            event_kind,
            instance_id,
            {
                "skill_id": state.skill_id,
                "sp_before": state.sp,
                "sp_after": state.sp - consumed_sp,
                "charge_before": state.sp // definition.sp_cost,
                "charge_after": (state.sp - consumed_sp) // definition.sp_cost,
            },
            priority=3,
        )

    def _queue_auto_next_heal(
        self,
        instance_id: str,
        operator: OperatorState,
    ) -> bool:
        """Commit one source-timed charged heal in place of a normal attack."""

        if self._has_control(instance_id, ControlEffect.SILENCE):
            return False
        for state in sorted(operator.skills, key=lambda item: item.skill_id):
            definition = self._skill_definition(operator, state.skill_id)
            if definition.activation_policy is not SkillActivationPolicy.AUTO_NEXT_HEAL:
                continue
            if not self._skill_is_ready(operator, state):
                continue
            heal_effects = tuple(
                effect
                for effect in definition.effects
                if isinstance(effect, (HealEffectDefinition, ScaledHealEffectDefinition))
            )
            if len(heal_effects) != 1:
                raise EffectVMError("auto_next_heal requires exactly one heal payload")
            effect = heal_effects[0]
            geometry_cells = self._effect_geometry_cells(operator, effect)
            targets = self._select_effect_targets(
                operator,
                effect.target,
                effect.range_tiles,
                exact_cells=(
                    frozenset((cell.col, cell.row) for cell in geometry_cells)
                    if geometry_cells is not None
                    else None
                ),
            )
            if not targets:
                continue
            target = self.operators.get(targets[0]) or self.summons.get(targets[0])
            threshold = definition.activation_hp_ratio_max_milli
            if target is None or threshold is None:
                raise EffectVMError("auto_next_heal target or threshold disappeared")
            if target.hp * 1000 >= self._max_hp(target) * threshold:
                continue
            consumed_sp = definition.sp_cost * definition.consume_charges
            states = {item.skill_id: item for item in operator.skills}
            states[state.skill_id] = state.model_copy(
                update={
                    "sp": state.sp - consumed_sp,
                    "cooldown_remaining": definition.cooldown_ticks,
                    "active_remaining": definition.duration_ticks,
                    "activation_pending": True,
                    "selected_target_id": target.instance_id,
                }
            )
            self.operators[instance_id] = operator.model_copy(
                update={
                    "attack_cooldown": self._operator_effective_stat(
                        operator, EffectStat.ATTACK_INTERVAL_TICKS
                    )
                    - 1,
                    "skills": tuple(states[key] for key in sorted(states)),
                }
            )
            self._emit(
                EventPhase.ATTACK,
                "skill_auto_heal_queued",
                instance_id,
                {
                    "skill_id": state.skill_id,
                    "target_id": target.instance_id,
                    "sp_before": state.sp,
                    "sp_after": state.sp - consumed_sp,
                    "charge_before": state.sp // definition.sp_cost,
                    "charge_after": (state.sp - consumed_sp) // definition.sp_cost,
                    "target_hp": target.hp,
                    "target_max_hp": self._max_hp(target),
                    "hp_ratio_exclusive_max_milli": threshold,
                },
                priority=9,
            )
            return True
        return False

    def _flush_pending_sp_grants(self) -> None:
        pending = self._pending_sp_grants
        self._pending_sp_grants = []
        grouped: dict[tuple[int, str, str], list[_PendingSpGrant]] = defaultdict(list)
        for grant in pending:
            if (
                not grant.survives_source_departure
                and grant.source_instance_id not in self.operators
                and grant.source_instance_id not in self.summons
            ):
                continue
            grouped[
                (
                    grant.trigger_event_sequence,
                    grant.target_instance_id,
                    grant.target_skill_id,
                )
            ].append(grant)
        for trigger_sequence, target_id, skill_id in sorted(grouped):
            source = self.operators.get(target_id) or self.summons.get(target_id)
            if source is None:
                continue
            states = {state.skill_id: state for state in source.skills}
            requested_skill_id = skill_id
            if skill_id == "*":
                if len(states) != 1:
                    raise EffectVMError(
                        "wildcard SP mutation target must expose exactly one selected skill: "
                        f"{target_id}/{sorted(states)}"
                    )
                skill_id = next(iter(states))
            state = states.get(skill_id)
            if state is None:
                raise EffectVMError(
                    f"SP mutation target has no declared skill: {target_id}/{skill_id}"
                )
            definition = self._skill_definition(source, skill_id)
            grants = sorted(
                grouped[(trigger_sequence, target_id, requested_skill_id)],
                key=lambda item: item.effect_id,
            )
            before = state.sp
            requested = sum(item.amount for item in grants)
            after = min(
                definition.sp_cost * definition.max_charges,
                max(0, before + requested),
            )
            was_ready = self._skill_is_ready(source, state)
            next_state = state.model_copy(update={"sp": after})
            states[skill_id] = next_state
            updated = self._write_actor_skill_state(target_id, source, states)
            self._emit(
                EventPhase.TERMINAL,
                "sp_changed",
                target_id,
                {
                    "source_instance_ids": sorted({item.source_instance_id for item in grants}),
                    "target_skill_id": skill_id,
                    "requested_target_skill_id": requested_skill_id,
                    "effect_ids": [item.effect_id for item in grants],
                    "requested_delta": requested,
                    "applied_delta": after - before,
                    "sp_before": before,
                    "sp_after": after,
                    "charge_before": before // definition.sp_cost,
                    "charge_after": after // definition.sp_cost,
                    "trigger_event_sequence": trigger_sequence,
                },
            )
            now_ready = self._skill_is_ready(updated, next_state)
            if now_ready and not was_ready:
                self._emit(
                    EventPhase.TERMINAL,
                    "skill_ready",
                    target_id,
                    {"skill_id": skill_id},
                    priority=2,
                )
            refreshed = self.operators.get(target_id) or self.summons.get(target_id)
            if refreshed is not None:
                refreshed_state = next(
                    item for item in refreshed.skills if item.skill_id == skill_id
                )
                self._queue_automatic_activation(target_id, refreshed, refreshed_state, definition)

    def _selected_target_is_valid(
        self,
        source: OperatorState | SummonState,
        skill_id: str,
        target_instance_id: str,
    ) -> bool:
        enemy = self.enemies.get(target_instance_id)
        if enemy is None or not self._friendly_can_target_enemy(source, enemy):
            return False
        definition = self._skill_definition(source, skill_id)
        selected_effects = [
            effect
            for effect in definition.effects
            if getattr(effect, "target", None) is EffectTargetSelector.SELECTED_ENEMY
        ]
        return bool(selected_effects) and all(
            self._distance_milli(source.position, enemy) <= effect.range_tiles * 1000
            for effect in selected_effects
        )

    def _selected_target_is_valid_for_state(
        self,
        source: OperatorState | SummonState,
        skill_id: str,
        enemy: EnemyState,
    ) -> bool:
        if not self._friendly_can_target_enemy(source, enemy):
            return False
        definition = self._skill_definition(source, skill_id)
        selected_effects = [
            effect
            for effect in definition.effects
            if getattr(effect, "target", None) is EffectTargetSelector.SELECTED_ENEMY
        ]
        return bool(selected_effects) and all(
            self._distance_milli(source.position, enemy) <= effect.range_tiles * 1000
            for effect in selected_effects
        )

    def _process_pending_skill_activations(
        self,
        *,
        activation_keys: frozenset[tuple[str, str]] | None = None,
    ) -> tuple[tuple[_DamageIntent, ...], tuple[_HealIntent, ...]]:
        damage_intents: list[_DamageIntent] = []
        heal_intents: list[_HealIntent] = []
        sources: list[tuple[str, OperatorState | SummonState]] = sorted(
            [*self.operators.items(), *self.summons.items()],
            key=lambda item: item[0],
        )
        for instance_id, source in sources:
            states = {state.skill_id: state for state in source.skills}
            for skill_id in sorted(states):
                state = states[skill_id]
                if not state.activation_pending:
                    continue
                if (
                    activation_keys is not None
                    and (instance_id, skill_id) not in activation_keys
                ):
                    continue
                definition = self._skill_definition(source, skill_id)
                for effect in definition.effects:
                    if isinstance(effect, AttackScaleEffectDefinition):
                        # Applied synchronously to the qualifying normal attack.
                        continue
                    if isinstance(effect, SpMutationEffectDefinition):
                        # Event-bound passive instruction; it is never an
                        # activation payload and is handled after its packet.
                        continue
                    if isinstance(effect, DeploymentPointsEffectDefinition):
                        # Applied synchronously in the source ACTION after SP
                        # consumption so a later same-tick deploy can use it.
                        continue
                    if isinstance(effect, SummonEffectDefinition):
                        plan = self._plan_summon(instance_id, effect)
                        targets: tuple[str, ...] = ()
                        if plan is not None:
                            self.summons[plan.state.instance_id] = plan.state
                            for enemy in plan.enemy_updates:
                                self.enemies[enemy.instance_id] = enemy
                            targets = (plan.state.instance_id,)
                            self._emit(
                                EventPhase.SPAWN,
                                "summon",
                                plan.state.instance_id,
                                {
                                    "summon_id": plan.state.summon_id,
                                    "position": plan.state.position.model_dump(mode="json"),
                                    "source_instance_id": instance_id,
                                },
                            )
                        else:
                            self._emit(
                                EventPhase.SPAWN,
                                "summon_fizzled",
                                instance_id,
                                {"effect_id": effect.effect_id},
                            )
                        self._emit(
                            EventPhase.ATTACK,
                            "skill_effect",
                            instance_id,
                            {
                                "skill_id": skill_id,
                                "effect_id": effect.effect_id,
                                "targets": list(targets),
                            },
                        )
                        continue
                    geometry_cells = self._effect_geometry_cells(source, effect)
                    if geometry_cells == () and effect.target in {
                        EffectTargetSelector.ALL_ALLIES_IN_RANGE,
                        EffectTargetSelector.ALL_ENEMIES_IN_RANGE,
                    }:
                        raise EffectVMError(
                            "all-target discrete geometry produced an empty in-map cell set"
                        )
                    targets = self._select_effect_targets(
                        source,
                        effect.target,
                        effect.range_tiles,
                        selected_target_id=state.selected_target_id,
                        exact_cells=(
                            frozenset((cell.col, cell.row) for cell in geometry_cells)
                            if geometry_cells is not None
                            else None
                        ),
                    )
                    if geometry_cells is not None:
                        self._emit(
                            EventPhase.ATTACK,
                            "effect_geometry_snapshotted",
                            instance_id,
                            {
                                "skill_id": skill_id,
                                "effect_id": effect.effect_id,
                                "source_position": source.position.model_dump(mode="json"),
                                "source_direction": (
                                    source.direction.value
                                    if isinstance(source, OperatorState)
                                    else None
                                ),
                                "range_rotation": effect.range_rotation.value,
                                "range_source_sha256": effect.range_source_sha256,
                                "cells": [cell.model_dump(mode="json") for cell in geometry_cells],
                                "targets": list(targets),
                            },
                        )
                    self._emit(
                        EventPhase.ATTACK,
                        "skill_effect",
                        instance_id,
                        {
                            "skill_id": skill_id,
                            "effect_id": effect.effect_id,
                            "targets": list(targets),
                        },
                    )
                    if isinstance(effect, HealEffectDefinition):
                        heal_intents.extend(
                            _HealIntent(instance_id, target_id, effect.amount)
                            for target_id in targets
                        )
                    elif isinstance(effect, ScaledHealEffectDefinition):
                        source_amount = (
                            self._operator_effective_stat(source, effect.source_stat)
                            if isinstance(source, OperatorState)
                            else self._summon_effective_stat(source, effect.source_stat)
                        )
                        amount = self._scale_fixed(source_amount, effect.scale_milli)
                        heal_intents.extend(
                            _HealIntent(instance_id, target_id, amount)
                            for target_id in targets
                        )
                    elif isinstance(effect, DamageEffectDefinition):
                        for target_id in targets:
                            enemy = self.enemies.get(target_id)
                            if enemy is None:
                                continue
                            amount = effect.amount
                            if effect.damage_type == "physical":
                                amount = _physical_damage(
                                    effect.amount,
                                    self._enemy_effective_stat(enemy, EffectStat.DEFENSE),
                                )
                            amount = self._scale_fixed(
                                amount,
                                self.global_modifiers.friendly_damage_multiplier_milli,
                            )
                            if amount:
                                damage_intents.append(
                                    _DamageIntent(instance_id, target_id, "enemy", amount)
                                )
                    elif isinstance(effect, RandomDamageEffectDefinition):
                        for target_id in targets:
                            enemy = self.enemies.get(target_id)
                            if enemy is None:
                                continue
                            amount = self._draw_rng(
                                effect.rng_stream, effect.minimum, effect.maximum
                            )
                            self._emit(
                                EventPhase.ATTACK,
                                "rng_draw",
                                instance_id,
                                {
                                    "effect_id": effect.effect_id,
                                    "stream": effect.rng_stream,
                                    "draw": self.rng_streams[effect.rng_stream],
                                    "value": amount,
                                    "target": target_id,
                                },
                            )
                            if effect.damage_type == "physical":
                                amount = _physical_damage(
                                    amount,
                                    self._enemy_effective_stat(enemy, EffectStat.DEFENSE),
                                )
                            amount = self._scale_fixed(
                                amount,
                                self.global_modifiers.friendly_damage_multiplier_milli,
                            )
                            if amount:
                                damage_intents.append(
                                    _DamageIntent(instance_id, target_id, "enemy", amount)
                                )
                    elif isinstance(effect, StatModifierEffectDefinition):
                        for target_id in targets:
                            self.effect_sequence += 1
                            effect_instance_id = f"effect-{self.effect_sequence:06d}"
                            self.active_effects[effect_instance_id] = ActiveEffectState(
                                instance_id=effect_instance_id,
                                effect_id=effect.effect_id,
                                source_instance_id=instance_id,
                                source_skill_id=skill_id,
                                target_instance_id=target_id,
                                remaining_ticks=effect.duration_ticks,
                                geometry_source_position=(
                                    source.position if geometry_cells is not None else None
                                ),
                                geometry_source_direction=(
                                    source.direction
                                    if geometry_cells is not None
                                    and isinstance(source, OperatorState)
                                    else None
                                ),
                                geometry_cells=geometry_cells or (),
                                geometry_target_ids=(targets if geometry_cells is not None else ()),
                            )
                    else:  # pragma: no cover - the finite union is checked by EffectVM
                        raise EffectVMError(
                            f"unsupported effect instruction: {type(effect).__name__}"
                        )
                states[skill_id] = self._fast_copy(
                    state, {"activation_pending": False, "selected_target_id": None}
                )
            updated = self._fast_copy(
                source, {"skills": tuple(states[key] for key in sorted(states))}
            )
            if isinstance(updated, OperatorState):
                self.operators[instance_id] = updated
            elif instance_id in self.summons:
                self.summons[instance_id] = cast(SummonState, updated)
        return tuple(damage_intents), tuple(heal_intents)

    def _consume_armed_payloads(
        self,
        source_instance_id: str,
        policy: SkillActivationPolicy,
        *,
        trigger_event_sequence: int,
        event_target_id: str,
    ) -> tuple[tuple[_DamageIntent, ...], tuple[_HealIntent, ...]]:
        source = self.operators.get(source_instance_id) or self.summons.get(source_instance_id)
        if source is None:
            return (), ()
        states = {state.skill_id: state for state in source.skills}
        consumed: list[str] = []
        for skill_id in sorted(states):
            state = states[skill_id]
            definition = self._skill_definition(source, skill_id)
            policy_matches = definition.activation_policy is policy or (
                policy is SkillActivationPolicy.NEXT_ATTACK
                and definition.activation_policy is SkillActivationPolicy.AUTO_NEXT_ATTACK
            )
            if not state.armed or not policy_matches:
                continue
            states[skill_id] = state.model_copy(
                update={
                    "armed": False,
                    "armed_event_sequence": None,
                    "activation_pending": True,
                    "active_remaining": definition.duration_ticks,
                }
            )
            consumed.append(skill_id)
            self._emit(
                EventPhase.ATTACK,
                "skill_payload_consumed",
                source_instance_id,
                {
                    "skill_id": skill_id,
                    "policy": policy.value,
                    "armed_event_sequence": state.armed_event_sequence,
                    "trigger_event_sequence": trigger_event_sequence,
                    "event_target_id": event_target_id,
                },
                priority=15,
            )
        if not consumed:
            return (), ()
        updated = source.model_copy(update={"skills": tuple(states[key] for key in sorted(states))})
        if isinstance(updated, OperatorState):
            self.operators[source_instance_id] = updated
        else:
            self.summons[source_instance_id] = cast(SummonState, updated)
        return self._process_pending_skill_activations(
            activation_keys=frozenset(
                (source_instance_id, skill_id) for skill_id in consumed
            )
        )

    def _effect_geometry_cells(
        self,
        source: OperatorState | SummonState,
        effect: object,
    ) -> tuple[GridPoint, ...] | None:
        offsets = cast(
            tuple[tuple[int, int], ...],
            getattr(effect, "range_offsets", ()),
        )
        if not offsets:
            return None
        rotation = cast(
            EffectRangeRotation,
            getattr(effect, "range_rotation", EffectRangeRotation.NONE),
        )
        direction = source.direction if isinstance(source, OperatorState) else Direction.RIGHT
        cells: set[tuple[int, int]] = set()
        for delta_col, delta_row in offsets:
            if rotation is EffectRangeRotation.SOURCE_FACING:
                if direction is Direction.DOWN:
                    delta_col, delta_row = -delta_row, delta_col
                elif direction is Direction.LEFT:
                    delta_col, delta_row = -delta_col, -delta_row
                elif direction is Direction.UP:
                    delta_col, delta_row = delta_row, -delta_col
            col = source.position.col + delta_col
            row = source.position.row + delta_row
            if 0 <= col < self.content.map.width and 0 <= row < self.content.map.height:
                cells.add((col, row))
        return tuple(
            GridPoint(col=col, row=row)
            for col, row in sorted(cells, key=lambda item: (item[1], item[0]))
        )

    def _select_effect_targets(
        self,
        source: OperatorState | SummonState,
        selector: EffectTargetSelector,
        range_tiles: int,
        *,
        selected_target_id: str | None = None,
        exact_cells: frozenset[tuple[int, int]] | None = None,
    ) -> tuple[str, ...]:
        if selector is EffectTargetSelector.SELF:
            if (
                exact_cells is not None
                and (
                    source.position.col,
                    source.position.row,
                )
                not in exact_cells
            ):
                return ()
            return (source.instance_id,)
        if selector is EffectTargetSelector.BLOCKED_ENEMIES:
            return tuple(
                enemy_id
                for enemy_id in sorted(source.blocked_enemy_ids)
                if enemy_id in self.enemies
                and (
                    exact_cells is None
                    or (
                        (point := self._enemy_grid_point(self.enemies[enemy_id])) is not None
                        and (point.col, point.row) in exact_cells
                    )
                )
            )
        if selector is EffectTargetSelector.ALL_ENEMIES_IN_RANGE:
            return tuple(
                enemy.instance_id
                for enemy in sorted(self.enemies.values(), key=lambda item: item.instance_id)
                if (
                    (
                        exact_cells is not None
                        and (point := self._enemy_grid_point(enemy)) is not None
                        and (point.col, point.row) in exact_cells
                    )
                    or (
                        exact_cells is None
                        and self._distance_milli(source.position, enemy) <= range_tiles * 1000
                    )
                )
                and self._friendly_can_target_enemy(source, enemy)
            )
        if selector is EffectTargetSelector.SELECTED_ENEMY:
            if selected_target_id is None:
                return ()
            enemy = self.enemies.get(selected_target_id)
            if (
                enemy is None
                or (
                    exact_cells is not None
                    and (
                        (point := self._enemy_grid_point(enemy)) is None
                        or (point.col, point.row) not in exact_cells
                    )
                )
                or (
                    exact_cells is None
                    and self._distance_milli(source.position, enemy) > range_tiles * 1000
                )
                or not self._friendly_can_target_enemy(source, enemy)
            ):
                return ()
            return (selected_target_id,)
        allies: list[OperatorState | SummonState] = [
            friendly
            for friendly in (*self.operators.values(), *self.summons.values())
            if (
                (
                    exact_cells is not None
                    and (friendly.position.col, friendly.position.row) in exact_cells
                )
                or (
                    exact_cells is None and self._friendly_distance(source, friendly) <= range_tiles
                )
            )
        ]
        if selector is EffectTargetSelector.ALL_ALLIES_IN_RANGE:
            return tuple(sorted(operator.instance_id for operator in allies))
        if selector is EffectTargetSelector.LOWEST_HP_ALLY_IN_RANGE:
            if selected_target_id is not None:
                selected = next(
                    (
                        friendly
                        for friendly in allies
                        if friendly.instance_id == selected_target_id
                    ),
                    None,
                )
                return () if selected is None else (selected.instance_id,)
            hurt = [friendly for friendly in allies if friendly.hp < self._max_hp(friendly)]
            if not hurt:
                return ()
            target = min(
                hurt,
                key=lambda item: (
                    item.hp * 1_000_000 // self._max_hp(item),
                    item.hp,
                    item.instance_id,
                ),
            )
            return (target.instance_id,)
        raise EffectVMError(f"unsupported target selector: {selector}")

    def _plan_summon(
        self,
        source_instance_id: str,
        effect: SummonEffectDefinition,
    ) -> _SummonPlan | None:
        definition = self._summon_defs[effect.summon_id]
        if any(
            item.position == effect.position
            for item in (*self.operators.values(), *self.summons.values())
        ):
            return None
        enemy_updates: tuple[EnemyState, ...] = ()
        if definition.blocks_routes:
            obstacles = self._route_obstacle_keys() | {(effect.position.col, effect.position.row)}
            updates: list[EnemyState] = []
            for enemy in sorted(self.enemies.values(), key=lambda item: item.instance_id):
                if self._enemy_defs[enemy.enemy_id].movement_layer is MovementLayer.FLYING:
                    continue
                replanned = self._repath_enemy(enemy, obstacles)
                if replanned is None:
                    return None
                updates.append(replanned)
            for scheduled in self._scheduled:
                if scheduled.occurrence_id in self.spawned_spawn_ids:
                    continue
                route = self._routes[scheduled.route_id]
                if route.movement_layer is MovementLayer.FLYING:
                    continue
                runtime_path = self._route_runtime_points[route.route_id]
                if self._route_teleports[route.route_id] and any(
                    (point.col, point.row) in obstacles for point in runtime_path[1:-1]
                ):
                    return None
                if not self._route_teleports[route.route_id] and self._path_around(
                    runtime_path[0], runtime_path[-1], obstacles
                ) is None:
                    return None
            enemy_updates = tuple(updates)
        self.entity_sequence += 1
        return _SummonPlan(
            state=SummonState(
                instance_id=f"summon-{self.entity_sequence:06d}",
                summon_id=effect.summon_id,
                source_instance_id=source_instance_id,
                position=effect.position,
                hp=definition.max_hp,
                skills=tuple(
                    SkillState(skill_id=skill.skill_id, sp=skill.initial_sp)
                    for skill in definition.skills
                ),
            ),
            enemy_updates=enemy_updates,
        )

    def _route_obstacle_keys(self) -> set[tuple[int, int]]:
        return {
            (summon.position.col, summon.position.row)
            for summon in self.summons.values()
            if self._summon_defs[summon.summon_id].blocks_routes
        }

    def _walkable_keys(self) -> set[tuple[int, int]]:
        if self.content.map.walkable_tiles:
            return {(point.col, point.row) for point in self.content.map.walkable_tiles}
        return {
            (col, row)
            for row in range(self.content.map.height)
            for col in range(self.content.map.width)
        }

    def _path_around(
        self,
        start: GridPoint,
        goal: GridPoint,
        obstacles: set[tuple[int, int]],
    ) -> tuple[GridPoint, ...] | None:
        start_key = (start.col, start.row)
        goal_key = (goal.col, goal.row)
        walkable = self._walkable_keys() - obstacles
        if start_key not in walkable or goal_key not in walkable:
            return None
        frontier = [start_key]
        parent: dict[tuple[int, int], tuple[int, int] | None] = {start_key: None}
        cursor = 0
        while cursor < len(frontier):
            current = frontier[cursor]
            cursor += 1
            if current == goal_key:
                break
            col, row = current
            neighbors = sorted(
                ((col, row - 1), (col - 1, row), (col + 1, row), (col, row + 1)),
                key=lambda item: (item[1], item[0]),
            )
            for neighbor in neighbors:
                if neighbor not in walkable or neighbor in parent:
                    continue
                parent[neighbor] = current
                frontier.append(neighbor)
        if goal_key not in parent:
            return None
        reversed_path: list[tuple[int, int]] = []
        node: tuple[int, int] | None = goal_key
        while node is not None:
            reversed_path.append(node)
            node = parent[node]
        return tuple(GridPoint(col=col, row=row) for col, row in reversed(reversed_path))

    def _repath_enemy(
        self,
        enemy: EnemyState,
        obstacles: set[tuple[int, int]],
        *,
        force_shorter: bool = False,
    ) -> EnemyState | None:
        current_path = self._enemy_route_points(enemy)
        waypoint_index = enemy.waypoint_index
        progress = enemy.segment_progress_milli
        remaining_points = current_path[waypoint_index + (1 if progress else 0) :]
        remaining_keys = {(point.col, point.row) for point in remaining_points}
        if self._route_teleports[enemy.route_id]:
            # Replanning a route containing a discontinuous source transition
            # as one ordinary start-to-goal walk would silently erase the
            # portal.  The obstacle action is illegal only when it intersects
            # the remaining source-exact route; otherwise retain the route.
            return None if remaining_keys & obstacles else enemy
        if not force_shorter and not (remaining_keys & obstacles):
            return enemy
        goal = self._route_runtime_points[enemy.route_id][-1]

        candidates: list[tuple[int, tuple[tuple[int, int], ...], tuple[GridPoint, ...], int]] = []
        current = current_path[waypoint_index]
        if progress == 0:
            path = self._path_around(current, goal, obstacles)
            if path is not None:
                candidates.append(
                    (
                        (len(path) - 1) * 1000,
                        tuple((point.row, point.col) for point in path),
                        path,
                        0,
                    )
                )
        else:
            following = current_path[waypoint_index + 1]
            forward = self._path_around(following, goal, obstacles)
            if forward is not None:
                path = (current, *forward)
                candidates.append(
                    (
                        1000 - progress + (len(forward) - 1) * 1000,
                        tuple((point.row, point.col) for point in path),
                        path,
                        progress,
                    )
                )
            backward = self._path_around(current, goal, obstacles)
            if backward is not None:
                path = (following, *backward)
                candidates.append(
                    (
                        progress + (len(backward) - 1) * 1000,
                        tuple((point.row, point.col) for point in path),
                        path,
                        1000 - progress,
                    )
                )
        if not candidates:
            return None
        remaining_distance = (1000 - progress if progress else 0) + max(
            0, len(current_path) - waypoint_index - (2 if progress else 1)
        ) * 1000
        distance, _, path, next_progress = min(candidates)
        if force_shorter and distance >= remaining_distance:
            return enemy
        return enemy.model_copy(
            update={
                "path_override": path,
                "waypoint_index": 0,
                "segment_progress_milli": next_progress,
                "wait_remaining_ticks": 0,
            }
        )

    def _shorten_routes_after_obstacle_removal(self, *, reason: str) -> None:
        obstacles = self._route_obstacle_keys()
        for enemy_id in sorted(self.enemies):
            enemy = self.enemies[enemy_id]
            if self._enemy_defs[enemy.enemy_id].movement_layer is MovementLayer.FLYING:
                continue
            replanned = self._repath_enemy(enemy, obstacles, force_shorter=True)
            if replanned is None or replanned == enemy:
                continue
            self.enemies[enemy_id] = replanned
            self._emit(
                EventPhase.ACTION if reason == "summon_retreat" else EventPhase.DEATH,
                "route_replanned",
                enemy_id,
                {
                    "reason": reason,
                    "path": [point.model_dump(mode="json") for point in replanned.path_override],
                    "segment_progress_milli": replanned.segment_progress_milli,
                },
                priority=self.command_sequence * 100 + 2 if reason == "summon_retreat" else 2,
                tick=self.tick + 1 if reason == "summon_retreat" else self.tick,
            )

    def _validate_path(self, path: tuple[GridPoint, ...], *, context: str) -> None:
        if len(path) < 2:
            raise InvalidInitialStateError(f"{context} must contain at least two points")
        walkable = self._walkable_keys()
        for point in path:
            if (point.col, point.row) not in walkable:
                raise InvalidInitialStateError(f"{context} uses a non-walkable point")
        for before, after in zip(path, path[1:]):
            if abs(before.col - after.col) + abs(before.row - after.row) != 1:
                raise InvalidInitialStateError(f"{context} is not contiguous")

    def _draw_rng(self, stream: str, minimum: int, maximum: int) -> int:
        """Draw from an isolated counter-based stream with no platform PRNG state."""

        draw = self.rng_streams.get(stream, 0) + 1
        self.rng_streams[stream] = draw
        payload = f"{self.rng_seed}:{stream}:{draw}".encode("utf-8")
        sample = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
        return minimum + sample % (maximum - minimum + 1)

    @staticmethod
    def _scale_fixed(value: int, multiplier_milli: int) -> _Exact:
        # **恒返回精确量**, 而不是「输入带小数才带小数」。干员技能倍率走的正是
        # 这里: `_scale_fixed(816, 3400)` 旧版给 2774(精确 2774.4), 于是那一击
        # 恒 1974 而实测是 1974/1975 交替。整数入参出来仍是同一个整数, 只是它
        # 记得自己的精确值, 所以非伤害用途(冷却间隔一类)行为不变。
        return _exact(_damage_milli(value) * multiplier_milli // 1000)

    def _recover_cost_and_roster(
        self,
        *,
        cost_recovery_multiplier_milli: int = 1000,
    ) -> None:
        recovery_interval = max(
            1,
            self._scale_fixed(
                self.content.cost_recovery_interval_ticks,
                self.global_modifiers.cost_recovery_interval_multiplier_milli,
            ),
        )
        interval_milli = recovery_interval * 1000
        previous_clock_milli = (
            (self.tick - 1) * 1000 - self.cost_recovery_slowdown_debt_milli
        )
        self.cost_recovery_slowdown_debt_milli += (
            1000 - cost_recovery_multiplier_milli
        )
        current_clock_milli = (
            self.tick * 1000 - self.cost_recovery_slowdown_debt_milli
        )
        crossed_recovery_boundary = (
            previous_clock_milli // interval_milli
            < current_clock_milli // interval_milli
        )
        if crossed_recovery_boundary and self.cost < self.effective_max_cost:
            before = self.cost
            self.cost += 1
            self._emit(
                EventPhase.COST,
                "cost_recovered",
                "system",
                {"before": before, "after": self.cost},
            )
        for operator_id in sorted(self.roster):
            roster = self.roster[operator_id]
            if roster.redeploy_remaining <= 0:
                continue
            remaining = roster.redeploy_remaining - 1
            self.roster[operator_id] = roster.model_copy(update={"redeploy_remaining": remaining})
            if remaining == 0:
                self._emit(
                    EventPhase.COST,
                    "redeploy_ready",
                    operator_id,
                    {},
                    priority=1,
                )

    def _activate_due_tokens(self) -> None:
        """Bring predefined tokens into play at the tick their action fires.

        The shipped stages activate by alias (``trap_024_npcsld#1``) from a wave
        action, so the alias is what gets recorded -- not a synthesised id --
        and a token already activated is never activated again even if the
        battle is restored from a checkpoint mid-way.
        """

        for alias in sorted(self._tokens_by_alias):
            token = self._tokens_by_alias[alias]
            if alias in self.activated_token_aliases:
                continue
            if token.activation_tick > self.tick:
                continue
            self.activated_token_aliases.add(alias)
            self.entity_sequence += 1
            instance_id = f"summon-{self.entity_sequence:06d}"
            self.summons[instance_id] = SummonState(
                instance_id=instance_id,
                summon_id=token.summon_id,
                source_instance_id="stage",
                position=token.position,
                facing=token.facing,
                hp=self._summon_defs[token.summon_id].max_hp,
                pulse_remaining_ticks=(
                    self._summon_defs[token.summon_id].pulse_interval_ticks
                ),
            )
            self._emit(
                EventPhase.SPAWN,
                "predefined_token_activated",
                instance_id,
                {
                    "alias": alias,
                    "summon_id": token.summon_id,
                    "activation_tick": token.activation_tick,
                },
                priority=2,
            )

    def _spawn_due(self) -> None:
        for item in self._scheduled:
            if (
                item.tick != self.spawn_progress_tick
                or item.occurrence_id in self.spawned_spawn_ids
            ):
                continue
            definition = self._enemy_defs[item.enemy_id]
            route = self._routes[item.route_id]
            path_override: tuple[GridPoint, ...] = ()
            obstacles = self._route_obstacle_keys()
            runtime_path = self._route_runtime_points[route.route_id]
            if route.movement_layer is MovementLayer.GROUND and any(
                (point.col, point.row) in obstacles for point in runtime_path[1:-1]
            ):
                if self._route_teleports[route.route_id]:
                    raise UnsupportedMechanicError(
                        f"active obstacle intersects teleport route for spawn {item.occurrence_id}"
                    )
                planned = self._path_around(route.waypoints[0], route.waypoints[-1], obstacles)
                if planned is None:
                    raise UnsupportedMechanicError(
                        f"active obstacles leave no route for spawn {item.occurrence_id}"
                    )
                path_override = planned
            self.entity_sequence += 1
            instance_id = f"enemy-{self.entity_sequence:06d}"
            self.enemies[instance_id] = EnemyState(
                instance_id=instance_id,
                enemy_id=item.enemy_id,
                route_id=item.route_id,
                hp=definition.max_hp,
                wait_remaining_ticks=(
                    0
                    if path_override
                    else self._route_runtime_waits[route.route_id][0]
                ),
                path_override=path_override,
                spawn_occurrence_id=item.occurrence_id,
                coefficient_runtime=self._initial_enemy_coefficient_runtime(definition),
                herd_resting_remaining_ticks=(
                    self._seconds_micros_to_ticks(standby_duration)
                    if (
                        standby_duration := coefficient_value(
                            definition, "revive.unmove_duration"
                        )
                    )
                    else (
                        4500
                        if self.content.bundle_id == "level_rogue3_1-4"
                        and definition.enemy_id
                        in {"enemy_1303_mhshep", "enemy_1303_mhshep_b"}
                        else 0
                    )
                ),
            )
            started_modifiers = self._start_enemy_timed_self_modifiers(instance_id, definition)
            self.spawned_spawn_ids.add(item.occurrence_id)
            self._emit(
                EventPhase.SPAWN,
                "spawn",
                instance_id,
                {
                    "occurrence_id": item.occurrence_id,
                    "enemy_id": item.enemy_id,
                    "route_id": item.route_id,
                },
            )
            for state, modifier in started_modifiers:
                self._emit(
                    EventPhase.SPAWN,
                    "enemy_self_modifier_started",
                    instance_id,
                    {
                        "effect_instance_id": state.instance_id,
                        "modifier_id": modifier.modifier_id,
                        "stack_key": modifier.stack_key,
                        "stat": modifier.stat.value,
                        "additive": modifier.additive,
                        "multiplier_milli": modifier.multiplier_milli,
                        "duration_ticks": modifier.duration_ticks,
                        "active_from_tick": self.tick,
                        "expires_before_tick": self.tick + modifier.duration_ticks,
                    },
                    priority=1,
                )

    @staticmethod
    def _seconds_micros_to_ticks(value_micros: int) -> int:
        numerator = value_micros * 30
        if numerator % 1_000_000:
            raise UnsupportedMechanicError(
                "enemy coefficient duration is not exactly representable at 30 TPS"
            )
        return numerator // 1_000_000

    @staticmethod
    def _ramp_interval_ticks(value_micros: int) -> int:
        """The charge ramp's interval, rounded to the nearest whole tick.

        萨卡兹穿刺手 ships 0.5 s, which lands on 15 ticks exactly, but 凶豕兽 ships
        **0.333** -- a three-decimal truncation of a third of a second, whose
        exact value is 9.99 ticks.  Refusing it the way every other duration is
        refused would mean 4-4 could not be constructed at all, so the ramp
        rounds: 0.333 s becomes 10 ticks, i.e. the third of a second the shipped
        figure is a truncation of.

        Where the engine puts that rounding is a 取整位置 question and is
        registered 待对齐 in the ledger; this is the default until a live
        readback settles it.  Only the ramp uses it -- every other duration
        still has to be exact.
        """

        numerator = value_micros * 30
        ticks = (numerator + 500_000) // 1_000_000
        if ticks <= 0:
            raise UnsupportedMechanicError(
                "charge acceleration interval is shorter than one tick"
            )
        return ticks

    def _initial_enemy_coefficient_runtime(
        self,
        enemy: EnemyDefinition,
    ) -> EnemyCoefficientRuntimeState | None:
        """Seed per-enemy coefficient state, then merge data-driven families.

        The id-keyed branches below predate the S2-b lift and each returns
        early, so a data-driven family declared by an enemy that also happens to
        be in one of those sets would be dropped silently.  Merging afterwards
        keeps the families independent of that branch order.
        """

        base = self._initial_enemy_coefficient_runtime_by_id(enemy)
        seed: dict[str, object] = {}

        interval = coefficient_value(enemy, "FreezeAura.interval")
        if interval is not None:
            pulse_ticks = self._seconds_micros_to_ticks(interval)
            if pulse_ticks <= 0:
                raise UnsupportedMechanicError(
                    "freeze aura interval is shorter than one tick"
                )
            seed["freeze_aura_pulse_remaining_ticks"] = pulse_ticks

        self_damage = coefficient_value(enemy, "periodic_damage.damage")
        if self_damage is None:
            # The ratio form of the same mechanic: 「每秒流失最大生命值×4%」.
            self_damage = coefficient_value(enemy, "Bleed.hp_ratio")
        if self_damage is not None:
            # PRTS states the period as one second, so it is the bundle's clock
            # rather than a constant: a bundle at another TPS still loses the
            # same HP per second.
            seed["self_damage_remaining_ticks"] = self.content.ticks_per_second

        if enemy.charged_skill is not None and enemy.charged_skill.cooldown_ticks:
            # The first use waits out `initCooldown`, which ships shorter than
            # the recharge; seeding the recharge would delay it.
            seed["charged_skill_cooldown_ticks"] = (
                enemy.charged_skill.initial_cooldown_ticks
                or enemy.charged_skill.cooldown_ticks
            )
        elif enemy.charged_skill is not None:
            seed.setdefault("charged_skill_sp", 0)

        if enemy.takeoff is not None:
            # 「初始冷却1秒」 -- it cannot fly out of the very first block.
            seed["takeoff_cooldown_remaining_ticks"] = (
                enemy.takeoff.initial_cooldown_ticks
            )
        if enemy.victory_buff is not None:
            # The skill's own cooldown runs from spawn, exactly as the
            # timed charged skills do; the partner condition gates the
            # use rather than starting the clock.
            seed["victory_delay_remaining_ticks"] = enemy.victory_buff.delay_ticks
            seed.setdefault("victory_active", False)

        if any(item.key in ENERGY_KEYS for item in enemy.mechanic_coefficients):
            # A pool holder needs the side table from the start: energy can be
            # granted to it, or taken from it, on any tick.
            seed.setdefault("energy_stacks", 0)
        if enemy.energy_charge is not None:
            # The first window opens after `initCooldown`, not at tick one: the
            # charger used to start granting immediately and never stop.
            seed["energy_charge_cycle_remaining_ticks"] = max(
                1, enemy.energy_charge.initial_cooldown_ticks
            )
        steal_interval = coefficient_value(enemy, "steal_energy.interval")
        if steal_interval is not None:
            seed["energy_steal_remaining_ticks"] = self._seconds_micros_to_ticks(
                steal_interval
            )

        if any(item.key in RAGE_KEYS for item in enemy.mechanic_coefficients):
            # A rage carrier needs the side table from the start: the flag is
            # raised by its partner's exit, which can happen on any tick.
            seed.setdefault("rage_active", False)

        if enemy.predation is not None:
            seed["predation_cooldown_remaining_ticks"] = (
                enemy.predation.bite_initial_cooldown_ticks
            )
        if enemy.bomb_run is not None:
            seed["bomb_run_cooldown_remaining_ticks"] = (
                enemy.bomb_run.initial_cooldown_ticks
            )
        if enemy.born_bugs is not None:
            seed["born_bugs_cooldown_remaining_ticks"] = (
                enemy.born_bugs.initial_cooldown_ticks
            )
        if enemy.repair is not None:
            seed["repair_cooldown_remaining_ticks"] = (
                enemy.repair.initial_cooldown_ticks
            )
        if enemy.immolation is not None:
            seed["immolation_cooldown_remaining_ticks"] = (
                enemy.immolation.initial_cooldown_ticks
            )
        if enemy.doom is not None:
            seed["doom_cooldown_remaining_ticks"] = (
                enemy.doom.initial_cooldown_ticks
            )
        if enemy.chain_buff is not None:
            seed["chain_buff_cooldown_remaining_ticks"] = (
                enemy.chain_buff.initial_cooldown_ticks
            )
        if enemy.blink is not None:
            seed["blink_cooldown_remaining_ticks"] = (
                enemy.blink.initial_cooldown_ticks
            )
        if enemy.ice_bomb is not None:
            seed["bomb_delay_remaining_ticks"] = enemy.ice_bomb.delay_ticks
        if enemy.four_direction_volley is not None:
            seed["volley_cooldown_remaining_ticks"] = (
                enemy.four_direction_volley.initial_cooldown_ticks
            )
        if enemy.backstab is not None:
            seed["backstab_cooldown_remaining_ticks"] = (
                enemy.backstab.initial_cooldown_ticks
            )
        if enemy.guided_strike is not None:
            # 「初始10s」 -- the first lock is a shipped delay, not zero.
            seed["guide_cooldown_remaining_ticks"] = (
                enemy.guided_strike.initial_cooldown_ticks
            )
        ramp = rush_acceleration_rows(enemy)
        if ramp:
            seed["rush_interval_remaining_ticks"] = self._ramp_interval_ticks(
                ramp["interval"]
            )
            predelay = ramp.get("predelay")
            seed["rush_predelay_remaining_ticks"] = (
                self._seconds_micros_to_ticks(predelay) if predelay else 0
            )

        if not seed:
            return base
        return (base or EnemyCoefficientRuntimeState()).model_copy(update=seed)

    def _initial_enemy_coefficient_runtime_by_id(
        self,
        enemy: EnemyDefinition,
    ) -> EnemyCoefficientRuntimeState | None:
        if carries_polluted_tank(enemy):
            charge = coefficient_value(enemy, "Empty.sp")
            if charge is None or charge % 1_000_000:
                raise UnsupportedMechanicError(
                    "Return Body tank charge is not a whole attack-recovery point"
                )
            return EnemyCoefficientRuntimeState(
                polluted_attack_charge=charge // 1_000_000
            )
        if enemy.enemy_id in RETURN_BODY_CANNON_IDS:
            initial_cooldown = coefficient_value(enemy, "Cannon.initCooldown")
            if initial_cooldown is None:
                raise UnsupportedMechanicError(
                    "Return Body cannon initial cooldown disappeared at runtime"
                )
            return EnemyCoefficientRuntimeState(
                cannon_skill_cooldown_remaining_ticks=(
                    self._seconds_micros_to_ticks(initial_cooldown)
                )
            )
        if carries_polluted_death(enemy) or carries_bleed_on_attack(enemy):
            return EnemyCoefficientRuntimeState()
        if enemy.enemy_id in MAINLINE12_DISGUISED_IDS:
            return EnemyCoefficientRuntimeState(
                identity_detection_phase="phase1",
                identity_detection_trigger_remaining_ticks=(
                    MAINLINE12_DETECTION_INTERVAL_TICKS
                ),
            )
        if enemy.enemy_id in MAINLINE12_FIRST_AOE_IDS:
            return EnemyCoefficientRuntimeState()
        if enemy.enemy_id in GHOST_REDACE_IDS | GHOST_HOLOGRAM_IDS:
            return EnemyCoefficientRuntimeState()
        if enemy.enemy_id in STANDBY_REVIVE_ENEMY_IDS:
            guard_duration = (
                coefficient_value(enemy, "buff.duration")
                if enemy.enemy_id in STANDBY_REVIVE_INITIAL_GUARD_IDS
                else None
            )
            return EnemyCoefficientRuntimeState(
                initial_guard_remaining_ticks=(
                    self._seconds_micros_to_ticks(guard_duration)
                    if guard_duration is not None
                    else 0
                )
            )
        if not any(
            coefficient.key in WAITING_FLOCK_KEYS for coefficient in enemy.mechanic_coefficients
        ):
            return None
        lifetime = coefficient_value(enemy, "Suicide.duration")
        return EnemyCoefficientRuntimeState(
            lifetime_remaining_ticks=(
                self._seconds_micros_to_ticks(lifetime) if lifetime is not None else None
            )
        )

    def _validate_enemy_coefficient_runtime(
        self,
        enemy: EnemyState,
        definition: EnemyDefinition,
    ) -> None:
        runtime = enemy.coefficient_runtime
        requires_runtime = any(
            coefficient.key in WAITING_FLOCK_KEYS
            for coefficient in definition.mechanic_coefficients
        ) or definition.enemy_id in (
            STANDBY_REVIVE_ENEMY_IDS
            | GHOST_REDACE_IDS
            | GHOST_HOLOGRAM_IDS
            | MAINLINE12_DISGUISED_IDS
            | self._polluted_death_ids
            | self._polluted_tank_ids
            | RETURN_BODY_CANNON_IDS
        ) or carries_bleed_on_attack(definition)
        requires_runtime = requires_runtime or any(
            coefficient.key == "AOEAttack.atk_scale"
            for coefficient in definition.mechanic_coefficients
        )
        declares_freeze_aura = any(
            coefficient.key in FREEZE_AURA_KEYS
            for coefficient in definition.mechanic_coefficients
        )
        requires_runtime = requires_runtime or declares_freeze_aura
        requires_runtime = requires_runtime or bool(
            rush_acceleration_rows(definition)
        )
        requires_runtime = requires_runtime or (
            quill_spray_rows(definition) is not None
        )
        requires_runtime = requires_runtime or (
            self._def_reduce_step(definition) is not None
        )
        requires_runtime = requires_runtime or (
            coefficient_value(definition, "periodic_damage.damage") is not None
        )
        requires_runtime = requires_runtime or any(
            item.key in RAGE_KEYS for item in definition.mechanic_coefficients
        )
        requires_runtime = requires_runtime or any(
            item.key in ENERGY_KEYS | STEAL_ENERGY_KEYS
            for item in definition.mechanic_coefficients
        )
        requires_runtime = requires_runtime or definition.charged_skill is not None
        requires_runtime = requires_runtime or definition.victory_buff is not None
        requires_runtime = requires_runtime or definition.takeoff is not None
        requires_runtime = requires_runtime or definition.self_rage is not None
        requires_runtime = requires_runtime or definition.predation is not None
        requires_runtime = requires_runtime or definition.guided_strike is not None
        requires_runtime = requires_runtime or definition.backstab is not None
        requires_runtime = requires_runtime or definition.ice_bomb is not None
        requires_runtime = requires_runtime or definition.spawn_decay is not None
        requires_runtime = requires_runtime or definition.blink is not None
        requires_runtime = requires_runtime or definition.chain_buff is not None
        requires_runtime = requires_runtime or definition.doom is not None
        requires_runtime = requires_runtime or definition.reborning is not None
        requires_runtime = requires_runtime or definition.immolation is not None
        requires_runtime = requires_runtime or definition.repair is not None
        requires_runtime = requires_runtime or definition.flame is not None
        requires_runtime = requires_runtime or definition.born_bugs is not None
        requires_runtime = (
            requires_runtime or definition.crossroads_volley is not None
        )
        requires_runtime = requires_runtime or definition.bomb_run is not None
        requires_runtime = (
            requires_runtime or definition.four_direction_volley is not None
        )
        requires_runtime = (
            requires_runtime or bool(definition.stealth_strike_scale_micros)
        )
        requires_runtime = (
            requires_runtime or definition.environmental_fragile is not None
        )
        if requires_runtime and runtime is None:
            raise InvalidInitialStateError("coefficient enemy lacks deterministic runtime state")
        if runtime is None:
            return
        ramp_rows = rush_acceleration_rows(definition)
        if bool(runtime.rush_interval_remaining_ticks) and not ramp_rows:
            raise InvalidInitialStateError(
                "charge acceleration counter exists without a source ramp"
            )
        if ramp_rows:
            if runtime.rush_acceleration_stacks > ramp_rows["trig_cnt"] // 1_000_000:
                raise InvalidInitialStateError(
                    "charge acceleration stacks exceed the source trigger count"
                )
            if runtime.rush_interval_remaining_ticks > (
                self._ramp_interval_ticks(ramp_rows["interval"])
            ):
                raise InvalidInitialStateError(
                    "charge acceleration counter exceeds its source interval"
                )
        if bool(runtime.freeze_aura_pulse_remaining_ticks) != declares_freeze_aura:
            raise InvalidInitialStateError(
                "freeze aura pulse counter and its source declaration disagree"
            )
        if declares_freeze_aura:
            aura_interval = coefficient_value(definition, "FreezeAura.interval")
            if aura_interval is None or runtime.freeze_aura_pulse_remaining_ticks > (
                self._seconds_micros_to_ticks(aura_interval)
            ):
                raise InvalidInitialStateError(
                    "freeze aura pulse counter exceeds its source interval"
                )
        lifetime = coefficient_value(definition, "Suicide.duration")
        if runtime.lifetime_remaining_ticks is not None:
            if lifetime is None or runtime.lifetime_remaining_ticks > (
                self._seconds_micros_to_ticks(lifetime)
            ):
                raise InvalidInitialStateError("enemy coefficient lifetime exceeds its source")
        speed_duration = coefficient_value(definition, "SpeedUp.duration")
        speed_cooldown = coefficient_value(definition, "SpeedUp.cooldown")
        if runtime.speed_duration_remaining_ticks > (
            self._seconds_micros_to_ticks(speed_duration) if speed_duration else 0
        ):
            raise InvalidInitialStateError("enemy coefficient speed duration exceeds its source")
        if runtime.speed_cooldown_remaining_ticks > (
            self._seconds_micros_to_ticks(speed_cooldown) if speed_cooldown else 0
        ):
            raise InvalidInitialStateError("enemy coefficient speed cooldown exceeds its source")
        if (
            runtime.post_hit_speed_active
            and coefficient_value(definition, "run.attack@move_speed") is None
        ):
            raise InvalidInitialStateError("enemy post-hit speed state lacks its source")
        # The enemy-side death-buff fields are no longer written: PRTS puts the
        # drone's buff on 我方/召唤物, so it lives on the friendly states now.
        # The fields stay declared because snapshots and fixtures carry them,
        # but pinning their values here would guard a path with no writer.
        guard_duration = coefficient_value(definition, "buff.duration")
        maximum_guard_ticks = (
            self._seconds_micros_to_ticks(guard_duration)
            if definition.enemy_id in STANDBY_REVIVE_INITIAL_GUARD_IDS
            and guard_duration is not None
            else 0
        )
        if runtime.initial_guard_remaining_ticks > maximum_guard_ticks:
            raise InvalidInitialStateError("enemy initial guard exceeds its source duration")
        if runtime.ghost_created and definition.enemy_id not in GHOST_REDACE_IDS:
            raise InvalidInitialStateError("ghost-created state lacks a Redmark source")
        if (
            runtime.ghost_owner_instance_id is not None
            and definition.enemy_id not in GHOST_HOLOGRAM_IDS
        ):
            raise InvalidInitialStateError("ghost-owner state lacks a hologram source")
        if definition.enemy_id in MAINLINE12_DISGUISED_IDS:
            if runtime.identity_detection_phase is None:
                raise InvalidInitialStateError(
                    "Mainline 12 disguised enemy lacks its identity runtime"
                )
            if (
                runtime.enhanced_attack_pending
                and definition.enemy_id not in MAINLINE12_ENHANCED_ATTACK_IDS
            ):
                raise InvalidInitialStateError(
                    "Mainline 12 enhanced attack escaped its source enemy"
                )
            if (
                runtime.revealed_invisible
                and definition.enemy_id not in MAINLINE12_REVEALED_INVISIBLE_IDS
            ):
                raise InvalidInitialStateError(
                    "Mainline 12 invisibility escaped its source enemy"
                )
        elif runtime.identity_detection_phase is not None:
            raise InvalidInitialStateError(
                "identity detection runtime escaped the Mainline 12 disguised family"
            )
        return_body_ids = (
            self._polluted_death_ids
            | self._polluted_tank_ids
            | RETURN_BODY_CANNON_IDS
        ) | frozenset(
            enemy.enemy_id
            for enemy in self.content.enemies
            if carries_bleed_on_attack(enemy)
        )
        if definition.enemy_id in self._polluted_tank_ids:
            maximum_charge = coefficient_value(definition, "Empty.sp")
            if maximum_charge is None or maximum_charge % 1_000_000:
                raise InvalidInitialStateError("Return Body tank charge source is invalid")
            if runtime.polluted_attack_charge > maximum_charge // 1_000_000:
                raise InvalidInitialStateError("Return Body tank charge exceeds its source")
        elif runtime.polluted_attack_charge:
            raise InvalidInitialStateError("pollution attack charge escaped its source enemy")
        if definition.enemy_id in RETURN_BODY_CANNON_IDS:
            initial_cooldown = coefficient_value(definition, "Cannon.initCooldown")
            cooldown = coefficient_value(definition, "Cannon.cooldown")
            if initial_cooldown is None or cooldown is None:
                raise InvalidInitialStateError("Return Body cannon cooldown matrix is incomplete")
            maximum_cooldown = max(
                self._seconds_micros_to_ticks(initial_cooldown),
                self._seconds_micros_to_ticks(cooldown),
            )
            if runtime.cannon_skill_cooldown_remaining_ticks > maximum_cooldown:
                raise InvalidInitialStateError("Return Body cannon cooldown exceeds its source")
        elif (
            runtime.cannon_skill_cooldown_remaining_ticks
            or runtime.cannon_barrage_remaining_shots
            or runtime.cannon_barrage_next_shot_ticks
            or runtime.cannon_barrage_center is not None
        ):
            raise InvalidInitialStateError("cannon barrage state escaped its source enemy")
        if definition.enemy_id not in return_body_ids and (
            runtime.polluted_attack_charge
            or runtime.cannon_skill_cooldown_remaining_ticks
            or runtime.cannon_barrage_remaining_shots
            or runtime.cannon_barrage_next_shot_ticks
            or runtime.cannon_barrage_center is not None
        ):
            raise InvalidInitialStateError("Return Body runtime escaped its source family")

    def _spawn_damage_hologram(self, owner: EnemyState) -> str:
        """Create the exact identity-bound, stationary first-hit hologram."""

        # 「首次受伤时制造一个**全息幻影**」 -- the phantom's id is shipped on
        # the carrier (`createGhost.enemy_key`, a string), so it is read rather
        # than looked up in a hand-written table.  The table said the same
        # thing, but nothing checked that it kept saying it.
        hologram_enemy_id = self._enemy_defs[owner.enemy_id].hologram_enemy_id
        if hologram_enemy_id is None:
            raise UnsupportedMechanicError(
                "hologram carrier lost its shipped phantom id at runtime"
            )
        definition = self._enemy_defs.get(hologram_enemy_id)
        if definition is None:
            raise UnsupportedMechanicError("Redmark hologram definition disappeared at runtime")
        if owner.route_teleport_destination_index is not None:
            raise UnsupportedMechanicError(
                "hologram creation during route teleport has no source-exact position"
            )
        self.entity_sequence += 1
        instance_id = f"enemy-{self.entity_sequence:06d}"
        self.enemies[instance_id] = EnemyState(
            instance_id=instance_id,
            enemy_id=hologram_enemy_id,
            route_id=owner.route_id,
            waypoint_index=owner.waypoint_index,
            segment_progress_milli=owner.segment_progress_milli,
            movement_phase_remainder=owner.movement_phase_remainder,
            hp=definition.max_hp,
            path_override=owner.path_override,
            coefficient_runtime=EnemyCoefficientRuntimeState(
                ghost_owner_instance_id=owner.instance_id
            ),
        )
        col_milli, row_milli = self._enemy_position_milli(self.enemies[instance_id])
        self._emit(
            EventPhase.DAMAGE,
            "enemy_coefficient_ghost_created",
            owner.instance_id,
            {
                "hologram_instance_id": instance_id,
                "hologram_enemy_id": hologram_enemy_id,
                "owner_enemy_id": owner.enemy_id,
                "col_milli": col_milli,
                "row_milli": row_milli,
                "source_key": "createGhost.enemy_key",
            },
            priority=1,
        )
        return instance_id

    def _pulse_freeze_aura(self, instance_id: str, enemy: EnemyState) -> int:
        """Freeze every friendly within the carrier's aura radius; return the reset.

        Freeze shares the "cannot act" countdown with stun, exactly as the
        attack-borne control path does, because :class:`ControlEffect` has no
        frozen state of its own.  An existing longer countdown is kept rather
        than shortened, so overlapping sources never cancel each other.
        """

        definition = self._enemy_defs[enemy.enemy_id]
        radius = coefficient_value(definition, "FreezeAura.range_radius")
        freeze = coefficient_value(definition, "FreezeAura.freeze")
        interval = coefficient_value(definition, "FreezeAura.interval")
        if radius is None or freeze is None or interval is None:
            raise UnsupportedMechanicError(
                "freeze aura lost part of its triple at runtime"
            )
        freeze_ticks = self._seconds_micros_to_ticks(freeze)
        radius_milli = radius // 1000
        radius_squared = radius_milli * radius_milli
        center_col, center_row = self._enemy_position_milli(enemy)

        frozen_ids: list[str] = []
        for target_id in sorted((*self.operators, *self.summons)):
            actor: OperatorState | SummonState = (
                self.operators[target_id]
                if target_id in self.operators
                else cast(SummonState, self.summons[target_id])
            )
            delta_col = actor.position.col * 1000 - center_col
            delta_row = actor.position.row * 1000 - center_row
            if delta_col * delta_col + delta_row * delta_row > radius_squared:
                continue
            if freeze_ticks <= 0:
                continue
            updated = actor.model_copy(
                update=self._enemy_cold_update(actor, freeze_ticks)
            )
            if target_id in self.operators:
                self.operators[target_id] = cast(OperatorState, updated)
            else:
                self.summons[target_id] = cast(SummonState, updated)
            frozen_ids.append(target_id)

        self._emit(
            EventPhase.ATTACK,
            "enemy_coefficient_freeze_aura_pulsed",
            instance_id,
            {
                "target_ids": tuple(frozen_ids),
                "duration_ticks": freeze_ticks,
                "radius_milli_tiles": radius_milli,
                "source_key": "FreezeAura.interval",
            },
            priority=21,
        )
        return self._seconds_micros_to_ticks(interval)

    def _tick_enemy_coefficient_runtime(self) -> bool:
        """Advance coefficient timers in O(live enemies), before this tick's actions."""

        expired_lifetime = False
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            runtime = enemy.coefficient_runtime
            if runtime is None:
                continue
            update: dict[str, object] = {}
            if (
                self._enemy_defs[enemy.enemy_id].stealth_strike_scale_micros
                and not runtime.stealth_strike_armed
                and self._enemy_is_hidden(enemy)
            ):
                # 【强击标记】 is earned for *being* hidden, and is read at the top
                # of this pass -- before any of the branches below can `continue`
                # past it, and before blocks reconcile.  A carrier that walks
                # into its blocker later in the same tick still spent the tick
                # hidden, and that swing is 「隐匿效果失效后的下一次攻击」.
                update["stealth_strike_armed"] = True
            if runtime.reborning_remaining_ticks:
                remaining = runtime.reborning_remaining_ticks - 1
                update["reborning_remaining_ticks"] = remaining
                if not remaining:
                    # 「重生结束后攻击力+50%」
                    update["reborning_empowered"] = True
                    self._emit(
                        EventPhase.SPAWN,
                        "enemy_reborning_finished",
                        instance_id,
                        {"enemy_id": enemy.enemy_id},
                        priority=1,
                    )
            if runtime.chain_buff_remaining_ticks:
                remaining = runtime.chain_buff_remaining_ticks - 1
                update["chain_buff_remaining_ticks"] = remaining
                if not remaining:
                    update["chain_buff_attack_speed"] = 0
                    update["chain_buff_move_speed_micros"] = 0
            decay = self._enemy_defs[enemy.enemy_id].spawn_decay
            if decay is not None and not runtime.spawn_decay_frozen:
                elapsed = runtime.spawn_decay_elapsed_ticks + 1
                update["spawn_decay_elapsed_ticks"] = elapsed
                if elapsed >= decay.duration_ticks:
                    # 「登场10秒后**强制击杀自身**」 -- nothing else kills it, so
                    # this is what ends it.
                    self.enemies[instance_id] = enemy.model_copy(update={"hp": 0})
                    self._emit(
                        EventPhase.DEATH,
                        "enemy_spawn_decay_expired",
                        instance_id,
                        {"duration_ticks": decay.duration_ticks},
                    )
                    expired_lifetime = True
                    continue
            if runtime.lifetime_remaining_ticks is not None:
                remaining = max(0, runtime.lifetime_remaining_ticks - 1)
                update["lifetime_remaining_ticks"] = remaining
                if remaining == 0:
                    self.enemies[instance_id] = enemy.model_copy(update={"hp": 0})
                    self._emit(
                        EventPhase.DEATH,
                        "enemy_coefficient_self_destructed",
                        instance_id,
                        {"coefficient_key": "Suicide.duration"},
                    )
                    expired_lifetime = True
                    continue
            if runtime.speed_duration_remaining_ticks:
                if enemy.blocked_by is not None:
                    # 染污躯壳 PRTS: 「受到伤害时**若未被阻挡**则2s内移动速度+300%
                    # （**被阻挡时立刻解除**）」 -- so holding it is what takes the
                    # burst away, which is the whole counterplay to it.
                    update["speed_duration_remaining_ticks"] = 0
                    self._emit(
                        EventPhase.MOVE,
                        "enemy_coefficient_speed_cancelled",
                        instance_id,
                        {"reason": "blocked", "source_key": "SpeedUp.move_speed"},
                        priority=2,
                    )
                else:
                    update["speed_duration_remaining_ticks"] = (
                        runtime.speed_duration_remaining_ticks - 1
                    )
            if runtime.speed_cooldown_remaining_ticks:
                update["speed_cooldown_remaining_ticks"] = (
                    runtime.speed_cooldown_remaining_ticks - 1
                )
            if runtime.friendly_buff_remaining_ticks:
                remaining = runtime.friendly_buff_remaining_ticks - 1
                update["friendly_buff_remaining_ticks"] = remaining
                if remaining == 0:
                    update["friendly_attack_speed_additive_micros"] = 0
                    update["friendly_defense_multiplier_milli"] = 1000
            if runtime.initial_guard_remaining_ticks:
                update["initial_guard_remaining_ticks"] = (
                    runtime.initial_guard_remaining_ticks - 1
                )
            if runtime.freeze_aura_pulse_remaining_ticks:
                remaining = runtime.freeze_aura_pulse_remaining_ticks - 1
                if remaining == 0:
                    remaining = self._pulse_freeze_aura(instance_id, enemy)
                update["freeze_aura_pulse_remaining_ticks"] = remaining
            if runtime.overload_remaining_ticks:
                remaining = runtime.overload_remaining_ticks - 1
                update["overload_remaining_ticks"] = remaining
                if remaining == 0:
                    # PRTS: the pool resets when overload ends.
                    update["energy_stacks"] = 0
                    self._emit(
                        EventPhase.ATTACK,
                        "enemy_overload_ended",
                        instance_id,
                        {"source_key": "over_energy.duration"},
                    )
            else:
                schedule = self._enemy_defs[enemy.enemy_id].energy_charge
                if schedule is not None:
                    update.update(
                        self._advance_energy_charge(instance_id, enemy, runtime, schedule)
                    )
            if runtime.energy_steal_remaining_ticks:
                remaining = runtime.energy_steal_remaining_ticks - 1
                if remaining == 0:
                    definition = self._enemy_defs[enemy.enemy_id]
                    radius = coefficient_value(definition, "steal_energy.range_radius")
                    count = coefficient_value(definition, "steal_energy.steal_count")
                    interval = coefficient_value(definition, "steal_energy.interval")
                    if radius is None or count is None or interval is None:
                        raise UnsupportedMechanicError(
                            "energy theft lost part of its triple at runtime"
                        )
                    taken = 0
                    for victim_id in self._energy_holders_near(
                        enemy, radius // 1000, ground_only=True
                    ):
                        victim = self.enemies[victim_id]
                        victim_runtime = victim.coefficient_runtime
                        if victim_runtime is None or not victim_runtime.energy_stacks:
                            continue
                        stolen = min(count // 1_000_000, victim_runtime.energy_stacks)
                        self.enemies[victim_id] = victim.model_copy(
                            update={
                                "coefficient_runtime": victim_runtime.model_copy(
                                    update={
                                        "energy_stacks": (
                                            victim_runtime.energy_stacks - stolen
                                        )
                                    }
                                )
                            }
                        )
                        taken += stolen
                    if taken:
                        # It keeps what it takes.  This cannot go through
                        # `_grant_energy`: that writes the enemy row directly,
                        # and this loop rewrites the same row afterwards from a
                        # copy taken at the top of the iteration, which would
                        # silently discard the gain.  So it folds into the same
                        # update the loop is about to apply.
                        cap = self._energy_cap(self._enemy_defs[enemy.enemy_id])
                        current = int(
                            update.get("energy_stacks", runtime.energy_stacks)
                        )
                        filled = min(cap, current + taken)
                        update["energy_stacks"] = filled
                        if filled >= cap and not runtime.overload_remaining_ticks:
                            overload_seconds = coefficient_value(
                                self._enemy_defs[enemy.enemy_id],
                                "over_energy.duration",
                            )
                            if overload_seconds is None:
                                raise UnsupportedMechanicError(
                                    "overload lost its duration at runtime"
                                )
                            update["overload_remaining_ticks"] = (
                                self._seconds_micros_to_ticks(overload_seconds)
                            )
                            self._emit(
                                EventPhase.ATTACK,
                                "enemy_overload_started",
                                instance_id,
                                {
                                    "duration_ticks": update[
                                        "overload_remaining_ticks"
                                    ],
                                    "source_key": "over_energy.duration",
                                },
                                priority=21,
                            )
                        self._emit(
                            EventPhase.ATTACK,
                            "enemy_energy_stolen",
                            instance_id,
                            {
                                "amount": taken,
                                "source_key": "steal_energy.steal_count",
                            },
                            priority=21,
                        )
                    remaining = self._seconds_micros_to_ticks(interval)
                update["energy_steal_remaining_ticks"] = remaining
            stack_rows = attack_stack_rows(self._enemy_defs[enemy.enemy_id])
            if (
                idle_speed_rows(self._enemy_defs[enemy.enemy_id]) is not None
                or stack_rows is not None
            ):
                # 「一定时间不攻击后」 -- the window is time spent *not* attacking,
                # so this counts up every tick and the attack path resets it.
                update["no_attack_idle_ticks"] = runtime.no_attack_idle_ticks + 1
            if self._enemy_defs[enemy.enemy_id].devour is not None:
                update.update(self._resolve_devour(instance_id, enemy))
            conditional = self._enemy_defs[enemy.enemy_id].conditional_takeoff
            if conditional is not None:
                update.update(
                    self._advance_takeoff_detector(instance_id, enemy, runtime, conditional)
                )
            takeoff = self._enemy_defs[enemy.enemy_id].takeoff
            if takeoff is not None:
                # The two 90% windows run on their own clocks: the first inside
                # the flight, the second after it has ended.
                if runtime.takeoff_slow_remaining_ticks:
                    update["takeoff_slow_remaining_ticks"] = (
                        runtime.takeoff_slow_remaining_ticks - 1
                    )
                if runtime.landing_slow_remaining_ticks:
                    update["landing_slow_remaining_ticks"] = (
                        runtime.landing_slow_remaining_ticks - 1
                    )
                if runtime.airborne_remaining_ticks:
                    remaining_flight = runtime.airborne_remaining_ticks - 1
                    update["airborne_remaining_ticks"] = remaining_flight
                    if not remaining_flight:
                        # 「飞行模式持续7秒；**结束后，1.333秒内不可阻挡，移动速度
                        # 最终降低90%**」 -- the landing is its own window, not a
                        # clean return to the ground.
                        update["landing_slow_remaining_ticks"] = (
                            takeoff.landing_slow_ticks
                        )
                        self._emit(
                            EventPhase.MOVE,
                            "enemy_landed",
                            instance_id,
                            {
                                "landing_slow_ticks": takeoff.landing_slow_ticks,
                                "source_key": "TakeOff",
                            },
                            priority=1,
                        )
                elif runtime.takeoff_cooldown_remaining_ticks:
                    update["takeoff_cooldown_remaining_ticks"] = (
                        runtime.takeoff_cooldown_remaining_ticks - 1
                    )
                elif enemy.blocked_by is not None:
                    # PRTS 「被阻挡时获取升空检测器…检测到被阻挡时切换至飞行模式」.
                    # The block is released here rather than waiting for the
                    # next reconciliation, because 「无法被阻挡」 starts now.
                    update["airborne_remaining_ticks"] = takeoff.duration_ticks
                    update["takeoff_cooldown_remaining_ticks"] = (
                        takeoff.cooldown_ticks
                    )
                    # 「切换为飞行模式后，1.5秒内移动速度最终降低90%」
                    update["takeoff_slow_remaining_ticks"] = (
                        takeoff.takeoff_slow_ticks
                    )
                    self._release_block(enemy)
                    self._emit(
                        EventPhase.MOVE,
                        "enemy_took_off",
                        instance_id,
                        {
                            "duration_ticks": takeoff.duration_ticks,
                            "blocked_by": enemy.blocked_by,
                            "source_key": "TakeOff",
                        },
                        priority=1,
                    )
            if stack_rows is not None and runtime.attack_stacks:
                idle = update.get("no_attack_idle_ticks", runtime.no_attack_idle_ticks)
                if idle >= self._seconds_micros_to_ticks(stack_rows["delay"]):
                    # 「4秒不攻击后清空」 -- cleared outright, not decayed.
                    update["attack_stacks"] = 0
            if runtime.ember_remaining_ticks:
                if runtime.ember_transition_remaining_ticks:
                    update["ember_transition_remaining_ticks"] = (
                        runtime.ember_transition_remaining_ticks - 1
                    )
                remaining = runtime.ember_remaining_ticks - 1
                update["ember_remaining_ticks"] = remaining
                if not remaining:
                    # 「10s后若未被击倒则变回战士形态并恢复所有生命」
                    update["ember_transition_remaining_ticks"] = 0
            victory = self._enemy_defs[enemy.enemy_id].victory_buff
            if victory is not None and not runtime.victory_active:
                if runtime.victory_delay_remaining_ticks:
                    update["victory_delay_remaining_ticks"] = (
                        runtime.victory_delay_remaining_ticks - 1
                    )
                elif self._victory_partner_departed(enemy):
                    update["victory_active"] = True
                    self._emit(
                        # No upkeep phase exists, and the trigger *is* a
                        # departure -- the same phase paired rage emits in, so
                        # the two partner-exit mechanics order together.
                        EventPhase.DEATH,
                        "enemy_victory_triggered",
                        enemy.instance_id,
                        {
                            "enemy_id": enemy.enemy_id,
                            "source_key": "Victory",
                        },
                        priority=1,
                    )
            if runtime.charged_skill_cooldown_ticks:
                update["charged_skill_cooldown_ticks"] = (
                    runtime.charged_skill_cooldown_ticks - 1
                )
            if runtime.self_damage_remaining_ticks:
                remaining = runtime.self_damage_remaining_ticks - 1
                if remaining == 0:
                    definition = self._enemy_defs[enemy.enemy_id]
                    amount = coefficient_value(
                        definition, "periodic_damage.damage"
                    )
                    if amount is not None:
                        loss = amount // 1_000_000
                        source_key = "periodic_damage.damage"
                    else:
                        # 「每秒流失最大生命值×4%的生命值」 -- the ratio form, so
                        # the loss is of the carrier's **max** HP and stays the
                        # same as it dies rather than tapering off.
                        ratio = coefficient_value(definition, "Bleed.hp_ratio")
                        if ratio is None:
                            raise UnsupportedMechanicError(
                                "periodic self damage lost its amount at runtime"
                            )
                        loss = definition.max_hp * ratio // 1_000_000
                        source_key = "Bleed.hp_ratio"
                    # 「无来源真实伤害」: true damage from nobody, so it does not
                    # go through the damage-intent path at all -- no defence, no
                    # declared resistance, and nothing that reacts to an attack.
                    self.enemies[instance_id] = enemy.model_copy(
                        update={"hp": max(0, enemy.hp - loss)}
                    )
                    enemy = self.enemies[instance_id]
                    self._emit(
                        EventPhase.DAMAGE,
                        "enemy_periodic_self_damage",
                        instance_id,
                        {
                            "amount": loss,
                            "remaining_hp": enemy.hp,
                            "source_key": source_key,
                        },
                    )
                    remaining = self.content.ticks_per_second
                update["self_damage_remaining_ticks"] = remaining
            ramp = rush_acceleration_rows(self._enemy_defs[enemy.enemy_id])
            if ramp:
                # PRTS words 「萨卡兹穿刺手」 and 「凶豕兽」 identically:
                # 「每0.1秒检测一次：①若受晕眩/束缚影响，停止加速 ②若未被阻挡，
                # 开始加速」, 「增益于停止加速时解除」, 「登场后的0秒内/每次开始
                # 冲撞后3秒内的加速尝试将失败」.  Three readings follow, and the
                # earlier halt-and-park model had all three the other way round:
                #
                # * **Blocking does not stop a charge already running.**  ② is a
                #   *start* condition, re-evaluated by the poll; nothing in ①
                #   mentions blocking.  This is why the blocked attack hits so
                #   hard -- the carrier keeps accelerating while it is held.
                # * **Whatever does stop it releases the buff**, so the stacks
                #   are lost rather than parked for later.
                # * **The stand-still delay is per charge**, not per spawn.
                #   穿刺手 ships 0 for it, so only 凶豕兽 (3 s / 5 s) can tell the
                #   two readings apart.
                #
                # The poll runs every tick here rather than every 0.1 s, the same
                # frame-boundary registration the burst already carries.  On the
                # enemy side only 晕眩 has a representation: 冻结/浮空/束缚 have no
                # enemy-facing field, so ① is implemented on the one it does have.
                full_interval = self._ramp_interval_ticks(ramp["interval"])
                predelay_source = ramp.get("predelay")
                full_predelay = (
                    self._seconds_micros_to_ticks(predelay_source)
                    if predelay_source
                    else 0
                )
                charging = runtime.rush_charging
                stacks = runtime.rush_acceleration_stacks
                predelay_left = runtime.rush_predelay_remaining_ticks
                interval_left = runtime.rush_interval_remaining_ticks
                armed = runtime.rush_first_attack_armed
                if enemy.talent_stun_remaining_ticks:
                    charging = False
                    stacks = 0
                    predelay_left = full_predelay
                    interval_left = full_interval
                elif not charging and enemy.blocked_by is None:
                    charging = True
                    armed = True
                    predelay_left = full_predelay
                    interval_left = full_interval
                if charging:
                    if predelay_left:
                        predelay_left -= 1
                    else:
                        interval_left -= 1
                        if interval_left == 0:
                            cap = ramp["trig_cnt"] // 1_000_000
                            if stacks < cap:
                                stacks += 1
                            interval_left = full_interval
                update["rush_charging"] = charging
                update["rush_acceleration_stacks"] = stacks
                update["rush_predelay_remaining_ticks"] = predelay_left
                update["rush_interval_remaining_ticks"] = interval_left
                update["rush_first_attack_armed"] = armed
            if (
                runtime.cannon_skill_cooldown_remaining_ticks
                and not runtime.cannon_barrage_remaining_shots
            ):
                update["cannon_skill_cooldown_remaining_ticks"] = (
                    runtime.cannon_skill_cooldown_remaining_ticks - 1
                )
            phase = runtime.identity_detection_phase
            if phase in {"phase1", "phase2"}:
                remaining = runtime.identity_detection_trigger_remaining_ticks - 1
                if remaining:
                    update["identity_detection_trigger_remaining_ticks"] = remaining
                else:
                    blocked = enemy.blocked_by is not None
                    blocked_delta = (
                        MAINLINE12_DETECTION_PHASE1_BLOCKED_DELTA
                        if phase == "phase1"
                        else MAINLINE12_DETECTION_PHASE2_BLOCKED_DELTA
                    )
                    stacks = max(
                        0,
                        runtime.identity_detection_stacks
                        + (
                            blocked_delta
                            if blocked
                            else MAINLINE12_DETECTION_UNBLOCKED_DELTA
                        ),
                    )
                    threshold = (
                        MAINLINE12_DETECTION_PHASE1_THRESHOLD
                        if phase == "phase1"
                        else MAINLINE12_DETECTION_PHASE2_THRESHOLD
                    )
                    if stacks >= threshold:
                        next_phase = "phase2" if phase == "phase1" else "switching"
                        update.update(
                            {
                                "identity_detection_phase": next_phase,
                                "identity_detection_stacks": 0,
                                "identity_detection_trigger_remaining_ticks": (
                                    MAINLINE12_DETECTION_INTERVAL_TICKS
                                    if next_phase == "phase2"
                                    else MAINLINE12_SWITCH_TRIGGER_TICKS
                                ),
                            }
                        )
                        self._emit(
                            EventPhase.BLOCK,
                            "enemy_identity_detection_phase_completed",
                            instance_id,
                            {
                                "completed_phase": phase,
                                "next_phase": next_phase,
                                "blocked": blocked,
                            },
                        )
                    else:
                        update.update(
                            {
                                "identity_detection_stacks": stacks,
                                "identity_detection_trigger_remaining_ticks": (
                                    MAINLINE12_DETECTION_INTERVAL_TICKS
                                ),
                            }
                        )
            elif phase == "switching":
                remaining = runtime.identity_detection_trigger_remaining_ticks - 1
                if remaining:
                    update["identity_detection_trigger_remaining_ticks"] = remaining
                else:
                    update.update(
                        {
                            "identity_detection_phase": "revealed",
                            "identity_detection_trigger_remaining_ticks": 0,
                            "enhanced_attack_pending": (
                                enemy.enemy_id in MAINLINE12_ENHANCED_ATTACK_IDS
                            ),
                            "revealed_invisible": (
                                enemy.enemy_id in MAINLINE12_REVEALED_INVISIBLE_IDS
                            ),
                        }
                    )
                    self._emit(
                        EventPhase.BLOCK,
                        "enemy_identity_revealed",
                        instance_id,
                        {
                            "enemy_id": enemy.enemy_id,
                            "enhanced_attack_pending": (
                                enemy.enemy_id in MAINLINE12_ENHANCED_ATTACK_IDS
                            ),
                            "revealed_invisible": (
                                enemy.enemy_id in MAINLINE12_REVEALED_INVISIBLE_IDS
                            ),
                        },
                    )
            if update:
                enemy_update: dict[str, object] = {
                    "coefficient_runtime": runtime.model_copy(update=update)
                }
                if (
                    runtime.ember_remaining_ticks
                    and update.get("ember_remaining_ticks") == 0
                ):
                    # 「10s后若未被击倒则变回战士形态并恢复所有生命」 -- the ember's
                    # five points were only its temporary pool; leaving the form
                    # restores the warrior whole.
                    enemy_update["hp"] = self._enemy_defs[enemy.enemy_id].max_hp
                    self._emit(
                        # No upkeep phase exists; the form change is a death /
                        # revival transition, which is where its sibling
                        # (death transform) also reports.
                        EventPhase.DEATH,
                        "enemy_ember_form_left",
                        instance_id,
                        {
                            "enemy_id": enemy.enemy_id,
                            "restored_hp": self._enemy_defs[enemy.enemy_id].max_hp,
                            "source_key": "Revive[Trigger].interval",
                        },
                        priority=1,
                    )
                self.enemies[instance_id] = enemy.model_copy(update=enemy_update)
        for instance_id, operator in tuple(self.operators.items()):
            cooldowns = {
                field: getattr(operator, field) - 1
                for field in (
                    "element_neural_cooldown_ticks",
                    "element_erosion_cooldown_ticks",
                    "element_burn_cooldown_ticks",
                )
                if getattr(operator, field)
            }
            if cooldowns:
                self.operators[instance_id] = operator.model_copy(update=cooldowns)
                operator = self.operators[instance_id]
            if operator.coefficient_cold_remaining_ticks:
                self.operators[instance_id] = operator.model_copy(
                    update={
                        "coefficient_cold_remaining_ticks": (
                            operator.coefficient_cold_remaining_ticks - 1
                        )
                    }
                )
                operator = self.operators[instance_id]
            if operator.block_reduction_remaining_ticks:
                remaining = operator.block_reduction_remaining_ticks - 1
                self.operators[instance_id] = operator.model_copy(
                    update=(
                        {"block_reduction_remaining_ticks": remaining}
                        if remaining
                        else {
                            "block_reduction_remaining_ticks": 0,
                            "block_reduction": 0,
                        }
                    )
                )
                operator = self.operators[instance_id]
            if operator.death_buff_remaining_ticks:
                remaining = operator.death_buff_remaining_ticks - 1
                self.operators[instance_id] = operator.model_copy(
                    update=(
                        {"death_buff_remaining_ticks": remaining}
                        if remaining
                        else {
                            "death_buff_remaining_ticks": 0,
                            "death_buff_attack_speed_points_micros": 0,
                            "death_buff_defense_multiplier_milli": 1000,
                        }
                    )
                )
                operator = self.operators[instance_id]
            if operator.coefficient_stun_remaining_ticks:
                self.operators[instance_id] = operator.model_copy(
                    update={
                        "coefficient_stun_remaining_ticks": (
                            operator.coefficient_stun_remaining_ticks - 1
                        ),
                        "coefficient_frozen_remaining_ticks": max(
                            0, operator.coefficient_frozen_remaining_ticks - 1
                        ),
                    }
                )
        for instance_id, summon in tuple(self.summons.items()):
            cooldowns = {
                field: getattr(summon, field) - 1
                for field in (
                    "element_neural_cooldown_ticks",
                    "element_erosion_cooldown_ticks",
                    "element_burn_cooldown_ticks",
                )
                if getattr(summon, field)
            }
            if cooldowns:
                self.summons[instance_id] = summon.model_copy(update=cooldowns)
                summon = self.summons[instance_id]
            if summon.coefficient_cold_remaining_ticks:
                self.summons[instance_id] = summon.model_copy(
                    update={
                        "coefficient_cold_remaining_ticks": (
                            summon.coefficient_cold_remaining_ticks - 1
                        )
                    }
                )
                summon = self.summons[instance_id]
            if summon.block_reduction_remaining_ticks:
                remaining = summon.block_reduction_remaining_ticks - 1
                self.summons[instance_id] = summon.model_copy(
                    update=(
                        {"block_reduction_remaining_ticks": remaining}
                        if remaining
                        else {
                            "block_reduction_remaining_ticks": 0,
                            "block_reduction": 0,
                        }
                    )
                )
                summon = self.summons[instance_id]
            if summon.death_buff_remaining_ticks:
                remaining = summon.death_buff_remaining_ticks - 1
                self.summons[instance_id] = summon.model_copy(
                    update=(
                        {"death_buff_remaining_ticks": remaining}
                        if remaining
                        else {
                            "death_buff_remaining_ticks": 0,
                            "death_buff_attack_speed_points_micros": 0,
                            "death_buff_defense_multiplier_milli": 1000,
                        }
                    )
                )
                summon = self.summons[instance_id]
            if summon.coefficient_stun_remaining_ticks:
                self.summons[instance_id] = summon.model_copy(
                    update={
                        "coefficient_stun_remaining_ticks": (
                            summon.coefficient_stun_remaining_ticks - 1
                        ),
                        "coefficient_frozen_remaining_ticks": max(
                            0, summon.coefficient_frozen_remaining_ticks - 1
                        ),
                    }
                )
        return expired_lifetime

    @staticmethod
    def _enemy_cold_update(
        actor: OperatorState | SummonState,
        ticks: int,
    ) -> dict[str, object]:
        """State change for one application of 敌方寒冷 to a friendly actor.

        PRTS 异常效果图鉴: cold is attack speed -30, and a further cold applied
        while the actor is **already cold or already frozen** lands as 冻结
        instead -- which is the hold.  So the first hit slows and the second
        locks, rather than every hit locking.

        Frozen writes the shared "cannot act" countdown as well, because that is
        what the rest of the engine reads to stop an actor.
        """

        already = bool(
            actor.coefficient_cold_remaining_ticks
            or actor.coefficient_frozen_remaining_ticks
        )
        if not already:
            return {
                "coefficient_cold_remaining_ticks": max(
                    actor.coefficient_cold_remaining_ticks, ticks
                )
            }
        return {
            "coefficient_cold_remaining_ticks": 0,
            "coefficient_frozen_remaining_ticks": max(
                actor.coefficient_frozen_remaining_ticks, ticks
            ),
            "coefficient_stun_remaining_ticks": max(
                actor.coefficient_stun_remaining_ticks, ticks
            ),
        }

    def _max_control_ticks(self) -> int:
        """Longest "cannot act" countdown this bundle's own data can produce.

        Derived from the content rather than hardcoded.  The constant this
        replaced was 120 ticks (4.0 s), which was true of the attack-borne
        control keys alone; once ``FreezeAura.freeze`` (4.5 s = 135 ticks) was
        implemented, any snapshot taken while an aura freeze was running could
        no longer be restored -- the gate rejected a state the engine itself had
        just produced.  A bound that names its sources cannot go stale that way.
        """

        keys = frozenset(ATTACK_CONTROL_KEYS) | {"FreezeAura.freeze"}
        longest = 0
        for enemy in self.content.enemies:
            for key in sorted(keys):
                value = coefficient_value(enemy, key)
                if value is not None:
                    longest = max(longest, self._seconds_micros_to_ticks(value))
        return longest

    def _friendly_max_hp(self, actor: OperatorState | SummonState) -> int:
        if isinstance(actor, OperatorState):
            return self._operator_defs[actor.operator_id].max_hp
        return self._summon_defs[actor.summon_id].max_hp

    def _friendly_magic_resistance(self, actor: OperatorState | SummonState) -> int:
        if isinstance(actor, OperatorState):
            return self._operator_defs[actor.operator_id].magic_resistance
        return self._summon_defs[actor.summon_id].magic_resistance

    @staticmethod
    def _friendly_target_side(
        actor: OperatorState | SummonState,
    ) -> Literal["operator", "summon"]:
        return "operator" if isinstance(actor, OperatorState) else "summon"

    def _create_polluted_zone(
        self,
        source: EnemyState,
        *,
        center_col_milli: int,
        center_row_milli: int,
        coefficient_prefix: Literal["PollutedDie", "PollutedRangedAtk"],
    ) -> str:
        definition = self._enemy_defs[source.enemy_id]
        radius_key = (
            "projectile_range"
            if coefficient_prefix == "PollutedDie"
            else "range_radius"
        )
        radius = coefficient_value(definition, f"{coefficient_prefix}.{radius_key}")
        lifetime = coefficient_value(
            definition, f"{coefficient_prefix}.projectile_life_time"
        )
        low_damage = coefficient_value(
            definition, f"{coefficient_prefix}.polluted_damage_low"
        )
        high_damage = coefficient_value(
            definition, f"{coefficient_prefix}.polluted_damage_high"
        )
        values = (radius, lifetime, low_damage, high_damage)
        if any(value is None for value in values):
            raise UnsupportedMechanicError(
                "Return Body pollution matrix disappeared at runtime"
            )
        if cast(int, low_damage) % 1_000_000 or cast(int, high_damage) % 1_000_000:
            raise UnsupportedMechanicError(
                "Return Body pollution damage is not a whole HP value"
            )
        if cast(int, radius) % 1000:
            raise UnsupportedMechanicError(
                "Return Body pollution radius is not representable in milli-tiles"
            )
        self.entity_sequence += 1
        instance_id = f"pollution-{self.entity_sequence:06d}"
        zone = PollutedZoneState(
            instance_id=instance_id,
            source_enemy_id=source.enemy_id,
            center_col_milli=center_col_milli,
            center_row_milli=center_row_milli,
            radius_milli_tiles=cast(int, radius) // 1000,
            remaining_ticks=self._seconds_micros_to_ticks(cast(int, lifetime)),
            pulse_remaining_ticks=RETURN_BODY_POLLUTION_PULSE_TICKS,
            low_ground_damage=cast(int, low_damage) // 1_000_000,
            high_ground_damage=cast(int, high_damage) // 1_000_000,
        )
        self.polluted_zones[instance_id] = zone
        self._emit(
            EventPhase.ATTACK,
            "polluted_zone_created",
            instance_id,
            {
                "source_instance_id": source.instance_id,
                "source_enemy_id": source.enemy_id,
                "coefficient_prefix": coefficient_prefix,
                "center_col_milli": center_col_milli,
                "center_row_milli": center_row_milli,
                "radius_milli_tiles": zone.radius_milli_tiles,
                "duration_ticks": zone.remaining_ticks,
            },
            priority=1,
        )
        return instance_id

    def _advance_energy_charge(
        self,
        instance_id: str,
        enemy: EnemyState,
        runtime: Any,
        schedule: Any,
    ) -> dict[str, object]:
        """One tick of 匪帮射手's charging cycle.

        「周期性停止移动，并为攻击范围内所有敌人持续提供能量」: a
        5-second window every 28 seconds, granting one point a second inside it,
        with the first window at 5 seconds.  The grant lands on the tick the
        window opens and every `interval` after, so a 5-second window at one
        second apart grants five times -- the frame boundary at the closing edge
        is the one thing the data does not state, and it is registered rather
        than presented as source-exact.
        """

        update: dict[str, object] = {}
        window = runtime.energy_charge_window_remaining_ticks
        grant_in = runtime.energy_charge_remaining_ticks
        if not window:
            cycle = runtime.energy_charge_cycle_remaining_ticks
            if not cycle:
                return update
            cycle -= 1
            update["energy_charge_cycle_remaining_ticks"] = cycle
            if cycle:
                return update
            # The window opens on this very tick and its first grant lands with
            # it, so a 5-second window at one second apart grants five times.
            # Opening on one tick and granting on the next would quietly make it
            # four.
            window = schedule.duration_ticks
            grant_in = 0
            self._emit(
                EventPhase.ATTACK,
                "enemy_energy_charge_window_opened",
                instance_id,
                {
                    "duration_ticks": schedule.duration_ticks,
                    "interval_ticks": schedule.interval_ticks,
                    "amount": schedule.amount,
                    "radius_milli_tiles": schedule.radius_milli_tiles,
                },
                priority=21,
            )

        if grant_in <= 0:
            for target_id in self._energy_holders_near(
                enemy, schedule.radius_milli_tiles, ground_only=False
            ):
                self._grant_energy(target_id, schedule.amount)
            # One short, because this tick is the first of the wait: `interval`
            # ticks from a grant to the next grant, not `interval` + 1.
            grant_in = schedule.interval_ticks - 1
        else:
            grant_in -= 1
        window -= 1
        update["energy_charge_window_remaining_ticks"] = window
        update["energy_charge_remaining_ticks"] = grant_in
        if window == 0:
            update["energy_charge_remaining_ticks"] = 0
            update["energy_charge_cycle_remaining_ticks"] = max(
                1, schedule.cooldown_ticks - schedule.duration_ticks
            )
            self._emit(
                EventPhase.ATTACK,
                "enemy_energy_charge_window_closed",
                instance_id,
                {
                    "next_window_in_ticks": update[
                        "energy_charge_cycle_remaining_ticks"
                    ]
                },
                priority=21,
            )
        return update

    def _energy_cap(self, definition: EnemyDefinition) -> int:
        cap = coefficient_value(definition, "blacksteel_energy.max_stack_cnt")
        if cap is None:
            raise UnsupportedMechanicError("energy pool lost its cap at runtime")
        return cap // 1_000_000

    def _grant_energy(self, target_id: str, amount: int) -> None:
        """Add energy to one enemy, arming its overload when the pool fills."""

        enemy = self.enemies.get(target_id)
        if enemy is None or enemy.coefficient_runtime is None:
            return
        definition = self._enemy_defs[enemy.enemy_id]
        if not any(
            item.key in ENERGY_KEYS for item in definition.mechanic_coefficients
        ):
            return
        runtime = enemy.coefficient_runtime
        if runtime.overload_remaining_ticks:
            # Already overloaded.  PRTS has the pool reset when overload ends,
            # so topping it up meanwhile would let a second overload start the
            # instant the first expires.
            return
        cap = self._energy_cap(definition)
        filled = min(cap, max(0, runtime.energy_stacks + amount))
        update: dict[str, object] = {"energy_stacks": filled}
        if filled >= cap:
            duration = coefficient_value(definition, "over_energy.duration")
            if duration is None:
                raise UnsupportedMechanicError("overload lost its duration at runtime")
            update["overload_remaining_ticks"] = self._seconds_micros_to_ticks(duration)
            self._emit(
                EventPhase.ATTACK,
                "enemy_overload_started",
                target_id,
                {
                    "duration_ticks": update["overload_remaining_ticks"],
                    "source_key": "over_energy.duration",
                },
                priority=21,
            )
        self.enemies[target_id] = enemy.model_copy(
            update={"coefficient_runtime": runtime.model_copy(update=update)}
        )

    def _energy_holders_near(
        self,
        origin: EnemyState,
        radius_milli: int,
        *,
        ground_only: bool,
    ) -> list[str]:
        """Other energy-holding enemies within ``radius_milli`` of ``origin``."""

        col_milli, row_milli = self._enemy_position_milli(origin)
        found: list[str] = []
        for other_id in sorted(self.enemies):
            if other_id == origin.instance_id:
                continue
            other = self.enemies[other_id]
            definition = self._enemy_defs[other.enemy_id]
            if not any(
                item.key in ENERGY_KEYS for item in definition.mechanic_coefficients
            ):
                continue
            if ground_only and definition.movement_layer is MovementLayer.FLYING:
                continue
            other_col, other_row = self._enemy_position_milli(other)
            delta_col = other_col - col_milli
            delta_row = other_row - row_milli
            if (
                delta_col * delta_col + delta_row * delta_row
                <= radius_milli * radius_milli
            ):
                found.append(other_id)
        return found

    def _enrage_rage_partners(self, departed: EnemyState, reason: str) -> None:
        """Raise rage on the partner of a carrier that has left the board.

        PRTS 「霜牡」: 「'雪牝'**死亡/进入终点时**」 -- either exit arms the survivor,
        so this is called from both the death path and the leak path.  Missing
        the leak case would leave the survivor permanently weak in exactly the
        run where one was let through.

        The pair needs no id table: both carriers ship the same `triggerrage`
        rows, so a partner is any other carrier of the family.
        """

        departed_definition = self._enemy_defs[departed.enemy_id]
        if not any(
            item.key in RAGE_KEYS
            for item in departed_definition.mechanic_coefficients
        ):
            return
        for instance_id in sorted(self.enemies):
            survivor = self.enemies[instance_id]
            if survivor.enemy_id == departed.enemy_id:
                # A second copy of the same enemy is not the partner.
                continue
            definition = self._enemy_defs[survivor.enemy_id]
            if not any(
                item.key in RAGE_KEYS for item in definition.mechanic_coefficients
            ):
                continue
            runtime = survivor.coefficient_runtime
            if runtime is None or runtime.rage_active:
                continue
            self.enemies[instance_id] = survivor.model_copy(
                update={
                    "coefficient_runtime": runtime.model_copy(
                        update={"rage_active": True}
                    )
                }
            )
            self._emit(
                EventPhase.DEATH,
                "enemy_rage_triggered",
                instance_id,
                {
                    "departed_instance_id": departed.instance_id,
                    "departed_enemy_id": departed.enemy_id,
                    "reason": reason,
                    "source_key": "triggerrage.attack_speed",
                },
                priority=1,
            )

    def _victory_partner_departed(self, enemy: EnemyState) -> bool:
        """Has this carrier's 决胜 partner been on the board and left it?

        PRTS phrases the condition as a *state* -- 「仅<对方>已退场时可触发1次」 --
        not as an event, so a carrier entering after its partner is already gone
        qualifies too.  An event hook alone would miss exactly that ordering.

        Deriving it from the consumed spawns and the live roster needs no new
        snapshot field, so checkpoint restore keeps working unchanged: a partner
        has departed when its spawn was consumed and no instance of it stands.
        """

        live = {other.enemy_id for other in self.enemies.values()}
        for item in self._scheduled:
            if item.occurrence_id not in self.spawned_spawn_ids:
                continue
            if item.enemy_id == enemy.enemy_id or item.enemy_id in live:
                continue
            if self._enemy_defs[item.enemy_id].victory_buff is not None:
                return True
        return False

    def _friendly_taunt_level(self, target: Any) -> int:
        """The taunt tier of a friendly target, or zero.

        Only summons declare one: 盾卫's 列阵！ ships ``taunt_level 1`` with a
        permanent duration, and it stands in all 63 IS3 stages.
        """

        summon_id = getattr(target, "summon_id", None)
        if summon_id is None:
            return 0
        return self._summon_defs[summon_id].taunt_level

    def _airflow_relation(
        self, position: GridPoint, moving: Direction | None
    ) -> tuple[str, Any] | None:
        """Which airflow covers ``position``, and how ``moving`` relates to it.

        源石流发生装置 blows 「向前方3格」 along its own facing, so the
        affected tiles are the up-to-three steps ahead of the device -- not a
        radius, and not including its own tile.

        Returns ``(relation, definition)`` where relation is ``"same"``,
        ``"opposite"`` or ``"vertical"``, or None when no airflow reaches the
        tile.  An actor with no direction of its own reads as ``"vertical"``:
        the shipped `[vertical]` figure is exactly 0.0, so "no relation to the
        flow" and "across the flow" already mean the same thing in the data.
        """

        if not self._airflow_instances:
            return None
        for summon in self.summons.values():
            definition = self._summon_defs.get(summon.summon_id)
            if (
                definition is None
                or not definition.airflow_range_tiles
                or summon.facing is None
            ):
                continue
            step = DIRECTION_STEPS[summon.facing]
            covered = False
            for distance in range(1, definition.airflow_range_tiles + 1):
                if (
                    summon.position.col + step[0] * distance == position.col
                    and summon.position.row + step[1] * distance == position.row
                ):
                    covered = True
                    break
            if not covered:
                continue
            if moving is None:
                return "vertical", definition
            if moving is summon.facing:
                return "same", definition
            if DIRECTION_STEPS[moving] == (-step[0], -step[1]):
                return "opposite", definition
            return "vertical", definition
        return None

    def _enemy_step_direction(self, enemy: EnemyState) -> Direction | None:
        """The cardinal step an enemy is currently walking, if it is walking.

        Built on the facing the engine already derives from the route, so the
        airflow and every other facing-sensitive mechanic answer from one place.
        A blocked enemy, one at the end of its path, and one stepping diagonally
        all read as None -- which the airflow treats as across the flow, where
        the shipped figure is 0.0 anyway.
        """

        if enemy.blocked_by is not None:
            return None
        facing = self._enemy_facing_milli(enemy)
        if facing is None or (facing[0] and facing[1]):
            return None
        unit = tuple(
            (0 if value == 0 else (1 if value > 0 else -1)) for value in facing
        )
        return STEP_DIRECTIONS.get(unit)

    def _device_max_cost_ceiling(self) -> int:
        """Total cap increase the devices **placed on this map** could grant.

        Counted per placed instance, over both ways a device reaches the board,
        and without regard to whether it has died yet: a snapshot does not
        record which devices were destroyed, so the ceiling is what the board
        could have paid out in total.
        """

        total = 0
        for token in self.content.predefined_tokens:
            definition = self._summon_defs.get(token.summon_id)
            if definition is not None:
                total += definition.death_max_cost_gain
        for summon in self.content.initial_summons:
            definition = self._summon_defs.get(summon.summon_id)
            if definition is not None:
                total += definition.death_max_cost_gain
        return total

    def _apply_summon_death_effects(self, instance_id: str, summon: Any) -> None:
        """A destroyed device's own consequence, from its shipped talent.

        指挥终端 (`trap_008_farm`, IS3 2-6): 「被摧毁时扣除 1 点关卡生命值」 --
        the only friendly unit in the topic whose loss costs life directly, so a
        stage placing it has a second lose condition on the board.

        街头应急储备 (`trap_111_wdfarm`, IS3 2-4): 「被击败后恢复 5 点费用上限
        及费用」 -- the cap rises *and* the pool is credited, and the credit is
        clamped to the new cap the same way every other cost gain is.
        """

        definition = self._summon_defs.get(summon.summon_id)
        if definition is None:
            return
        if definition.death_life_point_cost:
            damage = definition.death_life_point_cost
            self.life_points = max(0, self.life_points - damage)
            self._emit(
                EventPhase.DEATH,
                "summon_death_life_damage",
                instance_id,
                {
                    "summon_id": summon.summon_id,
                    "life_damage": damage,
                    "life_points": self.life_points,
                },
                priority=2,
            )
        if definition.death_max_cost_gain or definition.death_cost_gain:
            self.max_cost_bonus += definition.death_max_cost_gain
            before = self.cost
            self.cost = min(
                self.effective_max_cost, self.cost + definition.death_cost_gain
            )
            self._emit(
                EventPhase.DEATH,
                "summon_death_cost_restored",
                instance_id,
                {
                    "summon_id": summon.summon_id,
                    "max_cost_delta": definition.death_max_cost_gain,
                    "max_cost_bonus": self.max_cost_bonus,
                    "effective_max_cost": self.effective_max_cost,
                    "cost_before": before,
                    "cost": self.cost,
                },
                priority=2,
            )

    def _rush_contact_damage(self, enemy: EnemyState) -> tuple[int, int] | None:
        """The two contact hits for a moving 「肇事者」, or None.

        PRTS: 「自主移动时…造成攻击力40%的无途径物理伤害，成功造成伤害后再造成
        当前移动速度×800的预计算近战途径物理伤害」.  「自主移动时」 is the gate:
        a blocked carrier is not moving under its own power, so it deals none.
        """

        definition = self._enemy_defs[enemy.enemy_id]
        rows = rush_contact_rows(definition)
        if rows is None or enemy.blocked_by is not None:
            return None
        attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
        first = _scale_micros(attack, rows["attack_fraction"])
        # 「当前移动速度x800」 -- current speed in tiles per second, so the
        # milli-per-second figure carries the extra thousand.
        speed_milli = self._enemy_move_milli_tiles_per_second(enemy)
        second = speed_milli * (rows["speed_coefficient"] // 1_000_000) // 1000
        return first, second

    def _crash_contact_damage(self, enemy: EnemyState) -> tuple[int, int] | None:
        """凶豕兽's charge damage, or None.

        PRTS: 「自主移动时，对进入自身半径0.5范围内的位于低地的我方单位
        造成当前移动速度×800的预计算近战途径物理伤害与0秒晕眩」.

        「自主移动时」 is the same gate its sibling uses: a blocked carrier is
        not charging, so it deals none.  Returned in the sibling's two-hit shape
        with an empty first hit, because this one lands a single blow and the
        applier is shared.
        """

        definition = self._enemy_defs[enemy.enemy_id]
        scale = crash_contact_rows(definition)
        if scale is None or enemy.blocked_by is not None:
            return None
        # 「**当前**移动速度」 -- the ramp counts.  Taken from the source per-second
        # figure times the accrued acceleration rather than from the per-tick
        # stat, so the payload is not skewed by the per-tick truncation, which
        # is the same reading `firstattack.atk_scale` already follows.
        speed_milli = self._enemy_move_milli_tiles_per_second(enemy)
        runtime = enemy.coefficient_runtime
        ramp = rush_acceleration_rows(definition)
        if ramp and runtime is not None and runtime.rush_acceleration_stacks:
            speed_milli = (
                speed_milli
                * (1_000_000 + ramp["move_speed"] * runtime.rush_acceleration_stacks)
                // 1_000_000
            )
        # A single blow, so it is the **primary** hit: the follow-up slot is the
        # one that only lands behind a successful primary.
        return speed_milli * (scale // 1_000_000) // 1000, 0

    def _apply_rush_contact(
        self,
        instance_id: str,
        enemy: EnemyState,
        contact: tuple[int, int],
        *,
        radius_milli: int = RUSH_CONTACT_RADIUS_MILLI_TILES,
        source_key: str = "Combat.rush_damage",
    ) -> list["_DamageIntent"]:
        """Deal 「肇事者」's two contact hits to friendlies inside 0.3 tiles.

        PRTS gives no cooldown, and firing every tick would be thirty hits a
        second, so a target is hit once per contact episode: its id is kept
        while it stays in radius and dropped when it leaves, which re-arms it.

        The second hit lands only where the first one did -- 「成功造成伤害后再
        造成…」 -- so a target the attack-fraction hit cannot hurt takes nothing.
        """

        # (primary, follow-up).  The follow-up lands only where the primary did
        # -- 肇事者's 「成功造成伤害后再造成…」 -- so a carrier with a single blow
        # passes it as the **primary** and leaves the follow-up empty.  Passing
        # it the other way round silently dealt nothing, which is how this was
        # first written.
        first, second = contact
        runtime = enemy.coefficient_runtime
        assert runtime is not None
        centre = self._enemy_position_milli(enemy)
        radius = radius_milli
        in_radius: list[tuple[str, str]] = []
        for friendly_id, friendly in sorted(self.operators.items()):
            if self._within_milli(centre, friendly.position, radius):
                in_radius.append((friendly_id, "operator"))
        for friendly_id, friendly in sorted(self.summons.items()):
            if self._within_milli(centre, friendly.position, radius):
                in_radius.append((friendly_id, "summon"))

        present = {friendly_id for friendly_id, _side in in_radius}
        already = set(runtime.rush_contact_hit_ids)
        intents: list[_DamageIntent] = []
        fired: set[str] = set()
        for friendly_id, side in in_radius:
            if friendly_id in already:
                continue
            fired.add(friendly_id)
            if first:
                intents.append(_DamageIntent(instance_id, friendly_id, side, first))
                if second:
                    intents.append(
                        _DamageIntent(instance_id, friendly_id, side, second)
                    )
            self._emit(
                EventPhase.DAMAGE,
                "enemy_contact_damage",
                instance_id,
                {
                    "target_id": friendly_id,
                    "primary_damage": first,
                    "follow_up_damage": second,
                    "source_key": source_key,
                },
                priority=20,
            )
        # Keep only ids still in radius, plus the ones just hit: leaving the
        # radius is what re-arms a target.
        remaining = tuple(sorted((already & present) | fired))
        if remaining != runtime.rush_contact_hit_ids:
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "coefficient_runtime": runtime.model_copy(
                        update={"rush_contact_hit_ids": remaining}
                    )
                }
            )
        return intents

    @staticmethod
    def _within_milli(centre: tuple[int, int], position, radius_milli: int) -> bool:
        delta_col = position.col * 1000 - centre[0]
        delta_row = position.row * 1000 - centre[1]
        return delta_col * delta_col + delta_row * delta_row <= radius_milli * radius_milli

    def _resolve_death_unload(self, enemy: EnemyState) -> None:
        """Unload this carrier's passengers when it dies **while blocked**.

        家族专用轿车 「被击倒时若已被阻挡，将卸下三个家族士兵」.  The condition is
        the whole point: a car killed before anything blocked it unloads
        nothing, so a run that stops it at range is meaningfully different from
        one that blocks it -- which is exactly the sort of choice the tournament
        is about.

        The passengers arrive where the car fell, inheriting its route and
        progress, the same way the death *transform* successor does.
        """

        definition = self._enemy_defs[enemy.enemy_id]
        passenger_id = definition.death_unload_enemy_id
        if passenger_id is None or not definition.death_unload_count:
            return
        if enemy.blocked_by is None:
            self._emit(
                EventPhase.DEATH,
                "enemy_death_unload_skipped",
                enemy.instance_id,
                {
                    "reason": "carrier was not blocked",
                    "passenger_enemy_id": passenger_id,
                    "source_key": "DeathRattle.attack@cnt",
                },
                priority=1,
            )
            return
        passenger = self._enemy_defs.get(passenger_id)
        if passenger is None:
            raise UnsupportedMechanicError(
                "death unload passenger disappeared at runtime"
            )
        if enemy.route_teleport_destination_index is not None:
            raise UnsupportedMechanicError(
                "death unload during a route teleport has no exact position"
            )
        unloaded: list[str] = []
        for _index in range(definition.death_unload_count):
            self.entity_sequence += 1
            instance_id = "enemy-%06d" % self.entity_sequence
            self.enemies[instance_id] = EnemyState(
                instance_id=instance_id,
                enemy_id=passenger_id,
                route_id=enemy.route_id,
                waypoint_index=enemy.waypoint_index,
                segment_progress_milli=enemy.segment_progress_milli,
                movement_phase_remainder=enemy.movement_phase_remainder,
                hp=passenger.max_hp,
                path_override=enemy.path_override,
                spawn_occurrence_id=enemy.spawn_occurrence_id,
                coefficient_runtime=self._initial_enemy_coefficient_runtime(passenger),
            )
            unloaded.append(instance_id)
        self._emit(
            EventPhase.DEATH,
            "enemy_death_unload",
            enemy.instance_id,
            {
                "passenger_instance_ids": unloaded,
                "passenger_enemy_id": passenger_id,
                "carrier_enemy_id": enemy.enemy_id,
                "blocked_by": enemy.blocked_by,
                "source_key": "DeathRattle.attack@cnt",
            },
            priority=1,
        )

    def _advance_takeoff_detector(
        self,
        instance_id: str,
        enemy: EnemyState,
        runtime: Any,
        conditional: Any,
    ) -> dict[str, object]:
        """One tick of 弧光突击队's 升空检测器.

        「每 0.2 秒检测一次：当前移动速度超过登场基础速度或 0.9，且处于
        自主移动中，计数器 +0.2；计数器 ≥ 1.0 且未被沉默时强制进入飞行」.

        Two conditions, both required and both checked against what the engine
        can actually see: the effective speed against the threshold, and
        「自主移动中」 as *not blocked and not held*, because a blocked or stunned
        carrier is not moving under its own power however fast its stat says.

        A poll that fails resets the counter rather than pausing it: 「助跑」 is
        a run-up, and a run-up interrupted is a run-up restarted.  The data does
        not say so in as many words, so that reading is registered.

        Landing is control-driven, per PRTS -- silence, stun or daze -- and the
        0.5-second daze that follows is applied as the hold the engine already
        models.
        """

        update: dict[str, object] = {}
        held = self._has_control(
            instance_id, ControlEffect.STUN, ControlEffect.BIND, ControlEffect.DISARM
        )
        if runtime.conditional_airborne:
            if held:
                update["conditional_airborne"] = False
                update["takeoff_detector_progress_ticks"] = 0
                self._emit(
                    EventPhase.MOVE,
                    "enemy_landed",
                    instance_id,
                    {
                        "reason": "control",
                        "daze_ticks": conditional.landing_daze_ticks,
                        "source_key": "passive.fly_speed",
                    },
                    priority=2,
                )
            return update

        if self.tick % conditional.detector_interval_ticks:
            return update
        speed = self._enemy_effective_stat(
            enemy, EffectStat.MOVE_MILLI_TILES_PER_TICK
        )
        threshold = max(
            1,
            conditional.speed_threshold_milli_tiles_per_second
            // self.content.ticks_per_second,
        )
        moving = enemy.blocked_by is None and not held
        if not moving or speed <= threshold:
            if runtime.takeoff_detector_progress_ticks:
                update["takeoff_detector_progress_ticks"] = 0
            return update
        progress = (
            runtime.takeoff_detector_progress_ticks
            + conditional.detector_interval_ticks
        )
        if progress < conditional.detector_required_ticks:
            update["takeoff_detector_progress_ticks"] = progress
            return update
        update["takeoff_detector_progress_ticks"] = 0
        update["conditional_airborne"] = True
        self._emit(
            EventPhase.MOVE,
            "enemy_took_off",
            instance_id,
            {
                "trigger": "speed_above_threshold",
                "speed_milli_tiles_per_tick": speed,
                "threshold_milli_tiles_per_tick": threshold,
                "source_key": "passive.fly_speed",
            },
            priority=2,
        )
        return update

    def _advance_environmental_fragile(self, instance_id: str) -> None:
        """Run down 「10s 100%的脆弱」."""

        enemy = self.enemies[instance_id]
        runtime = enemy.coefficient_runtime
        if runtime is None or not runtime.environmental_fragile_remaining_ticks:
            return
        self.enemies[instance_id] = enemy.model_copy(
            update={
                "coefficient_runtime": runtime.model_copy(
                    update={
                        "environmental_fragile_remaining_ticks": (
                            runtime.environmental_fragile_remaining_ticks - 1
                        )
                    }
                )
            }
        )

    def _advance_self_rage(self, instance_id: str) -> None:
        """Fire 「生命值首次低于X%」, and let it run out if it has a duration.

        Both carriers word it as 首次, so this is a latch: healing back over the
        line does not re-arm it, and the buff that has a duration simply ends.
        """

        enemy = self.enemies[instance_id]
        definition = self._enemy_defs[enemy.enemy_id]
        rage = definition.self_rage
        if rage is None:
            return
        runtime = enemy.coefficient_runtime
        if runtime is None:
            raise UnsupportedMechanicError(
                "self rage carrier lacks deterministic runtime state"
            )
        if runtime.self_rage_active:
            if not rage.duration_ticks:
                return
            remaining = runtime.self_rage_remaining_ticks - 1
            update = {"self_rage_remaining_ticks": max(0, remaining)}
            if remaining <= 0:
                update["self_rage_active"] = False
                self._emit(
                    EventPhase.MOVE,
                    "enemy_self_rage_ended",
                    instance_id,
                    {"enemy_id": enemy.enemy_id},
                    priority=3,
                )
            self.enemies[instance_id] = enemy.model_copy(
                update={"coefficient_runtime": runtime.model_copy(update=update)}
            )
            return
        if runtime.self_rage_spent:
            return
        if enemy.hp * 1000 >= rage.hp_threshold_milli * definition.max_hp:
            return
        self.enemies[instance_id] = enemy.model_copy(
            update={
                "coefficient_runtime": runtime.model_copy(
                    update={
                        "self_rage_active": True,
                        "self_rage_spent": True,
                        "self_rage_remaining_ticks": rage.duration_ticks,
                    }
                )
            }
        )
        self._emit(
            EventPhase.MOVE,
            "enemy_self_rage_started",
            instance_id,
            {
                "enemy_id": enemy.enemy_id,
                "attack_speed_bonus": rage.attack_speed_bonus,
                "move_speed_bonus_micros": rage.move_speed_bonus_micros,
                "duration_ticks": rage.duration_ticks,
            },
            priority=2,
        )

    def _advance_overload(
        self,
        instance_id: str,
        enemy: EnemyState,
        runtime: Any,
        overload: Any,
    ) -> tuple[dict[str, object], list[_DamageIntent]]:
        """One tick of R-31's 【超负载】.

        PRTS, verbatim: 「仅**被阻挡**、且半径0.8范围内存在非飞行我方单位时
        可触发：以阻挡单位为目标，**解除自身阻挡**，10s内对目标连续出拳
        （每拳1s），每次造成攻击力75%的物理伤害，附加2s晕眩（技能期间不会
        改变攻击目标）※技能结束动画持续1s；**目标退场后立刻中止技能**」.

        Three parts of that sentence are not in the shipped blackboard and are
        the reason this needed its own channel: it triggers on **being blocked**
        rather than on a timer, it **releases that block** as it starts, and the
        punches land on a **locked** target that the ordinary retarget path must
        not change.
        """

        update: dict[str, object] = {}
        intents: list[_DamageIntent] = []

        window = runtime.punch_window_remaining_ticks
        next_punch = runtime.punch_next_ticks
        target_id = runtime.punch_target_id

        if not window:
            if runtime.punch_recovery_remaining_ticks:
                update["punch_recovery_remaining_ticks"] = (
                    runtime.punch_recovery_remaining_ticks - 1
                )
                return update, intents
            if runtime.punch_cooldown_remaining_ticks:
                update["punch_cooldown_remaining_ticks"] = (
                    runtime.punch_cooldown_remaining_ticks - 1
                )
                return update, intents
            blocker_id = enemy.blocked_by
            if blocker_id is None:
                return update, intents
            if not self._non_flying_friendly_within(
                enemy, overload.trigger_radius_milli_tiles
            ):
                return update, intents
            window = overload.duration_ticks
            next_punch = 0
            target_id = blocker_id
            update["punch_cooldown_remaining_ticks"] = overload.cooldown_ticks
            self._release_block(enemy)
            self._emit(
                EventPhase.ATTACK,
                "enemy_overload_started",
                instance_id,
                {
                    "target_id": blocker_id,
                    "duration_ticks": overload.duration_ticks,
                    "punch_interval_ticks": overload.punch_interval_ticks,
                    "source_key": "Punch.duration",
                },
                priority=20,
            )
            # and straight into the first punch: the window opens *with* a
            # swing, so a ten-second window at one a second lands ten.

        target = (
            self.operators.get(target_id) or self.summons.get(target_id)
            if target_id
            else None
        )
        if target is None:
            # 「目标退场后立刻中止技能」 -- no punches, no closing animation.
            update["punch_window_remaining_ticks"] = 0
            update["punch_next_ticks"] = 0
            update["punch_target_id"] = None
            self._emit(
                EventPhase.ATTACK,
                "enemy_overload_aborted",
                instance_id,
                {"reason": "target_left", "source_key": "Punch.duration"},
                priority=20,
            )
            return update, intents

        if next_punch <= 0:
            damage = (
                self._enemy_defs[enemy.enemy_id].attack
                * overload.attack_scale_micros
                // 1_000_000
            )
            intents.append(
                _DamageIntent(
                    instance_id,
                    target_id,
                    self._friendly_target_side(target),
                    damage,
                )
            )
            stun_ticks = self._seconds_micros_to_ticks(overload.stun_seconds_micros)
            self._stun_friendly(target_id, stun_ticks)
            self._emit(
                EventPhase.ATTACK,
                "enemy_overload_punch",
                instance_id,
                {
                    "target_id": target_id,
                    "damage": damage,
                    "stun_ticks": stun_ticks,
                    "source_key": "Punch.atk_scale",
                },
                priority=20,
            )
            # One short, because this tick is the first of the wait.
            next_punch = overload.punch_interval_ticks - 1
        else:
            next_punch -= 1

        window -= 1
        update["punch_window_remaining_ticks"] = window
        update["punch_next_ticks"] = next_punch
        update["punch_target_id"] = target_id
        if window == 0:
            update["punch_next_ticks"] = 0
            update["punch_target_id"] = None
            update["punch_recovery_remaining_ticks"] = overload.recovery_unmovable_ticks
        return update, intents

    def _non_flying_friendly_within(
        self, enemy: EnemyState, radius_milli: int
    ) -> bool:
        """「半径0.8范围内存在非飞行我方单位」 -- 我方 is the defender's side."""

        col, row = self._enemy_position_milli(enemy)
        limit = radius_milli * radius_milli
        for actor in (*self.operators.values(), *self.summons.values()):
            delta_col = actor.position.col * 1000 - col
            delta_row = actor.position.row * 1000 - row
            if delta_col * delta_col + delta_row * delta_row <= limit:
                return True
        return False

    def _stun_friendly(self, target_id: str, ticks: int) -> None:
        """Hold one of ours for `ticks`, keeping the longer of the two holds."""

        if ticks <= 0:
            return
        operator = self.operators.get(target_id)
        if operator is not None:
            self.operators[target_id] = operator.model_copy(
                update={
                    "coefficient_stun_remaining_ticks": max(
                        operator.coefficient_stun_remaining_ticks, ticks
                    )
                }
            )
            return
        summon = self.summons.get(target_id)
        if summon is not None:
            self.summons[target_id] = summon.model_copy(
                update={
                    "coefficient_stun_remaining_ticks": max(
                        summon.coefficient_stun_remaining_ticks, ticks
                    )
                }
            )

    def _resolve_devour(self, instance_id: str, enemy: EnemyState) -> dict[str, object]:
        """「首次被阻挡时吞噬阻挡者」, and switch to 饱腹形态.

        PRTS: 「仅被阻挡、且半径0.7范围内存在非【吞噬免疫】的我方干员/召唤物
        时可触发」.  The swallowed operator leaves the board entirely -- it is
        not retreated and does not go on redeploy, it is **held**, and killing
        the carrier is what gets it back.

        「首次」 is a once-per-carrier flag rather than a cooldown: a second
        blocker is not eaten.
        """

        definition = self._enemy_defs[enemy.enemy_id]
        devour = definition.devour
        runtime = enemy.coefficient_runtime
        if devour is None or runtime is None or runtime.devour_used:
            return {}
        blocker_id = enemy.blocked_by
        if blocker_id is None or blocker_id not in self.operators:
            return {}
        if not self._non_flying_friendly_within(
            enemy, devour.trigger_radius_milli_tiles
        ):
            return {}
        swallowed = self.operators.pop(blocker_id)
        self._devoured_operators[blocker_id] = swallowed
        self._release_block(enemy)
        self._emit(
            EventPhase.ATTACK,
            "enemy_devoured_operator",
            instance_id,
            {
                "operator_instance_id": blocker_id,
                "operator_id": swallowed.operator_id,
                "defence_bonus": devour.defence_bonus,
                "move_speed_penalty_micros": devour.move_speed_penalty_micros,
                "source_key": "Stronger.def",
            },
            priority=2,
        )
        return {"devoured_operator_id": blocker_id, "devour_used": True}

    def _release_devoured(self, enemy: EnemyState) -> None:
        """「被击倒时被吞噬的干员返回」.

        It comes back as it went in -- same tile, same health -- because
        nothing in the data or on PRTS says otherwise, and inventing a penalty
        (or a heal) would change how much killing this thing is worth.
        Registered as the default reading rather than presented as source-exact.
        """

        runtime = enemy.coefficient_runtime
        if runtime is None or runtime.devoured_operator_id is None:
            return
        operator_id = runtime.devoured_operator_id
        swallowed = self._devoured_operators.pop(operator_id, None)
        if swallowed is None:
            return
        self.operators[operator_id] = swallowed.model_copy(
            update={"blocked_enemy_ids": ()}
        )
        self._emit(
            EventPhase.DEATH,
            "enemy_released_operator",
            enemy.instance_id,
            {
                "operator_instance_id": operator_id,
                "operator_id": swallowed.operator_id,
                "returned_hp": swallowed.hp,
            },
            priority=1,
        )

    def _enemy_is_overloading(self, enemy: EnemyState) -> bool:
        """Whether R-31 is mid-【超负载】.

        「解除自身阻挡」 would mean nothing if the next reconciliation pass simply
        put the block back: the armour would be held again a tick later and
        would go on swinging normally while its own skill punched.  So the
        window excludes it from blocking exactly the way flight does.
        """

        runtime = enemy.coefficient_runtime
        return bool(runtime is not None and runtime.punch_window_remaining_ticks)

    def _enemy_is_airborne(self, enemy: EnemyState) -> bool:
        """Whether 喷气人 is currently in its flight mode."""

        runtime = enemy.coefficient_runtime
        if runtime is None:
            return False
        # Two different flights: 喷射人's timed one counts down, 弧光突击队's
        # condition-triggered one has no duration and ends only on control.
        # 「结束后，**1.333秒内不可阻挡**」 -- the jet-pack man is still out of
        # reach on the way down, which is what makes the block that started it
        # a bad trade rather than a delay.
        return bool(
            runtime.airborne_remaining_ticks
            or runtime.conditional_airborne
            or runtime.landing_slow_remaining_ticks
        )

    def _release_block(self, enemy: EnemyState) -> None:
        """Drop the block on this enemy from both sides."""

        blocker_id = enemy.blocked_by
        if blocker_id is None:
            return
        self.enemies[enemy.instance_id] = self.enemies[enemy.instance_id].model_copy(
            update={"blocked_by": None}
        )
        for collection in (self.operators, self.summons):
            friendly = collection.get(blocker_id)
            if friendly is None:
                continue
            collection[blocker_id] = friendly.model_copy(
                update={
                    "blocked_enemy_ids": tuple(
                        item
                        for item in friendly.blocked_enemy_ids
                        if item != enemy.instance_id
                    )
                }
            )

    def _refresh_device_pollution_auras(self) -> None:
        """汲营枯枝's 污染秧蚀, sourced from a **device** rather than an enemy.

        Identical mechanic to 食腐使徒's, and deliberately the same
        `PollutedZoneState`: a following aura whose centre is rewritten each
        tick, pulsing the low- and high-ground figures inside its radius.  What
        differs is only where it comes from, which is why the snapshot check no
        longer insists a zone's source be an enemy.

        Seventeen instances stand in 5-2 and ev-3, all of them draining 50 HP a
        second from whatever of ours is within 1.1 tiles -- so this is a
        floor-5 stage's main source of attrition, and it was absent.
        """

        for instance_id in sorted(self.summons):
            summon = self.summons[instance_id]
            definition = self._summon_defs.get(summon.summon_id)
            if definition is None or not definition.pollution_aura_radius_milli_tiles:
                continue
            zone_id = f"pollution-aura-{instance_id}"
            existing = self.polluted_zones.get(zone_id)
            pulse = (
                existing.pulse_remaining_ticks
                if existing is not None
                else RETURN_BODY_POLLUTION_PULSE_TICKS
            )
            self.polluted_zones[zone_id] = PollutedZoneState(
                instance_id=zone_id,
                source_enemy_id=summon.summon_id,
                center_col_milli=summon.position.col * 1000,
                center_row_milli=summon.position.row * 1000,
                radius_milli_tiles=definition.pollution_aura_radius_milli_tiles,
                # Two ticks for the same reason as the enemy-sourced aura: the
                # zone loop decrements and deletes at zero.
                remaining_ticks=2,
                pulse_remaining_ticks=max(1, pulse),
                low_ground_damage=definition.pollution_aura_low_damage,
                high_ground_damage=definition.pollution_aura_high_damage,
            )
            if existing is None:
                self._emit(
                    EventPhase.SPAWN,
                    "device_pollution_aura_started",
                    instance_id,
                    {
                        "summon_id": summon.summon_id,
                        "radius_milli_tiles": (
                            definition.pollution_aura_radius_milli_tiles
                        ),
                        "low_ground_damage": definition.pollution_aura_low_damage,
                        "high_ground_damage": definition.pollution_aura_high_damage,
                    },
                    priority=2,
                )

    def _refresh_pollution_auras(self) -> None:
        """Keep 食腐使徒's 污染秽蚀 centred on it while it lives.

        「释放污染秽蚀，使周围我方持续损失生命」 is a *following* aura, not a zone
        dropped once, so the zone's centre is rewritten each tick from the
        carrier's position.  The effect itself reuses `PollutedZoneState`, which
        already pulses low- and high-ground damage inside a radius for the death
        variant of the same mechanic -- `low`/`high` are ground heights there,
        and that is the reading followed here.

        A carrier that dies stops being refreshed, so its zone lapses on its own
        remaining_ticks rather than needing a teardown path.
        """

        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            rows = pollution_aura_rows(self._enemy_defs[enemy.enemy_id])
            if rows is None:
                continue
            col_milli, row_milli = self._enemy_position_milli(enemy)
            zone_id = f"pollution-aura-{instance_id}"
            existing = self.polluted_zones.get(zone_id)
            pulse = (
                existing.pulse_remaining_ticks
                if existing is not None
                else RETURN_BODY_POLLUTION_PULSE_TICKS
            )
            self.polluted_zones[zone_id] = PollutedZoneState(
                instance_id=zone_id,
                source_enemy_id=enemy.enemy_id,
                center_col_milli=col_milli,
                center_row_milli=row_milli,
                radius_milli_tiles=rows["radius"] // 1000,
                # Two ticks, not one: the zone loop decrements and deletes at
                # zero, so a one-tick zone would expire and be re-created every
                # tick -- emitting an expiry event each time and resetting the
                # pulse.  Two survives the decrement and the refresh tops it
                # back up, so it lapses shortly after the carrier stops being
                # refreshed (i.e. dies).
                remaining_ticks=2,
                pulse_remaining_ticks=max(1, pulse),
                low_ground_damage=rows["low"] // 1_000_000,
                high_ground_damage=rows["high"] // 1_000_000,
            )
            if existing is None:
                self._emit(
                    EventPhase.ATTACK,
                    "pollution_aura_started",
                    instance_id,
                    {
                        "zone_id": zone_id,
                        "source_enemy_id": enemy.enemy_id,
                        "radius_milli_tiles": rows["radius"] // 1000,
                        "low_ground_damage": rows["low"] // 1_000_000,
                        "high_ground_damage": rows["high"] // 1_000_000,
                        "source_key": "PollutedArea.range_Radius",
                    },
                    priority=1,
                )

    def _resolve_death_transform(self, enemy: EnemyState) -> None:
        """Put this carrier's successor on the board where it fell.

        術師快艇 「载具损毁后术师会继续作战」: the vehicle is destroyed and the mage
        it carried continues from the same spot, so the successor inherits the
        route, waypoint and sub-tile progress rather than re-entering at the
        route's start.  It arrives at full health -- the successor is a separate
        stat block, not the remains of the carrier.
        """

        definition = self._enemy_defs[enemy.enemy_id]
        successor_id = definition.death_transform_enemy_id
        if successor_id is None:
            return
        successor = self._enemy_defs.get(successor_id)
        if successor is None:
            raise UnsupportedMechanicError(
                "death transform successor disappeared at runtime"
            )
        if enemy.route_teleport_destination_index is not None:
            raise UnsupportedMechanicError(
                "death transform during a route teleport has no exact position"
            )
        self.entity_sequence += 1
        instance_id = "enemy-%06d" % self.entity_sequence
        self.enemies[instance_id] = EnemyState(
            instance_id=instance_id,
            enemy_id=successor_id,
            route_id=enemy.route_id,
            waypoint_index=enemy.waypoint_index,
            segment_progress_milli=enemy.segment_progress_milli,
            movement_phase_remainder=enemy.movement_phase_remainder,
            hp=successor.max_hp,
            path_override=enemy.path_override,
            spawn_occurrence_id=enemy.spawn_occurrence_id,
            coefficient_runtime=self._initial_enemy_coefficient_runtime(successor),
        )
        self._emit(
            EventPhase.DEATH,
            "enemy_death_transform",
            enemy.instance_id,
            {
                "successor_instance_id": instance_id,
                "successor_enemy_id": successor_id,
                "carrier_enemy_id": enemy.enemy_id,
                "source_key": "DeathRattle.enemy_key",
            },
            priority=1,
        )

    def _arm_death_explosion(self, enemy: EnemyState) -> None:
        """Leave a fused blast behind, if this carrier has one.

        PRTS gives both IS3 carriers a 1 second delay, so the blast is armed
        here and resolved later -- by then the carrier is off the board, which
        is why the blast carries its own frozen numbers rather than looking
        anything up at detonation time.
        """

        definition = self._enemy_defs[enemy.enemy_id]
        scale = coefficient_value(definition, "boom.atk_scale")
        if scale is None:
            return
        radius_micros = coefficient_value(
            definition, "boom.range_radius"
        ) or coefficient_value(definition, "boom.projectile_range")
        if radius_micros is not None:
            radius_milli = radius_micros // 1000
        else:
            radius_milli = BOOM_RADIUS_MILLI_TILES_BY_ENEMY.get(definition.enemy_id, 0)
        if radius_milli <= 0:
            raise UnsupportedMechanicError(
                "death explosion has no radius, from data or the cited table"
            )
        damage = _scale_micros(definition.attack, scale)
        if damage <= 0:
            # A carrier with no attack cannot splash for anything; saying so is
            # better than arming a blast that would deal a floored 1 damage.
            return
        cold = coefficient_value(definition, "boom.freeze")
        fuse_ticks = BOOM_FUSE_TICKS_BY_ENEMY.get(
            definition.enemy_id, BOOM_FUSE_TICKS
        )
        col_milli, row_milli = self._enemy_position_milli(enemy)
        self.entity_sequence += 1
        instance_id = "boom-%06d" % self.entity_sequence
        self.pending_explosions[instance_id] = PendingExplosionState(
            instance_id=instance_id,
            source_enemy_id=definition.enemy_id,
            center_col_milli=col_milli,
            center_row_milli=row_milli,
            radius_milli_tiles=radius_milli,
            fuse_remaining_ticks=fuse_ticks,
            damage=damage,
            cold_ticks=0 if cold is None else self._seconds_micros_to_ticks(cold),
        )
        self._emit(
            EventPhase.DEATH,
            "death_explosion_armed",
            enemy.instance_id,
            {
                "explosion_id": instance_id,
                "fuse_ticks": fuse_ticks,
                "damage": damage,
                "radius_milli_tiles": radius_milli,
                "source_key": "boom.atk_scale",
            },
        )

    def _grant_death_energy(self, enemy: EnemyState) -> None:
        """「给予全场敌人 1 点能量」, from `Boom.add_count`.

        辐能源石虫 (IS3 3-7) ships `Boom.add_count` 1.0 alongside its explosion,
        and its description says 「死亡后会产生爆炸，并使场上敌人获得1点能量」.
        PRTS makes the scope explicit -- **全场**, every enemy on the field, not
        the ones near the blast -- which matters because 3-7 is the energy stage:
        the same map holds 匪帮射手's charger and 匪帮斧手's thief, so a slime
        dying anywhere on it feeds every pool at once.

        The grant is separate from the explosion on purpose: the blast is fused
        and lands half a second later, while the energy is immediate, and
        nothing about the energy depends on the blast reaching anybody.
        """

        amount = coefficient_value(
            self._enemy_defs[enemy.enemy_id], "Boom.add_count"
        )
        if amount is None or amount <= 0:
            return
        points = amount // 1_000_000
        if points <= 0:
            return
        for target_id in sorted(self.enemies):
            if target_id == enemy.instance_id:
                continue
            self._grant_energy(target_id, points)
        self._emit(
            EventPhase.DEATH,
            "death_energy_granted",
            enemy.instance_id,
            {"points": points, "source_key": "Boom.add_count"},
            priority=2,
        )

    def _collect_overload_punches(self) -> tuple[_DamageIntent, ...]:
        """Advance every 【超负载】 window and return this tick's punches.

        Separate from the ordinary attack pass because the punches replace it:
        the carrier is unblocked by its own skill and hits a locked target on a
        cadence the skill sets, not on its attack interval.
        """

        intents: list[_DamageIntent] = []
        for instance_id in sorted(tuple(self.enemies)):
            if instance_id in self.enemies:
                self._advance_environmental_fragile(instance_id)
            if instance_id in self.enemies:
                self._advance_self_rage(instance_id)
            if instance_id in self.enemies:
                self._advance_predation_facing(instance_id)
            enemy = self.enemies.get(instance_id)
            if enemy is None:
                continue
            overload = self._enemy_defs[enemy.enemy_id].overload
            runtime = enemy.coefficient_runtime
            if overload is None or runtime is None:
                continue
            update, punches = self._advance_overload(
                instance_id, enemy, runtime, overload
            )
            intents.extend(punches)
            if update:
                current = self.enemies.get(instance_id)
                if current is None:
                    continue
                current_runtime = current.coefficient_runtime
                if current_runtime is None:
                    continue
                self.enemies[instance_id] = current.model_copy(
                    update={
                        "coefficient_runtime": current_runtime.model_copy(
                            update=update
                        )
                    }
                )
        return tuple(intents)

    def _pulse_device_cold(self) -> None:
        """源石冰晶's 【寒潮】: ten seconds of 寒冷 to every friendly in reach.

        `sktok_frosts`「寒潮」: 「发射一道寒霜冲击波，使**周围我方单位**陷入**寒冷**
        状态 **10** 秒」, on the same derived fifteen-second cadence and the same
        13-tile `x-1` grid as 源石祭坛's pulse.

        The payload is a **state**, not damage, so this rides the same cold path
        the freeze aura uses -- two colds make a freeze, and that escalation is
        the reason parking on one of 4-8's two crystals is a real mistake rather
        than a slow leak.
        """

        for instance_id in sorted(self.summons):
            summon = self.summons[instance_id]
            definition = self._summon_defs.get(summon.summon_id)
            if definition is None or not definition.cold_pulse_interval_ticks:
                continue
            remaining = summon.cold_pulse_remaining_ticks
            if remaining <= 0:
                # `initSp` 0: arm it rather than firing on the spot.
                self.summons[instance_id] = summon.model_copy(
                    update={
                        "cold_pulse_remaining_ticks": (
                            definition.cold_pulse_interval_ticks
                        )
                    }
                )
                continue
            remaining -= 1
            if remaining:
                self.summons[instance_id] = summon.model_copy(
                    update={"cold_pulse_remaining_ticks": remaining}
                )
                continue
            self.summons[instance_id] = summon.model_copy(
                update={
                    "cold_pulse_remaining_ticks": definition.cold_pulse_interval_ticks
                }
            )
            tiles = {
                (summon.position.col + col, summon.position.row + row)
                for col, row in definition.cold_pulse_range_offsets
            }
            chilled: list[str] = []
            for target_id in sorted((*self.operators, *self.summons)):
                if target_id == instance_id:
                    continue
                actor: OperatorState | SummonState = (
                    self.operators[target_id]
                    if target_id in self.operators
                    else cast(SummonState, self.summons[target_id])
                )
                if (actor.position.col, actor.position.row) not in tiles:
                    continue
                updated = actor.model_copy(
                    update=self._enemy_cold_update(
                        actor, definition.cold_pulse_ticks
                    )
                )
                if target_id in self.operators:
                    self.operators[target_id] = cast(OperatorState, updated)
                else:
                    self.summons[target_id] = cast(SummonState, updated)
                chilled.append(target_id)
            self._emit(
                EventPhase.ATTACK,
                "device_cold_pulse",
                instance_id,
                {
                    "summon_id": summon.summon_id,
                    "target_ids": tuple(chilled),
                    "duration_ticks": definition.cold_pulse_ticks,
                },
                priority=21,
            )

    def _collect_device_pulse_damage(self) -> tuple["_DamageIntent", ...]:
        """源石祭坛's 脉冲波: 500 true damage to **everything** in reach.

        PRTS 「源石祭坛」, verbatim:

            「向四周喷发脉冲波，对周围所有我方与敌方单位造成500真实伤害。
              并使特殊敌人获得强化」
            「※无视无法选择（但可受孤立、迷彩干扰），可对空」
            「※造成预计算无途径真实无类型伤害，不会对**免疫脉冲波**的单位造成伤害」

        Four of these stand in 3-2, so a defender who parks on the wrong tile
        loses 500 HP every seven seconds to a device on their own side.  That is
        the point of the stage, and it is the only effect in this engine that
        damages both sides from one source.

        真实伤害 means the amount lands as shipped: no defence, no resistance and
        no ground-height split.  「可对空」 means no movement-layer filter either,
        which is why nothing here looks at one.
        """

        intents: list["_DamageIntent"] = []
        for instance_id in sorted(self.summons):
            summon = self.summons[instance_id]
            definition = self._summon_defs.get(summon.summon_id)
            if definition is None or not definition.pulse_interval_ticks:
                continue
            remaining = summon.pulse_remaining_ticks
            if remaining <= 0:
                # A device restored from a snapshot that predates the pulse, or
                # placed by a fixture that did not seed it: arm it rather than
                # firing on the spot, which would put a pulse where the shipped
                # `initSp` 0 says there is none yet.
                self.summons[instance_id] = summon.model_copy(
                    update={"pulse_remaining_ticks": definition.pulse_interval_ticks}
                )
                continue
            remaining -= 1
            if remaining:
                self.summons[instance_id] = summon.model_copy(
                    update={"pulse_remaining_ticks": remaining}
                )
                continue
            self.summons[instance_id] = summon.model_copy(
                update={"pulse_remaining_ticks": definition.pulse_interval_ticks}
            )
            tiles = {
                (summon.position.col + col, summon.position.row + row)
                for col, row in definition.pulse_range_offsets
            }
            struck: list[str] = []
            immune: list[str] = []
            for operator_id in sorted(self.operators):
                actor = self.operators[operator_id]
                if (actor.position.col, actor.position.row) in tiles:
                    intents.append(
                        _DamageIntent(
                            instance_id,
                            operator_id,
                            "operator",
                            definition.pulse_damage,
                        )
                    )
                    struck.append(operator_id)
            for other_id in sorted(self.summons):
                if other_id == instance_id:
                    # 「对周围所有我方与敌方单位」 counts the other devices, but a
                    # shrine standing on its own centre tile would otherwise kill
                    # itself in a pulse -- and four of them stand within reach of
                    # each other in 3-2, which is the shipped layout.
                    continue
                other = self.summons[other_id]
                if (other.position.col, other.position.row) in tiles:
                    intents.append(
                        _DamageIntent(
                            instance_id, other_id, "summon", definition.pulse_damage
                        )
                    )
                    struck.append(other_id)
            for enemy_id in sorted(self.enemies):
                enemy = self.enemies[enemy_id]
                # An enemy has no tile of its own -- it is somewhere along a
                # route -- so the tile it is nearest to is the one the pulse
                # covers.  `_enemy_grid_point` would have skipped everything
                # mid-stride, which for a marching enemy is most ticks.
                col_milli, row_milli = self._enemy_position_milli(enemy)
                here = ((col_milli + 500) // 1000, (row_milli + 500) // 1000)
                if here not in tiles:
                    continue
                enemy_definition = self._enemy_defs[enemy.enemy_id]
                if enemy_definition.pulse_immune:
                    immune.append(enemy_id)
                    if not enemy.pulse_empowered:
                        self.enemies[enemy_id] = enemy.model_copy(
                            update={"pulse_empowered": True}
                        )
                        self._emit(
                            EventPhase.DAMAGE,
                            "enemy_pulse_empowered",
                            enemy_id,
                            {
                                "source_id": instance_id,
                                "damage_type": (
                                    enemy_definition.pulse_empowered_damage_type
                                ),
                            },
                            priority=2,
                        )
                    continue
                intents.append(
                    _DamageIntent(
                        instance_id, enemy_id, "enemy", definition.pulse_damage
                    )
                )
                struck.append(enemy_id)
            self._emit(
                EventPhase.DAMAGE,
                "device_pulse",
                instance_id,
                {
                    "summon_id": summon.summon_id,
                    "damage": definition.pulse_damage,
                    "struck": tuple(struck),
                    "immune": tuple(immune),
                },
                priority=1,
            )
        return tuple(intents)

    def _tactical_command_giver(self) -> "EnemyDefinition | None":
        """The herald whose 【战术命令】 is up, if one is on the board.

        PRTS 「游击队传令兵组长」: 「**在场时**令**全场敌方单位**（无视其可选性）
        攻击力+10%、防御力+100，并获得<战术命令>效果」.  Being on the board is the
        whole condition -- no radius, no cooldown, and no line of sight -- so
        this is a census rather than a range query, the same shape 山海密语仪's
        field aura has.

        Two heralds would stack in the shipped wording ("每个在场的"), but IS3
        fields exactly one, so the first is taken and the question left alone.
        """

        # `_enemy_effective_stat` is the hottest read in the engine, so the
        # bundles that field no herald at all -- 62 of the 63 -- pay one
        # attribute lookup rather than a census.
        if not self._tactical_command_present:
            return None
        for instance_id in sorted(self.enemies):
            definition = self._enemy_defs[self.enemies[instance_id].enemy_id]
            command = definition.tactical_command
            if command is not None and command.role == "giver":
                return definition
        return None

    def _tactical_command_bonuses(
        self, enemy: EnemyState
    ) -> "tuple[int, int, int, int]":
        """(attack micros, flat defence, move micros, attack-speed points).

        The giver's own aura reaches 「全场敌方单位」, which includes the herald
        itself; a receiver adds what its own talent grants for being under the
        command on top.
        """

        giver = self._tactical_command_giver()
        if giver is None:
            return (0, 0, 0, 0)
        aura = giver.tactical_command
        assert aura is not None
        attack = aura.attack_bonus_micros
        defense = aura.defense_bonus
        move = aura.move_speed_bonus_micros
        speed = aura.attack_speed_bonus
        own = self._enemy_defs[enemy.enemy_id].tactical_command
        if own is not None and own.role == "receiver":
            attack += own.attack_bonus_micros
            defense += own.defense_bonus
            move += own.move_speed_bonus_micros
            speed += own.attack_speed_bonus
        return (attack, defense, move, speed)

    def _collect_guided_strike_damage(self) -> tuple[_DamageIntent, ...]:
        """山海众司魅人's 【引导】: lock one of ours, hold five seconds, then hit.

        PRTS 「山海众司魅人」 技能0: 「锁定攻击范围内**1名**我方单位，蓄力约 **5s**
        后，对其造成攻击力 **200%** 的法术普通伤害。随后**自身**攻击速度 **+150**，
        持续 **20s**」.

        `spCost` 0 with a cooldown makes this a **timer**, not an attack-charged
        skill: it comes round at `initCooldown` and every `cooldown` after,
        whatever the caster is doing in between.

        The target is chosen once, when the lock starts.  If it is gone when the
        five seconds are up, the blow lands on nothing and the cooldown starts
        anyway -- 「锁定」 picks a unit, not a tile, and nothing in the source
        says it re-picks.  That is registered rather than smoothed over.
        """

        intents: list[_DamageIntent] = []
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            guide = definition.guided_strike
            if guide is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "guided strike carrier lacks deterministic runtime state"
                )
            update: dict[str, object] = {}
            if runtime.guide_buff_remaining_ticks:
                update["guide_buff_remaining_ticks"] = (
                    runtime.guide_buff_remaining_ticks - 1
                )
            if runtime.guide_windup_remaining_ticks:
                remaining = runtime.guide_windup_remaining_ticks - 1
                update["guide_windup_remaining_ticks"] = remaining
                if remaining == 0:
                    target_id = runtime.guide_target_id
                    update["guide_target_id"] = None
                    update["guide_cooldown_remaining_ticks"] = guide.cooldown_ticks
                    update["guide_buff_remaining_ticks"] = guide.buff_duration_ticks
                    actor = (
                        self.operators.get(target_id or "")
                        or self.summons.get(target_id or "")
                    )
                    if actor is not None:
                        attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
                        payload = _scale_micros(attack, guide.attack_scale_micros)
                        resistance = (
                            self._operator_defs[actor.operator_id].magic_resistance
                            if isinstance(actor, OperatorState)
                            else self._summon_defs[actor.summon_id].magic_resistance
                        )
                        intents.append(
                            _DamageIntent(
                                instance_id,
                                cast(str, target_id),
                                self._friendly_target_side(actor),
                                _arts_damage(payload, resistance),
                            )
                        )
                    self._emit(
                        EventPhase.DAMAGE,
                        "enemy_guided_strike_released",
                        instance_id,
                        {
                            "target_id": target_id,
                            "landed": actor is not None,
                            "attack_speed_bonus": guide.buff_attack_speed_bonus,
                            "buff_duration_ticks": guide.buff_duration_ticks,
                        },
                        priority=1,
                    )
            elif runtime.guide_cooldown_remaining_ticks:
                update["guide_cooldown_remaining_ticks"] = (
                    runtime.guide_cooldown_remaining_ticks - 1
                )
            else:
                target = self._enemy_attack_target(enemy)
                if target is not None:
                    update["guide_windup_remaining_ticks"] = guide.windup_ticks
                    update["guide_target_id"] = target
                    self._emit(
                        EventPhase.ATTACK,
                        "enemy_guided_strike_locked",
                        instance_id,
                        {
                            "target_id": target,
                            "windup_ticks": guide.windup_ticks,
                        },
                        priority=2,
                    )
            if update:
                current = self.enemies.get(instance_id)
                if current is None:
                    continue
                current_runtime = current.coefficient_runtime
                if current_runtime is None:
                    continue
                self.enemies[instance_id] = current.model_copy(
                    update={
                        "coefficient_runtime": current_runtime.model_copy(
                            update=update
                        )
                    }
                )
        return tuple(intents)

    def _enemy_attack_target(self, enemy: EnemyState) -> "str | None":
        """The friendly 【引导】 locks: the nearest one inside the caster's range.

        The skill says 「锁定**攻击范围内**1名我方单位」 and nothing about which,
        so this takes the nearest by squared distance with the instance id as
        the tie-break -- deterministic, and the same ordering the rest of the
        engine uses when a source has to pick one of several.
        """

        definition = self._enemy_defs[enemy.enemy_id]
        reach_milli = definition.attack_range_milli_tiles or 1000
        col_milli, row_milli = self._enemy_position_milli(enemy)
        best: tuple[int, str] | None = None
        for target_id in sorted((*self.operators, *self.summons)):
            actor: OperatorState | SummonState = (
                self.operators[target_id]
                if target_id in self.operators
                else cast(SummonState, self.summons[target_id])
            )
            delta_col = actor.position.col * 1000 - col_milli
            delta_row = actor.position.row * 1000 - row_milli
            distance = delta_col * delta_col + delta_row * delta_row
            if distance > reach_milli * reach_milli:
                continue
            if best is None or distance < best[0]:
                best = (distance, target_id)
        return None if best is None else best[1]

    def _collect_four_direction_volley_damage(self) -> tuple[_DamageIntent, ...]:
        """残党正式萨克斯手's 【全力吹奏】: one ray down each axis, first hit only.

        PRTS 「残党正式萨克斯手」 技能0: 「蓄力数秒后向自身周围 **4个正方向** 发射
        弹道，弹道碰撞半径 **0.2**，对**首个**触碰到的我方单位造成攻击力 **150%**
        的物理普通伤害」.

        The front rank shields whoever is behind it: a column of operators takes
        one hit down that axis, not one each.  With a 0.2 collision radius on a
        one-tile grid, "on the axis" means exactly that -- the ray touches the
        tiles of its own row and column and nothing else.
        """

        intents: list[_DamageIntent] = []
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            volley = definition.four_direction_volley
            if volley is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "four-direction volley carrier lacks deterministic runtime state"
                )
            if runtime.volley_cooldown_remaining_ticks:
                self.enemies[instance_id] = enemy.model_copy(
                    update={
                        "coefficient_runtime": runtime.model_copy(
                            update={
                                "volley_cooldown_remaining_ticks": (
                                    runtime.volley_cooldown_remaining_ticks - 1
                                )
                            }
                        )
                    }
                )
                continue
            attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
            payload = _scale_micros(attack, volley.attack_scale_micros)
            col_milli, row_milli = self._enemy_position_milli(enemy)
            origin = ((col_milli + 500) // 1000, (row_milli + 500) // 1000)
            occupied: dict[tuple[int, int], str] = {}
            for target_id in sorted((*self.operators, *self.summons)):
                actor: OperatorState | SummonState = (
                    self.operators[target_id]
                    if target_id in self.operators
                    else cast(SummonState, self.summons[target_id])
                )
                occupied.setdefault(
                    (actor.position.col, actor.position.row), target_id
                )
            struck: list[str] = []
            for step_col, step_row in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                col, row = origin
                while True:
                    col += step_col
                    row += step_row
                    if not (
                        0 <= col < self.content.map.width
                        and 0 <= row < self.content.map.height
                    ):
                        break
                    target_id = occupied.get((col, row))
                    if target_id is None:
                        continue
                    actor = (
                        self.operators[target_id]
                        if target_id in self.operators
                        else cast(SummonState, self.summons[target_id])
                    )
                    defense = (
                        self._operator_effective_stat(actor, EffectStat.DEFENSE)
                        if isinstance(actor, OperatorState)
                        else self._summon_effective_stat(actor, EffectStat.DEFENSE)
                    )
                    intents.append(
                        _DamageIntent(
                            instance_id,
                            target_id,
                            self._friendly_target_side(actor),
                            _physical_damage(payload, defense),
                        )
                    )
                    struck.append(target_id)
                    break
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "coefficient_runtime": runtime.model_copy(
                        update={
                            "volley_cooldown_remaining_ticks": volley.cooldown_ticks
                        }
                    )
                }
            )
            self._emit(
                EventPhase.DAMAGE,
                "enemy_four_direction_volley",
                instance_id,
                {"payload": payload, "target_ids": tuple(struck)},
                priority=2,
            )
        return tuple(intents)

    def _collect_ice_bomb_damage(self) -> tuple[_DamageIntent, ...]:
        """虚幻's 【轰炸】: the one bomb it ever drops.

        PRTS 「虚幻」 技能0: 「**仅攻击范围内存在非飞行我方单位时可触发**：对目标
        及其**周围八格**的我方单位造成 **100%** 法术伤害，附加 **10秒寒冷**
        ※可溅射飞行单位 ※**此技能仅能触发一次**」.

        The gate is a condition, not a clock -- the third carrier in this topic
        whose skill reads 「仅…时可触发」 -- and it counts only **non-flying**
        friendlies, while the splash that follows reaches everything in the nine
        tiles.  This engine has no flying friendlies, so the two halves coincide
        here; the distinction is kept because the data states it.
        """

        intents: list[_DamageIntent] = []
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            bomb = definition.ice_bomb
            if bomb is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "ice bomb carrier lacks deterministic runtime state"
                )
            if runtime.bomb_spent:
                continue
            reach_milli = definition.attack_range_milli_tiles or 1000
            col_milli, row_milli = self._enemy_position_milli(enemy)
            in_range: list[tuple[int, str]] = []
            for target_id in sorted((*self.operators, *self.summons)):
                actor: OperatorState | SummonState = (
                    self.operators[target_id]
                    if target_id in self.operators
                    else cast(SummonState, self.summons[target_id])
                )
                delta_col = actor.position.col * 1000 - col_milli
                delta_row = actor.position.row * 1000 - row_milli
                distance = delta_col * delta_col + delta_row * delta_row
                if distance <= reach_milli * reach_milli:
                    in_range.append((distance, target_id))
            if not in_range:
                # 「仅攻击范围内存在非飞行我方单位时可触发」: with nobody in reach
                # the wait does not even start.
                continue
            if runtime.bomb_delay_remaining_ticks:
                self.enemies[instance_id] = enemy.model_copy(
                    update={
                        "coefficient_runtime": runtime.model_copy(
                            update={
                                "bomb_delay_remaining_ticks": (
                                    runtime.bomb_delay_remaining_ticks - 1
                                )
                            }
                        )
                    }
                )
                continue
            in_range.sort()
            centre_id = in_range[0][1]
            centre = (
                self.operators[centre_id]
                if centre_id in self.operators
                else cast(SummonState, self.summons[centre_id])
            ).position
            attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
            payload = _scale_micros(attack, bomb.attack_scale_micros)
            struck: list[str] = []
            for target_id in sorted((*self.operators, *self.summons)):
                actor = (
                    self.operators[target_id]
                    if target_id in self.operators
                    else cast(SummonState, self.summons[target_id])
                )
                if (
                    abs(actor.position.col - centre.col) > 1
                    or abs(actor.position.row - centre.row) > 1
                ):
                    continue
                resistance = (
                    self._operator_defs[actor.operator_id].magic_resistance
                    if isinstance(actor, OperatorState)
                    else self._summon_defs[actor.summon_id].magic_resistance
                )
                intents.append(
                    _DamageIntent(
                        instance_id,
                        target_id,
                        self._friendly_target_side(actor),
                        _arts_damage(payload, resistance),
                    )
                )
                self._apply_cold_to_friendly(target_id, bomb.cold_ticks)
                struck.append(target_id)
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "coefficient_runtime": runtime.model_copy(
                        update={"bomb_spent": True, "bomb_delay_remaining_ticks": 0}
                    )
                }
            )
            self._emit(
                EventPhase.DAMAGE,
                "enemy_ice_bomb",
                instance_id,
                {
                    "centre_id": centre_id,
                    "payload": payload,
                    "cold_ticks": bomb.cold_ticks,
                    "target_ids": tuple(struck),
                },
                priority=1,
            )
        return tuple(intents)

    def _collect_blink_damage(self) -> tuple[_DamageIntent, ...]:
        """路加萨尔古斯's 【瞬身】: slip the block, then bill whoever held it.

        PRTS 「路加萨尔古斯，历法之王」 技能0: 「**被阻挡时可触发**：①获得持续1秒
        的不可阻挡效果 ②……向下个检查点方向瞬移 **1.5格**……期间拥有不可阻挡，
        无敌，失衡免疫 ③技能触发 **0.5s** 后，对**之前阻挡自身的单位**造成攻击力
        **150%** 的法术普通伤害」.

        Being held is the trigger, so a squad that never blocks this boss never
        sees the skill; and the blow lands half a second later on the blocker it
        has already left behind, which is why the target is remembered by id.
        """

        intents: list[_DamageIntent] = []
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            blink = definition.blink
            if blink is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "blink carrier lacks deterministic runtime state"
                )
            update: dict[str, object] = {}
            if runtime.blink_unblockable_remaining_ticks:
                update["blink_unblockable_remaining_ticks"] = (
                    runtime.blink_unblockable_remaining_ticks - 1
                )
            if runtime.blink_strike_remaining_ticks:
                remaining = runtime.blink_strike_remaining_ticks - 1
                update["blink_strike_remaining_ticks"] = remaining
                if remaining == 0:
                    target_id = runtime.blink_strike_target_id
                    update["blink_strike_target_id"] = None
                    actor = (
                        self.operators.get(target_id or "")
                        or self.summons.get(target_id or "")
                    )
                    if actor is not None:
                        attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
                        payload = _scale_micros(attack, blink.attack_scale_micros)
                        resistance = (
                            self._operator_defs[actor.operator_id].magic_resistance
                            if isinstance(actor, OperatorState)
                            else self._summon_defs[actor.summon_id].magic_resistance
                        )
                        intents.append(
                            _DamageIntent(
                                instance_id,
                                cast(str, target_id),
                                self._friendly_target_side(actor),
                                _arts_damage(payload, resistance),
                            )
                        )
                    self._emit(
                        EventPhase.DAMAGE,
                        "enemy_blink_strike",
                        instance_id,
                        {"target_id": target_id, "landed": actor is not None},
                        priority=2,
                    )
            elif runtime.blink_cooldown_remaining_ticks:
                update["blink_cooldown_remaining_ticks"] = (
                    runtime.blink_cooldown_remaining_ticks - 1
                )
            elif enemy.blocked_by is not None:
                # 「被阻挡时可触发」 -- the hold is the trigger.
                blocker_id = enemy.blocked_by
                displaced = self._displace_along_route(
                    enemy, blink.distance_milli_tiles
                )
                self._release_block(enemy)
                enemy = self.enemies[instance_id]
                self.enemies[instance_id] = enemy.model_copy(
                    update={
                        "waypoint_index": displaced[0],
                        "segment_progress_milli": displaced[1],
                    }
                )
                update["blink_cooldown_remaining_ticks"] = blink.cooldown_ticks
                update["blink_unblockable_remaining_ticks"] = blink.unblockable_ticks
                update["blink_strike_remaining_ticks"] = max(
                    1, blink.strike_delay_ticks
                )
                update["blink_strike_target_id"] = blocker_id
                self._emit(
                    EventPhase.MOVE,
                    "enemy_blinked",
                    instance_id,
                    {
                        "blocker_id": blocker_id,
                        "distance_milli_tiles": blink.distance_milli_tiles,
                        "waypoint_index": displaced[0],
                        "segment_progress_milli": displaced[1],
                    },
                    priority=1,
                )
            if update:
                current = self.enemies.get(instance_id)
                if current is None:
                    continue
                current_runtime = current.coefficient_runtime
                if current_runtime is None:
                    continue
                self.enemies[instance_id] = current.model_copy(
                    update={
                        "coefficient_runtime": current_runtime.model_copy(
                            update=update
                        )
                    }
                )
        return tuple(intents)

    def _displace_along_route(
        self, enemy: EnemyState, distance_milli: int
    ) -> "tuple[int, int]":
        """Move this enemy `distance_milli` further along its own route.

        A blink is not a walk, so this deliberately skips the portals, waits and
        block boundaries the movement pass honours -- 「向**下个检查点方向**瞬移」
        is a straight step along the remaining path.  It also settles
        「目的地位于可通行地块时」 for free: a route is walkable by construction,
        so the destination always is.
        """

        path = self._enemy_route_points(enemy)
        waypoint_index = enemy.waypoint_index
        progress = enemy.segment_progress_milli + distance_milli
        while progress >= 1000 and waypoint_index < len(path) - 1:
            progress -= 1000
            waypoint_index += 1
        if waypoint_index >= len(path) - 1:
            return (len(path) - 1, 0)
        return (waypoint_index, progress)

    def _enemy_is_blinking(self, enemy: EnemyState) -> bool:
        """Whether 【瞬身】's unblockable window is running.

        「期间拥有不可阻挡」 -- and, like R-31's 【超负载】, saying so is not enough
        unless the reconciliation passes agree: the next tick would otherwise
        hand it straight back to the blocker it just left.
        """

        runtime = enemy.coefficient_runtime
        return bool(runtime is not None and runtime.blink_unblockable_remaining_ticks)

    def _collect_chain_buff_damage(self) -> tuple[_DamageIntent, ...]:
        """雪祀's 【反自然馈赠】: it shoots its own side, and they come out faster.

        PRTS 「陷落雪祀」 技能0: 「对**敌人**进行一次攻击，造成攻击力 **10%** 的
        法术伤害，攻击会在 **3个目标** 间跳跃，跳跃半径 **1.6**；受影响的目标
        **攻击速度+100，移动速度+100%，持续10s**；技能本身与跳跃均**不会选择自身
        为目标**」.

        「敌人」 is written from the player's side, so every target here is one of
        the carrier's **own allies**: the second enemy-on-enemy interaction in
        this engine after 狡兽's predation, and the first that is not a kill.

        The chain walks from each link to the nearest ally within the jump
        radius that it has not already touched -- and never back to the caster,
        which the source states twice in one sentence.
        """

        intents: list[_DamageIntent] = []
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            chain = definition.chain_buff
            if chain is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "chain buff carrier lacks deterministic runtime state"
                )
            if runtime.chain_buff_cooldown_remaining_ticks:
                self.enemies[instance_id] = enemy.model_copy(
                    update={
                        "coefficient_runtime": runtime.model_copy(
                            update={
                                "chain_buff_cooldown_remaining_ticks": (
                                    runtime.chain_buff_cooldown_remaining_ticks - 1
                                )
                            }
                        )
                    }
                )
                continue
            reach_squared = chain.jump_range_milli_tiles**2
            positions = {
                other_id: self._enemy_position_milli(self.enemies[other_id])
                for other_id in sorted(self.enemies)
            }
            origin = positions[instance_id]
            touched: list[str] = []
            here = origin
            while len(touched) < chain.max_targets:
                best: tuple[int, str] | None = None
                for other_id in sorted(self.enemies):
                    if other_id == instance_id or other_id in touched:
                        continue
                    other_col, other_row = positions[other_id]
                    delta_col = other_col - here[0]
                    delta_row = other_row - here[1]
                    distance = delta_col * delta_col + delta_row * delta_row
                    if distance > reach_squared:
                        continue
                    if best is None or distance < best[0]:
                        best = (distance, other_id)
                if best is None:
                    break
                touched.append(best[1])
                here = positions[best[1]]
            attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
            for index, target_id in enumerate(touched):
                scale = (
                    chain.attack_scale_micros
                    if index == 0
                    else chain.attack_scale_micros
                    * chain.chain_attack_scale_micros
                    // 1_000_000
                )
                payload = _scale_micros(attack, scale)
                target = self.enemies[target_id]
                intents.append(
                    _DamageIntent(
                        instance_id,
                        target_id,
                        "enemy",
                        _arts_damage(payload, self._enemy_magic_resistance(target)),
                    )
                )
                target_runtime = target.coefficient_runtime
                if target_runtime is None:
                    # The buff has nowhere to live, so it is dropped rather than
                    # silently applied to a carrier with no side table.
                    continue
                self.enemies[target_id] = target.model_copy(
                    update={
                        "coefficient_runtime": target_runtime.model_copy(
                            update={
                                "chain_buff_remaining_ticks": chain.duration_ticks,
                                "chain_buff_attack_speed": chain.attack_speed_bonus,
                                "chain_buff_move_speed_micros": (
                                    chain.move_speed_bonus_micros
                                ),
                            }
                        )
                    }
                )
            current = self.enemies[instance_id]
            current_runtime = current.coefficient_runtime
            if current_runtime is not None:
                self.enemies[instance_id] = current.model_copy(
                    update={
                        "coefficient_runtime": current_runtime.model_copy(
                            update={
                                "chain_buff_cooldown_remaining_ticks": (
                                    chain.cooldown_ticks
                                )
                            }
                        )
                    }
                )
            self._emit(
                EventPhase.ATTACK,
                "enemy_chain_buff",
                instance_id,
                {
                    "target_ids": tuple(touched),
                    "attack_speed_bonus": chain.attack_speed_bonus,
                    "move_speed_bonus_micros": chain.move_speed_bonus_micros,
                    "duration_ticks": chain.duration_ticks,
                },
                priority=2,
            )
        return tuple(intents)

    def _resolve_reborning(self) -> None:
        """Stand 无垠回荡克雷松 back up before the death pass can take it.

        PRTS 「无垠回荡克雷松」 天赋: 「**首次被击倒后**进行持续 **5s** 的重生，
        **恢复100%生命值**；重生结束后**攻击力+50%**」.

        Called at the top of the death resolution, so the boss never reaches
        `dead_enemies` on its first trip to zero -- which is what 「首次被击倒后」
        means: the kill happens and then is undone, rather than being prevented.
        """

        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            if enemy.hp > 0:
                continue
            definition = self._enemy_defs[enemy.enemy_id]
            reborning = definition.reborning
            if reborning is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None or runtime.reborning_spent:
                continue
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "hp": definition.max_hp,
                    "coefficient_runtime": runtime.model_copy(
                        update={
                            "reborning_remaining_ticks": reborning.duration_ticks,
                            "reborning_spent": True,
                        }
                    ),
                }
            )
            self._emit(
                EventPhase.DEATH,
                "enemy_reborning_started",
                instance_id,
                {
                    "enemy_id": enemy.enemy_id,
                    "duration_ticks": reborning.duration_ticks,
                    "restored_hp": definition.max_hp,
                },
                priority=1,
            )

    def _enemy_is_reborning(self, enemy: EnemyState) -> bool:
        """Whether the five-second revival is running.

        「进行持续5s的重生」 is a performance, not a state it fights from, so the
        carrier neither swings nor walks while it lasts.
        """

        runtime = enemy.coefficient_runtime
        return bool(runtime is not None and runtime.reborning_remaining_ticks)

    def _credit_armour_charge(
        self,
        intents: "list[_DamageIntent]",
        definition: EnemyDefinition,
    ) -> None:
        """Feed a killed Dor unit to the armour that killed it.

        【研究成功】: 「吸收范围内的实验产物回复技力，**技力满时启动**」, and the
        Dor units' own 天赋 says the same from the other end: 「被未激活的<R系列
        动力装甲>**击杀**时，为其回复50/100SP」.

        **100 SP is one Dor-3 or two Dor-1**, which is what makes 4-2 a race:
        killing the escort before it reaches the armour is the whole defence.
        """

        for intent in sorted(intents, key=lambda item: item.source_id):
            summon = self.summons.get(intent.source_id)
            if summon is None:
                continue
            summon_definition = self._summon_defs[summon.summon_id]
            if not summon_definition.activation_sp_cost:
                continue
            charged = min(
                summon_definition.activation_sp_cost,
                summon.activation_sp + definition.armour_charge_sp,
            )
            if charged == summon.activation_sp:
                return
            self.summons[intent.source_id] = summon.model_copy(
                update={"activation_sp": charged}
            )
            self._emit(
                EventPhase.DEATH,
                "summon_absorbed_experiment",
                intent.source_id,
                {
                    "enemy_id": definition.enemy_id,
                    "granted_sp": definition.armour_charge_sp,
                    "activation_sp": charged,
                    "activation_sp_cost": summon_definition.activation_sp_cost,
                },
                priority=2,
            )
            if charged >= summon_definition.activation_sp_cost:
                # 「技力满时启动」.  **The turning-on itself is not implemented**:
                # the activated form is a separate enemy
                # (`enemy_1255_lybgpa` / `enemy_1256_lyacpa`) and putting an
                # enemy on a named tile is the shared prerequisite this theme
                # has already registered three times over.  Said out loud rather
                # than approximated.
                self._emit(
                    EventPhase.DEATH,
                    "summon_activation_ready",
                    intent.source_id,
                    {
                        "summon_id": summon.summon_id,
                        "activation_sp_cost": summon_definition.activation_sp_cost,
                    },
                    priority=2,
                )
            return

    def _beam_tiles(self) -> frozenset[tuple[int, int]]:
        """The tiles 重整束流 currently covers.

        PRTS 「“低音号”」, verbatim:

            「部署在重整束流上的**每一位我方干员**，都会令重整束流在这之后的流向
              变为**该干员的部署方向**。」
            「重整束流经过**召唤物**时**不会被重新导向**，但**可被选中的召唤物被视为
              连接在重整束流上**。」

        Derived from live positions every time it is asked, so there is no state
        to keep in step -- the beam is a function of where the emitter and the
        redirectors stand, nothing more.

        **The defender's half is not here.**  「该干员的部署方向」 needs an
        operator to have a facing and `OperatorState` has none, so only the
        stage's own 回音壁 bend the light.  That is registered rather than
        approximated by, say, using the operator's tile alone.
        """

        emitter: tuple[GridPoint, Direction] | None = None
        redirectors: dict[tuple[int, int], Direction] = {}
        for instance_id in sorted(self.summons):
            summon = self.summons[instance_id]
            definition = self._summon_defs.get(summon.summon_id)
            if definition is None or summon.hp <= 0:
                continue
            facing = summon.facing
            if facing is None:
                continue
            if definition.beam_emitter and emitter is None:
                emitter = (summon.position, facing)
            elif definition.beam_redirector:
                redirectors[(summon.position.col, summon.position.row)] = facing
        if emitter is None:
            return frozenset()
        point, direction = emitter
        position = (point.col, point.row)
        covered = {position}
        turned: set[tuple[int, int]] = set()
        width = self.content.map.width
        height = self.content.map.height
        while True:
            step = DIRECTION_STEPS[direction]
            nxt = (position[0] + step[0], position[1] + step[1])
            if not (0 <= nxt[0] < width and 0 <= nxt[1] < height):
                return frozenset(covered)
            position = nxt
            covered.add(position)
            if position in redirectors and position not in turned:
                turned.add(position)
                direction = redirectors[position]

    def _enemy_stands_on_beam(self, enemy: EnemyState) -> bool:
        """Whether this enemy's tile is lit.

        An enemy has no tile of its own -- it is somewhere along a route -- so
        the tile it is nearest to is the one that counts, the same reading the
        shrine's pulse uses.
        """

        beam = self._beam_tiles()
        if not beam:
            return False
        col_milli, row_milli = self._enemy_position_milli(enemy)
        return (
            (col_milli + 500) // 1000,
            (row_milli + 500) // 1000,
        ) in beam

    def _resolve_born_bugs(self) -> None:
        """枯朽萃聚使徒's 【枯朽召唤】: three seeds every ten seconds, behind it.

        PRTS 技能0「枯朽召唤」: 「**0.7s延迟**后，在自身位置，自身位置左1.0，自身
        位置右1.0，**以自身路径**各召唤 **1** 个枯朽之种」.

        「以自身路径」 is the part that makes this summonable here: the seeds take
        the summoner's route and its exact place along it, the way the Redmark
        hologram and the car's unloaded soldiers already do.  The **left/right
        1.0** spread has no representation -- an enemy in this engine is a point
        on a route, not a point on the map -- so all three arrive together and
        that is said out loud rather than approximated with a fake offset.
        """

        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            born = definition.born_bugs
            if born is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "BornBugs carrier lacks deterministic runtime state"
                )
            if runtime.born_bugs_delay_remaining_ticks:
                remaining = runtime.born_bugs_delay_remaining_ticks - 1
                self.enemies[instance_id] = enemy.model_copy(
                    update={
                        "coefficient_runtime": runtime.model_copy(
                            update={"born_bugs_delay_remaining_ticks": remaining}
                        )
                    }
                )
                if remaining:
                    continue
                self._hatch_born_bugs(instance_id, born)
                continue
            if runtime.born_bugs_cooldown_remaining_ticks:
                self.enemies[instance_id] = enemy.model_copy(
                    update={
                        "coefficient_runtime": runtime.model_copy(
                            update={
                                "born_bugs_cooldown_remaining_ticks": (
                                    runtime.born_bugs_cooldown_remaining_ticks - 1
                                )
                            }
                        )
                    }
                )
                continue
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "coefficient_runtime": runtime.model_copy(
                        update={
                            "born_bugs_cooldown_remaining_ticks": born.cooldown_ticks,
                            "born_bugs_delay_remaining_ticks": born.delay_ticks,
                        }
                    )
                }
            )

    def _hatch_born_bugs(self, instance_id: str, born: "BornBugs") -> None:
        """Put the seeds on the board, on their summoner's route and place."""

        parent = self.enemies.get(instance_id)
        if parent is None:
            return
        definition = self._enemy_defs.get(born.summon_enemy_id)
        if definition is None:
            raise UnsupportedMechanicError(
                "BornBugs summon definition disappeared at runtime"
            )
        hatched: list[str] = []
        for _index in range(born.count):
            self.entity_sequence += 1
            seed_id = f"enemy-{self.entity_sequence:06d}"
            self.enemies[seed_id] = EnemyState(
                instance_id=seed_id,
                enemy_id=born.summon_enemy_id,
                route_id=parent.route_id,
                waypoint_index=parent.waypoint_index,
                segment_progress_milli=parent.segment_progress_milli,
                movement_phase_remainder=parent.movement_phase_remainder,
                hp=definition.max_hp,
                path_override=parent.path_override,
                coefficient_runtime=self._initial_enemy_coefficient_runtime(
                    definition
                ),
            )
            hatched.append(seed_id)
        self._emit(
            EventPhase.SPAWN,
            "enemy_born_bugs",
            instance_id,
            {
                "enemy_id": born.summon_enemy_id,
                "instance_ids": tuple(hatched),
                "count": born.count,
            },
            priority=1,
        )

    def _reflect_summon_damage(
        self,
        summon_id: str,
        intents: "list[_DamageIntent]",
        applied: int,
    ) -> None:
        """尖刺宝箱: 「**反弹部分受到的伤害**」, 40% of it, at whoever dealt it.

        `trap_109_smrbox`「尖刺宝箱」 stands **72 times** across IS3 and its
        `atk_scale` is **0.4**.  The chest has 295000 hit points and no attack at
        all, so this is the only thing it can do to anyone -- and it does it to
        the operator opening it, which makes 「哪个箱子」 a real choice rather than
        free loot.

        The share is taken against the damage that **landed**, for the same
        reason the leech and the elemental conversion are: it is the hit that
        comes back, not the attacker's stat block.  Each attacker gets its own
        share, so nobody is repaid for somebody else's blow.
        """

        if applied <= 0:
            return
        summon = self.summons.get(summon_id)
        if summon is None:
            return
        definition = self._summon_defs.get(summon.summon_id)
        if definition is None or not definition.damage_reflect_micros:
            return
        total = sum(item.amount for item in intents if item.amount > 0)
        if total <= 0:
            return
        for intent in sorted(intents, key=lambda item: item.source_id):
            if intent.amount <= 0:
                continue
            attacker = self.operators.get(intent.source_id)
            if attacker is None or attacker.hp <= 0:
                # 「反弹……受到的伤害」 goes back to whoever dealt it; a source that
                # is not a live operator has nowhere to take it.
                continue
            share = intent.amount * applied // total
            payback = _scale_micros(share, definition.damage_reflect_micros)
            if payback <= 0:
                continue
            self.operators[intent.source_id] = attacker.model_copy(
                update={"hp": max(0, attacker.hp - payback)}
            )
            self._emit(
                EventPhase.DAMAGE,
                "summon_reflected_damage",
                summon_id,
                {
                    "summon_id": summon.summon_id,
                    "target_id": intent.source_id,
                    "amount": payback,
                    "reflect_micros": definition.damage_reflect_micros,
                },
                priority=21,
            )

    def _collect_bomb_run_damage(self) -> tuple[_DamageIntent, ...]:
        """暴鸰's 【投弹】: one bomb, then twice the speed and nothing left.

        PRTS 技能0「投弹」: 「**仅攻击范围内存在我方单位时可触发**：对**目标及其周围
        八格**的我方单位造成 **100%** 物理伤害……技能结束后**移速最终提升至200%**。
        ※此技能**仅能触发一次**」, on a carrier whose 天赋 is 「静态刚体，**不进行
        普通攻击**」.

        So killing it before it arrives is the only way to stop the bomb, and
        killing it afterwards is pointless -- it has nothing else.
        """

        intents: list[_DamageIntent] = []
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            bomb = definition.bomb_run
            if bomb is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "bomb run carrier lacks deterministic runtime state"
                )
            if runtime.bomb_run_spent:
                continue
            if runtime.bomb_run_cooldown_remaining_ticks:
                self.enemies[instance_id] = enemy.model_copy(
                    update={
                        "coefficient_runtime": runtime.model_copy(
                            update={
                                "bomb_run_cooldown_remaining_ticks": (
                                    runtime.bomb_run_cooldown_remaining_ticks - 1
                                )
                            }
                        )
                    }
                )
                continue
            target = self._nearest_friendly_in_reach(enemy)
            if target is None:
                continue
            attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
            payload = _scale_micros(attack, bomb.attack_scale_micros)
            centre = (target.position.col, target.position.row)
            tiles = {centre} | {
                (centre[0] + col, centre[1] + row)
                for col, row in AOE_DAMAGE_RANGE_X4
            }
            struck: list[str] = []
            for target_id in sorted((*self.operators, *self.summons)):
                actor: OperatorState | SummonState = (
                    self.operators[target_id]
                    if target_id in self.operators
                    else cast(SummonState, self.summons[target_id])
                )
                if actor.hp <= 0:
                    continue
                if (actor.position.col, actor.position.row) not in tiles:
                    continue
                defence = (
                    self._operator_effective_stat(
                        cast(OperatorState, actor), EffectStat.DEFENSE
                    )
                    if isinstance(actor, OperatorState)
                    else self._summon_effective_stat(
                        cast(SummonState, actor), EffectStat.DEFENSE
                    )
                )
                intents.append(
                    _DamageIntent(
                        instance_id,
                        target_id,
                        self._friendly_target_side(actor),
                        _physical_damage(payload, defence),
                    )
                )
                struck.append(target_id)
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "coefficient_runtime": runtime.model_copy(
                        update={"bomb_run_spent": True}
                    )
                }
            )
            self._emit(
                EventPhase.DAMAGE,
                "enemy_bomb_run",
                instance_id,
                {
                    "target_id": target.instance_id,
                    "target_ids": tuple(struck),
                    "payload": payload,
                    "move_speed_multiple_micros": bomb.move_speed_multiple_micros,
                },
                priority=1,
            )
        return tuple(intents)

    def _collect_crossroads_damage(self) -> tuple[_DamageIntent, ...]:
        """「十字路口」量产型: a cross and a ring, once a second, while it walks.

        PRTS 「“十字路口”量产型」 天赋: 「**每秒执行两项操作**：①向**四个正方向**各
        发射弹道，造成攻击力 **50%** 的远程法术伤害；②对 **0.7半径** 范围内造成攻击力
        **100%** 的近战法术伤害」, on a carrier whose same talent says 「**不进行
        普通攻击**」.

        Standing next to it costs **150%**: the ring and the axis ray both land.
        """

        intents: list[_DamageIntent] = []
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            volley = definition.crossroads_volley
            if volley is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "crossroads carrier lacks deterministic runtime state"
                )
            if runtime.crossroads_cooldown_remaining_ticks:
                self.enemies[instance_id] = enemy.model_copy(
                    update={
                        "coefficient_runtime": runtime.model_copy(
                            update={
                                "crossroads_cooldown_remaining_ticks": (
                                    runtime.crossroads_cooldown_remaining_ticks - 1
                                )
                            }
                        )
                    }
                )
                continue
            attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
            centre = self._enemy_position_milli(enemy)
            origin = ((centre[0] + 500) // 1000, (centre[1] + 500) // 1000)
            ranged_payload = _scale_micros(attack, volley.four_direction_scale_micros)
            close_payload = _scale_micros(attack, volley.close_scale_micros)
            struck: list[str] = []
            #: One ray down each axis, taking whatever it reaches first -- the
            #: same reading 残党正式萨克斯手's rays already use here.
            nearest: dict[tuple[int, int], tuple[int, str]] = {}
            for target_id in sorted((*self.operators, *self.summons)):
                actor: OperatorState | SummonState = (
                    self.operators[target_id]
                    if target_id in self.operators
                    else cast(SummonState, self.summons[target_id])
                )
                if actor.hp <= 0:
                    continue
                delta_col = actor.position.col - origin[0]
                delta_row = actor.position.row - origin[1]
                axis: tuple[int, int] | None = None
                if delta_col == 0 and delta_row:
                    axis = (0, 1 if delta_row > 0 else -1)
                elif delta_row == 0 and delta_col:
                    axis = (1 if delta_col > 0 else -1, 0)
                if axis is None:
                    continue
                distance = abs(delta_col) + abs(delta_row)
                current = nearest.get(axis)
                if current is None or distance < current[0]:
                    nearest[axis] = (distance, target_id)
            for _axis, (_distance, target_id) in sorted(nearest.items()):
                actor = (
                    self.operators[target_id]
                    if target_id in self.operators
                    else cast(SummonState, self.summons[target_id])
                )
                resistance = (
                    self._operator_defs[actor.operator_id].magic_resistance
                    if isinstance(actor, OperatorState)
                    else self._summon_defs[actor.summon_id].magic_resistance
                )
                intents.append(
                    _DamageIntent(
                        instance_id,
                        target_id,
                        self._friendly_target_side(actor),
                        _arts_damage(ranged_payload, resistance),
                    )
                )
                struck.append(target_id)
            close: list[str] = []
            for target_id in sorted((*self.operators, *self.summons)):
                actor = (
                    self.operators[target_id]
                    if target_id in self.operators
                    else cast(SummonState, self.summons[target_id])
                )
                if actor.hp <= 0:
                    continue
                if not self._within_milli(
                    centre, actor.position, volley.close_radius_milli_tiles
                ):
                    continue
                resistance = (
                    self._operator_defs[actor.operator_id].magic_resistance
                    if isinstance(actor, OperatorState)
                    else self._summon_defs[actor.summon_id].magic_resistance
                )
                intents.append(
                    _DamageIntent(
                        instance_id,
                        target_id,
                        self._friendly_target_side(actor),
                        _arts_damage(close_payload, resistance),
                    )
                )
                close.append(target_id)
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "coefficient_runtime": runtime.model_copy(
                        update={
                            "crossroads_cooldown_remaining_ticks": (
                                volley.interval_ticks
                            )
                        }
                    )
                }
            )
            self._emit(
                EventPhase.DAMAGE,
                "enemy_crossroads_volley",
                instance_id,
                {
                    "ray_target_ids": tuple(struck),
                    "ray_payload": ranged_payload,
                    "close_target_ids": tuple(close),
                    "close_payload": close_payload,
                },
                priority=1,
            )
        return tuple(intents)

    def _collect_flame_damage(self) -> tuple[_DamageIntent, ...]:
        """深池焚毁者's 【焚毁】: one operator held in the fire, not a burst.

        PRTS 技能0「焚毁」: 「**锁定攻击范围内一名我方单位**，持续喷射火焰，**每
        0.5s** 对目标造成攻击力 **12%** 的法术普通伤害和攻击力 **6%** 的灼燃损伤」,
        inside a 「持续 **10.6s** 的施法状态」 that 「**受到沉默/眩晕效果时，立刻
        退出**」 and whose cooldown starts 「施法状态下**持续6s后**」.

        So the counterplay is a stun or a silence, not out-healing it -- and the
        cooldown running *inside* the cast is why the cycle is 16 seconds rather
        than 20.
        """

        intents: list[_DamageIntent] = []
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            flame = definition.flame
            if flame is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "flame carrier lacks deterministic runtime state"
                )
            update: dict[str, object] = {}
            if runtime.flame_cooldown_remaining_ticks:
                update["flame_cooldown_remaining_ticks"] = (
                    runtime.flame_cooldown_remaining_ticks - 1
                )
            if runtime.flame_cast_remaining_ticks:
                stopped = bool(
                    enemy.talent_stun_remaining_ticks
                    or self._has_control(instance_id, ControlEffect.SILENCE)
                    or self._has_control(instance_id, ControlEffect.STUN)
                )
                if stopped:
                    # 「施法状态下受到沉默/眩晕效果时，立刻退出施法状态」 and
                    # 「退出施法状态时，中断技能效果并进入冷却时间」.
                    update["flame_cast_remaining_ticks"] = 0
                    update["flame_target_id"] = ""
                    update["flame_pulse_remaining_ticks"] = 0
                    update["flame_cooldown_remaining_ticks"] = flame.cooldown_ticks
                    self._emit(
                        EventPhase.ATTACK,
                        "enemy_flame_interrupted",
                        instance_id,
                        {"reason": "control"},
                        priority=21,
                    )
                    self.enemies[instance_id] = enemy.model_copy(
                        update={
                            "coefficient_runtime": runtime.model_copy(update=update)
                        }
                    )
                    continue
                pulse = runtime.flame_pulse_remaining_ticks
                if pulse:
                    update["flame_pulse_remaining_ticks"] = pulse - 1
                else:
                    intents.extend(
                        self._flame_jet(instance_id, enemy, definition, flame)
                    )
                    update["flame_pulse_remaining_ticks"] = (
                        flame.hit_interval_ticks - 1
                    )
                remaining = runtime.flame_cast_remaining_ticks - 1
                update["flame_cast_remaining_ticks"] = remaining
                if not remaining:
                    update["flame_target_id"] = ""
                self.enemies[instance_id] = enemy.model_copy(
                    update={"coefficient_runtime": runtime.model_copy(update=update)}
                )
                continue
            if update:
                self.enemies[instance_id] = enemy.model_copy(
                    update={"coefficient_runtime": runtime.model_copy(update=update)}
                )
            if runtime.flame_cooldown_remaining_ticks:
                continue
            target = self._nearest_friendly_in_reach(enemy)
            if target is None:
                continue
            enemy = self.enemies[instance_id]
            runtime = cast("EnemyCoefficientRuntimeState", enemy.coefficient_runtime)
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "coefficient_runtime": runtime.model_copy(
                        update={
                            "flame_cast_remaining_ticks": flame.cast_duration_ticks - 1,
                            "flame_target_id": target.instance_id,
                            "flame_pulse_remaining_ticks": (
                                flame.hit_interval_ticks - 1
                            ),
                            # The wait begins **inside** the cast, so a carrier
                            # left alone repeats every 6 + 10 seconds rather than
                            # every 10.6 + 10.
                            "flame_cooldown_remaining_ticks": (
                                flame.cooldown_starts_after_ticks + flame.cooldown_ticks
                                if flame.cooldown_starts_after_ticks
                                else flame.cast_duration_ticks + flame.cooldown_ticks
                            ),
                        }
                    )
                }
            )
            intents.extend(
                self._flame_jet(
                    instance_id, self.enemies[instance_id], definition, flame
                )
            )
            self._emit(
                EventPhase.ATTACK,
                "enemy_flame_started",
                instance_id,
                {
                    "target_id": target.instance_id,
                    "duration_ticks": flame.cast_duration_ticks,
                },
                priority=21,
            )
        return tuple(intents)

    def _flame_jet(
        self,
        instance_id: str,
        enemy: EnemyState,
        definition: EnemyDefinition,
        flame: "FlameSkill",
    ) -> list["_DamageIntent"]:
        """One half-second of fire on the locked target, or nothing."""

        runtime = enemy.coefficient_runtime
        assert runtime is not None
        target_id = runtime.flame_target_id
        actor: OperatorState | SummonState | None = self.operators.get(target_id)
        if actor is None:
            actor = self.summons.get(target_id)
        if actor is None or actor.hp <= 0:
            # 「锁定」: the jet holds one target, so a target that dies or is
            # withdrawn ends the damage without ending the cast.
            return []
        resistance = (
            self._operator_defs[actor.operator_id].magic_resistance
            if isinstance(actor, OperatorState)
            else self._summon_defs[actor.summon_id].magic_resistance
        )
        attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
        payload = _scale_micros(attack, flame.attack_scale_micros)
        if flame.element_ratio_micros and definition.element_damage_type:
            build_up = _scale_micros(attack, flame.element_ratio_micros)
            if build_up > 0:
                self._add_elemental(
                    target_id, definition.element_damage_type, build_up
                )
        self._emit(
            EventPhase.ATTACK,
            "enemy_flame_jet",
            instance_id,
            {"target_id": target_id, "payload": payload},
            priority=21,
        )
        return [
            _DamageIntent(
                instance_id,
                target_id,
                self._friendly_target_side(actor),
                _arts_damage(payload, resistance),
            )
        ]

    def _nearest_friendly_in_reach(
        self, enemy: EnemyState
    ) -> "OperatorState | SummonState | None":
        """The closest friendly inside this enemy's attack range."""

        centre = self._enemy_position_milli(enemy)
        best: tuple[int, str] | None = None
        chosen: OperatorState | SummonState | None = None
        for target_id in sorted((*self.operators, *self.summons)):
            actor: OperatorState | SummonState = (
                self.operators[target_id]
                if target_id in self.operators
                else cast(SummonState, self.summons[target_id])
            )
            if actor.hp <= 0:
                continue
            if not self._enemy_range_contains(enemy, actor.position):
                continue
            delta_col = actor.position.col * 1000 - centre[0]
            delta_row = actor.position.row * 1000 - centre[1]
            key = (delta_col * delta_col + delta_row * delta_row, target_id)
            if best is None or key < best:
                best = key
                chosen = actor
        return chosen

    def _collect_repair_healing(self) -> tuple[_HealIntent, ...]:
        """莱茵生命工程科成员's 【修复】: the enemy that heals the furniture.

        PRTS 技能0「修复」: 「**仅攻击范围内存在生命值未满的未激活的<R系列动力装甲>
        时可触发**：锁定**距离自身最近**的目标持续施法，最长持续 **10秒**，每秒
        治疗目标**最大生命值5%**。每次治疗后若目标生命值已满，**立刻打断技能**。
        施法期间**受到沉默影响后，立即结束技能**。**索敌不受阻挡影响**」.

        In this engine 「未激活」 needs no flag: the dormant armour is a *token*
        (a summon) and the activated one is a separate *enemy*, so the summon
        side already means inactive.  「索敌不受阻挡影响」 likewise falls out --
        nothing here consults `blocked_by`.
        """

        intents: list[_HealIntent] = []
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            repair = definition.repair
            if repair is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "repair carrier lacks deterministic runtime state"
                )
            update: dict[str, object] = {}
            if runtime.repair_remaining_ticks:
                target = self.summons.get(runtime.repair_target_id)
                silenced = self._has_control(instance_id, ControlEffect.SILENCE)
                full = target is not None and target.hp >= (
                    self._summon_defs[target.summon_id].max_hp
                )
                if target is None or target.hp <= 0 or full or silenced:
                    # 「若目标生命值已满，立刻打断技能」 /
                    # 「受到沉默影响后，立即结束技能」.
                    self._end_repair(instance_id, enemy, runtime, repair, silenced)
                    continue
                pulse = runtime.repair_pulse_remaining_ticks
                if pulse:
                    update["repair_pulse_remaining_ticks"] = pulse - 1
                else:
                    amount = (
                        self._summon_defs[target.summon_id].max_hp
                        * repair.hp_ratio_micros
                        // 1_000_000
                    )
                    intents.append(_HealIntent(instance_id, target.instance_id, amount))
                    update["repair_pulse_remaining_ticks"] = repair.interval_ticks - 1
                    self._emit(
                        EventPhase.ATTACK,
                        "enemy_repair_pulse",
                        instance_id,
                        {"target_id": target.instance_id, "amount": amount},
                        priority=31,
                    )
                remaining = runtime.repair_remaining_ticks - 1
                update["repair_remaining_ticks"] = remaining
                if not remaining:
                    update["repair_target_id"] = ""
                    update["repair_cooldown_remaining_ticks"] = repair.cooldown_ticks
                self.enemies[instance_id] = enemy.model_copy(
                    update={"coefficient_runtime": runtime.model_copy(update=update)}
                )
                continue
            if runtime.repair_cooldown_remaining_ticks:
                self.enemies[instance_id] = enemy.model_copy(
                    update={
                        "coefficient_runtime": runtime.model_copy(
                            update={
                                "repair_cooldown_remaining_ticks": (
                                    runtime.repair_cooldown_remaining_ticks - 1
                                )
                            }
                        )
                    }
                )
                continue
            target = self._nearest_repairable_armour(enemy)
            if target is None:
                continue
            amount = (
                self._summon_defs[target.summon_id].max_hp
                * repair.hp_ratio_micros
                // 1_000_000
            )
            intents.append(_HealIntent(instance_id, target.instance_id, amount))
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "coefficient_runtime": runtime.model_copy(
                        update={
                            "repair_remaining_ticks": repair.duration_ticks - 1,
                            "repair_target_id": target.instance_id,
                            "repair_pulse_remaining_ticks": repair.interval_ticks - 1,
                        }
                    )
                }
            )
            self._emit(
                EventPhase.ATTACK,
                "enemy_repair_started",
                instance_id,
                {
                    "target_id": target.instance_id,
                    "duration_ticks": repair.duration_ticks,
                    "amount": amount,
                },
                priority=31,
            )
        return tuple(intents)

    def _end_repair(
        self,
        instance_id: str,
        enemy: EnemyState,
        runtime: "EnemyCoefficientRuntimeState",
        repair: "RepairSkill",
        silenced: bool,
    ) -> None:
        """Drop the cast and start the wait, saying which sentence ended it."""

        self.enemies[instance_id] = enemy.model_copy(
            update={
                "coefficient_runtime": runtime.model_copy(
                    update={
                        "repair_remaining_ticks": 0,
                        "repair_target_id": "",
                        "repair_pulse_remaining_ticks": 0,
                        "repair_cooldown_remaining_ticks": repair.cooldown_ticks,
                    }
                )
            }
        )
        self._emit(
            EventPhase.ATTACK,
            "enemy_repair_interrupted",
            instance_id,
            {"reason": "silenced" if silenced else "target_full_or_gone"},
            priority=31,
        )

    def _nearest_repairable_armour(self, enemy: EnemyState) -> "SummonState | None":
        """The closest dormant armour in range that is missing health.

        「仅攻击范围内存在**生命值未满**的未激活的<R系列动力装甲>时可触发：锁定
        **距离自身最近**的目标」 -- both halves, in that order.
        """

        centre = self._enemy_position_milli(enemy)
        best: tuple[int, str] | None = None
        chosen: SummonState | None = None
        for summon_id in sorted(self.summons):
            summon = self.summons[summon_id]
            if summon.summon_id not in INACTIVE_POWER_ARMOUR_SUMMON_IDS:
                continue
            definition = self._summon_defs[summon.summon_id]
            if summon.hp <= 0 or summon.hp >= definition.max_hp:
                continue
            if not self._enemy_range_contains(enemy, summon.position):
                continue
            delta_col = summon.position.col * 1000 - centre[0]
            delta_row = summon.position.row * 1000 - centre[1]
            distance = delta_col * delta_col + delta_row * delta_row
            key = (distance, summon_id)
            if best is None or key < best:
                best = key
                chosen = summon
        return chosen

    def _collect_immolation_damage(self) -> tuple[_DamageIntent, ...]:
        """游击队萨卡兹术师's 【献祭】: an aura the pulse widens instead of killing.

        能力: 「周期性施法，对周围所有单位造成相当于攻击力的法术伤害；接触脉冲波
        时，**不受伤害**且攻击**范围扩大**」.  PRTS 技能【献祭】 is 「攻击力100%的
        近战途径法术溅射伤害」 at radius **1.5**, and the 强化模式 copy is the same
        damage at **2.5** -- which is the entire difference between the shipped
        `Immo` and `Immo[Rage]`, since their blackboards are identical.

        `pulse_empowered` is already the latch the shrine sets when its pulse
        washes over a `pulse_immune` enemy, so 强化模式 costs nothing extra here:
        it is the same flag, read for a radius instead of a damage type.
        """

        intents: list[_DamageIntent] = []
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            immolation = definition.immolation
            if immolation is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "immolation carrier lacks deterministic runtime state"
                )
            if runtime.immolation_cooldown_remaining_ticks:
                self.enemies[instance_id] = enemy.model_copy(
                    update={
                        "coefficient_runtime": runtime.model_copy(
                            update={
                                "immolation_cooldown_remaining_ticks": (
                                    runtime.immolation_cooldown_remaining_ticks - 1
                                )
                            }
                        )
                    }
                )
                continue
            radius = (
                immolation.empowered_radius_milli_tiles
                if enemy.pulse_empowered
                else immolation.radius_milli_tiles
            )
            attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
            payload = _scale_micros(attack, immolation.attack_scale_micros)
            centre = self._enemy_position_milli(enemy)
            struck: list[str] = []
            for target_id in sorted((*self.operators, *self.summons)):
                actor: OperatorState | SummonState = (
                    self.operators[target_id]
                    if target_id in self.operators
                    else cast(SummonState, self.summons[target_id])
                )
                if not self._within_milli(centre, actor.position, radius):
                    continue
                resistance = (
                    self._operator_defs[actor.operator_id].magic_resistance
                    if isinstance(actor, OperatorState)
                    else self._summon_defs[actor.summon_id].magic_resistance
                )
                intents.append(
                    _DamageIntent(
                        instance_id,
                        target_id,
                        self._friendly_target_side(actor),
                        _arts_damage(payload, resistance),
                    )
                )
                struck.append(target_id)
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "coefficient_runtime": runtime.model_copy(
                        update={
                            "immolation_cooldown_remaining_ticks": (
                                immolation.cooldown_ticks
                            )
                        }
                    )
                }
            )
            self._emit(
                EventPhase.DAMAGE,
                "enemy_immolation",
                instance_id,
                {
                    "payload": payload,
                    "radius_milli_tiles": radius,
                    "empowered": enemy.pulse_empowered,
                    "target_ids": tuple(struck),
                },
                priority=1,
            )
        return tuple(intents)

    def _collect_doom_damage(self) -> tuple[_DamageIntent, ...]:
        """「萨米的意志」's 【叹息】: the deadline, and what it costs to miss it.

        PRTS 「“萨米的意志”」 技能0: 「对场上**全体**我方单位造成攻击力 **150%** 的
        法术伤害；技能结束时**扣除30目标生命值**，随后**自身死亡**」.

        `value` -30 is thirty **life points**, not damage, on a run that carries
        a handful -- so this is the clock ending the fight rather than a move to
        play around.  The boss leaves with it, which is why the whole encounter
        is a damage race with a stated deadline.
        """

        intents: list[_DamageIntent] = []
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            doom = definition.doom
            if doom is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "doom carrier lacks deterministic runtime state"
                )
            if runtime.doom_cooldown_remaining_ticks:
                self.enemies[instance_id] = enemy.model_copy(
                    update={
                        "coefficient_runtime": runtime.model_copy(
                            update={
                                "doom_cooldown_remaining_ticks": (
                                    runtime.doom_cooldown_remaining_ticks - 1
                                )
                            }
                        )
                    }
                )
                continue
            attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
            payload = _scale_micros(attack, doom.attack_scale_micros)
            struck: list[str] = []
            for target_id in sorted((*self.operators, *self.summons)):
                actor: OperatorState | SummonState = (
                    self.operators[target_id]
                    if target_id in self.operators
                    else cast(SummonState, self.summons[target_id])
                )
                resistance = (
                    self._operator_defs[actor.operator_id].magic_resistance
                    if isinstance(actor, OperatorState)
                    else self._summon_defs[actor.summon_id].magic_resistance
                )
                intents.append(
                    _DamageIntent(
                        instance_id,
                        target_id,
                        self._friendly_target_side(actor),
                        _arts_damage(payload, resistance),
                    )
                )
                struck.append(target_id)
            if doom.life_point_cost:
                self.life_points = max(0, self.life_points - doom.life_point_cost)
            # 「随后自身死亡」 -- the deadline takes the boss with it.
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "hp": 0,
                    "coefficient_runtime": runtime.model_copy(
                        update={"doom_cooldown_remaining_ticks": doom.cooldown_ticks}
                    ),
                }
            )
            self._emit(
                EventPhase.DAMAGE,
                "enemy_doom",
                instance_id,
                {
                    "payload": payload,
                    "target_ids": tuple(struck),
                    "life_damage": doom.life_point_cost,
                    "life_points": self.life_points,
                },
                priority=1,
            )
        return tuple(intents)

    def _collect_backstab_damage(self) -> tuple[_DamageIntent, ...]:
        """梦魇效仿者's 【恐惧凝集】: everyone whose back tile nobody is watching.

        PRTS 「梦魇效仿者」 技能0: 「对场上**所有**身后一格**没有视野**的我方
        **干员**造成攻击力 **85%** 的物理普通伤害」.

        Board-wide, so the carrier's own attack range does not enter into it;
        operators only, so devices are untouched; and 「视野」 is whether **any**
        friendly covers the tile behind, the operator itself included.
        """

        intents: list[_DamageIntent] = []
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            backstab = definition.backstab
            if backstab is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "backstab carrier lacks deterministic runtime state"
                )
            if runtime.backstab_cooldown_remaining_ticks:
                self.enemies[instance_id] = enemy.model_copy(
                    update={
                        "coefficient_runtime": runtime.model_copy(
                            update={
                                "backstab_cooldown_remaining_ticks": (
                                    runtime.backstab_cooldown_remaining_ticks - 1
                                )
                            }
                        )
                    }
                )
                continue
            attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
            payload = _scale_micros(attack, backstab.attack_scale_micros)
            struck: list[str] = []
            for operator_id in sorted(self.operators):
                operator = self.operators[operator_id]
                behind = self._tile_behind(operator)
                if behind is None or self._tile_is_watched(behind):
                    continue
                defense = self._operator_effective_stat(
                    operator, EffectStat.DEFENSE
                )
                intents.append(
                    _DamageIntent(
                        instance_id,
                        operator_id,
                        "operator",
                        _physical_damage(payload, defense),
                    )
                )
                struck.append(operator_id)
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "coefficient_runtime": runtime.model_copy(
                        update={
                            "backstab_cooldown_remaining_ticks": (
                                backstab.cooldown_ticks
                            )
                        }
                    )
                }
            )
            self._emit(
                EventPhase.DAMAGE,
                "enemy_backstab",
                instance_id,
                {"payload": payload, "target_ids": tuple(struck)},
                priority=2,
            )
        return tuple(intents)

    @staticmethod
    def _tile_behind(operator: OperatorState) -> "tuple[int, int] | None":
        """The tile one step opposite the way this operator faces.

        The rotation convention is the one the effect ranges already use: base
        offsets point RIGHT, and DOWN/LEFT/UP rotate them.  Applying that to a
        single step forward gives the four facings below, and behind is its
        negation.
        """

        forward = {
            Direction.RIGHT: (1, 0),
            Direction.LEFT: (-1, 0),
            Direction.DOWN: (0, 1),
            Direction.UP: (0, -1),
        }.get(operator.direction)
        if forward is None:
            return None
        return (
            operator.position.col - forward[0],
            operator.position.row - forward[1],
        )

    def _tile_is_watched(self, tile: "tuple[int, int]") -> bool:
        """Whether any operator's attack range covers this tile.

        Exact offsets when the operator has them, the radius otherwise -- the
        same two cases `_operator_range_contains` distinguishes, because 「视野」
        in this skill is simply "somebody could shoot there".
        """

        col, row = tile
        for operator in self.operators.values():
            definition = self._operator_defs[operator.operator_id]
            offsets = definition.attack_range_offsets
            if offsets:
                if (col, row) in self._operator_covered_cells(operator, offsets):
                    return True
                continue
            reach = self._operator_effective_stat(
                operator, EffectStat.ATTACK_RANGE_TILES
            )
            delta_col = abs(operator.position.col - col)
            delta_row = abs(operator.position.row - row)
            if delta_col * delta_col + delta_row * delta_row <= reach * reach:
                return True
        return False

    def _collect_predation_damage(self) -> tuple[_DamageIntent, ...]:
        """狡兽 eats a 爪兽 and puts on its shell.

        PRTS 「狡兽」: 「''通常状态''时：可以攻击自身**半径0.8范围**内的硬甲爪兽/
        刚甲爪兽（不可对空），每次造成自身攻击力**1500%**的真实伤害；击倒后获得
        <甲壳>（**仅触发1次**）」.

        The first enemy-on-enemy attack in this engine, and with those numbers a
        one-shot: 1090 x 15 = 16350 against 硬甲爪兽's 14000, 1260 x 15 = 18900
        against 刚甲爪兽's 18000.  True damage, so neither defence nor the prey's
        own weakness resistance applies -- which matters, because the prey's
        -80% would otherwise turn a kill into two bites.

        Cadence is the predator's own attack interval: 「可以攻击」 is an attack,
        and nothing in the data or on PRTS gives the bite a clock of its own.
        """

        intents: list[_DamageIntent] = []
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            predation = definition.predation
            if predation is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "predation carrier lacks deterministic runtime state"
                )
            if runtime.predation_spent or enemy.attack_cooldown:
                continue
            if runtime.predation_cooldown_remaining_ticks:
                # The five seconds `DropShield`/`ShieldUp` ship.  Spent here
                # rather than in the runtime pass so that a carrier with no prey
                # in reach still burns it -- it is a clock, not a wind-up.
                self.enemies[instance_id] = enemy.model_copy(
                    update={
                        "coefficient_runtime": runtime.model_copy(
                            update={
                                "predation_cooldown_remaining_ticks": (
                                    runtime.predation_cooldown_remaining_ticks - 1
                                )
                            }
                        )
                    }
                )
                continue
            reach_squared = predation.reach_milli_tiles**2
            col_milli, row_milli = self._enemy_position_milli(enemy)
            prey_id = None
            for candidate_id in sorted(self.enemies):
                if candidate_id == instance_id:
                    continue
                candidate = self.enemies[candidate_id]
                if candidate.enemy_id not in predation.prey_enemy_ids:
                    continue
                if (
                    self._enemy_defs[candidate.enemy_id].movement_layer
                    is not MovementLayer.GROUND
                ):
                    # 「不可对空」
                    continue
                other_col, other_row = self._enemy_position_milli(candidate)
                delta_col = other_col - col_milli
                delta_row = other_row - row_milli
                if delta_col * delta_col + delta_row * delta_row > reach_squared:
                    continue
                prey_id = candidate_id
                break
            if prey_id is None:
                continue
            prey = self.enemies[prey_id]
            attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
            damage = _scale_micros(attack, predation.attack_scale_micros)
            intents.append(_DamageIntent(instance_id, prey_id, "enemy", damage))
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "attack_cooldown": self._enemy_effective_stat(
                        enemy, EffectStat.ATTACK_INTERVAL_TICKS
                    )
                }
            )
            if damage >= prey.hp:
                # 「击倒……后获得<甲壳>」.  The kill is this bite's, so the shell is
                # decided here rather than by watching the corpse next tick.
                enemy = self.enemies[instance_id]
                shell_update = {
                    "predation_shell": True,
                    "predation_spent": True,
                }
                cleared_stun = enemy.talent_stun_remaining_ticks
                self.enemies[instance_id] = enemy.model_copy(
                    update={
                        "coefficient_runtime": runtime.model_copy(
                            update=shell_update
                        ),
                        # 「获得晕眩**反制**」 -- PRTS separates 反制 from 免疫:
                        # 「会**清除**掉包含对应异常效果的 Buff，且阻止此类 Buff 被
                        # 添加」.  So a stun already running ends here.
                        "talent_stun_remaining_ticks": 0,
                    }
                )
                self._emit(
                    EventPhase.DAMAGE,
                    "enemy_predation_shell_taken",
                    instance_id,
                    {
                        "prey_id": prey_id,
                        "prey_enemy_id": prey.enemy_id,
                        "damage": damage,
                        "cleared_stun_ticks": cleared_stun,
                    },
                    priority=1,
                )
            else:
                self._emit(
                    EventPhase.DAMAGE,
                    "enemy_predation_bite",
                    instance_id,
                    {"prey_id": prey_id, "damage": damage},
                    priority=2,
                )
        return tuple(intents)

    def _predation_facing_milli(
        self, enemy: EnemyState
    ) -> "tuple[int, int] | None":
        """Which way the shell makes it look.

        PRTS: 「③**未被阻挡时**，始终面向我方干员数量较多的方向（相同数量则保持
        当前方向）」.  A protected front plus a chosen facing is the whole point:
        the carrier turns its -80% side toward wherever most of the squad is.

        Ties keep the stored answer, which is why the direction is state rather
        than a function of the board.
        """

        definition = self._enemy_defs[enemy.enemy_id]
        predation = definition.predation
        runtime = enemy.coefficient_runtime
        if (
            predation is None
            or not predation.shell_faces_operator_majority
            or runtime is None
            or not runtime.predation_shell
            or enemy.blocked_by is not None
        ):
            return None
        if not runtime.predation_facing_col:
            return None
        return (runtime.predation_facing_col * 1000, 0)

    def _advance_predation_facing(self, instance_id: str) -> None:
        """Recompute the shelled carrier's facing from where our squad stands."""

        enemy = self.enemies[instance_id]
        definition = self._enemy_defs[enemy.enemy_id]
        predation = definition.predation
        runtime = enemy.coefficient_runtime
        if (
            predation is None
            or not predation.shell_faces_operator_majority
            or runtime is None
            or not runtime.predation_shell
            or enemy.blocked_by is not None
        ):
            return
        col_milli, _row_milli = self._enemy_position_milli(enemy)
        left = sum(
            1
            for actor in self.operators.values()
            if actor.position.col * 1000 < col_milli
        )
        right = sum(
            1
            for actor in self.operators.values()
            if actor.position.col * 1000 > col_milli
        )
        if left == right:
            return  # 「相同数量则保持当前方向」
        facing = -1 if left > right else 1
        if facing == runtime.predation_facing_col:
            return
        self.enemies[instance_id] = enemy.model_copy(
            update={
                "coefficient_runtime": runtime.model_copy(
                    update={"predation_facing_col": facing}
                )
            }
        )

    def _collect_quill_spray_damage(self) -> tuple[_DamageIntent, ...]:
        """箭棘兽's 【箭棘】: a rear-facing sector, fired by a lost-HP check.

        PRTS 「箭棘兽」: 「受到伤害后进行**10%的已损失生命值判定**，若通过判定则对
        自身**背面180°半径1.8范围**的扇形区域内的所有我方单位造成攻击力**110%**的
        物理普通伤害」.

        Two things this shares with the rest of the engine and one it does not:

        * The **geometry** is the same projection the weakness resistance uses --
          a friendly is behind the carrier when its offset projects negatively
          onto the direction of travel.  A carrier with no next waypoint has no
          facing and therefore no back, so it cannot spray.
        * The **timing** is a tick behind the damage that earns it, like every
          other reaction here (the death explosion's fuse works the same way).
          PRTS says 「受到伤害后」; a tick is the resolution this engine has.
        """

        intents: list[_DamageIntent] = []
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            rows = quill_spray_rows(definition)
            if rows is None:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None:
                raise UnsupportedMechanicError(
                    "quill spray carrier lacks deterministic runtime state"
                )
            if not lost_hp_check_passes(
                enemy.hp, definition.max_hp, rows["hp_ratio"], runtime.lost_hp_stage
            ):
                continue
            stage = lost_hp_next_stage(
                enemy.hp, definition.max_hp, rows["hp_ratio"]
            )
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "coefficient_runtime": runtime.model_copy(
                        update={"lost_hp_stage": stage}
                    )
                }
            )
            facing = self._enemy_facing_milli(enemy)
            if facing is None:
                # No direction of travel, so no back to spray from.  Recorded as
                # a spent stage rather than a held one: the check passed.
                self._emit(
                    EventPhase.DAMAGE,
                    "enemy_quill_spray_without_facing",
                    instance_id,
                    {"stage": stage},
                    priority=3,
                )
                continue
            attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
            payload = _scale_micros(attack, rows["atk_scale"])
            radius_squared = (rows["range_radius"] // 1000) ** 2
            centre_col, centre_row = self._enemy_position_milli(enemy)
            struck: list[str] = []
            for target_id in sorted((*self.operators, *self.summons)):
                actor: OperatorState | SummonState = (
                    self.operators[target_id]
                    if target_id in self.operators
                    else cast(SummonState, self.summons[target_id])
                )
                delta_col = actor.position.col * 1000 - centre_col
                delta_row = actor.position.row * 1000 - centre_row
                if delta_col * delta_col + delta_row * delta_row > radius_squared:
                    continue
                if delta_col * facing[0] + delta_row * facing[1] > 0:
                    # 180 degrees, so anything not ahead of it is behind it --
                    # including the two directions exactly abreast.
                    continue
                defense = (
                    self._operator_effective_stat(actor, EffectStat.DEFENSE)
                    if isinstance(actor, OperatorState)
                    else self._summon_effective_stat(actor, EffectStat.DEFENSE)
                )
                intents.append(
                    _DamageIntent(
                        instance_id,
                        target_id,
                        self._friendly_target_side(actor),
                        _physical_damage(payload, defense),
                    )
                )
                struck.append(target_id)
            self._emit(
                EventPhase.DAMAGE,
                "enemy_quill_spray",
                instance_id,
                {
                    "stage": stage,
                    "payload": payload,
                    "target_ids": tuple(struck),
                },
                priority=2,
            )
        return tuple(intents)

    def _collect_death_explosion_damage(self) -> tuple[_DamageIntent, ...]:
        """Advance every fuse and resolve the ones that reach zero."""

        intents: list[_DamageIntent] = []
        for instance_id in sorted(tuple(self.pending_explosions)):
            blast = self.pending_explosions[instance_id]
            remaining = blast.fuse_remaining_ticks - 1
            if remaining > 0:
                self.pending_explosions[instance_id] = blast.model_copy(
                    update={"fuse_remaining_ticks": remaining}
                )
                continue
            del self.pending_explosions[instance_id]
            radius_squared = blast.radius_milli_tiles * blast.radius_milli_tiles
            hit: list[str] = []
            for target_id in sorted((*self.operators, *self.summons)):
                actor: OperatorState | SummonState = (
                    self.operators[target_id]
                    if target_id in self.operators
                    else cast(SummonState, self.summons[target_id])
                )
                delta_col = actor.position.col * 1000 - blast.center_col_milli
                delta_row = actor.position.row * 1000 - blast.center_row_milli
                if delta_col * delta_col + delta_row * delta_row > radius_squared:
                    continue
                hit.append(target_id)
                intents.append(
                    _DamageIntent(
                        blast.instance_id,
                        target_id,
                        self._friendly_target_side(actor),
                        blast.damage,
                    )
                )
                if blast.cold_ticks:
                    self._apply_cold_to_friendly(target_id, blast.cold_ticks)
            self._emit(
                EventPhase.DAMAGE,
                "death_explosion_detonated",
                blast.instance_id,
                {
                    "source_enemy_id": blast.source_enemy_id,
                    "target_ids": tuple(hit),
                    "damage": blast.damage,
                    "cold_ticks": blast.cold_ticks,
                },
            )
        return tuple(intents)

    def _apply_cold_to_friendly(self, target_id: str, ticks: int) -> None:
        """Apply one 敌方寒冷 to a friendly actor by id, escalating if needed."""

        if target_id in self.operators:
            actor = self.operators[target_id]
            self.operators[target_id] = actor.model_copy(
                update=self._enemy_cold_update(actor, ticks)
            )
        else:
            summon = cast(SummonState, self.summons[target_id])
            self.summons[target_id] = summon.model_copy(
                update=self._enemy_cold_update(summon, ticks)
            )

    def _collect_return_body_effect_damage(self) -> tuple[_DamageIntent, ...]:
        """Advance pollution, bleed, and artillery on the canonical 30 TPS clock."""
        self._refresh_pollution_auras()
        self._refresh_device_pollution_auras()

        intents: list[_DamageIntent] = []
        friendlies: tuple[OperatorState | SummonState, ...] = tuple(
            sorted(
                (*self.operators.values(), *self.summons.values()),
                key=lambda item: item.instance_id,
            )
        )
        high_tiles = {
            (tile.col, tile.row) for tile in self.content.map.high_ground_tiles
        }

        # All shipped Return Body zones attach the same-name pollution payload;
        # overlapping zones therefore contribute at most one packet per target
        # on a pulse tick.  Prefer the larger exact packet if future variants
        # differ, then the stable source id.
        pollution_packets: dict[
            str, tuple[int, str, Literal["operator", "summon"]]
        ] = {}
        for zone_id in sorted(tuple(self.polluted_zones)):
            zone = self.polluted_zones[zone_id]
            remaining = zone.remaining_ticks - 1
            pulse_remaining = zone.pulse_remaining_ticks - 1
            if pulse_remaining == 0:
                radius_squared = zone.radius_milli_tiles**2
                for actor in friendlies:
                    delta_col = actor.position.col * 1000 - zone.center_col_milli
                    delta_row = actor.position.row * 1000 - zone.center_row_milli
                    if delta_col * delta_col + delta_row * delta_row > radius_squared:
                        continue
                    damage = (
                        zone.high_ground_damage
                        if (actor.position.col, actor.position.row) in high_tiles
                        else zone.low_ground_damage
                    )
                    packet = (
                        damage,
                        zone_id,
                        self._friendly_target_side(actor),
                    )
                    current = pollution_packets.get(actor.instance_id)
                    if current is None or (damage, zone_id) > (current[0], current[1]):
                        pollution_packets[actor.instance_id] = packet
                pulse_remaining = RETURN_BODY_POLLUTION_PULSE_TICKS
            if remaining == 0:
                del self.polluted_zones[zone_id]
                self._emit(
                    EventPhase.DAMAGE,
                    "polluted_zone_expired",
                    zone_id,
                    {},
                    priority=1,
                )
            else:
                self.polluted_zones[zone_id] = zone.model_copy(
                    update={
                        "remaining_ticks": remaining,
                        "pulse_remaining_ticks": pulse_remaining,
                    }
                )
        for target_id, (damage, source_id, target_side) in sorted(
            pollution_packets.items()
        ):
            intents.append(_DamageIntent(source_id, target_id, target_side, damage))
            self._emit(
                EventPhase.DAMAGE,
                "polluted_zone_pulse",
                source_id,
                {"target_id": target_id, "damage": damage},
                priority=2,
            )

        for actor in friendlies:
            if not actor.coefficient_bleeding_remaining_ticks:
                continue
            remaining = actor.coefficient_bleeding_remaining_ticks - 1
            pulse_remaining = actor.coefficient_bleeding_pulse_remaining_ticks - 1
            if pulse_remaining == 0:
                hp_loss = actor.coefficient_bleeding_is_hp_loss
                if hp_loss:
                    # 「每秒流失30生命值」 -- no type and no non-lethal clause, so
                    # it lands as shipped and can finish the operator off.
                    damage = actor.coefficient_bleeding_damage
                else:
                    damage = _arts_damage(
                        actor.coefficient_bleeding_damage,
                        self._friendly_magic_resistance(actor),
                    )
                damage = self._scale_fixed(
                    damage,
                    self.global_modifiers.enemy_damage_multiplier_milli,
                )
                intents.append(
                    _DamageIntent(
                        "coefficient-bleeding",
                        actor.instance_id,
                        self._friendly_target_side(actor),
                        damage,
                        nonlethal=not hp_loss,
                    )
                )
                self._emit(
                    EventPhase.DAMAGE,
                    "enemy_coefficient_bleeding_pulse",
                    actor.instance_id,
                    {"damage": damage, "nonlethal": not hp_loss},
                    priority=3,
                )
                pulse_remaining = RETURN_BODY_BLEEDING_PULSE_TICKS
            update = (
                {
                    "coefficient_bleeding_remaining_ticks": remaining,
                    "coefficient_bleeding_pulse_remaining_ticks": pulse_remaining,
                }
                if remaining
                else {
                    "coefficient_bleeding_remaining_ticks": 0,
                    "coefficient_bleeding_pulse_remaining_ticks": 0,
                    "coefficient_bleeding_damage": 0,
                    "coefficient_bleeding_is_hp_loss": False,
                }
            )
            updated = actor.model_copy(update=update)
            if isinstance(actor, OperatorState):
                self.operators[actor.instance_id] = cast(OperatorState, updated)
            else:
                self.summons[actor.instance_id] = cast(SummonState, updated)

        for enemy_id in sorted(tuple(self.enemies)):
            enemy = self.enemies[enemy_id]
            if enemy.enemy_id not in RETURN_BODY_CANNON_IDS:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None or not runtime.cannon_barrage_remaining_shots:
                continue
            next_shot = runtime.cannon_barrage_next_shot_ticks - 1
            if next_shot:
                self.enemies[enemy_id] = enemy.model_copy(
                    update={
                        "coefficient_runtime": runtime.model_copy(
                            update={"cannon_barrage_next_shot_ticks": next_shot}
                        )
                    }
                )
                continue
            center = cast(GridPoint, runtime.cannon_barrage_center)
            candidates = [
                actor
                for actor in friendlies
                if (
                    actor.position.col - center.col,
                    actor.position.row - center.row,
                )
                in RETURN_BODY_CANNON_RANGE_OFFSETS
            ]
            target = (
                max(
                    candidates,
                    key=lambda actor: (
                        Fraction(actor.hp, self._friendly_max_hp(actor)),
                        actor.instance_id,
                    ),
                )
                if candidates
                else None
            )
            if target is not None:
                attack_scale = coefficient_value(
                    self._enemy_defs[enemy.enemy_id], "Cannon.atk_scale"
                )
                if attack_scale is None:
                    raise UnsupportedMechanicError(
                        "Return Body cannon scale disappeared at runtime"
                    )
                attack = (
                    self._enemy_effective_stat(enemy, EffectStat.ATTACK)
                    * attack_scale
                    // 1_000_000
                )
                damage = _arts_damage(
                    attack,
                    self._friendly_magic_resistance(target),
                )
                damage = self._scale_fixed(
                    damage,
                    self.global_modifiers.enemy_damage_multiplier_milli,
                )
                intents.append(
                    _DamageIntent(
                        enemy_id,
                        target.instance_id,
                        self._friendly_target_side(target),
                        damage,
                    )
                )
                self._emit(
                    EventPhase.ATTACK,
                    "enemy_coefficient_cannon_shot",
                    enemy_id,
                    {
                        "target_id": target.instance_id,
                        "damage": damage,
                        "remaining_shots_before": runtime.cannon_barrage_remaining_shots,
                    },
                    priority=1,
                )
            remaining_shots = runtime.cannon_barrage_remaining_shots - 1
            runtime_update: dict[str, object]
            enemy_update: dict[str, object] = {}
            if remaining_shots:
                runtime_update = {
                    "cannon_barrage_remaining_shots": remaining_shots,
                    "cannon_barrage_next_shot_ticks": (
                        RETURN_BODY_CANNON_SHOT_INTERVAL_TICKS
                    ),
                }
            else:
                cooldown = coefficient_value(
                    self._enemy_defs[enemy.enemy_id], "Cannon.cooldown"
                )
                if cooldown is None:
                    raise UnsupportedMechanicError(
                        "Return Body cannon cooldown disappeared at runtime"
                    )
                runtime_update = {
                    "cannon_skill_cooldown_remaining_ticks": (
                        self._seconds_micros_to_ticks(cooldown)
                    ),
                    "cannon_barrage_remaining_shots": 0,
                    "cannon_barrage_next_shot_ticks": 0,
                    "cannon_barrage_center": None,
                }
                enemy_update["attack_cooldown"] = (
                    self._enemy_effective_stat(
                        enemy, EffectStat.ATTACK_INTERVAL_TICKS
                    )
                    - 1
                )
            enemy_update["coefficient_runtime"] = runtime.model_copy(
                update=runtime_update
            )
            self.enemies[enemy_id] = enemy.model_copy(update=enemy_update)
        return tuple(intents)

    def _tick_blood_debt(self) -> None:
        """血债账款与清算时刻(「复仇黑手」`trap_092_vgctrl`, ev-5)。

        PRTS 装置页: 总进度 **500**, 每秒自然积累 **3/秒**(约 166.7 秒攒满),
        「不同的敌人可以以其特有的方式影响血债账款的进度」; 满了进**清算时刻**,
        **35 秒**内**全场敌人攻击力 +20%、防御力 +15%**, 「持续期间血债账款不再
        继续累积」, 结束后「从 0 开始重新积累」。

        账本存在**装置自己身上**(`SummonState`), 不在引擎的全局字段上: 一张图上
        可以摆不止一台, 而且这样它跟着快照走, 不需要另写序列化。
        """

        for instance_id in sorted(self.summons):
            summon = self.summons[instance_id]
            ledger = self._summon_defs[summon.summon_id].blood_debt_ledger
            if ledger is None:
                continue
            if summon.reckoning_remaining_ticks:
                remaining = summon.reckoning_remaining_ticks - 1
                update = {"reckoning_remaining_ticks": remaining}
                if remaining == 0:
                    # 「结束后从 0 开始重新积累」。
                    update["blood_debt_charge_milli"] = 0
                self.summons[instance_id] = summon.model_copy(update=update)
                if remaining == 0:
                    self._emit(
                        EventPhase.ACTION,
                        "blood_debt_reckoning_ended",
                        instance_id,
                        {"summon_id": summon.summon_id},
                        priority=1,
                    )
                continue
            charge = min(
                ledger.max_charge_milli,
                summon.blood_debt_charge_milli + ledger.charge_per_tick_milli,
            )
            if charge < ledger.max_charge_milli:
                self.summons[instance_id] = summon.model_copy(
                    update={"blood_debt_charge_milli": charge}
                )
                continue
            self._start_reckoning(instance_id, ledger)

    def _start_reckoning(self, instance_id: str, ledger: BloodDebtLedger) -> None:
        """账款攒满: 进清算时刻, 账款停在满格直到它结束。"""

        summon = self.summons[instance_id]
        self.summons[instance_id] = summon.model_copy(
            update={
                "blood_debt_charge_milli": ledger.max_charge_milli,
                "reckoning_remaining_ticks": ledger.reckoning_ticks,
            }
        )
        self._emit(
            EventPhase.ACTION,
            "blood_debt_reckoning_started",
            instance_id,
            {
                "summon_id": summon.summon_id,
                "duration_ticks": ledger.reckoning_ticks,
                "attack_bonus_micros": ledger.reckoning_attack_bonus_micros,
                "defense_bonus_micros": ledger.reckoning_defense_bonus_micros,
            },
            priority=1,
        )

    def _credit_blood_debt(self, amount_micros: int) -> bool:
        """把一笔血债记进场上的账本, 返回有没有账本收下它。

        「清算时刻持续期间，血债账款不再继续累积」——所以正在清算的账本不收账,
        而这**不是丢弃**: 它已经在给全场敌人加成了。
        """

        credited = False
        for instance_id in sorted(self.summons):
            summon = self.summons[instance_id]
            ledger = self._summon_defs[summon.summon_id].blood_debt_ledger
            if ledger is None or summon.reckoning_remaining_ticks:
                continue
            credited = True
            charge = min(
                ledger.max_charge_milli,
                summon.blood_debt_charge_milli + amount_micros // 1000,
            )
            if charge >= ledger.max_charge_milli:
                self.summons[instance_id] = summon.model_copy(
                    update={"blood_debt_charge_milli": charge}
                )
                self._start_reckoning(instance_id, ledger)
            else:
                self.summons[instance_id] = summon.model_copy(
                    update={"blood_debt_charge_milli": charge}
                )
        return credited

    def _reckoning_bonus_micros(self, stat: EffectStat) -> int:
        """清算时刻给**全场敌人**的加成, 取场上正在清算的账本里最大的一份。"""

        best = 0
        for summon in self.summons.values():
            if not summon.reckoning_remaining_ticks:
                continue
            ledger = self._summon_defs[summon.summon_id].blood_debt_ledger
            if ledger is None:
                continue
            if stat is EffectStat.ATTACK:
                best = max(best, ledger.reckoning_attack_bonus_micros)
            elif stat is EffectStat.DEFENSE:
                best = max(best, ledger.reckoning_defense_bonus_micros)
        return best

    def _tick_enemy_herd_lifecycle(self) -> None:
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            if not enemy.herd_resting_remaining_ticks:
                continue
            remaining = enemy.herd_resting_remaining_ticks - 1
            self.enemies[instance_id] = enemy.model_copy(
                update={"herd_resting_remaining_ticks": remaining}
            )
            if remaining == 0:
                self._emit(
                    EventPhase.MOVE,
                    "enemy_herd_resting_ended",
                    instance_id,
                    {"reason": "source_duration_elapsed"},
                )

    def _tick_enemy_talent_controls(self) -> None:
        """Advance source-bound operator talent controls before this tick acts."""

        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            if not enemy.talent_stun_remaining_ticks:
                continue
            remaining = enemy.talent_stun_remaining_ticks - 1
            self.enemies[instance_id] = enemy.model_copy(
                update={"talent_stun_remaining_ticks": remaining}
            )
            if remaining == 0:
                self._emit(
                    EventPhase.TERMINAL,
                    "operator_talent_stun_expired",
                    instance_id,
                    {},
                )

    def _start_enemy_timed_self_modifiers(
        self,
        instance_id: str,
        enemy: EnemyDefinition,
    ) -> tuple[tuple[ActiveEffectState, EnemyTimedSelfModifierDefinition], ...]:
        """Create one spawn modifier batch without exposing a partial batch."""

        started: list[tuple[ActiveEffectState, EnemyTimedSelfModifierDefinition]] = []
        first_sequence = self.effect_sequence + 1
        for index, modifier in enumerate(
            sorted(enemy.initial_timed_modifiers, key=lambda item: item.modifier_id)
        ):
            state = ActiveEffectState(
                instance_id=f"effect-{first_sequence + index:06d}",
                effect_id=enemy_timed_modifier_effect_id(enemy.enemy_id, modifier.modifier_id),
                source_instance_id=instance_id,
                source_skill_id=f"enemy_spawn:{enemy.enemy_id}",
                target_instance_id=instance_id,
                remaining_ticks=modifier.duration_ticks,
            )
            started.append((state, modifier))
        self.effect_sequence += len(started)
        self.active_effects.update((state.instance_id, state) for state, _ in started)
        return tuple(started)

    def _block_constraint_coefficient(self, enemy: EnemyState):
        if enemy.enemy_id != _BLOCK_CONSTRAINT_ENEMY_ID:
            return None
        if enemy.spawn_occurrence_id is None:
            return None
        spawn_id, separator, ordinal = enemy.spawn_occurrence_id.rpartition(":")
        if not separator or ordinal != "0" or spawn_id not in _BLOCK_CONSTRAINT_SPAWN_BINDINGS:
            return None
        return next(
            (
                coefficient
                for coefficient in self._enemy_defs[enemy.enemy_id].mechanic_coefficients
                if coefficient.key == _BLOCK_CONSTRAINT_KEY
            ),
            None,
        )

    def _evaluate_block_constraint(
        self,
        enemy: EnemyState,
        *,
        point: GridPoint,
        waypoint_index: int,
        blocker_instance_ids: tuple[str, ...],
        blocker_count: int,
        event_phase: EventPhase,
    ) -> EnemyBlockConstraintState | None:
        coefficient = self._block_constraint_coefficient(enemy)
        if coefficient is None:
            return None
        source_sha256 = coefficient.source_sha256
        if source_sha256 is None:
            raise UnsupportedMechanicError("block constraint coefficient lacks source SHA-256")
        spawn_id = cast(str, enemy.spawn_occurrence_id).rpartition(":")[0]
        state = EnemyBlockConstraintState(
            content_version=cast(str, self.content.content_version),
            source_package_hash=cast(str, self.content.source_package_hash),
            stage_id=cast(str, self.content.stage_id),
            spawn_id=spawn_id,
            enemy_instance_id=enemy.instance_id,
            enemy_id=enemy.enemy_id,
            coefficient_key=_BLOCK_CONSTRAINT_KEY,
            value_micros=coefficient.value_micros,
            required_block_count=coefficient.value_micros // 1_000_000,
            tick=self.tick,
            position=point,
            route_id=enemy.route_id,
            waypoint_index=waypoint_index,
            blocker_instance_ids=blocker_instance_ids,
            blocker_count=blocker_count,
            constraint_state=(
                "satisfied"
                if blocker_count >= coefficient.value_micros // 1_000_000
                else "unsatisfied"
            ),
            source_pointer=coefficient.source_pointer,
            source_sha256=source_sha256,
        )
        details = state.model_dump(mode="json")
        self._emit(
            event_phase,
            "enemy_block_constraint_evaluated",
            enemy.instance_id,
            details,
        )
        old_state = enemy.block_constraint.constraint_state if enemy.block_constraint else None
        if old_state != state.constraint_state:
            kind = (
                "enemy_block_constraint_satisfied"
                if state.constraint_state == "satisfied"
                else "enemy_block_constraint_released"
            )
            if kind == "enemy_block_constraint_satisfied" or old_state == "satisfied":
                self._emit(event_phase, kind, enemy.instance_id, details, priority=1)
        return state

    def _reconcile_blocks(self, *, event_phase: EventPhase = EventPhase.BLOCK) -> None:
        # Block-slot assignment is sticky: an enemy that already holds a slot
        # keeps it for as long as it (and its blocker) remain eligible and
        # co-located, and is released only when the blocked enemy departs
        # (death/leak/teleport/etc. -- it simply drops out of self.enemies or
        # loses eligibility) or the blocker's own capacity shrinks below what
        # it is currently holding. A later-arriving enemy can only take a
        # slot that is genuinely vacant; it can never evict an incumbent,
        # regardless of instance id. This mirrors the real game's blocking
        # queue, where an already-blocked unit is not bumped by a new
        # arrival. See PRIORITY-DIRECTIVE §73/§74 for the root-cause audit
        # this fixes (engine_semantics_changed: sticky_block_slots).
        old_enemy_blocks = {enemy_id: enemy.blocked_by for enemy_id, enemy in self.enemies.items()}
        friendlies: dict[str, OperatorState | SummonState] = self.operators | self.summons
        assignments: dict[str, list[str]] = {friendly_id: [] for friendly_id in friendlies}
        blocked_by: dict[str, str | None] = {enemy_id: None for enemy_id in self.enemies}
        constraint_states: dict[str, EnemyBlockConstraintState | None] = {}

        eligible_points: dict[str, GridPoint] = {}
        for enemy_id in sorted(self.enemies):
            enemy = self.enemies[enemy_id]
            if self._enemy_defs[enemy.enemy_id].movement_layer is MovementLayer.FLYING:
                continue
            point = self._enemy_grid_point(enemy)
            if point is None:
                continue
            if (
                self._enemy_has_active_hologram(enemy)
                or self._enemy_is_airborne(enemy)
                or self._enemy_is_overloading(enemy)
                or self._enemy_is_blinking(enemy)
            ):
                # 「近地悬浮期间无法被阻挡」 -- the same exclusion the reserve
                # path applies, and this is the pass that would otherwise
                # re-block it on the very next tick.  R-31 mid-【超负载】 joins
                # it for the same reason: it released its own block.
                continue
            eligible_points[enemy_id] = point

        # Pass 1: sticky retention for ordinary (non-coefficient-constrained)
        # enemies that already held a slot. This runs before any new
        # arrival is considered, so incumbents can never be displaced.
        # The rare minimum-block-count constraint mechanic (a single scripted
        # enemy/spawn binding) is excluded here and always goes through pass
        # 2's from-scratch evaluation, unchanged from prior behaviour, since
        # it emits its own per-tick constraint-satisfaction telemetry.
        for enemy_id, point in eligible_points.items():
            enemy = self.enemies[enemy_id]
            if self._block_constraint_coefficient(enemy) is not None:
                continue
            previous_blocker = old_enemy_blocks.get(enemy_id)
            if previous_blocker is None:
                continue
            friendly = friendlies.get(previous_blocker)
            if friendly is None or friendly.position != point:
                continue
            if len(assignments[previous_blocker]) >= self._friendly_block_count(friendly):
                continue
            assignments[previous_blocker].append(enemy_id)
            blocked_by[enemy_id] = previous_blocker

        # Pass 2: fill whatever slots remain open with enemies that are not
        # yet blocked, in ascending instance-id order (the existing arrival
        # proxy) -- unchanged from prior behaviour except that incumbents
        # from pass 1 have already claimed their slots and are skipped here.
        for enemy_id, point in eligible_points.items():
            if blocked_by[enemy_id] is not None:
                continue
            enemy = self.enemies[enemy_id]
            candidates = sorted(
                friendly.instance_id
                for friendly in friendlies.values()
                if friendly.position == point
                and len(assignments[friendly.instance_id]) < self._friendly_block_count(friendly)
            )
            coefficient = self._block_constraint_coefficient(enemy)
            required = coefficient.value_micros // 1_000_000 if coefficient else 1
            if coefficient is None:
                selected = tuple(candidates[:1])
                evaluated = selected
                blocker_count = len(selected)
            else:
                best = min(
                    candidates,
                    key=lambda candidate: (
                        -self._friendly_block_count(friendlies[candidate]),
                        candidate,
                    ),
                    default=None,
                )
                blocker_count = (
                    self._friendly_block_count(friendlies[best])
                    if best is not None
                    else 0
                )
                evaluated = (best,) if best is not None else ()
                selected = evaluated if blocker_count >= required else ()
            if coefficient is not None:
                constraint_states[enemy_id] = self._evaluate_block_constraint(
                    enemy,
                    point=point,
                    waypoint_index=enemy.waypoint_index,
                    blocker_instance_ids=evaluated,
                    blocker_count=blocker_count,
                    event_phase=event_phase,
                )
            if not selected:
                continue
            for blocker_id in selected:
                assignments[blocker_id].append(enemy_id)
            blocked_by[enemy_id] = selected[0]

        for operator_id, operator in tuple(self.operators.items()):
            self.operators[operator_id] = self._fast_copy(
                operator, {"blocked_enemy_ids": tuple(assignments[operator_id])}
            )
        for summon_id, summon in tuple(self.summons.items()):
            self.summons[summon_id] = summon.model_copy(
                update={"blocked_enemy_ids": tuple(assignments[summon_id])}
            )
        for enemy_id, enemy in tuple(self.enemies.items()):
            new_blocker = blocked_by[enemy_id]
            update: dict[str, object] = {"blocked_by": new_blocker}
            if enemy_id in constraint_states:
                update["block_constraint"] = constraint_states[enemy_id]
            self.enemies[enemy_id] = self._fast_copy(enemy, update)
            if old_enemy_blocks[enemy_id] != new_blocker:
                kind = "block" if new_blocker else "unblock"
                self._emit(
                    event_phase,
                    kind,
                    enemy_id,
                    {"operator_instance_id": new_blocker or old_enemy_blocks[enemy_id]},
                )

    def _collect_attacks(self) -> tuple[_DamageIntent, ...]:
        intents: list[_DamageIntent] = []
        for instance_id in sorted(self.operators):
            operator = self.operators[instance_id]
            definition = self._operator_defs[operator.operator_id]
            if operator.attack_cooldown > 0:
                self.operators[instance_id] = self._fast_copy(
                    operator, {"attack_cooldown": operator.attack_cooldown - 1}
                )
                continue
            if self._has_control(
                instance_id,
                ControlEffect.STUN,
                ControlEffect.DISARM,
            ):
                continue
            if self._queue_auto_next_heal(instance_id, operator):
                continue
            targets = self._operator_targets(operator)
            if not targets:
                continue
            attack = self._operator_effective_stat(operator, EffectStat.ATTACK)
            armed_packets = [
                effect
                for state in operator.skills
                if state.armed
                for effect in self._skill_definition(operator, state.skill_id).effects
                if isinstance(effect, AttackScaleEffectDefinition)
            ]
            if len(armed_packets) > 1:
                raise EffectVMError("multiple armed attack_scale packets are ambiguous")
            attack_packet = armed_packets[0] if armed_packets else None
            packet_attack = (
                self._scale_fixed(attack, attack_packet.scale_milli)
                if attack_packet is not None
                else attack
            )
            packet_attack = self._scale_fixed(
                packet_attack,
                definition.normal_attack_scale_milli,
            )
            if definition.normal_attack_random_scale_probability_milli:
                rng_stream = cast(
                    str, definition.normal_attack_random_scale_rng_stream
                )
                rng_roll = self._draw_rng(rng_stream, 1, 1000)
                random_scale_triggered = (
                    rng_roll
                    <= definition.normal_attack_random_scale_probability_milli
                )
                if random_scale_triggered:
                    packet_attack = self._scale_fixed(
                        packet_attack,
                        definition.normal_attack_random_scale_milli,
                    )
                self._emit(
                    EventPhase.ATTACK,
                    "normal_attack_random_scale",
                    instance_id,
                    {
                        "rng_stream": rng_stream,
                        "rng_draw": self.rng_streams[rng_stream],
                        "roll_milli": rng_roll,
                        "probability_milli": (
                            definition.normal_attack_random_scale_probability_milli
                        ),
                        "scale_milli": definition.normal_attack_random_scale_milli,
                        "triggered": random_scale_triggered,
                        "source_semantic_id": (
                            definition.normal_attack_random_scale_source_semantic_id
                        ),
                    },
                    priority=9,
                )
            packet_hits = attack_packet.hit_count if attack_packet is not None else 1
            packet_interval_ticks = (
                attack_packet.packet_interval_ticks if attack_packet is not None else 0
            )
            packet_projectile_travel_ticks = (
                attack_packet.projectile_travel_ticks
                if attack_packet is not None
                and attack_packet.projectile_travel_ticks is not None
                else definition.projectile_travel_ticks
            )
            target_damage: list[tuple[EnemyState, int]] = []
            for target in targets:
                damage = (
                    _arts_damage(
                        packet_attack,
                        self._enemy_magic_resistance(target),
                    )
                    if definition.damage_type == "arts"
                    else _physical_damage(
                        packet_attack,
                        self._enemy_effective_stat(target, EffectStat.DEFENSE),
                    )
                )
                target_damage.append(
                    (
                        target,
                        self._scale_fixed(
                            damage,
                            self.global_modifiers.friendly_damage_multiplier_milli,
                        ),
                    )
                )
            effective_interval = self._operator_effective_stat(
                operator, EffectStat.ATTACK_INTERVAL_TICKS
            )
            self.operators[instance_id] = operator.model_copy(
                update={"attack_cooldown": effective_interval - 1}
            )
            self._emit(
                EventPhase.ATTACK,
                "attack",
                instance_id,
                {
                    "target_id": target_damage[0][0].instance_id,
                    "damage": target_damage[0][1] * packet_hits,
                    "damage_per_packet": target_damage[0][1],
                    "targets": [
                        {
                            "target_id": target.instance_id,
                            "damage": damage * packet_hits,
                            "damage_per_packet": damage,
                        }
                        for target, damage in target_damage
                    ],
                    "projectile_travel_ticks": packet_projectile_travel_ticks,
                    "packet_interval_ticks": packet_interval_ticks,
                    "attack_scale_milli": (
                        attack_packet.scale_milli if attack_packet is not None else 1000
                    ),
                    "hit_count": packet_hits,
                },
                priority=10,
            )
            attack_event_sequence = self.event_sequence
            armed_damage, armed_healing = self._consume_armed_payloads(
                instance_id,
                SkillActivationPolicy.NEXT_ATTACK,
                trigger_event_sequence=attack_event_sequence,
                event_target_id=target_damage[0][0].instance_id,
            )
            if armed_healing:
                raise EffectVMError("next_attack payload emitted healing")
            intents.extend(armed_damage)
            self._queue_sp_event(
                instance_id,
                SkillSpEvent.NORMAL_ATTACK_COMMITTED,
                trigger_event_sequence=attack_event_sequence,
            )
            for target, _damage in target_damage:
                for effect in definition.normal_attack_effects:
                    existing = next(
                        (
                            active
                            for active in self.active_effects.values()
                            if active.effect_id == effect.effect_id
                            and active.source_instance_id == instance_id
                            and active.target_instance_id == target.instance_id
                        ),
                        None,
                    )
                    if existing is not None:
                        self.active_effects[existing.instance_id] = existing.model_copy(
                            update={"remaining_ticks": effect.duration_ticks}
                        )
                        effect_instance_id = existing.instance_id
                    else:
                        self.effect_sequence += 1
                        effect_instance_id = f"effect-{self.effect_sequence:06d}"
                        self.active_effects[effect_instance_id] = ActiveEffectState(
                            instance_id=effect_instance_id,
                            effect_id=effect.effect_id,
                            source_instance_id=instance_id,
                            source_skill_id=f"normal_attack:{operator.operator_id}",
                            target_instance_id=target.instance_id,
                            remaining_ticks=effect.duration_ticks,
                        )
                    self._emit(
                        EventPhase.ATTACK,
                        "normal_attack_effect",
                        instance_id,
                        {
                            "effect_id": effect.effect_id,
                            "effect_instance_id": effect_instance_id,
                            "target_id": target.instance_id,
                        },
                        priority=11,
                    )
            for target, damage in target_damage:
                if not damage:
                    continue
                for packet_index in range(packet_hits):
                    if packet_projectile_travel_ticks:
                        self._launch_projectile(
                            instance_id,
                            target.instance_id,
                            damage,
                            packet_projectile_travel_ticks
                            + packet_index * packet_interval_ticks,
                            splash_attack=(
                                attack if definition.projectile_splash_radius_tiles else 0
                            ),
                            splash_radius_tiles=definition.projectile_splash_radius_tiles,
                            packet_index=packet_index,
                            packet_count=packet_hits,
                        )
                    else:
                        intents.append(
                            _DamageIntent(instance_id, target.instance_id, "enemy", damage)
                        )
                if definition.normal_attack_aftershock_count:
                    target_col, target_row = self._enemy_position_milli(target)
                    for victim in sorted(
                        self.enemies.values(), key=lambda item: item.instance_id
                    ):
                        if (
                            definition.normal_attack_aftershock_ground_only
                            and self._enemy_defs[victim.enemy_id].movement_layer
                            is MovementLayer.FLYING
                        ):
                            continue
                        victim_col, victim_row = self._enemy_position_milli(victim)
                        radius = definition.normal_attack_aftershock_radius_milli_tiles
                        if (
                            abs(victim_col - target_col) > radius
                            or abs(victim_row - target_row) > radius
                        ):
                            continue
                        aftershock_attack = self._scale_fixed(
                            attack,
                            definition.normal_attack_aftershock_scale_milli,
                        )
                        aftershock_damage = (
                            _arts_damage(
                                aftershock_attack,
                                self._enemy_magic_resistance(victim),
                            )
                            if definition.damage_type == "arts"
                            else _physical_damage(
                                aftershock_attack,
                                self._enemy_effective_stat(victim, EffectStat.DEFENSE),
                            )
                        )
                        aftershock_damage = self._scale_fixed(
                            aftershock_damage
                            * definition.normal_attack_aftershock_count,
                            self.global_modifiers.friendly_damage_multiplier_milli,
                        )
                        if aftershock_damage:
                            intents.append(
                                _DamageIntent(
                                    instance_id,
                                    victim.instance_id,
                                    "enemy",
                                    aftershock_damage,
                                )
                            )
                    self._emit(
                        EventPhase.ATTACK,
                        "normal_attack_aftershock",
                        instance_id,
                        {
                            "target_id": target.instance_id,
                            "count": definition.normal_attack_aftershock_count,
                            "scale_milli": (
                                definition.normal_attack_aftershock_scale_milli
                            ),
                            "radius_milli_tiles": (
                                definition.normal_attack_aftershock_radius_milli_tiles
                            ),
                            "source_semantic_id": (
                                definition.normal_attack_aftershock_source_semantic_id
                            ),
                        },
                        priority=12,
                    )
                if definition.normal_attack_aftershock_proc_probability_milli:
                    rng_stream = cast(
                        str, definition.normal_attack_aftershock_proc_rng_stream
                    )
                    rng_roll = self._draw_rng(rng_stream, 1, 1000)
                    proc_triggered = (
                        rng_roll
                        <= definition.normal_attack_aftershock_proc_probability_milli
                    )
                    proc_victims: list[dict[str, int | str]] = []
                    if proc_triggered:
                        target_col, target_row = self._enemy_position_milli(target)
                        for victim in sorted(
                            self.enemies.values(), key=lambda item: item.instance_id
                        ):
                            victim_col, victim_row = self._enemy_position_milli(victim)
                            radius = (
                                definition.normal_attack_aftershock_proc_radius_milli_tiles
                            )
                            if (
                                abs(victim_col - target_col) > radius
                                or abs(victim_row - target_row) > radius
                            ):
                                continue
                            proc_attack = self._scale_fixed(
                                attack,
                                definition.normal_attack_aftershock_proc_scale_milli,
                            )
                            proc_damage = (
                                _arts_damage(
                                    proc_attack,
                                    self._enemy_magic_resistance(victim),
                                )
                                if definition.damage_type == "arts"
                                else _physical_damage(
                                    proc_attack,
                                    self._enemy_effective_stat(
                                        victim, EffectStat.DEFENSE
                                    ),
                                )
                            )
                            proc_damage = self._scale_fixed(
                                proc_damage,
                                self.global_modifiers.friendly_damage_multiplier_milli,
                            )
                            if proc_damage:
                                intents.append(
                                    _DamageIntent(
                                        instance_id,
                                        victim.instance_id,
                                        "enemy",
                                        proc_damage,
                                    )
                                )
                            if (
                                definition.normal_attack_aftershock_proc_stun_ticks
                                and not self._enemy_defs[
                                    victim.enemy_id
                                ].immunities.stun
                            ):
                                # Blocked at the write as well as in
                                # `_has_control`: this sets a state field
                                # that the charge ramp reads directly,
                                # without going through the control query.
                                self.enemies[victim.instance_id] = victim.model_copy(
                                    update={
                                        "talent_stun_remaining_ticks": max(
                                            victim.talent_stun_remaining_ticks,
                                            definition.normal_attack_aftershock_proc_stun_ticks,
                                        )
                                    }
                                )
                            proc_victims.append(
                                {
                                    "target_id": victim.instance_id,
                                    "damage": proc_damage,
                                }
                            )
                    self._emit(
                        EventPhase.ATTACK,
                        "normal_attack_aftershock_proc",
                        instance_id,
                        {
                            "target_id": target.instance_id,
                            "rng_stream": rng_stream,
                            "rng_draw": self.rng_streams[rng_stream],
                            "roll_milli": rng_roll,
                            "probability_milli": (
                                definition.normal_attack_aftershock_proc_probability_milli
                            ),
                            "scale_milli": (
                                definition.normal_attack_aftershock_proc_scale_milli
                            ),
                            "radius_milli_tiles": (
                                definition.normal_attack_aftershock_proc_radius_milli_tiles
                            ),
                            "stun_ticks": (
                                definition.normal_attack_aftershock_proc_stun_ticks
                            ),
                            "triggered": proc_triggered,
                            "victims": proc_victims,
                            "source_semantic_id": (
                                definition.normal_attack_aftershock_proc_source_semantic_id
                            ),
                        },
                        priority=13,
                    )

        for instance_id in sorted(self.summons):
            summon = self.summons[instance_id]
            definition = self._summon_defs[summon.summon_id]
            if summon.attack_cooldown > 0:
                self.summons[instance_id] = summon.model_copy(
                    update={"attack_cooldown": summon.attack_cooldown - 1}
                )
                continue
            if self._has_control(
                instance_id,
                ControlEffect.STUN,
                ControlEffect.DISARM,
            ):
                continue
            target = self._summon_target(summon)
            if target is None:
                continue
            damage = _physical_damage(
                self._summon_effective_stat(summon, EffectStat.ATTACK),
                self._enemy_effective_stat(target, EffectStat.DEFENSE),
            )
            damage = self._scale_fixed(
                damage,
                self.global_modifiers.friendly_damage_multiplier_milli,
            )
            self.summons[instance_id] = summon.model_copy(
                update={
                    "attack_cooldown": self._summon_effective_stat(
                        summon, EffectStat.ATTACK_INTERVAL_TICKS
                    )
                    - 1
                }
            )
            self._emit(
                EventPhase.ATTACK,
                "summon_attack",
                instance_id,
                {"target_id": target.instance_id, "damage": damage},
                priority=11,
            )
            attack_event_sequence = self.event_sequence
            armed_damage, armed_healing = self._consume_armed_payloads(
                instance_id,
                SkillActivationPolicy.NEXT_ATTACK,
                trigger_event_sequence=attack_event_sequence,
                event_target_id=target.instance_id,
            )
            if armed_healing:
                raise EffectVMError("next_attack summon payload emitted healing")
            intents.extend(armed_damage)
            self._queue_sp_event(
                instance_id,
                SkillSpEvent.NORMAL_ATTACK_COMMITTED,
                trigger_event_sequence=attack_event_sequence,
            )
            if damage:
                intents.append(_DamageIntent(instance_id, target.instance_id, "enemy", damage))

        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            runtime = enemy.coefficient_runtime
            contact = self._rush_contact_damage(enemy)
            if contact is not None and runtime is not None:
                intents.extend(self._apply_rush_contact(instance_id, enemy, contact))
            charge = self._crash_contact_damage(enemy)
            if charge is not None and runtime is not None:
                # 凶豕兽's charge, on the same episode bookkeeping and its own
                # wider reach.  The carriers are disjoint, so the shared
                # `rush_contact_hit_ids` cannot collide.
                intents.extend(
                    self._apply_rush_contact(
                        instance_id,
                        self.enemies[instance_id],
                        charge,
                        radius_milli=CRASH_CONTACT_RADIUS_MILLI_TILES,
                        source_key="Crash.atk_scale",
                    )
                )
            if (
                enemy.enemy_id in MAINLINE12_DISGUISED_IDS
                and (
                    runtime is None
                    or runtime.identity_detection_phase != "revealed"
                )
            ):
                continue
            if enemy.route_teleport_destination_index is not None:
                continue
            if definition.enemy_id in RETURN_BODY_CANNON_IDS:
                if runtime is None:
                    raise UnsupportedMechanicError(
                        "Return Body cannon lacks its runtime state"
                    )
                if runtime.cannon_barrage_remaining_shots:
                    # The source enemy is immune to control during the barrage,
                    # and its normal attack is replaced until all ten shots end.
                    continue
                if (
                    runtime.cannon_skill_cooldown_remaining_ticks == 0
                    and not self._has_control(
                        instance_id,
                        ControlEffect.STUN,
                        ControlEffect.DISARM,
                    )
                    and not enemy.herd_resting_remaining_ticks
                ):
                    lock_candidates = [
                        actor
                        for actor in (*self.operators.values(), *self.summons.values())
                        if self._enemy_range_contains(enemy, actor.position)
                    ]
                    if lock_candidates:
                        lock_target = max(
                            lock_candidates,
                            key=lambda actor: (
                                self._friendly_max_hp(actor),
                                actor.instance_id,
                            ),
                        )
                        next_runtime = runtime.model_copy(
                            update={
                                "cannon_barrage_remaining_shots": (
                                    RETURN_BODY_CANNON_BARRAGE_SHOTS
                                ),
                                "cannon_barrage_next_shot_ticks": (
                                    RETURN_BODY_CANNON_WINDUP_TICKS
                                ),
                                "cannon_barrage_center": lock_target.position,
                            }
                        )
                        self.enemies[instance_id] = enemy.model_copy(
                            update={"coefficient_runtime": next_runtime}
                        )
                        self._emit(
                            EventPhase.ATTACK,
                            "enemy_coefficient_cannon_barrage_started",
                            instance_id,
                            {
                                "lock_target_id": lock_target.instance_id,
                                "center": lock_target.position.model_dump(mode="json"),
                                "shots": RETURN_BODY_CANNON_BARRAGE_SHOTS,
                                "windup_ticks": RETURN_BODY_CANNON_WINDUP_TICKS,
                                "shot_interval_ticks": (
                                    RETURN_BODY_CANNON_SHOT_INTERVAL_TICKS
                                ),
                            },
                            priority=1,
                        )
                        continue
            if enemy.attack_cooldown > 0:
                self.enemies[instance_id] = enemy.model_copy(
                    update={"attack_cooldown": enemy.attack_cooldown - 1}
                )
                continue
            if self._has_control(
                instance_id,
                ControlEffect.STUN,
                ControlEffect.DISARM,
            ):
                continue
            if enemy.herd_resting_remaining_ticks:
                continue
            if self._enemy_is_reborning(enemy):
                continue
            if definition.attack_mode == "none":
                continue
            if self._enemy_is_overloading(enemy):
                # Its punches are the skill's own cadence; an ordinary swing on
                # top would be a second attack the rule does not describe.
                continue
            ranged_targets: list[OperatorState | SummonState] = []
            if definition.attack_mode == "ranged" or (
                # 「未阻挡时远程攻击」 -- read from the carrier's own description,
                # not from an id set: that set held the base 帝国前锋精锐 and not
                # its 百战 elite, so the elite stood in b-6 shooting nothing.
                definition.ranged_while_unblocked
                and enemy.blocked_by is None
            ):
                ranged_targets = [
                    *(
                        target
                        for target in self.operators.values()
                        if self._enemy_range_contains(enemy, target.position)
                    ),
                    *(
                        target
                        for target in self.summons.values()
                        if self._enemy_range_contains(enemy, target.position)
                    ),
                ]
                if definition.enemy_id in self._polluted_tank_ids:
                    high_tiles = {
                        (tile.col, tile.row)
                        for tile in self.content.map.high_ground_tiles
                    }
                    ranged_targets = [
                        target
                        for target in ranged_targets
                        if (target.position.col, target.position.row) not in high_tiles
                    ]
                # 「更容易受到敌方的攻击」 -- a friendly's own taunt tier comes
                # first, and only equal-taunt friendlies fall back to the game's
                # last-deployed priority.  Tiers are compared, never summed.
                # Operators carry no taunt tier today, so they read as 0.
                # 「**优先攻击位于重整束流上的目标**」 -- the violinist picks
                # a lit target over an unlit one before anything else is
                # weighed, because the light is what lets its shot travel.
                lit_first = (
                    self._beam_tiles()
                    if definition.beam_conduction is not None
                    else frozenset()
                )
                ranged_targets.sort(
                    key=lambda item: (
                        -self._friendly_taunt_level(item),
                        # 「**优先攻击位于重整束流上的目标**」 sits **below** taunt,
                        # the way 末路狂徒's 「目标嘲讽等级相同时，优先攻击指挥终端」
                        # spells out for its own preference: a stated priority is
                        # a tiebreak, not an override of the taunt tier.
                        (item.position.col, item.position.row) not in lit_first
                        if lit_first
                        else False,
                        -int(item.instance_id.rsplit("-", 1)[-1]),
                    )
                )
            target_ids: list[str] = []
            if enemy.blocked_by is not None:
                target_ids.append(enemy.blocked_by)
            # The count is declared by the carrier when it has one
            # (`vampire.attack@max_target` = 3 on 萨卡兹宿主百夫长, 「同时攻击三个
            # 目标」).  The id allow-list stays as the compatibility branch for
            # the standby-revive pair, whose count is not in their data; the
            # larger of the two wins so neither path loses targets.
            declared_targets = coefficient_value(
                definition, "vampire.attack@max_target"
            )
            volley_targets = (
                RANGED_MODE_TARGETS_BY_ENEMY.get(definition.enemy_id, 1)
                if coefficient_value(definition, "Passive.attack@ranged.atk_scale")
                is not None
                and enemy.blocked_by is None
                else 1
            )
            target_limit = max(
                volley_targets,
                2 if definition.enemy_id in STANDBY_REVIVE_RANGED_MULTI_TARGET_IDS else 1,
                1 if declared_targets is None else declared_targets // 1_000_000,
            )
            for ranged_target in ranged_targets:
                # The ceiling is checked *before* appending.  Checking after it
                # let a blocked ranged attacker take its blocker plus one extra
                # target on every swing, however low its limit was -- the
                # blocker already filled the list, so the first ranged
                # candidate was appended and only then did the loop stop.
                if len(target_ids) >= target_limit:
                    break
                if ranged_target.instance_id not in target_ids:
                    target_ids.append(ranged_target.instance_id)
            if not target_ids:
                continue
            attack_targets: list[
                tuple[str, OperatorState | None, SummonState | None]
            ] = []
            for candidate_id in target_ids:
                operator_target = self.operators.get(candidate_id)
                summon_target_candidate = self.summons.get(candidate_id)
                if operator_target is None and summon_target_candidate is None:
                    continue
                attack_targets.append(
                    (candidate_id, operator_target, summon_target_candidate)
                )
            if not attack_targets:
                continue
            target_id, target, summon_target = attack_targets[0]
            first_aoe_scale = (
                coefficient_value(definition, "AOEAttack.atk_scale")
                if (
                    definition.enemy_id in MAINLINE12_FIRST_AOE_IDS
                    and runtime is not None
                    and runtime.attacks_committed == 0
                )
                else None
            )
            if first_aoe_scale is not None:
                primary_position = (
                    target.position
                    if target is not None
                    else cast(SummonState, summon_target).position
                )
                cross_targets = [
                    friendly
                    for friendly in (*self.operators.values(), *self.summons.values())
                    if (
                        friendly.position.col - primary_position.col,
                        friendly.position.row - primary_position.row,
                    )
                    in MAINLINE12_X5_RANGE_OFFSETS
                ]
                cross_targets.sort(
                    key=lambda item: (
                        item.instance_id != target_id,
                        item.instance_id,
                    )
                )
                attack_targets = [
                    (
                        friendly.instance_id,
                        friendly if isinstance(friendly, OperatorState) else None,
                        friendly if isinstance(friendly, SummonState) else None,
                    )
                    for friendly in cross_targets
                ]
            tank_melee_scale = (
                coefficient_value(definition, "Empty.attack@chuang_atk_scale")
                if (
                    definition.enemy_id in self._polluted_tank_ids
                    and enemy.blocked_by is not None
                )
                else None
            )
            tank_max_charge = (
                coefficient_value(definition, "Empty.sp")
                if definition.enemy_id in self._polluted_tank_ids
                else None
            )
            if tank_max_charge is not None and tank_max_charge % 1_000_000:
                raise UnsupportedMechanicError(
                    "Return Body tank charge is not a whole attack-recovery point"
                )
            tank_special = bool(
                definition.enemy_id in self._polluted_tank_ids
                and enemy.blocked_by is None
                and runtime is not None
                and tank_max_charge is not None
                and runtime.polluted_attack_charge >= tank_max_charge // 1_000_000
            )
            attack = self._enemy_effective_stat(enemy, EffectStat.ATTACK)
            enhanced_attack_scale = (
                coefficient_value(definition, "EnhancedAttack.atk_scale")
                if runtime is not None and runtime.enhanced_attack_pending
                else None
            )
            if enhanced_attack_scale is not None:
                attack = _scale_micros(attack, enhanced_attack_scale)
            if first_aoe_scale is not None:
                attack = _scale_micros(attack, first_aoe_scale)
            if tank_melee_scale is not None:
                attack = _scale_micros(attack, tank_melee_scale)
            blocked_attack_scale = (
                coefficient_value(
                    definition,
                    "combat.attack@ccwitch_rage_attack.atk_scale",
                )
                if enemy.blocked_by is not None
                else None
            )
            if blocked_attack_scale is not None:
                attack = _scale_micros(attack, blocked_attack_scale)
            stealth_strike = 0
            if definition.stealth_strike_scale_micros and (
                runtime is not None and runtime.stealth_strike_armed
            ):
                # 「强击标记」: earned by hiding, spent by the next attack.  The
                # carrier is attacking, so it is blocked and therefore visible --
                # this is the blow the ability line calls 「隐匿效果失效后的下一次
                # 攻击」.
                stealth_strike = definition.stealth_strike_scale_micros
                attack = _scale_micros(attack, stealth_strike)
            # 「未被阻挡时进行远程途径攻击，造成攻击力50%的物理伤害」 -- the weaker
            # volley applies only while free; held, the carrier hits normally.
            if enemy.blocked_by is None:
                volley_scale = coefficient_value(
                    definition, "Passive.attack@ranged.atk_scale"
                )
                if volley_scale is not None:
                    attack = _scale_micros(attack, volley_scale)
            # PRTS 「霜牡」: 「自身对冻结目标造成伤害时攻击倍率提升至250%」.  The
            # condition is being frozen, not merely held, which is why freeze
            # carries its own marker alongside the shared "cannot act" timer.
            # Two shipped spellings carry this: the canonical key above and
            # `atkup.atk_scale` on 雪怪小队 / 凿冰人 / 破冰者 (1.5 / 2.5 / 3.0).
            # The compiler resolves whichever applies into a typed field, because
            # `atkup.atk_scale` also carries 狂徒's facility priority and the key
            # alone cannot tell them apart -- the description does.
            frozen_scale = coefficient_value(definition, "damagescaletofrozen.atk_scale")
            if frozen_scale is None and definition.frozen_target_attack_scale_micros:
                frozen_scale = definition.frozen_target_attack_scale_micros
            if frozen_scale is not None:
                frozen_ticks = (
                    target.coefficient_frozen_remaining_ticks
                    if target is not None
                    else cast(
                        SummonState, summon_target
                    ).coefficient_frozen_remaining_ticks
                )
                if frozen_ticks:
                    attack = _scale_micros(attack, frozen_scale)
            # Attack-charged skills.  PRTS 「狙击步兵」: 「攻击2次后，下一次攻击
            # 会晕眩」 -- spCost counts normal attacks, so the hit *after* the
            # count is reached carries the skill and the counter resets.  A
            # timed variant (迷路的巨像, 13 s) uses the cooldown instead.
            charged = definition.charged_skill
            charged_fires = False
            if (
                charged is not None
                and runtime is not None
                and not self._has_control(instance_id, ControlEffect.SILENCE)
            ):
                if charged.sp_cost:
                    charged_fires = runtime.charged_skill_sp >= charged.sp_cost
                else:
                    charged_fires = runtime.charged_skill_cooldown_ticks == 0
                if charged_fires and not charged.windup_ticks:
                    attack = _scale_micros(attack, charged.attack_scale_micros)
            # PRTS 「萨卡兹穿刺手」 and 「凶豕兽」, same sentence in both 天赋:
            # 「加速时/冲撞时，……普通攻击附加的Buff会额外造成一次**当前移动速度
            # ×600**的预计算近战途径物理伤害并停止加速」.  The condition is the
            # *charge*, not the block -- a melee carrier only attacks something
            # blocking it, which is what made the 能力 line read as 「被阻挡后的
            # 首次攻击」.  Both spellings of the key are the same mechanism.
            # The speed is taken per second from the source value times the ramp,
            # not from the per-tick figure, so the burst is not skewed by the
            # per-tick truncation.
            charge_burst = 0
            burst_scale = next(
                (
                    value
                    for value in (
                        coefficient_value(definition, key)
                        for key in CHARGE_BURST_KEY_ORDER
                    )
                    if value is not None
                ),
                None,
            )
            if (
                burst_scale is not None
                and runtime is not None
                and runtime.rush_charging
                and runtime.rush_first_attack_armed
            ):
                ramp = rush_acceleration_rows(definition)
                base_per_second = definition.move_milli_tiles_per_second
                if base_per_second is None or not ramp:
                    raise UnsupportedMechanicError(
                        "charge burst needs an exact speed and a ramp"
                    )
                speed_milli_per_second = (
                    base_per_second
                    * (
                        1_000_000
                        + ramp["move_speed"] * runtime.rush_acceleration_stacks
                    )
                    // 1_000_000
                )
                charge_burst = (
                    speed_milli_per_second * burst_scale // 1_000_000_000
                )
                attack += charge_burst
            kingdom_attack_scale = None
            if definition.black_fog_range_offsets:
                primary_position = (
                    target.position
                    if target is not None
                    else cast(SummonState, summon_target).position
                )
                if self._friendly_in_black_fog(primary_position):
                    kingdom_attack_scale = coefficient_value(
                        definition,
                        "DamageUp.atk_scale",
                    )
                    if kingdom_attack_scale is None:
                        raise UnsupportedMechanicError(
                            "Blade Point Kingdom damage scale disappeared at runtime"
                        )
                    attack = _scale_micros(attack, kingdom_attack_scale)
            damage_type = (
                "physical"
                if (
                    blocked_attack_scale is not None
                    or charge_burst
                    or kingdom_attack_scale is not None
                    or first_aoe_scale is not None
                    or definition.enemy_id in self._polluted_tank_ids
                )
                else (
                    "arts"
                    if definition.enemy_id in STANDBY_REVIVE_MELEE_ARTS_IDS
                    # 强化模式: 「接触脉冲波时……攻击切换成法术伤害」.  A latch, not
                    # a timer -- once 源石祭坛's pulse has washed over it, this
                    # enemy stays switched for the rest of the battle.
                    or (
                        enemy.pulse_empowered
                        and definition.pulse_empowered_damage_type == "arts"
                    )
                    else definition.damage_type
                )
            )
            collision_scale = coefficient_value(definition, "HitEnemy.atk_scale")
            target_damage: list[
                tuple[str, OperatorState | None, SummonState | None, int]
            ] = []
            for candidate_id, operator_target, summon_target_candidate in attack_targets:
                target_defense = (
                    self._operator_effective_stat(operator_target, EffectStat.DEFENSE)
                    if operator_target is not None
                    else self._summon_effective_stat(
                        cast(SummonState, summon_target_candidate),
                        EffectStat.DEFENSE,
                    )
                )
                target_magic_resistance = (
                    self._operator_defs[operator_target.operator_id].magic_resistance
                    if operator_target is not None
                    else self._summon_defs[
                        cast(SummonState, summon_target_candidate).summon_id
                    ].magic_resistance
                )
                damage = (
                    _arts_damage(attack, target_magic_resistance)
                    if damage_type == "arts"
                    else _physical_damage(attack, target_defense)
                )
                damage = self._scale_fixed(
                    damage,
                    self.global_modifiers.enemy_damage_multiplier_milli,
                )
                if collision_scale is not None:
                    damage = _scale_micros(damage, collision_scale)
                target_damage.append(
                    (candidate_id, operator_target, summon_target_candidate, damage)
                )
            damage = target_damage[0][3]
            runtime_update: dict[str, object] = {}
            if runtime is not None:
                runtime_update["attacks_committed"] = runtime.attacks_committed + 1
                # 「一定时间不攻击后」 -- attacking is what ends the idle window.
                runtime_update["no_attack_idle_ticks"] = 0
                stack_rows = attack_stack_rows(definition)
                if stack_rows is not None:
                    # 「连续攻击时会提高攻击力和攻击速度」 -- one stack per landed
                    # attack, up to the carrier's own ceiling.
                    runtime_update["attack_stacks"] = min(
                        stack_rows["ceiling"] // 1_000_000,
                        runtime.attack_stacks + 1,
                    )
                if enhanced_attack_scale is not None:
                    runtime_update["enhanced_attack_pending"] = False
                if charged is not None:
                    if charged_fires:
                        # Spent: the counter restarts from zero, and the
                        # timed variant restarts its cooldown.
                        runtime_update["charged_skill_sp"] = 0
                        if charged.cooldown_ticks:
                            runtime_update["charged_skill_cooldown_ticks"] = (
                                charged.cooldown_ticks
                            )
                    elif charged.sp_cost:
                        runtime_update["charged_skill_sp"] = min(
                            charged.sp_cost, runtime.charged_skill_sp + 1
                        )
                if stealth_strike:
                    runtime_update["stealth_strike_armed"] = False
                    self._emit(
                        EventPhase.ATTACK,
                        "enemy_stealth_strike",
                        instance_id,
                        {"scale_micros": stealth_strike},
                        priority=2,
                    )
                if charge_burst:
                    # 「成功造成该额外伤害后停止加速/停止冲撞」 -- the charge itself
                    # ends here, and 「增益于停止加速时解除」 takes the stacks with
                    # it.  It restarts only once the carrier is free again.
                    runtime_update["rush_charging"] = False
                    runtime_update["rush_acceleration_stacks"] = 0
                    runtime_update["rush_first_attack_armed"] = False
                if coefficient_value(definition, "run.attack@move_speed") is not None:
                    runtime_update["post_hit_speed_active"] = True
                if definition.enemy_id in self._polluted_tank_ids:
                    if tank_max_charge is None:
                        raise UnsupportedMechanicError(
                            "Return Body tank charge disappeared at runtime"
                        )
                    runtime_update["polluted_attack_charge"] = (
                        0
                        if tank_special
                        else min(
                            tank_max_charge // 1_000_000,
                            runtime.polluted_attack_charge + 1,
                        )
                    )
            commit: dict[str, object] = {
                "attack_cooldown": self._enemy_effective_stat(
                    enemy, EffectStat.ATTACK_INTERVAL_TICKS
                )
                - 1,
                **(
                    {"coefficient_runtime": runtime.model_copy(update=runtime_update)}
                    if runtime is not None
                    else {}
                ),
            }
            if definition.dies_after_attacking and not (
                enemy.talent_stun_remaining_ticks
                or self._has_control(instance_id, ControlEffect.STUN)
            ):
                # 「**普通攻击结束时**，若自身未持有晕眩/冻结/浮空/沉睡，**强制击杀
                # 自身**」.  Held rather than prevented: the hit lands first, and
                # the seed goes with it.
                commit["hp"] = 0
                self._emit(
                    EventPhase.ATTACK,
                    "enemy_self_destructed_after_attacking",
                    instance_id,
                    {"enemy_id": enemy.enemy_id, "target_id": target_id},
                    priority=2,
                )
            self.enemies[instance_id] = enemy.model_copy(update=commit)
            self._emit(
                EventPhase.ATTACK,
                "attack",
                instance_id,
                {
                    "target_id": target_id,
                    "damage": damage,
                    "damage_type": damage_type,
                    "attack_scale_micros": (
                        enhanced_attack_scale
                        or first_aoe_scale
                        or tank_melee_scale
                        or blocked_attack_scale
                        or kingdom_attack_scale
                        or 1_000_000
                    ),
                    **(
                        {
                            "coefficient_key": "AOEAttack.atk_scale",
                            "range_id": "x-5",
                        }
                        if first_aoe_scale is not None
                        else {}
                    ),
                    **(
                        {
                            "targets": [
                                {"target_id": candidate_id, "damage": candidate_damage}
                                for candidate_id, _, _, candidate_damage in target_damage
                            ]
                        }
                        if len(target_damage) > 1
                        else {}
                    ),
                },
                priority=20,
            )
            if tank_special:
                primary_actor = (
                    target if target is not None else cast(SummonState, summon_target)
                )
                delay = coefficient_value(
                    definition, "PollutedRangedAtk.projectile_delay_time"
                )
                if delay is None:
                    raise UnsupportedMechanicError(
                        "Return Body pollution projectile timing disappeared at runtime"
                    )
                self._create_polluted_zone(
                    enemy,
                    center_col_milli=primary_actor.position.col * 1000,
                    center_row_milli=primary_actor.position.row * 1000,
                    coefficient_prefix="PollutedRangedAtk",
                )
                self._emit(
                    EventPhase.ATTACK,
                    "enemy_coefficient_polluted_ranged_attack",
                    instance_id,
                    {
                        "target_id": target_id,
                        "projectile_delay_time_micros": delay,
                    },
                    priority=20,
                )
            blood_hit = definition.blood_hit
            if blood_hit is not None:
                # 「技能期间自身成功造成伤害后……且**目标为干员**，对目标施加5秒每秒
                # 流失30生命值的【流血】效果（同名效果重复施加时仅**刷新持续时间**）」
                # -- operators only, and refreshed rather than stacked, which is
                # what writing the whole window back each time amounts to.
                for candidate_id, operator_target, _summon, _dealt in target_damage:
                    if operator_target is None:
                        continue
                    current_operator = self.operators.get(candidate_id)
                    if current_operator is None:
                        continue
                    self.operators[candidate_id] = current_operator.model_copy(
                        update={
                            "coefficient_bleeding_remaining_ticks": (
                                blood_hit.duration_ticks
                            ),
                            "coefficient_bleeding_pulse_remaining_ticks": (
                                RETURN_BODY_BLEEDING_PULSE_TICKS
                            ),
                            "coefficient_bleeding_damage": (
                                blood_hit.hp_loss_per_second
                            ),
                            "coefficient_bleeding_is_hp_loss": True,
                        }
                    )
                    self._emit(
                        EventPhase.ATTACK,
                        "enemy_blood_hit_applied",
                        instance_id,
                        {
                            "target_id": candidate_id,
                            "hp_loss_per_second": blood_hit.hp_loss_per_second,
                            "duration_ticks": blood_hit.duration_ticks,
                        },
                        priority=3,
                    )
            if carries_bleed_on_attack(definition):
                duration = coefficient_value(definition, "Bleeding.attack@duration")
                bleeding_damage = coefficient_value(
                    definition, "Bleeding.attack@bleeding_damage"
                )
                if (
                    duration is None
                    or bleeding_damage is None
                    or bleeding_damage % 1_000_000
                ):
                    raise UnsupportedMechanicError(
                        "Return Body bleeding payload disappeared at runtime"
                    )
                duration_ticks = self._seconds_micros_to_ticks(duration)
                for candidate_id, operator_target, summon_target_candidate, _ in target_damage:
                    current_actor: OperatorState | SummonState | None = (
                        self.operators.get(candidate_id)
                        if operator_target is not None
                        else self.summons.get(candidate_id)
                    )
                    if current_actor is None:
                        continue
                    next_actor = current_actor.model_copy(
                        update={
                            "coefficient_bleeding_remaining_ticks": duration_ticks,
                            "coefficient_bleeding_pulse_remaining_ticks": (
                                RETURN_BODY_BLEEDING_PULSE_TICKS
                            ),
                            "coefficient_bleeding_damage": (
                                bleeding_damage // 1_000_000
                            ),
                        }
                    )
                    if isinstance(current_actor, OperatorState):
                        self.operators[candidate_id] = cast(OperatorState, next_actor)
                    else:
                        self.summons[candidate_id] = cast(SummonState, next_actor)
                    self._emit(
                        EventPhase.ATTACK,
                        "enemy_coefficient_bleeding_started",
                        instance_id,
                        {
                            "target_id": candidate_id,
                            "duration_ticks": duration_ticks,
                            "damage_per_pulse": bleeding_damage // 1_000_000,
                        },
                        priority=21,
                    )
            defense_delta = coefficient_value(definition, "defdown.def")
            if defense_delta is not None:
                if defense_delta >= 0 or defense_delta % 1_000_000:
                    raise UnsupportedMechanicError(
                        "Tiacauh defense reduction must be a negative whole stat point"
                    )
                reduction = -defense_delta // 1_000_000
                if target is not None:
                    updated_reduction = (
                        target.enemy_coefficient_defense_reduction + reduction
                    )
                    self.operators[target_id] = target.model_copy(
                        update={
                            "enemy_coefficient_defense_reduction": updated_reduction
                        }
                    )
                else:
                    current_summon = cast(SummonState, summon_target)
                    updated_reduction = (
                        current_summon.enemy_coefficient_defense_reduction + reduction
                    )
                    self.summons[target_id] = current_summon.model_copy(
                        update={
                            "enemy_coefficient_defense_reduction": updated_reduction
                        }
                    )
                self._emit(
                    EventPhase.ATTACK,
                    "enemy_coefficient_defense_reduced",
                    instance_id,
                    {
                        "target_id": target_id,
                        "amount": reduction,
                        "total_reduction": updated_reduction,
                    },
                    priority=21,
                )
            # 历战高塔术师 「攻击击中**未处于重整束流上**的目标时，额外对目标
            # **周围8格内的其他所有我方单位**造成攻击力100%的法术伤害」.  The
            # condition is negative: the light is what shelters them.
            beam_splash = definition.beam_splash
            if beam_splash is not None:
                lit = self._beam_tiles()
                splash = _scale_micros(attack, beam_splash.attack_scale_micros)
                for struck_id in list(target_ids):
                    struck = self.operators.get(struck_id) or self.summons.get(
                        struck_id
                    )
                    if struck is None:
                        continue
                    if (struck.position.col, struck.position.row) in lit:
                        continue
                    around = {
                        (struck.position.col + col, struck.position.row + row)
                        for col, row in beam_splash.range_offsets
                    }
                    splashed: list[str] = []
                    for collection, side in (
                        (self.operators, "operator"),
                        (self.summons, "summon"),
                    ):
                        for other_id in sorted(collection):
                            if other_id in target_ids:
                                # 「其他所有我方单位」
                                continue
                            other = collection[other_id]
                            if (
                                other.position.col,
                                other.position.row,
                            ) not in around:
                                continue
                            resistance = (
                                self._operator_defs[other.operator_id].magic_resistance
                                if side == "operator"
                                else self._summon_defs[other.summon_id].magic_resistance
                            )
                            if splash:
                                intents.append(
                                    _DamageIntent(
                                        instance_id,
                                        other_id,
                                        side,
                                        _arts_damage(splash, resistance),
                                    )
                                )
                            splashed.append(other_id)
                    self._emit(
                        EventPhase.DAMAGE,
                        "enemy_off_beam_splash",
                        instance_id,
                        {
                            "target_id": struck_id,
                            "target_ids": tuple(splashed),
                            "payload": splash,
                        },
                        priority=21,
                    )
            # 残党首席小提琴家 「攻击命中重整束流上的目标后，分裂出按**正/反方向
            # 沿重整束流移动**的弹道，弹道对**触碰到的所有我方单位**造成攻击力50%
            # 的法术普通伤害」.  The two projectiles between them run the whole
            # beam, so the reach is 「everyone else standing in the light」.
            conduction = definition.beam_conduction
            if conduction is not None:
                lit = self._beam_tiles()
                conducted = _scale_micros(attack, conduction.attack_scale_micros)
                for struck_id in list(target_ids):
                    struck = self.operators.get(struck_id) or self.summons.get(
                        struck_id
                    )
                    if struck is None:
                        continue
                    if (struck.position.col, struck.position.row) not in lit:
                        # 「攻击命中**重整束流上的**目标后」 -- off the beam, nothing
                        # splits off at all.
                        continue
                    carried: list[str] = []
                    for collection, side in (
                        (self.operators, "operator"),
                        (self.summons, "summon"),
                    ):
                        for other_id in sorted(collection):
                            if other_id in target_ids:
                                continue
                            other = collection[other_id]
                            if (other.position.col, other.position.row) not in lit:
                                continue
                            resistance = (
                                self._operator_defs[other.operator_id].magic_resistance
                                if side == "operator"
                                else self._summon_defs[other.summon_id].magic_resistance
                            )
                            if conducted:
                                intents.append(
                                    _DamageIntent(
                                        instance_id,
                                        other_id,
                                        side,
                                        _arts_damage(conducted, resistance),
                                    )
                                )
                            carried.append(other_id)
                    self._emit(
                        EventPhase.DAMAGE,
                        "enemy_beam_conduction",
                        instance_id,
                        {
                            "target_id": struck_id,
                            "target_ids": tuple(carried),
                            "payload": conducted,
                            "directions": conduction.directions,
                        },
                        priority=21,
                    )
            # 爆破攻坚手 「攻击造成爆炸，使目标周围四格的单位受到溅射伤害」.
            # PRTS gives the whole rule: 「对目标周围四格的**其他**我方单位造成
            # 攻击力50%的近战途径物理溅射伤害（格子判定，不可对空，不受迷彩影响）」
            # -- tile-based, orthogonal neighbours, and never the target itself.
            splash_micros = splash_attack_scale(definition)
            if splash_micros:
                splash_damage = _scale_micros(attack, splash_micros)
                for target_id in list(target_ids):
                    struck = self.operators.get(target_id) or self.summons.get(
                        target_id
                    )
                    if struck is None:
                        continue
                    neighbours = {
                        (struck.position.col + 1, struck.position.row),
                        (struck.position.col - 1, struck.position.row),
                        (struck.position.col, struck.position.row + 1),
                        (struck.position.col, struck.position.row - 1),
                    }
                    for collection, side in (
                        (self.operators, "operator"),
                        (self.summons, "summon"),
                    ):
                        for other_id in sorted(collection):
                            if other_id in target_ids:
                                # 「其他我方单位」 -- the struck target is out.
                                continue
                            other = collection[other_id]
                            if (other.position.col, other.position.row) not in neighbours:
                                continue
                            if splash_damage:
                                intents.append(
                                    _DamageIntent(
                                        instance_id,
                                        other_id,
                                        side,
                                        splash_damage,
                                    )
                                )
                            self._emit(
                                EventPhase.DAMAGE,
                                "enemy_splash_damage",
                                instance_id,
                                {
                                    "target_id": other_id,
                                    "struck_target_id": target_id,
                                    "damage": splash_damage,
                                    "source_key": "aoe.attack@<carrier>.atk_scale",
                                },
                                priority=20,
                            )
            # 特工 「攻击时会短暂减少我方单位的阻挡上限」 -- applied to whatever
            # this attack landed on, alongside the attack-borne control below.
            if definition.on_attack_block_count_delta:
                reduction_ticks = definition.on_attack_block_count_duration_ticks
                amount = -definition.on_attack_block_count_delta
                for target_id in target_ids:
                    friendly = self.operators.get(target_id) or self.summons.get(
                        target_id
                    )
                    if friendly is None:
                        continue
                    update_fields = {
                        # Tiers do not stack: the shipped carriers both reduce
                        # by one, and two of them holding the same operator
                        # should not take two points off.
                        "block_reduction": max(friendly.block_reduction, amount),
                        "block_reduction_remaining_ticks": max(
                            friendly.block_reduction_remaining_ticks,
                            reduction_ticks,
                        ),
                    }
                    if target_id in self.operators:
                        self.operators[target_id] = friendly.model_copy(
                            update=update_fields
                        )
                    else:
                        self.summons[target_id] = friendly.model_copy(
                            update=update_fields
                        )
                    self._emit(
                        EventPhase.ATTACK,
                        "friendly_block_count_reduced",
                        instance_id,
                        {
                            "target_id": target_id,
                            "amount": amount,
                            "duration_ticks": reduction_ticks,
                            "source_key": "ReduceBlockCnt.block_cnt",
                        },
                        priority=21,
                    )
            # Attack-borne control, driven by the enemy's own data rather than
            # one hardcoded key: Combat.attack@stun plus the freeze keys other
            # carriers use.  Freeze runs on the same "cannot act" countdown
            # because ControlEffect has no frozen state (see ATTACK_CONTROL_KEYS).
            control_hits = [
                (key, kind, coefficient_value(definition, key))
                for key, kind in sorted(ATTACK_CONTROL_KEYS.items())
            ]
            if charged_fires and charged is not None and charged.stun_seconds_micros:
                control_hits.append(
                    (
                        f"{charged.prefab_key}.stun",
                        "stun",
                        charged.stun_seconds_micros,
                    )
                )
            if charged_fires and charged is not None and charged.windup_ticks:
                # 「蓄力一段时间后对目标周围所有单位造成法术伤害」 -- the ordinary
                # attack is untouched and the skill lands later, on an area
                # centred where the target stands when the charge starts.  It
                # rides the fused-blast path the death explosions already use:
                # same shape (centre, radius, fuse, damage), same resolution.
                centre = (
                    target.position
                    if target is not None
                    else self.summons[target_id].position
                    if target_id in self.summons
                    else None
                )
                if centre is not None:
                    self.entity_sequence += 1
                    blast_id = "charge-%06d" % self.entity_sequence
                    payload = (
                        _scale_micros(definition.attack, charged.attack_scale_micros)
                    )
                    self.pending_explosions[blast_id] = PendingExplosionState(
                        instance_id=blast_id,
                        source_enemy_id=definition.enemy_id,
                        center_col_milli=centre.col * 1000,
                        center_row_milli=centre.row * 1000,
                        radius_milli_tiles=charged.splash_radius_milli_tiles,
                        fuse_remaining_ticks=charged.windup_ticks,
                        damage=payload,
                        cold_ticks=0,
                    )
                    self._emit(
                        EventPhase.ATTACK,
                        "enemy_charge_attack_started",
                        instance_id,
                        {
                            "explosion_id": blast_id,
                            "target_id": target_id,
                            "windup_ticks": charged.windup_ticks,
                            "radius_milli_tiles": charged.splash_radius_milli_tiles,
                            "damage": payload,
                            "source_key": f"{charged.prefab_key}.atk_scale",
                        },
                        priority=20,
                    )
            if charged_fires and charged is not None and charged.cold_seconds_micros:
                # 「每攻击2次后下次攻击会对目标施加寒冷」 -- the "freeze" kind is
                # this engine's 寒冷, which is why it rides the same branch the
                # other cold-applying keys use rather than the stun one.
                control_hits.append(
                    (
                        f"{charged.prefab_key}.freeze",
                        "freeze",
                        charged.cold_seconds_micros,
                    )
                )
            control_hits = [item for item in control_hits if item[2] is not None]
            for control_key, control_kind, control_duration in control_hits:
                stun_duration = control_duration
                stun_ticks = self._seconds_micros_to_ticks(stun_duration)
                if stun_ticks <= 0:
                    # A zero duration is a declared no-op (Crash.stun ships 0.0).
                    continue
                if target is not None:
                    target = self.operators[target_id]
                    control_update = (
                        self._enemy_cold_update(target, stun_ticks)
                        if control_kind == "freeze"
                        else {
                            "coefficient_stun_remaining_ticks": max(
                                target.coefficient_stun_remaining_ticks,
                                stun_ticks,
                            )
                        }
                    )
                    self.operators[target_id] = target.model_copy(update=control_update)
                else:
                    current_summon = cast(SummonState, self.summons[target_id])
                    summon_update = (
                        self._enemy_cold_update(current_summon, stun_ticks)
                        if control_kind == "freeze"
                        else {
                            "coefficient_stun_remaining_ticks": max(
                                current_summon.coefficient_stun_remaining_ticks,
                                stun_ticks,
                            )
                        }
                    )
                    self.summons[target_id] = current_summon.model_copy(
                        update=summon_update
                    )
                self._emit(
                    EventPhase.ATTACK,
                    "enemy_coefficient_stun_started",
                    instance_id,
                    {
                        "target_id": target_id,
                        "duration_ticks": stun_ticks,
                        "control": control_kind,
                        "source_key": control_key,
                    },
                    priority=21,
                )
            base_exp = coefficient_value(definition, "HitEnemy.base_exp")
            if base_exp is not None:
                self._emit(
                    EventPhase.ATTACK,
                    "enemy_coefficient_collision_accounted",
                    instance_id,
                    {"target_id": target_id, "base_exp_micros": base_exp},
                    priority=22,
                )
            for (
                candidate_id,
                operator_target,
                _,
                candidate_damage,
            ) in target_damage:
                if candidate_damage:
                    intents.append(
                        _DamageIntent(
                            instance_id,
                            candidate_id,
                            "operator" if operator_target is not None else "summon",
                            candidate_damage,
                            attack_packet=True,
                        )
                    )
        return tuple(intents)

    def _launch_projectile(
        self,
        source_id: str,
        target_id: str,
        amount: int,
        travel_ticks: int,
        *,
        splash_attack: int = 0,
        splash_radius_tiles: int = 0,
        packet_index: int = 0,
        packet_count: int = 1,
    ) -> None:
        self.entity_sequence += 1
        instance_id = f"projectile-{self.entity_sequence:06d}"
        self.projectiles[instance_id] = ProjectileState(
            instance_id=instance_id,
            source_instance_id=source_id,
            target_instance_id=target_id,
            amount=amount,
            remaining_ticks=travel_ticks,
            packet_index=packet_index,
            packet_count=packet_count,
            splash_attack=splash_attack,
            splash_radius_tiles=splash_radius_tiles,
        )
        self._emit(
            EventPhase.ATTACK,
            "projectile_launched",
            instance_id,
            {
                "source_id": source_id,
                "target_id": target_id,
                "amount": amount,
                "travel_ticks": travel_ticks,
                "packet_index": packet_index,
                "packet_count": packet_count,
                "splash_attack": splash_attack,
                "splash_radius_tiles": splash_radius_tiles,
            },
            priority=12,
        )

    def _advance_projectiles(self) -> tuple[_DamageIntent, ...]:
        intents: list[_DamageIntent] = []
        for instance_id in sorted(tuple(self.projectiles)):
            projectile = self.projectiles[instance_id]
            remaining = projectile.remaining_ticks - 1
            if remaining:
                self.projectiles[instance_id] = projectile.model_copy(
                    update={"remaining_ticks": remaining}
                )
                continue
            del self.projectiles[instance_id]
            if projectile.target_instance_id not in self.enemies:
                self._emit(
                    EventPhase.ATTACK,
                    "projectile_fizzled",
                    instance_id,
                    {
                        "target_id": projectile.target_instance_id,
                        "packet_index": projectile.packet_index,
                        "packet_count": projectile.packet_count,
                    },
                    priority=1,
                )
                continue
            primary = self.enemies[projectile.target_instance_id]
            if primary.route_teleport_destination_index is not None:
                self._emit(
                    EventPhase.ATTACK,
                    "projectile_fizzled",
                    instance_id,
                    {
                        "target_id": projectile.target_instance_id,
                        "packet_index": projectile.packet_index,
                        "packet_count": projectile.packet_count,
                    },
                    priority=1,
                )
                continue
            primary_position = self._enemy_position_milli(primary)
            target_amounts: list[tuple[str, int]] = []
            for enemy in sorted(self.enemies.values(), key=lambda item: item.instance_id):
                if enemy.route_teleport_destination_index is not None:
                    continue
                if enemy.instance_id == primary.instance_id:
                    target_amounts.append((enemy.instance_id, projectile.amount))
                    continue
                if not projectile.splash_radius_tiles:
                    continue
                position = self._enemy_position_milli(enemy)
                distance = abs(position[0] - primary_position[0]) + abs(
                    position[1] - primary_position[1]
                )
                if distance > projectile.splash_radius_tiles * 1000:
                    continue
                amount = _physical_damage(
                    projectile.splash_attack,
                    self._enemy_effective_stat(enemy, EffectStat.DEFENSE),
                )
                amount = self._scale_fixed(
                    amount,
                    self.global_modifiers.friendly_damage_multiplier_milli,
                )
                if amount:
                    target_amounts.append((enemy.instance_id, amount))
            self._emit(
                EventPhase.ATTACK,
                "projectile_impact",
                instance_id,
                {
                    "target_id": projectile.target_instance_id,
                    "amount": projectile.amount,
                    "packet_index": projectile.packet_index,
                    "packet_count": projectile.packet_count,
                    "targets": [
                        {"target_id": target_id, "amount": amount}
                        for target_id, amount in target_amounts
                    ],
                },
                priority=1,
            )
            intents.extend(
                _DamageIntent(instance_id, target_id, "enemy", amount)
                for target_id, amount in target_amounts
            )
        return tuple(intents)

    def _collect_special_tile_effects(
        self,
    ) -> tuple[tuple[_DamageIntent, ...], tuple[_HealIntent, ...]]:
        damage_intents: list[_DamageIntent] = []
        heal_intents: list[_HealIntent] = []
        for tile in sorted(self.content.special_tiles, key=lambda item: item.tile_id):
            if self.tick % tile.interval_ticks:
                continue
            targets: list[tuple[str, Literal["operator", "summon", "enemy"]]] = []
            if tile.target in {"ground_enemies", "both"}:
                for enemy in sorted(self.enemies.values(), key=lambda item: item.instance_id):
                    if self._enemy_defs[enemy.enemy_id].movement_layer is MovementLayer.FLYING:
                        continue
                    if self._enemy_grid_point(enemy) != tile.position:
                        continue
                    targets.append((enemy.instance_id, "enemy"))
            if tile.target in {"friendlies", "both"}:
                for operator in sorted(self.operators.values(), key=lambda item: item.instance_id):
                    if operator.position == tile.position:
                        targets.append((operator.instance_id, "operator"))
                for summon in sorted(self.summons.values(), key=lambda item: item.instance_id):
                    if summon.position == tile.position and (
                        not tile.healing
                        or self._summon_defs[summon.summon_id].receives_external_healing
                    ):
                        targets.append((summon.instance_id, "summon"))
            for effect in sorted(tile.effects, key=lambda item: item.effect_id):
                for target_id, _ in targets:
                    self._apply_special_tile_persistent_effect(tile.tile_id, effect, target_id)
            for target_id, target_side in targets:
                if tile.healing:
                    heal_intents.append(_HealIntent(tile.tile_id, target_id, tile.healing))
                elif tile.damage:
                    if target_side == "operator":
                        defense = self._operator_effective_stat(
                            self.operators[target_id], EffectStat.DEFENSE
                        )
                    elif target_side == "summon":
                        defense = self._summon_effective_stat(
                            self.summons[target_id], EffectStat.DEFENSE
                        )
                    else:
                        defense = self._enemy_effective_stat(
                            self.enemies[target_id], EffectStat.DEFENSE
                        )
                    amount = (
                        tile.damage
                        if tile.damage_type == "true"
                        else _physical_damage(tile.damage, defense)
                    )
                    if amount:
                        damage_intents.append(
                            _DamageIntent(tile.tile_id, target_id, target_side, amount)
                        )
            self._emit(
                EventPhase.ATTACK,
                "special_tile_pulse",
                tile.tile_id,
                {
                    "effect": (
                        "healing"
                        if tile.healing
                        else "damage"
                        if tile.damage
                        else "persistent_effects"
                    ),
                    "amount": tile.healing or tile.damage,
                    "targets": [target_id for target_id, _ in targets],
                    **(
                        {"effects": [effect.effect_id for effect in tile.effects]}
                        if tile.effects
                        else {}
                    ),
                },
                priority=5,
            )
        return tuple(damage_intents), tuple(heal_intents)

    def _apply_special_tile_persistent_effect(
        self,
        tile_id: str,
        definition: SpecialTileStatModifierDefinition | SpecialTileControlEffectDefinition,
        target_instance_id: str,
    ) -> None:
        existing = next(
            (
                effect
                for effect in self.active_effects.values()
                if effect.source_instance_id == tile_id
                and effect.effect_id == definition.effect_id
                and effect.target_instance_id == target_instance_id
            ),
            None,
        )
        if existing is not None:
            self.active_effects[existing.instance_id] = existing.model_copy(
                update={"remaining_ticks": definition.duration_ticks}
            )
            self._emit(
                EventPhase.ATTACK,
                "special_tile_effect_refreshed",
                tile_id,
                {
                    "effect_id": definition.effect_id,
                    "target_id": target_instance_id,
                    "duration_ticks": definition.duration_ticks,
                },
                priority=4,
            )
            return
        self.effect_sequence += 1
        effect_instance_id = f"effect-{self.effect_sequence:06d}"
        self.active_effects[effect_instance_id] = ActiveEffectState(
            instance_id=effect_instance_id,
            effect_id=definition.effect_id,
            source_instance_id=tile_id,
            source_skill_id=f"special_tile:{tile_id}",
            target_instance_id=target_instance_id,
            remaining_ticks=definition.duration_ticks,
        )
        self._emit(
            EventPhase.ATTACK,
            "special_tile_effect_applied",
            tile_id,
            {
                "effect_id": definition.effect_id,
                "target_id": target_instance_id,
                "duration_ticks": definition.duration_ticks,
            },
            priority=4,
        )

    def _summon_target(self, summon: SummonState) -> EnemyState | None:
        definition = self._summon_defs[summon.summon_id]
        candidates = [
            enemy
            for enemy in self.enemies.values()
            if enemy.route_teleport_destination_index is None
            and self._distance_milli(summon.position, enemy)
            <= definition.attack_range_tiles * 1000
            and (
                self._enemy_defs[enemy.enemy_id].movement_layer is MovementLayer.GROUND
                or definition.can_target_flying
            )
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=self._enemy_objective_priority,
        )

    def _friendly_block_count(self, friendly: OperatorState | SummonState) -> int:
        if isinstance(friendly, OperatorState):
            return self._operator_effective_stat(friendly, EffectStat.BLOCK_COUNT)
        return self._summon_effective_stat(friendly, EffectStat.BLOCK_COUNT)

    def _collect_operator_healing(self) -> tuple[_HealIntent, ...]:
        intents: list[_HealIntent] = []
        for instance_id in sorted(self.operators):
            operator = self.operators[instance_id]
            definition = self._operator_defs[operator.operator_id]
            healing_power = self._operator_effective_stat(operator, EffectStat.HEALING_POWER)
            if healing_power <= 0:
                continue
            if operator.heal_cooldown > 0:
                self.operators[instance_id] = operator.model_copy(
                    update={"heal_cooldown": operator.heal_cooldown - 1}
                )
                continue
            if self._has_control(instance_id, ControlEffect.STUN):
                continue
            allowed_positions = (
                self._operator_covered_cells(operator, definition.healing_range_offsets)
                if definition.healing_range_offsets
                else None
            )
            target = self._lowest_hp_ally(
                operator.position,
                definition.healing_range_tiles,
                allowed_positions=allowed_positions,
            )
            if target is None:
                continue
            self.operators[instance_id] = operator.model_copy(
                update={"heal_cooldown": definition.healing_interval_ticks - 1}
            )
            self._emit(
                EventPhase.ATTACK,
                "heal_cast",
                instance_id,
                {"target_id": target.instance_id, "amount": healing_power},
                priority=30,
            )
            heal_event_sequence = self.event_sequence
            armed_damage, armed_healing = self._consume_armed_payloads(
                instance_id,
                SkillActivationPolicy.NEXT_HEAL,
                trigger_event_sequence=heal_event_sequence,
                event_target_id=target.instance_id,
            )
            if armed_damage:
                raise EffectVMError("next_heal payload emitted damage")
            intents.extend(armed_healing)
            intents.append(_HealIntent(instance_id, target.instance_id, healing_power))
        return tuple(intents)

    def _collect_summon_healing(self) -> tuple[_HealIntent, ...]:
        intents: list[_HealIntent] = []
        for instance_id in sorted(self.summons):
            summon = self.summons[instance_id]
            definition = self._summon_defs[summon.summon_id]
            healing_power = self._summon_effective_stat(summon, EffectStat.HEALING_POWER)
            if healing_power <= 0:
                continue
            if summon.heal_cooldown > 0:
                self.summons[instance_id] = summon.model_copy(
                    update={"heal_cooldown": summon.heal_cooldown - 1}
                )
                continue
            if self._has_control(instance_id, ControlEffect.STUN):
                continue
            target = self._lowest_hp_ally(
                summon.position,
                definition.healing_range_tiles,
            )
            if target is None:
                continue
            self.summons[instance_id] = summon.model_copy(
                update={"heal_cooldown": definition.healing_interval_ticks - 1}
            )
            self._emit(
                EventPhase.ATTACK,
                "summon_heal_cast",
                instance_id,
                {"target_id": target.instance_id, "amount": healing_power},
                priority=32,
            )
            heal_event_sequence = self.event_sequence
            armed_damage, armed_healing = self._consume_armed_payloads(
                instance_id,
                SkillActivationPolicy.NEXT_HEAL,
                trigger_event_sequence=heal_event_sequence,
                event_target_id=target.instance_id,
            )
            if armed_damage:
                raise EffectVMError("next_heal summon payload emitted damage")
            intents.extend(armed_healing)
            intents.append(_HealIntent(instance_id, target.instance_id, healing_power))
        return tuple(intents)

    def _collect_device_healing(self) -> tuple[_HealIntent, ...]:
        intents: list[_HealIntent] = []
        for instance_id in sorted(self.healing_devices):
            device = self.healing_devices[instance_id]
            definition = self._device_defs[device.device_id]
            if device.pulse_cooldown > 0:
                self.healing_devices[instance_id] = device.model_copy(
                    update={"pulse_cooldown": device.pulse_cooldown - 1}
                )
                continue
            target = self._lowest_hp_ally(
                device.position,
                definition.healing_range_tiles,
            )
            if target is None:
                continue
            self.healing_devices[instance_id] = device.model_copy(
                update={"pulse_cooldown": definition.pulse_interval_ticks - 1}
            )
            self._emit(
                EventPhase.ATTACK,
                "device_heal_pulse",
                instance_id,
                {"target_id": target.instance_id, "amount": definition.pulse_heal},
                priority=31,
            )
            intents.append(_HealIntent(instance_id, target.instance_id, definition.pulse_heal))
        return tuple(intents)

    def _accumulate_elemental(
        self,
        intents: list[_DamageIntent],
        applied: int,
        total: int,
    ) -> None:
        """Convert part of a landed hit into elemental build-up.

        PRTS 「元素」: the amount that lands is 「数值 x (1 - 元素抗性 x 0.01)」.
        The ratio is taken against the damage that actually landed, for the same
        reason the leech is: it is the hit that converts, not the attacker's
        stat block.  Several attackers in one packet each contribute their own
        share, so nobody's ratio is applied to somebody else's damage.
        """

        if applied <= 0 or total <= 0:
            return
        for intent in intents:
            if intent.amount <= 0:
                continue
            enemy = self.enemies.get(intent.source_id)
            if enemy is None:
                continue
            definition = self._enemy_defs[enemy.enemy_id]
            ratio = None
            for key in sorted(ELEMENTAL_RATIO_KEYS):
                ratio = coefficient_value(definition, key)
                if ratio is not None:
                    break
            if ratio is None:
                continue
            element = definition.element_damage_type
            if element is None:
                raise UnsupportedMechanicError(
                    "elemental conversion lost its element type at runtime"
                )
            share = intent.amount * applied // total
            build_up = share * ratio // 1_000_000
            if build_up > 0:
                self._add_elemental(intent.target_id, element, build_up)

    def _add_elemental(self, target_id: str, element: str, amount: int) -> None:
        """Add build-up to one gauge, bursting it when it fills."""

        gauge_field = f"element_{element}"
        cooldown_field = f"element_{element}_cooldown_ticks"
        actor: OperatorState | SummonState | None = self.operators.get(target_id)
        is_operator = actor is not None
        if actor is None:
            actor = self.summons.get(target_id)
        if actor is None:
            return
        if getattr(actor, cooldown_field):
            # In burst cooldown: PRTS has the unit enter one immediately after a
            # burst, and build-up during it would let bursts chain.
            return
        filled = getattr(actor, gauge_field) + amount
        if filled < ELEMENTAL_GAUGE_MAX:
            updated = actor.model_copy(update={gauge_field: filled})
            if is_operator:
                self.operators[target_id] = cast(OperatorState, updated)
            else:
                self.summons[target_id] = cast(SummonState, updated)
            return

        update: dict[str, object] = {
            gauge_field: 0,
            cooldown_field: ELEMENTAL_BURST_COOLDOWN_TICKS,
        }
        true_damage = ELEMENTAL_BURST_TRUE_DAMAGE[element]
        physical = ELEMENTAL_BURST_PHYSICAL_DAMAGE[element]
        arts = ELEMENTAL_BURST_ARTS_DAMAGE[element]
        stun_seconds = ELEMENTAL_BURST_STUN_SECONDS[element]
        defence_loss = ELEMENTAL_PERMANENT_DEFENCE_LOSS[element]

        loss = true_damage
        if physical:
            defence = (
                self._operator_effective_stat(
                    cast(OperatorState, actor), EffectStat.DEFENSE
                )
                if is_operator
                else self._summon_effective_stat(
                    cast(SummonState, actor), EffectStat.DEFENSE
                )
            )
            loss += max(0, physical - defence)
        # Arts damage arrives unmitigated: friendly magic resistance is not on
        # the effective-stat surface at all, so there is nothing to reduce it
        # by.  Registered rather than approximated with a guessed resistance.
        loss += arts
        if loss:
            update["hp"] = max(0, actor.hp - loss)
        if stun_seconds:
            update["coefficient_stun_remaining_ticks"] = max(
                actor.coefficient_stun_remaining_ticks,
                stun_seconds * self.content.ticks_per_second,
            )
        if defence_loss:
            update["permanent_defence_loss"] = (
                actor.permanent_defence_loss + defence_loss
            )

        updated = actor.model_copy(update=update)
        if is_operator:
            self.operators[target_id] = cast(OperatorState, updated)
        else:
            self.summons[target_id] = cast(SummonState, updated)
        self._emit(
            EventPhase.DAMAGE,
            "elemental_burst",
            target_id,
            {
                "element": element,
                "hp_lost": loss,
                "stun_ticks": update.get("coefficient_stun_remaining_ticks", 0),
                "permanent_defence_loss": defence_loss,
                "source_key": "ep_damage_ratio",
            },
            priority=21,
        )

    def _apply_attack_leech(
        self,
        intents: list[_DamageIntent],
        applied: int,
        total: int,
    ) -> None:
        """Heal any leeching attacker by its share of the damage that landed.

        PRTS 「成功造成伤害后，治疗自身相当于伤害量150%的生命值」: the multiple is on
        damage **dealt**, so it is computed here from what actually landed
        rather than from the attacker's stat block.  When several attackers
        contributed to one packet each gets its proportional share, so the leech
        never exceeds the damage the carrier itself caused.
        """

        if applied <= 0 or total <= 0:
            return
        for intent in intents:
            if intent.amount <= 0:
                continue
            enemy = self.enemies.get(intent.source_id)
            if enemy is None:
                continue
            definition = self._enemy_defs[enemy.enemy_id]
            scale = coefficient_value(definition, "vampire.heal_scale")
            if scale is None:
                continue
            share = intent.amount * applied // total
            healed = share * scale // 1_000_000
            if healed <= 0:
                continue
            restored = min(definition.max_hp, enemy.hp + healed)
            if restored == enemy.hp:
                continue
            self.enemies[intent.source_id] = enemy.model_copy(
                update={"hp": restored}
            )
            self._emit(
                EventPhase.DAMAGE,
                "enemy_attack_leech",
                intent.source_id,
                {
                    "target_id": intent.target_id,
                    "damage_dealt": share,
                    "healed": restored - enemy.hp,
                    "source_key": "vampire.heal_scale",
                },
                priority=1,
            )

    def _source_deals_physical(self, source_id: str) -> bool:
        """Whether this attacker's damage is 物理.

        `_DamageIntent` carries no damage type, so it is resolved from the
        attacker: an operator or summon has a declared `damage_type`, and
        anything else -- a tile pulse, an aura, a coefficient effect -- is not
        an 攻击 to be dodged and is left alone rather than assumed physical.
        """

        operator = self.operators.get(source_id)
        if operator is not None:
            return self._operator_defs[operator.operator_id].damage_type == "physical"
        summon = self.summons.get(source_id)
        if summon is not None:
            return True
        return False

    def _survive_physical_evade(
        self,
        target_id: str,
        definition: EnemyDefinition,
        intents: list[Any],
    ) -> list[Any]:
        """The packets that get through 不法分子's 「闪避物理攻击」.

        「未被阻挡时」 is checked by the caller, alongside the other
        unblocked-conditional resistance so the two conditions stay in one
        place.  One roll per physical packet, on the carrier's own stream.
        """

        kept: list[Any] = []
        dodged = False
        for item in intents:
            if item.amount > 0 and self._source_deals_physical(item.source_id):
                stream = cast(
                    str, definition.unblocked_physical_evade_rng_stream
                )
                roll = self._draw_rng(stream, 1, 1000)
                if roll <= definition.unblocked_physical_evade_probability_milli:
                    dodged = True
                    self._emit(
                        EventPhase.DAMAGE,
                        "enemy_physical_attack_evaded",
                        target_id,
                        {
                            "source_id": item.source_id,
                            "requested": item.amount,
                            "probability_milli": (
                                definition.unblocked_physical_evade_probability_milli
                            ),
                            "rng_roll": roll,
                            "rng_draw": self.rng_streams[stream],
                            "source_key": "Evade.prob",
                        },
                    )
                    continue
            kept.append(item)
        return kept if dodged else intents

    def _apply_damage(self, intents: Iterable[_DamageIntent]) -> None:
        grouped: dict[tuple[str, str], list[_DamageIntent]] = defaultdict(list)
        retaliation: list[_DamageIntent] = []
        for intent in intents:
            grouped[(intent.target_side, intent.target_id)].append(intent)
        for target_side, target_id in sorted(grouped, key=lambda item: item[1]):
            target_intents = sorted(
                grouped[(target_side, target_id)], key=lambda item: item.source_id
            )
            total = sum(item.amount for item in target_intents)
            total_milli = sum(_damage_milli(item.amount) for item in target_intents)
            if target_side == "enemy" and target_id in self.enemies:
                if any(item.nonlethal for item in target_intents):
                    raise EffectVMError("nonlethal coefficient damage cannot target enemies")
                target = self.enemies[target_id]
                if self._mainline12_enemy_is_invincible(target):
                    continue
                definition = self._enemy_defs[target.enemy_id]
                ember = target.coefficient_runtime
                if ember is not None and ember.ember_remaining_ticks:
                    if ember.ember_transition_remaining_ticks:
                        # 「1s内不移动且持有[无敌]」
                        continue
                    # 「成功受到伤害，应用生命值变化时令生命值只降低1点（不论伤害
                    # 多少）」 -- one point per landed hit, so five hits kill a
                    # five-point ember however hard each one is.
                    landed = sum(1 for item in target_intents if item.amount > 0)
                    self.enemies[target_id] = target.model_copy(
                        update={"hp": max(0, target.hp - landed)}
                    )
                    continue
                if (
                    definition.unblocked_physical_evade_probability_milli
                    and target.blocked_by is None
                ):
                    kept = self._survive_physical_evade(
                        target_id, definition, target_intents
                    )
                    if not kept:
                        continue
                    if kept is not target_intents:
                        target_intents = kept
                        total = sum(item.amount for item in target_intents)
                        total_milli = sum(
                            _damage_milli(item.amount) for item in target_intents
                        )
                # Source-declared incoming-damage resistances (DamageReduce
                # while unblocked, Madness below half health).  Driven by the
                # enemy's own talent coefficients rather than an id allow-list,
                # so any enemy carrying the key gets the mechanism.
                scale_milli = incoming_damage_multiplier_milli(
                    definition.mechanic_coefficients,
                    is_blocked=target.blocked_by is not None,
                    hp=target.hp,
                    max_hp=definition.max_hp,
                )
                # Rear-hit resistance depends on where the attackers stand, so
                # it is applied here where the contributing sources are known.
                rear_milli = self._rear_hit_multiplier_milli(
                    target,
                    definition,
                    tuple(item.source_id for item in target_intents),
                )
                if rear_milli != 1000:
                    scale_milli = scale_milli * rear_milli // 1000
                fragile = definition.environmental_fragile
                target_runtime = target.coefficient_runtime
                if (
                    fragile is not None
                    and target_runtime is not None
                    and target_runtime.environmental_fragile_remaining_ticks
                ):
                    # 「100%的脆弱」 -- damage taken doubles while the window runs.
                    scale_milli = (
                        scale_milli * (fragile.damage_scale_micros // 1000) // 1000
                    )
                if scale_milli == 0:
                    # Declared invulnerability (Doom): the packet is nullified.
                    total_milli = 0
                elif scale_milli != 1000:
                    # Integer floor, but a positive packet never becomes zero:
                    # a *partial* resistance must not grant immunity.  The floor
                    # deliberately does not apply to the zero case above.
                    # **本身仍取整**: 「至少 1 点」是这条规则自己写的下限, 不是
                    # 精度问题, 所以留在整点上并登记待对齐。
                    scaled = total_milli * scale_milli // 1000
                    total_milli = (
                        max(1000, scaled) if total_milli > 0 else scaled
                    )
                # 伤害的精确值带小数(atk 816 打 def 800 是 40.8), 血量是整数:
                # 余数留在目标身上, 下一击接着算。**这一步之后 `total` 才是这一
                # 击真正扣掉的血**, 报告与后续判定全用它。
                carried = target.damage_remainder_milli + total_milli
                total = carried // 1000
                update: dict[str, object] = {
                    "hp": max(0, target.hp - total),
                    "damage_remainder_milli": carried - total * 1000,
                }
                if (
                    update["hp"] == 0
                    and definition.armour_charge_sp
                    and target.hp > 0
                ):
                    # 「**被未激活的<R系列动力装甲>击杀时**，为其回复50/100SP」.
                    # Credited to the armour that landed the killing packet, not
                    # to whatever happened to be nearby.
                    self._credit_armour_charge(target_intents, definition)
                if fragile is not None and total > 0 and any(
                    item.source_id in self._special_tile_ids
                    for item in target_intents
                ):
                    # 「受到**环境伤害**时」.  A hazard tile is the only thing in
                    # this engine that damages from the ground, and both vines
                    # stand on exactly the one their own description names --
                    # b-3's nine volcano tiles, b-3-b's two infection tiles.
                    runtime_for_fragile = (
                        target_runtime
                        if target_runtime is not None
                        else EnemyCoefficientRuntimeState()
                    )
                    update["coefficient_runtime"] = runtime_for_fragile.model_copy(
                        update={
                            "environmental_fragile_remaining_ticks": (
                                fragile.duration_ticks
                            )
                        }
                    )
                    self._emit(
                        EventPhase.DAMAGE,
                        "enemy_environmental_fragile",
                        target.instance_id,
                        {
                            "duration_ticks": fragile.duration_ticks,
                            "damage_scale_micros": fragile.damage_scale_micros,
                        },
                        priority=3,
                    )
                wake_threshold = coefficient_value(definition, "revive.hp_ratio")
                wakes_from_damage = total > 0
                if wake_threshold is not None:
                    next_hp = max(0, target.hp - total)
                    wakes_from_damage = (
                        next_hp * 1_000_000 <= definition.max_hp * wake_threshold
                    )
                if wakes_from_damage and target.herd_resting_remaining_ticks:
                    update["herd_resting_remaining_ticks"] = 0
                    self._emit(
                        EventPhase.DAMAGE,
                        "enemy_herd_resting_ended",
                        target_id,
                        {"reason": "damage_received"},
                        priority=1,
                    )
                runtime = target.coefficient_runtime
                erosion_step = self._def_reduce_step(definition)
                if erosion_step is not None and total > 0 and runtime is not None:
                    # PRTS 「每次受到伤害时，（于防御/法抗计算后）获得1层」: the stack
                    # lands after this hit, so the first hit meets full defence.
                    _per_def, _per_mres, ceiling = erosion_step
                    if runtime.damage_taken_stacks < ceiling:
                        runtime = runtime.model_copy(
                            update={
                                "damage_taken_stacks": runtime.damage_taken_stacks + 1
                            }
                        )
                        update["coefficient_runtime"] = runtime
                creates_ghost = (
                    total > 0
                    and definition.hologram_enemy_id is not None
                    and runtime is not None
                    and not runtime.ghost_created
                )
                if creates_ghost:
                    runtime = runtime.model_copy(update={"ghost_created": True})
                    update["coefficient_runtime"] = runtime
                speed_duration = coefficient_value(definition, "SpeedUp.duration")
                speed_cooldown = coefficient_value(definition, "SpeedUp.cooldown")
                if (
                    total > 0
                    and runtime is not None
                    and speed_duration is not None
                    and speed_cooldown is not None
                    and runtime.speed_cooldown_remaining_ticks == 0
                ):
                    runtime = runtime.model_copy(
                        update={
                            "speed_duration_remaining_ticks": (
                                self._seconds_micros_to_ticks(speed_duration)
                            ),
                            "speed_cooldown_remaining_ticks": (
                                self._seconds_micros_to_ticks(speed_cooldown)
                            ),
                        }
                    )
                    update["coefficient_runtime"] = runtime
                    self._emit(
                        EventPhase.DAMAGE,
                        "enemy_coefficient_speed_started",
                        target_id,
                        {
                            "duration_ticks": runtime.speed_duration_remaining_ticks,
                            "cooldown_ticks": runtime.speed_cooldown_remaining_ticks,
                            "move_speed_micros": coefficient_value(
                                definition, "SpeedUp.move_speed"
                            ),
                        },
                        priority=1,
                    )
                self.enemies[target_id] = target.model_copy(update=update)
                if creates_ghost:
                    self._spawn_damage_hologram(self.enemies[target_id])
                    self._reconcile_blocks(event_phase=EventPhase.DAMAGE)
            elif target_side == "operator" and target_id in self.operators:
                target = self.operators[target_id]
                definition = self._operator_defs[target.operator_id]
                resisted: list[_DamageIntent] = []
                effective_intents: list[_DamageIntent] = []
                for item in target_intents:
                    if item.amount > 0 and definition.incoming_damage_resistance_probability_milli:
                        stream = cast(str, definition.incoming_damage_resistance_rng_stream)
                        roll = self._draw_rng(stream, 1, 1000)
                        if roll <= definition.incoming_damage_resistance_probability_milli:
                            resisted.append(item)
                            self._emit(
                                EventPhase.DAMAGE,
                                "incoming_damage_resisted",
                                target_id,
                                {
                                    "source_id": item.source_id,
                                    "requested": item.amount,
                                    "nonlethal": item.nonlethal,
                                    "probability_milli": definition.incoming_damage_resistance_probability_milli,
                                    "rng_roll": roll,
                                    "rng_draw": self.rng_streams[stream],
                                    "source_semantic_id": definition.incoming_damage_resistance_source_semantic_id,
                                },
                            )
                            continue
                    effective_intents.append(item)
                if target.hp > 0 and definition.incoming_attack_retaliation_scale_milli:
                    triggers = (
                        target_intents
                        if definition.incoming_attack_retaliation_on_resisted
                        else effective_intents
                    )
                    attack = self._scale_fixed(
                        self._operator_effective_stat(target, EffectStat.ATTACK),
                        definition.incoming_attack_retaliation_scale_milli,
                    )
                    for item in triggers:
                        attacker = self.enemies.get(item.source_id)
                        if not item.attack_packet or attacker is None or attacker.hp <= 0:
                            continue
                        amount = _physical_damage(
                            attack, self._enemy_effective_stat(attacker, EffectStat.DEFENSE)
                        )
                        retaliation.append(_DamageIntent(target_id, item.source_id, "enemy", amount))
                        self._emit(
                            EventPhase.DAMAGE,
                            "incoming_attack_retaliation",
                            target_id,
                            {
                                "target_id": item.source_id,
                                "damage": amount,
                                "damage_type": "physical",
                                "source_semantic_id": definition.incoming_attack_retaliation_source_semantic_id,
                                "trigger_resisted": item in resisted,
                            },
                        )
                if not effective_intents:
                    continue
                target_intents = effective_intents
                total_milli = sum(
                    _damage_milli(item.amount) for item in target_intents
                )
                lethal_milli = sum(
                    _damage_milli(item.amount)
                    for item in target_intents
                    if not item.nonlethal
                )
                lethal, nonlethal, remainder = _split_with_remainder(
                    lethal_milli,
                    total_milli - lethal_milli,
                    target.damage_remainder_milli,
                )
                total = lethal + nonlethal
                next_hp = max(0, target.hp - lethal)
                if next_hp and nonlethal:
                    next_hp = max(1, next_hp - nonlethal)
                self.operators[target_id] = target.model_copy(
                    update={"hp": next_hp, "damage_remainder_milli": remainder}
                )
                applied = target.hp - next_hp
                self._apply_attack_leech(target_intents, applied, total)
                self._accumulate_elemental(target_intents, applied, total)
            elif target_side == "summon" and target_id in self.summons:
                target = self.summons[target_id]
                lethal_milli = sum(
                    _damage_milli(item.amount)
                    for item in target_intents
                    if not item.nonlethal
                )
                lethal, nonlethal, remainder = _split_with_remainder(
                    lethal_milli,
                    total_milli - lethal_milli,
                    target.damage_remainder_milli,
                )
                total = lethal + nonlethal
                next_hp = max(0, target.hp - lethal)
                if next_hp and nonlethal:
                    next_hp = max(1, next_hp - nonlethal)
                self.summons[target_id] = target.model_copy(
                    update={"hp": next_hp, "damage_remainder_milli": remainder}
                )
                applied = target.hp - next_hp
                self._reflect_summon_damage(target_id, target_intents, applied)
            else:
                continue
            if target_side == "enemy":
                applied = min(total, target.hp)
            self._emit(
                EventPhase.DAMAGE,
                "damage",
                target_id,
                {
                    "amount": applied,
                    **({"requested": total} if applied != total else {}),
                    "sources": [item.source_id for item in target_intents],
                },
            )
            if target_side in {"operator", "summon"}:
                self._queue_sp_event(
                    target_id,
                    SkillSpEvent.DAMAGE_RECEIVED,
                    trigger_event_sequence=self.event_sequence,
                    event_target_id=target_id,
                )

        # Resolve all source hits first. Retaliation is a damage packet, not a new
        # attack opportunity; it neither resets attack cooldown nor recurses.
        if retaliation:
            self._apply_damage(retaliation)

    def _apply_healing(self, intents: Iterable[_HealIntent]) -> None:
        grouped: dict[str, list[_HealIntent]] = defaultdict(list)
        for intent in intents:
            grouped[intent.target_id].append(intent)
        for target_id in sorted(grouped):
            target = self.operators.get(target_id)
            summon = self.summons.get(target_id)
            if target is None and summon is None:
                continue
            actor = target if target is not None else cast(SummonState, summon)
            if (
                target is None
                and not self._summon_defs[
                    cast(SummonState, actor).summon_id
                ].receives_external_healing
            ):
                continue
            if actor.hp <= 0:
                continue
            target_intents = sorted(grouped[target_id], key=lambda item: item.source_id)
            requested = self._scale_fixed(
                sum(item.amount for item in target_intents),
                self.global_modifiers.healing_multiplier_milli,
            )
            max_hp = (
                self._operator_defs[target.operator_id].max_hp
                if target is not None
                else self._summon_defs[cast(SummonState, summon).summon_id].max_hp
            )
            applied = min(requested, max_hp - actor.hp)
            if applied <= 0:
                continue
            clears_bleeding = bool(actor.coefficient_bleeding_remaining_ticks)
            updated = actor.model_copy(
                update={
                    "hp": actor.hp + applied,
                    **(
                        {
                            "coefficient_bleeding_remaining_ticks": 0,
                            "coefficient_bleeding_pulse_remaining_ticks": 0,
                            "coefficient_bleeding_damage": 0,
                        }
                        if clears_bleeding
                        else {}
                    ),
                }
            )
            if target is not None:
                self.operators[target_id] = cast(OperatorState, updated)
            else:
                self.summons[target_id] = cast(SummonState, updated)
            self._emit(
                EventPhase.DAMAGE,
                "healing",
                target_id,
                {
                    "amount": applied,
                    "requested": requested,
                    "sources": [item.source_id for item in target_intents],
                },
                priority=1,
            )
            if clears_bleeding:
                self._emit(
                    EventPhase.DAMAGE,
                    "enemy_coefficient_bleeding_cleared",
                    target_id,
                    {"reason": "actual_healing"},
                    priority=2,
                )
            trigger_event_sequence = self.event_sequence
            for source_id in sorted({item.source_id for item in target_intents}):
                if source_id in self.operators or source_id in self.summons:
                    self._queue_sp_event(
                        source_id,
                        SkillSpEvent.HEAL_COMMITTED,
                        trigger_event_sequence=trigger_event_sequence,
                        event_target_id=target_id,
                    )

    def _enter_ember_form(self) -> None:
        """Turn a fallen carrier into 暴怒的余烬 instead of removing it.

        PRTS: 「被击倒后重生，持续1s，随后变为暴怒的余烬，1s内不移动且持有[无敌]」,
        the ember 「基础最大生命值临时变为5」 and 「10s后若未被击倒则变回战士形态
        并恢复所有生命」.  An ember that is finished off inside the window dies
        for real, which is why this only fires for a carrier not already in it.
        """

        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            if enemy.hp > 0:
                continue
            runtime = enemy.coefficient_runtime
            if runtime is None or runtime.ember_remaining_ticks:
                continue
            rows = ember_revive_rows(self._enemy_defs[enemy.enemy_id])
            if rows is None:
                continue
            window = self._seconds_micros_to_ticks(rows["interval"])
            self.enemies[instance_id] = enemy.model_copy(
                update={
                    "hp": rows["hits"] // 1_000_000,
                    "coefficient_runtime": runtime.model_copy(
                        update={
                            "ember_remaining_ticks": window,
                            "ember_transition_remaining_ticks": (
                                self.content.ticks_per_second
                                * EMBER_TRANSITION_SECONDS
                            ),
                        }
                    ),
                }
            )
            self._emit(
                EventPhase.DEATH,
                "enemy_ember_form_entered",
                instance_id,
                {
                    "enemy_id": enemy.enemy_id,
                    "ember_hp": rows["hits"] // 1_000_000,
                    "window_ticks": window,
                    "source_key": "Revive[Trigger].prop_max_hp",
                },
                priority=1,
            )

    def _resolve_deaths(self) -> None:
        self._resolve_reborning()
        self._resolve_born_bugs()
        self._enter_ember_form()
        dead_enemies = sorted(enemy_id for enemy_id, enemy in self.enemies.items() if enemy.hp <= 0)
        dead_hologram = any(
            self.enemies[enemy_id].enemy_id in GHOST_HOLOGRAM_IDS
            for enemy_id in dead_enemies
        )
        dead_operators = sorted(
            operator_id for operator_id, operator in self.operators.items() if operator.hp <= 0
        )
        dead_summons = sorted(
            summon_id for summon_id, summon in self.summons.items() if summon.hp <= 0
        )
        removed_route_obstacle = any(
            self._summon_defs[self.summons[summon_id].summon_id].blocks_routes
            for summon_id in dead_summons
        )
        dead_enemy_set = set(dead_enemies)
        for enemy_id in dead_enemies:
            enemy = self.enemies.pop(enemy_id)
            self._arm_death_explosion(enemy)
            self._grant_death_energy(enemy)
            self._release_devoured(enemy)
            self._resolve_death_transform(enemy)
            self._resolve_death_unload(enemy)
            self._enrage_rage_partners(enemy, "death")
            if enemy.enemy_id in self._polluted_death_ids:
                col_milli, row_milli = self._enemy_position_milli(enemy)
                self._create_polluted_zone(
                    enemy,
                    center_col_milli=col_milli,
                    center_row_milli=row_milli,
                    coefficient_prefix="PollutedDie",
                )
            self._apply_enemy_coefficient_death_buff(enemy, excluded_ids=dead_enemy_set)
            cost = self._enemy_defs[enemy.enemy_id].death_life_point_cost
            if cost:
                # 「死亡时扣除2目标生命值」 -- the protect-this-one inversion.  It
                # is deducted here rather than in the leak path because its
                # `lifePointReduce` is 0: walking off the far end costs nothing,
                # and dying is what costs.
                self.life_points = max(0, self.life_points - cost)
                self._emit(
                    EventPhase.DEATH,
                    "enemy_death_life_point_cost",
                    enemy_id,
                    {
                        "enemy_id": enemy.enemy_id,
                        "life_damage": cost,
                        "life_points": self.life_points,
                    },
                    priority=1,
                )
            blood_debt_definition = self._enemy_defs[enemy.enemy_id]
            blood_debt_key = next(
                (
                    key
                    for key in sorted(BLOOD_DEBT_KEYS)
                    if coefficient_value(blood_debt_definition, key) is not None
                ),
                None,
            )
            blood_debt = (
                coefficient_value(blood_debt_definition, blood_debt_key)
                if blood_debt_key is not None
                else None
            )
            if blood_debt is not None:
                # 「被击倒时，使『血债账款』增加」(家族士兵 **20** / 恫吓者 **35** /
                # 强暴恫吓者 **80**; 叙拉古平民走 `Passive.speople_dead_vg.sp`, 库里
                # 40 而 ev-5 改写成 **200**)。
                #
                # **这里原来写着「血债账款是 run 层计数器, 战斗引擎没有可对齐的
                # 行为」——错了。** 消费方就在场上: 「复仇黑手」`trap_092_vgctrl`
                # 拿着这本账, 攒满 500 就是 35 秒全场敌人 +20% 攻 / +15% 防。
                # **找不到消费方不等于没有消费方。**
                credited = self._credit_blood_debt(blood_debt)
                self._emit(
                    EventPhase.DEATH,
                    "enemy_blood_debt_accounted",
                    enemy_id,
                    {
                        "enemy_id": enemy.enemy_id,
                        "blood_debt_micros": blood_debt,
                        "source_key": blood_debt_key,
                        # 场上没有账本时这笔账仍然报出来(与调查熟练度同一处置),
                        # 因为携带者会出现在没有复仇黑手的关卡里。
                        "credited_to_a_ledger": credited,
                    },
                    priority=2,
                )
            self.kills += 1
            self._emit(
                EventPhase.DEATH,
                "death",
                enemy_id,
                {"side": "enemy", "enemy_id": enemy.enemy_id},
            )
        for instance_id in dead_operators:
            operator = self.operators.pop(instance_id)
            definition = self._operator_defs[operator.operator_id]
            roster = self.roster[operator.operator_id]
            self.roster[operator.operator_id] = roster.model_copy(
                update={"redeploy_remaining": definition.redeploy_ticks}
            )
            self._emit(
                EventPhase.DEATH,
                "death",
                instance_id,
                {"side": "operator", "operator_id": operator.operator_id},
                priority=1,
            )
        for instance_id in dead_summons:
            summon = self.summons.pop(instance_id)
            self._emit(
                EventPhase.DEATH,
                "death",
                instance_id,
                {"side": "summon", "summon_id": summon.summon_id},
                priority=1,
            )
            self._apply_summon_death_effects(instance_id, summon)
        dead_actor_ids = set(dead_enemies) | set(dead_operators) | set(dead_summons)
        removed_effect_ids = sorted(
            effect_id
            for effect_id, effect in self.active_effects.items()
            if effect.source_instance_id in dead_actor_ids
            or effect.target_instance_id in dead_actor_ids
        )
        for effect_id in removed_effect_ids:
            del self.active_effects[effect_id]
            self._emit(
                EventPhase.DEATH,
                "effect_removed",
                effect_id,
                {"reason": "actor_death"},
                priority=2,
            )
        dead_projectiles = sorted(
            projectile_id
            for projectile_id, projectile in self.projectiles.items()
            if projectile.target_instance_id in dead_enemies
        )
        for projectile_id in dead_projectiles:
            projectile = self.projectiles.pop(projectile_id)
            self._emit(
                EventPhase.DEATH,
                "projectile_fizzled",
                projectile_id,
                {
                    "target_id": projectile.target_instance_id,
                    "reason": "target_death",
                    "packet_index": projectile.packet_index,
                    "packet_count": projectile.packet_count,
                },
                priority=3,
            )
        if dead_enemies:
            dead_enemy_ids = set(dead_enemies)
            for operator_id, operator in tuple(self.operators.items()):
                skills = tuple(
                    skill.model_copy(update={"selected_target_id": None})
                    if skill.selected_target_id in dead_enemy_ids
                    else skill
                    for skill in operator.skills
                )
                if skills != operator.skills:
                    self.operators[operator_id] = operator.model_copy(update={"skills": skills})
            for summon_id, summon in tuple(self.summons.items()):
                skills = tuple(
                    skill.model_copy(update={"selected_target_id": None})
                    if skill.selected_target_id in dead_enemy_ids
                    else skill
                    for skill in summon.skills
                )
                if skills != summon.skills:
                    self.summons[summon_id] = summon.model_copy(update={"skills": skills})
        if dead_enemies or dead_operators or dead_summons:
            dead = dead_actor_ids
            for operator_id, operator in tuple(self.operators.items()):
                remaining = tuple(item for item in operator.blocked_enemy_ids if item not in dead)
                if remaining != operator.blocked_enemy_ids:
                    self.operators[operator_id] = operator.model_copy(
                        update={"blocked_enemy_ids": remaining}
                    )
            for summon_id, summon in tuple(self.summons.items()):
                remaining = tuple(item for item in summon.blocked_enemy_ids if item not in dead)
                if remaining != summon.blocked_enemy_ids:
                    self.summons[summon_id] = summon.model_copy(
                        update={"blocked_enemy_ids": remaining}
                    )
            for enemy_id, enemy in tuple(self.enemies.items()):
                if enemy.blocked_by in dead:
                    self.enemies[enemy_id] = enemy.model_copy(update={"blocked_by": None})
        if dead_hologram:
            self._reconcile_blocks(event_phase=EventPhase.DEATH)
        if removed_route_obstacle:
            self._shorten_routes_after_obstacle_removal(reason="obstacle_destroyed")

    def _apply_enemy_coefficient_death_buff(
        self,
        source: EnemyState,
        *,
        excluded_ids: set[str],
    ) -> None:
        definition = self._enemy_defs[source.enemy_id]
        duration = coefficient_value(definition, "buff.duration")
        attack_speed = coefficient_value(definition, "buff.attack_speed")
        defense_delta = coefficient_value(definition, "buff.def")
        if duration is None or attack_speed is None or defense_delta is None:
            return
        duration_ticks = self._seconds_micros_to_ticks(duration)
        defense_multiplier = 1000 + defense_delta // 1000
        target_ids: list[str] = []
        # PRTS: 「对**我方/召唤物**施加…」 -- the player's units and summons, not
        # the other enemies.  `excluded_ids` names enemies dying on this tick and
        # is therefore irrelevant here; it is still accepted so the caller does
        # not have to know which side benefits.
        for target_id in sorted(self.operators):
            target = self.operators[target_id]
            self.operators[target_id] = target.model_copy(
                update={
                    "death_buff_remaining_ticks": max(
                        target.death_buff_remaining_ticks, duration_ticks
                    ),
                    "death_buff_attack_speed_points_micros": max(
                        target.death_buff_attack_speed_points_micros, attack_speed
                    ),
                    "death_buff_defense_multiplier_milli": max(
                        target.death_buff_defense_multiplier_milli, defense_multiplier
                    ),
                }
            )
            target_ids.append(target_id)
        for target_id in sorted(self.summons):
            target = self.summons[target_id]
            self.summons[target_id] = target.model_copy(
                update={
                    "death_buff_remaining_ticks": max(
                        target.death_buff_remaining_ticks, duration_ticks
                    ),
                    "death_buff_attack_speed_points_micros": max(
                        target.death_buff_attack_speed_points_micros, attack_speed
                    ),
                    "death_buff_defense_multiplier_milli": max(
                        target.death_buff_defense_multiplier_milli, defense_multiplier
                    ),
                }
            )
            target_ids.append(target_id)
        self._emit(
            EventPhase.DEATH,
            "enemy_coefficient_death_buff_started",
            source.instance_id,
            {
                "target_ids": target_ids,
                "duration_ticks": duration_ticks,
                "attack_speed_additive_micros": attack_speed,
                "defense_multiplier_milli": defense_multiplier,
            },
            priority=1,
        )

    def _move_enemies(self) -> None:
        leaked: list[str] = []
        friendlies: dict[str, OperatorState | SummonState] = self.operators | self.summons
        reserved_blocks = {
            friendly_id: len(friendly.blocked_enemy_ids)
            for friendly_id, friendly in friendlies.items()
        }

        def reserve_blockers(
            enemy: EnemyState,
            point: GridPoint,
        ) -> tuple[tuple[str, ...], tuple[str, ...]]:
            if (
                self._enemy_has_active_hologram(enemy)
                or self._enemy_is_airborne(enemy)
                or self._enemy_is_overloading(enemy)
                or self._enemy_is_blinking(enemy)
            ):
                # 「近地悬浮期间无法被阻挡或近战攻击」
                return (), ()
            candidates = sorted(
                friendly.instance_id
                for friendly in friendlies.values()
                if friendly.position == point
                and reserved_blocks[friendly.instance_id] < self._friendly_block_count(friendly)
            )
            coefficient = self._block_constraint_coefficient(enemy)
            required = coefficient.value_micros // 1_000_000 if coefficient else 1
            if coefficient is None:
                selected = tuple(candidates[:1])
            else:
                best = min(
                    candidates,
                    key=lambda candidate: (
                        -self._friendly_block_count(friendlies[candidate]),
                        candidate,
                    ),
                    default=None,
                )
                selected = (
                    (best,)
                    if best is not None
                    and self._friendly_block_count(friendlies[best]) >= required
                    else ()
                )
            for blocker_id in selected:
                reserved_blocks[blocker_id] += 1
            return selected, tuple(candidates)

        def enter_boundary(
            enemy: EnemyState,
            path: tuple[GridPoint, ...],
            waypoint_index: int,
        ) -> tuple[EnemyState, bool]:
            if self._enemy_defs[enemy.enemy_id].movement_layer is MovementLayer.FLYING:
                return enemy, False
            selected, candidates = reserve_blockers(enemy, path[waypoint_index])
            if selected:
                return enemy, True
            if self._block_constraint_coefficient(enemy) is None:
                return enemy, False
            boundary_enemy = enemy.model_copy(
                update={
                    "waypoint_index": waypoint_index,
                    "segment_progress_milli": 0,
                }
            )
            best_candidate = min(
                candidates,
                key=lambda candidate: (
                    -self._friendly_block_count(friendlies[candidate]),
                    candidate,
                ),
                default=None,
            )
            constraint = self._evaluate_block_constraint(
                boundary_enemy,
                point=path[waypoint_index],
                waypoint_index=waypoint_index,
                blocker_instance_ids=(best_candidate,) if best_candidate is not None else (),
                blocker_count=(
                    self._friendly_block_count(friendlies[best_candidate])
                    if best_candidate is not None
                    else 0
                ),
                event_phase=EventPhase.MOVE,
            )
            return enemy.model_copy(update={"block_constraint": constraint}), False

        for enemy_id in sorted(self.enemies):
            enemy = self.enemies[enemy_id]
            if enemy.enemy_id in GHOST_HOLOGRAM_IDS:
                continue
            if enemy.blocked_by or self._has_control(
                enemy_id,
                ControlEffect.STUN,
                ControlEffect.BIND,
            ):
                continue
            if enemy.herd_resting_remaining_ticks:
                continue
            if self._enemy_is_reborning(enemy):
                continue
            runtime_state = enemy.coefficient_runtime
            if runtime_state is not None and (
                runtime_state.punch_window_remaining_ticks
                or runtime_state.punch_recovery_remaining_ticks
            ):
                # 「技能结束动画持续1s」 holds it after the window, and it stands
                # over its locked target during the window rather than walking
                # away from the thing it is punching.  The window half is not
                # stated in as many words, so it is registered.
                continue
            if (
                runtime_state is not None
                and runtime_state.energy_charge_window_remaining_ticks
            ):
                # 「周期性**停止移动**，并为攻击范围内所有敌人持续提供能量」 --
                # the charger holds still while it channels, which is what makes
                # the window an opening rather than bookkeeping.
                continue
            path = self._enemy_route_points(enemy)
            waits = (
                (0,) * len(path)
                if enemy.path_override
                else self._route_runtime_waits[enemy.route_id]
            )
            teleports = {} if enemy.path_override else self._route_teleports[enemy.route_id]
            stopped_for_block = False
            transit_started = False

            if enemy.route_teleport_destination_index is not None:
                remaining = max(0, enemy.wait_remaining_ticks - 1)
                self._emit(
                    EventPhase.MOVE,
                    "route_teleport_wait",
                    enemy_id,
                    {
                        "remaining_ticks": remaining,
                        "waypoint_index": enemy.waypoint_index,
                        "destination_index": enemy.route_teleport_destination_index,
                    },
                )
                if remaining:
                    self.enemies[enemy_id] = enemy.model_copy(
                        update={"wait_remaining_ticks": remaining}
                    )
                    continue
                entrance = enemy.waypoint_index
                teleport = teleports.get(entrance)
                destination = enemy.route_teleport_destination_index
                if teleport is None or teleport[0] != destination:
                    raise UnsupportedMechanicError("enemy teleport runtime lost its source pair")
                _destination, _wait, _source_entry, source_exit = teleport
                enemy = enemy.model_copy(
                    update={
                        "waypoint_index": destination,
                        "route_teleport_destination_index": None,
                        "wait_remaining_ticks": 0,
                    }
                )
                self._emit(
                    EventPhase.MOVE,
                    "route_teleport_exit",
                    enemy_id,
                    {
                        "source_checkpoint_index": source_exit,
                        "entrance_index": entrance,
                        "destination_index": destination,
                        "col": path[destination].col,
                        "row": path[destination].row,
                    },
                    priority=1,
                )
                enemy, stopped_for_block = enter_boundary(enemy, path, destination)
                wait_ticks = waits[destination]
                if stopped_for_block or wait_ticks:
                    moved = enemy.model_copy(
                        update={
                            "segment_progress_milli": 0,
                            "wait_remaining_ticks": wait_ticks,
                        }
                    )
                    self.enemies[enemy_id] = moved
                    if stopped_for_block:
                        self._reconcile_blocks(event_phase=EventPhase.MOVE)
                    if destination >= len(path) - 1 and wait_ticks == 0:
                        leaked.append(enemy_id)
                    continue
            elif enemy.wait_remaining_ticks > 0:
                remaining = enemy.wait_remaining_ticks - 1
                self.enemies[enemy_id] = enemy.model_copy(
                    update={"wait_remaining_ticks": remaining}
                )
                self._emit(
                    EventPhase.MOVE,
                    "route_wait",
                    enemy_id,
                    {
                        "remaining_ticks": remaining,
                        "waypoint_index": enemy.waypoint_index,
                    },
                )
                if remaining == 0 and enemy.waypoint_index >= len(path) - 1:
                    leaked.append(enemy_id)
                continue

            waypoint_index = enemy.waypoint_index
            progress = enemy.segment_progress_milli
            movement_remainder = enemy.movement_phase_remainder
            wait_ticks = 0

            # Enter a portal before ordinary movement when the actor starts the
            # tick on its type-5 checkpoint.  This avoids inventing a one-tick
            # edge for a source transition whose duration is exactly zero.
            while waypoint_index in teleports:
                destination, transit_wait, source_entry, source_exit = teleports[waypoint_index]
                self._emit(
                    EventPhase.MOVE,
                    "route_teleport_enter",
                    enemy_id,
                    {
                        "source_checkpoint_index": source_entry,
                        "entrance_index": waypoint_index,
                        "destination_index": destination,
                        "transit_wait_ticks": transit_wait,
                    },
                    priority=1,
                )
                if transit_wait:
                    transit_started = True
                    moved = enemy.model_copy(
                        update={
                            "waypoint_index": waypoint_index,
                            "segment_progress_milli": 0,
                            "route_teleport_destination_index": destination,
                            "wait_remaining_ticks": transit_wait,
                        }
                    )
                    self.enemies[enemy_id] = moved
                    break
                entrance = waypoint_index
                waypoint_index = destination
                enemy = enemy.model_copy(update={"waypoint_index": destination})
                self._emit(
                    EventPhase.MOVE,
                    "route_teleport_exit",
                    enemy_id,
                    {
                        "source_checkpoint_index": source_exit,
                        "entrance_index": entrance,
                        "destination_index": destination,
                        "col": path[destination].col,
                        "row": path[destination].row,
                    },
                    priority=1,
                )
                enemy, stopped_for_block = enter_boundary(enemy, path, destination)
                wait_ticks = waits[destination]
                if stopped_for_block or wait_ticks:
                    moved = enemy.model_copy(
                        update={
                            "waypoint_index": waypoint_index,
                            "segment_progress_milli": 0,
                            "wait_remaining_ticks": wait_ticks,
                        }
                    )
                    self.enemies[enemy_id] = moved
                    if stopped_for_block:
                        self._reconcile_blocks(event_phase=EventPhase.MOVE)
                    break
            if transit_started or stopped_for_block or wait_ticks:
                continue
            if waypoint_index >= len(path) - 1:
                moved = enemy.model_copy(
                    update={
                        "waypoint_index": waypoint_index,
                        "segment_progress_milli": 0,
                        "wait_remaining_ticks": 0,
                    }
                )
                self.enemies[enemy_id] = moved
                col_milli, row_milli = self._enemy_position_milli(moved)
                self._emit(
                    EventPhase.MOVE,
                    "move",
                    enemy_id,
                    {
                        "col_milli": col_milli,
                        "row_milli": row_milli,
                        "waypoint_index": waypoint_index,
                    },
                )
                leaked.append(enemy_id)
                continue
            definition = self._enemy_defs[enemy.enemy_id]
            effective_move = self._enemy_effective_stat(enemy, EffectStat.MOVE_MILLI_TILES_PER_TICK)
            if (
                definition.move_milli_tiles_per_second is not None
                and effective_move == definition.move_milli_tiles_per_tick
            ):
                denominator = self.content.ticks_per_second * 1000
                movement, movement_remainder = divmod(
                    movement_remainder
                    + self._enemy_move_milli_tiles_per_second(enemy)
                    * self.global_modifiers.enemy_movement_multiplier_milli,
                    denominator,
                )
            else:
                movement = self._scale_fixed(
                    effective_move,
                    self.global_modifiers.enemy_movement_multiplier_milli,
                )
                movement_remainder = 0
            # Exact per-second movement legitimately yields zero on most ticks
            # for very slow actors; the carried remainder releases progress on
            # the correct later tick.  Forcing one milli-tile here made their
            # wall-clock speed depend on TPS.
            progress += max(0, movement)
            while progress >= 1000 and waypoint_index < len(path) - 1:
                progress -= 1000
                waypoint_index += 1
                enemy, stopped_for_block = enter_boundary(enemy, path, waypoint_index)
                if stopped_for_block:
                    # Blocking begins at the entered grid cell. Discarding the
                    # carry prevents a non-integral movement step from skipping
                    # straight through an occupied blocker tile.
                    progress = 0
                    break
                wait_ticks = waits[waypoint_index]
                if wait_ticks:
                    progress = 0
                    break
                teleport = teleports.get(waypoint_index)
                if teleport is None:
                    continue
                destination, transit_wait, source_entry, source_exit = teleport
                self._emit(
                    EventPhase.MOVE,
                    "route_teleport_enter",
                    enemy_id,
                    {
                        "source_checkpoint_index": source_entry,
                        "entrance_index": waypoint_index,
                        "destination_index": destination,
                        "transit_wait_ticks": transit_wait,
                    },
                    priority=1,
                )
                if transit_wait:
                    progress = 0
                    enemy = enemy.model_copy(
                        update={"route_teleport_destination_index": destination}
                    )
                    wait_ticks = transit_wait
                    break
                entrance = waypoint_index
                waypoint_index = destination
                self._emit(
                    EventPhase.MOVE,
                    "route_teleport_exit",
                    enemy_id,
                    {
                        "source_checkpoint_index": source_exit,
                        "entrance_index": entrance,
                        "destination_index": destination,
                        "col": path[destination].col,
                        "row": path[destination].row,
                    },
                    priority=1,
                )
                enemy, stopped_for_block = enter_boundary(enemy, path, destination)
                if stopped_for_block:
                    progress = 0
                    break
                wait_ticks = waits[destination]
                if wait_ticks:
                    progress = 0
                    break
            if waypoint_index >= len(path) - 1:
                progress = 0
            moved = self._fast_copy(
                enemy,
                {
                    "waypoint_index": waypoint_index,
                    "segment_progress_milli": progress,
                    "movement_phase_remainder": movement_remainder,
                    "wait_remaining_ticks": wait_ticks,
                }
            )
            self.enemies[enemy_id] = moved
            if stopped_for_block:
                # Entry blocking occurs after this tick's attacks but before the
                # movement receipt, so publish it in the MOVE phase while keeping
                # the normal pre-attack reconciliation in the BLOCK phase.
                self._reconcile_blocks(event_phase=EventPhase.MOVE)
            col_milli, row_milli = self._enemy_position_milli(moved)
            self._emit(
                EventPhase.MOVE,
                "move",
                enemy_id,
                {
                    "col_milli": col_milli,
                    "row_milli": row_milli,
                    "waypoint_index": waypoint_index,
                },
            )
            if waypoint_index >= len(path) - 1 and wait_ticks == 0:
                leaked.append(enemy_id)

        for enemy_id in leaked:
            enemy = self.enemies.get(enemy_id)
            if enemy is None or enemy.blocked_by:
                continue
            del self.enemies[enemy_id]
            self._enrage_rage_partners(enemy, "reached_goal")
            runtime = enemy.coefficient_runtime
            if (
                enemy.enemy_id in MAINLINE12_CIVILIAN_IDS
                and runtime is not None
                and runtime.identity_detection_phase == "revealed"
            ):
                maximum_cost_delta = coefficient_value(
                    self._enemy_defs[enemy.enemy_id],
                    "civilianTalent.max_cost",
                )
                if maximum_cost_delta is None or maximum_cost_delta % 1_000_000:
                    raise UnsupportedMechanicError(
                        "Mainline 12 civilian maximum-cost delta is not a whole point"
                    )
                applied_delta = maximum_cost_delta // 1_000_000
                self.max_cost_bonus += applied_delta
                self._emit(
                    EventPhase.LEAK,
                    "enemy_coefficient_max_cost_restored",
                    enemy_id,
                    {
                        "applied_delta": applied_delta,
                        "max_cost_bonus": self.max_cost_bonus,
                        "effective_max_cost": self.effective_max_cost,
                        "current_cost_unchanged": self.cost,
                    },
                )
            spawn_id = (
                enemy.spawn_occurrence_id.rpartition(":")[0]
                if enemy.spawn_occurrence_id is not None
                else ""
            )
            if spawn_id in self._source_wave_unharmful_spawn_ids:
                self.kills += 1
                self._emit(
                    EventPhase.LEAK,
                    "unharmful_exit_counted_as_kill",
                    enemy_id,
                    {"spawn_id": spawn_id, "kills": self.kills},
                )
                continue
            damage = self._enemy_defs[enemy.enemy_id].life_damage
            if damage == 0:
                self._emit(
                    EventPhase.LEAK,
                    "zero_life_damage_exit",
                    enemy_id,
                    {"spawn_id": spawn_id, "life_points": self.life_points},
                )
                continue
            self.life_points = max(0, self.life_points - damage)
            self.leaks += 1
            self._emit(
                EventPhase.LEAK,
                "leak",
                enemy_id,
                {"life_damage": damage, "life_points": self.life_points},
            )

    def _tick_effects_and_skills(self) -> None:
        self._flush_pending_sp_grants()

        for effect_id in sorted(tuple(self.active_effects)):
            effect = self.active_effects[effect_id]
            remaining = effect.remaining_ticks - 1
            if remaining == 0:
                del self.active_effects[effect_id]
                definition = self._effect_vm.definition(effect.effect_id)
                if isinstance(definition, EnemyTimedSelfModifierDefinition):
                    self._emit(
                        EventPhase.TERMINAL,
                        "enemy_self_modifier_expired",
                        effect.target_instance_id,
                        {
                            "effect_instance_id": effect.instance_id,
                            "modifier_id": definition.modifier_id,
                            "stack_key": definition.stack_key,
                            "expired_before_tick": self.tick + 1,
                        },
                    )
                else:
                    self._emit(
                        EventPhase.TERMINAL,
                        "effect_expired",
                        effect_id,
                        {"effect_id": effect.effect_id},
                    )
            else:
                self.active_effects[effect_id] = effect.model_copy(
                    update={"remaining_ticks": remaining}
                )

        duration_ended: list[tuple[str, str]] = []
        became_ready: list[tuple[str, str]] = []
        automatic_candidates: list[tuple[str, str]] = []
        sources: list[tuple[str, OperatorState | SummonState]] = sorted(
            [*self.operators.items(), *self.summons.items()],
            key=lambda item: item[0],
        )
        for instance_id, source in sources:
            updated: list[SkillState] = []
            for state in sorted(source.skills, key=lambda item: item.skill_id):
                definition = self._skill_definition(source, state.skill_id)
                was_ready = self._skill_is_ready(source, state)
                changes: dict[str, int] = {}
                if state.active_remaining > 0:
                    active_remaining = state.active_remaining - 1
                    changes["active_remaining"] = active_remaining
                    if active_remaining == 0:
                        duration_ended.append((instance_id, state.skill_id))
                elif state.cooldown_remaining > 0:
                    changes["cooldown_remaining"] = state.cooldown_remaining - 1
                elif (
                    definition.sp_recovery_mode is SkillSpRecoveryMode.TIME
                    and state.sp < definition.sp_cost * definition.max_charges
                ):
                    changes["sp"] = min(
                        definition.sp_cost * definition.max_charges,
                        state.sp + definition.sp_per_tick,
                    )
                next_state = state.model_copy(update=changes)
                now_ready = self._skill_is_ready(source, next_state)
                if now_ready and not was_ready:
                    became_ready.append((instance_id, state.skill_id))
                if now_ready and definition.activation_policy in {
                    SkillActivationPolicy.AUTOMATIC,
                    SkillActivationPolicy.AUTO_NEXT_ATTACK,
                }:
                    automatic_candidates.append((instance_id, state.skill_id))
                updated.append(next_state)
            next_source = self._fast_copy(source, {"skills": tuple(updated)})
            if isinstance(next_source, OperatorState):
                self.operators[instance_id] = next_source
            elif instance_id in self.summons:
                self.summons[instance_id] = cast(SummonState, next_source)
        for instance_id, skill_id in duration_ended:
            self._emit(
                EventPhase.TERMINAL,
                "skill_duration_ended",
                instance_id,
                {"skill_id": skill_id},
                priority=1,
            )
        for instance_id, skill_id in became_ready:
            self._emit(
                EventPhase.TERMINAL,
                "skill_ready",
                instance_id,
                {"skill_id": skill_id},
                priority=2,
            )
        for instance_id, skill_id in automatic_candidates:
            source = self.operators.get(instance_id) or self.summons.get(instance_id)
            if source is None:
                continue
            state = next(item for item in source.skills if item.skill_id == skill_id)
            definition = self._skill_definition(source, skill_id)
            self._queue_automatic_activation(instance_id, source, state, definition)

    @staticmethod
    def _death_buff_applied(
        actor: "OperatorState | SummonState", stat: EffectStat, base: int
    ) -> int:
        """Apply the drone's death buff to one friendly stat.

        「攻击速度+100、防御力+50%」: the attack-speed figure is points against a
        100 baseline, so the interval is divided; the defence figure is a ratio.
        """

        if not actor.death_buff_remaining_ticks:
            return base
        if stat is EffectStat.ATTACK_INTERVAL_TICKS:
            points = actor.death_buff_attack_speed_points_micros
            if not points:
                return base
            return max(1, base * 100_000_000 // (100_000_000 + points))
        if stat is EffectStat.DEFENSE:
            return base * actor.death_buff_defense_multiplier_milli // 1000
        return base

    @staticmethod
    def _block_reduction_applied(
        actor: "OperatorState | SummonState", stat: EffectStat, base: int
    ) -> int:
        """特工 「短暂减少我方单位的阻挡上限」.

        Floors at zero: a reduction takes the cap away, it does not turn it
        negative.  A cap below what the unit is already holding is exactly how
        an enemy 「冲破我方防线」 -- the surplus is released on the next
        block reconciliation.
        """

        if stat is not EffectStat.BLOCK_COUNT:
            return base
        if not actor.block_reduction_remaining_ticks:
            return base
        return max(0, base - actor.block_reduction)

    def _operator_effective_stat(
        self,
        operator: OperatorState,
        stat: EffectStat,
    ) -> int:
        definition = self._operator_defs[operator.operator_id]
        base_by_stat = {
            EffectStat.ATTACK: definition.attack,
            EffectStat.DEFENSE: definition.defense,
            EffectStat.HEALING_POWER: definition.healing_power,
            EffectStat.ATTACK_INTERVAL_TICKS: definition.attack_interval_ticks,
            EffectStat.ATTACK_RANGE_TILES: definition.attack_range_tiles,
            EffectStat.BLOCK_COUNT: definition.block_count,
        }
        if stat not in base_by_stat:
            raise EffectVMError(f"operator stat is not supported: {stat.value}")
        base = base_by_stat[stat]
        extra_blocked = max(0, len(operator.blocked_enemy_ids) - 1)
        if stat is EffectStat.ATTACK:
            bonus_milli = (
                definition.attack_bonus_milli
                + extra_blocked * definition.attack_bonus_per_extra_blocked_milli
            )
            bonus_milli += self._periodic_self_stat_bonus_milli(operator, stat)
            # 「与气流同向/逆向部署的干员攻击提升/降低」 -- the operator's own
            # deployment facing against the airflow's, +-30%.
            relation = self._airflow_relation(operator.position, operator.direction)
            if relation is not None:
                which, device = relation
                if which == "same":
                    bonus_milli += device.airflow_operator_attack_same_micros // 1000
                elif which == "opposite":
                    bonus_milli += (
                        device.airflow_operator_attack_opposite_micros // 1000
                    )
            base = self._scale_fixed(base, 1000 + bonus_milli)
        elif stat is EffectStat.DEFENSE:
            bonus_milli = (
                definition.defense_bonus_milli
                + extra_blocked * definition.defense_bonus_per_extra_blocked_milli
            )
            bonus_milli += self._periodic_self_stat_bonus_milli(operator, stat)
            base = self._scale_fixed(base, 1000 + bonus_milli)
        value = self._effect_vm.effective_stat(
            base=base,
            stat=stat,
            target_instance_id=operator.instance_id,
            active_effects=self.active_effects.values(),
        )
        if stat is EffectStat.ATTACK_INTERVAL_TICKS:
            # Both sources are points against a 100 baseline, so they are summed
            # before one division rather than divided twice -- two divisions
            # would truncate twice and make the order of application matter.
            black_fog_attack_speed = self._black_fog_attack_speed(operator.position)
            attack_speed_points = (
                0 if black_fog_attack_speed is None else black_fog_attack_speed
            ) + self._attack_speed_aura_points_micros(operator.position)
            if operator.coefficient_cold_remaining_ticks:
                # 寒冷 is attack speed -30, on the same points-against-100 scale.
                attack_speed_points += COLD_ATTACK_SPEED_POINTS * 1_000_000
            if attack_speed_points:
                denominator = 100_000_000 + attack_speed_points
                if denominator <= 0:
                    raise UnsupportedMechanicError(
                        "attack-speed denominator is not positive"
                    )
                value = max(1, value * 100_000_000 // denominator)
        if stat is EffectStat.DEFENSE:
            value = max(
                0,
                value
                - operator.enemy_coefficient_defense_reduction
                - operator.permanent_defence_loss,
            )
        return self._block_reduction_applied(
            operator, stat, self._death_buff_applied(operator, stat, value)
        )

    def _companion_partner_present(self, enemy: EnemyState) -> bool:
        """Whether this carrier's opposite half stands within the pairing radius.

        PRTS states the radius as 1.4 for both halves, and it appears nowhere in
        the data.  The partner is identified by the *complementary* key rather
        than by id, so the rule holds for the elite variants without listing
        them: a guard looks for a blade's ``base_attack_time`` and a blade looks
        for a guard's ``range_radius``.
        """

        definition = self._enemy_defs[enemy.enemy_id]
        carried = {item.key for item in definition.mechanic_coefficients}
        if carried & TRAIT_ABILITY_GUARD_KEYS:
            wanted = TRAIT_ABILITY_BLADE_KEYS
        elif carried & TRAIT_ABILITY_BLADE_KEYS:
            wanted = TRAIT_ABILITY_GUARD_KEYS
        else:
            return False

        col_milli, row_milli = self._enemy_position_milli(enemy)
        reach = TRAIT_ABILITY_PAIRING_RADIUS_MILLI_TILES
        for other_id in sorted(self.enemies):
            if other_id == enemy.instance_id:
                continue
            other = self.enemies[other_id]
            other_keys = {
                item.key
                for item in self._enemy_defs[other.enemy_id].mechanic_coefficients
            }
            if not other_keys & wanted:
                continue
            other_col, other_row = self._enemy_position_milli(other)
            delta_col = other_col - col_milli
            delta_row = other_row - row_milli
            if delta_col * delta_col + delta_row * delta_row <= reach * reach:
                return True
        return False

    def _attack_speed_aura_points_micros(self, point: GridPoint) -> int:
        """Strongest enemy attack-speed suppression covering ``point``.

        Returned on the engine's points-against-100 scale, so it can be summed
        with the other attack-speed sources before the single division.  The
        source ships a *fraction* (-0.5 = -50%), hence the conversion; see
        ``ATTACK_SPEED_AURA_KEYS`` for why that matters.

        PRTS 「寒霜」 says 「不可叠加」, so several carriers contribute the single
        strongest value rather than a sum.
        """

        strongest = 0
        for instance_id in sorted(self.enemies):
            enemy = self.enemies[instance_id]
            definition = self._enemy_defs[enemy.enemy_id]
            guard_points = coefficient_value(definition, "traitAbility.attack_speed")
            if guard_points is not None and self._companion_partner_present(enemy):
                # 深池伙友卫队's field: already points against 100, so no scale
                # conversion here -- unlike `atkSpeedDown`, which is a fraction.
                guard_radius = coefficient_value(
                    definition, "traitAbility.range_radius"
                )
                if guard_radius is None:
                    raise UnsupportedMechanicError(
                        "companion guard lost its field radius at runtime"
                    )
                guard_milli = guard_radius // 1000
                center_col, center_row = self._enemy_position_milli(enemy)
                delta_col = point.col * 1000 - center_col
                delta_row = point.row * 1000 - center_row
                inside = (
                    delta_col * delta_col + delta_row * delta_row
                    <= guard_milli * guard_milli
                )
                if inside and guard_points < strongest:
                    strongest = guard_points
            fraction = coefficient_value(definition, "atkSpeedDown.attack_speed")
            if fraction is None:
                continue
            radius = coefficient_value(definition, "defup.range_radius")
            if radius is None:
                raise UnsupportedMechanicError(
                    "attack-speed aura lost its radius at runtime"
                )
            radius_milli = radius // 1000
            center_col, center_row = self._enemy_position_milli(enemy)
            delta_col = point.col * 1000 - center_col
            delta_row = point.row * 1000 - center_row
            if delta_col * delta_col + delta_row * delta_row > radius_milli * radius_milli:
                continue
            points = fraction * ATTACK_SPEED_FRACTION_TO_POINTS
            if points < strongest:
                strongest = points
        return strongest

    def _periodic_self_stat_bonus_milli(
        self,
        operator: OperatorState,
        stat: EffectStat,
    ) -> int:
        definition = self._operator_defs[operator.operator_id]
        if not definition.periodic_self_stat_interval_ticks:
            return 0
        stacks = min(
            definition.periodic_self_stat_max_stacks,
            max(0, self.tick - operator.deployment_tick)
            // definition.periodic_self_stat_interval_ticks,
        )
        per_stack = (
            definition.periodic_self_attack_per_stack_milli
            if stat is EffectStat.ATTACK
            else definition.periodic_self_defense_per_stack_milli
        )
        return stacks * per_stack

    def _summon_effective_stat(
        self,
        summon: SummonState,
        stat: EffectStat,
    ) -> int:
        definition = self._summon_defs[summon.summon_id]
        base_by_stat = {
            EffectStat.ATTACK: definition.attack,
            EffectStat.DEFENSE: definition.defense,
            EffectStat.HEALING_POWER: definition.healing_power,
            EffectStat.ATTACK_INTERVAL_TICKS: definition.attack_interval_ticks,
            EffectStat.BLOCK_COUNT: definition.block_count,
        }
        if stat not in base_by_stat:
            raise EffectVMError(f"summon stat is not supported: {stat.value}")
        value = self._effect_vm.effective_stat(
            base=base_by_stat[stat],
            stat=stat,
            target_instance_id=summon.instance_id,
            active_effects=self.active_effects.values(),
        )
        if stat is EffectStat.ATTACK_INTERVAL_TICKS:
            # PRTS 「寒霜」 says 「所有我方单位」, which includes summons.
            black_fog_attack_speed = self._black_fog_attack_speed(summon.position)
            attack_speed_points = (
                0 if black_fog_attack_speed is None else black_fog_attack_speed
            ) + self._attack_speed_aura_points_micros(summon.position)
            if summon.coefficient_cold_remaining_ticks:
                # 寒冷 is attack speed -30, on the same points-against-100 scale.
                attack_speed_points += COLD_ATTACK_SPEED_POINTS * 1_000_000
            if attack_speed_points:
                denominator = 100_000_000 + attack_speed_points
                if denominator <= 0:
                    raise UnsupportedMechanicError(
                        "attack-speed denominator is not positive"
                    )
                value = max(1, value * 100_000_000 // denominator)
        if stat is EffectStat.DEFENSE:
            value = max(
                0,
                value
                - summon.enemy_coefficient_defense_reduction
                - summon.permanent_defence_loss,
            )
        return self._block_reduction_applied(
            summon, stat, self._death_buff_applied(summon, stat, value)
        )

    def _enemy_field_aura(self) -> tuple[int, int]:
        """``(attack_speed_points, move_speed_multiple_milli)`` from devices.

        山海密语仪 buffs the enemy side 「在场时」, so it is recomputed from
        the **live** instances every time a stat is read: destroy one and its
        share of the buff is gone on the same tick, which is the only reason a
        defender would spend attacks on it.

        Stacking is per live instance -- PRTS says 「可无限叠加」 and 5-6 places
        six.  Points add; a 「提升至」 multiple compounds, because that is how
        two of the same modifier compose and there is no shipped cap.  **The
        composition itself is registered as pending alignment**: the data states
        the figure for one instance and says stacking exists, not how six of
        them combine.
        """

        if not self._has_enemy_field_aura:
            return 0, 1000
        points = 0
        multiple_milli = 1000
        for summon in self.summons.values():
            definition = self._summon_defs.get(summon.summon_id)
            if definition is None:
                continue
            points += definition.enemy_attack_speed_bonus
            if definition.enemy_move_speed_multiple_milli:
                multiple_milli = (
                    multiple_milli * definition.enemy_move_speed_multiple_milli
                ) // 1000
        return points, multiple_milli

    def _enemy_effective_stat(self, enemy: EnemyState, stat: EffectStat) -> int:
        definition = self._enemy_defs[enemy.enemy_id]
        base_by_stat = {
            EffectStat.ATTACK: definition.attack,
            EffectStat.DEFENSE: definition.defense,
            EffectStat.MAGIC_RESISTANCE: min(
                100,
                definition.magic_resistance
                + self._refracting_bonus_points(definition),
            ),
            EffectStat.ATTACK_INTERVAL_TICKS: definition.attack_interval_ticks,
            EffectStat.MOVE_MILLI_TILES_PER_TICK: definition.move_milli_tiles_per_tick,
        }
        if stat not in base_by_stat:
            raise EffectVMError(f"enemy stat is not supported: {stat.value}")
        base = base_by_stat[stat]
        if (
            stat is EffectStat.MOVE_MILLI_TILES_PER_TICK
            and definition.bomb_run is not None
            and enemy.coefficient_runtime is not None
            and enemy.coefficient_runtime.bomb_run_spent
        ):
            # 「技能结束后**移速最终提升至200%**」 -- 「最终」, so it is the finished
            # figure rather than another factor to stack, and it never lapses.
            base = (
                base * definition.bomb_run.move_speed_multiple_micros // 1_000_000
            )
        if (
            definition.beam_weakness is not None
            and stat in {EffectStat.DEFENSE, EffectStat.MAGIC_RESISTANCE}
            and self._enemy_stands_on_beam(enemy)
        ):
            # 「处于重整束流上时，防御和法术抗性大幅下降」 -- -1200 and -50 on a
            # carrier whose shipped defence is 1200, so the light takes all of it.
            base -= (
                definition.beam_weakness.defence_loss
                if stat is EffectStat.DEFENSE
                else definition.beam_weakness.magic_resistance_loss
            )
            base = max(0, base)
        runtime_state = enemy.coefficient_runtime
        if (
            stat is EffectStat.DEFENSE
            and definition.devour is not None
            and runtime_state is not None
            and runtime_state.devoured_operator_id is not None
        ):
            # 「饱腹形态：防御力+700」 -- flat, on a carrier whose shipped defence
            # is **0**, so this is the whole of its armour rather than a bonus
            # on top of some.
            base += definition.devour.defence_bonus
        if (
            stat is EffectStat.ATTACK_INTERVAL_TICKS
            and definition.guided_strike is not None
            and runtime_state is not None
            and runtime_state.guide_buff_remaining_ticks
        ):
            # 「随后**自身**攻击速度+150，持续20s」 -- points against the 100
            # baseline, on the caster rather than on what it just hit.
            base = max(
                1,
                base * 100 // (100 + definition.guided_strike.buff_attack_speed_bonus),
            )
        if runtime_state is not None and runtime_state.chain_buff_remaining_ticks:
            # 「受影响的目标攻击速度+100，移动速度+100%」 -- points against the
            # 100 baseline and a bonus fraction, exactly as 【引导】 and the
            # self-rage read their own.  Carried on the *target*, because two
            # carriers ship different figures and either may have reached it.
            if stat is EffectStat.ATTACK_INTERVAL_TICKS:
                base = max(
                    1,
                    base * 100 // (100 + runtime_state.chain_buff_attack_speed),
                )
            elif stat is EffectStat.MOVE_MILLI_TILES_PER_TICK:
                base = max(
                    1,
                    base
                    * (1_000_000 + runtime_state.chain_buff_move_speed_micros)
                    // 1_000_000,
                )
        if (
            stat is EffectStat.ATTACK
            and definition.reborning is not None
            and runtime_state is not None
            and runtime_state.reborning_empowered
        ):
            base = (
                base
                * (1_000_000 + definition.reborning.attack_bonus_micros)
                // 1_000_000
            )
        decay = definition.spawn_decay
        if stat is EffectStat.ATTACK and decay is not None:
            elapsed = (
                runtime_state.spawn_decay_elapsed_ticks
                if runtime_state is not None
                else 0
            )
            # 「线性降低至30%，每0.1秒降低一次」 -- stepped, so a carrier caught
            # between two steps is worth what it was at the last one.
            total_steps = max(1, decay.duration_ticks // SPAWN_DECAY_STEP_TICKS)
            steps = min(elapsed // SPAWN_DECAY_STEP_TICKS, total_steps)
            lost = (1_000_000 - decay.floor_scale_micros) * steps // total_steps
            base = max(1, base * (1_000_000 - lost) // 1_000_000)
        command_attack, command_defense, command_move, command_speed = (
            self._tactical_command_bonuses(enemy)
        )
        if stat is EffectStat.ATTACK and command_attack:
            # 「攻击力+10%」 / 「+50%」 -- fractions, so they multiply the base.
            base = base * (1_000_000 + command_attack) // 1_000_000
        if stat is EffectStat.DEFENSE and command_defense:
            # 「防御力+100」 -- flat, unlike the attack beside it in the same
            # sentence.  One family, two units.
            base += command_defense
        if stat is EffectStat.MOVE_MILLI_TILES_PER_TICK and command_move:
            base = max(1, base * (1_000_000 + command_move) // 1_000_000)
        if stat is EffectStat.ATTACK_INTERVAL_TICKS and command_speed:
            # Points against the 100 baseline, so the interval divides.
            base = max(1, base * 100 // (100 + command_speed))
        aura_points, aura_multiple_milli = self._enemy_field_aura()
        if stat is EffectStat.ATTACK_INTERVAL_TICKS and aura_points:
            # Points against a 100 baseline, so the interval is divided.
            base = max(1, base * 100_000_000 // (100_000_000 + aura_points * 1_000_000))
        if (
            stat is EffectStat.MOVE_MILLI_TILES_PER_TICK
            and aura_multiple_milli != 1000
        ):
            base = max(1, base * aura_multiple_milli // 1000)
        if stat is EffectStat.MOVE_MILLI_TILES_PER_TICK and self._airflow_instances:
            # 「同向/逆向移动的敌人移速提升/降低」 -- +80% walking with the flow,
            # -50% walking into it, and nothing crossing it.
            # Rounded to the nearest tile, not truncated: the airflow acts on
            # an enemy walking *through* its three tiles, and movement is
            # continuous, so strict tile occupancy would only ever catch the
            # exact boundary ticks.
            centre = self._enemy_position_milli(enemy)
            position = GridPoint(
                col=(centre[0] + 500) // 1000,
                row=(centre[1] + 500) // 1000,
            )
            relation = self._airflow_relation(
                position, self._enemy_step_direction(enemy)
            )
            if relation is not None:
                which, device = relation
                delta_micros = 0
                if which == "same":
                    delta_micros = device.airflow_enemy_move_same_micros
                elif which == "opposite":
                    delta_micros = device.airflow_enemy_move_opposite_micros
                if delta_micros:
                    base = max(
                        1, base * (1_000_000 + delta_micros) // 1_000_000
                    )
        if stat is EffectStat.ATTACK_INTERVAL_TICKS:
            runtime_state = enemy.coefficient_runtime
            if runtime_state is not None and runtime_state.overload_remaining_ticks:
                overload_points = coefficient_value(
                    definition, "over_energy.attack_speed"
                )
                if overload_points is None:
                    raise UnsupportedMechanicError(
                        "overload lost its attack-speed figure at runtime"
                    )
                base = max(
                    1, base * 100_000_000 // (100_000_000 + overload_points)
                )
            if (
                runtime_state is not None
                and runtime_state.self_rage_active
                and definition.self_rage is not None
            ):
                # 「攻击速度+100」 -- the same points-against-100 reading as the
                # paired rage below, which is what makes the interval divide.
                base = max(
                    1,
                    base
                    * 100
                    // (100 + definition.self_rage.attack_speed_bonus),
                )
            if runtime_state is not None and runtime_state.rage_active:
                rage_points = coefficient_value(
                    definition, "triggerrage.attack_speed"
                )
                if rage_points is None:
                    raise UnsupportedMechanicError(
                        "paired rage lost its attack-speed figure at runtime"
                    )
                # 「攻击速度增加100」 -- points against a 100 baseline, so the
                # interval is divided rather than subtracted from.
                base = max(
                    1, base * 100_000_000 // (100_000_000 + rage_points)
                )
            if (
                runtime_state is not None
                and runtime_state.victory_active
                and definition.victory_buff is not None
            ):
                # 「攻击速度+100」 -- points, same encoding as rage's.
                base = max(
                    1,
                    base
                    * 100_000_000
                    // (
                        100_000_000
                        + definition.victory_buff.attack_speed_points * 1_000_000
                    ),
                )
            stack_rows_speed = attack_stack_rows(definition)
            if (
                stack_rows_speed is not None
                and runtime_state is not None
                and runtime_state.attack_stacks
            ):
                # 「提高…攻击速度」 -- points, so the interval is divided.
                points = (
                    stack_rows_speed["attack_speed"] * runtime_state.attack_stacks
                )
                base = max(1, base * 100_000_000 // (100_000_000 + points))
            blade_seconds = coefficient_value(
                definition, "traitAbility.base_attack_time"
            )
            if blade_seconds is not None and self._companion_partner_present(enemy):
                # 「自身攻击间隔减少1.2s」 -- seconds off the interval, not points
                # of attack speed.  Negative in the data, hence the addition.
                base = max(
                    1, base + self._seconds_micros_to_ticks(blade_seconds)
                )
        if stat in (EffectStat.DEFENSE, EffectStat.MAGIC_RESISTANCE):
            overload = (
                enemy.coefficient_runtime.overload_remaining_ticks
                if enemy.coefficient_runtime is not None
                else 0
            )
            if overload:
                if stat is EffectStat.DEFENSE:
                    # 「防御力最终提升至200%」 -- a resulting multiple.
                    multiple = coefficient_value(definition, "over_energy.def")
                    if multiple is None:
                        raise UnsupportedMechanicError(
                            "overload lost its defence multiple at runtime"
                        )
                    base = base * multiple // 1_000_000
                else:
                    # 「法术抗性+30」 -- points, and capped like any resistance.
                    bonus = coefficient_value(
                        definition, "over_energy.magic_resistance"
                    )
                    if bonus is None:
                        raise UnsupportedMechanicError(
                            "overload lost its resistance figure at runtime"
                        )
                    base = min(100, base + bonus // 1_000_000)
            runtime_stacks = (
                enemy.coefficient_runtime.damage_taken_stacks
                if enemy.coefficient_runtime is not None
                else 0
            )
            if runtime_stacks:
                step = self._def_reduce_step(definition)
                if step is None:
                    raise UnsupportedMechanicError(
                        "eroding resistance stacks exist without their source"
                    )
                per_def, per_mres, _ceiling = step
                erosion = runtime_stacks * (
                    per_def if stat is EffectStat.DEFENSE else per_mres
                )
                base = max(0, base - erosion)
        if stat is EffectStat.DEFENSE:
            base += self._enemy_defence_aura_bonus(enemy)
        if (
            stat in (EffectStat.DEFENSE, EffectStat.MAGIC_RESISTANCE)
            and enemy.coefficient_runtime is not None
            and enemy.coefficient_runtime.victory_active
            and definition.victory_buff is not None
        ):
            # 「防御力最终+2000，法术抗性最终+50」 -- 最终 means applied after
            # the rest, which is what this position gives.  Resistance
            # is capped the way every other resistance bonus here is.
            if stat is EffectStat.DEFENSE:
                base += definition.victory_buff.defence_bonus
            else:
                base = min(
                    100,
                    base + definition.victory_buff.magic_resistance_bonus,
                )
        runtime_stacks_holder = enemy.coefficient_runtime
        if stat is EffectStat.ATTACK:
            stack_rows_outer = attack_stack_rows(definition)
            if (
                stack_rows_outer is not None
                and runtime_stacks_holder is not None
                and runtime_stacks_holder.attack_stacks
            ):
                # 「连续攻击时会提高攻击力」 -- +7% per banked stack, at any health.
                base = (
                    base
                    * (
                        1_000_000
                        + stack_rows_outer["attack"]
                        * runtime_stacks_holder.attack_stacks
                    )
                    // 1_000_000
                )
        if stat is EffectStat.ATTACK and enemy.hp * 2 <= definition.max_hp:
            # Half-health attack boost, driven by the enemy's own data rather
            # than an id allow-list.  `atkup.atk` names three different
            # mechanics; the stacking on-attack variant is the one that also
            # carries `atkup.max_stack_cnt`, so it is excluded here and stays
            # reported as unimplemented instead of being given the wrong rule.
            attack_delta = coefficient_value(definition, "atkup.atk")
            stacking = coefficient_value(definition, "atkup.max_stack_cnt")
            if attack_delta is not None and stacking is None:
                if attack_delta < 0:
                    raise UnsupportedMechanicError(
                        "half-health attack coefficient must not be negative"
                    )
                base = base * (1_000_000 + attack_delta) // 1_000_000
            elif (
                attack_delta is None
                and definition.ranged_while_unblocked
            ):
                raise UnsupportedMechanicError(
                    "Blade Point half-health attack coefficient disappeared at runtime"
                )
        if stat in (EffectStat.ATTACK, EffectStat.DEFENSE):
            # 【清算时刻】: 「持续期间**所有敌人**攻击力+20%，防御力+15%」——
            # 全场生效, 与携带者是谁无关, 所以它读的是场上的账本而不是这只敌人
            # 自己的天赋。
            reckoning = self._reckoning_bonus_micros(stat)
            if reckoning:
                base = base * (1_000_000 + reckoning) // 1_000_000
            if reckoning and stat is EffectStat.ATTACK:
                # 「部分敌人激活额外的能力」: 家族暗影灭迹人自己的那一份
                # (`Passive.atk` **0.15**)叠在全场那一份之上。
                own = coefficient_value(definition, "Passive.atk")
                if own is not None:
                    base = base * (1_000_000 + own) // 1_000_000
            if reckoning and stat is EffectStat.DEFENSE:
                own = coefficient_value(definition, "Passive.def")
                if own is not None:
                    base = base * (1_000_000 + own) // 1_000_000
        if stat is EffectStat.ATTACK_INTERVAL_TICKS:
            reckoning_speed = self._reckoning_bonus_micros(EffectStat.ATTACK)
            if reckoning_speed:
                # 「攻击速度+50」是**点数**, 与全场那两条百分比不同: 它只给声明了
                # 这个键的敌人, 而且按攻速点数除算(与引擎里别处的攻速加成同口径)。
                own_speed = coefficient_value(definition, "Passive.attack_speed")
                if own_speed is not None:
                    base = max(1, base * 100_000_000 // (100_000_000 + own_speed))
        if stat is EffectStat.DEFENSE and enemy.blocked_by is not None:
            blocked_defense = coefficient_value(definition, "Def.def")
            if blocked_defense is not None:
                if blocked_defense < 0 or blocked_defense % 1_000_000:
                    raise UnsupportedMechanicError(
                        "Tiacauh blocked defense must be a non-negative whole stat point"
                    )
                base += blocked_defense // 1_000_000
        if stat is EffectStat.DEFENSE and definition.formation_defense_aura:
            for source in self.enemies.values():
                if source.instance_id == enemy.instance_id:
                    continue
                if source.route_teleport_destination_index is not None:
                    continue
                source_definition = self._enemy_defs[source.enemy_id]
                if not source_definition.formation_defense_aura:
                    continue
                source_col, source_row = self._enemy_position_milli(source)
                target_col, target_row = self._enemy_position_milli(enemy)
                delta_col = source_col - target_col
                delta_row = source_row - target_row
                if (
                    delta_col * delta_col + delta_row * delta_row
                    <= source_definition.formation_aura_radius_milli_tiles**2
                ):
                    base += source_definition.formation_defense_aura
        runtime = enemy.coefficient_runtime
        if runtime is not None:
            if runtime.initial_guard_remaining_ticks:
                if stat is EffectStat.DEFENSE:
                    guard_defense = coefficient_value(definition, "buff.def")
                    if guard_defense is not None:
                        base += guard_defense // 1_000_000
                elif stat is EffectStat.MAGIC_RESISTANCE:
                    guard_resistance = coefficient_value(
                        definition, "buff.magic_resistance"
                    )
                    if guard_resistance is not None:
                        base = min(100, base + guard_resistance // 1_000_000)
            if stat is EffectStat.DEFENSE and runtime.friendly_buff_remaining_ticks:
                base = self._scale_fixed(base, runtime.friendly_defense_multiplier_milli)
            elif stat is EffectStat.ATTACK_INTERVAL_TICKS and (
                runtime.friendly_buff_remaining_ticks
                and runtime.friendly_attack_speed_additive_micros
            ):
                base = max(
                    1,
                    base
                    * 100_000_000
                    // (100_000_000 + runtime.friendly_attack_speed_additive_micros),
                )
            elif stat is EffectStat.MOVE_MILLI_TILES_PER_TICK:
                multiplier_micros = 1_000_000
                if runtime.speed_duration_remaining_ticks:
                    # 染污躯壳 PRTS: 「受到伤害时若未被阻挡则2s内**移动速度+300%**
                    # （被阻挡时立刻解除），5s内无法再次触发」.  The shipped 3.0 is
                    # that **+300%**, so it adds; reading it as a resulting x3
                    # left the carrier at three quarters of its real speed.
                    multiplier_micros += (
                        coefficient_value(definition, "SpeedUp.move_speed") or 0
                    )
                idle_rows = idle_speed_rows(definition)
                if idle_rows is not None:
                    window = self._seconds_micros_to_ticks(idle_rows["duration"])
                    if runtime.no_attack_idle_ticks >= window:
                        # 歪曲施法者 PRTS: 「出场/攻击结束后5s内未进行攻击时，
                        # **移动速度+400%**」.  A bonus fraction, like its
                        # damage-triggered sibling -- both were read as resulting
                        # multiples and both were a fifth to a quarter short.
                        multiplier_micros += idle_rows["multiple"]
                if runtime.post_hit_speed_active:
                    # 鸭爵 PRTS: 「受到伤害时开始奔跑，**移动速度+400%**」 -- the
                    # shipped 4.0 is that bonus, so it adds.  Read as a resulting
                    # x4 the duke ran at four fifths of its real speed.
                    multiplier_micros += (
                        coefficient_value(definition, "run.attack@move_speed") or 0
                    )
                revive_speed = coefficient_value(definition, "revive.move_speed")
                if revive_speed is not None and enemy.herd_resting_remaining_ticks == 0:
                    # 角兽 PRTS: 「【临战】状态：**移动速度+200%**」 -- the shipped
                    # 2.0 is that bonus.  Both spellings of the key
                    # (`Revive.move_speed` aliases onto this one) go through
                    # here, so the correction covers the Sarkaz pair too.
                    multiplier_micros += revive_speed
                if runtime.rage_active:
                    # 「移动速度最终提升至200%」 -- a resulting multiple, so it
                    # competes with the other multipliers rather than adding.
                    rage_move = coefficient_value(definition, "triggerrage.move_speed")
                    if rage_move is None:
                        raise UnsupportedMechanicError(
                            "paired rage lost its move multiplier at runtime"
                        )
                    multiplier_micros = max(multiplier_micros, rage_move)
                if runtime.self_rage_active and definition.self_rage is not None:
                    # 「移动速度+100%」 -- a bonus *fraction*, so it adds on top
                    # rather than competing the way 「提升至200%」 does.
                    multiplier_micros += definition.self_rage.move_speed_bonus_micros
                if runtime.victory_active and definition.victory_buff is not None:
                    # 「移动速度+100%」 -- a bonus *fraction*, unlike rage's
                    # 「提升至200%」 resulting multiple, so it adds to
                    # whichever multiplier won rather than competing.
                    multiplier_micros += (
                        definition.victory_buff.move_speed_bonus_micros
                    )
                if (
                    definition.devour is not None
                    and runtime.devoured_operator_id is not None
                ):
                    # 「饱腹形态：移动速度-30%」 -- a penalty fraction, so it
                    # subtracts from whichever multiplier won above.
                    multiplier_micros -= definition.devour.move_speed_penalty_micros
                conditional = definition.conditional_takeoff
                if conditional is not None and runtime.conditional_airborne:
                    # 「移动速度 +10%」 -- a bonus fraction while airborne, so it
                    # adds to whichever multiplier won rather than replacing it.
                    multiplier_micros += conditional.airborne_move_speed_bonus_micros
                takeoff = definition.takeoff
                if takeoff is not None and runtime.airborne_remaining_ticks:
                    # 「飞行模式：……**移动速度+50%**」 -- a bonus fraction, the way
                    # 弧光突击队's +10% is, not a resulting multiple.
                    multiplier_micros += takeoff.flight_move_speed_bonus_micros
                if takeoff is not None and (
                    runtime.takeoff_slow_remaining_ticks
                    or runtime.landing_slow_remaining_ticks
                ):
                    # 「移动速度**最终降低90%**」 -- 「最终」, so it is the finished
                    # figure and replaces everything above rather than stacking.
                    multiplier_micros = takeoff.slow_move_multiplier_micros
                if runtime.rush_acceleration_stacks:
                    # Each accrued increment adds that fraction of base speed, so
                    # the ramp composes on top of whichever multiplier won above
                    # rather than competing with it.
                    ramp = rush_acceleration_rows(definition)
                    multiplier_micros += (
                        ramp["move_speed"] * runtime.rush_acceleration_stacks
                    )
                base = max(1, base * multiplier_micros // 1_000_000)
        return self._effect_vm.effective_stat(
            base=base,
            stat=stat,
            target_instance_id=enemy.instance_id,
            active_effects=self.active_effects.values(),
        )

    def _enemy_defence_aura_bonus(self, enemy: EnemyState) -> int:
        """Additive defence this enemy currently receives from allied auras.

        Derived from live positions every time it is asked, so there is no
        runtime state to snapshot: an aura that depends only on who stands
        where cannot drift out of step with the board.

        Two families, cited rather than assumed (see ``DEFENCE_AURA_KEYS``):

        * ``defup.def`` -- granted by *other* enemies carrying it, within their
          own ``defup.range_radius``, and **not stackable**, so several carriers
          contribute the largest single bonus rather than a sum.
        * ``auraDefup.def`` -- mutual support among the phalanx units: the
          receiver must carry it too, and it gains its own bonus value once per
          *other* nearby carrier, **stacking without limit**.
        """

        receiver_col, receiver_row = self._enemy_position_milli(enemy)
        best_defup = 0
        aura_defup_neighbours = 0
        own_aura_defup = coefficient_value(
            self._enemy_defs[enemy.enemy_id], "auraDefup.def"
        )

        for other_id in sorted(self.enemies):
            if other_id == enemy.instance_id:
                # Both families say 「其他」: a carrier never buffs itself.
                continue
            other = self.enemies[other_id]
            other_definition = self._enemy_defs[other.enemy_id]
            grants_defup = coefficient_value(other_definition, "defup.def")
            grants_aura = coefficient_value(other_definition, "auraDefup.def")
            if grants_defup is None and grants_aura is None:
                continue
            other_col, other_row = self._enemy_position_milli(other)
            delta_col = other_col - receiver_col
            delta_row = other_row - receiver_row
            distance_squared = delta_col * delta_col + delta_row * delta_row

            if grants_defup is not None:
                radius = coefficient_value(other_definition, "defup.range_radius")
                if radius is None:
                    raise UnsupportedMechanicError(
                        "defup defence aura lost its radius at runtime"
                    )
                radius_milli = radius // 1000
                if distance_squared <= radius_milli * radius_milli:
                    best_defup = max(best_defup, grants_defup // 1_000_000)

            if (
                grants_aura is not None
                and own_aura_defup is not None
                and distance_squared
                <= AURA_DEFUP_RADIUS_MILLI_TILES * AURA_DEFUP_RADIUS_MILLI_TILES
            ):
                aura_defup_neighbours += 1

        bonus = best_defup
        if own_aura_defup is not None and aura_defup_neighbours:
            bonus += (own_aura_defup // 1_000_000) * aura_defup_neighbours
        return bonus

    @staticmethod
    def _def_reduce_step(definition: EnemyDefinition) -> tuple[int, int, int] | None:
        """``(def_per_stack, mres_per_stack, max_stacks)``, or None if absent.

        The shipped numbers are the totals at maximum stacks and divide exactly,
        so the step is arithmetic on the data rather than a separate constant.
        """

        total_def = coefficient_value(definition, "def_reduce.def")
        total_mres = coefficient_value(definition, "def_reduce.magic_resistance")
        max_stacks = coefficient_value(definition, "def_reduce.max_stack_cnt")
        if total_def is None or total_mres is None or max_stacks is None:
            return None
        stacks = max_stacks // 1_000_000
        if stacks <= 0:
            raise UnsupportedMechanicError(
                "eroding resistance has no positive stack ceiling"
            )
        return (
            (-total_def // 1_000_000) // stacks,
            (-total_mres // 1_000_000) // stacks,
            stacks,
        )

    @staticmethod
    def _refracting_bonus_points(definition: EnemyDefinition) -> int:
        """Refraction's magic-resistance bonus, in points.

        Two carriers of the same number: the typed field on
        ``EnemyDefinition`` (hand-filled by fixtures) and the source coefficient
        the stage compiler now emits.  The larger wins rather than the sum, so a
        bundle that supplies both does not double-count.
        """

        from_source = coefficient_value(definition, "refracting.magic_resistance")
        return max(
            definition.refracting_magic_resistance_bonus,
            0 if from_source is None else from_source // 1_000_000,
        )

    def _enemy_magic_resistance(self, enemy: EnemyState) -> int:
        return self._enemy_effective_stat(enemy, EffectStat.MAGIC_RESISTANCE)

    def _enemy_move_milli_tiles_per_second(self, enemy: EnemyState) -> int:
        definition = self._enemy_defs[enemy.enemy_id]
        base = definition.move_milli_tiles_per_second
        if base is None:
            raise UnsupportedMechanicError(
                "exact per-second movement was requested without a source value"
            )
        runtime = enemy.coefficient_runtime
        if runtime is None or runtime.identity_detection_phase != "revealed":
            return base
        additive_micros = coefficient_value(definition, "moveSpeed.move_speed")
        if additive_micros is None:
            return base
        if additive_micros < 0 or additive_micros % 1000:
            raise UnsupportedMechanicError(
                "Mainline 12 revealed speed is not exact milli-tiles per second"
            )
        return base + additive_micros // 1000

    def _lowest_hp_ally(
        self,
        position: GridPoint,
        range_tiles: int,
        *,
        allowed_positions: set[tuple[int, int]] | None = None,
    ) -> OperatorState | SummonState | None:
        candidates: list[OperatorState | SummonState] = [
            operator
            for operator in self.operators.values()
            if operator.hp < self._operator_defs[operator.operator_id].max_hp
            and (
                allowed_positions is None
                or (operator.position.col, operator.position.row) in allowed_positions
            )
            and abs(position.col - operator.position.col)
            + abs(position.row - operator.position.row)
            <= range_tiles
        ]
        candidates.extend(
            summon
            for summon in self.summons.values()
            if self._summon_defs[summon.summon_id].receives_external_healing
            and summon.hp < self._summon_defs[summon.summon_id].max_hp
            and (
                allowed_positions is None
                or (summon.position.col, summon.position.row) in allowed_positions
            )
            and abs(position.col - summon.position.col) + abs(position.row - summon.position.row)
            <= range_tiles
        )
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (
                item.hp * 1_000_000 // self._max_hp(item),
                item.hp,
                item.instance_id,
            ),
        )

    def _max_hp(self, friendly: OperatorState | SummonState) -> int:
        if isinstance(friendly, OperatorState):
            return self._operator_defs[friendly.operator_id].max_hp
        return self._summon_defs[friendly.summon_id].max_hp

    @staticmethod
    def _friendly_distance(
        first: OperatorState | SummonState,
        second: OperatorState | SummonState,
    ) -> int:
        return abs(first.position.col - second.position.col) + abs(
            first.position.row - second.position.row
        )

    def _enemy_is_immune(self, enemy: EnemyState, control: ControlEffect) -> bool:
        """Whether this enemy ships immunity to the named control.

        Read side rather than write side, so every application path is
        covered at once -- including a fixture that hand-fills an
        ActiveEffectState.  `BIND` maps to nothing: the shipped data has
        no bindImmune to honour.
        """

        field = CONTROL_IMMUNITY_FIELDS.get(control.value)
        if field is None:
            return False
        definition = self._enemy_defs[enemy.enemy_id]
        if getattr(definition.immunities, field):
            return True
        # 蒸汽战车 「生命值首次低于50%时……获得：晕眩免疫，冻结免疫，浮空免疫，
        # 沉睡免疫」 -- four immunities it does **not** have in its attribute
        # frame, granted only while the rage runs.  Silence, palsy and the rest
        # are deliberately not in the list: PRTS names exactly these four.
        predation = definition.predation
        runtime = enemy.coefficient_runtime
        if (
            predation is not None
            and predation.shell_counters_stun
            and runtime is not None
            and runtime.predation_shell
            and field == "stun"
        ):
            return True
        rage = definition.self_rage
        if (
            rage is not None
            and rage.grants_control_immunity
            and runtime is not None
            and runtime.self_rage_active
            and field in {"stun", "frozen", "levitate", "sleep"}
        ):
            return True
        return False

    def _has_control(self, target_instance_id: str, *controls: ControlEffect) -> bool:
        enemy = self.enemies.get(target_instance_id)
        if enemy is not None:
            # 「boss 被眩晕」 -- the error this family was registered
            # against.  Immune controls are dropped before any of them
            # is allowed to answer yes.
            controls = tuple(
                control
                for control in controls
                if not self._enemy_is_immune(enemy, control)
            )
            if not controls:
                return False
        if ControlEffect.STUN in controls:
            friendly = self.operators.get(target_instance_id) or self.summons.get(
                target_instance_id
            )
            if friendly is not None and friendly.coefficient_stun_remaining_ticks:
                return True
            enemy = self.enemies.get(target_instance_id)
            if enemy is not None and enemy.talent_stun_remaining_ticks:
                return True
        active_effects = getattr(self, "active_effects", {}).values()
        return any(
            self._effect_vm.has_control(
                control=control,
                target_instance_id=target_instance_id,
                active_effects=active_effects,
            )
            for control in controls
        )

    def _resolve_terminal(self) -> None:
        next_status = self.status
        if self.life_points <= 0:
            next_status = BattleStatus.DEFEAT
        elif self.spawned_spawn_ids == self._scheduled_ids and not self.enemies:
            next_status = BattleStatus.VICTORY
        if next_status is not self.status:
            self.status = next_status
            self._emit(
                EventPhase.TERMINAL,
                "terminal",
                "system",
                {"status": next_status.value},
                priority=10,
            )

    def _operator_target(self, operator: OperatorState) -> EnemyState | None:
        targets = self._operator_targets(operator)
        return targets[0] if targets else None

    def _operator_targets(self, operator: OperatorState) -> tuple[EnemyState, ...]:
        definition = self._operator_defs[operator.operator_id]
        attack_range_tiles = self._operator_effective_stat(operator, EffectStat.ATTACK_RANGE_TILES)
        candidates = [
            enemy
            for enemy in self.enemies.values()
            if self._operator_range_contains(
                operator,
                enemy,
                attack_range_tiles=attack_range_tiles,
            )
            and self._operator_can_target_enemy(operator, enemy)
        ]
        if not candidates:
            return ()
        if definition.normal_attack_target_priority == "highest_defense":
            ordered = sorted(
                candidates,
                key=lambda enemy: (
                    -self._enemy_effective_stat(enemy, EffectStat.DEFENSE),
                    self._enemy_objective_priority(enemy),
                ),
            )
        else:
            ordered = sorted(
                candidates,
                key=self._enemy_objective_priority,
            )
        if definition.normal_attack_max_targets <= 1:
            return (ordered[0],)
        blocked = set(operator.blocked_enemy_ids)
        blocked_targets = tuple(enemy for enemy in ordered if enemy.instance_id in blocked)
        if blocked_targets:
            return blocked_targets[: definition.normal_attack_max_targets]
        return (ordered[0],)

    def _enemy_objective_priority(self, enemy: EnemyState) -> tuple[int, int, str]:
        """Order targets by taunt, objective distance, then stable id."""

        path = self._enemy_route_points(enemy)
        remaining_segments = max(0, len(path) - 1 - enemy.waypoint_index)
        remaining_milli = remaining_segments * 1000 - enemy.segment_progress_milli
        definition = self._enemy_defs[enemy.enemy_id]
        taunt_level = 0
        if self._enemy_has_active_hologram(enemy):
            taunt_micros = coefficient_value(definition, "createGhost.taunt_level")
            if taunt_micros is None or taunt_micros % 1_000_000:
                raise UnsupportedMechanicError(
                    "Redmark hologram taunt level is not an exact whole tier"
                )
            taunt_level = taunt_micros // 1_000_000
        # A carrier may also declare a standing level of its own -- 家族恫吓者
        # 「能吸引敌人攻击自己」.  The higher of the two applies; tiers are
        # compared, not summed, so a temporary hologram tier does not stack on
        # top of a permanent one.
        declared_micros = coefficient_value(definition, "DeathRattle.taunt_level")
        if declared_micros is not None:
            if declared_micros % 1_000_000:
                raise UnsupportedMechanicError(
                    "declared taunt level is not an exact whole tier"
                )
            taunt_level = max(taunt_level, declared_micros // 1_000_000)
        return -taunt_level, remaining_milli, enemy.instance_id

    def _enemy_has_active_hologram(self, enemy: EnemyState) -> bool:
        if enemy.enemy_id not in GHOST_REDACE_IDS:
            return False
        return any(
            other.coefficient_runtime is not None
            and other.coefficient_runtime.ghost_owner_instance_id == enemy.instance_id
            for other in self.enemies.values()
        )

    def _operator_range_contains(
        self,
        operator: OperatorState,
        enemy: EnemyState,
        *,
        attack_range_tiles: int,
    ) -> bool:
        definition = self._operator_defs[operator.operator_id]
        active_skill_ranges = tuple(
            self._skill_defs[(operator.operator_id, state.skill_id)].attack_range_offsets
            for state in operator.skills
            if state.active_remaining > 0
            and self._skill_defs[(operator.operator_id, state.skill_id)].attack_range_offsets
        )
        if len(active_skill_ranges) > 1:
            raise EffectVMError("operator has multiple active exact skill ranges")
        offsets = active_skill_ranges[0] if active_skill_ranges else definition.attack_range_offsets
        if (
            not offsets
            and self.content.bundle_id == "level_rogue3_1-4"
            and operator.operator_id in _WAITING_FLOCK_OPERATOR_RANGE_OFFSETS
        ):
            offsets = _WAITING_FLOCK_OPERATOR_RANGE_OFFSETS[operator.operator_id]
        exact_offsets_apply = bool(active_skill_ranges) or (
            offsets and attack_range_tiles == definition.attack_range_tiles
        )
        if offsets and exact_offsets_apply:
            enemy_col, enemy_row = self._enemy_position_milli(enemy)
            for center_col, center_row in self._operator_covered_cells(operator, offsets):
                center_col *= 1000
                center_row *= 1000
                if abs(enemy_col - center_col) <= 500 and abs(enemy_row - center_row) <= 500:
                    return True
            return False
        return self._distance_milli(operator.position, enemy) <= attack_range_tiles * 1000

    @staticmethod
    def _operator_covered_cells(
        operator: OperatorState,
        offsets: tuple[tuple[int, int], ...],
    ) -> set[tuple[int, int]]:
        covered = set()
        for col, source_row in offsets:
            if operator.direction is Direction.RIGHT:
                delta_col, delta_row = col, -source_row
            elif operator.direction is Direction.DOWN:
                delta_col, delta_row = source_row, col
            elif operator.direction is Direction.LEFT:
                delta_col, delta_row = -col, source_row
            else:
                delta_col, delta_row = -source_row, -col
            covered.add(
                (
                    operator.position.col + delta_col,
                    operator.position.row + delta_row,
                )
            )
        return covered

    def _enemy_is_hidden(self, enemy: EnemyState) -> bool:
        """隐匿: 「无法被对立阵营的部分能力和部分弹道选中」.

        Blocking is what lifts it.  PRTS is explicit that this is not intrinsic
        to 迷彩 -- 「迷彩与"阻挡时解除"没有直接关系，该机制通常由其他效果实现」 --
        but every IS3 carrier that states its own rule states the same one, and
        流泪小子's is the plainest: 「隐匿（**解除阻挡0秒后恢复**）」.  Zero
        seconds, so a carrier is hidden again on the tick its blocker lets go.

        This engine has no 反隐 on the friendly side to model the other exit;
        the IS3 anti-stealth unit (技术侦察兵) works the other way round, on
        *our* units' stealth, which nothing here has either.
        """

        return (
            self._enemy_defs[enemy.enemy_id].stealthed and enemy.blocked_by is None
        )

    def _operator_can_target_enemy(self, operator: OperatorState, enemy: EnemyState) -> bool:
        if not self._mainline12_enemy_is_targetable(enemy):
            return False
        if self._enemy_is_hidden(enemy):
            return False
        return (
            enemy.route_teleport_destination_index is None
            and (
                self._enemy_defs[enemy.enemy_id].movement_layer is MovementLayer.GROUND
                or self._operator_defs[operator.operator_id].can_target_flying
            )
        )

    def _friendly_can_target_enemy(
        self,
        source: OperatorState | SummonState,
        enemy: EnemyState,
    ) -> bool:
        if not self._mainline12_enemy_is_targetable(enemy):
            return False
        if self._enemy_is_hidden(enemy):
            return False
        if isinstance(source, OperatorState):
            return self._operator_can_target_enemy(source, enemy)
        return (
            enemy.route_teleport_destination_index is None
            and (
                self._enemy_defs[enemy.enemy_id].movement_layer is MovementLayer.GROUND
                or self._summon_defs[source.summon_id].can_target_flying
            )
        )

    @staticmethod
    def _mainline12_enemy_is_targetable(enemy: EnemyState) -> bool:
        if enemy.enemy_id not in MAINLINE12_DISGUISED_IDS:
            return True
        runtime = enemy.coefficient_runtime
        if runtime is None or runtime.identity_detection_phase != "revealed":
            return False
        return not (runtime.revealed_invisible and enemy.blocked_by is None)

    @staticmethod
    def _mainline12_enemy_is_invincible(enemy: EnemyState) -> bool:
        if enemy.enemy_id not in MAINLINE12_DISGUISED_IDS:
            return False
        runtime = enemy.coefficient_runtime
        return runtime is None or runtime.identity_detection_phase != "revealed"

    def _tile_accepts_operator(self, tile: GridPoint, definition: OperatorDefinition) -> bool:
        ground = self._ground_tile_coords
        high = self._high_ground_tile_coords
        if not ground and not high:
            return True
        tile_key = (tile.col, tile.row)
        if definition.deployment_class is DeploymentClass.EITHER:
            return tile_key in ground or tile_key in high
        if definition.deployment_class is DeploymentClass.GROUND:
            return tile_key in ground
        return tile_key in high

    def _distance_milli(self, point: GridPoint, enemy: EnemyState) -> int:
        col, row = self._enemy_position_milli(enemy)
        return abs(point.col * 1000 - col) + abs(point.row * 1000 - row)

    def _enemy_range_contains(self, enemy: EnemyState, point: GridPoint) -> bool:
        definition = self._enemy_defs[enemy.enemy_id]
        col, row = self._enemy_position_milli(enemy)
        delta_col = point.col * 1000 - col
        delta_row = point.row * 1000 - row
        reach = definition.attack_range_milli_tiles
        conditional = definition.conditional_takeoff
        runtime = enemy.coefficient_runtime
        if (
            conditional is not None
            and runtime is not None
            and runtime.conditional_airborne
        ):
            # 「攻击范围扩大至基础半径的 179%」 while airborne.
            reach = reach * conditional.attack_range_multiplier_milli // 1000
        return delta_col * delta_col + delta_row * delta_row <= reach**2

    def _black_fog_sources_covering(self, point: GridPoint) -> tuple[EnemyState, ...]:
        """Return living Blade Point sources whose exact x-4 Kingdom covers a tile."""

        target_col = point.col * 1000
        target_row = point.row * 1000
        covered: list[EnemyState] = []
        for enemy in self.enemies.values():
            # The grid is per carrier, read from its own `BlackFog.range_id`:
            # b-1's blade projects `x-4` and b-1-b's 游荡者 projects the much
            # larger `x-3`.  An id allow-list held only the first of the two, so
            # the emergency boss had no 国度 at all.
            offsets = self._enemy_defs[enemy.enemy_id].black_fog_range_offsets
            if not offsets:
                continue
            if enemy.route_teleport_destination_index is not None:
                continue
            source_col, source_row = self._enemy_position_milli(enemy)
            if any(
                abs(target_col - (source_col + offset_col * 1000)) <= 500
                and abs(target_row - (source_row + offset_row * 1000)) <= 500
                for offset_col, offset_row in offsets
            ):
                covered.append(enemy)
        return tuple(sorted(covered, key=lambda item: item.instance_id))

    def _friendly_in_black_fog(self, point: GridPoint) -> bool:
        return bool(self._black_fog_sources_covering(point))

    def _black_fog_attack_speed(self, point: GridPoint) -> int | None:
        sources = self._black_fog_sources_covering(point)
        if not sources:
            return None
        values = {
            coefficient_value(self._enemy_defs[source.enemy_id], "BlackFog.attack_speed")
            for source in sources
        }
        if None in values or len(values) != 1:
            raise UnsupportedMechanicError(
                "Blade Point overlapping Kingdom attack-speed rows are not source-identical"
            )
        return cast(int, next(iter(values)))

    def _enemy_grid_point(self, enemy: EnemyState) -> GridPoint | None:
        if (
            enemy.segment_progress_milli != 0
            or enemy.route_teleport_destination_index is not None
        ):
            return None
        return self._enemy_route_points(enemy)[enemy.waypoint_index]

    def _enemy_facing_milli(self, enemy: EnemyState) -> tuple[int, int] | None:
        """Return the enemy's facing vector, or None when it is not moving.

        Facing is the direction of travel along its route: from the current
        waypoint toward the next.  At the end of the path there is no next
        waypoint and therefore no defined facing.
        """

        path = self._enemy_route_points(enemy)
        if enemy.waypoint_index >= len(path) - 1:
            return None
        current = path[enemy.waypoint_index]
        following = path[enemy.waypoint_index + 1]
        return (
            (following.col - current.col) * 1000,
            (following.row - current.row) * 1000,
        )

    def _rear_hit_multiplier_milli(
        self,
        enemy: EnemyState,
        definition: EnemyDefinition,
        source_ids: tuple[str, ...],
    ) -> int:
        """Damage multiplier for this packet given where the attackers stand.

        The resistance applies only when *every* contributing attacker stands on
        the protected side: a packet that includes one attacker on the weak side
        is not a protected hit, and picking one attacker arbitrarily would make
        the result depend on intent ordering.

        Which side is protected differs between carriers and is stated only in
        their descriptions -- 硬甲爪兽 protects its rear, 岩冠兽 its front -- so it
        is read from there at compile time and required here.
        """

        resistance = coefficient_value(definition, "Weakness.damage_resistance")
        if resistance is None:
            return 1000
        if not 0 <= resistance < 1_000_000:
            raise UnsupportedMechanicError(
                "rear-hit resistance must be a fraction below 1"
            )
        protected_side = definition.weakness_protected_side
        if protected_side is None:
            raise UnsupportedMechanicError(
                "directional damage resistance lost its protected side"
            )
        # 狡兽's 【弱点】 is not a standing trait: 「''持有<甲壳>''时：②获得
        # 【弱点】」.  Before it has eaten, the same carrier takes full damage
        # from every side.
        predation = definition.predation
        if predation is not None:
            runtime = enemy.coefficient_runtime
            if runtime is None or not runtime.predation_shell:
                return 1000
        facing = self._predation_facing_milli(enemy) or self._enemy_facing_milli(
            enemy
        )
        if facing is None:
            return 1000
        target_col, target_row = self._enemy_position_milli(enemy)
        on_protected_side = 0
        considered = 0
        for source_id in source_ids:
            actor = self.operators.get(source_id) or self.summons.get(source_id)
            if actor is None:
                continue
            considered += 1
            delta_col = actor.position.col * 1000 - target_col
            delta_row = actor.position.row * 1000 - target_row
            # Positive projection onto the facing vector means the attacker
            # stands ahead of the enemy; negative means behind it.
            ahead = delta_col * facing[0] + delta_row * facing[1] > 0
            if ahead == (protected_side == "front"):
                on_protected_side += 1
        if not considered or on_protected_side != considered:
            return 1000
        return 1000 - resistance // 1000

    def _enemy_route_points(self, enemy: EnemyState) -> tuple[GridPoint, ...]:
        return enemy.path_override or self._route_runtime_points[enemy.route_id]

    def _enemy_position_milli(self, enemy: EnemyState) -> tuple[int, int]:
        path = self._enemy_route_points(enemy)
        current = path[enemy.waypoint_index]
        if enemy.waypoint_index >= len(path) - 1:
            return current.col * 1000, current.row * 1000
        following = path[enemy.waypoint_index + 1]
        progress = enemy.segment_progress_milli
        return (
            current.col * 1000 + (following.col - current.col) * progress,
            current.row * 1000 + (following.row - current.row) * progress,
        )

    def _emit(
        self,
        phase: EventPhase,
        kind: str,
        actor_id: str,
        details: dict[str, object],
        *,
        priority: int = 0,
        tick: int | None = None,
    ) -> None:
        self.event_sequence += 1
        event = BattleEvent(
            tick=self.tick if tick is None else tick,
            phase=phase,
            priority=priority,
            actor_id=actor_id,
            sequence=self.event_sequence,
            kind=kind,
            details=details,
        )
        if self._event_observer is not None:
            self._event_observer(event)
        self._retain_history_row(
            self._events,
            event,
            self._replay_retention.max_events,
        )
        self._max_resident_events = max(self._max_resident_events, len(self._events))

    def _make_snapshot(self, parent_hash: str | None) -> BattleSnapshot:
        body = {
            "engine_version": ENGINE_VERSION,
            "content_hash": self.content.content_hash,
            "parent_snapshot_hash": parent_hash,
            "tick": self.tick,
            "cost": self.cost,
            "max_cost_bonus": self.max_cost_bonus,
            "initial_deployment_points_bonus": self.initial_deployment_points_bonus,
            "cost_recovery_slowdown_debt_milli": (
                self.cost_recovery_slowdown_debt_milli
            ),
            "battle_progress_tick": self.battle_progress_tick,
            "battle_progress_slowdown_debt_milli": (
                self.battle_progress_slowdown_debt_milli
            ),
            "spawn_progress_tick": self.spawn_progress_tick,
            "spawn_progress_slowdown_debt_milli": (
                self.spawn_progress_slowdown_debt_milli
            ),
            "life_points": self.life_points,
            "kills": self.kills,
            "leaks": self.leaks,
            "status": self.status,
            "spawned_spawn_ids": tuple(sorted(self.spawned_spawn_ids)),
            "activated_token_aliases": tuple(sorted(self.activated_token_aliases)),
            "pending_spawn_ids": tuple(
                item.occurrence_id
                for item in self._scheduled
                if item.occurrence_id not in self.spawned_spawn_ids
            ),
            "spawn_cursor": len(self.spawned_spawn_ids),
            "operators": tuple(sorted(self.operators.values(), key=lambda item: item.instance_id)),
            "enemies": tuple(sorted(self.enemies.values(), key=lambda item: item.instance_id)),
            "summons": tuple(sorted(self.summons.values(), key=lambda item: item.instance_id)),
            "projectiles": tuple(
                sorted(self.projectiles.values(), key=lambda item: item.instance_id)
            ),
            "healing_devices": tuple(
                sorted(self.healing_devices.values(), key=lambda item: item.instance_id)
            ),
            "pending_explosions": tuple(
                sorted(
                    self.pending_explosions.values(), key=lambda item: item.instance_id
                )
            ),
            "polluted_zones": tuple(
                sorted(self.polluted_zones.values(), key=lambda item: item.instance_id)
            ),
            "active_effects": tuple(
                sorted(self.active_effects.values(), key=lambda item: item.instance_id)
            ),
            "roster": tuple(sorted(self.roster.values(), key=lambda item: item.operator_id)),
            "entity_sequence": self.entity_sequence,
            "effect_sequence": self.effect_sequence,
            "event_sequence": self.event_sequence,
            "command_sequence": self.command_sequence,
            "rng_seed": self.rng_seed,
            "rng_streams": tuple(
                RNGStreamState(stream=name, draws=draws)
                for name, draws in sorted(self.rng_streams.items())
            ),
            "paused": self.paused,
            "speed": self.speed,
            "global_modifiers": self.global_modifiers,
            "tick_digest_count": self.tick_digest_count,
            "last_tick_digest_hash": self.last_tick_digest_hash,
        }
        unhashed = BattleSnapshot(**body, snapshot_hash="0" * 64)
        return unhashed.model_copy(update={"snapshot_hash": unhashed.computed_hash()})

    def _append_command(
        self,
        action: PublicAction,
        before: BattleSnapshot,
        event_count_before: int,
    ) -> None:
        previous = self._last_command_record_hash
        body = {
            "sequence": self.command_sequence,
            "tick_before": before.tick,
            "action": action.model_dump(mode="json"),
            "snapshot_hash_before": before.snapshot_hash,
            "snapshot_hash_after": self.snapshot.snapshot_hash,
            "event_count_before": event_count_before,
            "event_count_after": self.event_sequence,
            "previous_record_hash": previous,
        }
        record = CommandRecord(**body, record_hash=canonical_sha256(body))
        if self._command_observer is not None:
            self._command_observer(record)
        self._retain_history_row(
            self._commands,
            record,
            self._replay_retention.max_commands,
        )
        self._max_resident_commands = max(self._max_resident_commands, len(self._commands))
        self._last_command_record_hash = record.record_hash

    @staticmethod
    def _retain_history_row(
        rows: list[BattleEvent] | list[TickStateDigest] | list[CommandRecord],
        row: BattleEvent | TickStateDigest | CommandRecord,
        limit: int | None,
    ) -> None:
        rows.append(row)  # type: ignore[arg-type]
        if limit is not None and len(rows) > limit:
            del rows[: len(rows) - limit]

    @staticmethod
    def _initial_from_snapshot(snapshot: BattleSnapshot) -> InitialState:
        return InitialState(
            content_hash=snapshot.content_hash,
            tick=snapshot.tick,
            cost=snapshot.cost,
            max_cost_bonus=snapshot.max_cost_bonus,
            initial_deployment_points_bonus=snapshot.initial_deployment_points_bonus,
            cost_recovery_slowdown_debt_milli=(
                snapshot.cost_recovery_slowdown_debt_milli
            ),
            battle_progress_slowdown_debt_milli=(
                snapshot.battle_progress_slowdown_debt_milli
            ),
            spawn_progress_slowdown_debt_milli=(
                snapshot.spawn_progress_slowdown_debt_milli
            ),
            life_points=snapshot.life_points,
            kills=snapshot.kills,
            leaks=snapshot.leaks,
            status=snapshot.status,
            spawned_spawn_ids=snapshot.spawned_spawn_ids,
            activated_token_aliases=snapshot.activated_token_aliases,
            operators=snapshot.operators,
            enemies=snapshot.enemies,
            summons=snapshot.summons,
            projectiles=snapshot.projectiles,
            healing_devices=snapshot.healing_devices,
            polluted_zones=snapshot.polluted_zones,
            pending_explosions=snapshot.pending_explosions,
            active_effects=snapshot.active_effects,
            roster=snapshot.roster,
            entity_sequence=snapshot.entity_sequence,
            effect_sequence=snapshot.effect_sequence,
            event_sequence=snapshot.event_sequence,
            command_sequence=snapshot.command_sequence,
            rng_seed=snapshot.rng_seed,
            rng_streams=snapshot.rng_streams,
            paused=snapshot.paused,
            speed=snapshot.speed,
            global_modifiers=snapshot.global_modifiers,
            tick_digest_count=snapshot.tick_digest_count,
            last_tick_digest_hash=snapshot.last_tick_digest_hash,
            parent_snapshot_hash=snapshot.parent_snapshot_hash,
        )


def verify_replay(content: ContentBundle, replay: BattleReplay) -> ReplayVerification:
    issues: list[str] = []
    body = replay.model_dump(mode="json", exclude={"replay_hash"})
    if replay.replay_hash != canonical_sha256(body):
        issues.append("replay hash mismatch")
    if replay.content_hash != content.content_hash:
        issues.append("content hash mismatch")
        return ReplayVerification(valid=False, issues=tuple(issues))
    try:
        engine = BattleEngine(
            content,
            replay.initial_state,
            source_wave_package_hash=replay.source_wave_package_hash,
            source_wave_scenario_id=replay.source_wave_scenario_id,
            source_wave_scenario_assignments=replay.source_wave_scenario_assignments,
        )
        for record in replay.commands:
            engine.step(record.action)
        reproduced = engine.export_replay()
    except Exception as exc:  # fail closed on malformed or illegal command streams
        issues.append(f"replay execution failed: {exc}")
        return ReplayVerification(valid=False, issues=tuple(issues))

    if reproduced.commands != replay.commands:
        issues.append("command chain mismatch")
    if reproduced.events != replay.events:
        issues.append("event stream mismatch")
    if reproduced.tick_digests != replay.tick_digests:
        issues.append("tick digest chain mismatch")
    if reproduced.final_snapshot_hash != replay.final_snapshot_hash:
        issues.append("final snapshot hash mismatch")
    return ReplayVerification(
        valid=not issues,
        issues=tuple(issues),
        final_snapshot_hash=reproduced.final_snapshot_hash,
    )


__all__ = [
    "BattleEngine",
    "IncompleteReplayHistoryError",
    "IllegalActionError",
    "InvalidInitialStateError",
    "MAX_FORECAST_HORIZON_TICKS",
    "ObservationAccessError",
    "SUPPORTED_MECHANISMS",
    "TournamentSimError",
    "UnsupportedMechanicError",
    "verify_replay",
]
