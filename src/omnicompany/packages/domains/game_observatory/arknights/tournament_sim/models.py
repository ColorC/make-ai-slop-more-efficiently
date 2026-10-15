# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-10T02:35:03+08:00 type=battle-simulator-contracts status=active
"""Versioned contracts for the Arknights tournament headless simulator.

The contracts deliberately contain only numeric battle state.  They do not load
client assets, arbitrary scripts, account data, or emulator internals.  Unknown
mechanics are represented explicitly instead of being silently approximated.
"""

from __future__ import annotations

from functools import cached_property
from typing import Annotated, Any, Literal, Union

from pydantic import Field, StrictInt, model_validator

from .model_primitives import (
    CHECKPOINT_SCHEMA,
    CONTENT_SCHEMA,
    ENGINE_VERSION,
    INITIAL_STATE_SCHEMA,
    REPLAY_SCHEMA,
    SNAPSHOT_SCHEMA,
    TICK_DIGEST_SCHEMA,
    BattleStatus,
    ControlEffect,
    DeploymentClass,
    Direction,
    EffectRangeOrigin,
    EffectRangeRotation,
    EffectStat,
    EffectTargetSelector,
    EventPhase,
    FidelityLevel,
    MovementLayer,
    SkillActivationPolicy,
    SkillSpEvent,
    SkillSpRecoveryMode,
    VisibilityClass,
    _Contract,
    canonical_sha256,
)

# Re-exported so every existing ``from ...models import X`` keeps working.
from .model_primitives import MAX_EFFECT_RANGE_OFFSET, GridPoint  # noqa: F401
from .effect_definitions import (  # noqa: F401
    EffectDefinition,
    HealEffectDefinition,
    ScaledHealEffectDefinition,
    DamageEffectDefinition,
    AttackScaleEffectDefinition,
    RandomDamageEffectDefinition,
    StatModifierEffectDefinition,
    SpMutationEffectDefinition,
    DeploymentPointsEffectDefinition,
    SummonEffectDefinition,
)
from .special_tile_definitions import (  # noqa: F401
    SpecialTileStatModifierDefinition,
    SpecialTileControlEffectDefinition,
    SpecialTileEffectDefinition,
    SpecialTileDefinition,
)
from .skill_definitions import (  # noqa: F401
    SkillDefinition,
)
from .battle_actions import (  # noqa: F401
    DeployAction,
    RetreatAction,
    ActivateSkillAction,
    SelectTargetAction,
    SetPausedAction,
    SetSpeedAction,
    WaitAction,
    PublicAction,
)
from .replay_retention import (  # noqa: F401
    ReplayRetentionPolicy,
    ReplayRetentionState,
)
from .enemy_immunities import (  # noqa: F401
    CONTROL_IMMUNITY_FIELDS,
    EnemyImmunities,
    enemy_immunities,
)
from .enemy_skills import (  # noqa: F401
    ChargedEnemySkill,
    ConditionalTakeOff,
    DevourSkill,
    EnergyChargeSkill,
    BackstabSkill,
    BlinkSkill,
    ChainBuff,
    DoomSkill,
    Immolation,
    BeamConduction,
    BombRun,
    CrossroadsVolley,
    BeamSplash,
    BeamWeakness,
    BornBugs,
    FlameSkill,
    RepairSkill,
    Reborning,
    BloodHit,
    FourDirectionVolley,
    IceBomb,
    SpawnDecay,
    EnvironmentalFragile,
    GuidedStrike,
    OverloadSkill,
    TacticalCommand,
    Predation,
    SelfRage,
    TakeOffSkill,
    VictorySelfBuff,
)
from .pending_explosions import (  # noqa: F401
    PendingExplosionState,
)
from .polluted_zone import (  # noqa: F401
    PollutedZoneState,
)
from .operator_definitions import OperatorDefinition  # noqa: F401
from .entity_definitions import (  # noqa: F401
    BranchSpawnDefinition,
    BloodDebtLedger,
    SummonDefinition,
    BranchSpawnDefinition,
    InitialSummonDefinition,
    PredefinedTokenDefinition,
    HealingDeviceDefinition,
)
from .runtime_states import (  # noqa: F401
    EnemyCoefficientRuntimeState,
    ProjectileState,
    SkillState,
    ActiveEffectState,
    HealingDeviceState,
    RNGStreamState,
    SummonState,
)




class MapDefinition(_Contract):
    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    deployable_tiles: tuple[GridPoint, ...] = ()
    ground_tiles: tuple[GridPoint, ...] = ()
    high_ground_tiles: tuple[GridPoint, ...] = ()
    walkable_tiles: tuple[GridPoint, ...] = ()

    @model_validator(mode="after")
    def validate_tiles(self) -> "MapDefinition":
        seen: set[tuple[int, int]] = set()
        for tile in self.deployable_tiles:
            key = (tile.col, tile.row)
            if tile.col >= self.width or tile.row >= self.height:
                raise ValueError(f"deployable tile is outside map: {key}")
            if key in seen:
                raise ValueError(f"duplicate deployable tile: {key}")
            seen.add(key)
        ground = {(tile.col, tile.row) for tile in self.ground_tiles}
        high = {(tile.col, tile.row) for tile in self.high_ground_tiles}
        if len(ground) != len(self.ground_tiles) or len(high) != len(self.high_ground_tiles):
            raise ValueError("typed deployment tiles must be unique")
        if ground & high:
            raise ValueError("a deployment tile cannot be both ground and high ground")
        if (ground or high) and ground | high != seen:
            raise ValueError("typed deployment tiles must partition deployable_tiles")
        walkable = {(tile.col, tile.row) for tile in self.walkable_tiles}
        if len(walkable) != len(self.walkable_tiles):
            raise ValueError("walkable tiles must be unique")
        for key in walkable:
            if key[0] >= self.width or key[1] >= self.height:
                raise ValueError(f"walkable tile is outside map: {key}")
        return self


class SourceRouteCheckpoint(_Contract):
    """Lossless source checkpoint metadata alongside the compatibility path.

    Kinds 5/6 are the source ``DISAPPEAR`` / ``APPEAR_AT_POS`` pair.  Their
    positions are deliberately not treated symmetrically: type 5 enters from
    the current route point (the source position is a sentinel in shipped
    stages), while type 6 supplies the exit point.
    """

    source_index: int = Field(ge=0)
    checkpoint_type: int = Field(ge=0)
    time_ticks: int = Field(default=0, ge=0)
    position: GridPoint | None = None
    reach_offset_milli: tuple[int, int] = (0, 0)
    randomize_reach_offset: bool = False
    reach_distance_milli: int = Field(default=0, ge=0)


class RouteDefinition(_Contract):
    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    route_id: str = Field(min_length=1)
    waypoints: tuple[GridPoint, ...] = Field(min_length=1)
    waypoint_wait_ticks: tuple[int, ...] = ()
    movement_layer: MovementLayer = MovementLayer.GROUND
    source_route_index: int | None = Field(default=None, ge=0)
    source_checkpoints: tuple[SourceRouteCheckpoint, ...] = ()
    compatibility_path_approximates_teleports: bool = False
    source_non_runtime_route: bool = False

    def _runtime_projection(
        self,
    ) -> tuple[
        tuple[int, ...],
        tuple[int, ...],
        tuple[tuple[int, int, int, int, int], ...],
    ]:
        """Project the compatibility walk to source-exact runtime transitions.

        Returns compatibility waypoint indices, visible waits aligned to that
        projected path, and teleport tuples of
        ``(source_type5, source_type6, entrance, destination, transit_wait)``.
        Intermediate Manhattan cells inserted by old content translators are
        excluded only between a paired type-5/type-6 transition.
        """

        if not self.source_checkpoints:
            waits = self.waypoint_wait_ticks or (0,) * len(self.waypoints)
            return tuple(range(len(self.waypoints))), waits, ()

        compatibility = self.waypoints
        runtime_indices = [0]
        visible_waits = [0]
        compatibility_cursor = 0
        pending: tuple[int, int, int] | None = None
        teleports: list[tuple[int, int, int, int, int]] = []

        def locate(position: GridPoint, *, checkpoint_index: int) -> int:
            nonlocal compatibility_cursor
            if compatibility[compatibility_cursor] == position:
                return compatibility_cursor
            for index in range(compatibility_cursor + 1, len(compatibility)):
                if compatibility[index] == position:
                    return index
            raise ValueError(
                f"route {self.route_id!r} source checkpoint {checkpoint_index} "
                "is absent from its compatibility walk"
            )

        for checkpoint in self.source_checkpoints:
            kind = checkpoint.checkpoint_type
            if kind == 0:
                if pending is not None:
                    raise ValueError(
                        f"route {self.route_id!r} moves before its type-6 teleport exit"
                    )
                if checkpoint.position is None:
                    raise ValueError(
                        f"route {self.route_id!r} move checkpoint lacks a position"
                    )
                target = locate(
                    checkpoint.position,
                    checkpoint_index=checkpoint.source_index,
                )
                for index in range(compatibility_cursor + 1, target + 1):
                    runtime_indices.append(index)
                    visible_waits.append(0)
                compatibility_cursor = target
                continue
            if kind == 1:
                if pending is None:
                    visible_waits[-1] += checkpoint.time_ticks
                else:
                    source_index, entrance, transit_wait = pending
                    pending = (source_index, entrance, transit_wait + checkpoint.time_ticks)
                continue
            if kind == 5:
                if pending is not None:
                    raise ValueError(
                        f"route {self.route_id!r} has nested type-5 teleport entries"
                    )
                pending = (checkpoint.source_index, len(runtime_indices) - 1, 0)
                continue
            if kind == 6:
                if pending is None:
                    raise ValueError(
                        f"route {self.route_id!r} type-6 teleport exit lacks type-5 entry"
                    )
                if checkpoint.position is None:
                    raise ValueError(
                        f"route {self.route_id!r} type-6 teleport exit lacks a position"
                    )
                target = locate(
                    checkpoint.position,
                    checkpoint_index=checkpoint.source_index,
                )
                source_index, entrance, transit_wait = pending
                runtime_indices.append(target)
                visible_waits.append(0)
                teleports.append(
                    (
                        source_index,
                        checkpoint.source_index,
                        entrance,
                        len(runtime_indices) - 1,
                        transit_wait,
                    )
                )
                compatibility_cursor = target
                pending = None
                continue
            if self.compatibility_path_approximates_teleports:
                raise ValueError(
                    f"route {self.route_id!r} teleport stream contains unsupported "
                    f"checkpoint type {kind}"
                )

        if pending is not None:
            raise ValueError(f"route {self.route_id!r} has an unmatched type-5 teleport entry")
        for index in range(compatibility_cursor + 1, len(compatibility)):
            runtime_indices.append(index)
            visible_waits.append(0)
        return tuple(runtime_indices), tuple(visible_waits), tuple(teleports)

    @property
    def runtime_waypoints(self) -> tuple[GridPoint, ...]:
        indices, _waits, _teleports = self._runtime_projection()
        return tuple(self.waypoints[index] for index in indices)

    @property
    def runtime_waypoint_wait_ticks(self) -> tuple[int, ...]:
        _indices, waits, _teleports = self._runtime_projection()
        return waits

    @property
    def runtime_teleports(self) -> tuple[tuple[int, int, int, int, int], ...]:
        _indices, _waits, teleports = self._runtime_projection()
        return teleports

    @model_validator(mode="after")
    def route_is_contiguous(self) -> "RouteDefinition":
        if len(self.waypoints) < 2 and not self.source_non_runtime_route:
            raise ValueError("runtime routes require at least two waypoints")
        if self.waypoint_wait_ticks and len(self.waypoint_wait_ticks) != len(self.waypoints):
            raise ValueError("route waypoint_wait_ticks must match waypoints")
        if any(value < 0 for value in self.waypoint_wait_ticks):
            raise ValueError("route waypoint waits cannot be negative")
        for before, after in zip(self.waypoints, self.waypoints[1:]):
            distance = abs(before.col - after.col) + abs(before.row - after.row)
            if distance != 1:
                raise ValueError(
                    f"route {self.route_id!r} has non-contiguous waypoints: {before} -> {after}"
                )
        if not self.source_checkpoints:
            if self.compatibility_path_approximates_teleports:
                raise ValueError("teleport approximation lacks source checkpoints")
            return self
        waypoint_keys = tuple((item.col, item.row) for item in self.waypoints)
        projected = [0] * len(waypoint_keys)
        cursor = 0
        for checkpoint in self.source_checkpoints:
            if checkpoint.checkpoint_type in {0, 6} and checkpoint.position is not None:
                position_key = (checkpoint.position.col, checkpoint.position.row)
                try:
                    cursor = waypoint_keys.index(position_key, cursor)
                except ValueError:
                    continue
            elif checkpoint.checkpoint_type == 1 and checkpoint.time_ticks > 0:
                projected[cursor] += checkpoint.time_ticks
        source_has_teleports = any(
            checkpoint.checkpoint_type in {5, 6} for checkpoint in self.source_checkpoints
        )
        if source_has_teleports != self.compatibility_path_approximates_teleports:
            raise ValueError("teleport approximation flag conflicts with source checkpoints")
        # Validate pair ordering and ensure every source anchor is traceable in
        # the compatibility walk even though the engine will not traverse the
        # translator-invented cells between type 5 and type 6.
        self._runtime_projection()
        if not any(projected):
            return self
        declared = self.waypoint_wait_ticks
        if declared and any(declared) and declared != tuple(projected):
            raise ValueError("runtime waypoint waits conflict with source checkpoint waits")
        return self.model_copy(update={"waypoint_wait_ticks": tuple(projected)})


class SpawnDefinition(_Contract):
    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    spawn_id: str = Field(min_length=1)
    tick: int = Field(ge=1)
    enemy_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    count: int = Field(default=1, gt=0)
    interval_ticks: int = Field(default=0, ge=0)
    source_wave_index: int | None = Field(default=None, ge=0)
    source_fragment_index: int | None = Field(default=None, ge=0)
    source_action_index: int | None = Field(default=None, ge=0)
    wave_pre_delay_ticks: int = Field(default=0, ge=0)
    fragment_pre_delay_ticks: int = Field(default=0, ge=0)
    action_pre_delay_ticks: int = Field(default=0, ge=0)
    random_spawn_group_key: str | None = None
    random_spawn_group_pack_key: str | None = None
    hidden_group: str | None = None
    selection_probability_micros: int = Field(default=1_000_000, ge=0, le=1_000_000)
    not_count_in_total: bool = False


class SourceWaveActionDefinition(_Contract):
    source_index: int = Field(ge=0)
    action_type: int
    key: str | None = None
    route_index: int | None = None
    count: int = Field(default=0, ge=0)
    pre_delay_ticks: int = Field(default=0, ge=0)
    interval_ticks: int = Field(default=0, ge=0)
    weight_milli: int = Field(default=0, ge=0)
    random_spawn_group_key: str | None = None
    random_spawn_group_pack_key: str | None = None
    hidden_group: str | None = None
    not_count_in_total: bool = False
    source_json: str = Field(min_length=2)


class SourceWaveFragmentDefinition(_Contract):
    source_index: int = Field(ge=0)
    pre_delay_ticks: int = Field(default=0, ge=0)
    actions: tuple[SourceWaveActionDefinition, ...]


class SourceWaveDefinition(_Contract):
    source_index: int = Field(ge=0)
    pre_delay_ticks: int = Field(default=0, ge=0)
    post_delay_ticks: int = Field(default=0, ge=0)
    max_time_waiting_for_next_wave_ticks: int = Field(default=0, ge=0)
    fragments: tuple[SourceWaveFragmentDefinition, ...]


class SourceWaveScenarioAssignment(_Contract):
    group_kind: Literal["random_spawn_group", "random_spawn_group_pack", "hidden_group"]
    group_key: str = Field(min_length=1)
    source_wave_index: int | None = Field(default=None, ge=0)
    spawn_id: str | None = Field(default=None, min_length=1)
    spawn_ids: tuple[str, ...] = ()
    source_action_ids: tuple[str, ...] = ()
    enabled: bool | None = None

    @model_validator(mode="after")
    def selection_is_explicit(self) -> "SourceWaveScenarioAssignment":
        if self.spawn_ids != tuple(sorted(self.spawn_ids)):
            raise ValueError("source wave scenario spawn_ids must be sorted")
        if len(self.spawn_ids) != len(set(self.spawn_ids)):
            raise ValueError("source wave scenario spawn_ids must be unique")
        if self.source_action_ids != tuple(sorted(self.source_action_ids)):
            raise ValueError("source wave scenario source_action_ids must be sorted")
        if len(self.source_action_ids) != len(set(self.source_action_ids)):
            raise ValueError("source wave scenario source_action_ids must be unique")
        selection_forms = sum(
            (
                self.spawn_id is not None,
                bool(self.spawn_ids),
                bool(self.source_action_ids),
                self.enabled is not None,
            )
        )
        if selection_forms != 1:
            raise ValueError("source wave scenario assignment requires exactly one selection form")
        if self.enabled is True and self.group_kind != "hidden_group":
            raise ValueError("only hidden groups accept enabled=true")
        return self


class SourceWaveScheduleSnapshot(_Contract):
    content_version: str = Field(min_length=1)
    source_package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenario_id: str = Field(min_length=1)
    scenario_assignments: tuple[SourceWaveScenarioAssignment, ...]
    tick: int = Field(ge=0)
    wave_index: int = Field(ge=0)
    fragment_index: int = Field(ge=0)
    action_index: int = Field(ge=0)
    next_occurrence_tick: int | None = Field(default=None, ge=1)
    emitted_spawn_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def identities_are_unique(self) -> "SourceWaveScheduleSnapshot":
        groups = [
            (
                assignment.source_wave_index,
                assignment.group_kind,
                assignment.group_key,
            )
            for assignment in self.scenario_assignments
        ]
        if len(groups) != len(set(groups)):
            raise ValueError("source wave scenario assignment groups must be unique")
        if len(self.emitted_spawn_ids) != len(set(self.emitted_spawn_ids)):
            raise ValueError("source wave emitted spawn ids must be unique")
        return self


class SourceWaveScheduleEvent(_Contract):
    sequence: int = Field(gt=0)
    tick: int = Field(ge=0)
    kind: Literal[
        "wave_schedule_cursor_advanced",
        "source_action_scheduled",
        "runtime_spawn_scheduled",
        "scenario_binding_required",
        "wave_schedule_snapshot_loaded",
    ]
    wave_index: int = Field(ge=0)
    fragment_index: int = Field(ge=0)
    action_index: int = Field(ge=0)
    route_id: str | None = Field(default=None, min_length=1)
    enemy_id: str | None = Field(default=None, min_length=1)
    spawn_id: str | None = Field(default=None, min_length=1)
    cursor_before: tuple[int, int, int]
    cursor_after: tuple[int, int, int]
    details: dict[str, Any] = Field(default_factory=dict)


class SourceWaveScheduleAdvance(_Contract):
    snapshot: SourceWaveScheduleSnapshot
    events: tuple[SourceWaveScheduleEvent, ...]


class EnemyMechanicCoefficient(_Contract):
    """Source-exact numeric metadata for a closed enemy mechanic.

    Values use millionths so content packages remain deterministic JSON and do
    not introduce a float-expression surface.  Presence alone is never runtime
    authorization: the engine separately validates an implemented key family,
    its source identity, and its trigger semantics.
    """

    key: str = Field(min_length=1)
    value_micros: int
    source_pointer: str = Field(min_length=1)
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    interpretation: str = Field(min_length=1)
    pending_live_readback: bool = True


class EnemyTimedSelfModifierDefinition(_Contract):
    """One finite stat modifier created atomically when its enemy spawns."""

    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    modifier_id: str = Field(min_length=1)
    stat: EffectStat
    additive: int = Field(default=0, strict=True)
    multiplier_milli: int = Field(default=1000, ge=0, le=10000, strict=True)
    duration_ticks: int = Field(gt=0, strict=True)
    stack_key: str = Field(min_length=1)

    @model_validator(mode="after")
    def modifier_is_closed(self) -> "EnemyTimedSelfModifierDefinition":
        if self.stat not in {
            EffectStat.ATTACK,
            EffectStat.DEFENSE,
            EffectStat.MAGIC_RESISTANCE,
            EffectStat.ATTACK_INTERVAL_TICKS,
            EffectStat.MOVE_MILLI_TILES_PER_TICK,
        }:
            raise ValueError("selected stat is invalid for an enemy self modifier")
        if self.additive == 0 and self.multiplier_milli == 1000:
            raise ValueError("enemy self modifier must change the selected stat")
        return self


def enemy_timed_modifier_effect_id(enemy_id: str, modifier_id: str) -> str:
    """Return the deterministic VM definition id for an enemy-local modifier."""

    return f"enemy_self_modifier:{enemy_id}:{modifier_id}"


class EnemyDefinition(_Contract):
    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    enemy_id: str = Field(min_length=1)
    max_hp: int = Field(gt=0)
    attack: int = Field(ge=0)
    defense: int = Field(ge=0)
    magic_resistance: int = Field(default=0, ge=0, le=100)
    refracting_magic_resistance_bonus: int = Field(default=0, ge=0, le=100)
    # 「攻击被冻结的单位时攻击力大幅度提高」 resolved at compile time.  Two key
    # spellings carry this: the canonical `damagescaletofrozen.atk_scale`
    # (雪牝/霜牡, 2.5) and `atkup.atk_scale` on 雪怪小队凿冰人/破冰者 (2.5/3.0)
    # -- the *same* 2.5 on the base variant, which is what corroborates the
    # reading.  `atkup.atk_scale` also carries a different mechanism on
    # 狂徒 (「优先摧毁一切可见的设施」), so the two are told apart by the
    # description rather than by the key.
    frozen_target_attack_scale_micros: int = Field(default=0, ge=0)
    # Which side of this enemy resists damage, when it resists one at all.
    # `Weakness.damage_resistance` gives the magnitude but not the side, and the
    # side genuinely differs between carriers: 硬甲爪兽 is 「弱点部位位于正面，
    # 背面所受伤害大幅降低」 while 岩冠兽 is 「弱点部位位于背面，正面所受伤害大幅
    # 降低」.  Assuming one side inverts the mechanic for the other half of them.
    weakness_protected_side: Literal["front", "rear"] | None = None
    # The enemy this one becomes when it dies.  `DeathRattle.enemy_key` names it
    # as a **string**, so it cannot ride a numeric coefficient and arrives as a
    # typed field instead: 术师快艇 「载具损毁后术师会继续作战」 names
    # `enemy_1161_tidmag`, and the stage ships that definition alongside it.
    death_transform_enemy_id: str | None = Field(default=None, min_length=1)
    # 「红标」渗透者's 「首次受伤时制造一个全息幻影」: the phantom's id, shipped as
    # a string under `createGhost.enemy_key`.
    hologram_enemy_id: str | None = Field(default=None, min_length=1)
    # 「邪魔的利刃」's 【国度】: the tiles it projects around itself, from the
    # shipped `BlackFog.range_id`.  b-1 ships `x-4` (3x3) and b-1-b's 游荡者
    # ships `x-3` (a 25-tile diamond) -- 「自身中心**大范围**生成」.
    black_fog_range_offsets: tuple[tuple[int, int], ...] = ()
    # 「未阻挡时远程攻击」: a melee-mode enemy that shoots while nothing holds it.
    ranged_while_unblocked: bool = False
    # 家族专用轿车 「被击倒时若已被阻挡，将卸下三个家族士兵」: the unit it unloads
    # and how many.  `DeathRattle.attack@cnt` carries only the count, so the
    # id is resolved at compile time from the name in the description --
    # 「家族士兵」 matches exactly one enemy (`enemy_1277_sgman`).
    death_unload_enemy_id: str | None = Field(default=None, min_length=1)
    death_unload_count: int = Field(default=0, ge=0, le=8)
    # Whether this enemy grants energy to its own side.  Both energy carriers
    # hold the same `over_energy` rows, so only the description distinguishes
    # the charger (「充能」) from the thief (「夺取」).
    grants_energy: bool = False
    # 匪帮射手's charging schedule, when the skill ships one.  `grants_energy`
    # stays the description-derived role flag; this is the cadence, and without
    # it the engine charged continuously.
    energy_charge: "EnergyChargeSkill | None" = None
    # 弧光突击队的助跑起飞: a takeoff no timer fires, so it cannot share
    # 喷射人's `takeoff` field -- that one has a duration and a recharge and
    # this one has neither.
    conditional_takeoff: "ConditionalTakeOff | None" = None
    # R-31 重型动力装甲的【超负载】: a ten-second window that rewrites the
    # attack cadence, which no other skill contract here does.
    overload: "OverloadSkill | None" = None
    # 萨卡兹枯朽吞噬者的吞噬: the only mechanic here that takes one of ours
    # off the board and gives it back when the carrier dies.
    devour: "DevourSkill | None" = None
    # Which element this enemy inflicts.  The ratio keys say how much of a
    # hit converts; only the description says into what, so the stage compiler
    # reads it from there.
    element_damage_type: Literal["neural", "erosion", "burn"] | None = None
    # 不法分子 / 不法分子头目: 「未被阮挡时有几率闪避物理攻击」, `Evade.prob`
    # 0.4.  Probability in milli; the stream is per carrier so two of them on
    # the board do not share a roll sequence.
    # 逐腐兽: 「受到自身普通攻击的目标在10秒内每秒受到100点无来源法术
    # 普通伤害(该伤害无法致死)，重复触发刷新持续时间；目标受到治疗
    # 效果后该效果立刻解除」 (PRTS 「逐腐兽」).  Damage per second and the
    # window it runs for; the cadence is the bundle clock.
    bleed_on_attack_damage: int = Field(default=0, ge=0)
    bleed_on_attack_duration_ticks: int = Field(default=0, ge=0)
    unblocked_physical_evade_probability_milli: int = Field(default=0, ge=0, le=1000)
    unblocked_physical_evade_rng_stream: str | None = Field(default=None, min_length=1)
    # A skill this enemy charges off its own normal attacks (or, when
    # spCost is zero, off a timer).  Separate from the coefficients
    # because those fire on every attack and this must not.
    charged_skill: ChargedEnemySkill | None = None
    # 决胜: the one-shot permanent buff this carrier gains once its
    # partner has left the board.  Separate from `charged_skill`
    # because it rides no attack.
    victory_buff: VictorySelfBuff | None = None
    # 「生命值降至一半时」/「首次受到伤害后」 self-buff.  Separate from
    # `victory_buff` (a partner's exit) and from the `triggerrage` pair, all
    # three of which are one-shot buffs with different triggers.
    self_rage: "SelfRage | None" = None
    # 狡兽's 【形态差分】: the prey it eats and what the shell then gives it.
    predation: "Predation | None" = None
    # 【战术命令】: what this carrier gives the whole enemy side, or what it
    # gains while under it.  One key family, two opposite roles.
    tactical_command: "TacticalCommand | None" = None
    # 「受到环境伤害时，获得10s 100%的脆弱」 -- the vines' own frailty.
    environmental_fragile: "EnvironmentalFragile | None" = None
    # 山海众秘使's 【强击标记】: what its next attack is worth once it has been
    # hidden.  Zero for every carrier that does not ship `InvisibleCombat`.
    stealth_strike_scale_micros: int = Field(default=0, ge=0)
    # 山海众司魅人's 【引导】: the timed lock-and-blow, plus the speed it gains.
    guided_strike: "GuidedStrike | None" = None
    # 梦魇效仿者's 【恐惧凝集】: a board-wide sweep of every operator whose back
    # tile nobody is covering.
    backstab: "BackstabSkill | None" = None
    # 饥饿啮齿兽's 【啮咬】: an operator-only bleed that is HP loss rather than
    # damage, and refreshes rather than stacks.
    blood_hit: "BloodHit | None" = None
    # 残党正式萨克斯手's 【全力吹奏】: four rays, each stopping at the first
    # friendly it touches.
    four_direction_volley: "FourDirectionVolley | None" = None
    # 虚幻's 【轰炸】: the single bomb, its nine tiles and its ten seconds of cold.
    ice_bomb: "IceBomb | None" = None
    # 净浊之焰's ten seconds: the attack fades to 30% and then it kills itself.
    spawn_decay: "SpawnDecay | None" = None
    # 路加萨尔古斯's 【瞬身】: blocked, it steps 1.5 tiles down its own route and
    # bills the blocker half a second later.
    blink: "BlinkSkill | None" = None
    # 雪祀's 【反自然馈赠】: it shoots its own side, and they come out faster.
    chain_buff: "ChainBuff | None" = None
    # 「萨米的意志」's 【叹息】: ten minutes in, everything takes 150% arts, the
    # defender loses thirty life points, and the boss leaves.
    doom: "DoomSkill | None" = None
    # 无垠回荡克雷松's 【重生】: the first kill buys a five-second pause and a
    # boss that comes back whole and 50% stronger.
    reborning: "Reborning | None" = None
    # 游击队萨卡兹术师's 【献祭】: a periodic arts aura the shrine's pulse widens
    # from 1.5 tiles to 2.5 instead of hurting it.
    immolation: "Immolation | None" = None
    # 莱茵生命工程科成员's 【修复】: undoes the defender's damage on 4-2's five
    # dormant power-armour tokens, 5% of their maximum a second.
    repair: "RepairSkill | None" = None
    # 深池焚毁者's 【焚毁】: one operator held in a jet of fire, 12% arts and 6%
    # 灼燃 every half second for ten seconds.
    flame: "FlameSkill | None" = None
    # 枯朽萃聚使徒's 【枯朽召唤】: three seeds every ten seconds, on its own route.
    born_bugs: "BornBugs | None" = None
    # 枯朽之种: 「普通攻击结束时，若自身未持有晕眩/冻结/浮空/沉睡，强制击杀自身」.
    # Being controlled is what keeps it alive, which is the opposite of the
    # usual reading of a control effect.
    dies_after_attacking: bool = False
    # 改进型量产巨像: 「处于重整束流上时，防御和法术抗性大幅下降」.
    beam_weakness: "BeamWeakness | None" = None
    # 历战高塔术师: 「攻击击中**未处于重整束流上**的目标时，额外对目标周围8格内的
    # 其他所有我方单位造成攻击力100%的法术伤害」.
    beam_splash: "BeamSplash | None" = None
    # 残党首席小提琴家: 「优先攻击位于重整束流上的目标。攻击命中重整束流上的目标后，
    # 分裂出按正/反方向沿重整束流移动的弹道」.
    beam_conduction: "BeamConduction | None" = None
    # 「十字路口」量产型: 「每秒执行两项操作：①向四个正方向各发射弹道…②对0.7半径
    # 范围内造成攻击力100%的近战法术伤害」, and no ordinary attack at all.
    crossroads_volley: "CrossroadsVolley | None" = None
    # 暴鸰: one 3x3 bomb the moment anything is in reach, then 200% move speed
    # and nothing left to give.
    bomb_run: "BombRun | None" = None
    # `Driver.sp`: 「被未激活的<R系列动力装甲>击杀时，为其回复50/100SP」 -- what
    # this unit is worth to the armour that eats it.
    armour_charge_sp: int = Field(default=0, ge=0)
    # 大奖赛运输艇's `Dead.value` -2: what the battle loses when this enemy is
    # **killed**, as opposed to `life_damage`, which is what it costs when one
    # walks off the far end.  The transport ship is the inversion the tournament
    # has to notice: `lifePointReduce` 0 means letting it through is free, and
    # PRTS states the other half -- 「死亡时扣除2目标生命值」.
    death_life_point_cost: int = Field(default=0, ge=0)
    # 喷气人 「被阻挡后会飞起来一段时间」: flies out of a block on a timer.
    takeoff: TakeOffSkill | None = None
    # Shipped control immunities.  Defaults to none immune, so a
    # hand-written fixture keeps its old behaviour.
    # 源石祭坛's 脉冲波 and the two enemies that welcome it.  PRTS 「游击队萨卡兹
    # 战士」/「游击队萨卡兹术师」 天赋: 「**免疫脉冲波**，受源石祭坛的技能【脉冲波】
    # 影响后可进入**强化模式**」, and the shipped description says the same in one
    # line -- 「接触脉冲波时，不受伤害且攻击切换成法术伤害」.  Immunity and
    # empowerment are two fields because a carrier can have the first without a
    # representable second: 术师's 强化模式 is 「攻击范围扩大」 with no figure
    # anywhere, so it is immune and its empowerment is registered, not guessed.
    # 隐匿: 「无法被对立阵营的部分能力和部分弹道选中」.  Read from the
    # enemy's own description, or granted by a stage rune -- either way it is a
    # property of the definition rather than a coefficient, because nothing
    # numeric ships with it.
    stealthed: bool = False
    pulse_immune: bool = False
    pulse_empowered_damage_type: Literal["arts"] | None = None
    immunities: EnemyImmunities = Field(default_factory=EnemyImmunities)
    formation_defense_aura: int = Field(default=0, ge=0, le=10000)
    formation_aura_radius_milli_tiles: int = Field(default=0, ge=0, le=50000)
    on_attack_block_count_delta: int = Field(default=0, le=0)
    on_attack_block_count_duration_ticks: int = Field(default=0, ge=0)
    minimum_block_count_to_stop: int = Field(default=1, ge=1, le=16)
    attack_mode: Literal["none", "melee", "ranged"] = "melee"
    damage_type: Literal["physical", "arts"] = "physical"
    attack_range_milli_tiles: int = Field(default=0, ge=0, le=50000)
    attack_interval_ticks: int = Field(gt=0)
    move_milli_tiles_per_tick: int = Field(gt=0, le=1000)
    move_milli_tiles_per_second: int | None = Field(default=None, gt=0, le=30000)
    life_damage: int = Field(default=1, ge=0)
    movement_layer: MovementLayer = MovementLayer.GROUND
    mechanic_coefficients: tuple[EnemyMechanicCoefficient, ...] = ()
    initial_timed_modifiers: tuple[EnemyTimedSelfModifierDefinition, ...] = ()

    @model_validator(mode="after")
    def mechanic_coefficient_keys_are_unique(self) -> "EnemyDefinition":
        keys = [coefficient.key for coefficient in self.mechanic_coefficients]
        if len(keys) != len(set(keys)):
            raise ValueError("enemy mechanic coefficient keys must be unique")
        block_coefficients = [
            coefficient
            for coefficient in self.mechanic_coefficients
            if coefficient.key == "minimum_block_count_to_stop"
        ]
        if block_coefficients:
            coefficient = block_coefficients[0]
            if coefficient.value_micros % 1_000_000:
                raise ValueError("minimum block count must use exact integer millionths")
            normalized = coefficient.value_micros // 1_000_000
            if not 1 <= normalized <= 16:
                raise ValueError("minimum block count must normalize to 1..16")
            if normalized != self.minimum_block_count_to_stop:
                raise ValueError("minimum block count coefficient disagrees with normalized field")
        # Legacy packages before v17 carried the normalized typed field without
        # the source coefficient row.  Runtime authorization still happens in
        # BattleEngine; model parsing must remain backward compatible.
        modifier_ids = [modifier.modifier_id for modifier in self.initial_timed_modifiers]
        if len(modifier_ids) != len(set(modifier_ids)):
            raise ValueError("enemy timed modifier ids must be unique")
        stack_keys = [modifier.stack_key for modifier in self.initial_timed_modifiers]
        if len(stack_keys) != len(set(stack_keys)):
            raise ValueError("enemy timed modifier stack keys must be unique")
        return self












































class MechanismFidelity(_Contract):
    mechanism: str = Field(min_length=1)
    level: FidelityLevel
    note: str = Field(min_length=1)


class GlobalModifierState(_Contract):
    """Closed fixed-point global corrections for judge-constructed states."""

    friendly_damage_multiplier_milli: int = Field(default=1000, ge=0, le=10000)
    enemy_damage_multiplier_milli: int = Field(default=1000, ge=0, le=10000)
    healing_multiplier_milli: int = Field(default=1000, ge=0, le=10000)
    enemy_movement_multiplier_milli: int = Field(default=1000, ge=1, le=10000)
    cost_recovery_interval_multiplier_milli: int = Field(default=1000, ge=1, le=10000)


CORE_MECHANISMS: tuple[str, ...] = (
    "map",
    "route",
    "spawn",
    "movement",
    "cost",
    "deployment",
    "retreat",
    "blocking",
    "basic_physical_attack",
    "damage",
    "death",
    "terminal",
)

DEFAULT_MECHANISM_FIDELITY: tuple[MechanismFidelity, ...] = tuple(
    MechanismFidelity(
        mechanism=name,
        level=FidelityLevel.C,
        note="deterministic prototype; not yet calibrated tick-by-tick against the client",
    )
    for name in CORE_MECHANISMS
)

DEFAULT_UNSUPPORTED_MECHANISMS: tuple[str, ...] = ()


class ContentBundle(_Contract):
    """A self-contained, versioned, numeric rules/content bundle."""

    schema_id: Literal[CONTENT_SCHEMA] = CONTENT_SCHEMA
    bundle_id: str = Field(min_length=1)
    client_version: str = Field(min_length=1)
    source: str = Field(min_length=1)
    generator_version: str = Field(
        default="arknights-tournament-sim/manual-v1",
        min_length=1,
    )
    content_version: str | None = Field(default=None, min_length=1)
    stage_id: str | None = Field(default=None, min_length=1)
    source_package_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    visibility: VisibilityClass = VisibilityClass.BASE_PUBLIC
    map: MapDefinition
    routes: tuple[RouteDefinition, ...]
    spawns: tuple[SpawnDefinition, ...] = ()
    source_waves: tuple[SourceWaveDefinition, ...] = ()
    enemies: tuple[EnemyDefinition, ...]
    operators: tuple[OperatorDefinition, ...]
    healing_devices: tuple[HealingDeviceDefinition, ...] = ()
    special_tiles: tuple[SpecialTileDefinition, ...] = ()
    summons: tuple[SummonDefinition, ...] = ()
    initial_summons: tuple[InitialSummonDefinition, ...] = ()
    # Devices and friendly NPCs placed on the map, each entering play
    # when its wave action fires.  Separate from `initial_summons`
    # because the tick matters: most shipped tokens are `hidden` and
    # arrive mid-battle.
    predefined_tokens: tuple[PredefinedTokenDefinition, ...] = ()
    # Event branches, keyed by the id the devices name.  A branch fires when
    # its device is destroyed, so these are not part of the wave schedule.
    branch_spawns: tuple[BranchSpawnDefinition, ...] = ()
    ticks_per_second: int = Field(default=30, gt=0)
    initial_cost: int = Field(default=0, ge=0)
    max_cost: int = Field(default=99, ge=0)
    cost_recovery_interval_ticks: int = Field(default=30, gt=0)
    initial_life_points: int = Field(default=1, gt=0)
    initial_global_modifiers: GlobalModifierState = GlobalModifierState()
    required_mechanisms: tuple[str, ...] = CORE_MECHANISMS
    mechanism_fidelity: tuple[MechanismFidelity, ...] = DEFAULT_MECHANISM_FIDELITY
    unsupported_mechanisms: tuple[str, ...] = DEFAULT_UNSUPPORTED_MECHANISMS

    @model_validator(mode="after")
    def references_are_closed(self) -> "ContentBundle":
        if self.initial_cost > self.max_cost:
            raise ValueError("initial_cost cannot exceed max_cost")
        visibility_rank = {
            VisibilityClass.BASE_PUBLIC: 0,
            VisibilityClass.IS_RESTRICTED: 1,
            VisibilityClass.JUDGE_PRIVATE: 2,
        }
        nested_visibility = [
            self.map.visibility,
            *(item.visibility for item in self.routes),
            *(item.visibility for item in self.spawns),
            *(item.visibility for item in self.enemies),
            *(
                modifier.visibility
                for enemy in self.enemies
                for modifier in enemy.initial_timed_modifiers
            ),
            *(item.visibility for item in self.operators),
            *(item.visibility for item in self.healing_devices),
            *(item.visibility for item in self.special_tiles),
            *(item.visibility for item in self.summons),
            *(skill.visibility for operator in self.operators for skill in operator.skills),
            *(skill.visibility for summon in self.summons for skill in summon.skills),
            *(
                effect.visibility
                for operator in self.operators
                for skill in operator.skills
                for effect in skill.effects
            ),
            *(
                effect.visibility
                for summon in self.summons
                for skill in summon.skills
                for effect in skill.effects
            ),
            *(effect.visibility for tile in self.special_tiles for effect in tile.effects),
        ]
        if any(
            visibility_rank[item] > visibility_rank[self.visibility] for item in nested_visibility
        ):
            raise ValueError("nested data visibility exceeds its content bundle visibility")

        routes = {route.route_id: route for route in self.routes}
        if len(routes) != len(self.routes):
            raise ValueError("route_id values must be unique")
        enemies = {enemy.enemy_id: enemy for enemy in self.enemies}
        if len(enemies) != len(self.enemies):
            raise ValueError("enemy_id values must be unique")
        operators = {operator.operator_id: operator for operator in self.operators}
        if len(operators) != len(self.operators):
            raise ValueError("operator_id values must be unique")
        devices = {device.device_id: device for device in self.healing_devices}
        if len(devices) != len(self.healing_devices):
            raise ValueError("device_id values must be unique")
        summons = {summon.summon_id: summon for summon in self.summons}
        if len(summons) != len(self.summons):
            raise ValueError("summon_id values must be unique")
        # Bounds, not walkability: 163 of the 532 shipped IS3 token instances
        # sit on non-walkable tiles (walls and floors), so requiring walkable
        # ground would discard a third of the real data.  Whether a device
        # blocks anything is a property of the tile, not of being placed.
        token_aliases = [token.alias for token in self.predefined_tokens]
        if len(token_aliases) != len(set(token_aliases)):
            raise ValueError("predefined token aliases must be unique")
        summon_ids = {summon.summon_id for summon in self.summons}
        for token in self.predefined_tokens:
            if token.summon_id not in summon_ids:
                raise ValueError(
                    "predefined token %s names an undeclared summon"
                    % token.alias
                )
            if not (
                0 <= token.position.col < self.map.width
                and 0 <= token.position.row < self.map.height
            ):
                raise ValueError(
                    "predefined token %s stands off the map" % token.alias
                )
        initial_summon_ids = {summon.instance_id for summon in self.initial_summons}
        if len(initial_summon_ids) != len(self.initial_summons):
            raise ValueError("initial summon instance ids must be unique")
        initial_summon_positions: set[GridPoint] = set()
        for summon in self.initial_summons:
            if summon.summon_id not in summons:
                raise ValueError(
                    f"initial summon references unknown definition: {summon.summon_id}"
                )
            if summon.position.col >= self.map.width or summon.position.row >= self.map.height:
                raise ValueError("initial summon is outside map")
            if summon.position in initial_summon_positions:
                raise ValueError("initial summons cannot occupy the same tile")
            initial_summon_positions.add(summon.position)
        special_tile_ids = {tile.tile_id for tile in self.special_tiles}
        if len(special_tile_ids) != len(self.special_tiles):
            raise ValueError("special tile ids must be unique")
        spawn_ids = {spawn.spawn_id for spawn in self.spawns}
        if len(spawn_ids) != len(self.spawns):
            raise ValueError("spawn_id values must be unique")

        for route in self.routes:
            for waypoint in route.waypoints:
                if waypoint.col >= self.map.width or waypoint.row >= self.map.height:
                    raise ValueError(
                        f"route {route.route_id!r} leaves the map at "
                        f"({waypoint.col}, {waypoint.row})"
                    )
                if (
                    not route.source_non_runtime_route
                    and route.movement_layer is MovementLayer.GROUND
                    and self.map.walkable_tiles
                    and waypoint not in self.map.walkable_tiles
                ):
                    raise ValueError(f"ground route {route.route_id!r} uses a non-walkable tile")
        for tile in self.special_tiles:
            if tile.position.col >= self.map.width or tile.position.row >= self.map.height:
                raise ValueError(f"special tile is outside map: {tile.tile_id}")
        for spawn in self.spawns:
            if spawn.enemy_id not in enemies:
                raise ValueError(f"spawn references unknown enemy: {spawn.enemy_id}")
            if spawn.route_id not in routes:
                raise ValueError(f"spawn references unknown route: {spawn.route_id}")
            if (
                spawn.enemy_id in enemies
                and spawn.route_id in routes
                and enemies[spawn.enemy_id].movement_layer
                is not routes[spawn.route_id].movement_layer
            ):
                raise ValueError("spawn enemy and route movement layers must match")

        declarations = {item.mechanism for item in self.mechanism_fidelity}
        if len(declarations) != len(self.mechanism_fidelity):
            raise ValueError("mechanism fidelity declarations must be unique")
        missing = set(self.required_mechanisms) - declarations
        if missing:
            raise ValueError(f"required mechanisms lack fidelity declarations: {sorted(missing)}")
        overlap = set(self.required_mechanisms) & set(self.unsupported_mechanisms)
        if overlap:
            raise ValueError(f"required mechanisms are declared unsupported: {sorted(overlap)}")

        all_effect_ids: list[str] = []
        has_stat_modifiers = False
        has_skills = False
        has_heal_effects = False
        has_rng_effects = any(
            operator.normal_attack_random_scale_probability_milli
            or operator.normal_attack_aftershock_proc_probability_milli
            or operator.incoming_damage_resistance_probability_milli
            for operator in self.operators
        )
        has_operator_damage_resistance_talents = any(
            operator.incoming_damage_resistance_probability_milli
            for operator in self.operators
        )
        has_operator_random_attack_talents = any(
            operator.normal_attack_random_scale_probability_milli
            or operator.normal_attack_aftershock_proc_probability_milli
            for operator in self.operators
        )
        has_summon_effects = False
        has_skill_sp_activation = False
        has_deployment_points = any(
            operator.initial_deployment_points_if_rostered for operator in self.operators
        )
        has_discrete_effect_geometry = False
        has_enemy_timed_self_modifiers = any(
            enemy.initial_timed_modifiers for enemy in self.enemies
        )
        skill_owners = (*self.operators, *self.summons)
        has_summon_skills = any(summon.skills for summon in self.summons)
        for owner in skill_owners:
            has_skills = has_skills or bool(owner.skills)
            for skill in owner.skills:
                for effect in skill.effects:
                    all_effect_ids.append(effect.effect_id)
                    has_stat_modifiers = has_stat_modifiers or isinstance(
                        effect, StatModifierEffectDefinition
                    )
                    has_heal_effects = has_heal_effects or isinstance(
                        effect, (HealEffectDefinition, ScaledHealEffectDefinition)
                    )
                    has_rng_effects = has_rng_effects or isinstance(
                        effect, RandomDamageEffectDefinition
                    )
                    has_skill_sp_activation = has_skill_sp_activation or isinstance(
                        effect, SpMutationEffectDefinition
                    )
                    has_deployment_points = has_deployment_points or isinstance(
                        effect, DeploymentPointsEffectDefinition
                    )
                    if (
                        isinstance(
                            effect,
                            (
                                HealEffectDefinition,
                                ScaledHealEffectDefinition,
                                DamageEffectDefinition,
                                StatModifierEffectDefinition,
                            ),
                        )
                        and effect.range_offsets
                    ):
                        has_discrete_effect_geometry = True
                        if (
                            isinstance(owner, SummonDefinition)
                            and effect.range_rotation is EffectRangeRotation.SOURCE_FACING
                        ):
                            raise ValueError(
                                "summon effect geometry cannot rotate by source facing"
                            )
                    if isinstance(effect, SummonEffectDefinition):
                        has_summon_effects = True
                        if effect.summon_id not in summons:
                            raise ValueError(
                                f"summon effect references unknown summon: {effect.summon_id}"
                            )
                        if (
                            effect.position.col >= self.map.width
                            or effect.position.row >= self.map.height
                        ):
                            raise ValueError("summon effect position is outside map")
                        if summons[effect.summon_id].blocks_routes:
                            endpoints = {
                                (route.waypoints[0].col, route.waypoints[0].row)
                                for route in self.routes
                                if route.movement_layer is MovementLayer.GROUND
                            } | {
                                (route.waypoints[-1].col, route.waypoints[-1].row)
                                for route in self.routes
                                if route.movement_layer is MovementLayer.GROUND
                            }
                            if (effect.position.col, effect.position.row) in endpoints:
                                raise ValueError(
                                    "route obstacle cannot occupy a ground route endpoint"
                                )
                    if isinstance(effect, SpMutationEffectDefinition):
                        if effect.target == "self" and effect.target_skill_id not in {
                            candidate.skill_id for candidate in owner.skills
                        }:
                            raise ValueError("self SP mutation references an unknown target skill")
            has_skill_sp_activation = has_skill_sp_activation or any(
                skill.sp_recovery_mode is not SkillSpRecoveryMode.TIME
                or skill.activation_policy is not SkillActivationPolicy.MANUAL
                or skill.max_charges != 1
                or skill.consume_charges != 1
                for skill in owner.skills
            )
        for operator in self.operators:
            for effect in operator.normal_attack_effects:
                all_effect_ids.append(effect.effect_id)
                has_stat_modifiers = True
                if effect.range_offsets:
                    raise ValueError("normal attack effects cannot declare skill effect geometry")
        for tile in self.special_tiles:
            all_effect_ids.extend(effect.effect_id for effect in tile.effects)
        all_effect_ids.extend(
            enemy_timed_modifier_effect_id(enemy.enemy_id, modifier.modifier_id)
            for enemy in self.enemies
            for modifier in enemy.initial_timed_modifiers
        )
        if len(all_effect_ids) != len(set(all_effect_ids)):
            raise ValueError("effect_id values must be globally unique within a bundle")
        required = set(self.required_mechanisms)
        has_route_teleports = any(route.runtime_teleports for route in self.routes)
        if has_route_teleports != ("route_teleport_metadata" in required):
            raise ValueError(
                "source teleport routes and route_teleport_metadata must be declared together"
            )
        if (
            self.initial_global_modifiers != GlobalModifierState()
            and "global_modifiers" not in required
        ):
            raise ValueError("non-default global modifiers must require global_modifiers")
        has_flying = any(
            item.movement_layer is MovementLayer.FLYING for item in self.enemies
        ) or any(item.movement_layer is MovementLayer.FLYING for item in self.routes)
        if has_flying and "flying" not in required:
            raise ValueError("flying enemies or routes must require the flying mechanism")
        has_typed_deployment = bool(self.map.ground_tiles or self.map.high_ground_tiles) or any(
            item.deployment_class is not DeploymentClass.EITHER for item in self.operators
        )
        if has_typed_deployment and "deployment_classes" not in required:
            raise ValueError(
                "typed tiles or operators must require the deployment_classes mechanism"
            )
        if has_skills and "skills" not in required:
            raise ValueError("bundles containing skills must require the skills mechanism")
        if any(op.incoming_attack_retaliation_scale_milli for op in self.operators) and "incoming_attack_retaliation" not in required:
            raise ValueError("incoming attack retaliation must require incoming_attack_retaliation")
        if has_summon_skills and "summon_skills" not in required:
            raise ValueError("summon skills must require summon_skills")
        if has_stat_modifiers and "buffs_debuffs" not in required:
            raise ValueError(
                "bundles containing stat modifiers must require the buffs_debuffs mechanism"
            )
        if has_heal_effects and "healing" not in required:
            raise ValueError("bundles containing heal effects must require healing")
        if has_rng_effects and "rng_effects" not in required:
            raise ValueError("bundles containing random effects must require rng_effects")
        if (
            has_operator_damage_resistance_talents
            and "operator_damage_resistance_talent" not in required
        ):
            raise ValueError(
                "damage resistance talents must require operator_damage_resistance_talent"
            )
        if (
            has_operator_random_attack_talents
            and "operator_random_attack_talent" not in required
        ):
            raise ValueError(
                "random normal attack talents must require operator_random_attack_talent"
            )
        if has_summon_effects and "summons" not in required:
            raise ValueError("bundles containing summon effects must require summons")
        if has_skill_sp_activation and "skill_sp_activation" not in required:
            raise ValueError("event SP or non-legacy activation must require skill_sp_activation")
        if has_deployment_points and "deployment_points" not in required:
            raise ValueError(
                "deployment-point effects or roster bonuses must require deployment_points"
            )
        if has_discrete_effect_geometry and "discrete_effect_geometry" not in required:
            raise ValueError("discrete effect ranges must require discrete_effect_geometry")
        if self.source_waves and "source_wave_schedule" not in required:
            raise ValueError("source waves must require source_wave_schedule")
        if has_enemy_timed_self_modifiers and "enemy_timed_self_modifier" not in required:
            raise ValueError("enemy initial timed modifiers must require enemy_timed_self_modifier")
        if (
            any(enemy.mechanic_coefficients for enemy in self.enemies)
            and "enemy_block_constraint_coefficients" not in required
        ):
            raise ValueError(
                "enemy mechanic coefficients must require enemy_block_constraint_coefficients"
            )
        if any(item.healing_power for item in self.summons) and "healing" not in required:
            raise ValueError("healing summons must require healing")
        if any(item.blocks_routes for item in self.summons) and "dynamic_obstacles" not in required:
            raise ValueError("route-blocking summons must require dynamic_obstacles")
        if any(item.blocks_routes for item in self.summons) and not self.map.walkable_tiles:
            raise ValueError("dynamic obstacles require an explicit walkable tile graph")
        if self.initial_summons and "battle_start_friendlies" not in required:
            raise ValueError("initial summons must require the battle_start_friendlies mechanism")
        skill_projectiles = any(
            isinstance(effect, AttackScaleEffectDefinition)
            and effect.projectile_travel_ticks is not None
            for operator in self.operators
            for skill in operator.skills
            for effect in skill.effects
        )
        if (
            any(item.projectile_travel_ticks for item in self.operators)
            or skill_projectiles
        ) and "projectiles" not in required:
            raise ValueError("delayed operator attacks must require projectiles")
        if (
            any(item.projectile_splash_radius_tiles for item in self.operators)
            and "area_projectiles" not in required
        ):
            raise ValueError("splash attacks must require area_projectiles")
        if (
            any(item.normal_attack_aftershock_count for item in self.operators)
            and "bombarder_aftershock" not in required
        ):
            raise ValueError("normal attack aftershocks must require bombarder_aftershock")
        if self.special_tiles and "special_tiles" not in required:
            raise ValueError("bundles containing special tiles must require special_tiles")
        if (
            any(tile.effects for tile in self.special_tiles)
            and "special_tile_stat_or_control_effects" not in required
        ):
            raise ValueError(
                "special tile stat or control effects must require "
                "special_tile_stat_or_control_effects"
            )
        if any(tile.healing for tile in self.special_tiles) and "healing" not in required:
            raise ValueError("healing special tiles must require healing")
        if self.healing_devices and not {"devices", "healing"} <= required:
            raise ValueError("bundles containing healing devices must require devices and healing")
        if any(operator.healing_power for operator in self.operators) and "healing" not in required:
            raise ValueError("healing operators must require the healing mechanism")
        return self

    @cached_property
    def content_hash(self) -> str:
        # 内容模型是 frozen 的，这个哈希是常量；但引擎每个 tick 都会读它一次
        # （`_append_tick_digest` 把它写进 tick digest）。实测不缓存时，
        # 整场战斗 87% 的时间花在重算这个常量上：model_dump → JSON → sha256，
        # 单场 2.98s → 0.42s（7.0x），且 tick digest 与 replay hash 逐字节不变。
        return canonical_sha256(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def death_transform_successors_travel_with_their_carriers(self) -> "ContentBundle":
        """A carrier that dies into another enemy must ship that enemy too.

        ``DeathRattle.enemy_key`` names the successor as a string, so it rides a
        typed field rather than a coefficient -- which means it cannot be
        checked by the coefficient gate, and a bundle with no coefficients at
        all is exactly the shape a transform-only carrier has.  Checking it here
        makes the rule hold for every bundle: a named-but-absent successor would
        leave the carrier dying into nothing while looking implemented.
        """

        known = {enemy.enemy_id for enemy in self.enemies}
        for enemy in self.enemies:
            passenger = enemy.death_unload_enemy_id
            if passenger is not None:
                if passenger == enemy.enemy_id:
                    raise ValueError(
                        f"death unload on {enemy.enemy_id} names itself"
                    )
                if passenger not in known:
                    raise ValueError(
                        f"death unload on {enemy.enemy_id} names {passenger}, "
                        "which this bundle does not carry"
                    )
            if bool(enemy.death_unload_count) != (passenger is not None):
                raise ValueError(
                    f"death unload on {enemy.enemy_id} needs both a passenger "
                    "and a count; one alone would look implemented and unload "
                    "nothing"
                )
            successor = enemy.death_transform_enemy_id
            if successor is None:
                continue
            if successor == enemy.enemy_id:
                raise ValueError(
                    f"death transform on {enemy.enemy_id} names itself, "
                    "which would never end"
                )
            if successor not in known:
                raise ValueError(
                    f"death transform names {successor!r}, which this bundle "
                    f"does not define; {enemy.enemy_id} would die into nothing"
                )
        return self


class RosterState(_Contract):
    operator_id: str = Field(min_length=1)
    redeploy_remaining: int = Field(default=0, ge=0)
    deploy_count: int = Field(default=0, ge=0)


class OperatorState(_Contract):
    instance_id: str = Field(min_length=1)
    operator_id: str = Field(min_length=1)
    position: GridPoint
    direction: Direction = Direction.RIGHT
    hp: int = Field(ge=0)
    #: 上一击没扣满一点血的那部分, 千分位。伤害的精确值带小数(atk 816 打 def
    #: 800 是 **40.8** 不是 40), 血量是整数, 于是余数必须存下来——否则每击下取整
    #: 会系统性少算, M 线实测约 2%(白板 §2.9)。与 `segment_progress_milli` 同
    #: 一个写法: 精确量在累加器里, 读出来的那个量取整。
    damage_remainder_milli: int = Field(default=0, ge=0, lt=1000)
    deployment_tick: int = Field(default=0, ge=0)
    attack_cooldown: int = Field(default=0, ge=0)
    heal_cooldown: int = Field(default=0, ge=0)
    blocked_enemy_ids: tuple[str, ...] = ()
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
    # 特工 「攻击时会短暂减少我方单位的阻挡上限」: the current reduction and how
    # long it lasts.  A cap below what the unit already holds is what lets an
    # enemy 「冲破我方防线」.
    block_reduction: int = Field(default=0, ge=0)
    block_reduction_remaining_ticks: int = Field(default=0, ge=0)
    death_buff_remaining_ticks: int = Field(default=0, ge=0)
    death_buff_attack_speed_points_micros: int = Field(default=0, ge=0)
    death_buff_defense_multiplier_milli: int = Field(default=1000, ge=1000)
    coefficient_bleeding_remaining_ticks: int = Field(default=0, ge=0)
    coefficient_bleeding_pulse_remaining_ticks: int = Field(default=0, ge=0, le=30)
    coefficient_bleeding_damage: int = Field(default=0, ge=0)
    #: Whether the bleed running on this operator is **HP loss** rather than the
    #: 「无来源法术普通伤害」 逐腐兽 inflicts.  饥饿啮齿兽's 【流血】 states no type
    #: and no non-lethal clause, so it lands as shipped and can finish an
    #: operator off; 逐腐兽's cannot, and says so.
    coefficient_bleeding_is_hp_loss: bool = False

    @model_validator(mode="after")
    def coefficient_bleeding_state_is_closed(self) -> "OperatorState":
        active = self.coefficient_bleeding_remaining_ticks > 0
        if active != bool(
            self.coefficient_bleeding_pulse_remaining_ticks
            and self.coefficient_bleeding_damage
        ):
            raise ValueError("operator bleeding timer and payload are inconsistent")
        return self


class EnemyBlockConstraintState(_Contract):
    content_version: str = Field(min_length=1)
    source_package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    stage_id: str = Field(min_length=1)
    spawn_id: str = Field(min_length=1)
    enemy_instance_id: str = Field(min_length=1)
    enemy_id: str = Field(min_length=1)
    coefficient_key: Literal["minimum_block_count_to_stop"]
    value_micros: StrictInt
    required_block_count: int = Field(ge=1, le=16, strict=True)
    tick: int = Field(ge=0)
    position: GridPoint
    route_id: str = Field(min_length=1)
    waypoint_index: int = Field(ge=0)
    blocker_instance_ids: tuple[str, ...] = ()
    blocker_count: int = Field(ge=0)
    constraint_state: Literal["unsatisfied", "satisfied"]
    source_pointer: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def state_is_closed(self) -> "EnemyBlockConstraintState":
        if self.value_micros != self.required_block_count * 1_000_000:
            raise ValueError("block constraint value is not exact integer millionths")
        if self.blocker_instance_ids != tuple(sorted(self.blocker_instance_ids)):
            raise ValueError("block constraint blocker ids must be sorted")
        if len(self.blocker_instance_ids) != len(set(self.blocker_instance_ids)):
            raise ValueError("block constraint blocker ids must be unique")
        if len(self.blocker_instance_ids) > 1:
            raise ValueError("block constraint accepts one blocking actor")
        if bool(self.blocker_instance_ids) != bool(self.blocker_count):
            raise ValueError("block constraint actor and block count are inconsistent")
        satisfied = self.blocker_count >= self.required_block_count
        if satisfied != (self.constraint_state == "satisfied"):
            raise ValueError("block constraint state disagrees with blocker count")
        return self


class EnemyState(_Contract):
    instance_id: str = Field(min_length=1)
    enemy_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    waypoint_index: int = Field(default=0, ge=0)
    segment_progress_milli: int = Field(default=0, ge=0, lt=1000)
    movement_phase_remainder: int = Field(default=0, ge=0)
    wait_remaining_ticks: int = Field(default=0, ge=0)
    route_teleport_destination_index: int | None = Field(default=None, ge=0)
    hp: int = Field(ge=0)
    #: 上一击没扣满一点血的那部分, 千分位。伤害的精确值带小数(atk 816 打 def
    #: 800 是 **40.8** 不是 40), 血量是整数, 于是余数必须存下来——否则每击下取整
    #: 会系统性少算, M 线实测约 2%(白板 §2.9)。与 `segment_progress_milli` 同
    #: 一个写法: 精确量在累加器里, 读出来的那个量取整。
    damage_remainder_milli: int = Field(default=0, ge=0, lt=1000)
    attack_cooldown: int = Field(default=0, ge=0)
    blocked_by: str | None = None
    path_override: tuple[GridPoint, ...] = ()
    spawn_occurrence_id: str | None = Field(default=None, min_length=1)
    block_constraint: EnemyBlockConstraintState | None = None
    coefficient_runtime: EnemyCoefficientRuntimeState | None = None
    herd_resting_remaining_ticks: int = Field(default=0, ge=0)
    talent_stun_remaining_ticks: int = Field(default=0, ge=0, le=300)
    #: Whether 脉冲波 has already switched this enemy into 强化模式.  PRTS gives
    #: it no duration -- 「受【脉冲波】影响后可进入强化模式」 -- so it is a latch,
    #: not a timer.
    pulse_empowered: bool = False
















class InitialState(_Contract):
    schema_id: Literal[INITIAL_STATE_SCHEMA] = INITIAL_STATE_SCHEMA
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tick: int = Field(default=0, ge=0)
    cost: int = Field(ge=0)
    max_cost_bonus: int = Field(default=0, ge=0, strict=True)
    initial_deployment_points_bonus: int = Field(default=0, ge=0, strict=True)
    cost_recovery_slowdown_debt_milli: int = Field(default=0, ge=0, strict=True)
    battle_progress_slowdown_debt_milli: int = Field(default=0, ge=0, strict=True)
    spawn_progress_slowdown_debt_milli: int = Field(default=0, ge=0, strict=True)
    life_points: int = Field(ge=0)
    kills: int = Field(default=0, ge=0)
    leaks: int = Field(default=0, ge=0)
    status: BattleStatus = BattleStatus.RUNNING
    spawned_spawn_ids: tuple[str, ...] = ()
    #: Aliases already brought into play, so a resumed battle does not
    #: activate the same predefined token twice.
    activated_token_aliases: tuple[str, ...] = ()
    operators: tuple[OperatorState, ...] = ()
    enemies: tuple[EnemyState, ...] = ()
    summons: tuple[SummonState, ...] = ()
    projectiles: tuple[ProjectileState, ...] = ()
    healing_devices: tuple[HealingDeviceState, ...] = ()
    polluted_zones: tuple[PollutedZoneState, ...] = ()
    pending_explosions: tuple[PendingExplosionState, ...] = ()
    active_effects: tuple[ActiveEffectState, ...] = ()
    roster: tuple[RosterState, ...] = ()
    entity_sequence: int = Field(default=0, ge=0)
    effect_sequence: int = Field(default=0, ge=0)
    event_sequence: int = Field(default=0, ge=0)
    command_sequence: int = Field(default=0, ge=0)
    rng_seed: int = Field(default=0, ge=0, le=18_446_744_073_709_551_615)
    rng_streams: tuple[RNGStreamState, ...] = ()
    parent_snapshot_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    paused: bool = False
    speed: Literal[1, 2] = 1
    global_modifiers: GlobalModifierState = GlobalModifierState()
    tick_digest_count: int = Field(default=0, ge=0)
    last_tick_digest_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def tick_digest_head_matches_count(self) -> "InitialState":
        if (self.tick_digest_count == 0) != (self.last_tick_digest_hash is None):
            raise ValueError("tick digest count and chain head are inconsistent")
        return self


class BattleSnapshot(_Contract):
    schema_id: Literal[SNAPSHOT_SCHEMA] = SNAPSHOT_SCHEMA
    engine_version: Literal[ENGINE_VERSION] = ENGINE_VERSION
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_snapshot_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    tick: int = Field(ge=0)
    cost: int = Field(ge=0)
    max_cost_bonus: int = Field(default=0, ge=0, strict=True)
    initial_deployment_points_bonus: int = Field(default=0, ge=0, strict=True)
    cost_recovery_slowdown_debt_milli: int = Field(default=0, ge=0, strict=True)
    battle_progress_tick: int = Field(default=0, ge=0, strict=True)
    battle_progress_slowdown_debt_milli: int = Field(default=0, ge=0, strict=True)
    spawn_progress_tick: int = Field(default=0, ge=0, strict=True)
    spawn_progress_slowdown_debt_milli: int = Field(default=0, ge=0, strict=True)
    life_points: int = Field(ge=0)
    kills: int = Field(ge=0)
    leaks: int = Field(ge=0)
    status: BattleStatus
    spawned_spawn_ids: tuple[str, ...]
    #: Aliases already brought into play, so a restored battle does
    #: not activate the same token twice.
    activated_token_aliases: tuple[str, ...] = ()
    pending_spawn_ids: tuple[str, ...]
    spawn_cursor: int = Field(ge=0)
    operators: tuple[OperatorState, ...]
    enemies: tuple[EnemyState, ...]
    summons: tuple[SummonState, ...]
    projectiles: tuple[ProjectileState, ...]
    healing_devices: tuple[HealingDeviceState, ...]
    polluted_zones: tuple[PollutedZoneState, ...]
    pending_explosions: tuple[PendingExplosionState, ...] = ()
    active_effects: tuple[ActiveEffectState, ...]
    roster: tuple[RosterState, ...]
    entity_sequence: int = Field(ge=0)
    effect_sequence: int = Field(ge=0)
    event_sequence: int = Field(ge=0)
    command_sequence: int = Field(ge=0)
    rng_seed: int = Field(ge=0, le=18_446_744_073_709_551_615)
    rng_streams: tuple[RNGStreamState, ...]
    paused: bool
    speed: Literal[1, 2]
    global_modifiers: GlobalModifierState
    tick_digest_count: int = Field(ge=0)
    last_tick_digest_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def tick_digest_head_matches_count(self) -> "BattleSnapshot":
        if (self.tick_digest_count == 0) != (self.last_tick_digest_hash is None):
            raise ValueError("tick digest count and chain head are inconsistent")
        return self

    def computed_hash(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"snapshot_hash"}))

    def verify_hash(self) -> bool:
        return self.snapshot_hash == self.computed_hash()


















class BattleEvent(_Contract):
    tick: int = Field(ge=0)
    phase: EventPhase
    priority: int = Field(default=0, ge=0)
    actor_id: str
    sequence: int = Field(ge=1)
    kind: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def ordering_key(self) -> tuple[int, int, int, str, int]:
        return self.tick, int(self.phase), self.priority, self.actor_id, self.sequence


class TickStateDigest(_Contract):
    schema_id: Literal[TICK_DIGEST_SCHEMA] = TICK_DIGEST_SCHEMA
    digest_index: int = Field(gt=0)
    tick: int = Field(ge=0)
    state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_digest_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    digest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    def computed_hash(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"digest_hash"}))

    def verify_hash(self) -> bool:
        return self.digest_hash == self.computed_hash()


class CapabilityReport(_Contract):
    engine_version: str
    required: tuple[MechanismFidelity, ...]
    unsupported: tuple[str, ...]
    competition_ready: bool


class ObservedEnemy(_Contract):
    instance_id: str
    enemy_id: str
    hp: int = Field(gt=0)
    col_milli: int
    row_milli: int
    blocked_by: str | None = None


class KnownOnlyObservation(_Contract):
    view: Literal["known_only"] = "known_only"
    tick: int
    status: BattleStatus
    cost: int
    life_points: int
    kills: int
    leaks: int
    paused: bool
    speed: Literal[1, 2]
    global_modifiers: GlobalModifierState
    deployed: tuple[OperatorState, ...]
    visible_summons: tuple[SummonState, ...]
    visible_projectiles: tuple[ProjectileState, ...]
    visible_devices: tuple[HealingDeviceState, ...]
    visible_polluted_zones: tuple[PollutedZoneState, ...] = ()
    visible_effects: tuple[ActiveEffectState, ...]
    visible_enemies: tuple[ObservedEnemy, ...]
    revealed_enemy_types: tuple[str, ...]
    legal_actions: tuple[PublicAction, ...]
    snapshot_hash: str
    capabilities: CapabilityReport


class OmniscientObservation(_Contract):
    view: Literal["omniscient"] = "omniscient"
    snapshot: BattleSnapshot
    pending_spawn_ids: tuple[str, ...]
    content_hash: str
    capabilities: CapabilityReport


class ForecastReceipt(_Contract):
    schema_id: Literal["omnicompany.arknights.tournament-sim.forecast-receipt.v1"] = (
        "omnicompany.arknights.tournament-sim.forecast-receipt.v1"
    )
    ledger_id: str
    forecast_index: int = Field(gt=0)
    raw_ops_charged: Literal[2] = 2
    total_raw_ops: int = Field(ge=2)
    total_forecasts: int = Field(ge=1)
    overage_forecasts: int = Field(ge=0)
    over_limit: bool
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_receipt_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ForecastResult(_Contract):
    base_snapshot_hash: str
    final_snapshot_hash: str
    simulated_ticks: int = Field(ge=0)
    observation: KnownOnlyObservation
    events: tuple[BattleEvent, ...]
    tick_digests: tuple[TickStateDigest, ...]
    receipt: ForecastReceipt


class CommandRecord(_Contract):
    sequence: int = Field(gt=0)
    tick_before: int = Field(ge=0)
    action: PublicAction
    snapshot_hash_before: str
    snapshot_hash_after: str
    event_count_before: int = Field(ge=0)
    event_count_after: int = Field(ge=0)
    previous_record_hash: str | None = None
    record_hash: str






class BattleReplay(_Contract):
    schema_id: Literal[REPLAY_SCHEMA] = REPLAY_SCHEMA
    engine_version: Literal[ENGINE_VERSION] = ENGINE_VERSION
    content_hash: str
    source_wave_package_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_wave_scenario_id: str | None = Field(default=None, min_length=1)
    source_wave_scenario_assignments: tuple[SourceWaveScenarioAssignment, ...] = ()
    initial_state: InitialState
    commands: tuple[CommandRecord, ...]
    events: tuple[BattleEvent, ...]
    tick_digests: tuple[TickStateDigest, ...]
    final_snapshot_hash: str
    replay_hash: str

    @model_validator(mode="after")
    def tick_digest_chain_is_well_formed(self) -> "BattleReplay":
        expected_index = self.initial_state.tick_digest_count
        expected_tick = self.initial_state.tick
        previous = self.initial_state.last_tick_digest_hash
        for digest in self.tick_digests:
            expected_index += 1
            expected_tick += 1
            if digest.digest_index != expected_index or digest.tick != expected_tick:
                raise ValueError("tick digest sequence is not contiguous")
            if digest.previous_digest_hash != previous or not digest.verify_hash():
                raise ValueError("tick digest chain is invalid")
            previous = digest.digest_hash
        return self


class ForecastBudgetState(_Contract):
    ledger_id: str = Field(min_length=1, max_length=256)
    raw_ops_limit: int = Field(ge=0)
    forecast_limit: int = Field(ge=0)
    total_raw_ops: int = Field(ge=0)
    total_forecasts: int = Field(ge=0)
    receipts: tuple[ForecastReceipt, ...] = ()


class EngineCheckpoint(_Contract):
    """Judge-side crash-recovery image; never part of ``known_only`` output."""

    schema_id: Literal[CHECKPOINT_SCHEMA] = CHECKPOINT_SCHEMA
    engine_version: Literal[ENGINE_VERSION] = ENGINE_VERSION
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_initial_state: InitialState
    snapshot: BattleSnapshot
    commands: tuple[CommandRecord, ...]
    events: tuple[BattleEvent, ...]
    tick_digests: tuple[TickStateDigest, ...]
    budget: ForecastBudgetState
    checkpoint_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def tick_digest_chain_matches_snapshot(self) -> "EngineCheckpoint":
        expected_index = self.original_initial_state.tick_digest_count
        expected_tick = self.original_initial_state.tick
        previous = self.original_initial_state.last_tick_digest_hash
        for digest in self.tick_digests:
            expected_index += 1
            expected_tick += 1
            if digest.digest_index != expected_index or digest.tick != expected_tick:
                raise ValueError("checkpoint tick digest sequence is not contiguous")
            if digest.previous_digest_hash != previous or not digest.verify_hash():
                raise ValueError("checkpoint tick digest chain is invalid")
            previous = digest.digest_hash
        if expected_index != self.snapshot.tick_digest_count:
            raise ValueError("checkpoint tick digest count does not match snapshot")
        if previous != self.snapshot.last_tick_digest_hash:
            raise ValueError("checkpoint tick digest head does not match snapshot")
        return self

    def computed_hash(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"checkpoint_hash"}))

    def verify_hash(self) -> bool:
        return self.checkpoint_hash == self.computed_hash()


class ReplayVerification(_Contract):
    valid: bool
    issues: tuple[str, ...] = ()
    final_snapshot_hash: str | None = None


class StepResult(_Contract):
    snapshot: BattleSnapshot
    events: tuple[BattleEvent, ...]
    tick_digests: tuple[TickStateDigest, ...]
    observation: KnownOnlyObservation
    events_complete: bool = True
    tick_digests_complete: bool = True


__all__ = [
    "ActivateSkillAction",
    "ActiveEffectState",
    "AttackScaleEffectDefinition",
    "BattleEvent",
    "BattleReplay",
    "BattleSnapshot",
    "BattleStatus",
    "CapabilityReport",
    "CommandRecord",
    "ControlEffect",
    "CHECKPOINT_SCHEMA",
    "ContentBundle",
    "DeployAction",
    "DeploymentPointsEffectDefinition",
    "DeploymentClass",
    "Direction",
    "DamageEffectDefinition",
    "EffectDefinition",
    "EffectRangeOrigin",
    "EffectRangeRotation",
    "EffectStat",
    "EffectTargetSelector",
    "EnemyBlockConstraintState",
    "EnemyCoefficientRuntimeState",
    "EnemyDefinition",
    "EnemyTimedSelfModifierDefinition",
    "EnemyState",
    "ENGINE_VERSION",
    "EventPhase",
    "FidelityLevel",
    "ForecastReceipt",
    "ForecastBudgetState",
    "ForecastResult",
    "GridPoint",
    "GlobalModifierState",
    "HealEffectDefinition",
    "ScaledHealEffectDefinition",
    "HealingDeviceDefinition",
    "HealingDeviceState",
    "InitialState",
    "EngineCheckpoint",
    "KnownOnlyObservation",
    "MapDefinition",
    "MechanismFidelity",
    "MovementLayer",
    "OmniscientObservation",
    "OperatorDefinition",
    "OperatorState",
    "PollutedZoneState",
    "ProjectileState",
    "PublicAction",
    "ReplayVerification",
    "ReplayRetentionPolicy",
    "ReplayRetentionState",
    "RandomDamageEffectDefinition",
    "RetreatAction",
    "RosterState",
    "RNGStreamState",
    "RouteDefinition",
    "SelectTargetAction",
    "SetPausedAction",
    "SetSpeedAction",
    "SkillDefinition",
    "SkillActivationPolicy",
    "SkillSpEvent",
    "SkillSpRecoveryMode",
    "SkillState",
    "SpawnDefinition",
    "SourceWaveActionDefinition",
    "SourceWaveDefinition",
    "SourceWaveFragmentDefinition",
    "SourceWaveScheduleAdvance",
    "SourceWaveScheduleEvent",
    "SourceWaveScheduleSnapshot",
    "SourceWaveScenarioAssignment",
    "SpecialTileDefinition",
    "SpecialTileControlEffectDefinition",
    "SpecialTileEffectDefinition",
    "SpecialTileStatModifierDefinition",
    "StepResult",
    "StatModifierEffectDefinition",
    "SpMutationEffectDefinition",
    "BloodDebtLedger",
    "SummonDefinition",
    "SummonEffectDefinition",
    "SummonState",
    "TICK_DIGEST_SCHEMA",
    "TickStateDigest",
    "VisibilityClass",
    "WaitAction",
    "canonical_sha256",
    "enemy_timed_modifier_effect_id",
]
