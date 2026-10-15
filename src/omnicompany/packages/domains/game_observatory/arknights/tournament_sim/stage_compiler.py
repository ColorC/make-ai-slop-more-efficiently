# [OMNI] origin=claude domain=game_observatory/arknights type=stage-compiler status=active
"""Compile shipped Arknights stage JSON into a :class:`ContentBundle`.

This is the S2-a gate.  Before it existed the engine could only run
hand-authored bundles, so stage runes, tiles, devices, branches and enemy
mechanisms had no way into a battle at all and the coverage denominator was
whatever had been transcribed by hand.

Design rules, in order of importance:

1. **Never invent a value.**  Anything the schema wants that the shipped data
   does not carry is recorded in :class:`CompileReport.missing_from_source`
   and left at its schema default; it is never guessed and never silently
   filled.  Callers that need it get told.
2. **Never drop a construct silently.**  Source constructs that the engine
   cannot yet represent (runes, devices, branches, global buffs, enemy
   talents/skills) are counted into
   :class:`CompileReport.unrepresented` so the report doubles as the honest
   coverage denominator for that stage.
3. **Prove the coordinate convention rather than assume it.**  The shipped
   ``mapData.map`` is a top-down array of tile indices while route positions
   count rows from the bottom, so every position crossing that boundary goes
   through :func:`flip_row`.  :func:`compile_stage` then checks that route
   start/end points actually land on start/end tiles and reports the result.
   Getting this axis backwards has already cost this project three live runs.

Operators are an input, never a product: a stage file has no roster, so the
caller supplies :class:`OperatorDefinition` values and the compiler only
validates that they fit the map.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .enemy_coefficients import (
    MAINLINE12_DISGUISED_IDS,
    death_transform_enemy_id,
    black_fog_range_offsets,
    hologram_enemy_id,
    death_unload_count,
    frozen_target_attack_scale,
    reduce_block_rows,
    death_unload_enemy_id,
    elemental_damage_type,
    enemy_is_stealthed,
    is_energy_charger,
    pulse_empowered_damage_type,
    pulse_immune,
    ranged_while_unblocked,
    unblocked_physical_evade,
    weakness_protected_side_from_description,
)
from .enemy_mechanics import (
    compile_enemy_mechanics,
    consumed_talent_keys,
    talent_key_gap_reason,
)
from .is3_run_modifiers import (
    CompositionPolicy,
    relic_modifiers,
    stage_rune_modifiers,
)
from .entity_definitions import (
    BranchSpawnDefinition,
    PredefinedTokenDefinition,
    SummonDefinition,
)
from .stage_clock import SPAWN_ACTION_TYPE, to_ticks  # noqa: F401
from .route_translation import (  # noqa: F401
    _compile_route,
    _point,
    _shortest,
    flip_row,
)
from .modifier_folding import (  # noqa: F401
    RUNE_ATTRIBUTE_TO_SOURCE,
    RELIC_ATTRIBUTE_TO_OPERATOR_FIELD,
    RUNE_ATTRIBUTE_TO_SUMMON_FIELD,
    _apply_relics_to_operators,
    _apply_skill_blackboard_multipliers,
    _apply_skill_cooldown_scale,
    _apply_rune_modifiers,
    _apply_runes_to_summon,
)
from .token_ingestion import (
    TOKEN_ACTIVATION_ACTION_TYPE,  # noqa: F401
    _compile_token_activations,
    _token_summon,
    token_facing,
)
from .enemy_immunities import enemy_immunities
from .enemy_skills import (  # noqa: F401 -- re-exported for importers
    CHARGED_SKILL_PREFABS,
    ChargedEnemySkill,
    ConditionalTakeOff,
    DevourSkill,
    EnergyChargeSkill,
    OverloadSkill,
    TakeOffSkill,
    VictorySelfBuff,
    charged_enemy_skill,
    conditional_takeoff_skill,
    devour_skill,
    energy_charge_skill,
    overload_skill,
    predation_talent,
    environmental_fragile_talent,
    backstab_skill,
    blood_hit_skill,
    four_direction_volley,
    ice_bomb_skill,
    blink_skill,
    chain_buff_skill,
    doom_skill,
    immolation_skill,
    driver_charge_sp,
    born_bugs_skill,
    beam_conduction_talent,
    bomb_run_skill,
    crossroads_volley_talent,
    beam_splash_talent,
    beam_weakness_talent,
    dies_after_attacking,
    flame_skill,
    repair_skill,
    reborning_skill,
    spawn_decay_talent,
    death_life_point_cost,
    guided_strike_skill,
    stealth_strike_scale_micros,
    tactical_command_talent,
    self_rage_talent,
    takeoff_skill,
    victory_self_buff,
)
from .models import (
    CORE_MECHANISMS,
    ContentBundle,
    EffectStat,
    EnemyDefinition,
    FidelityLevel,
    GridPoint,
    MapDefinition,
    MechanismFidelity,
    MovementLayer,
    OperatorDefinition,
    RouteDefinition,
    SourceRouteCheckpoint,
    SourceWaveActionDefinition,
    SourceWaveDefinition,
    SourceWaveFragmentDefinition,
    SpawnDefinition,
    SpecialTileDefinition,
    SpecialTileStatModifierDefinition,
)

REPORT_SCHEMA = "omnicompany.arknights.tournament-sim.stage-compile-report.v1"
DEFAULT_TPS = 30

#: ``buildableType`` values in shipped tiles.
BUILDABLE_GROUND = 1
BUILDABLE_HIGH = 2

#: ``passableMask`` bits: bit 0 is ground traversal, bit 1 is flight.
PASSABLE_GROUND = 1
PASSABLE_FLYING = 2

#: Rune attribute names mapped onto the shipped enemy attribute names they
#: modify.  `mass_level` has no engine counterpart and is reported instead.

#: Tile keys whose blackboard is a periodic damage / stat pulse.  The values
#: live in the shipped tile blackboard, so these are compiled, not guessed.
PULSE_TILE_KEYS = frozenset(
    {
        "tile_volcano",
        "tile_infection",
        "tile_pollution_road",
        "tile_pollution_wall",
        "tile_defbreak",
    }
)


class _Report(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InvariantResult(_Report):
    """Outcome of one compile-time self-check."""

    name: str = Field(min_length=1)
    passed: bool
    detail: str = Field(min_length=1)


class UnrepresentedConstruct(_Report):
    """A shipped construct the engine cannot express yet."""

    kind: Literal[
        "rune",
        "device",
        "branch",
        "global_buff",
        "enemy_talent",
        "enemy_skill",
        "tile_semantic",
        "route",
        "wave_action",
        "option",
    ]
    key: str = Field(min_length=1)
    count: int = Field(ge=1)
    reason: str = Field(min_length=1)


class MissingSourceField(_Report):
    """A schema field the shipped data does not carry."""

    field_path: str = Field(min_length=1)
    used_instead: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class CompileReport(_Report):
    schema_id: Literal[REPORT_SCHEMA] = REPORT_SCHEMA
    stage_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ticks_per_second: int = Field(gt=0)
    map_width: int = Field(gt=0)
    map_height: int = Field(gt=0)
    route_count: int = Field(ge=0)
    spawn_count: int = Field(ge=0)
    enemy_count: int = Field(ge=0)
    special_tile_count: int = Field(ge=0)
    invariants: tuple[InvariantResult, ...] = ()
    unrepresented: tuple[UnrepresentedConstruct, ...] = ()
    missing_from_source: tuple[MissingSourceField, ...] = ()
    node_variant: Literal["normal", "raid"] = "normal"
    rune_applications: tuple[str, ...] = ()
    relic_applications: tuple[str, ...] = ()
    #: Tokens the shipped data describes but this bundle does not place,
    #: each with why.  Never silently dropped.
    token_notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when every self-check passed.

        Unrepresented constructs do not make a compile invalid -- they are the
        point of the report -- but a failed invariant means the compiled bundle
        may be geometrically wrong and must not be trusted.
        """

        return all(item.passed for item in self.invariants)


# --------------------------------------------------------------------------- #
# source helpers
# --------------------------------------------------------------------------- #


def unwrap(node: Any, default: Any = None) -> Any:
    """Unwrap the shipped ``{m_defined, m_value}`` optional wrapper."""

    if isinstance(node, dict) and "m_value" in node:
        return node["m_value"] if node.get("m_defined") else default
    return default if node is None else node






def _blackboard(entries: Iterable[dict[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in entries or []:
        value = item.get("valueStr")
        out[item["key"]] = item.get("value") if value is None else value
    return out


def load_enemy_database(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Index ``enemy_database.json`` by enemy id."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {row["key"]: row["value"] for row in payload["enemies"]}


# --------------------------------------------------------------------------- #
# map
# --------------------------------------------------------------------------- #


def _compile_map(
    stage: dict[str, Any],
) -> tuple[MapDefinition, dict[tuple[int, int], dict[str, Any]], set[tuple[int, int]]]:
    map_data = stage.get("mapData") or {}
    rows = map_data.get("map") or []
    tiles = map_data.get("tiles") or []
    if not rows or not rows[0]:
        raise ValueError("stage has no map grid")
    height, width = len(rows), len(rows[0])

    by_position: dict[tuple[int, int], dict[str, Any]] = {}
    ground: list[GridPoint] = []
    high: list[GridPoint] = []
    walkable: list[GridPoint] = []
    for row_index, row in enumerate(rows):
        for col, tile_index in enumerate(row):
            tile = tiles[int(tile_index)]
            by_position[(col, row_index)] = tile
            buildable = int(tile.get("buildableType") or 0)
            passable = int(tile.get("passableMask") or 0)
            spot = GridPoint(col=col, row=row_index)
            if buildable == BUILDABLE_GROUND:
                ground.append(spot)
            elif buildable == BUILDABLE_HIGH:
                high.append(spot)
            if passable & PASSABLE_GROUND:
                walkable.append(spot)

    definition = MapDefinition(
        width=width,
        height=height,
        deployable_tiles=tuple(ground + high),
        ground_tiles=tuple(ground),
        high_ground_tiles=tuple(high),
        walkable_tiles=tuple(walkable),
    )
    walkable_keys = {(spot.col, spot.row) for spot in walkable}
    return definition, by_position, walkable_keys


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #






# --------------------------------------------------------------------------- #
# waves and spawns
# --------------------------------------------------------------------------- #


def _compile_waves(stage: dict[str, Any], tps: int) -> tuple[SourceWaveDefinition, ...]:
    waves: list[SourceWaveDefinition] = []
    for wave_index, wave in enumerate(stage.get("waves") or []):
        fragments: list[SourceWaveFragmentDefinition] = []
        for fragment_index, fragment in enumerate(wave.get("fragments") or []):
            actions: list[SourceWaveActionDefinition] = []
            for action_index, action in enumerate(fragment.get("actions") or []):
                actions.append(
                    SourceWaveActionDefinition(
                        source_index=action_index,
                        action_type=int(action.get("actionType", -1)),
                        key=None if action.get("key") is None else str(action["key"]),
                        route_index=(
                            int(action["routeIndex"]) if action.get("routeIndex") is not None else None
                        ),
                        count=max(0, int(action.get("count") or 0)),
                        pre_delay_ticks=to_ticks(action.get("preDelay"), tps),
                        interval_ticks=to_ticks(action.get("interval"), tps),
                        weight_milli=max(0, round(float(action.get("weight") or 0) * 1000)),
                        random_spawn_group_key=action.get("randomSpawnGroupKey") or None,
                        random_spawn_group_pack_key=action.get("randomSpawnGroupPackKey") or None,
                        hidden_group=action.get("hiddenGroup") or None,
                        not_count_in_total=bool(action.get("notCountInTotal")),
                        source_json=json.dumps(action, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    )
                )
            fragments.append(
                SourceWaveFragmentDefinition(
                    source_index=fragment_index,
                    pre_delay_ticks=to_ticks(fragment.get("preDelay"), tps),
                    actions=tuple(actions),
                )
            )
        waves.append(
            SourceWaveDefinition(
                source_index=wave_index,
                pre_delay_ticks=to_ticks(wave.get("preDelay"), tps),
                post_delay_ticks=to_ticks(wave.get("postDelay"), tps),
                max_time_waiting_for_next_wave_ticks=to_ticks(
                    wave.get("maxTimeWaitingForNextWave"), tps
                ),
                fragments=tuple(fragments),
            )
        )
    return tuple(waves)


def _forbidden_deploy_points(
    stage: dict[str, Any],
    node_variant: str,
    width: int,
    height: int,
) -> tuple[set[tuple[int, int]], list[str], list[str]]:
    """Parse `global_forbid_location` into grid points to un-deploy.

    The location string looks like ``(4,3)|(2,5)|(3,3)``.  The pair order is
    **(row, col)**, not (col, row): stage 5-7 and b-3-b both name ``(3,9)`` on
    grids 11 and 12 wide but only 7 and 8 tall, so reading it as (col, row)
    puts it outside the map entirely.

    The row is then flipped like every other shipped position.  That last part
    is the registered ambiguity: across the three stages carrying this rune the
    flipped and unflipped readings each land on a deployable tile in one stage
    the other misses, so the evidence ties.  The flipped reading is the default
    because every other position in these files counts rows from the bottom;
    each point that fails to land on a deployable tile is reported so a wrong
    choice is visible instead of silent.
    """

    wanted_bit = 1 if node_variant == "normal" else 2
    points: set[tuple[int, int]] = set()
    applied: list[str] = []
    notes: list[str] = []
    for rune in stage.get("runes") or []:
        if str(rune.get("key") or "") != "global_forbid_location":
            continue
        if not int(rune.get("difficultyMask") or 0) & wanted_bit:
            continue
        raw = _blackboard(rune.get("blackboard")).get("location")
        if not isinstance(raw, str) or not raw.strip():
            notes.append("global_forbid_location: blackboard carries no location string")
            continue
        for chunk in raw.split("|"):
            text = chunk.strip().strip("()")
            parts = [item.strip() for item in text.split(",")]
            if len(parts) != 2 or not all(item.lstrip("-").isdigit() for item in parts):
                notes.append(f"global_forbid_location: cannot parse {chunk!r}")
                continue
            row, col = int(parts[0]), int(parts[1])
            spot = (col, flip_row(row, height))
            if not (0 <= spot[0] < width and 0 <= spot[1] < height):
                notes.append(
                    f"global_forbid_location: {chunk} -> {spot} is outside the "
                    f"{width}x{height} grid, skipped"
                )
                continue
            points.add(spot)
        if points:
            applied.append("global_forbid_location")
            notes.append(
                "global_forbid_location: %d deploy tiles removed %s"
                % (len(points), sorted(points))
            )
    return points, applied, notes


def _level_scope_runes(
    stage: dict[str, Any],
    node_variant: str,
) -> tuple[dict[str, str], int | None, int | None, list[str], list[str]]:
    """Read the level-scope runes that a compiler can honour exactly.

    Returns ``(enemy_replacements, max_cost, life_points, applied, notes)``.
    These runes are static per node variant, so folding them in at compile time
    is exact -- there is no runtime decision for the engine to make.
    """

    wanted_bit = 1 if node_variant == "normal" else 2
    replacements: dict[str, str] = {}
    max_cost: int | None = None
    life_points: int | None = None
    applied: list[str] = []
    notes: list[str] = []

    for rune in stage.get("runes") or []:
        key = str(rune.get("key") or "")
        if not int(rune.get("difficultyMask") or 0) & wanted_bit:
            continue
        blackboard = _blackboard(rune.get("blackboard"))
        if key == "level_enemy_replace":
            source = blackboard.get("key")
            target = blackboard.get("value")
            if isinstance(source, str) and isinstance(target, str) and source and target:
                replacements[source] = target
                applied.append(key)
                notes.append(f"level_enemy_replace: {source} -> {target}")
            else:
                notes.append("level_enemy_replace: blackboard lacks a key/value id pair")
        elif key == "cbuff_max_cost":
            value = blackboard.get("max_cost")
            if isinstance(value, (int, float)):
                max_cost = int(value)
                applied.append(key)
                # `max_cost_ceil` is the ceiling relics may raise the cap back
                # to; it is a run-layer concern, not this battle's cap.
                notes.append(
                    f"cbuff_max_cost: max_cost -> {max_cost} "
                    f"(ceil {blackboard.get('max_cost_ceil')} left to the run layer)"
                )
        elif key == "global_lifepoint":
            value = blackboard.get("value")
            if isinstance(value, (int, float)) and value >= 1:
                life_points = int(value)
                applied.append(key)
                notes.append(f"global_lifepoint: life_points -> {life_points}")
    return replacements, max_cost, life_points, applied, notes







def _compile_spawns(stage: dict[str, Any], tps: int) -> list[dict[str, Any]]:
    """Flatten waves into absolute-tick spawn events.

    The shipped clock is relative: a wave's ``preDelay`` starts after the
    previous wave finished emitting, and a fragment's ``preDelay`` is relative
    to its wave.  The running clock below reproduces that nesting.
    """

    events: list[dict[str, Any]] = []
    # The clock accumulates in **ticks**, not seconds, because
    # `source_wave_schedule._compile_rows` -- the authoritative scheduler --
    # adds the already-rounded `pre_delay_ticks` of each level.  Summing the
    # seconds and rounding once at the end diverges from rounding each part and
    # adding: sub-tick preDelays that each round to one tick accumulated an
    # extra tick per fragment there and none here, and the audit comparing the
    # two clocks then refused the stage.
    wave_clock = 0
    for wave_index, wave in enumerate(stage.get("waves") or []):
        wave_pre = to_ticks(wave.get("preDelay"), tps)
        wave_clock += wave_pre
        fragment_clock = wave_clock
        for fragment_index, fragment in enumerate(wave.get("fragments") or []):
            fragment_pre = to_ticks(fragment.get("preDelay"), tps)
            fragment_clock += fragment_pre
            fragment_end = fragment_clock
            actions = fragment.get("actions") or []

            # Random-spawn groups pick one action by weight; the probability is
            # the action's share of its group total.
            group_weight: dict[str, float] = {}
            for action in actions:
                group = str(action.get("randomSpawnGroupKey") or "")
                if group:
                    group_weight[group] = group_weight.get(group, 0.0) + max(
                        0.0, float(action.get("weight") or 0)
                    )

            for action_index, action in enumerate(actions):
                if int(action.get("actionType", -1)) != SPAWN_ACTION_TYPE:
                    continue
                key = str(action.get("key") or "")
                if not key:
                    continue
                pre = to_ticks(action.get("preDelay"), tps)
                interval = to_ticks(action.get("interval"), tps)
                count = max(1, int(action.get("count") or 1))
                group = str(action.get("randomSpawnGroupKey") or "")
                probability = 1.0
                if group:
                    total = max(100.0, group_weight.get(group, 0.0))
                    probability = max(0.0, float(action.get("weight") or 0)) / total
                elif action.get("hiddenGroup") and float(action.get("weight") or 0) > 0:
                    probability = min(1.0, float(action.get("weight") or 0) / 100.0)
                events.append(
                    {
                        "spawn_id": f"w{wave_index}-f{fragment_index}-a{action_index}",
                        "tick": max(1, fragment_clock + pre),
                        "enemy_id": key,
                        "source_route_index": int(action.get("routeIndex") or 0),
                        "count": count,
                        "interval_ticks": interval,
                        "source_wave_index": wave_index,
                        "source_fragment_index": fragment_index,
                        "source_action_index": action_index,
                        "wave_pre_delay_ticks": wave_pre,
                        "fragment_pre_delay_ticks": fragment_pre,
                        "action_pre_delay_ticks": pre,
                        "random_spawn_group_key": group or None,
                        "random_spawn_group_pack_key": action.get("randomSpawnGroupPackKey") or None,
                        "hidden_group": action.get("hiddenGroup") or None,
                        "selection_probability_micros": round(probability * 1_000_000),
                        "not_count_in_total": bool(action.get("notCountInTotal")),
                    }
                )
                # `dontBlockWave` actions do **not** extend the fragment.
                # `source_wave_schedule._compile_rows` is the authoritative
                # clock and skips them; advancing here for every spawn made this
                # clock run ahead of that one, and the audit comparing the two
                # then refused the stage ("translated spawn does not match its
                # source action") -- five IS3 stages could not be instantiated
                # for exactly this reason.
                if not action.get("dontBlockWave"):
                    fragment_end = max(
                        fragment_end,
                        fragment_clock + pre + max(0, count - 1) * interval,
                    )
            fragment_clock = fragment_end
        wave_clock = fragment_clock + to_ticks(wave.get("postDelay"), tps)
    return events


# --------------------------------------------------------------------------- #
# enemies
# --------------------------------------------------------------------------- #


def deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """Overlay a stage override on a database record, honouring ``m_defined``.

    A shallow ``dict.update`` is wrong here: an override usually carries a
    *partial* ``attributes`` block, so replacing the whole block drops every
    attribute it does not mention (this silently zeroed ``maxHp`` on the first
    attempt).  Optional wrappers with ``m_defined: false`` mean "not overridden"
    and must leave the base value alone.
    """

    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and "m_defined" in value:
            if value.get("m_defined"):
                result[key] = value
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        elif isinstance(value, list):
            # `skills` and `talentBlackboard` are lists, so they have no
            # `m_defined` flag of their own.  An empty list on an upper layer is
            # read as "not overridden here" (inherit), matching how every
            # scalar in the same record signals absence -- 102 level-N records
            # would otherwise strip abilities their level-0 base defines.
            # See CompileReport.missing_from_source for the recorded assumption.
            if value:
                result[key] = value
        elif value is not None:
            result[key] = value
    return result


def _enemy_source(
    ref: dict[str, Any],
    database: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Resolve one ``enemyDbRefs`` entry to its effective ``enemyData``.

    ``useDb: false`` means the stage carries the whole record inline; otherwise
    ``overwrittenData`` is deep-merged over the database record for the
    matching level.
    """

    enemy_id = ref["id"]
    level = int(ref.get("level") or 0)
    override = ref.get("overwrittenData") or {}
    records = database.get(enemy_id)
    if records is None:
        if not override:
            raise KeyError(f"enemy {enemy_id!r} is absent from the database and carries no inline data")
        return dict(override)

    # Levels are layered, not standalone: a level-N record redefines only what
    # changes (usually the stat block) and leaves everything else
    # ``m_defined: false``, meaning "inherit".  Reading level N alone silently
    # turns flying units into ground units and zeroes their move speed, which
    # is how 117 of the 644 IS3 enemy references were being mis-compiled.
    ordered = sorted(
        (row for row in records if int(row.get("level") or 0) <= level),
        key=lambda row: int(row.get("level") or 0),
    )
    if not ordered:
        ordered = [min(records, key=lambda row: int(row.get("level") or 0))]
    merged: dict[str, Any] = {}
    for row in ordered:
        merged = deep_merge(merged, row["enemyData"])
    return deep_merge(merged, override)



#: Relic blackboards name attributes the way the shipped tables do; these are
#: the ones an OperatorDefinition can actually carry.  Anything else is reported
#: as unapplied rather than silently dropped.





def _compile_enemy(
    enemy_id: str,
    data: dict[str, Any],
    tps: int,
    rune_modifiers: Any = None,
    relic_enemy_modifiers: Any = None,
    enemy_names: dict[str, str] | None = None,
    talent_addends: dict[str, float] | None = None,
    talent_multipliers: dict[str, float] | None = None,
    granted_abilities: frozenset[str] = frozenset(),
    skill_blackboard_factors: "dict[str, dict[str, float]] | None" = None,
) -> tuple[EnemyDefinition, list[str], tuple[Any, ...], list[str]]:
    # `enemy_skill_blackb_mul` rewrites a *skill's* own blackboard rather than
    # the carrier's stats, so it is applied where the skill contract is read
    # rather than through the attribute modifiers below.
    skill_factors = dict(skill_blackboard_factors or {})
    scaled_stealth_strike = stealth_strike_scale_micros(data)
    invisible_factor = (skill_factors.get("InvisibleCombat") or {}).get("atk_scale")
    if scaled_stealth_strike and invisible_factor:
        scaled_stealth_strike = round(scaled_stealth_strike * float(invisible_factor))
    attributes = {key: unwrap(value) for key, value in (data.get("attributes") or {}).items()}
    attributes, rune_notes = _apply_rune_modifiers(attributes, rune_modifiers)
    attributes, relic_attribute_notes = _apply_rune_modifiers(
        attributes, relic_enemy_modifiers
    )
    rune_notes.extend(f"relic {note}" for note in relic_attribute_notes)
    notes: list[str] = []

    max_hp = int(attributes.get("maxHp") or 0)
    if max_hp <= 0:
        raise ValueError(f"enemy {enemy_id!r} has no positive maxHp")

    base_attack_time = float(attributes.get("baseAttackTime") or 0)
    attack_speed = float(attributes.get("attackSpeed") or 100.0) or 100.0
    if base_attack_time > 0:
        # Shipped attackSpeed is a percentage: the effective interval is
        # baseAttackTime * 100 / attackSpeed.
        interval_ticks = max(1, round(base_attack_time * 100.0 / attack_speed * tps))
    else:
        interval_ticks = 1
        notes.append("baseAttackTime")

    move_speed = float(attributes.get("moveSpeed") or 0)
    if move_speed <= 0:
        move_speed = 0.0
        notes.append("moveSpeed")
    per_tick = max(1, min(1000, round(move_speed * 1000 / tps))) if move_speed > 0 else 1
    per_second = max(1, min(30000, round(move_speed * 1000))) if move_speed > 0 else 1

    magic_resistance = int(round(float(attributes.get("magicResistance") or 0)))
    range_radius = float(unwrap(data.get("rangeRadius"), 0) or 0)
    if range_radius > 0:
        # `enemy_attackradius_mul` multiplies the enemy's reach.  Applied here
        # rather than through the attribute block for two reasons the data
        # forces: `rangeRadius` is a top-level field, not a member of
        # `attributes`, and it is a real fraction (2.2 tiles) that must not be
        # floored the way hp/atk/def are.
        for modifiers in (rune_modifiers, relic_enemy_modifiers):
            if modifiers is None:
                continue
            scaled = float(
                modifiers.apply(
                    {"rangeRadius": range_radius},
                    CompositionPolicy(rounding="none"),
                )["rangeRadius"]
            )
            if scaled != range_radius:
                # Said out loud in the report: a doubled reach changes which
                # tiles are safe, and a silent one would be invisible.
                notes_radius = "rangeRadius %.3f -> %.3f" % (range_radius, scaled)
                rune_notes.append(notes_radius)
            range_radius = scaled
    if range_radius < 0:
        # -1 means "use the prefab default", which is not in the shipped data.
        range_radius = 0.0
        notes.append("rangeRadius")

    motion = int(unwrap(data.get("motion"), 0) or 0)
    life_damage = int(unwrap(data.get("lifePointReduce"), 1))

    # S2-b: mechanics come from this enemy's own blackboards, not an id
    # allow-list.  Keys the engine has no family for come back as `declared`
    # so the caller can report them instead of dropping them.
    coefficients, declared = compile_enemy_mechanics(enemy_id, data)
    if talent_addends:
        # `enemy_talent_blackb_add` changes a mechanism's own figure rather than
        # a stat, so it lands on the compiled coefficient and is said out loud:
        # 3-7's emergency node turns the slime's 1 point of death energy into 4,
        # which is four times the pressure on the same stage's energy pools.
        adjusted = []
        for coefficient in coefficients:
            addend = talent_addends.get(coefficient.key)
            if addend is None:
                adjusted.append(coefficient)
                continue
            micros = coefficient.value_micros + round(addend * 1_000_000)
            adjusted.append(coefficient.model_copy(update={"value_micros": micros}))
            rune_notes.append(
                "%s %+g -> %g"
                % (
                    coefficient.key,
                    addend,
                    micros / 1_000_000,
                )
            )
        coefficients = tuple(adjusted)
        for key in sorted(set(talent_addends) - {c.key for c in coefficients}):
            # Named a key this enemy does not carry: reported, never invented.
            rune_notes.append("%s: enemy carries no such talent key" % key)
    if talent_multipliers:
        # `enemy_talent_blackb_mul`: 5-2's emergency node doubles 食腐使徒's
        # 污染秽蚀 damage band.  Like the addends this lands on the compiled
        # coefficient rather than the stat block, because it changes a
        # mechanism's own figure.
        scaled = []
        for coefficient in coefficients:
            factor = talent_multipliers.get(coefficient.key)
            if factor is None:
                scaled.append(coefficient)
                continue
            micros = round(coefficient.value_micros * factor)
            scaled.append(coefficient.model_copy(update={"value_micros": micros}))
            rune_notes.append(
                "%s x%g -> %g"
                % (coefficient.key, factor, micros / 1_000_000)
            )
        coefficients = tuple(scaled)

    immolation = immolation_skill(data, tps)
    born_bugs = born_bugs_skill(data, tps, enemy_names)
    crossroads = crossroads_volley_talent(data)
    bomb_run = bomb_run_skill(data, tps)
    definition = EnemyDefinition(
        enemy_id=enemy_id,
        max_hp=max_hp,
        attack=max(0, int(attributes.get("atk") or 0)),
        defense=max(0, int(attributes.get("def") or 0)),
        magic_resistance=max(0, min(100, magic_resistance)),
        # 游击队萨卡兹术师 的天赋 says 「**不进行普通攻击**」: the aura is
        # the whole of it.  Its shipped `rangeRadius` is 0.0, which would
        # otherwise have compiled into an ordinary melee swing it never
        # makes.
        attack_mode=(
            "none"
            if immolation is not None
            or born_bugs is not None
            or crossroads is not None
            or bomb_run is not None
            else ("ranged" if range_radius > 1.0 else "melee")
        ),
        damage_type="physical",
        attack_range_milli_tiles=max(0, round(range_radius * 1000)),
        attack_interval_ticks=interval_ticks,
        move_milli_tiles_per_tick=per_tick,
        move_milli_tiles_per_second=per_second,
        life_damage=max(0, life_damage),
        movement_layer=MovementLayer.FLYING if motion == 1 else MovementLayer.GROUND,
        mechanic_coefficients=coefficients,
        weakness_protected_side=weakness_protected_side_from_description(
            unwrap(data.get("description"))
        ),
        black_fog_range_offsets=black_fog_range_offsets(
            unwrap(data.get("talentBlackboard"))
        ),
        hologram_enemy_id=hologram_enemy_id(
            unwrap(data.get("talentBlackboard"))
        ),
        death_transform_enemy_id=death_transform_enemy_id(
            data.get("talentBlackboard")
        ),
        on_attack_block_count_delta=(
            (reduce_block_rows(data.get("talentBlackboard")) or (0, 0))[0]
        ),
        on_attack_block_count_duration_ticks=round(
            (reduce_block_rows(data.get("talentBlackboard")) or (0, 0))[1] * tps
        ),
        frozen_target_attack_scale_micros=frozen_target_attack_scale(
            unwrap(data.get("description")), data.get("talentBlackboard")
        ),
        death_unload_count=death_unload_count(data.get("talentBlackboard")),
        death_unload_enemy_id=(
            death_unload_enemy_id(
                unwrap(data.get("description")), enemy_names or {}
            )
            if death_unload_count(data.get("talentBlackboard"))
            else None
        ),
        grants_energy=is_energy_charger(unwrap(data.get("description"))),
        # 岗哨密探 is excluded on purpose: it is one of the disguised trio,
        # whose stealth is a *phase* of an identity mechanic the engine already
        # runs (`revealed_invisible`).  Setting the flat trait as well would
        # hide it before it has revealed itself.
        stealthed=(
            enemy_id not in MAINLINE12_DISGUISED_IDS
            and (
                enemy_is_stealthed(unwrap(data.get("description")))
                or "invisible" in granted_abilities
            )
        ),
        pulse_immune=pulse_immune(unwrap(data.get("description"))),
        ranged_while_unblocked=ranged_while_unblocked(
            unwrap(data.get("description"))
        ),
        pulse_empowered_damage_type=pulse_empowered_damage_type(
            unwrap(data.get("description"))
        ),
        unblocked_physical_evade_probability_milli=unblocked_physical_evade(
            unwrap(data.get("description")), data.get("talentBlackboard")
        ),
        unblocked_physical_evade_rng_stream=(
            f"enemy-evade:{enemy_id}"
            if unblocked_physical_evade(
                unwrap(data.get("description")), data.get("talentBlackboard")
            )
            else None
        ),
        element_damage_type=elemental_damage_type(
            unwrap(data.get("description"))
        ),
        charged_skill=charged_enemy_skill(data, tps, enemy_id),
        victory_buff=victory_self_buff(data, tps),
        takeoff=takeoff_skill(data, tps),
        conditional_takeoff=conditional_takeoff_skill(data, tps),
        overload=overload_skill(data, tps),
        self_rage=self_rage_talent(data, tps),
        predation=predation_talent(data, enemy_names, tps),
        tactical_command=tactical_command_talent(data),
        environmental_fragile=environmental_fragile_talent(data, tps),
        stealth_strike_scale_micros=scaled_stealth_strike,
        guided_strike=guided_strike_skill(data, tps, skill_factors),
        death_life_point_cost=death_life_point_cost(data),
        backstab=backstab_skill(data, tps),
        blood_hit=blood_hit_skill(data, tps),
        four_direction_volley=four_direction_volley(data, tps),
        ice_bomb=ice_bomb_skill(data, tps),
        spawn_decay=spawn_decay_talent(data, tps),
        blink=blink_skill(data, tps),
        chain_buff=chain_buff_skill(data, tps),
        doom=doom_skill(data, tps),
        reborning=reborning_skill(data, tps),
        immolation=immolation,
        repair=repair_skill(data, tps),
        flame=flame_skill(data, tps),
        born_bugs=born_bugs,
        dies_after_attacking=dies_after_attacking(data.get("description")),
        beam_weakness=beam_weakness_talent(data),
        beam_splash=beam_splash_talent(data),
        beam_conduction=beam_conduction_talent(data),
        crossroads_volley=crossroads,
        bomb_run=bomb_run,
        armour_charge_sp=driver_charge_sp(data),
        devour=devour_skill(data, tps),
        energy_charge=energy_charge_skill(data, tps),
        immunities=enemy_immunities(attributes),
    )
    return definition, notes, declared, rune_notes


# --------------------------------------------------------------------------- #
# special tiles
# --------------------------------------------------------------------------- #


#: A tile whose blackboard carries only a stat change describes a *continuous*
#: effect while a unit stands on it, but SpecialTileDefinition models pulses.
#: Re-applying a 2-tick modifier every tick is the closest faithful encoding.
#: Neither number is in the shipped data, so the frame boundary is registered
#: as a pending alignment rather than presented as source-exact.
CONTACT_PULSE_INTERVAL_TICKS = 1
CONTACT_PULSE_DURATION_TICKS = 2

#: Pollution tiles carry a damage amount with no cadence.  One pulse per second
#: is the default; the shipped data does not state a rate.
CONTACT_DAMAGE_INTERVAL_TICKS = 30

#: Blackboard stat names on special tiles mapped to engine stats.
TILE_STAT_TO_EFFECT_STAT = {
    "def": EffectStat.DEFENSE,
    "atk": EffectStat.ATTACK,
    "magic_resistance": EffectStat.MAGIC_RESISTANCE,
}


def _stat_tile_effects(
    tile_key: str,
    blackboard: dict[str, Any],
    col: int,
    row: int,
    tps: int,
) -> list[SpecialTileStatModifierDefinition]:
    """Build stat-modifier effects for a tile whose blackboard is a stat change.

    `tile_defbreak` ships `def: 0.5`, i.e. defence is *multiplied* by 0.5 while
    a unit stands on the cracked ground -- a ratio, not a flat subtraction,
    because the value is between 0 and 1 while defence values are in the
    hundreds.
    """

    effects: list[SpecialTileStatModifierDefinition] = []
    for name, value in sorted(blackboard.items()):
        if not isinstance(value, (int, float)):
            continue

        if name == "attack_speed":
            # Shipped attack_speed is a percentage against a 100 baseline while
            # the engine stores an interval, so a -50 speed penalty *doubles*
            # the interval: interval' = interval * 100 / (100 + value).
            speed = float(value)
            if speed <= -100 or speed == 0:
                continue
            multiplier = 100.0 / (100.0 + speed)
            milli = int(round(multiplier * 1000))
            if not 0 <= milli <= 10000:
                continue
            effects.append(
                SpecialTileStatModifierDefinition(
                    effect_id=f"{tile_key}@{col},{row}:{name}",
                    stat=EffectStat.ATTACK_INTERVAL_TICKS,
                    multiplier_milli=milli,
                    duration_ticks=CONTACT_PULSE_DURATION_TICKS,
                )
            )
            continue

        stat = TILE_STAT_TO_EFFECT_STAT.get(name)
        if stat is None:
            continue
        if not 0 < float(value) < 1:
            # Outside a ratio's range the reading is unclear; report instead.
            continue
        effects.append(
            SpecialTileStatModifierDefinition(
                effect_id=f"{tile_key}@{col},{row}:{name}",
                stat=stat,
                multiplier_milli=int(round(float(value) * 1000)),
                duration_ticks=CONTACT_PULSE_DURATION_TICKS,
            )
        )
    return effects


def _compile_special_tiles(
    by_position: dict[tuple[int, int], dict[str, Any]],
    tps: int,
) -> tuple[list[SpecialTileDefinition], list[UnrepresentedConstruct]]:
    """Compile periodic-damage tiles from their shipped blackboards.

    ``tile_volcano`` and friends carry ``damage`` plus ``cd_min``/``cd_max`` in
    the stage file, which lines up exactly with
    :class:`SpecialTileDefinition`.  Tiles whose blackboard is a stat change
    rather than damage are reported instead of approximated.
    """

    compiled: list[SpecialTileDefinition] = []
    unrepresented: dict[str, int] = {}
    for (col, row), tile in sorted(by_position.items()):
        blackboard = _blackboard(tile.get("blackboard"))
        if not blackboard:
            continue
        key = tile.get("tileKey") or "tile_unknown"
        damage = blackboard.get("damage")
        cd_min = blackboard.get("cd_min")
        cd_max = blackboard.get("cd_max")

        if damage is None and cd_min is None:
            # A pure stat tile: tile_defbreak ships only `def: 0.5`.  The value
            # is source-exact; the pulse interval and duration are not in the
            # data at all, so they use the continuous-contact default below and
            # the frame boundary is registered as a pending alignment.
            effects = _stat_tile_effects(key, blackboard, col, row, tps)
            if effects:
                compiled.append(
                    SpecialTileDefinition(
                        tile_id=f"{key}@{col},{row}",
                        position=GridPoint(col=col, row=row),
                        interval_ticks=CONTACT_PULSE_INTERVAL_TICKS,
                        target="friendlies",
                        effects=tuple(effects),
                    )
                )
                continue
            unrepresented[key] = unrepresented.get(key, 0) + 1
            continue

        if damage is not None and cd_min is None:
            # Pollution tiles ship `damage` and `attack_speed` but no cadence.
            # The damage amount is source-exact; the pulse rate is not in the
            # data, so it uses the continuous-contact default and is registered
            # as a pending alignment rather than presented as exact.
            effects = _stat_tile_effects(key, blackboard, col, row, tps)
            compiled.append(
                SpecialTileDefinition(
                    tile_id=f"{key}@{col},{row}",
                    position=GridPoint(col=col, row=row),
                    damage=max(0, int(round(float(damage)))),
                    interval_ticks=CONTACT_DAMAGE_INTERVAL_TICKS,
                    target="friendlies",
                    damage_type="true",
                    effects=tuple(effects),
                )
            )
            continue

        if damage is None or cd_min is None:
            unrepresented[key] = unrepresented.get(key, 0) + 1
            continue
        if cd_max is not None and float(cd_max) != float(cd_min):
            # A randomised interval needs an RNG draw per pulse; the schema
            # takes a single interval, so report rather than pick one end.
            unrepresented[key] = unrepresented.get(key, 0) + 1
            continue
        interval = to_ticks(cd_min, tps)
        if interval <= 0:
            unrepresented[key] = unrepresented.get(key, 0) + 1
            continue
        compiled.append(
            SpecialTileDefinition(
                tile_id=f"{key}@{col},{row}",
                position=GridPoint(col=col, row=row),
                damage=max(0, int(round(float(damage)))),
                interval_ticks=interval,
                target="ground_enemies",
                damage_type="true",
            )
        )
    reports = [
        UnrepresentedConstruct(
            kind="tile_semantic",
            key=key,
            count=count,
            reason=(
                "tile blackboard is a stat change or a randomised interval; "
                "SpecialTileDefinition takes one fixed interval and a damage or healing amount"
            ),
        )
        for key, count in sorted(unrepresented.items())
    ]
    return compiled, reports


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def compile_stage(
    level_path: str | Path,
    enemy_database: dict[str, list[dict[str, Any]]],
    *,
    operators: Sequence[OperatorDefinition] = (),
    client_version: str,
    tps: int = DEFAULT_TPS,
    stage_id: str | None = None,
    node_variant: Literal["normal", "raid"] = "normal",
    character_table: Mapping[str, Mapping[str, Any]] | None = None,
    skill_table: Mapping[str, Mapping[str, Any]] | None = None,
    held_relics: Sequence[str] = (),
    relic_table: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[ContentBundle, CompileReport]:
    """Compile one shipped stage file into a bundle plus an honest report.

    ``node_variant`` selects which stage runes apply.  IS3 serves both the
    ordinary and the emergency (raid) form of a node from the same file, and
    36/36 ordinary combat stages carry an ``enemy_attribute_mul`` rune under
    ``difficultyMask=2`` -- up to 2.5x enemy HP.  Compiling without choosing a
    variant silently produces the ordinary form.
    """

    path = Path(level_path)
    raw = path.read_bytes()
    stage = json.loads(raw.decode("utf-8"))
    resolved_stage_id = stage_id or path.stem.removeprefix("level_")
    if node_variant != "normal":
        resolved_stage_id = f"{resolved_stage_id}#{node_variant}"
    # Enemy display name -> id, for the mechanisms whose description names a
    # unit rather than carrying its id (家族专用轿车's unloaded passengers).
    enemy_names: dict[str, str] = {}
    for candidate_id, levels in enemy_database.items():
        for level in levels:
            name = unwrap(((level.get("enemyData") or {}).get("name")))
            if isinstance(name, str) and name:
                enemy_names.setdefault(name, candidate_id)
    runes = stage_rune_modifiers(stage, node_variant=node_variant)
    # Relics are fixed for the length of a battle, so they fold in here
    # beside the runes rather than becoming a roguelike layer in the engine.
    relic_enemy_modifiers = None
    relic_operator_modifiers = None
    if held_relics:
        relic_enemy_modifiers, relic_operator_modifiers = relic_modifiers(
            held_relics, relic_table or {}
        )
    (
        enemy_replacements,
        rune_max_cost,
        rune_life_points,
        level_rune_keys,
        level_rune_notes,
    ) = _level_scope_runes(stage, node_variant)

    map_definition, by_position, walkable = _compile_map(stage)
    height = map_definition.height
    forbidden, forbid_applied, forbid_notes = _forbidden_deploy_points(
        stage, node_variant, map_definition.width, height
    )
    if forbidden:
        kept_ground = tuple(
            spot for spot in map_definition.ground_tiles
            if (spot.col, spot.row) not in forbidden
        )
        kept_high = tuple(
            spot for spot in map_definition.high_ground_tiles
            if (spot.col, spot.row) not in forbidden
        )
        removed = len(map_definition.ground_tiles) + len(map_definition.high_ground_tiles) - (
            len(kept_ground) + len(kept_high)
        )
        for spot in sorted(forbidden):
            tile = (by_position.get(spot) or {}).get("tileKey", "OUT")
            buildable = int((by_position.get(spot) or {}).get("buildableType") or 0)
            if buildable == 0:
                forbid_notes.append(
                    f"global_forbid_location: {spot} is {tile} (not deployable) -- "
                    "forbidding it changes nothing; row orientation may be inverted"
                )
        map_definition = map_definition.model_copy(
            update={
                "ground_tiles": kept_ground,
                "high_ground_tiles": kept_high,
                "deployable_tiles": kept_ground + kept_high,
            }
        )
        forbid_notes.append(f"global_forbid_location: {removed} deployable tiles dropped")

    spawn_events = _compile_spawns(stage, tps)
    if enemy_replacements:
        declared_ids = {ref["id"] for ref in stage.get("enemyDbRefs") or []}
        for event in spawn_events:
            target = enemy_replacements.get(event["enemy_id"])
            if target is None:
                continue
            if target not in declared_ids:
                raise ValueError(
                    f"level_enemy_replace names {target!r}, which the stage does not declare"
                )
            event["enemy_id"] = target
    source_waves = _compile_waves(stage, tps)

    unrepresented: list[UnrepresentedConstruct] = []
    missing: list[MissingSourceField] = []

    # ---- routes: only those a spawn actually uses become runtime routes.
    raw_routes = stage.get("routes") or []
    used_indices = sorted({int(event["source_route_index"]) for event in spawn_events})
    # Event branches spawn on their own routes, which no wave action references.
    # Leaving them out compiled the branch table with 84 of its 85 spawns
    # dropped as "route index is unknown", so those routes are compiled too --
    # this is why they used to be reported as `route_of_branch_phase`
    # unrepresented rather than being usable.
    branch_route_indices = {
        int(action.get("routeIndex") or 0)
        for branch in (stage.get("branches") or {}).values()
        for phase in (branch.get("phases") or [])
        for action in (phase.get("actions") or [])
        if int(action.get("actionType", -1)) == SPAWN_ACTION_TYPE and action.get("key")
    }
    branch_only_indices = sorted(
        index
        for index in branch_route_indices
        if index not in set(used_indices)
        and index < len(stage.get("routes") or [])
    )
    routes: list[RouteDefinition] = []
    route_ids: dict[int, str] = {}
    non_runtime = 0
    sentinel_positions = 0
    for index in used_indices:
        if index >= len(raw_routes):
            raise ValueError(f"spawn references route {index} but the stage has {len(raw_routes)}")
        definition, flagged, sentinels = _compile_route(
            raw_routes[index], index, map_definition.width, height, walkable
        )
        routes.append(definition)
        route_ids[index] = definition.route_id
        non_runtime += 1 if flagged else 0
        sentinel_positions += sentinels
    # Branch routes, compiled tolerantly: some are degenerate (a single
    # waypoint) and a whole stage must not fail to compile because an event
    # branch's route is not runtime-valid geometry.  A skipped one leaves its
    # branch spawn unrepresented, which the branch table then reports.
    degenerate_branch_routes = 0
    for index in branch_only_indices:
        try:
            definition, flagged, sentinels = _compile_route(
                raw_routes[index], index, map_definition.width, height, walkable
            )
        except ValueError:
            degenerate_branch_routes += 1
            continue
        routes.append(definition)
        route_ids[index] = definition.route_id
        non_runtime += 1 if flagged else 0
        sentinel_positions += sentinels
    if degenerate_branch_routes:
        unrepresented.append(
            UnrepresentedConstruct(
                kind="route",
                key="route_of_branch_phase_degenerate",
                count=degenerate_branch_routes,
                reason=(
                    "an event branch spawns on a route that is not runtime-valid "
                    "geometry (fewer than two waypoints); the branch's spawns on "
                    "that route are therefore not represented"
                ),
            )
        )
    if non_runtime:
        unrepresented.append(
            UnrepresentedConstruct(
                kind="route",
                key="route_teleport_approximated",
                count=non_runtime,
                reason=(
                    "no walkable path joins the anchors because the route teleports: every such "
                    "route in IS3 carries type-5/6 checkpoints (203/203), so this is the teleport "
                    "gap already counted as tile_telin/tile_telout, not a separate route defect. "
                    "The compatibility walk is an L-shaped approximation and the route is flagged "
                    "source_non_runtime_route"
                ),
            )
        )

    # Routes the stage defines but no enemy spawn uses.  Classifying these
    # matters: a flat count reads as ~591 missing routes, when in fact every one
    # is referenced by a construct already counted elsewhere.
    unused_indices = sorted(set(range(len(raw_routes))) - set(used_indices))
    if unused_indices:
        device_routes: set[int] = set()
        non_spawn_routes: set[int] = set()
        empty_outcome_routes: set[int] = set()
        for wave in stage.get("waves") or []:
            for fragment in wave.get("fragments") or []:
                for action in fragment.get("actions") or []:
                    index = action.get("routeIndex")
                    if index is None or int(index) not in set(unused_indices):
                        continue
                    kind = int(action.get("actionType", -1))
                    if kind == SPAWN_ACTION_TYPE and not str(action.get("key") or ""):
                        empty_outcome_routes.add(int(index))
                    elif kind == 6:
                        device_routes.add(int(index))
                    elif kind in {1, 5}:
                        non_spawn_routes.add(int(index))
        branch_routes: set[int] = set()
        for branch in (stage.get("branches") or {}).values():
            for phase in branch.get("phases") or []:
                for action in phase.get("actions") or []:
                    index = action.get("routeIndex")
                    if index is not None and int(index) in set(unused_indices):
                        branch_routes.add(int(index))

        if device_routes:
            unrepresented.append(
                UnrepresentedConstruct(
                    kind="route",
                    key="route_of_device_activation",
                    count=len(device_routes),
                    reason=(
                        "route belongs to an actionType-6 action whose key is a predefined token "
                        "instance (trap_*#N, matching predefines.tokenInsts), i.e. a device "
                        "activation -- counted with the device gap, not an enemy-spawn gap"
                    ),
                )
            )
        if branch_routes:
            unrepresented.append(
                UnrepresentedConstruct(
                    kind="route",
                    key="route_of_branch_phase",
                    count=len(branch_routes),
                    reason=(
                        "route is referenced only from a branch phase (event-triggered spawn) -- "
                        "counted with the branch gap"
                    ),
                )
            )
        if non_spawn_routes:
            unrepresented.append(
                UnrepresentedConstruct(
                    kind="route",
                    key="route_of_non_spawn_action",
                    count=len(non_spawn_routes),
                    reason=(
                        "route belongs to an actionType-1/5 action, which does not spawn: 27/27 "
                        "type-5 and 18/20 type-1 enemy keys also appear in a type-0 spawn, and the "
                        "two type-1-only keys are absent from enemyDbRefs so no data exists to "
                        "spawn them. Informational, not a gap"
                    ),
                )
            )
        if empty_outcome_routes:
            unrepresented.append(
                UnrepresentedConstruct(
                    kind="route",
                    key="route_of_empty_group_outcome",
                    count=len(empty_outcome_routes),
                    reason=(
                        "route belongs to an actionType-0 action with an empty key: the "
                        "'nothing spawns' arm of a weighted random spawn group. It carries a "
                        "weight and so affects the group's probability normalisation, but emits "
                        "no enemy -- informational, not a behaviour gap"
                    ),
                )
            )
        accounted = (
            device_routes | branch_routes | non_spawn_routes | empty_outcome_routes
        )
        remainder = [index for index in unused_indices if index not in accounted]
        if remainder:
            unrepresented.append(
                UnrepresentedConstruct(
                    kind="route",
                    key="route_unreferenced",
                    count=len(remainder),
                    reason="route is defined but no wave action or branch phase references it",
                )
            )
    if stage.get("extraRoutes"):
        unrepresented.append(
            UnrepresentedConstruct(
                kind="route",
                key="extraRoutes_unconsumed",
                count=len(stage["extraRoutes"]),
                reason=(
                    "extraRoutes are all motionMode 0 and appear as a uniform pair in 38 stages "
                    "with no tile_flystart and no wave/branch reference (routeIndex indexes "
                    "`routes`), so no in-battle behaviour is attributable to them -- structural, "
                    "not a behaviour gap"
                ),
            )
        )

    # ---- enemies referenced by spawns.
    wanted = {event["enemy_id"] for event in spawn_events}
    by_id = {ref["id"]: ref for ref in stage.get("enemyDbRefs") or []}
    enemies: list[EnemyDefinition] = []
    talent_keys: dict[str, int] = {}
    skill_keys: dict[str, int] = {}
    missing_attr: dict[str, int] = {}
    layered_levels = 0
    rune_applications: list[str] = []
    relic_applications: list[str] = []
    declared_mechanics: dict[tuple[str, str], Any] = {}
    for enemy_id in sorted(wanted):
        ref = by_id.get(enemy_id)
        if ref is None:
            raise ValueError(f"stage spawns {enemy_id!r} but declares no enemyDbRefs entry for it")
        if int(ref.get("level") or 0) > 0:
            layered_levels += 1
        data = _enemy_source(ref, enemy_database)
        definition, notes, declared, rune_notes = _compile_enemy(
            enemy_id,
            data,
            tps,
            runes.for_enemy(enemy_id),
            relic_enemy_modifiers,
            enemy_names,
            talent_addends=runes.enemy_talent_addends.get(enemy_id),
            talent_multipliers={
                **runes.enemy_talent_multipliers,
                **runes.enemy_talent_multipliers_by_enemy.get(enemy_id, {}),
            },
            granted_abilities=frozenset(
                runes.enemy_granted_abilities.get(enemy_id) or ()
            ),
            skill_blackboard_factors=(
                runes.enemy_skill_blackboard_multipliers.get(enemy_id)
            ),
        )
        rune_applications.extend(rune_notes)
        enemies.append(definition)
        for item in declared:
            declared_mechanics[(item.group, item.enemy_id)] = item
        for note in notes:
            missing_attr[note] = missing_attr.get(note, 0) + 1
        # Count only the occurrences nothing consumed.  The same key can be a
        # gap on one carrier and implemented on another -- `atkup.atk_scale` is
        # exactly that -- so consumption is decided per enemy, not per key.
        consumed = consumed_talent_keys(
            data,
            frozen_target_scale_micros=definition.frozen_target_attack_scale_micros,
            definition=definition,
        )
        for entry in data.get("talentBlackboard") or []:
            key = entry["key"]
            if key in consumed:
                continue
            talent_keys[key] = talent_keys.get(key, 0) + 1
        for skill in data.get("skills") or []:
            name = skill.get("prefabKey") or "?"
            skill_keys[name] = skill_keys.get(name, 0) + 1

    if layered_levels:
        missing.append(
            MissingSourceField(
                field_path="enemy_database[level>0].skills / .talentBlackboard",
                used_instead="inherited from the level-0 record when the upper level lists are empty",
                reason=(
                    f"{layered_levels} enemies are referenced at level>0; those records redefine only "
                    "the stat block and leave list fields empty, which carries no m_defined flag. "
                    "Empty is read as inherit, consistent with every scalar in the same record, but "
                    "the data does not state it -- registered as a pending alignment"
                ),
            )
        )
    for name, count in sorted(missing_attr.items()):
        missing.append(
            MissingSourceField(
                field_path=f"enemy_database.attributes.{name}",
                used_instead="schema minimum (interval 1 tick / speed 1 milli-tile / range 0)",
                reason=(
                    "the shipped record leaves this attribute undefined (m_defined=false) or -1; "
                    "the prefab default it falls back to is not in the shipped data"
                ),
            )
        )
    for key, count in sorted(talent_keys.items()):
        unrepresented.append(
            UnrepresentedConstruct(
                kind="enemy_talent",
                key=key,
                count=count,
                reason=talent_key_gap_reason(key),
            )
        )
    for key, count in sorted(skill_keys.items()):
        unrepresented.append(
            UnrepresentedConstruct(
                kind="enemy_skill",
                key=key,
                count=count,
                reason="no mechanism layer consumes enemy skill prefabs",
            )
        )

    # ---- constructs with no schema home at all.
    applied_rune_keys = set(runes.applied) | set(level_rune_keys) | set(forbid_applied)
    wanted_bit = 1 if node_variant == "normal" else 2
    for rune in stage.get("runes") or []:
        key = str(rune.get("key") or "?")
        if key in applied_rune_keys:
            # Folded into the compiled enemy stats above; not a gap.
            continue
        if not int(rune.get("difficultyMask") or 0) & wanted_bit:
            # Belongs to the other node variant.  Reporting it as unrepresented
            # would count the emergency runes as gaps in every ordinary compile.
            unrepresented.append(
                UnrepresentedConstruct(
                    kind="rune",
                    key=f"{key}@other_variant",
                    count=1,
                    reason=(
                        f"rune difficultyMask={rune.get('difficultyMask')} excludes the "
                        f"compiled node_variant={node_variant}; compile the other variant "
                        "to apply it. Not a capability gap"
                    ),
                )
            )
            continue
        unrepresented.append(
            UnrepresentedConstruct(
                kind="rune",
                key=key,
                count=1,
                reason=(
                    f"stage rune (difficultyMask={rune.get('difficultyMask')}) has no ContentBundle field; "
                    "difficulty and emergency-operation modifiers therefore do not reach the engine"
                ),
            )
        )
    devices: dict[str, int] = {}
    for token in (stage.get("predefines") or {}).get("tokenInsts") or []:
        key = (token.get("inst") or {}).get("characterKey") or token.get("characterKey") or "?"
        devices[key] = devices.get(key, 0) + 1
    for key, count in sorted(devices.items()):
        unrepresented.append(
            UnrepresentedConstruct(
                kind="device",
                key=key,
                count=count,
                reason="predefined stage devices have no ContentBundle representation",
            )
        )
    branches = stage.get("branches") or {}
    if branches:
        unrepresented.append(
            UnrepresentedConstruct(
                kind="branch",
                key="branches",
                count=len(branches),
                reason="event-triggered spawn branches are not part of the wave schedule",
            )
        )
    for buff in stage.get("globalBuffs") or []:
        unrepresented.append(
            UnrepresentedConstruct(
                kind="global_buff",
                key=str(buff.get("prefabKey") or "?"),
                count=1,
                reason="stage global buffs have no ContentBundle representation",
            )
        )

    options = stage.get("options") or {}
    move_multiplier = float(options.get("moveMultiplier") or 1.0)
    if move_multiplier != 1.0:
        unrepresented.append(
            UnrepresentedConstruct(
                kind="option",
                key="moveMultiplier",
                count=1,
                reason=(
                    f"stage scales all movement by {move_multiplier}; the bundle carries no "
                    "equivalent, so enemy speeds are compiled unscaled"
                ),
            )
        )
    if options.get("steeringEnabled", False):
        unrepresented.append(
            UnrepresentedConstruct(
                kind="option",
                key="steeringEnabled",
                count=1,
                reason="cornering interpolation is not modelled; movement is straight between waypoints",
            )
        )

    special_tiles, tile_reports = _compile_special_tiles(by_position, tps)
    unrepresented.extend(tile_reports)

    # ---- options that do map onto the schema.
    initial_cost = int(options.get("initialCost") or 0)
    max_cost = int(options.get("maxCost") or 99)
    if rune_max_cost is not None:
        max_cost = rune_max_cost
    cost_interval = to_ticks(options.get("costIncreaseTime") or 1.0, tps) or tps
    life = int(options.get("maxLifePoint") or 1)
    if rune_life_points is not None:
        life = rune_life_points
    if life >= 99:
        missing.append(
            MissingSourceField(
                field_path="options.maxLifePoint",
                used_instead=f"{life} taken verbatim",
                reason=(
                    "IS3 holds the real life total in the roguelike run layer; the stage value "
                    "99/100 is an unbounded placeholder, so leak budgets from it are not meaningful"
                ),
            )
        )

    spawns = tuple(
        SpawnDefinition(
            spawn_id=event["spawn_id"],
            tick=event["tick"],
            enemy_id=event["enemy_id"],
            route_id=route_ids[event["source_route_index"]],
            count=event["count"],
            interval_ticks=event["interval_ticks"],
            source_wave_index=event["source_wave_index"],
            source_fragment_index=event["source_fragment_index"],
            source_action_index=event["source_action_index"],
            wave_pre_delay_ticks=event["wave_pre_delay_ticks"],
            fragment_pre_delay_ticks=event["fragment_pre_delay_ticks"],
            action_pre_delay_ticks=event["action_pre_delay_ticks"],
            random_spawn_group_key=event["random_spawn_group_key"],
            random_spawn_group_pack_key=event["random_spawn_group_pack_key"],
            hidden_group=event["hidden_group"],
            selection_probability_micros=event["selection_probability_micros"],
            not_count_in_total=event["not_count_in_total"],
        )
        for event in spawn_events
    )

    # The bundle validator cross-checks content against declared mechanisms, so
    # the declaration is derived from what was actually compiled rather than
    # hardcoded -- a stage with flying routes or teleport checkpoints must say so.
    required = list(CORE_MECHANISMS)
    if any(route.movement_layer is MovementLayer.FLYING for route in routes) or any(
        enemy.movement_layer is MovementLayer.FLYING for enemy in enemies
    ):
        required.append("flying")
    if any(route.runtime_teleports for route in routes):
        required.append("route_teleport_metadata")
    if map_definition.ground_tiles or map_definition.high_ground_tiles:
        required.append("deployment_classes")
    if special_tiles:
        required.append("special_tiles")
    if any(tile.effects for tile in special_tiles):
        required.append("special_tile_stat_or_control_effects")
        required.append("buffs_debuffs")
    if source_waves:
        required.append("source_wave_schedule")
    if any(enemy.mechanic_coefficients for enemy in enemies):
        required.append("enemy_block_constraint_coefficients")

    # Event branches: a device names one in its `overrideSkillBlackboard`, and
    # destroying the device fires it.  Only the spawn actions are represented;
    # a branch naming an enemy the bundle does not carry is reported rather than
    # dropped, the same rule the unload passenger follows.
    # A carrier that unloads passengers on death must ship their definition.
    # The passenger is genuinely part of the stage's content -- t-3 lists
    # `enemy_1277_sgman` in its own `enemyDbRefs` -- it just is not *spawned*,
    # and the compiler only walks spawned enemies.  Without this the engine
    # would raise "passenger disappeared at runtime" the first time a car died
    # while blocked.
    compiled_ids = {enemy.enemy_id for enemy in enemies}
    for carrier in list(enemies):
        passenger_id = carrier.death_unload_enemy_id
        if passenger_id is None or passenger_id in compiled_ids:
            continue
        passenger_ref = next(
            (
                ref
                for ref in (stage.get("enemyDbRefs") or [])
                if ref.get("id") == passenger_id
            ),
            None,
        )
        if passenger_ref is None:
            # Named by the description but absent from the stage: report it and
            # leave the carrier without the mechanism rather than inventing a
            # definition out of the database.
            missing_attr[
                f"{carrier.enemy_id}: unload passenger {passenger_id} is not in enemyDbRefs"
            ] = 1
            continue
        passenger_definition, _notes, _declared, _rune_notes = _compile_enemy(
            passenger_id,
            _enemy_source(passenger_ref, enemy_database),
            tps,
            runes.for_enemy(passenger_id),
            relic_enemy_modifiers,
            enemy_names,
        )
        enemies.append(passenger_definition)
        compiled_ids.add(passenger_id)

    # 枯朽萃聚使徒's seeds follow the same rule: 5-2 lists `enemy_1269_nhfly` in
    # its own `enemyDbRefs` and no wave ever spawns it, because the apostle is
    # what puts it on the board.
    for carrier in list(enemies):
        born = carrier.born_bugs
        if born is None or born.summon_enemy_id in compiled_ids:
            continue
        seed_ref = next(
            (
                ref
                for ref in (stage.get("enemyDbRefs") or [])
                if ref.get("id") == born.summon_enemy_id
            ),
            None,
        )
        if seed_ref is None:
            missing_attr[
                f"{carrier.enemy_id}: summoned {born.summon_enemy_id} is not in enemyDbRefs"
            ] = 1
            continue
        seed_definition, _notes, _declared, _rune_notes = _compile_enemy(
            born.summon_enemy_id,
            _enemy_source(seed_ref, enemy_database),
            tps,
            runes.for_enemy(born.summon_enemy_id),
            relic_enemy_modifiers,
            enemy_names,
        )
        enemies.append(seed_definition)
        compiled_ids.add(born.summon_enemy_id)

    folded_operators, operator_relic_notes = _apply_relics_to_operators(
        operators, relic_operator_modifiers
    )
    relic_applications.extend(operator_relic_notes)
    folded_operators, skill_cooldown_notes = _apply_skill_cooldown_scale(
        folded_operators, runes.operator_skill_cooldown_scale
    )
    rune_applications.extend(skill_cooldown_notes)
    # Predefined tokens: the shipped instances, matched to the wave
    # actions that activate them.  Without a character table there are
    # no stats to give them, so the existing device gap stands.
    token_summons: dict[str, SummonDefinition] = {}
    predefined_tokens: list[PredefinedTokenDefinition] = []
    token_notes: list[str] = []
    if character_table:
        activation_ticks = _compile_token_activations(stage, tps)
        for token in (stage.get("predefines") or {}).get("tokenInsts") or []:
            alias = str(token.get("alias") or "")
            character_key = str(
                (token.get("inst") or {}).get("characterKey") or ""
            )
            record = character_table.get(character_key)
            if not alias or record is None:
                continue
            summon = token_summons.get(character_key)
            if summon is None:
                summon = _token_summon(
                    character_key, record, tps, skill_table
                )
                if summon is not None:
                    scoped = runes.for_char(character_key)
                    if scoped is not None:
                        summon, scoped_notes = _apply_runes_to_summon(
                            summon, scoped
                        )
                        token_notes.extend(scoped_notes)
                if summon is None:
                    token_notes.append(
                        f"{character_key}: no usable attribute frame in character_table"
                    )
                    continue
                # `char_skill_blackb_mul`: the scoped rows and the squad-wide
                # ones both land here, on the device's compiled skill figures.
                skill_factors = dict(runes.char_skill_blackboard_multipliers)
                skill_factors.update(
                    runes.char_skill_blackboard_by_char.get(character_key, {})
                )
                summon, skill_notes = _apply_skill_blackboard_multipliers(
                    summon, skill_factors
                )
                rune_applications.extend(skill_notes)
                token_summons[character_key] = summon
            position = token.get("position") or {}
            point = GridPoint(
                col=int(position.get("col") or 0),
                row=int(position.get("row") or 0),
            )
            if not (
                0 <= point.col < map_definition.width
                and 0 <= point.row < map_definition.height
            ):
                # Reported, never dropped: a device off the map is a real
                # discrepancy, not a rounding detail.  Walkability is *not*
                # required -- devices sit on walls and floors too.
                token_notes.append(
                    f"{alias}: position {point.col},{point.row} is off the map"
                )
                continue
            branch_id = next(
                (
                    str(entry.get("valueStr"))
                    for entry in (token.get("overrideSkillBlackboard") or [])
                    if isinstance(entry, dict)
                    and entry.get("key") == "branch_id"
                    and entry.get("valueStr")
                ),
                None,
            )
            hidden = bool(token.get("hidden"))
            tick = activation_ticks.get(alias)
            if hidden and tick is None and alias in runes.enabled_predefines:
                # Enabled by a `level_predefines_enable` rune instead of a wave
                # action -- the second entry path.  Enabled means present, so
                # it stands from the first tick.
                hidden = False
            if hidden and tick is None:
                # Placed, hidden, and never named by an activating
                # action: it cannot enter play, so it is not put on the
                # board -- and that is said out loud.
                token_notes.append(f"{alias}: hidden with no actionType-6 activation")
                continue
            predefined_tokens.append(
                PredefinedTokenDefinition(
                    alias=alias,
                    summon_id=character_key,
                    branch_id=branch_id,
                    position=point,
                    activation_tick=0 if not hidden else tick,
                    facing=token_facing(token),
                )
            )
    branch_spawns: list[BranchSpawnDefinition] = []
    wanted_branches = {
        token.branch_id for token in predefined_tokens if token.branch_id
    }
    for branch_id, branch in sorted((stage.get("branches") or {}).items()):
        if branch_id not in wanted_branches:
            # A branch nothing names can never fire; left out rather than
            # carried as dead content.
            continue
        for phase in branch.get("phases") or []:
            phase_pre = to_ticks(phase.get("preDelay"), tps)
            for action in phase.get("actions") or []:
                if int(action.get("actionType", -1)) != SPAWN_ACTION_TYPE:
                    continue
                key = str(action.get("key") or "")
                if not key:
                    continue
                route_id = route_ids.get(int(action.get("routeIndex") or 0))
                if route_id is None:
                    token_notes.append(
                        f"branch {branch_id}: route index "
                        f"{action.get('routeIndex')} is unknown"
                    )
                    continue
                branch_spawns.append(
                    BranchSpawnDefinition(
                        branch_id=branch_id,
                        enemy_id=key,
                        route_id=route_id,
                        count=max(1, min(16, int(action.get("count") or 1))),
                        pre_delay_ticks=phase_pre
                        + to_ticks(action.get("preDelay"), tps),
                        interval_ticks=to_ticks(action.get("interval"), tps),
                    )
                )

    # Declared last, because the token pass below adds to `required`: a stage
    # placing a route obstacle must say so, and that is not known until the
    # tokens are read.
    if any(summon.blocks_routes for summon in token_summons.values()):
        required.append("dynamic_obstacles")
    if any(getattr(operator, "skills", ()) for operator in folded_operators):
        # The roster comes from the caller, so whether the bundle contains
        # skills is not knowable from the stage.  Without this the compiler
        # accepted a roster with skills and then refused to build one --
        # 「bundles containing skills must require the skills mechanism」 -- which
        # is how `char_skill_cd_mul` turned out to be untestable rather than
        # unimplementable.
        required.append("skills")
    fidelity = tuple(
        MechanismFidelity(
            mechanism=name,
            level=FidelityLevel.C,
            note="compiled from shipped stage data; not calibrated tick-by-tick against the client",
        )
        for name in dict.fromkeys(required)
    )

    bundle = ContentBundle(
        bundle_id=resolved_stage_id,
        required_mechanisms=tuple(dict.fromkeys(required)),
        mechanism_fidelity=fidelity,
        client_version=client_version,
        source=path.as_posix(),
        generator_version="arknights-tournament-sim/stage-compiler-v1",
        stage_id=resolved_stage_id,
        map=map_definition,
        routes=tuple(routes),
        spawns=spawns,
        source_waves=source_waves,
        enemies=tuple(enemies),
        operators=folded_operators,
        summons=tuple(
            token_summons[key] for key in sorted(token_summons)
        ),
        predefined_tokens=tuple(
            sorted(predefined_tokens, key=lambda item: item.alias)
        ),
        branch_spawns=tuple(branch_spawns),
        special_tiles=tuple(special_tiles),
        ticks_per_second=tps,
        initial_cost=initial_cost,
        max_cost=max_cost,
        cost_recovery_interval_ticks=cost_interval,
        initial_life_points=max(1, life),
    )

    invariants = _check_invariants(stage, map_definition, by_position, routes, spawn_events, height)
    report = CompileReport(
        stage_id=resolved_stage_id,
        source_path=path.as_posix(),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        ticks_per_second=tps,
        map_width=map_definition.width,
        map_height=map_definition.height,
        route_count=len(routes),
        spawn_count=len(spawns),
        enemy_count=len(enemies),
        special_tile_count=len(special_tiles),
        invariants=tuple(invariants),
        unrepresented=tuple(unrepresented),
        missing_from_source=tuple(missing),
        node_variant=node_variant,
        rune_applications=(
            tuple(rune_applications) + tuple(level_rune_notes) + tuple(forbid_notes)
        ),
        relic_applications=tuple(relic_applications),
        token_notes=tuple(token_notes),
    )
    return bundle, report


def _check_invariants(
    stage: dict[str, Any],
    map_definition: MapDefinition,
    by_position: dict[tuple[int, int], dict[str, Any]],
    routes: Sequence[RouteDefinition],
    spawn_events: Sequence[dict[str, Any]],
    height: int,
) -> list[InvariantResult]:
    """Self-checks that would catch a wrong row axis or a broken tile index."""

    results: list[InvariantResult] = []

    tiles = (stage.get("mapData") or {}).get("tiles") or []
    expected = map_definition.width * map_definition.height
    results.append(
        InvariantResult(
            name="tile_table_covers_grid",
            passed=len(tiles) == expected,
            detail=f"{len(tiles)} tiles for a {map_definition.width}x{map_definition.height} grid "
            f"(expected {expected})",
        )
    )

    # The axis check.  Requiring every route to begin on a spawn tile is wrong:
    # reinforcements and teleport arrivals legitimately start mid-map, so a
    # strict check fires on correct data.  What *is* decisive is comparing the
    # two candidate row conventions against each other -- the correct one puts
    # far more route origins on actual spawn tiles.  A flipped axis cannot pass
    # this, and legitimate mid-map spawns cannot fail it.
    start_keys = {"tile_start", "tile_flystart"}
    raw_routes = stage.get("routes") or []
    used = sorted({int(event["source_route_index"]) for event in spawn_events})
    hits = {True: 0, False: 0}
    for index in used:
        if index >= len(raw_routes):
            continue
        spot = (raw_routes[index].get("startPosition") or {})
        col, row = spot.get("col"), spot.get("row")
        if not isinstance(col, int) or not isinstance(row, int):
            continue
        for flipped in (True, False):
            resolved = (col, flip_row(row, height) if flipped else row)
            if (by_position.get(resolved) or {}).get("tileKey") in start_keys:
                hits[flipped] += 1
    results.append(
        InvariantResult(
            name="row_axis_orientation",
            # Ties happen only on stages with no usable evidence (e.g. every
            # route starts mid-map); those are reported, not failed.
            passed=hits[True] >= hits[False],
            detail=(
                f"start-tile hits: flipped={hits[True]} vs unflipped={hits[False]} "
                f"over {len(used)} spawn routes (flipped is the compiled convention)"
            ),
        )
    )

    on_start = sum(
        1
        for route in routes
        if (by_position.get((route.waypoints[0].col, route.waypoints[0].row)) or {}).get("tileKey")
        in start_keys
    )
    on_end = sum(
        1
        for route in routes
        if (by_position.get((route.waypoints[-1].col, route.waypoints[-1].row)) or {}).get("tileKey")
        == "tile_end"
    )
    results.append(
        InvariantResult(
            name="route_endpoint_terrain_census",
            passed=True,  # informational: mid-map spawns and exits are legitimate
            detail=(
                f"{on_start}/{len(routes)} routes start on a spawn tile, "
                f"{on_end}/{len(routes)} end on tile_end"
            ),
        )
    )

    referenced = {event["enemy_id"] for event in spawn_events}
    declared = {ref["id"] for ref in stage.get("enemyDbRefs") or []}
    results.append(
        InvariantResult(
            name="spawned_enemies_are_declared",
            passed=referenced <= declared,
            detail=f"{len(referenced - declared)} spawned ids missing from enemyDbRefs",
        )
    )
    return results
