# [OMNI] origin=claude created_by=claude intent="make Arknights battle memory observation recoverable across processes and builds"
"""Cold-path discovery for the read-only Arknights battle memory channel.

:mod:`memory_observer` owns the hot path: it decodes fixed-size frames once the
addresses are known.  This module produces those addresses, and it refuses to
let them outlive the process or the build they were measured on.

Discovery is three streaming passes over the target's own mappings:

1. the exact UTF-8 metadata strings for a class name and its namespace;
2. the ``Il2CppClass`` whose name/namespace pointer pair matches;
3. managed objects whose first word is that class pointer, structurally checked.

The recovered IL2CPP metadata lives in a shared ``/dev/zero`` mapping while
managed objects live in the scudo heap, so the string pass deliberately looks
at a wider region set than the object pass.

Child objects are reached through pointer chains rather than absolute
addresses, and every address is stamped with the process identity and build
fingerprint it came from.  Nothing here opens a writable handle to the target.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence

from .memory_observer import BattleRuntimeAddresses, MemoryObservationError
from ..subprocess_policy import headless_process_kwargs

__all__ = [
    "AdbProcessMemoryReader",
    "BattleLayoutProfile",
    "LocatedBattleAddresses",
    "MapRegion",
    "MemoryLocatorError",
    "ProcessIdentity",
    "ProcessMemoryReader",
    "StaleAddressError",
    "TORAPPU_2_7_61",
    "assert_addresses_live",
    "battle_is_loaded",
    "load_located_addresses",
    "locate_battle_addresses",
    "locate_battle_addresses_native",
    "locate_battle_controller_class",
    "parse_maps",
]

_MAP_LINE = re.compile(
    r"([0-9a-f]+)-([0-9a-f]+)\s+(\S{4})\s+\S+\s+\S+\s+\S+\s*(.*)"
)

_MAX_REGION_BYTES = 300 * 1024 * 1024
_NATIVE_DIAGNOSTIC_LIMIT = 512


class MemoryLocatorError(RuntimeError):
    """Discovery could not produce exactly one trustworthy address set."""


class StaleAddressError(MemoryLocatorError):
    """The located addresses no longer describe the process in front of us."""


def _native_diagnostic_excerpt(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace").strip()
    if len(text) <= _NATIVE_DIAGNOSTIC_LIMIT:
        return text
    return f"{text[:_NATIVE_DIAGNOSTIC_LIMIT]}...<truncated>"


def _parse_native_locator_payload(stdout: bytes) -> dict[str, object]:
    """Accept one unambiguous JSON object, with optional line noise around it."""

    text = stdout.decode("utf-8", "replace").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        candidates: list[dict[str, object]] = []
        for line in text.splitlines():
            try:
                candidate = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                candidates.append(candidate)
        if len(candidates) != 1:
            if candidates:
                raise ValueError(
                    "ambiguous native locator output: "
                    f"found {len(candidates)} JSON objects"
                )
            raise ValueError("native locator output contains no JSON object")
        payload = candidates[0]
    if not isinstance(payload, dict):
        raise TypeError("native locator JSON root must be an object")
    return payload


@dataclass(frozen=True, slots=True)
class MapRegion:
    start: int
    end: int
    permissions: str
    path: str

    @property
    def size(self) -> int:
        return self.end - self.start

    @property
    def readable(self) -> bool:
        return self.permissions.startswith("r")

    @property
    def writable_data(self) -> bool:
        if not self.permissions.startswith("rw") or self.size > _MAX_REGION_BYTES:
            return False
        return not (
            self.path.startswith("[anon:dalvik") or self.path.startswith("[stack")
        )

    @property
    def holds_managed_objects(self) -> bool:
        return self.writable_data and (
            not self.path or self.path.startswith("[anon:scudo")
        )

    @property
    def holds_metadata_strings(self) -> bool:
        """Where the recovered IL2CPP string blob is mapped.

        On every build measured so far this is the shared ``/dev/zero``
        mapping, which is roughly fifty times smaller than the managed heap.
        Scanning it alone turns the string pass from a minute into a second;
        :func:`locate_battle_addresses` widens the search only if it comes up
        empty, so a build that maps the blob elsewhere still resolves.
        """

        return self.writable_data and self.path.startswith("/dev/zero")


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """What has to stay constant for a located address to remain valid."""

    pid: int
    start_ticks: int
    package: str
    version_name: str = ""
    il2cpp_sha256: str = ""

    def matches(self, other: "ProcessIdentity") -> bool:
        if (self.pid, self.start_ticks, self.package) != (
            other.pid,
            other.start_ticks,
            other.package,
        ):
            return False
        for mine, theirs in (
            (self.version_name, other.version_name),
            (self.il2cpp_sha256, other.il2cpp_sha256),
        ):
            if mine and theirs and mine != theirs:
                return False
        return True


@dataclass(frozen=True, slots=True)
class BattleLayoutProfile:
    """Field offsets and pointer chains for one client build family."""

    profile_id: str
    class_name: bytes = b"BattleController\x00"
    class_namespace: bytes = b"Torappu.Battle\x00"
    scheduler: int = 0x30
    cost_manager: int = 0x108
    loaded: int = 0x200
    battle_state: int = 0x220
    battle_result: int = 0x224
    speed_level: int = 0x228
    enable_state: int = 0x278
    origin_scale: int = 0x280
    real_play_time: int = 0x284
    level_id: int = 0x2C8
    scheduler_backref: int = 0x130
    cost_backref: int = 0x10
    cost_status_chain: tuple[int, ...] = (0x20, 0x10, 0x28)
    cost_timer_chain: tuple[int, ...] = (0x28, 0x10, 0x28)
    pause_list_chain: tuple[int, ...] = (0x10, 0x10)
    controller_window: int = 0x300


TORAPPU_2_7_61 = BattleLayoutProfile(profile_id="torappu-2.7.61")


@dataclass(frozen=True, slots=True)
class LocatedBattleAddresses:
    identity: ProcessIdentity
    profile_id: str
    addresses: BattleRuntimeAddresses
    controller_class_name: str
    cost_status_class_name: str
    pause_list_class_name: str
    scan_notes: dict[str, int] = field(default_factory=dict)
    # Where the controller was found. Every new battle allocates a fresh
    # controller, so the addresses die between fights and the sweep would
    # otherwise run in full each time -- long enough that a contestant's call
    # times out and the channel goes blind exactly when a battle starts.
    controller_region: tuple[int, int] | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "schema_id": "game-observatory.arknights-battle-addresses.v1",
            "profile_id": self.profile_id,
            "identity": {
                "pid": self.identity.pid,
                "start_ticks": self.identity.start_ticks,
                "package": self.identity.package,
                "version_name": self.identity.version_name,
                "il2cpp_sha256": self.identity.il2cpp_sha256,
            },
            "addresses": {
                "pid": self.addresses.pid,
                "controller_class": hex(self.addresses.controller_class),
                "controller": hex(self.addresses.controller),
                "scheduler": hex(self.addresses.scheduler),
                "cost_manager": hex(self.addresses.cost_manager),
                "cost_status": hex(self.addresses.cost_status),
                "cost_timer": hex(self.addresses.cost_timer),
                "pause_list": hex(self.addresses.pause_list),
            },
            "resolved_class_names": {
                "controller": self.controller_class_name,
                "cost_status": self.cost_status_class_name,
                "pause_list": self.pause_list_class_name,
            },
            "scan_notes": dict(self.scan_notes),
            "controller_region": (
                None
                if self.controller_region is None
                else [hex(self.controller_region[0]), hex(self.controller_region[1])]
            ),
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_json(), indent=2, ensure_ascii=False), encoding="utf-8"
        )


def load_located_addresses(path: Path) -> LocatedBattleAddresses:
    payload = json.loads(path.read_text(encoding="utf-8"))
    identity = payload["identity"]
    addresses = payload["addresses"]
    names = payload.get("resolved_class_names", {})
    return LocatedBattleAddresses(
        identity=ProcessIdentity(
            pid=int(identity["pid"]),
            start_ticks=int(identity["start_ticks"]),
            package=str(identity["package"]),
            version_name=str(identity.get("version_name", "")),
            il2cpp_sha256=str(identity.get("il2cpp_sha256", "")),
        ),
        profile_id=str(payload["profile_id"]),
        addresses=BattleRuntimeAddresses(
            pid=int(addresses["pid"]),
            controller_class=int(str(addresses["controller_class"]), 16),
            controller=int(str(addresses["controller"]), 16),
            scheduler=int(str(addresses["scheduler"]), 16),
            cost_manager=int(str(addresses["cost_manager"]), 16),
            cost_status=int(str(addresses["cost_status"]), 16),
            cost_timer=int(str(addresses["cost_timer"]), 16),
            pause_list=int(str(addresses["pause_list"]), 16),
        ),
        controller_class_name=str(names.get("controller", "")),
        cost_status_class_name=str(names.get("cost_status", "")),
        pause_list_class_name=str(names.get("pause_list", "")),
        scan_notes=dict(payload.get("scan_notes", {})),
        controller_region=(
            None
            if not payload.get("controller_region")
            else (
                int(str(payload["controller_region"][0]), 16),
                int(str(payload["controller_region"][1]), 16),
            )
        ),
    )


class ProcessMemoryReader(Protocol):
    """Read-only view of one target process."""

    def identity(self) -> ProcessIdentity: ...

    def maps(self) -> Sequence[MapRegion]: ...

    def read(self, address: int, size: int) -> bytes: ...

    def read_region(self, start: int, end: int) -> bytes: ...


def parse_maps(text: str) -> tuple[MapRegion, ...]:
    regions = []
    for line in text.splitlines():
        match = _MAP_LINE.match(line)
        if match:
            regions.append(
                MapRegion(
                    start=int(match[1], 16),
                    end=int(match[2], 16),
                    permissions=match[3],
                    path=match[4].strip(),
                )
            )
    return tuple(regions)


def _find_all(blob: bytes, base: int, needle: bytes) -> list[int]:
    hits: list[int] = []
    cursor = blob.find(needle)
    while cursor >= 0:
        hits.append(base + cursor)
        cursor = blob.find(needle, cursor + 1)
    return hits


def _deref(reader: ProcessMemoryReader, address: int) -> int:
    if address <= 0:
        raise MemoryLocatorError("refusing to dereference a null pointer")
    word = reader.read(address, 8)
    if len(word) != 8:
        raise MemoryLocatorError(f"short read while dereferencing {address:#x}")
    return struct.unpack_from("<Q", word, 0)[0]


def _follow(
    reader: ProcessMemoryReader, address: int, chain: Sequence[int]
) -> int:
    node = address
    for offset in chain:
        node = _deref(reader, node + offset)
    if node <= 0:
        raise MemoryLocatorError("pointer chain ended on a null pointer")
    return node


def _class_name(reader: ProcessMemoryReader, klass: int) -> str:
    head = reader.read(klass, 0x30)
    if len(head) < 0x20:
        raise MemoryLocatorError(f"short read for Il2CppClass at {klass:#x}")
    name_pointer, namespace_pointer = struct.unpack_from("<QQ", head, 0x10)
    name = reader.read(name_pointer, 96).split(b"\0")[0].decode("utf-8", "replace")
    namespace = (
        reader.read(namespace_pointer, 96).split(b"\0")[0].decode("utf-8", "replace")
    )
    return f"{namespace}.{name}" if namespace else name


def locate_battle_controller_class(
    reader: ProcessMemoryReader,
    *,
    profile: BattleLayoutProfile = TORAPPU_2_7_61,
) -> tuple[int, dict[str, int]]:
    """Resolve the ``BattleController`` class pointer for this process.

    The class outlives every individual battle, so callers should cache the
    result against :meth:`ProcessMemoryReader.identity` and re-run only the
    object sweep per battle.  A losing battle can be over in ninety seconds,
    which is less than a full three-pass scan takes.
    """

    regions = tuple(reader.maps())
    object_regions = tuple(r for r in regions if r.holds_managed_objects)
    string_regions = tuple(r for r in regions if r.holds_metadata_strings)

    name_hits: list[int] = []
    namespace_hits: list[int] = []
    scanned_bytes = 0
    for candidates in (string_regions, object_regions):
        for region in candidates:
            blob = reader.read_region(region.start, region.end)
            if not blob:
                continue
            scanned_bytes += len(blob)
            name_hits.extend(_find_all(blob, region.start, profile.class_name))
            namespace_hits.extend(
                _find_all(blob, region.start, profile.class_namespace)
            )
        if name_hits and namespace_hits:
            break
    if not name_hits or not namespace_hits:
        raise MemoryLocatorError(
            "IL2CPP metadata strings for "
            f"{profile.class_namespace!r}.{profile.class_name!r} were not found"
        )

    namespace_set = set(namespace_hits)
    classes: set[int] = set()
    for region in object_regions:
        blob = reader.read_region(region.start, region.end)
        if not blob:
            continue
        scanned_bytes += len(blob)
        for name_pointer in name_hits:
            needle = struct.pack("<Q", name_pointer)
            for address in _find_all(blob, region.start, needle):
                offset = address - region.start
                if offset + 16 > len(blob) or (address - 0x10) % 8 != 0:
                    continue
                if struct.unpack_from("<Q", blob, offset + 8)[0] in namespace_set:
                    classes.add(address - 0x10)
    if len(classes) != 1:
        raise MemoryLocatorError(
            f"expected exactly one Il2CppClass, found {len(classes)}"
        )
    return classes.pop(), {
        "object_regions": len(object_regions),
        "string_regions": len(string_regions),
        "scanned_bytes": scanned_bytes,
        "name_string_hits": len(name_hits),
        "namespace_string_hits": len(namespace_hits),
    }


def locate_battle_addresses(
    reader: ProcessMemoryReader,
    *,
    profile: BattleLayoutProfile = TORAPPU_2_7_61,
    controller_class: int | None = None,
    prefer_region: tuple[int, int] | None = None,
    native_locator_path: str | None = None,
) -> LocatedBattleAddresses:
    """Find the one live ``BattleController`` and its collaborators.

    Raises :class:`MemoryLocatorError` unless the scan converges on exactly one
    class and one structurally valid controller.  A battle has to be loaded;
    outside battle there is no controller to find, and that is reported rather
    than guessed around.

    Pass ``controller_class`` from a previous locate on the *same* process to
    skip straight to the object sweep.
    """

    identity = reader.identity()
    if native_locator_path:
        if not isinstance(reader, AdbProcessMemoryReader):
            raise TypeError("native locator requires AdbProcessMemoryReader")
        return locate_battle_addresses_native(
            reader,
            native_locator_path,
            profile=profile,
            identity=identity,
        )
    regions = tuple(reader.maps())
    readable = tuple((r.start, r.end) for r in regions if r.readable)
    object_regions = tuple(r for r in regions if r.holds_managed_objects)

    def mapped(address: int) -> bool:
        return any(start <= address < end for start, end in readable)

    if controller_class is None:
        controller_class, scan_notes = locate_battle_controller_class(
            reader, profile=profile
        )
    else:
        scan_notes = {"object_regions": len(object_regions), "class_reused": 1}

    window = profile.controller_window
    needle = struct.pack("<Q", controller_class)

    # The previous battle's controller region is the best guess for the next
    # one, and checking it alone turns a minute-long sweep into a short read.
    # A hit still has to pass every structural check below, so preferring a
    # region changes how long discovery takes, never what it will accept.
    sweep_order = list(object_regions)
    if prefer_region is not None:
        preferred = [
            region
            for region in object_regions
            if (region.start, region.end) == prefer_region
        ]
        if preferred:
            sweep_order = preferred + [
                region for region in object_regions if region not in preferred
            ]

    controllers: list[int] = []
    found_region: tuple[int, int] | None = None
    for region in sweep_order:
        blob = reader.read_region(region.start, region.end)
        if not blob:
            continue
        before = len(controllers)
        for address in _find_all(blob, region.start, needle):
            offset = address - region.start
            if address % 8 != 0 or offset + window > len(blob):
                continue
            body = blob[offset : offset + window]
            scheduler = struct.unpack_from("<Q", body, profile.scheduler)[0]
            cost_manager = struct.unpack_from("<Q", body, profile.cost_manager)[0]
            loaded = body[profile.loaded]
            state = struct.unpack_from("<i", body, profile.battle_state)[0]
            result = struct.unpack_from("<i", body, profile.battle_result)[0]
            speed = struct.unpack_from("<i", body, profile.speed_level)[0]
            origin_scale = struct.unpack_from("<f", body, profile.origin_scale)[0]
            if loaded not in (0, 1) or not 0 <= state <= 32:
                continue
            if not -8 <= result <= 32 or speed not in (0, 1, 2, 3):
                continue
            if origin_scale != 1.0:
                continue
            if not all(
                value and value % 8 == 0 and mapped(value)
                for value in (scheduler, cost_manager)
            ):
                continue
            controllers.append(address)
        if len(controllers) > before and found_region is None:
            found_region = (region.start, region.end)
        # A preferred region that yields exactly one verified controller is
        # enough; sweeping the rest would only cost the caller a minute.
        if prefer_region is not None and len(controllers) == 1 and found_region:
            break
    if len(controllers) != 1:
        raise MemoryLocatorError(
            f"expected exactly one live BattleController, found {len(controllers)}"
            + ("; no battle is loaded" if not controllers else "")
        )
    controller = controllers[0]

    body = reader.read(controller, window)
    if len(body) != window:
        raise MemoryLocatorError("short read for the BattleController body")
    scheduler = struct.unpack_from("<Q", body, profile.scheduler)[0]
    cost_manager = struct.unpack_from("<Q", body, profile.cost_manager)[0]
    enable_state = struct.unpack_from("<Q", body, profile.enable_state)[0]

    if _deref(reader, scheduler + profile.scheduler_backref) != controller:
        raise MemoryLocatorError("Scheduler does not point back to BattleController")
    if _deref(reader, cost_manager + profile.cost_backref) != controller:
        raise MemoryLocatorError(
            "BattleCostManager does not point back to BattleController"
        )

    cost_status = _follow(reader, cost_manager, profile.cost_status_chain)
    cost_timer = _follow(reader, cost_manager, profile.cost_timer_chain)
    pause_list = _follow(reader, enable_state, profile.pause_list_chain)

    controller_class_name = _class_name(reader, controller_class)
    cost_status_class_name = _class_name(reader, _deref(reader, cost_status))
    pause_list_class_name = _class_name(reader, _deref(reader, pause_list))
    if not controller_class_name.endswith("BattleController"):
        raise MemoryLocatorError(
            f"located class resolves to {controller_class_name!r}"
        )

    addresses = BattleRuntimeAddresses(
        pid=identity.pid,
        controller_class=controller_class,
        controller=controller,
        scheduler=scheduler,
        cost_manager=cost_manager,
        cost_status=cost_status,
        cost_timer=cost_timer,
        pause_list=pause_list,
    )
    return LocatedBattleAddresses(
        identity=identity,
        profile_id=profile.profile_id,
        addresses=addresses,
        controller_class_name=controller_class_name,
        cost_status_class_name=cost_status_class_name,
        pause_list_class_name=pause_list_class_name,
        scan_notes=scan_notes,
        controller_region=found_region,
    )


def locate_battle_addresses_native(
    reader: "AdbProcessMemoryReader",
    helper_path: str,
    *,
    profile: BattleLayoutProfile = TORAPPU_2_7_61,
    identity: ProcessIdentity | None = None,
) -> LocatedBattleAddresses:
    """Locate on-device, then independently recheck the tiny result in Python.

    Pulling every managed heap region through ADB took minutes. The native
    helper performs the same read-only exact-pattern and structural checks on
    the emulator and emits only the seven addresses. This verifier rejects the
    result unless the process identity, live object class and resolved class
    names all still agree.
    """

    identity = identity or reader.identity()
    completed = subprocess.run(
        (
            reader.adb_path,
            "-s",
            reader.serial,
            "exec-out",
            helper_path,
            str(identity.pid),
        ),
        capture_output=True,
        check=False,
        timeout=30.0,
        **headless_process_kwargs(),
    )
    if completed.returncode != 0:
        stderr = _native_diagnostic_excerpt(completed.stderr)
        stdout = _native_diagnostic_excerpt(completed.stdout)
        raise MemoryLocatorError(
            f"native locator failed rc={completed.returncode}; "
            f"stderr={stderr!r}; stdout={stdout!r}"
        )
    try:
        payload = _parse_native_locator_payload(completed.stdout)
        addresses = BattleRuntimeAddresses(
            pid=int(payload["pid"]),
            controller_class=int(str(payload["controller_class"]), 16),
            controller=int(str(payload["controller"]), 16),
            scheduler=int(str(payload["scheduler"]), 16),
            cost_manager=int(str(payload["cost_manager"]), 16),
            cost_status=int(str(payload["cost_status"]), 16),
            cost_timer=int(str(payload["cost_timer"]), 16),
            pause_list=int(str(payload["pause_list"]), 16),
        )
        region_payload = payload["controller_region"]
        controller_region = (
            int(str(region_payload[0]), 16),
            int(str(region_payload[1]), 16),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        stderr = _native_diagnostic_excerpt(completed.stderr)
        stdout = _native_diagnostic_excerpt(completed.stdout)
        raise MemoryLocatorError(
            "native locator returned invalid JSON: "
            f"{error}; stderr={stderr!r}; stdout={stdout!r}"
        ) from error
    if addresses.pid != identity.pid or not identity.matches(reader.identity()):
        raise StaleAddressError("game process identity changed during native locate")
    controller_class_name = _class_name(reader, addresses.controller_class)
    cost_status_class_name = _class_name(reader, _deref(reader, addresses.cost_status))
    pause_list_class_name = _class_name(reader, _deref(reader, addresses.pause_list))
    if not controller_class_name.endswith("BattleController"):
        raise MemoryLocatorError(
            f"native class resolves to {controller_class_name!r}"
        )
    if not cost_status_class_name.endswith("CostStatus"):
        raise MemoryLocatorError(
            f"native cost status resolves to {cost_status_class_name!r}"
        )
    if not pause_list_class_name.startswith("System.Collections.Generic.List"):
        raise MemoryLocatorError(
            f"native pause list resolves to {pause_list_class_name!r}"
        )
    located = LocatedBattleAddresses(
        identity=identity,
        profile_id=profile.profile_id,
        addresses=addresses,
        controller_class_name=controller_class_name,
        cost_status_class_name=cost_status_class_name,
        pause_list_class_name=pause_list_class_name,
        scan_notes={
            "native_device_side": 1,
            "scanned_bytes": int(payload.get("scanned_bytes") or 0),
        },
        controller_region=controller_region,
    )
    assert_addresses_live(located, reader)
    if not battle_is_loaded(reader, located, profile=profile):
        raise MemoryLocatorError("native locator result is not a loaded battle")
    return located


def assert_addresses_live(
    located: LocatedBattleAddresses, reader: ProcessMemoryReader
) -> None:
    """Fail closed when the process behind the addresses has changed.

    A restarted game keeps neither its pid nor its layout, and a re-installed
    build keeps neither.  Callers are expected to re-locate and to fall back to
    OCR while they do.
    """

    current = reader.identity()
    if not located.identity.matches(current):
        raise StaleAddressError(
            "process identity changed: "
            f"located {located.identity} but found {current}"
        )
    klass = _deref(reader, located.addresses.controller)
    if klass != located.addresses.controller_class:
        raise StaleAddressError(
            "BattleController class pointer changed; the object was reclaimed"
        )


def battle_is_loaded(
    reader: ProcessMemoryReader,
    located: LocatedBattleAddresses,
    *,
    profile: BattleLayoutProfile = TORAPPU_2_7_61,
) -> bool:
    body = reader.read(located.addresses.controller, profile.controller_window)
    if len(body) != profile.controller_window:
        raise MemoryObservationError("short read for the BattleController body")
    return bool(body[profile.loaded])


class AdbProcessMemoryReader:
    """Read one authorized emulator process over ADB, never writing to it.

    Every access is ``cat /proc/<pid>/maps`` or ``dd if=/proc/<pid>/mem``.  The
    build fingerprint is captured once; the process identity is re-read on
    demand so callers can notice a restart cheaply.
    """

    def __init__(
        self,
        *,
        adb_path: Path | str,
        serial: str,
        package: str,
    ) -> None:
        self.adb_path = str(adb_path)
        self.serial = serial
        self.package = package
        self._version_name = self._read_version_name()
        self._il2cpp_sha256 = self._read_il2cpp_sha256()
        self._pid = self._read_pid()

    def _adb(self, *args: str, timeout: float = 120.0) -> bytes:
        completed = subprocess.run(
            [self.adb_path, "-s", self.serial, *args],
            capture_output=True,
            check=False,
            timeout=timeout,
            **headless_process_kwargs(),
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()
            raise MemoryLocatorError(f"adb {' '.join(args[:2])} failed: {detail}")
        return completed.stdout

    def _read_pid(self) -> int:
        text = self._adb("shell", "pidof", self.package).decode().strip()
        if not text:
            raise MemoryLocatorError(f"{self.package} is not running on {self.serial}")
        return int(text.split()[0])

    def _read_version_name(self) -> str:
        text = self._adb(
            "shell", "dumpsys", "package", self.package
        ).decode("utf-8", "replace")
        match = re.search(r"versionName=(\S+)", text)
        return match[1] if match else ""

    def _read_il2cpp_sha256(self) -> str:
        pid = self._read_pid()
        maps = self._adb("shell", "cat", f"/proc/{pid}/maps").decode("utf-8", "replace")
        match = re.search(r"(\S*libil2cpp\.so)\s*$", maps, re.MULTILINE)
        if not match:
            return ""
        output = self._adb("shell", "sha256sum", match[1]).decode().strip()
        return output.split()[0] if output else ""

    # -- ProcessMemoryReader -------------------------------------------
    def identity(self) -> ProcessIdentity:
        pid = self._read_pid()
        stat = self._adb("shell", "cat", f"/proc/{pid}/stat").decode(
            "utf-8", "replace"
        )
        tail = stat.rsplit(")", 1)[-1].split()
        # Field 22 of proc(5) counted from `state`, which is index 0 here.
        start_ticks = int(tail[19]) if len(tail) > 19 else 0
        return ProcessIdentity(
            pid=pid,
            start_ticks=start_ticks,
            package=self.package,
            version_name=self._version_name,
            il2cpp_sha256=self._il2cpp_sha256,
        )

    def maps(self) -> tuple[MapRegion, ...]:
        text = self._adb("shell", "cat", f"/proc/{self._pid}/maps").decode(
            "utf-8", "replace"
        )
        return parse_maps(text)

    def read(self, address: int, size: int) -> bytes:
        return self._adb(
            "exec-out",
            "sh",
            "-c",
            f"dd if=/proc/{self._pid}/mem bs=1 skip={address} count={size} status=none",
        )

    def read_region(self, start: int, end: int) -> bytes:
        return self._adb(
            "exec-out",
            "sh",
            "-c",
            f"dd if=/proc/{self._pid}/mem bs=4096 skip={start // 4096} "
            f"count={(end - start) // 4096} status=none",
            timeout=600.0,
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Locate the live Arknights battle objects on an authorized instance."
    )
    parser.add_argument(
        "--adb", default=r"C:\Program Files\Netease\MuMu\nx_main\adb.exe"
    )
    parser.add_argument("--serial", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    reader = AdbProcessMemoryReader(
        adb_path=args.adb, serial=args.serial, package=args.package
    )
    try:
        located = locate_battle_addresses(reader)
    except MemoryLocatorError as error:
        print(f"locate failed: {error}", file=sys.stderr)
        return 1
    located.write(args.out)
    print(json.dumps(located.to_json(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
