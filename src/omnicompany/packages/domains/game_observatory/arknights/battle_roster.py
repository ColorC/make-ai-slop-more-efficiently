# [OMNI] origin=claude created_by=claude intent="read-only controller-bound battle roster identity + departures (F1-F4), fixture-testable, never HP"
"""Per-unit battle roster observation for Arknights.

This is the minimal implementation of F1-F4 from
``OBSERVATION-FACILITY-FEASIBILITY-20260906`` (承接
``X2B-REAL-OBSERVATION-CONTRACT-20260906``): a read-only, controller-bound view
of the live battle entity list that mints its own logical unit identity and
derives departures, without ever touching HP.

It reuses the reverse-engineered, controller-bound container path that was
verified read-only under paused stepping (surface doc §6.17-§6.27)::

    controller + 0x78        -> ObjectManager instance
    ObjectManager + 0x18     -> m_cachedList  (BCL List`1 of live units,
                                enemies and operators mixed together)
    List._items @ 0x10, List._size @ 0x18
    element pointers begin at   items + 0x20   (Il2CppArray header is 0x20 bytes)
    element word0            -> Il2CppClass;  class name @ class + 0x10
      Enemy:     <data> backing @ 1296 (0x510) -> EnemyData;
                 EnemyData.key @ 0x20  (species, e.g. ``enemy_1006_shield``)
      Character: key @ 328 (0x148)             (operator id, e.g. ``char_112_siege``)

Discipline enforced here:

* **Read-only.**  Nothing opens a writable handle; all access is through a
  narrow ``read`` seam that the ADB reader already satisfies.
* **Never HP.**  Live HP is a證據化 blocker (feasibility §3/§6).  This module
  does not read, infer, or expose HP.  Do not add it here.
* **Fail closed.**  Any structural mismatch -- size gate violation, unmapped
  pointer, unknown element class, or a key that will not decode -- rejects the
  whole snapshot.  A plausible-looking placeholder id is never emitted.
* **No hardcoded live addresses as success.**  The offsets below are a
  *candidate* build-family profile, verified only under paused stepping and
  deliberately kept out of the fail-closed locator's converge-to-one guarantee.
  They are defended structurally at read time.  The decode is exercised against
  synthetic fixtures, not against a pinned live address.

The roster block is intended as an *optional* extension of
``game-observatory.arknights-battle-observation.v1``: its absence must remain
legal (the aggregate fixed-frame path in :mod:`battle_observation` is untouched
and keeps reading only the six singleton blocks).  A future authorized live or
daemon caller drives :func:`observe_roster`; this module never starts a run.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol, Sequence

from .memory_observer import MemoryObservationError, decode_il2cpp_string

__all__ = [
    "Departure",
    "ObservedUnit",
    "RosterBindingHead",
    "RosterLayoutProfile",
    "RosterMemoryReader",
    "RosterObservationError",
    "RosterSnapshot",
    "RosterTracker",
    "RosterUnitIdentity",
    "RosterUpdate",
    "TORAPPU_ROSTER_2_7",
    "decode_roster_snapshot",
    "mapped_from_reader",
    "observe_roster",
    "roster_update_to_json",
]


class RosterObservationError(RuntimeError):
    """The roster channel cannot serve a trustworthy snapshot right now."""


@dataclass(frozen=True, slots=True)
class RosterLayoutProfile:
    """Container/element offsets for one client build family.

    These are a *candidate* set: verified read-only under paused stepping only,
    and not promoted into the fail-closed locator.  ``current_tile_field`` is
    left ``None`` on purpose -- an on-tile pointer offset was mentioned in the
    surface notes but never pinned in the artifacts, so departures are driven by
    container membership (the verified §6.18 signal) rather than a guessed
    field.  A future session that verifies the offset can set it here; until
    then the field is reported as ``None`` and plays no part in attribution.
    """

    profile_id: str
    object_manager_field: int = 0x78
    cached_list_field: int = 0x18
    list_items_field: int = 0x10
    list_size_field: int = 0x18
    array_data_offset: int = 0x20
    klass_name_pointer_field: int = 0x10
    enemy_data_backing_field: int = 0x510  # 1296
    enemy_data_key_field: int = 0x20
    character_key_field: int = 0x148  # 328
    enemy_klass_name: str = "Enemy"
    character_klass_name: str = "Character"
    current_tile_field: int | None = None
    max_units: int = 512
    string_read_bytes: int = 256


TORAPPU_ROSTER_2_7 = RosterLayoutProfile(profile_id="torappu-roster-2.7.x-candidate")


class RosterMemoryReader(Protocol):
    """The narrow read-only seam the roster decoder needs.

    :class:`.memory_locator.AdbProcessMemoryReader` already satisfies this, and
    a dict-backed fake satisfies it in tests without an emulator.
    """

    def read(self, address: int, size: int) -> bytes: ...


def mapped_from_reader(reader: object) -> Callable[[int], bool]:
    """Build an ``is-mapped`` predicate from a reader that exposes ``maps()``.

    Falls back to "everything is mapped" only when the reader has no maps view,
    which is the case for minimal fakes; live readers always provide maps and
    therefore always enforce the readable-region check.
    """

    maps = getattr(reader, "maps", None)
    if maps is None:
        return lambda _address: True
    readable = tuple((r.start, r.end) for r in maps() if getattr(r, "readable", True))

    def _mapped(address: int) -> bool:
        return any(start <= address < end for start, end in readable)

    return _mapped


@dataclass(frozen=True, slots=True)
class _BoundReader:
    """A reader plus a mapping predicate, with fail-closed pointer helpers."""

    reader: RosterMemoryReader
    mapped: Callable[[int], bool]

    def deref(self, address: int, *, label: str) -> int:
        if address <= 0 or not self.mapped(address):
            raise RosterObservationError(f"{label}: source {address:#x} is not mapped")
        word = self.reader.read(address, 8)
        if len(word) != 8:
            raise RosterObservationError(f"{label}: short read at {address:#x}")
        return struct.unpack_from("<Q", word, 0)[0]

    def pointer(self, address: int, *, label: str) -> int:
        value = self.deref(address, label=label)
        if value == 0 or value % 8 != 0 or not self.mapped(value):
            raise RosterObservationError(
                f"{label}: {value:#x} is not a non-zero, aligned, mapped pointer"
            )
        return value

    def read_at(self, address: int, size: int, *, label: str) -> bytes:
        if address <= 0 or not self.mapped(address):
            raise RosterObservationError(f"{label}: {address:#x} is not mapped")
        blob = self.reader.read(address, size)
        if len(blob) != size:
            raise RosterObservationError(f"{label}: short read at {address:#x}")
        return blob


@dataclass(frozen=True, slots=True)
class ObservedUnit:
    """One live unit decoded from a single roster sample (before id minting)."""

    slot_index: int
    slot_ptr: int
    klass: str  # "enemy" | "operator"
    species_key: str  # enemy_id or char id
    current_tile_ptr: int | None


@dataclass(frozen=True, slots=True)
class RosterBindingHead:
    """F4: what the roster was read through, so a consumer can judge continuity."""

    controller_ptr: int
    object_manager_ptr: int
    cached_list_ptr: int
    pid: int
    start_ticks: int
    il2cpp_sha256: str

    def key(self) -> tuple[int, int, int, str]:
        """Identity that must hold for a departure diff to be legal."""

        return (self.controller_ptr, self.pid, self.start_ticks, self.il2cpp_sha256)


@dataclass(frozen=True, slots=True)
class RosterSnapshot:
    """F1 + F2 + F4: one fresh, structurally verified roster sample."""

    binding_head: RosterBindingHead
    size: int  # List._size, already gated against should_be_present
    should_be_present: int
    units: tuple[ObservedUnit, ...]
    observed_monotonic: float
    observed_at: str
    tick: int


@dataclass(frozen=True, slots=True)
class RosterUnitIdentity:
    """F1 with a minted logical id stable while the slot holds the same unit."""

    logical_id: str
    slot_ptr: int
    slot_generation: int
    klass: str
    species_key: str
    current_tile_ptr: int | None


@dataclass(frozen=True, slots=True)
class Departure:
    """F3: a unit that left the container between two fresh, same-controller samples."""

    logical_id: str
    slot_ptr: int
    species_key: str
    klass: str
    kind: str  # "kill" | "leak" | "unknown"
    tick: int


@dataclass(frozen=True, slots=True)
class RosterUpdate:
    """The tracker's verdict for one sample: current roster + any departures."""

    roster: tuple[RosterUnitIdentity, ...]
    departures: tuple[Departure, ...]
    continuity: str  # first_sample | controller_reset | fresh | gap_too_large
    binding_head: RosterBindingHead
    snapshot: RosterSnapshot


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_species(
    bound: _BoundReader,
    element_ptr: int,
    klass_name: str,
    profile: RosterLayoutProfile,
) -> tuple[str, str]:
    """Return ``(klass, species_key)`` for one element, or fail closed."""

    if klass_name == profile.enemy_klass_name:
        data_ptr = bound.pointer(
            element_ptr + profile.enemy_data_backing_field, label="Enemy.<data>"
        )
        key_ptr = bound.pointer(
            data_ptr + profile.enemy_data_key_field, label="EnemyData.key"
        )
        klass = "enemy"
    elif klass_name == profile.character_klass_name:
        key_ptr = bound.pointer(
            element_ptr + profile.character_key_field, label="Character.key"
        )
        klass = "operator"
    else:
        raise RosterObservationError(f"unknown roster element class: {klass_name!r}")

    raw = bound.read_at(key_ptr, profile.string_read_bytes, label="species Il2CppString")
    try:
        species = decode_il2cpp_string(raw)
    except MemoryObservationError as error:
        raise RosterObservationError(f"species key would not decode: {error}") from error
    if not species:
        raise RosterObservationError("decoded species key is empty")
    return klass, species


def _element_class_name(
    bound: _BoundReader, element_ptr: int, profile: RosterLayoutProfile
) -> str:
    klass_ptr = bound.pointer(element_ptr, label="element klass pointer")
    name_ptr = bound.pointer(
        klass_ptr + profile.klass_name_pointer_field, label="klass name pointer"
    )
    raw = bound.read_at(name_ptr, 96, label="klass name")
    text = raw.split(b"\x00", 1)[0]
    try:
        name = text.decode("ascii")
    except UnicodeDecodeError as error:
        raise RosterObservationError("klass name is not ASCII") from error
    if not name or not name.isprintable():
        raise RosterObservationError("klass name is empty or unprintable")
    return name


def decode_roster_snapshot(
    reader: RosterMemoryReader,
    *,
    controller_ptr: int,
    should_be_present: int,
    pid: int,
    start_ticks: int,
    il2cpp_sha256: str,
    tick: int,
    observed_monotonic: float,
    mapped: Callable[[int], bool] | None = None,
    profile: RosterLayoutProfile = TORAPPU_ROSTER_2_7,
) -> RosterSnapshot:
    """Enumerate the live container once and return a gated, fully decoded sample.

    ``should_be_present`` is ``spawned - killed - leaked`` from the aggregate
    30Hz frame (F2 gate).  If the container size disagrees, the whole snapshot
    is rejected rather than served.
    """

    if should_be_present < 0:
        raise RosterObservationError(
            f"should_be_present is negative ({should_be_present}); aggregate frame is incoherent"
        )
    bound = _BoundReader(reader, mapped or mapped_from_reader(reader))

    object_manager_ptr = bound.pointer(
        controller_ptr + profile.object_manager_field, label="controller.m_objectManger"
    )
    cached_list_ptr = bound.pointer(
        object_manager_ptr + profile.cached_list_field, label="ObjectManager.m_cachedList"
    )

    head = bound.read_at(cached_list_ptr, 0x20, label="m_cachedList head")
    items_ptr = struct.unpack_from("<Q", head, profile.list_items_field)[0]
    size = struct.unpack_from("<i", head, profile.list_size_field)[0]

    if not 0 <= size <= profile.max_units:
        raise RosterObservationError(f"implausible roster size: {size}")
    # F2: the container must agree with the aggregate ladder or the whole frame
    # is untrustworthy -- reject rather than serve a partial roster.
    if size != should_be_present:
        raise RosterObservationError(
            f"roster size {size} != should_be_present {should_be_present}"
        )

    units: list[ObservedUnit] = []
    if size:
        if items_ptr == 0 or not bound.mapped(items_ptr):
            raise RosterObservationError("m_cachedList._items is null while size > 0")
        element_bytes = bound.read_at(
            items_ptr + profile.array_data_offset, size * 8, label="roster element array"
        )
        for index in range(size):
            element_ptr = struct.unpack_from("<Q", element_bytes, index * 8)[0]
            if element_ptr == 0 or element_ptr % 8 != 0 or not bound.mapped(element_ptr):
                raise RosterObservationError(
                    f"roster slot {index} holds a bad pointer {element_ptr:#x}"
                )
            klass_name = _element_class_name(bound, element_ptr, profile)
            klass, species = _decode_species(bound, element_ptr, klass_name, profile)
            current_tile_ptr: int | None = None
            if profile.current_tile_field is not None:
                current_tile_ptr = bound.deref(
                    element_ptr + profile.current_tile_field, label="current_tile"
                )
            units.append(
                ObservedUnit(
                    slot_index=index,
                    slot_ptr=element_ptr,
                    klass=klass,
                    species_key=species,
                    current_tile_ptr=current_tile_ptr,
                )
            )

    binding_head = RosterBindingHead(
        controller_ptr=controller_ptr,
        object_manager_ptr=object_manager_ptr,
        cached_list_ptr=cached_list_ptr,
        pid=pid,
        start_ticks=start_ticks,
        il2cpp_sha256=il2cpp_sha256,
    )
    return RosterSnapshot(
        binding_head=binding_head,
        size=size,
        should_be_present=should_be_present,
        units=tuple(units),
        observed_monotonic=observed_monotonic,
        observed_at=_now_iso(),
        tick=tick,
    )


@dataclass
class _SlotState:
    logical_id: str
    generation: int
    klass: str
    species_key: str


class RosterTracker:
    """F3: mint logical ids and derive departures across fresh samples.

    Departures are produced **only** when the previous and current samples were
    read through the *same* controller (same binding head).  A controller change
    -- a new battle, or a restarted/reinstalled process -- resets all state and
    yields no departures, because pool slots cannot be compared across
    controllers.  Object-pool reuse (a slot that returns holding a different
    unit) mints a fresh logical id; a bare pointer is never treated as identity.
    """

    def __init__(
        self,
        *,
        profile: RosterLayoutProfile = TORAPPU_ROSTER_2_7,
        max_continuity_gap_seconds: float = 0.5,
    ) -> None:
        self.profile = profile
        self.max_continuity_gap_seconds = max_continuity_gap_seconds
        self._binding_key: tuple[int, int, int, str] | None = None
        self._active: dict[int, _SlotState] = {}
        self._generation: dict[int, int] = {}
        self._counter = 0
        self._last_monotonic: float | None = None

    def _mint(self, slot_ptr: int, unit: ObservedUnit) -> _SlotState:
        generation = self._generation.get(slot_ptr, -1) + 1
        self._generation[slot_ptr] = generation
        self._counter += 1
        state = _SlotState(
            logical_id=f"L{self._counter}",
            generation=generation,
            klass=unit.klass,
            species_key=unit.species_key,
        )
        self._active[slot_ptr] = state
        return state

    def _reset(self, binding_key: tuple[int, int, int, str]) -> None:
        self._binding_key = binding_key
        self._active = {}
        self._generation = {}
        # logical-id counter is intentionally monotone across resets so an id is
        # never reused for a different physical unit within a tracker's life.

    def _roster_from_active(
        self, snapshot: RosterSnapshot
    ) -> tuple[RosterUnitIdentity, ...]:
        by_slot = {unit.slot_ptr: unit for unit in snapshot.units}
        roster: list[RosterUnitIdentity] = []
        for unit in snapshot.units:
            state = self._active[unit.slot_ptr]
            roster.append(
                RosterUnitIdentity(
                    logical_id=state.logical_id,
                    slot_ptr=unit.slot_ptr,
                    slot_generation=state.generation,
                    klass=unit.klass,
                    species_key=unit.species_key,
                    current_tile_ptr=unit.current_tile_ptr,
                )
            )
        assert len(roster) == len(by_slot)
        return tuple(roster)

    @staticmethod
    def _attribute(
        departed: Sequence[tuple[int, _SlotState]],
        *,
        killed_delta: int,
        leaked_delta: int,
        continuity_ok: bool,
        tick: int,
    ) -> tuple[Departure, ...]:
        count = len(departed)
        if count == 0:
            return ()
        if not continuity_ok:
            kind = "unknown"
        elif leaked_delta == 0 and killed_delta == count:
            kind = "kill"
        elif killed_delta == 0 and leaked_delta == count:
            kind = "leak"
        else:
            # Mixed or ambiguous ladder movement: never default to kill.
            kind = "unknown"
        return tuple(
            Departure(
                logical_id=state.logical_id,
                slot_ptr=slot_ptr,
                species_key=state.species_key,
                klass=state.klass,
                kind=kind,
                tick=tick,
            )
            for slot_ptr, state in departed
        )

    def update(
        self,
        snapshot: RosterSnapshot,
        *,
        killed_delta: int,
        leaked_delta: int,
    ) -> RosterUpdate:
        """Fold one fresh snapshot in; return the current roster and departures.

        ``killed_delta`` / ``leaked_delta`` are the change in the aggregate
        ``valid_killed_enemies`` / ``leaked_enemies`` counters since the
        previous accepted sample.  They are used only to *label* departures that
        the container diff already found; they never invent one.
        """

        if killed_delta < 0 or leaked_delta < 0:
            raise RosterObservationError("kill/leak deltas cannot be negative")

        binding_key = snapshot.binding_head.key()

        # First sample, or a controller/process change: no cross-controller diff.
        if self._binding_key is None or binding_key != self._binding_key:
            continuity = "first_sample" if self._binding_key is None else "controller_reset"
            self._reset(binding_key)
            for unit in snapshot.units:
                self._mint(unit.slot_ptr, unit)
            self._last_monotonic = snapshot.observed_monotonic
            return RosterUpdate(
                roster=self._roster_from_active(snapshot),
                departures=(),
                continuity=continuity,
                binding_head=snapshot.binding_head,
                snapshot=snapshot,
            )

        # Same controller: diff the slot set, honouring object-pool reuse.
        interval = (
            None
            if self._last_monotonic is None
            else snapshot.observed_monotonic - self._last_monotonic
        )
        continuity_ok = interval is not None and 0 <= interval <= self.max_continuity_gap_seconds
        continuity = "fresh" if continuity_ok else "gap_too_large"

        current = {unit.slot_ptr: unit for unit in snapshot.units}
        departed: list[tuple[int, _SlotState]] = []

        for slot_ptr, state in list(self._active.items()):
            unit = current.get(slot_ptr)
            if unit is None:
                # Slot left the container entirely.
                departed.append((slot_ptr, state))
                del self._active[slot_ptr]
            elif unit.species_key != state.species_key or unit.klass != state.klass:
                # Same pool slot, different occupant: old one departed, and the
                # newcomer must get a fresh logical id (minted below).
                departed.append((slot_ptr, state))
                del self._active[slot_ptr]

        # Mint ids for arrivals (new slots) and reused slots now holding a new unit.
        for unit in snapshot.units:
            if unit.slot_ptr not in self._active:
                self._mint(unit.slot_ptr, unit)

        departures = self._attribute(
            departed,
            killed_delta=killed_delta,
            leaked_delta=leaked_delta,
            continuity_ok=continuity_ok,
            tick=snapshot.tick,
        )
        self._last_monotonic = snapshot.observed_monotonic
        return RosterUpdate(
            roster=self._roster_from_active(snapshot),
            departures=departures,
            continuity=continuity,
            binding_head=snapshot.binding_head,
            snapshot=snapshot,
        )


def roster_update_to_json(update: RosterUpdate) -> dict[str, object]:
    """Serialise one update as the optional roster block.

    Its schema id is distinct from the aggregate frame's; a consumer that does
    not understand it, or a frame that omits it, must remain valid.
    """

    snapshot = update.snapshot
    head = update.binding_head
    return {
        "schema_id": "game-observatory.arknights-battle-roster.v1",
        "binding_head": {
            "controller": hex(head.controller_ptr),
            "object_manager": hex(head.object_manager_ptr),
            "cached_list": hex(head.cached_list_ptr),
            "pid": head.pid,
            "start_ticks": head.start_ticks,
            "il2cpp_sha256": head.il2cpp_sha256,
        },
        "size": snapshot.size,
        "should_be_present": snapshot.should_be_present,
        "observed_at": snapshot.observed_at,
        "tick": snapshot.tick,
        "continuity": update.continuity,
        "roster": [
            {
                "logical_id": entry.logical_id,
                "klass": entry.klass,
                "species_key": entry.species_key,
                "slot_ptr": hex(entry.slot_ptr),
                "slot_generation": entry.slot_generation,
                "current_tile_ptr": (
                    None if entry.current_tile_ptr is None else hex(entry.current_tile_ptr)
                ),
            }
            for entry in update.roster
        ],
        "departures": [
            {
                "logical_id": departure.logical_id,
                "klass": departure.klass,
                "species_key": departure.species_key,
                "slot_ptr": hex(departure.slot_ptr),
                "kind": departure.kind,
                "tick": departure.tick,
            }
            for departure in update.departures
        ],
    }


def observe_roster(
    reader: RosterMemoryReader,
    tracker: RosterTracker,
    *,
    controller_ptr: int,
    should_be_present: int,
    pid: int,
    start_ticks: int,
    il2cpp_sha256: str,
    tick: int,
    observed_monotonic: float,
    killed_delta: int,
    leaked_delta: int,
    mapped: Callable[[int], bool] | None = None,
    profile: RosterLayoutProfile = TORAPPU_ROSTER_2_7,
) -> RosterUpdate:
    """Decode one snapshot and fold it into ``tracker``.

    This is the seam a future authorized live/daemon caller invokes once per
    sample.  It performs no navigation, starts no run, and reads nothing beyond
    the container path.  All liveness/aggregate inputs (``should_be_present``,
    ``killed_delta``, ``leaked_delta``, the process identity) come from the
    already-served fixed-frame channel; this function does not re-derive them.
    """

    snapshot = decode_roster_snapshot(
        reader,
        controller_ptr=controller_ptr,
        should_be_present=should_be_present,
        pid=pid,
        start_ticks=start_ticks,
        il2cpp_sha256=il2cpp_sha256,
        tick=tick,
        observed_monotonic=observed_monotonic,
        mapped=mapped,
        profile=profile,
    )
    return tracker.update(
        snapshot, killed_delta=killed_delta, leaked_delta=leaked_delta
    )
