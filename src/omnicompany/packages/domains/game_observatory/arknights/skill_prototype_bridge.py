# [OMNI] origin=codex created_by=codex intent="compile source-addressed Arknights skill prototypes into the finite effect VM"
"""Fail-closed public-knowledge skill prototypes for the deterministic simulator.

This bridge deliberately recognizes one narrow, numeric-only prototype.  It is
not a description parser: descriptions are retained as provenance but never
supply a simulated number.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tournament_sim import (
    EffectStat,
    EffectTargetSelector,
    FidelityLevel,
    SkillDefinition,
    StatModifierEffectDefinition,
)

TICKS_PER_SECOND = 30
_ATTACK_WINDOW = "attack_multiplier_window"
_AMMO_ATTACK_WINDOW = "ammo_attack_scale_window"
_EXACT_STAT_WINDOW = "operator_exact_stat_window"

# These are source-addressed skill mechanics, not description parsing.  Each
# entry admits one exact skill id and one exact blackboard key set.  Mechanics
# the finite VM cannot yet express stay explicit in ``omitted_mechanics`` so a
# forecast is a conservative lower bound rather than invented control/healing.
_EXACT_STAT_WINDOWS: dict[str, dict[str, Any]] = {
    "skchr_gvial2_1": {
        "keys": frozenset({"atk", "heal_scale"}),
        "modifiers": (("atk", EffectStat.ATTACK, "fraction"),),
        "omitted_mechanics": ("heal_scale:on-hit-self-healing",),
    },
    "skchr_gvial2_2": {
        "keys": frozenset({"atk", "def", "block_cnt", "attack@force"}),
        "modifiers": (
            ("atk", EffectStat.ATTACK, "fraction"),
            ("def", EffectStat.DEFENSE, "fraction"),
        ),
        "range_modifier": {
            "range_id": "2-5",
            "additive_tiles": 2,
        },
        "omitted_mechanics": (
            "attack@force:pull-unblocked-enemy",
        ),
    },
    "skcom_quickattack[1]": {
        "keys": frozenset({"atk", "attack_speed"}),
        "modifiers": (
            ("atk", EffectStat.ATTACK, "fraction"),
            ("attack_speed", EffectStat.ATTACK_INTERVAL_TICKS, "attack_speed"),
        ),
        "omitted_mechanics": (),
    },
    "skcom_range_extend": {
        "keys": frozenset({"atk", "ability_range_forward_extend"}),
        "modifiers": (("atk", EffectStat.HEALING_POWER, "fraction"),),
        "omitted_mechanics": ("ability_range_forward_extend",),
    },
}


@dataclass(frozen=True, slots=True)
class CompiledSkillPrototype:
    """One source-addressed skill compilation result, including failures."""

    operator_id: str
    skill_index: int
    skill_id: str | None
    level: int | None
    prototype: str | None
    definition: SkillDefinition | None
    source_pointer: dict[str, str]
    field_mapping: dict[str, Any]
    fidelity: FidelityLevel
    unsupported_reason: str | None = None

    @property
    def supported(self) -> bool:
        return self.definition is not None


def _unsupported(
    *, operator_id: str, skill_index: int, skill_id: str | None, level: int | None,
    source_pointer: dict[str, str], reason: str, field_mapping: dict[str, Any] | None = None,
) -> CompiledSkillPrototype:
    return CompiledSkillPrototype(
        operator_id=operator_id,
        skill_index=skill_index,
        skill_id=skill_id,
        level=level,
        prototype=None,
        definition=None,
        source_pointer=source_pointer,
        field_mapping=field_mapping or {},
        fidelity=FidelityLevel.U,
        unsupported_reason=reason,
    )


def _integer_ticks(value: object, *, name: str, positive: bool) -> int | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    ticks = round(float(value) * TICKS_PER_SECOND)
    if abs(float(value) * TICKS_PER_SECOND - ticks) > 1e-9 or (ticks <= 0 if positive else ticks < 0):
        return None
    return ticks


def _integer(value: object, *, name: str, positive: bool = False) -> int | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    result = round(float(value))
    if abs(float(value) - result) > 1e-9 or (result <= 0 if positive else result < 0):
        return None
    return result


def compile_operator_skill_prototype(
    database: Path | str,
    operator_id: str,
    skill_index: int,
    *,
    level: int | None = None,
    account_label: str | None = None,
    attack_interval_seconds: float | None = None,
) -> CompiledSkillPrototype:
    """Compile one explicitly selected or confirmed-mastery skill level.

    Only a manual timed skill (``skill_type=1``, ``duration_type=0``) with
    passive SP recovery (``sp_type=1``) and exactly one ``atk`` blackboard
    entry is admitted.  This is intentionally much stricter than the source
    schema because the remaining fields can change gameplay semantics.
    """

    connection = sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        skill = connection.execute(
            """SELECT skill_id, source_path, source_pointer FROM operator_skills
               WHERE operator_id=? AND skill_index=?""",
            (operator_id, skill_index),
        ).fetchone()
        pointers: dict[str, str] = {}
        if skill is None:
            return _unsupported(
                operator_id=operator_id, skill_index=skill_index, skill_id=None, level=level,
                source_pointer=pointers, reason="operator skill is absent from public knowledge",
            )
        skill_id = str(skill["skill_id"])
        pointers["operator_skill"] = f"{skill['source_path']}#{skill['source_pointer']}"
        mastery = None
        if level is None:
            mastery = connection.execute(
                """SELECT status, mastery_level, source_path, source_pointer
                   FROM account_skill_masteries
                   WHERE operator_id=? AND skill_index=?
                     AND (? IS NULL OR account_label=?)
                   ORDER BY account_label LIMIT 1""",
                (operator_id, skill_index, account_label, account_label),
            ).fetchone()
            if mastery is None:
                return _unsupported(
                    operator_id=operator_id, skill_index=skill_index, skill_id=skill_id, level=None,
                    source_pointer=pointers, reason="no explicit level and no account mastery evidence",
                )
            pointers["account_mastery"] = f"{mastery['source_path']}#{mastery['source_pointer']}"
            if mastery["status"] != "confirmed":
                return _unsupported(
                    operator_id=operator_id, skill_index=skill_index, skill_id=skill_id, level=None,
                    source_pointer=pointers, reason="account mastery is not confirmed",
                )
            mastery_level = mastery["mastery_level"]
            if mastery_level not in (1, 2, 3):
                return _unsupported(
                    operator_id=operator_id, skill_index=skill_index, skill_id=skill_id, level=None,
                    source_pointer=pointers, reason="confirmed mastery cannot derive a level in 8..10",
                )
            level = 7 + int(mastery_level)
        row = connection.execute(
            """SELECT * FROM skill_levels WHERE skill_id=? AND level_index=?""",
            (skill_id, level),
        ).fetchone()
        if row is None:
            return _unsupported(
                operator_id=operator_id, skill_index=skill_index, skill_id=skill_id, level=level,
                source_pointer=pointers, reason="selected skill level is absent from public knowledge",
            )
        pointers["skill_level"] = f"{row['source_path']}#{row['source_pointer']}"
        raw_blackboard = str(row["blackboard_json"])
        blackboard_hash = hashlib.sha256(raw_blackboard.encode("utf-8")).hexdigest()
        try:
            entries = json.loads(raw_blackboard)
        except json.JSONDecodeError:
            entries = None
        mapping: dict[str, Any] = {
            "skill_type": row["skill_type"], "duration_type": row["duration_type"],
            "sp_type": row["sp_type"], "sp_cost": row["sp_cost"],
            "initial_sp": row["initial_sp"], "sp_increment": row["sp_increment"],
            "duration": row["duration"], "range_id": row["range_id"],
            "blackboard_sha256": blackboard_hash,
        }
        entry_by_key = {
            str(entry.get("key")): entry.get("value")
            for entry in entries or []
            if isinstance(entry, dict)
        }
        exact_stat_spec = _EXACT_STAT_WINDOWS.get(skill_id)
        if (
            exact_stat_spec is not None
            and isinstance(entries, list)
            and len(entries) == len(exact_stat_spec["keys"])
            and set(entry_by_key) == set(exact_stat_spec["keys"])
            and (row["skill_type"], row["duration_type"], row["sp_type"])
            == (1, 0, 1)
        ):
            source_sp_cost = _integer(row["sp_cost"], name="sp_cost", positive=True)
            source_initial_sp = _integer(row["initial_sp"], name="initial_sp")
            sp_per_tick = _integer(row["sp_increment"], name="sp_increment")
            duration_ticks = _integer_ticks(
                row["duration"], name="duration", positive=True
            )
            if (
                source_sp_cost is None
                or source_initial_sp is None
                or sp_per_tick is None
                or duration_ticks is None
                or source_initial_sp > source_sp_cost
            ):
                return _unsupported(
                    operator_id=operator_id,
                    skill_index=skill_index,
                    skill_id=skill_id,
                    level=level,
                    source_pointer=pointers,
                    field_mapping=mapping,
                    reason="exact stat-window SP or duration fields are not representable",
                )
            effects = []
            mapped_modifiers = []
            for source_key, stat, transform in exact_stat_spec["modifiers"]:
                raw_value = entry_by_key.get(source_key)
                if not isinstance(raw_value, (int, float)) or isinstance(
                    raw_value, bool
                ):
                    return _unsupported(
                        operator_id=operator_id,
                        skill_index=skill_index,
                        skill_id=skill_id,
                        level=level,
                        source_pointer=pointers,
                        field_mapping=mapping,
                        reason=f"exact stat-window value is not numeric: {source_key}",
                    )
                if transform == "fraction":
                    exact_multiplier = (1.0 + float(raw_value)) * 1000
                else:
                    # Arknights attack speed is a percentage-point modifier:
                    # interval' = interval * 100 / (100 + attack_speed).
                    if float(raw_value) <= -100:
                        return _unsupported(
                            operator_id=operator_id,
                            skill_index=skill_index,
                            skill_id=skill_id,
                            level=level,
                            source_pointer=pointers,
                            field_mapping=mapping,
                            reason="attack-speed modifier would make interval non-positive",
                        )
                    exact_multiplier = 100_000 / (100 + float(raw_value))
                multiplier_milli = round(exact_multiplier)
                if not 0 < multiplier_milli <= 10_000:
                    return _unsupported(
                        operator_id=operator_id,
                        skill_index=skill_index,
                        skill_id=skill_id,
                        level=level,
                        source_pointer=pointers,
                        field_mapping=mapping,
                        reason=f"exact stat-window multiplier is outside VM bounds: {source_key}",
                    )
                effects.append(
                    StatModifierEffectDefinition(
                        effect_id=(
                            f"{operator_id}:{skill_index}:{skill_id}:l{level}:"
                            f"{stat.value}"
                        ),
                        target=EffectTargetSelector.SELF,
                        stat=stat,
                        multiplier_milli=multiplier_milli,
                        duration_ticks=duration_ticks,
                    )
                )
                mapped_modifiers.append(
                    {
                        "blackboard_key": source_key,
                        "effect_stat": stat.value,
                        "multiplier_milli": multiplier_milli,
                    }
                )
            range_spec = exact_stat_spec.get("range_modifier")
            skill_range_offsets: tuple[tuple[int, int], ...] = ()
            if range_spec is not None:
                if row["range_id"] != range_spec["range_id"]:
                    return _unsupported(
                        operator_id=operator_id,
                        skill_index=skill_index,
                        skill_id=skill_id,
                        level=level,
                        source_pointer=pointers,
                        field_mapping=mapping,
                        reason="exact stat-window range id does not match source contract",
                    )
                range_row = connection.execute(
                    "SELECT source_path, source_pointer, grids_json FROM ranges WHERE range_id=?",
                    (row["range_id"],),
                ).fetchone()
                if range_row is None:
                    return _unsupported(
                        operator_id=operator_id,
                        skill_index=skill_index,
                        skill_id=skill_id,
                        level=level,
                        source_pointer=pointers,
                        field_mapping=mapping,
                        reason="exact stat-window range grid is absent from public knowledge",
                    )
                pointers["skill_range"] = (
                    f"{range_row['source_path']}#{range_row['source_pointer']}"
                )
                try:
                    skill_range_offsets = tuple(
                        (int(item["col"]), int(item["row"]))
                        for item in json.loads(str(range_row["grids_json"]))
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    return _unsupported(
                        operator_id=operator_id,
                        skill_index=skill_index,
                        skill_id=skill_id,
                        level=level,
                        source_pointer=pointers,
                        field_mapping=mapping,
                        reason="exact stat-window range grid is malformed",
                    )
                if not skill_range_offsets:
                    return _unsupported(
                        operator_id=operator_id,
                        skill_index=skill_index,
                        skill_id=skill_id,
                        level=level,
                        source_pointer=pointers,
                        field_mapping=mapping,
                        reason="exact stat-window range grid is empty",
                    )
                range_additive = int(range_spec["additive_tiles"])
                effects.append(
                    StatModifierEffectDefinition(
                        effect_id=(
                            f"{operator_id}:{skill_index}:{skill_id}:l{level}:"
                            f"{EffectStat.ATTACK_RANGE_TILES.value}"
                        ),
                        target=EffectTargetSelector.SELF,
                        stat=EffectStat.ATTACK_RANGE_TILES,
                        additive=range_additive,
                        duration_ticks=duration_ticks,
                    )
                )
                mapped_modifiers.append(
                    {
                        "source_field": "range_id",
                        "source_value": row["range_id"],
                        "effect_stat": EffectStat.ATTACK_RANGE_TILES.value,
                        "additive": range_additive,
                        "exact_offsets": [list(item) for item in skill_range_offsets],
                    }
                )
            sp_cost = source_sp_cost * TICKS_PER_SECOND
            initial_sp = source_initial_sp * TICKS_PER_SECOND
            mapping["simulator_sp"] = {
                "ticks_per_second": TICKS_PER_SECOND,
                "sp_cost": sp_cost,
                "initial_sp": initial_sp,
                "sp_per_tick": sp_per_tick,
            }
            mapping["exact_stat_window"] = {
                "modifiers": mapped_modifiers,
                "omitted_mechanics": list(exact_stat_spec["omitted_mechanics"]),
                "interpretation": "conservative-lower-bound",
            }
            definition = SkillDefinition(
                skill_id=f"{operator_id}:{skill_index}:{skill_id}:l{level}",
                sp_cost=sp_cost,
                initial_sp=initial_sp,
                sp_per_tick=sp_per_tick,
                duration_ticks=duration_ticks,
                attack_range_offsets=skill_range_offsets,
                effects=tuple(effects),
            )
            return CompiledSkillPrototype(
                operator_id,
                skill_index,
                skill_id,
                level,
                _EXACT_STAT_WINDOW,
                definition,
                pointers,
                mapping,
                FidelityLevel.C,
            )
        ammo_scale = entry_by_key.get("attack@atk_scale")
        ammo_count = _integer(
            entry_by_key.get("attack@trigger_time"),
            name="attack@trigger_time",
            positive=True,
        )
        interval_ticks = _integer_ticks(
            attack_interval_seconds,
            name="attack_interval_seconds",
            positive=True,
        )
        if (
            (row["skill_type"], row["duration_type"], row["sp_type"])
            in {(1, 1, 1), (2, 1, 1)}
            and isinstance(ammo_scale, (int, float))
            and not isinstance(ammo_scale, bool)
            and float(ammo_scale) > 1.0
            and ammo_count is not None
            and interval_ticks is not None
        ):
            source_sp_cost = _integer(row["sp_cost"], name="sp_cost", positive=True)
            source_initial_sp = _integer(row["initial_sp"], name="initial_sp")
            sp_per_tick = _integer(row["sp_increment"], name="sp_increment")
            if (
                source_sp_cost is None
                or source_initial_sp is None
                or sp_per_tick is None
                or source_initial_sp > source_sp_cost
            ):
                return _unsupported(
                    operator_id=operator_id, skill_index=skill_index,
                    skill_id=skill_id, level=level, source_pointer=pointers,
                    field_mapping=mapping,
                    reason="ammo skill SP fields are not representable",
                )
            multiplier_milli = round(float(ammo_scale) * 1000)
            duration_ticks = ammo_count * interval_ticks
            sp_cost = source_sp_cost * TICKS_PER_SECOND
            initial_sp = source_initial_sp * TICKS_PER_SECOND
            mapping.update(
                {
                    "simulator_sp": {
                        "ticks_per_second": TICKS_PER_SECOND,
                        "sp_cost": sp_cost,
                        "initial_sp": initial_sp,
                        "sp_per_tick": sp_per_tick,
                    },
                    "ammo_attack": {
                        "multiplier_milli": multiplier_milli,
                        "ammo_count": ammo_count,
                        "attack_interval_ticks": interval_ticks,
                        "duration_ticks": duration_ticks,
                        "omitted_blackboard_keys": sorted(
                            set(entry_by_key)
                            - {"attack@atk_scale", "attack@trigger_time", "range_id"}
                        ),
                    },
                }
            )
            definition = SkillDefinition(
                skill_id=f"{operator_id}:{skill_index}:{skill_id}:l{level}",
                sp_cost=sp_cost,
                initial_sp=initial_sp,
                sp_per_tick=sp_per_tick,
                duration_ticks=duration_ticks,
                effects=(StatModifierEffectDefinition(
                    effect_id=f"{operator_id}:{skill_index}:{skill_id}:l{level}:atk",
                    target=EffectTargetSelector.SELF,
                    stat=EffectStat.ATTACK,
                    multiplier_milli=multiplier_milli,
                    duration_ticks=duration_ticks,
                ),),
            )
            return CompiledSkillPrototype(
                operator_id, skill_index, skill_id, level,
                _AMMO_ATTACK_WINDOW, definition, pointers, mapping, FidelityLevel.C,
            )
        if (row["skill_type"], row["duration_type"], row["sp_type"]) != (1, 0, 1):
            return _unsupported(operator_id=operator_id, skill_index=skill_index, skill_id=skill_id,
                level=level, source_pointer=pointers, field_mapping=mapping,
                reason="skill trigger, duration, or SP type is outside the allowlist")
        if not isinstance(entries, list) or len(entries) != 1 or entries[0].get("key") != "atk":
            return _unsupported(operator_id=operator_id, skill_index=skill_index, skill_id=skill_id,
                level=level, source_pointer=pointers, field_mapping=mapping,
                reason="blackboard is not the exact allowlisted atk-only prototype")
        source_sp_cost = _integer(row["sp_cost"], name="sp_cost", positive=True)
        source_initial_sp = _integer(row["initial_sp"], name="initial_sp")
        sp_per_tick = _integer(row["sp_increment"], name="sp_increment")
        duration_ticks = _integer_ticks(row["duration"], name="duration", positive=True)
        atk_fraction = entries[0].get("value")
        if (
            source_sp_cost is None
            or source_initial_sp is None
            or sp_per_tick is None
            or duration_ticks is None
            or source_initial_sp > source_sp_cost
            or not isinstance(atk_fraction, (int, float))
            or isinstance(atk_fraction, bool)
        ):
            return _unsupported(operator_id=operator_id, skill_index=skill_index, skill_id=skill_id,
                level=level, source_pointer=pointers, field_mapping=mapping,
                reason="SP, duration, or atk blackboard value is not representable as deterministic ticks")
        # Public skill SP is denominated per second, while the deterministic
        # engine accrues integer SP units every 10 Hz tick.  Scale the stock
        # and threshold into tenths of SP so one source SP/second remains one
        # simulator unit/tick instead of charging ten times too quickly.
        sp_cost = source_sp_cost * TICKS_PER_SECOND
        initial_sp = source_initial_sp * TICKS_PER_SECOND
        mapping["simulator_sp"] = {
            "ticks_per_second": TICKS_PER_SECOND,
            "sp_cost": sp_cost,
            "initial_sp": initial_sp,
            "sp_per_tick": sp_per_tick,
        }
        multiplier_milli = round((1.0 + float(atk_fraction)) * 1000)
        if abs((1.0 + float(atk_fraction)) * 1000 - multiplier_milli) > 1e-9 or multiplier_milli in (0, 1000):
            return _unsupported(operator_id=operator_id, skill_index=skill_index, skill_id=skill_id,
                level=level, source_pointer=pointers, field_mapping=mapping,
                reason="atk blackboard value is not a non-noop integer-milli multiplier")
        mapping["atk"] = {"blackboard_key": "atk", "multiplier_milli": multiplier_milli}
        definition = SkillDefinition(
            skill_id=f"{operator_id}:{skill_index}:{skill_id}:l{level}",
            sp_cost=sp_cost, initial_sp=initial_sp, sp_per_tick=sp_per_tick,
            duration_ticks=duration_ticks,
            effects=(StatModifierEffectDefinition(
                effect_id=f"{operator_id}:{skill_index}:{skill_id}:l{level}:atk",
                target=EffectTargetSelector.SELF, stat=EffectStat.ATTACK,
                multiplier_milli=multiplier_milli, duration_ticks=duration_ticks,
            ),),
        )
        return CompiledSkillPrototype(operator_id, skill_index, skill_id, level, _ATTACK_WINDOW,
            definition, pointers, mapping, FidelityLevel.C)
    finally:
        connection.close()


__all__ = ["CompiledSkillPrototype", "TICKS_PER_SECOND", "compile_operator_skill_prototype"]
