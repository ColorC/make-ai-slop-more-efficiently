"""Deterministic, snapshot-backed execution of translated source wave schedules."""

from __future__ import annotations

from dataclasses import dataclass
import json

from .models import (
    ContentBundle,
    SourceWaveActionDefinition,
    SourceWaveScenarioAssignment,
    SourceWaveScheduleAdvance,
    SourceWaveScheduleEvent,
    SourceWaveScheduleSnapshot,
    SpawnDefinition,
)


class SourceWaveScheduleError(ValueError):
    """Raised when source schedule content or scenario state is incomplete."""


_BLOCKING_MECHANISMS = frozenset({"route_teleport_metadata"})
_GROUP_FIELDS = (
    ("random_spawn_group", "random_spawn_group_key"),
    ("random_spawn_group_pack", "random_spawn_group_pack_key"),
    ("hidden_group", "hidden_group"),
)


@dataclass(frozen=True, slots=True)
class _ActionRow:
    tick: int
    wave_index: int
    fragment_index: int
    action_index: int
    action: SourceWaveActionDefinition
    spawn: SpawnDefinition | None

    @property
    def cursor(self) -> tuple[int, int, int]:
        return (self.wave_index, self.fragment_index, self.action_index)


def _scenario_selects(
    spawn: SpawnDefinition,
    assignments: dict[tuple[str, str], SourceWaveScenarioAssignment],
) -> bool:
    for group_kind, field in _GROUP_FIELDS:
        group_key = getattr(spawn, field)
        if not group_key:
            continue
        assignment = assignments[(group_kind, group_key)]
        if assignment.enabled is not None:
            if not assignment.enabled:
                return False
            continue
        selected = (
            (assignment.spawn_id,) if assignment.spawn_id is not None else assignment.spawn_ids
        )
        if spawn.spawn_id not in selected:
            return False
    return True


def _compile_rows(
    content: ContentBundle,
    *,
    scenario_assignments: dict[tuple[str, str], SourceWaveScenarioAssignment] | None = None,
) -> tuple[_ActionRow, ...]:
    if "source_wave_schedule" not in content.required_mechanisms:
        raise SourceWaveScheduleError("content does not require source_wave_schedule")
    if not content.source_waves:
        raise SourceWaveScheduleError("source_wave_schedule requires source_waves")
    routes_by_source = {
        route.source_route_index: route.route_id
        for route in content.routes
        if route.source_route_index is not None
    }
    spawns_by_source: dict[tuple[int, int, int], SpawnDefinition] = {}
    for spawn in content.spawns:
        indices = (
            spawn.source_wave_index,
            spawn.source_fragment_index,
            spawn.source_action_index,
        )
        if any(index is None for index in indices):
            raise SourceWaveScheduleError("source schedule spawn lacks source indices")
        key = (int(indices[0]), int(indices[1]), int(indices[2]))
        if key in spawns_by_source:
            raise SourceWaveScheduleError("source schedule spawn indices are ambiguous")
        spawns_by_source[key] = spawn

    rows: list[_ActionRow] = []
    runtime_keys: set[tuple[int, int, int]] = set()
    wave_clock = 0
    for wave_ordinal, wave in enumerate(content.source_waves):
        if wave.source_index != wave_ordinal:
            raise SourceWaveScheduleError("source wave indices must be contiguous")
        wave_clock += wave.pre_delay_ticks
        fragment_clock = wave_clock
        for fragment_ordinal, fragment in enumerate(wave.fragments):
            if fragment.source_index != fragment_ordinal:
                raise SourceWaveScheduleError("source fragment indices must be contiguous")
            fragment_clock += fragment.pre_delay_ticks
            fragment_end = fragment_clock
            for action_ordinal, action in enumerate(fragment.actions):
                if action.source_index != action_ordinal:
                    raise SourceWaveScheduleError("source action indices must be contiguous")
                # actionType=5 is a source-only route-preview row.  The source
                # translator deliberately does not materialize it as a combat
                # spawn; keeping it in the clock/audit stream is nevertheless
                # required for lossless stage schedules such as main_04-01.
                if action.action_type not in {0, 5, 6}:
                    raise SourceWaveScheduleError(
                        f"unsupported source wave action type: {action.action_type}"
                    )
                try:
                    source = json.loads(action.source_json)
                except json.JSONDecodeError as exc:
                    raise SourceWaveScheduleError("source action JSON is malformed") from exc
                if not isinstance(source, dict) or source.get("actionType") != action.action_type:
                    raise SourceWaveScheduleError("source action JSON does not match action_type")
                if source.get("managedByScheduler") is False:
                    raise SourceWaveScheduleError(
                        "source action is not declared as managed by the scheduler"
                    )
                if source.get("blockFragment") or source.get("forceBlockWaveInBranch"):
                    raise SourceWaveScheduleError(
                        "blocking source wave flags require an explicit runtime contract"
                    )
                key = (wave_ordinal, fragment_ordinal, action_ordinal)
                action_tick = max(1, fragment_clock + action.pre_delay_ticks)
                spawn = spawns_by_source.get(key)
                is_runtime = action.action_type == 0 and bool(action.key)
                if is_runtime:
                    if action.count <= 0 or action.route_index is None:
                        raise SourceWaveScheduleError(
                            "runtime source action requires positive count and route"
                        )
                    route_id = routes_by_source.get(action.route_index)
                    if route_id is None:
                        raise SourceWaveScheduleError("runtime source action route is unknown")
                    if spawn is None:
                        raise SourceWaveScheduleError(
                            "runtime source action lacks translated spawn"
                        )
                    expected_spawn_id = f"w{wave_ordinal}-f{fragment_ordinal}-a{action_ordinal}"
                    if (
                        spawn.spawn_id != expected_spawn_id
                        or (scenario_assignments is None and spawn.tick != action_tick)
                        or spawn.enemy_id != action.key
                        or spawn.route_id != route_id
                        or spawn.count != action.count
                        or spawn.interval_ticks != action.interval_ticks
                        or spawn.random_spawn_group_key != action.random_spawn_group_key
                        or spawn.random_spawn_group_pack_key != action.random_spawn_group_pack_key
                        or spawn.hidden_group != action.hidden_group
                    ):
                        raise SourceWaveScheduleError(
                            "translated spawn does not match its source action"
                        )
                    runtime_keys.add(key)
                    selected = scenario_assignments is None or _scenario_selects(
                        spawn, scenario_assignments
                    )
                    if selected and not bool(source.get("dontBlockWave")):
                        fragment_end = max(
                            fragment_end,
                            fragment_clock
                            + action.pre_delay_ticks
                            + (action.count - 1) * action.interval_ticks,
                        )
                elif spawn is not None:
                    raise SourceWaveScheduleError("non-spawn source action gained a runtime spawn")
                rows.append(
                    _ActionRow(
                        tick=action_tick,
                        wave_index=wave_ordinal,
                        fragment_index=fragment_ordinal,
                        action_index=action_ordinal,
                        action=action,
                        spawn=spawn,
                    )
                )
            fragment_clock = fragment_end
        wave_clock = fragment_clock + wave.post_delay_ticks
    if set(spawns_by_source) != runtime_keys:
        raise SourceWaveScheduleError("translated spawns are not source-action exact")
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row.tick,
                row.wave_index,
                row.fragment_index,
                row.action_index,
            ),
        )
    )


def audit_source_wave_schedule(content: ContentBundle) -> dict[str, int]:
    """Validate lossless translation without authorizing runtime execution."""

    rows = _compile_rows(content)
    return {
        "source_waves": len(content.source_waves),
        "source_actions": len(rows),
        "runtime_spawns": sum(row.spawn is not None for row in rows),
    }


class SourceWaveSchedule:
    """Advance a fully scenario-bound source schedule without any RNG draw."""

    def __init__(
        self,
        content: ContentBundle,
        *,
        source_package_hash: str,
        scenario_id: str,
        scenario_assignments: tuple[SourceWaveScenarioAssignment, ...],
    ) -> None:
        self.content = content
        self._rows = _compile_rows(content)
        blockers = set(content.required_mechanisms) & _BLOCKING_MECHANISMS
        if blockers:
            raise SourceWaveScheduleError(
                f"source wave execution still requires unsupported mechanics: {sorted(blockers)}"
            )
        if len(source_package_hash) != 64 or any(
            character not in "0123456789abcdef" for character in source_package_hash
        ):
            raise SourceWaveScheduleError("source package hash must be lowercase SHA-256")
        if not scenario_id:
            raise SourceWaveScheduleError("source wave execution requires scenario_id")
        assignments = tuple(
            sorted(
                scenario_assignments,
                key=lambda item: (item.group_kind, item.group_key),
            )
        )
        assignment_map = {(item.group_kind, item.group_key): item for item in assignments}
        if len(assignment_map) != len(assignments):
            raise SourceWaveScheduleError("scenario assignment groups are ambiguous")
        options: dict[tuple[str, str], set[str]] = {}
        for row in self._rows:
            if row.spawn is None:
                continue
            for group_kind, field in _GROUP_FIELDS:
                group_key = getattr(row.spawn, field)
                if group_key:
                    options.setdefault((group_kind, group_key), set()).add(row.spawn.spawn_id)
        if set(assignment_map) != set(options):
            raise SourceWaveScheduleError(
                "random and hidden groups require one complete scenario assignment"
            )
        for group, assignment in assignment_map.items():
            if assignment.enabled is not None:
                continue
            selected = (
                frozenset({assignment.spawn_id})
                if assignment.spawn_id is not None
                else frozenset(assignment.spawn_ids)
            )
            if not selected or not selected <= options[group]:
                raise SourceWaveScheduleError("scenario assignment selects an invalid option")
            if assignment.group_kind == "random_spawn_group_pack" and len(selected) != 1:
                raise SourceWaveScheduleError(
                    "random spawn group packs require exactly one selected spawn"
                )
        self.source_package_hash = source_package_hash
        self.scenario_id = scenario_id
        self.scenario_assignments = assignments
        self._assignment_map = assignment_map
        self._rows = _compile_rows(content, scenario_assignments=assignment_map)
        self._cursor = 0
        self._tick = 0
        self._sequence = 0
        self._emitted_spawn_ids: list[str] = []
        self.snapshot_loaded_event: SourceWaveScheduleEvent | None = None

    def _selected(self, spawn: SpawnDefinition) -> bool:
        return _scenario_selects(spawn, self._assignment_map)

    @property
    def selected_spawn_ticks(self) -> dict[str, int]:
        return {
            row.spawn.spawn_id: row.tick
            for row in self._rows
            if row.spawn is not None and self._selected(row.spawn)
        }

    @property
    def unharmful_spawn_ids(self) -> frozenset[str]:
        return frozenset(
            row.spawn.spawn_id
            for row in self._rows
            if row.spawn is not None
            and self._selected(row.spawn)
            and bool(json.loads(row.action.source_json).get("isUnharmfulAndAlwaysCountAsKilled"))
        )

    @property
    def final_tick(self) -> int:
        return max(
            (
                row.tick + (row.spawn.count - 1) * row.spawn.interval_ticks
                if row.spawn is not None and self._selected(row.spawn)
                else row.tick
            )
            for row in self._rows
        )

    def _snapshot(self) -> SourceWaveScheduleSnapshot:
        row = self._rows[min(self._cursor, len(self._rows) - 1)]
        return SourceWaveScheduleSnapshot(
            content_version=self.content.client_version,
            source_package_hash=self.source_package_hash,
            scenario_id=self.scenario_id,
            scenario_assignments=self.scenario_assignments,
            tick=self._tick,
            wave_index=row.wave_index,
            fragment_index=row.fragment_index,
            action_index=row.action_index,
            next_occurrence_tick=(
                self._rows[self._cursor].tick if self._cursor < len(self._rows) else None
            ),
            emitted_spawn_ids=tuple(self._emitted_spawn_ids),
        )

    @property
    def snapshot(self) -> SourceWaveScheduleSnapshot:
        return self._snapshot()

    def _event(
        self,
        kind: str,
        row: _ActionRow,
        before: tuple[int, int, int],
        after: tuple[int, int, int],
        *,
        details: dict[str, object] | None = None,
    ) -> SourceWaveScheduleEvent:
        self._sequence += 1
        spawn = row.spawn
        return SourceWaveScheduleEvent(
            sequence=self._sequence,
            tick=row.tick,
            kind=kind,
            wave_index=row.wave_index,
            fragment_index=row.fragment_index,
            action_index=row.action_index,
            route_id=spawn.route_id if spawn else None,
            enemy_id=spawn.enemy_id if spawn else None,
            spawn_id=spawn.spawn_id if spawn else None,
            cursor_before=before,
            cursor_after=after,
            details=details or {},
        )

    def advance_to(self, tick: int) -> SourceWaveScheduleAdvance:
        if tick < self._tick:
            raise SourceWaveScheduleError("source wave schedule cannot move backwards")
        events: list[SourceWaveScheduleEvent] = []
        while self._cursor < len(self._rows) and self._rows[self._cursor].tick <= tick:
            row = self._rows[self._cursor]
            before = row.cursor
            next_cursor = (
                self._rows[self._cursor + 1].cursor
                if self._cursor + 1 < len(self._rows)
                else row.cursor
            )
            spawn = row.spawn
            groups = [
                {
                    "group_kind": group_kind,
                    "group_key": group_key,
                    "assignment": self._assignment_map[
                        (
                            group_kind,
                            group_key,
                        )
                    ].model_dump(mode="json"),
                }
                for group_kind, field in _GROUP_FIELDS
                if spawn is not None and (group_key := getattr(spawn, field))
            ]
            if groups:
                events.append(
                    self._event(
                        "scenario_binding_required",
                        row,
                        before,
                        before,
                        details={"scenario_id": self.scenario_id, "groups": groups},
                    )
                )
            events.append(
                self._event(
                    "source_action_scheduled",
                    row,
                    before,
                    before,
                    details={
                        "action_type": row.action.action_type,
                        "source_json": row.action.source_json,
                    },
                )
            )
            if spawn is not None and self._selected(spawn):
                self._emitted_spawn_ids.append(spawn.spawn_id)
                events.append(
                    self._event(
                        "runtime_spawn_scheduled",
                        row,
                        before,
                        before,
                        details={
                            "count": spawn.count,
                            "interval_ticks": spawn.interval_ticks,
                            "scenario_id": self.scenario_id,
                        },
                    )
                )
            events.append(
                self._event(
                    "wave_schedule_cursor_advanced",
                    row,
                    before,
                    next_cursor,
                    details={
                        "next_occurrence_tick": (
                            self._rows[self._cursor + 1].tick
                            if self._cursor + 1 < len(self._rows)
                            else None
                        )
                    },
                )
            )
            self._cursor += 1
        self._tick = tick
        return SourceWaveScheduleAdvance(snapshot=self.snapshot, events=tuple(events))

    @classmethod
    def restore(
        cls,
        content: ContentBundle,
        snapshot: SourceWaveScheduleSnapshot,
        *,
        source_package_hash: str,
        scenario_id: str,
        scenario_assignments: tuple[SourceWaveScenarioAssignment, ...],
    ) -> "SourceWaveSchedule":
        expected_assignments = tuple(
            sorted(
                scenario_assignments,
                key=lambda item: (item.group_kind, item.group_key),
            )
        )
        if (
            snapshot.source_package_hash != source_package_hash
            or snapshot.scenario_id != scenario_id
            or snapshot.scenario_assignments != expected_assignments
        ):
            raise SourceWaveScheduleError("source wave snapshot package or scenario pin mismatch")
        schedule = cls(
            content,
            source_package_hash=source_package_hash,
            scenario_id=scenario_id,
            scenario_assignments=expected_assignments,
        )
        rebuilt = schedule.advance_to(snapshot.tick).snapshot
        if rebuilt != snapshot:
            raise SourceWaveScheduleError(
                "source wave snapshot cursor, package, scenario or emissions mismatch"
            )
        row = schedule._rows[min(schedule._cursor, len(schedule._rows) - 1)]
        schedule.snapshot_loaded_event = SourceWaveScheduleEvent(
            sequence=schedule._sequence + 1,
            tick=snapshot.tick,
            kind="wave_schedule_snapshot_loaded",
            wave_index=snapshot.wave_index,
            fragment_index=snapshot.fragment_index,
            action_index=snapshot.action_index,
            route_id=row.spawn.route_id if row.spawn else None,
            enemy_id=row.spawn.enemy_id if row.spawn else None,
            spawn_id=row.spawn.spawn_id if row.spawn else None,
            cursor_before=(
                snapshot.wave_index,
                snapshot.fragment_index,
                snapshot.action_index,
            ),
            cursor_after=(
                snapshot.wave_index,
                snapshot.fragment_index,
                snapshot.action_index,
            ),
            details={"source_package_hash": snapshot.source_package_hash},
        )
        return schedule


__all__ = [
    "SourceWaveSchedule",
    "SourceWaveScheduleError",
    "audit_source_wave_schedule",
]
