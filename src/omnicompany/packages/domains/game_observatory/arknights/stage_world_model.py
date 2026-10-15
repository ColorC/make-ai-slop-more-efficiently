# [OMNI] origin=codex created_by=codex intent="compile loaded Arknights stage geometry, routes, enemies, and operator placements"
"""Resident text world model for one already-loaded Arknights stage.

The model is compiled once after a stage identity is evidenced.  It keeps the
level-file coordinate conversion, route geometry, operator ranges, and small
numeric comparisons out of the contestant's language-model loop.  It never
discovers or exposes an unloaded stage.

Coordinates in every public result are ``[col, row]`` with the origin at the
top-left, matching the live battle action surface.  Level route rows use a
bottom origin and are converted exactly once here.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_ID = "omnicompany.arknights.loaded-stage-world-model.v1"
MODEL_VERSION = "arknights-stage-world-model/1.0.0"
FACINGS = ("up", "right", "down", "left")
ARRIVAL_BURST_WINDOW_SECONDS = 5.0


class StageWorldModelError(ValueError):
    """Loaded stage or operator data cannot support a truthful model."""


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _point(value: Mapping[str, Any] | None, height: int) -> tuple[int, int] | None:
    if not isinstance(value, Mapping):
        return None
    col, row = value.get("col"), value.get("row")
    if isinstance(col, int) and isinstance(row, int):
        return col, height - 1 - row
    return None


def _predefined_tokens(
    level: Mapping[str, Any], height: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    predefines = level.get("predefines") or {}
    for index, raw in enumerate(predefines.get("tokenInsts") or []):
        if not isinstance(raw, Mapping):
            continue
        inst = raw.get("inst") or {}
        if not isinstance(inst, Mapping):
            inst = {}
        point = _point(raw.get("position"), height)
        rows.append(
            {
                "source_index": index,
                "alias": str(raw.get("alias") or ""),
                "character_id": str(inst.get("characterKey") or ""),
                "phase": int(inst.get("phase") or 0),
                "level": max(1, int(inst.get("level") or 1)),
                "hidden": bool(raw.get("hidden")),
                "position": None if point is None else list(point),
                "direction": int(raw.get("direction") or 0),
            }
        )
    return rows


def _four_connected_segment(
    start: tuple[int, int], end: tuple[int, int]
) -> list[tuple[int, int]]:
    """Rasterize a segment into a deterministic four-neighbour tile walk.

    The client permits diagonal route motion.  A tile coverage query needs a
    finite set, while the existing deterministic simulator requires adjacent
    grid nodes.  Diagonal crossings therefore include both crossed neighbours
    and are explicitly reported as an approximation by the public model.
    """

    x, y = start
    tx, ty = end
    result = [(x, y)]
    dx, dy = tx - x, ty - y
    steps = max(abs(dx), abs(dy))
    if not steps:
        return result
    for index in range(1, steps + 1):
        nx = round(start[0] + dx * index / steps)
        ny = round(start[1] + dy * index / steps)
        while x != nx:
            x += 1 if nx > x else -1
            result.append((x, y))
        while y != ny:
            y += 1 if ny > y else -1
            result.append((x, y))
    return result


def _shortest_passable_segment(
    start: tuple[int, int],
    end: tuple[int, int],
    walkable: set[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    """Find the deterministic four-neighbour road walk between route anchors."""

    allowed = walkable | {start, end}
    frontier = deque([start])
    previous: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    while frontier:
        current = frontier.popleft()
        if current == end:
            break
        col, row = current
        neighbours = [
            (col - 1, row),
            (col + 1, row),
            (col, row - 1),
            (col, row + 1),
        ]
        neighbours.sort(
            key=lambda point: (
                abs(point[0] - end[0]) + abs(point[1] - end[1]),
                point[1],
                point[0],
            )
        )
        for point in neighbours:
            if point not in allowed or point in previous:
                continue
            previous[point] = current
            frontier.append(point)
    if end not in previous:
        raise StageWorldModelError(
            f"ground route anchors are disconnected on passable tiles: {start} -> {end}"
        )
    reversed_path = []
    current: tuple[int, int] | None = end
    while current is not None:
        reversed_path.append(current)
        current = previous[current]
    return tuple(reversed(reversed_path))


def _route_cells(
    anchors: Sequence[tuple[int, int]],
    *,
    walkable: set[tuple[int, int]] | None = None,
) -> tuple[tuple[int, int], ...]:
    if not anchors:
        return ()
    cells = [anchors[0]]
    for start, end in zip(anchors, anchors[1:]):
        segment = (
            _shortest_passable_segment(start, end, walkable)
            if walkable is not None
            else tuple(_four_connected_segment(start, end))
        )
        for cell in segment[1:]:
            if cell != cells[-1]:
                cells.append(cell)
    return tuple(cells)


def _spawn_events(level: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expand static spawn actions with the existing conservative time model."""

    events: list[dict[str, Any]] = []
    wave_clock = 0.0
    for wave_index, wave in enumerate(level.get("waves") or []):
        wave_clock += float(wave.get("preDelay") or 0.0)
        fragment_clock = wave_clock
        for fragment_index, fragment in enumerate(wave.get("fragments") or []):
            fragment_clock += float(fragment.get("preDelay") or 0.0)
            fragment_end = fragment_clock
            actions = fragment.get("actions") or []
            group_weights: dict[str, float] = {}
            for action in actions:
                group = str(action.get("randomSpawnGroupKey") or "").strip()
                if group:
                    group_weights[group] = group_weights.get(group, 0.0) + max(
                        0.0, float(action.get("weight") or 0.0)
                    )
            for action_index, action in enumerate(actions):
                if int(action.get("actionType", -1)) != 0:
                    continue
                enemy_id = str(action.get("key") or "").strip()
                random_group = str(action.get("randomSpawnGroupKey") or "").strip()
                random_group_scope = (
                    f"w{wave_index}-f{fragment_index}:{random_group}"
                    if random_group
                    else None
                )
                probability = 1.0
                if random_group:
                    # Game weights are percentages.  A sum below 100 leaves an
                    # implicit empty outcome, while malformed sums above 100
                    # are normalized rather than emitting probability > 1.
                    total_weight = max(100.0, group_weights.get(random_group, 0.0))
                    probability = (
                        max(0.0, float(action.get("weight") or 0.0)) / total_weight
                        if total_weight > 0
                        else 0.0
                    )
                elif action.get("hiddenGroup") and float(action.get("weight") or 0.0) > 0:
                    probability = min(
                        1.0, max(0.0, float(action.get("weight") or 0.0) / 100.0)
                    )
                count = max(0, int(action.get("count") or 0))
                pre_delay = float(action.get("preDelay") or 0.0)
                interval = float(action.get("interval") or 0.0)
                for ordinal in range(count):
                    at = fragment_clock + pre_delay + ordinal * interval
                    fragment_end = max(fragment_end, at)
                    events.append(
                        {
                            "spawn_id": (
                                f"w{wave_index}-f{fragment_index}-a{action_index}-n{ordinal}"
                            ),
                            "enemy_id": enemy_id or None,
                            "is_empty_option": not bool(enemy_id),
                            "route_index": int(action.get("routeIndex", -1)),
                            "time_seconds": round(at, 3),
                            "not_count_in_total": bool(action.get("notCountInTotal")),
                            "random_group": random_group or None,
                            "random_group_scope": random_group_scope,
                            "random_choice_id": (
                                f"w{wave_index}-f{fragment_index}-a{action_index}"
                                if random_group
                                else None
                            ),
                            "selection_probability": round(probability, 6),
                            "hidden_group": action.get("hiddenGroup"),
                        }
                    )
            fragment_clock = fragment_end
        wave_clock = fragment_clock + float(wave.get("postDelay") or 0.0)
    return events


def _map_facts(level: Mapping[str, Any]) -> dict[str, Any]:
    map_data = level.get("mapData") or {}
    raw_rows = map_data.get("map") or []
    definitions = map_data.get("tiles") or []
    height = len(raw_rows)
    width = len(raw_rows[0]) if height else 0
    if width < 1 or height < 1 or any(len(row) != width for row in raw_rows):
        raise StageWorldModelError("loaded stage map is empty or ragged")
    glyphs = {
        "tile_forbidden": "#",
        "tile_road": ".",
        "tile_wall": "^",
        "tile_hole": "O",
        "tile_start": "S",
        "tile_end": "E",
    }
    ground: list[tuple[int, int]] = []
    high: list[tuple[int, int]] = []
    walkable: list[tuple[int, int]] = []
    starts: list[tuple[int, int]] = []
    ends: list[tuple[int, int]] = []
    rows: list[str] = []
    special: list[dict[str, Any]] = []
    for row_index, indexes in enumerate(raw_rows):
        rendered: list[str] = []
        for col_index, tile_index in enumerate(indexes):
            try:
                tile = definitions[int(tile_index)]
            except (IndexError, TypeError, ValueError) as exc:
                raise StageWorldModelError("map references an invalid tile definition") from exc
            location = (col_index, row_index)
            buildable = int(tile.get("buildableType") or 0)
            if buildable == 1:
                ground.append(location)
            elif buildable == 2:
                high.append(location)
            if int(tile.get("passableMask") or 0) & 1:
                walkable.append(location)
            tile_key = str(tile.get("tileKey") or "")
            if tile_key == "tile_start":
                starts.append(location)
            elif tile_key == "tile_end":
                ends.append(location)
            if tile_key not in glyphs or tile.get("blackboard"):
                special.append(
                    {
                        "location": list(location),
                        "tile_key": tile_key,
                        "blackboard": tile.get("blackboard") or [],
                    }
                )
            rendered.append(glyphs.get(tile_key, "?"))
        rows.append("".join(rendered))
    return {
        "width": width,
        "height": height,
        "glyph_rows": rows,
        "glyph_legend": {
            "#": "forbidden",
            ".": "ground",
            "^": "high_ground",
            "O": "hole",
            "S": "enemy_start",
            "E": "defense_end",
            "?": "special_or_other",
        },
        "deployable_ground": ground,
        "deployable_high": high,
        "walkable": walkable,
        "enemy_starts": starts,
        "defense_ends": ends,
        "special_tiles": special,
    }


def _routes(
    level: Mapping[str, Any],
    height: int,
    spawn_events: Sequence[Mapping[str, Any]],
    *,
    walkable: set[tuple[int, int]],
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    spawn_by_route: dict[int, list[Mapping[str, Any]]] = {}
    for event in spawn_events:
        if bool(event.get("is_empty_option")):
            continue
        spawn_by_route.setdefault(int(event["route_index"]), []).append(event)

    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    index_to_id: dict[int, str] = {}
    for route_index, route in enumerate(level.get("routes") or []):
        if route_index not in spawn_by_route or not isinstance(route, Mapping):
            continue
        start = _point(route.get("startPosition"), height)
        end = _point(route.get("endPosition"), height)
        if start is None or end is None:
            raise StageWorldModelError(f"spawned route {route_index} lacks endpoints")
        anchors = [start]
        waits: list[dict[str, Any]] = []
        for checkpoint_index, checkpoint in enumerate(route.get("checkpoints") or []):
            if not isinstance(checkpoint, Mapping):
                continue
            checkpoint_type = int(checkpoint.get("type", -1))
            if checkpoint_type == 0:
                position = _point(checkpoint.get("position"), height)
                if position is not None and position != anchors[-1]:
                    anchors.append(position)
            elif checkpoint_type == 1:
                waits.append(
                    {
                        "after_anchor": len(anchors) - 1,
                        "seconds": float(checkpoint.get("time") or 0.0),
                        "source_checkpoint": checkpoint_index,
                    }
                )
        if end != anchors[-1]:
            anchors.append(end)
        motion_mode = int(route.get("motionMode") or 0)
        key = (
            motion_mode,
            tuple(anchors),
            tuple((item["after_anchor"], item["seconds"]) for item in waits),
        )
        group = grouped.get(key)
        if group is None:
            route_id = f"route-{len(grouped) + 1}"
            cells = _route_cells(
                anchors,
                walkable=(None if motion_mode == 1 else walkable),
            )
            route_length = (
                sum(
                    math.hypot(after[0] - before[0], after[1] - before[1])
                    for before, after in zip(anchors, anchors[1:])
                )
                if motion_mode == 1
                else max(0, len(cells) - 1)
            )
            group = {
                "route_id": route_id,
                "source_indices": [],
                "movement_layer": "flying" if motion_mode == 1 else "ground",
                "anchors": [list(item) for item in anchors],
                "cells": [list(item) for item in cells],
                "waits": waits,
                "wait_seconds": round(sum(item["seconds"] for item in waits), 3),
                "length_tiles": round(route_length, 3),
                "cell_walk_length": max(0, len(cells) - 1),
                "spawn_count": 0,
                "expected_spawn_count": 0.0,
                "counted_spawn_count": 0,
                "expected_counted_spawn_count": 0.0,
                "enemy_ids": [],
                "first_spawn_seconds": None,
                "last_spawn_seconds": None,
            }
            grouped[key] = group
        group["source_indices"].append(route_index)
        index_to_id[route_index] = str(group["route_id"])
        events = spawn_by_route[route_index]
        group["spawn_count"] += len(events)
        group["expected_spawn_count"] = round(
            float(group["expected_spawn_count"])
            + sum(float(event["selection_probability"]) for event in events),
            6,
        )
        group["counted_spawn_count"] += sum(
            not bool(event["not_count_in_total"]) for event in events
        )
        group["expected_counted_spawn_count"] = round(
            float(group["expected_counted_spawn_count"])
            + sum(
                float(event["selection_probability"])
                for event in events
                if not bool(event["not_count_in_total"])
            ),
            6,
        )
        group["enemy_ids"] = sorted(
            set(group["enemy_ids"]) | {str(event["enemy_id"]) for event in events}
        )
        times = [float(event["time_seconds"]) for event in events]
        first = min(times)
        last = max(times)
        current_first = group["first_spawn_seconds"]
        current_last = group["last_spawn_seconds"]
        group["first_spawn_seconds"] = first if current_first is None else min(current_first, first)
        group["last_spawn_seconds"] = last if current_last is None else max(current_last, last)
    return list(grouped.values()), index_to_id


def _enemy_value(value: Any) -> Any:
    """Read the real level-file wrapper used by enemy overrides and merges."""
    if isinstance(value, Mapping) and "m_value" in value:
        return value["m_value"]
    return value


def _enemy_rows(resolved_enemies: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for enemy in (resolved_enemies.get("enemies") or {}).get("enemy_refs") or []:
        attributes = enemy.get("attributes") or {}
        rows.append(
            {
                "enemy_id": str(enemy.get("id") or ""),
                "name": _enemy_value(enemy.get("name")),
                "description": _enemy_value(enemy.get("description")),
                "level": enemy.get("level"),
                "resolved": bool(enemy.get("resolved")),
                "hp": _enemy_value(attributes.get("maxHp")),
                "atk": _enemy_value(attributes.get("atk")),
                "def": _enemy_value(attributes.get("def")),
                "res": _enemy_value(attributes.get("magicResistance")),
                "move_speed": _enemy_value(attributes.get("moveSpeed")),
                "attack_time": _enemy_value(attributes.get("baseAttackTime")),
                "apply_way": _enemy_value(enemy.get("applyWay")),
                "range_radius": _enemy_value(enemy.get("rangeRadius")),
                "life_point_reduce": _enemy_value(enemy.get("lifePointReduce")),
                "talent_blackboard": [
                    {
                        "key": str(item.get("key") or ""),
                        "value": item.get("value"),
                        "value_str": item.get("valueStr"),
                    }
                    for item in (enemy.get("talentBlackboard") or [])
                    if isinstance(item, Mapping) and str(item.get("key") or "")
                ],
                "skills": enemy.get("skills") or [],
            }
        )
    return rows


@dataclass(frozen=True, slots=True)
class OperatorCombatProfile:
    operator_id: str
    name: str
    profession: str
    sub_profession_id: str
    position: str
    phase: int
    level: int
    level_basis: str
    range_id: str
    range_offsets: tuple[tuple[int, int], ...]
    hp: int
    atk: int
    defence: int
    res: float
    cost: int
    block: int
    attack_interval_seconds: float
    damage_kind: str
    can_target_flying: bool
    attack_speed_bonus: float = 0.0
    attack_bonus_milli: int = 0
    defense_bonus_milli: int = 0
    attack_bonus_per_extra_blocked_milli: int = 0
    defense_bonus_per_extra_blocked_milli: int = 0
    talent_evidence: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "operator_id": self.operator_id,
            "name": self.name,
            "profession": self.profession,
            "sub_profession_id": self.sub_profession_id,
            "position": self.position,
            "phase": self.phase,
            "level": self.level,
            "level_basis": self.level_basis,
            "range_id": self.range_id,
            "stats": {
                "hp": self.hp,
                "atk": self.atk,
                "def": self.defence,
                "res": self.res,
                "cost": self.cost,
                "block": self.block,
                "attack_interval_seconds": self.attack_interval_seconds,
                "attack_speed_bonus": self.attack_speed_bonus,
                "attack_bonus_milli": self.attack_bonus_milli,
                "defense_bonus_milli": self.defense_bonus_milli,
                "attack_bonus_per_extra_blocked_milli": self.attack_bonus_per_extra_blocked_milli,
                "defense_bonus_per_extra_blocked_milli": self.defense_bonus_per_extra_blocked_milli,
            },
            "baseline_damage_kind": self.damage_kind,
            "can_target_flying": self.can_target_flying,
            "talent_evidence": list(self.talent_evidence),
        }


def _active_talents(
    connection: sqlite3.Connection,
    operator_id: str,
    *,
    phase: int,
    level: int,
    potential: int,
) -> tuple[dict[str, Any], ...]:
    """Select the latest source candidate unlocked by the current recruit state."""

    rows = connection.execute(
        "SELECT * FROM talents WHERE operator_id=? ORDER BY talent_index, candidate_index",
        (operator_id,),
    ).fetchall()
    selected: dict[int, dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        unlock_phase = int(row.get("unlock_phase") or 0)
        unlock_level = int(row.get("unlock_level") or 1)
        required_potential = int(row.get("required_potential_rank") or 0)
        if unlock_phase > phase or required_potential > potential:
            continue
        if unlock_phase == phase and unlock_level > level:
            continue
        talent_index = int(row["talent_index"])
        selected[talent_index] = row
    return tuple(selected[index] for index in sorted(selected))


def _talent_blackboard(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in rows:
        for item in json.loads(str(row.get("blackboard_json") or "[]")):
            key = str(item.get("key") or "")
            value = item.get("value")
            if key and isinstance(value, (int, float)) and not isinstance(value, bool):
                result[key] = float(value)
    return result


def _interpolate_frames(frames: Sequence[Mapping[str, Any]], level: int) -> dict[str, Any]:
    ordered = sorted(frames, key=lambda item: int(item.get("level") or 0))
    if not ordered:
        raise StageWorldModelError("operator phase has no attribute keyframes")
    before = ordered[0]
    after = ordered[-1]
    for candidate in ordered:
        candidate_level = int(candidate.get("level") or 0)
        if candidate_level <= level:
            before = candidate
        if candidate_level >= level:
            after = candidate
            break
    low_level, high_level = int(before["level"]), int(after["level"])
    low, high = before.get("data") or {}, after.get("data") or {}
    ratio = 0.0 if high_level == low_level else (level - low_level) / (high_level - low_level)
    result: dict[str, Any] = {}
    for key in set(low) | set(high):
        left, right = low.get(key), high.get(key)
        if (
            isinstance(left, (int, float))
            and not isinstance(left, bool)
            and isinstance(right, (int, float))
            and not isinstance(right, bool)
        ):
            result[key] = float(left) + (float(right) - float(left)) * ratio
        else:
            result[key] = left if left is not None else right
    return result


def _damage_kind(profession: str, sub_profession: str) -> str:
    if profession == "MEDIC" and sub_profession == "incantationmedic":
        return "arts"
    if profession == "MEDIC":
        return "healing"
    if profession == "CASTER" or sub_profession in {
        "artsfghter", "artsprotector", "alchemist", "ritualist", "slower", "summoner", "underminer"
    }:
        return "arts"
    if profession in {"SNIPER", "PIONEER", "WARRIOR", "TANK", "SPECIAL"}:
        return "physical"
    return "unknown"


def load_operator_profile(
    database: Path | str,
    query: str,
    *,
    phase: int | None = None,
    level: int | None = None,
) -> OperatorCombatProfile:
    """Resolve one exact operator and interpolate its selected phase/level."""

    connection = sqlite3.connect(Path(database))
    connection.row_factory = sqlite3.Row
    try:
        matches = connection.execute(
            """
            SELECT * FROM operators
            WHERE operator_id=? OR name=?
            ORDER BY CASE WHEN operator_id=? THEN 0 ELSE 1 END
            """,
            (query, query, query),
        ).fetchall()
        if len(matches) != 1:
            raise StageWorldModelError(
                f"operator must resolve exactly by id or name: {query!r} ({len(matches)} matches)"
            )
        operator = dict(matches[0])
        operator_id = str(operator["operator_id"])
        account = connection.execute(
            """
            SELECT * FROM account_roster
            WHERE resolved_operator_id=? AND owned=1
            ORDER BY account_label LIMIT 1
            """,
            (operator_id,),
        ).fetchone()
        phases = connection.execute(
            "SELECT * FROM operator_phases WHERE operator_id=? ORDER BY phase_index",
            (operator_id,),
        ).fetchall()
        if not phases:
            raise StageWorldModelError(f"operator has no phase data: {operator_id}")
        if phase is None:
            if account is not None and account["elite_phase"] is not None:
                phase = min(1, int(account["elite_phase"]))
                level_basis = (
                    "integrated_strategy_initial_recruit_cap; current exploration "
                    "promotion not proved"
                )
            else:
                phase = int(phases[-1]["phase_index"])
                level_basis = "catalog_max; operator not found in account roster"
        else:
            level_basis = "explicit_phase"
        selected_rows = [row for row in phases if int(row["phase_index"]) == phase]
        if len(selected_rows) != 1:
            raise StageWorldModelError(f"operator {operator_id} has no phase {phase}")
        selected = dict(selected_rows[0])
        maximum = int(selected.get("max_level") or 1)
        if level is None:
            if (
                account is not None
                and int(account["elite_phase"] or 0) == phase
                and account["level"] is not None
            ):
                level = int(account["level"])
            else:
                level = maximum
        if level < 1 or level > maximum:
            raise StageWorldModelError(
                f"operator {operator_id} phase {phase} level must be within 1..{maximum}"
            )
        frames = json.loads(str(selected["attribute_keyframes_json"]))
        attributes = _interpolate_frames(frames, level)
        range_id = str(selected.get("range_id") or "")
        range_row = connection.execute(
            "SELECT grids_json FROM ranges WHERE range_id=?", (range_id,)
        ).fetchone()
        if range_row is None:
            raise StageWorldModelError(f"range data missing: {range_id}")
        offsets = tuple(
            (int(row["col"]), int(row["row"]))
            for row in json.loads(str(range_row["grids_json"]))
        )
        profession = str(operator.get("profession") or "")
        sub_profession = str(operator.get("sub_profession_id") or "")
        position = str(operator.get("position") or "")
        potential = int(account["potential"] or 0) if account is not None else 0
        active_talents = _active_talents(
            connection,
            operator_id,
            phase=phase,
            level=level,
            potential=potential,
        )
        talent_blackboard = _talent_blackboard(active_talents)
        # These source shapes are unconditional for the two current operators.
        # Keeping the adapters explicit avoids treating similarly named conditional
        # talent keys elsewhere in the roster as permanent stats.
        attack_speed_bonus = (
            talent_blackboard.get("attack_speed", 0.0)
            if operator_id == "char_278_orchid"
            else 0.0
        )
        if operator_id == "char_1026_gvial2":
            attack_bonus_milli = round(talent_blackboard.get("atk", 0.0) * 1000)
            defense_bonus_milli = round(talent_blackboard.get("def", 0.0) * 1000)
            attack_bonus_per_extra_blocked_milli = round(
                talent_blackboard.get("atk_add", 0.0) * 1000
            )
            defense_bonus_per_extra_blocked_milli = round(
                talent_blackboard.get("def_add", 0.0) * 1000
            )
        else:
            attack_bonus_milli = 0
            defense_bonus_milli = 0
            attack_bonus_per_extra_blocked_milli = 0
            defense_bonus_per_extra_blocked_milli = 0
        talent_evidence = tuple(
            f"{row['source_path']}#{row['source_pointer']}"
            for row in active_talents
            if row.get("source_path") and row.get("source_pointer")
        )
        return OperatorCombatProfile(
            operator_id=operator_id,
            name=str(operator.get("name") or operator_id),
            profession=profession,
            sub_profession_id=sub_profession,
            position=position,
            phase=phase,
            level=level,
            level_basis=level_basis,
            range_id=range_id,
            range_offsets=offsets,
            hp=round(float(attributes.get("maxHp") or 0)),
            atk=round(float(attributes.get("atk") or 0)),
            defence=round(float(attributes.get("def") or 0)),
            res=round(float(attributes.get("magicResistance") or 0), 3),
            cost=round(float(attributes.get("cost") or 0)),
            block=round(float(attributes.get("blockCnt") or 0)),
            attack_interval_seconds=round(
                float(attributes.get("baseAttackTime") or 1.0)
                * 100.0
                / max(
                    1.0,
                    float(attributes.get("attackSpeed") or 100.0)
                    + attack_speed_bonus,
                ),
                4,
            ),
            damage_kind=_damage_kind(profession, sub_profession),
            can_target_flying=(
                (position == "RANGED" and sub_profession not in {"bard", "craftsman"})
                or sub_profession in {"lord", "shotprotector"}
            ),
            attack_speed_bonus=attack_speed_bonus,
            attack_bonus_milli=attack_bonus_milli,
            defense_bonus_milli=defense_bonus_milli,
            attack_bonus_per_extra_blocked_milli=attack_bonus_per_extra_blocked_milli,
            defense_bonus_per_extra_blocked_milli=defense_bonus_per_extra_blocked_milli,
            talent_evidence=talent_evidence,
        )
    finally:
        connection.close()


def _rotate(offset: tuple[int, int], facing: str) -> tuple[int, int]:
    """Rotate source range coordinates into the top-origin live grid."""

    col, source_row = offset
    if facing == "right":
        return col, -source_row
    if facing == "down":
        return source_row, col
    if facing == "left":
        return -col, source_row
    if facing == "up":
        return -source_row, -col
    raise StageWorldModelError(f"invalid facing: {facing!r}")


def _covered_tiles(
    profile: OperatorCombatProfile,
    placement: tuple[int, int],
    facing: str,
    width: int,
    height: int,
) -> tuple[tuple[int, int], ...]:
    covered = {
        (placement[0] + delta[0], placement[1] + delta[1])
        for delta in (_rotate(offset, facing) for offset in profile.range_offsets)
    }
    return tuple(sorted(cell for cell in covered if 0 <= cell[0] < width and 0 <= cell[1] < height))


def _facing_vector(facing: str) -> tuple[int, int]:
    vectors = {
        "up": (0, -1),
        "right": (1, 0),
        "down": (0, 1),
        "left": (-1, 0),
    }
    try:
        return vectors[facing]
    except KeyError as exc:
        raise StageWorldModelError(f"invalid facing: {facing!r}") from exc


class StageWorldModel:
    """Immutable compiled geometry plus cheap placement and damage queries."""

    def __init__(
        self,
        *,
        loaded_stage: Mapping[str, Any],
        level: Mapping[str, Any],
        resolved_enemies: Mapping[str, Any],
        source: Mapping[str, Any],
    ) -> None:
        self.loaded_stage = dict(loaded_stage)
        self.level = dict(level)
        self.source = dict(source)
        self.map = _map_facts(level)
        self.predefined_tokens = _predefined_tokens(level, int(self.map["height"]))
        self.spawn_events = _spawn_events(level)
        self.routes, self.route_index_to_id = _routes(
            level,
            int(self.map["height"]),
            self.spawn_events,
            walkable={tuple(point) for point in self.map["walkable"]},
        )
        self.enemies = _enemy_rows(resolved_enemies)
        options = level.get("options") or {}
        self.options = {
            "character_limit": int(options.get("characterLimit") or 0),
            "static_max_life_point": int(options.get("maxLifePoint") or 0),
            "initial_cost": int(options.get("initialCost") or 0),
            "max_cost": int(options.get("maxCost") or 99),
            "cost_increase_time_seconds": float(options.get("costIncreaseTime") or 1.0),
            "move_multiplier": float(options.get("moveMultiplier") or 1.0),
        }
        core = {
            "schema_id": SCHEMA_ID,
            "model_version": MODEL_VERSION,
            "stage_identity": {
                key: self.loaded_stage.get(key)
                for key in ("name", "mode", "level_ids", "stage_ids")
            },
            "source": self.source,
            "map": self.map,
            "predefined_tokens": self.predefined_tokens,
            "options": self.options,
            "routes": self.routes,
            "spawn_events": self.spawn_events,
            "enemies": self.enemies,
        }
        self.model_hash = _canonical_hash(core)

    def summary(self, *, include_cells: bool = False) -> dict[str, Any]:
        routes = self.routes
        if not include_cells:
            routes = [
                {key: value for key, value in route.items() if key != "cells"}
                for route in self.routes
            ]
        concrete_events = [
            event for event in self.spawn_events if not event["is_empty_option"]
        ]
        unconditional_events = [
            event for event in concrete_events if event.get("hidden_group") is None
        ]
        mode = str(self.loaded_stage.get("mode") or "").strip().lower()
        default_hidden_group = (
            "n" if mode == "normal" else "h" if mode in {"hard", "emergency"} else None
        )
        expected_events = [
            event
            for event in concrete_events
            if event.get("hidden_group") is None
            or event.get("hidden_group") == default_hidden_group
        ]
        counted = sum(not event["not_count_in_total"] for event in concrete_events)
        expected = round(
            sum(float(event["selection_probability"]) for event in expected_events), 6
        )
        return {
            "ok": True,
            "schema_id": SCHEMA_ID,
            "model_version": MODEL_VERSION,
            "model_hash": self.model_hash,
            "loaded_stage": {
                key: self.loaded_stage.get(key)
                for key in ("name", "mode", "level_ids", "stage_ids")
            },
            "coordinate_system": {
                "location": "[col,row]",
                "col_origin": "left",
                "row_origin": "top",
                "range_source_facing": "right",
            },
            "map": {
                "width": self.map["width"],
                "height": self.map["height"],
                "glyph_rows": self.map["glyph_rows"],
                "glyph_legend": self.map["glyph_legend"],
                "deployable_ground": self.map["deployable_ground"],
                "deployable_high": self.map["deployable_high"],
                "enemy_starts": self.map["enemy_starts"],
                "defense_ends": self.map["defense_ends"],
                "special_tiles": self.map["special_tiles"],
            },
            "options": self.options,
            "predefined_tokens": self.predefined_tokens,
            "spawn_option_count": len(self.spawn_events),
            "spawn_count_upper_bound": len(concrete_events),
            "expected_spawn_count": expected,
            "conditional_spawn_option_count": len(concrete_events)
            - len(unconditional_events),
            "default_hidden_group": default_hidden_group,
            "counted_spawn_count_upper_bound": counted,
            "routes": routes,
            "enemies": self.enemies,
            "source": self.source,
            "fidelity": {
                "map_tiles": "source_exact",
                "route_anchors": "source_exact; WAIT checkpoints excluded from geometry",
                "route_cells": (
                    "deterministic shortest four-neighbour walk over passable tiles for "
                    "ground routes; flying routes use geometric supercover"
                ),
                "spawn_timing": "unverified fragment timing model",
                "predefined_tokens": (
                    "source-exact visibility, identity and bottom-to-top coordinate conversion; "
                    "combat compilation is delegated to source-addressed adapters"
                ),
                "enemy_stats": "level reference plus stage overrides",
                "live_state": "not included; query battle snapshot separately",
            },
        }

    def route_summary(self) -> dict[str, Any]:
        return {
            "ok": True,
            "model_hash": self.model_hash,
            "loaded_stage": self.loaded_stage.get("name"),
            "routes": self.routes,
            "spawn_timing_verified": False,
            "note": "WAIT checkpoints retain time but never become fake coordinates",
        }

    def compact_tactical_facts(self) -> dict[str, Any]:
        """Return only battle-relevant paths, spawn choices and enemy baselines.

        ``self.routes`` is already restricted to route indices referenced by a
        concrete enemy spawn.  This projection additionally merges variants
        whose traversed geometry is identical but whose waits differ.  The
        waits stay visible as variants instead of multiplying the path list.
        """

        grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
        route_to_path: dict[str, str] = {}
        for route in self.routes:
            key = (
                str(route["movement_layer"]),
                tuple(tuple(point) for point in route["anchors"]),
            )
            path = grouped.get(key)
            if path is None:
                path = {
                    "path_id": f"path-{len(grouped) + 1}",
                    "movement": route["movement_layer"],
                    "anchors": route["anchors"],
                    "cells": route["cells"],
                    "source_route_indices": [],
                    "wait_variants": [],
                    "spawn_option_count": 0,
                    "expected_spawn_count": 0.0,
                    "counted_option_upper_bound": 0,
                    "enemy_ids": [],
                    "first_spawn_seconds": None,
                    "last_spawn_seconds": None,
                }
                grouped[key] = path
            route_to_path[str(route["route_id"])] = str(path["path_id"])
            path["source_route_indices"].extend(route["source_indices"])
            wait_variant = {
                "source_route_indices": route["source_indices"],
                "waits": route["waits"],
            }
            if wait_variant not in path["wait_variants"]:
                path["wait_variants"].append(wait_variant)
            path["spawn_option_count"] += int(route["spawn_count"])
            path["expected_spawn_count"] = round(
                float(path["expected_spawn_count"])
                + float(route["expected_spawn_count"]),
                6,
            )
            path["counted_option_upper_bound"] += int(route["counted_spawn_count"])
            path["enemy_ids"] = sorted(
                set(path["enemy_ids"]) | set(route["enemy_ids"])
            )
            first = route["first_spawn_seconds"]
            last = route["last_spawn_seconds"]
            if first is not None:
                path["first_spawn_seconds"] = (
                    float(first)
                    if path["first_spawn_seconds"] is None
                    else min(float(path["first_spawn_seconds"]), float(first))
                )
            if last is not None:
                path["last_spawn_seconds"] = (
                    float(last)
                    if path["last_spawn_seconds"] is None
                    else max(float(path["last_spawn_seconds"]), float(last))
                )
        paths = list(grouped.values())
        for path in paths:
            path["source_route_indices"] = sorted(set(path["source_route_indices"]))

        route_index_to_path = {
            index: route_to_path[route_id]
            for index, route_id in self.route_index_to_id.items()
            if route_id in route_to_path
        }
        route_by_id = {str(route["route_id"]): route for route in self.routes}
        enemy_by_id = {str(enemy["enemy_id"]): enemy for enemy in self.enemies}
        certain: list[list[Any]] = []
        conditional: list[list[Any]] = []
        random_groups: dict[str, list[dict[str, Any]]] = {}
        defense_arrivals: list[dict[str, Any]] = []
        referenced_enemy_ids: set[str] = set()
        for event in self.spawn_events:
            enemy_id = event.get("enemy_id")
            if enemy_id:
                referenced_enemy_ids.add(str(enemy_id))
            path_id = route_index_to_path.get(int(event["route_index"]))
            route_id = self.route_index_to_id.get(int(event["route_index"]))
            route = route_by_id.get(str(route_id or ""))
            enemy = enemy_by_id.get(str(enemy_id or ""))
            move_speed = None if enemy is None else enemy.get("move_speed")
            move_multiplier = float(self.options.get("move_multiplier") or 1.0)
            arrival_seconds = None
            if (
                route is not None
                and isinstance(move_speed, (int, float))
                and not isinstance(move_speed, bool)
                and float(move_speed) > 0
                and move_multiplier > 0
            ):
                arrival_seconds = round(
                    float(event["time_seconds"])
                    + float(route.get("wait_seconds") or 0.0)
                    + float(route.get("length_tiles") or 0.0)
                    / (float(move_speed) * move_multiplier),
                    3,
                )
            item = {
                "p": float(event["selection_probability"]),
                "t": float(event["time_seconds"]),
                "enemy": enemy_id,
                "path": path_id,
                "hidden_condition": event.get("hidden_group"),
                "counts": not bool(event["not_count_in_total"]),
            }
            if enemy_id and path_id and arrival_seconds is not None:
                defense_arrivals.append(
                    {
                        "spawn_id": event["spawn_id"],
                        "enemy_id": enemy_id,
                        "path_id": path_id,
                        "movement": None if route is None else route["movement_layer"],
                        "spawn_seconds": float(event["time_seconds"]),
                        "route_wait_seconds": (
                            0.0 if route is None else float(route.get("wait_seconds") or 0.0)
                        ),
                        "route_length_tiles": (
                            0.0 if route is None else float(route.get("length_tiles") or 0.0)
                        ),
                        "move_speed": float(move_speed),
                        "stage_move_multiplier": move_multiplier,
                        "defense_arrival_seconds": arrival_seconds,
                        "probability": float(event["selection_probability"]),
                        "kind": (
                            "random"
                            if event.get("random_group_scope")
                            else "conditional"
                            if event.get("hidden_group")
                            else "certain"
                        ),
                    }
                )
            scope = event.get("random_group_scope")
            if scope:
                random_groups.setdefault(str(scope), []).append(item)
            elif event.get("hidden_group"):
                conditional.append(
                    [item["t"], item["enemy"], item["path"], item["hidden_condition"]]
                )
            elif enemy_id:
                certain.append([item["t"], item["enemy"], item["path"], item["counts"]])

        random_rows = []
        for scope, options in sorted(random_groups.items()):
            probability = round(sum(float(row["p"]) for row in options), 6)
            random_rows.append(
                {
                    "scope": scope,
                    "options": options,
                    "implicit_empty_probability": round(max(0.0, 1.0 - probability), 6),
                }
            )
        enemies = [
            row for row in self.enemies if row["enemy_id"] in referenced_enemy_ids
        ]
        arrivals_by_path: dict[str, list[float]] = {}
        for arrival in defense_arrivals:
            arrivals_by_path.setdefault(str(arrival["path_id"]), []).append(
                float(arrival["defense_arrival_seconds"])
            )
        for path in paths:
            arrivals = arrivals_by_path.get(str(path["path_id"]), [])
            path["defense_arrival_window_seconds"] = (
                None if not arrivals else [round(min(arrivals), 3), round(max(arrivals), 3)]
            )

        certain_ground = sorted(
            (
                row
                for row in defense_arrivals
                if row["kind"] == "certain" and row["movement"] == "ground"
            ),
            key=lambda row: (
                float(row["defense_arrival_seconds"]), str(row["spawn_id"])
            ),
        )
        bursts: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []
        for arrival in certain_ground:
            if current and (
                float(arrival["defense_arrival_seconds"])
                - float(current[0]["defense_arrival_seconds"])
                > ARRIVAL_BURST_WINDOW_SECONDS
            ):
                bursts.append(self._arrival_burst(current))
                current = []
            current.append(arrival)
        if current:
            bursts.append(self._arrival_burst(current))
        return {
            "paths": paths,
            "route_to_path": route_to_path,
            "spawn_plan": {
                "certain": certain,
                "conditional": conditional,
                "random_groups": random_rows,
                "tuple_legend": {
                    "certain": "[approx_time_s,enemy_id,path_id,counts_in_total]",
                    "conditional": "[approx_time_s,enemy_id,path_id,hidden_condition]",
                },
            },
            "defense_arrivals": defense_arrivals,
            "certain_ground_arrival_bursts": bursts,
            "arrival_model": {
                "burst_window_seconds": ARRIVAL_BURST_WINDOW_SECONDS,
                "basis": (
                    "spawn time + source route WAIT + compiled route length / "
                    "(enemy move speed * stage move multiplier)"
                ),
                "fidelity": (
                    "deterministic static estimate; enemy talents, release mechanics, "
                    "blocking, control and live scheduler drift can change physical arrival"
                ),
            },
            "enemies": enemies,
        }

    @staticmethod
    def _arrival_burst(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        arrivals = [float(row["defense_arrival_seconds"]) for row in rows]
        return {
            "start_seconds": round(min(arrivals), 3),
            "end_seconds": round(max(arrivals), 3),
            "certain_enemy_count": len(rows),
            "enemy_ids": sorted({str(row["enemy_id"]) for row in rows}),
            "path_ids": sorted({str(row["path_id"]) for row in rows}),
        }

    def compact_coverage(
        self,
        profiles: Iterable[OperatorCombatProfile],
        *,
        top: int = 3,
        tactical_facts: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Rank geometry neutrally while reporting compressed path coverage."""

        if not 1 <= top <= 4:
            raise ValueError("compact coverage top must be between 1 and 4")
        facts = dict(tactical_facts or self.compact_tactical_facts())
        route_to_path = dict(facts["route_to_path"])
        path_by_id = {row["path_id"]: row for row in facts["paths"]}
        result = []
        for profile in profiles:
            candidates = []
            for col, row in self._legal_tiles(profile):
                for facing in FACINGS:
                    deployment = self.deployment(profile, col, row, facing)
                    coverage_by_path: dict[str, dict[str, Any]] = {}
                    for route in deployment["route_coverage"]:
                        path_id = route_to_path.get(str(route["route_id"]))
                        if path_id is None or not route["effective_targeting"]:
                            continue
                        current = coverage_by_path.setdefault(
                            path_id,
                            {"path_id": path_id, "fraction": 0.0, "blocks": False},
                        )
                        current["fraction"] = max(
                            float(current["fraction"]), float(route["covered_fraction"])
                        )
                        current["blocks"] = bool(current["blocks"]) or bool(
                            route["placement_blocks_route"]
                        )
                    path_coverage = sorted(
                        coverage_by_path.values(), key=lambda value: value["path_id"]
                    )
                    weighted_fraction = round(
                        sum(
                            float(path_by_id[row["path_id"]]["expected_spawn_count"])
                            * float(row["fraction"])
                            for row in path_coverage
                        ),
                        4,
                    )
                    candidates.append(
                        {
                            "at": deployment["placement"],
                            "facing": facing,
                            "covered_tiles": deployment["covered_tiles"],
                            "path_coverage": [
                                [item["path_id"], item["fraction"], item["blocks"]]
                                for item in path_coverage
                            ],
                            "score": {
                                "spawn_weighted_path_fraction": weighted_fraction,
                                "covered_path_count": len(path_coverage),
                                "blocking_path_count": sum(
                                    bool(item["blocks"]) for item in path_coverage
                                ),
                            },
                        }
                    )
            candidates.sort(
                key=lambda item: (
                    -float(item["score"]["spawn_weighted_path_fraction"]),
                    -int(item["score"]["covered_path_count"]),
                    -int(item["score"]["blocking_path_count"]),
                    item["at"],
                    item["facing"],
                )
            )
            result.append(
                {
                    "operator_id": profile.operator_id,
                    "candidate_count": len(candidates),
                    "top_geometry_candidates": candidates[:top],
                }
            )
        return result

    def attack_geometry_samples(
        self,
        profile: OperatorCombatProfile,
        *,
        enemy_hitbox_radii: Mapping[str, float],
        projectile_origin_north_offset_tiles: float = 0.0,
    ) -> dict[str, Any]:
        """Project stage routes into every legal placement/facing.

        Each route cell receives an equal share of that route/enemy pair's
        expected spawn count.  This is a cheap positional prior, not a timing
        claim: it lets skill adapters consume real reachable cells, facings,
        enemy stats and hitboxes without pretending the rasterized path is a
        frame-perfect movement trace.
        """

        enemy_by_id = {str(row["enemy_id"]): row for row in self.enemies}
        expected_by_route_enemy: dict[tuple[str, str], float] = {}
        for event in self.spawn_events:
            enemy_id = str(event.get("enemy_id") or "")
            route_id = self.route_index_to_id.get(int(event["route_index"]))
            if not enemy_id or route_id is None:
                continue
            key = (route_id, enemy_id)
            expected_by_route_enemy[key] = expected_by_route_enemy.get(key, 0.0) + float(
                event["selection_probability"]
            )
        total_expected_spawn_count = sum(expected_by_route_enemy.values())
        placements = []
        for col, row in self._legal_tiles(profile):
            for facing in FACINGS:
                covered = set(
                    _covered_tiles(
                        profile,
                        (col, row),
                        facing,
                        int(self.map["width"]),
                        int(self.map["height"]),
                    )
                )
                forward_col, forward_row = _facing_vector(facing)
                origin_col = float(col)
                origin_row = float(row) - projectile_origin_north_offset_tiles
                samples = []
                for route in self.routes:
                    route_id = str(route["route_id"])
                    targetable_layer = (
                        route["movement_layer"] == "ground" or profile.can_target_flying
                    )
                    cells = [tuple(cell) for cell in route["cells"]]
                    if not cells:
                        continue
                    for (event_route_id, enemy_id), expected_count in sorted(
                        expected_by_route_enemy.items()
                    ):
                        if event_route_id != route_id:
                            continue
                        enemy = enemy_by_id.get(enemy_id) or {}
                        radius = enemy_hitbox_radii.get(enemy_id.lower())
                        cell_weight = expected_count / len(cells)
                        for cell_col, cell_row in cells:
                            delta_col = float(cell_col) - origin_col
                            delta_row = float(cell_row) - origin_row
                            longitudinal = (
                                delta_col * forward_col + delta_row * forward_row
                            )
                            perpendicular = abs(
                                delta_col * forward_row - delta_row * forward_col
                            )
                            tile_delta_col = cell_col - col
                            tile_delta_row = cell_row - row
                            samples.append(
                                {
                                    "route_id": route_id,
                                    "enemy_id": enemy_id,
                                    "cell": [cell_col, cell_row],
                                    "expected_enemy_cell_weight": round(cell_weight, 9),
                                    "targetable_layer": targetable_layer,
                                    "in_normal_range": targetable_layer
                                    and (cell_col, cell_row) in covered,
                                    "target_distance_tiles": round(
                                        math.hypot(tile_delta_col, tile_delta_row), 6
                                    ),
                                    "target_in_adjacent_eight": max(
                                        abs(tile_delta_col), abs(tile_delta_row)
                                    )
                                    <= 1,
                                    "projectile_longitudinal_distance_tiles": round(
                                        longitudinal, 6
                                    ),
                                    "projectile_perpendicular_distance_tiles": round(
                                        perpendicular, 6
                                    ),
                                    "target_hit_radius_tiles": radius,
                                    "enemy_defense": enemy.get("def"),
                                    "enemy_resistance": enemy.get("res"),
                                }
                            )
                placements.append(
                    {
                        "at": [col, row],
                        "facing": facing,
                        "covered_tiles": [list(cell) for cell in sorted(covered)],
                        "samples": samples,
                    }
                )
        return {
            "model_hash": self.model_hash,
            "operator_id": profile.operator_id,
            "projectile_origin_north_offset_tiles": projectile_origin_north_offset_tiles,
            "total_expected_spawn_count": round(total_expected_spawn_count, 9),
            "placements": placements,
            "fidelity": (
                "source route anchors projected through deterministic four-neighbour "
                "cells; equal occupancy prior within each route"
            ),
        }

    def _legal_tiles(self, profile: OperatorCombatProfile) -> list[tuple[int, int]]:
        if profile.position == "MELEE":
            return list(self.map["deployable_ground"])
        if profile.position == "RANGED":
            return list(self.map["deployable_high"])
        return sorted(set(self.map["deployable_ground"]) | set(self.map["deployable_high"]))

    def deployment(
        self,
        profile: OperatorCombatProfile,
        col: int,
        row: int,
        facing: str,
    ) -> dict[str, Any]:
        placement = (col, row)
        legal = placement in self._legal_tiles(profile)
        covered = _covered_tiles(
            profile, placement, facing, int(self.map["width"]), int(self.map["height"])
        )
        covered_set = set(covered)
        route_coverage = []
        for route in self.routes:
            cells = [tuple(cell) for cell in route["cells"]]
            hit_indexes = [index for index, cell in enumerate(cells) if cell in covered_set]
            blocks = (
                profile.position == "MELEE"
                and placement in cells
                and placement in self.map["deployable_ground"]
                and route["movement_layer"] == "ground"
            )
            placement_index = cells.index(placement) if blocks else None
            pre_block_hit_indexes = (
                []
                if placement_index is None
                else [index for index in hit_indexes if index < placement_index]
            )
            if not hit_indexes and not blocks:
                continue
            effective = route["movement_layer"] == "ground" or profile.can_target_flying
            route_coverage.append(
                {
                    "route_id": route["route_id"],
                    "movement_layer": route["movement_layer"],
                    "spawn_count": route["spawn_count"],
                    "expected_spawn_count": route["expected_spawn_count"],
                    "enemy_ids": route["enemy_ids"],
                    "covered_cell_count": len(hit_indexes),
                    "route_cell_count": len(cells),
                    "covered_fraction": round(len(hit_indexes) / max(1, len(cells)), 4),
                    "first_covered_index": min(hit_indexes) if hit_indexes else None,
                    "last_covered_index": max(hit_indexes) if hit_indexes else None,
                    "placement_index": placement_index,
                    "pre_block_covered_cell_count": len(pre_block_hit_indexes),
                    "pre_block_covered_fraction": round(
                        len(pre_block_hit_indexes) / max(1, len(cells)), 4
                    ),
                    "effective_targeting": effective,
                    "placement_blocks_route": blocks and profile.block > 0,
                }
            )
        effective_routes = [row for row in route_coverage if row["effective_targeting"]]
        enemy_route_weight = sum(
            float(item["expected_spawn_count"]) * float(item["covered_fraction"])
            for item in effective_routes
        )
        blocking_expected_spawn_count = sum(
            float(item["expected_spawn_count"])
            for item in route_coverage
            if item["placement_blocks_route"]
        )
        pre_block_route_weight = sum(
            float(item["expected_spawn_count"])
            * float(item["pre_block_covered_fraction"])
            for item in route_coverage
            if item["placement_blocks_route"] and item["effective_targeting"]
        )
        initial_cost = int(self.options["initial_cost"])
        recovery = float(self.options["cost_increase_time_seconds"])
        affordable = max(0.0, (profile.cost - initial_cost) * recovery)
        return {
            "ok": legal,
            "model_hash": self.model_hash,
            "operator": profile.public(),
            "placement": [col, row],
            "facing": facing,
            "legal_deployment": legal,
            "covered_tiles": [list(cell) for cell in covered],
            "route_coverage": route_coverage,
            "score": {
                "effective_route_count": len(effective_routes),
                "covered_spawn_count": sum(int(item["spawn_count"]) for item in effective_routes),
                "expected_covered_spawn_count": round(
                    sum(float(item["expected_spawn_count"]) for item in effective_routes), 6
                ),
                "spawn_weighted_route_fraction": round(enemy_route_weight, 4),
                "blocking_route_count": sum(
                    bool(item["placement_blocks_route"]) for item in route_coverage
                ),
                "blocking_expected_spawn_count": round(
                    blocking_expected_spawn_count, 6
                ),
                "spawn_weighted_pre_block_fraction": round(
                    pre_block_route_weight, 4
                ),
            },
            "opening": {
                "static_initial_cost": initial_cost,
                "operator_cost_before_potential": profile.cost,
                "earliest_affordable_seconds_without_other_cost_effects": round(affordable, 3),
            },
            "enemy_baselines": self._enemy_baselines(profile),
            "limitations": [
                "normal attacks only; skills, talents, traits, trust, potential and collectibles omitted",
                "current run promotion is not inferred from account roster",
                "route-cell coverage is geometric and does not prove uninterrupted time on target",
            ],
        }

    def compare(
        self,
        profiles: Iterable[OperatorCombatProfile],
        *,
        top: int = 6,
    ) -> dict[str, Any]:
        profiles = list(profiles)
        if not profiles:
            raise ValueError("coverage compare requires at least one operator")
        if len(profiles) > 12:
            raise ValueError("coverage compare accepts at most 12 operators")
        if not 1 <= top <= 8:
            raise ValueError("top must be between 1 and 8")
        operators = []
        for profile in profiles:
            placements = [
                self.deployment(profile, col, row, facing)
                for col, row in self._legal_tiles(profile)
                for facing in FACINGS
            ]
            placements.sort(
                key=lambda item: (
                    -float(item["score"]["spawn_weighted_route_fraction"]),
                    -int(item["score"]["effective_route_count"]),
                    -int(item["score"]["blocking_route_count"]),
                    int(item["operator"]["stats"]["cost"]),
                    item["placement"],
                    item["facing"],
                )
            )
            operators.append(
                {
                    "operator": profile.public(),
                    "legal_placement_count": len(self._legal_tiles(profile)) * len(FACINGS),
                    "best_placements": [
                        {
                            "placement": placement["placement"],
                            "facing": placement["facing"],
                            "covered_tiles": placement["covered_tiles"],
                            "covered_route_ids": [
                                row["route_id"]
                                for row in placement["route_coverage"]
                                if row["effective_targeting"]
                            ],
                            "coverage_intervals": [
                                {
                                    "route_id": row["route_id"],
                                    "first": row["first_covered_index"],
                                    "last": row["last_covered_index"],
                                    "fraction": row["covered_fraction"],
                                    "blocks": row["placement_blocks_route"],
                                }
                                for row in placement["route_coverage"]
                                if row["effective_targeting"]
                            ],
                            "score": placement["score"],
                            "opening": placement["opening"],
                        }
                        for placement in placements[:top]
                    ],
                }
            )
        return {
            "ok": True,
            "model_hash": self.model_hash,
            "operators": operators,
            "ranking_scope": "placement geometry only; this is evidence for the contestant, not an automatic pick",
        }

    def _enemy_baselines(self, profile: OperatorCombatProfile) -> list[dict[str, Any]]:
        rows = []
        for enemy in self.enemies:
            if not enemy["resolved"] or enemy["hp"] is None:
                continue
            kind = profile.damage_kind
            damage: float | None
            if kind == "physical" and enemy["def"] is not None:
                damage = max(profile.atk - float(enemy["def"]), profile.atk * 0.05)
            elif kind == "arts" and enemy["res"] is not None:
                damage = max(
                    profile.atk * (1.0 - float(enemy["res"]) / 100.0),
                    profile.atk * 0.05,
                )
            else:
                damage = None
            rows.append(
                {
                    "enemy_id": enemy["enemy_id"],
                    "name": enemy["name"],
                    "damage_per_normal_hit": None if damage is None else round(damage, 2),
                    "normal_attack_dps": (
                        None
                        if damage is None
                        else round(damage / max(profile.attack_interval_seconds, 0.001), 2)
                    ),
                    "hits_to_kill": (
                        None if not damage else math.ceil(float(enemy["hp"]) / damage)
                    ),
                }
            )
        return rows


__all__ = [
    "FACINGS",
    "MODEL_VERSION",
    "SCHEMA_ID",
    "OperatorCombatProfile",
    "StageWorldModel",
    "StageWorldModelError",
    "load_operator_profile",
]
