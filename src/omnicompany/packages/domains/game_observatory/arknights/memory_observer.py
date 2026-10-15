# [OMNI] origin=codex created_by=codex intent="provide validated read-only Arknights battle memory observation"
"""Low-bandwidth, read-only IL2CPP battle observation for Arknights.

The hot path is deliberately independent from screenshots and OCR.  A native
helper performs one ``process_vm_readv`` call per sample and streams fixed-size
frames through an existing ADB connection.  Discovery remains a cold-path
operation: exact UTF-8 metadata strings identify ``Il2CppClass`` and the class
pointer identifies the live managed object.

Nothing in this module opens or exposes a writable target-process handle.
"""

from __future__ import annotations

import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator, Sequence

from ..subprocess_policy import headless_process_kwargs

__all__ = [
    "BattleMemorySnapshot",
    "BattleRuntimeAddresses",
    "ContinuousAdbMemoryProbe",
    "MemoryBlock",
    "MemoryChunk",
    "MemoryObservationError",
    "decode_battle_integer",
    "decode_battle_snapshot",
    "decode_fixed_32_32",
    "decode_il2cpp_string",
    "find_il2cpp_class",
    "find_objects_by_class",
    "find_unique_bytes",
]


class MemoryObservationError(RuntimeError):
    """The read-only channel ended or returned structurally invalid state."""


@dataclass(frozen=True, slots=True)
class MemoryChunk:
    address: int
    data: bytes


@dataclass(frozen=True, slots=True)
class MemoryBlock:
    name: str
    address: int
    size: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("memory block name cannot be empty")
        if self.address <= 0 or self.size <= 0:
            raise ValueError("memory block address and size must be positive")

    @property
    def helper_argument(self) -> str:
        return f"{self.address:#x}:{self.size:#x}"


@dataclass(frozen=True, slots=True)
class BattleRuntimeAddresses:
    pid: int
    controller_class: int
    controller: int
    scheduler: int
    cost_manager: int
    cost_status: int
    cost_timer: int
    pause_list: int

    def blocks(self) -> tuple[MemoryBlock, ...]:
        return (
            MemoryBlock("controller", self.controller, 0x300),
            MemoryBlock("scheduler", self.scheduler, 0x140),
            MemoryBlock("cost_manager", self.cost_manager, 0x50),
            MemoryBlock("cost_status", self.cost_status, 0x4C),
            MemoryBlock("cost_timer", self.cost_timer, 0x20),
            MemoryBlock("pause_list", self.pause_list, 0x20),
        )


@dataclass(frozen=True, slots=True)
class BattleMemorySnapshot:
    captured_monotonic: float
    controller: int
    controller_class: int
    level_id_pointer: int
    loaded: bool
    battle_state: int
    battle_result: int
    speed_level: int
    real_play_time: float
    paused: bool
    life_points: int
    spawned_enemies: int
    spawned_waves: int
    killed_enemies: int
    valid_killed_enemies: int
    valid_missed_enemies: int
    total_enemies: int
    current_cost: int
    minimum_cost: int
    maximum_cost: int
    cost_progress: float
    cost_period_seconds: float
    cost_remaining_seconds: float
    cost_locked: bool
    negative_recovery_multiplier: float


def _signed_u32(value: int) -> int:
    return value - 0x1_0000_0000 if value & 0x8000_0000 else value


def decode_battle_integer(data: bytes, offset: int = 0) -> int:
    """Decode Torappu's three-word guarded integer without mutating it."""

    try:
        key, encoded, valid = struct.unpack_from("<IIi", data, offset)
    except struct.error as exc:
        raise MemoryObservationError("truncated BattleInteger") from exc
    if valid != 1:
        raise MemoryObservationError(f"BattleInteger validity word is {valid}, not 1")
    return _signed_u32(key ^ encoded)


def decode_fixed_32_32(data: bytes, offset: int = 0) -> float:
    """Decode the signed 32.32 fixed-point clock used by battle timers."""

    try:
        raw = struct.unpack_from("<q", data, offset)[0]
    except struct.error as exc:
        raise MemoryObservationError("truncated 32.32 fixed-point value") from exc
    return raw / 4_294_967_296.0


def decode_il2cpp_string(data: bytes) -> str:
    """Decode one complete ``Il2CppString`` object image."""

    if len(data) < 0x14:
        raise MemoryObservationError("truncated Il2CppString header")
    length = struct.unpack_from("<i", data, 0x10)[0]
    if length < 0 or length > 1_048_576:
        raise MemoryObservationError(f"invalid Il2CppString length: {length}")
    end = 0x14 + length * 2
    if end > len(data):
        raise MemoryObservationError("truncated Il2CppString payload")
    return data[0x14:end].decode("utf-16-le")


def _iter_matches(chunk: MemoryChunk, needle: bytes) -> Iterator[int]:
    cursor = 0
    while True:
        cursor = chunk.data.find(needle, cursor)
        if cursor < 0:
            return
        yield chunk.address + cursor
        cursor += 1


def find_unique_bytes(chunks: Iterable[MemoryChunk], needle: bytes) -> int:
    """Return the sole exact byte occurrence across already-read regions."""

    matches = [address for chunk in chunks for address in _iter_matches(chunk, needle)]
    if len(matches) != 1:
        raise MemoryObservationError(
            f"expected one exact metadata match, found {len(matches)}"
        )
    return matches[0]


def find_il2cpp_class(
    chunks: Iterable[MemoryChunk], *, name_pointer: int, namespace_pointer: int
) -> int:
    """Find a v27.2 ``Il2CppClass`` from its exact name/namespace pointer pair."""

    pair = struct.pack("<QQ", name_pointer, namespace_pointer)
    matches = [
        address - 0x10
        for chunk in chunks
        for address in _iter_matches(chunk, pair)
        if (address - 0x10) % 8 == 0
    ]
    if len(matches) != 1:
        raise MemoryObservationError(
            f"expected one Il2CppClass, found {len(matches)}"
        )
    return matches[0]


def find_objects_by_class(
    chunks: Iterable[MemoryChunk], class_pointer: int
) -> tuple[int, ...]:
    """Return aligned managed-object candidates whose first word is ``klass``."""

    needle = struct.pack("<Q", class_pointer)
    return tuple(
        address
        for chunk in chunks
        for address in _iter_matches(chunk, needle)
        if address % 8 == 0
    )


def _split_frame(
    payload: bytes, blocks: Sequence[MemoryBlock]
) -> dict[str, memoryview]:
    expected = sum(block.size for block in blocks)
    if len(payload) != expected:
        raise MemoryObservationError(
            f"memory frame is {len(payload)} bytes; expected {expected}"
        )
    result: dict[str, memoryview] = {}
    cursor = 0
    for block in blocks:
        if block.name in result:
            raise MemoryObservationError(f"duplicate memory block: {block.name}")
        result[block.name] = memoryview(payload)[cursor : cursor + block.size]
        cursor += block.size
    return result


def decode_battle_snapshot(
    payload: bytes,
    addresses: BattleRuntimeAddresses,
    *,
    captured_monotonic: float | None = None,
) -> BattleMemorySnapshot:
    """Decode and cross-check one 1.3 KiB battle-memory frame."""

    parts = _split_frame(payload, addresses.blocks())
    controller = parts["controller"]
    scheduler = parts["scheduler"]
    cost_manager = parts["cost_manager"]
    cost_status = parts["cost_status"]
    cost_timer = parts["cost_timer"]
    pause_list = parts["pause_list"]

    controller_class = struct.unpack_from("<Q", controller, 0)[0]
    scheduler_backref = struct.unpack_from("<Q", scheduler, 0x130)[0]
    cost_backref = struct.unpack_from("<Q", cost_manager, 0x10)[0]
    if controller_class != addresses.controller_class:
        raise MemoryObservationError("BattleController class pointer changed")
    if scheduler_backref != addresses.controller:
        raise MemoryObservationError("Scheduler no longer points to BattleController")
    if cost_backref != addresses.controller:
        raise MemoryObservationError("BattleCostManager no longer points to BattleController")

    period = decode_fixed_32_32(cost_timer, 0x10)
    remaining = decode_fixed_32_32(cost_timer, 0x18)
    if not 0.000_001 <= period <= 3_600:
        raise MemoryObservationError(f"invalid cost timer period: {period}")
    cost_progress = min(1.0, max(0.0, 1.0 - remaining / period))
    speed_level = struct.unpack_from("<i", controller, 0x228)[0]
    battle_state = struct.unpack_from("<i", controller, 0x220)[0]
    battle_result = struct.unpack_from("<i", controller, 0x224)[0]
    total_enemies = struct.unpack_from("<i", scheduler, 0x128)[0]
    if speed_level not in {0, 1, 2, 3}:
        raise MemoryObservationError(f"invalid speed level: {speed_level}")
    if not 0 <= battle_state <= 32 or not -8 <= battle_result <= 32:
        raise MemoryObservationError("battle state enum is outside the guarded range")
    if not 0 <= total_enemies <= 10_000:
        raise MemoryObservationError(f"invalid total enemy count: {total_enemies}")

    return BattleMemorySnapshot(
        captured_monotonic=(
            time.monotonic() if captured_monotonic is None else captured_monotonic
        ),
        controller=addresses.controller,
        controller_class=controller_class,
        level_id_pointer=struct.unpack_from("<Q", controller, 0x2C8)[0],
        loaded=bool(controller[0x200]),
        battle_state=battle_state,
        battle_result=battle_result,
        speed_level=speed_level,
        real_play_time=struct.unpack_from("<f", controller, 0x284)[0],
        paused=struct.unpack_from("<i", pause_list, 0x18)[0] > 0,
        life_points=decode_battle_integer(controller, 0x238),
        spawned_enemies=struct.unpack_from("<i", scheduler, 0x24)[0],
        spawned_waves=struct.unpack_from("<i", scheduler, 0x28)[0],
        killed_enemies=decode_battle_integer(scheduler, 0x40),
        valid_killed_enemies=decode_battle_integer(scheduler, 0x68),
        valid_missed_enemies=decode_battle_integer(scheduler, 0x7C),
        total_enemies=total_enemies,
        current_cost=decode_battle_integer(cost_status, 0x10),
        minimum_cost=decode_battle_integer(cost_status, 0x24),
        maximum_cost=decode_battle_integer(cost_status, 0x38),
        cost_progress=cost_progress,
        cost_period_seconds=period,
        cost_remaining_seconds=remaining,
        cost_locked=bool(cost_manager[0x38]),
        negative_recovery_multiplier=struct.unpack_from("<f", cost_manager, 0x40)[0],
    )


class ContinuousAdbMemoryProbe:
    """Consume fixed-rate frames from the read-only native Android helper."""

    def __init__(
        self,
        *,
        adb_path: Path | str,
        serial: str,
        helper_path: str,
        addresses: BattleRuntimeAddresses,
        sample_hz: float = 10.0,
    ) -> None:
        if not 0.5 <= sample_hz <= 60.0:
            raise ValueError("sample_hz must be between 0.5 and 60")
        self.adb_path = str(adb_path)
        self.serial = serial
        self.helper_path = helper_path
        self.addresses = addresses
        self.sample_hz = sample_hz
        self.blocks = addresses.blocks()
        self.frame_size = sum(block.size for block in self.blocks)
        self._process: subprocess.Popen[bytes] | None = None
        self._remote_pid: int | None = None
        self._reader_thread: threading.Thread | None = None
        self._latest_snapshot: BattleMemorySnapshot | None = None
        self._reader_error: MemoryObservationError | None = None
        self._latest_condition = threading.Condition()
        self._closing = threading.Event()

    @property
    def command(self) -> tuple[str, ...]:
        interval_us = max(1, round(1_000_000 / self.sample_hz))
        return (
            self.adb_path,
            "-s",
            self.serial,
            "exec-out",
            self.helper_path,
            f"--interval-us={interval_us}",
            "--emit-pid",
            str(self.addresses.pid),
            *(block.helper_argument for block in self.blocks),
        )

    def start(self) -> "ContinuousAdbMemoryProbe":
        if self._process is not None:
            return self
        self._process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            **headless_process_kwargs(),
        )
        assert self._process.stdout is not None
        header = self._process.stdout.readline().decode("ascii", "replace").strip()
        prefix = "OMNI_PROC_MEM_PROBE_PID "
        try:
            remote_pid = int(header.removeprefix(prefix))
        except ValueError as exc:
            self.close()
            raise MemoryObservationError(
                f"native memory probe returned an invalid header: {header!r}"
            ) from exc
        if not header.startswith(prefix) or remote_pid <= 0:
            self.close()
            raise MemoryObservationError(
                f"native memory probe returned an invalid header: {header!r}"
            )
        self._remote_pid = remote_pid
        self._closing.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"arknights-memory-latest-{self.addresses.pid}",
            daemon=True,
        )
        self._reader_thread.start()
        return self

    @staticmethod
    def _read_exact(stream: BinaryIO, length: int) -> bytes:
        payload = bytearray()
        while len(payload) < length:
            chunk = stream.read(length - len(payload))
            if not chunk:
                break
            payload += chunk
        return bytes(payload)

    def _reader_loop(self) -> None:
        """Drain the fixed-rate pipe continuously and retain only its newest frame."""

        process = self._process
        if process is None or process.stdout is None:
            return
        while not self._closing.is_set():
            payload = self._read_exact(process.stdout, self.frame_size)
            if len(payload) != self.frame_size:
                error = MemoryObservationError(
                    "native memory probe ended before a complete frame"
                )
                with self._latest_condition:
                    self._reader_error = error
                    self._latest_condition.notify_all()
                return
            try:
                snapshot = decode_battle_snapshot(
                    payload,
                    self.addresses,
                    captured_monotonic=time.monotonic(),
                )
            except MemoryObservationError as error:
                with self._latest_condition:
                    self._reader_error = error
                    self._latest_condition.notify_all()
                return
            with self._latest_condition:
                self._latest_snapshot = snapshot
                self._reader_error = None
                self._latest_condition.notify_all()

    def sample(
        self, *, timeout: float = 2.0, max_age_seconds: float = 0.5
    ) -> BattleMemorySnapshot:
        if self._process is None:
            self.start()
        deadline = time.monotonic() + timeout
        with self._latest_condition:
            while self._latest_snapshot is None and self._reader_error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MemoryObservationError(
                        f"native memory probe produced no frame within {timeout}s"
                    )
                self._latest_condition.wait(remaining)
            if self._reader_error is not None:
                raise self._reader_error
            assert self._latest_snapshot is not None
            snapshot = self._latest_snapshot
        age = time.monotonic() - snapshot.captured_monotonic
        if age > max_age_seconds:
            raise MemoryObservationError(
                f"newest resident memory frame is stale ({age:.3f}s)"
            )
        return snapshot

    def close(self) -> None:
        self._closing.set()
        process, self._process = self._process, None
        remote_pid, self._remote_pid = self._remote_pid, None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        if remote_pid is not None:
            try:
                resolved = subprocess.run(
                    (
                        self.adb_path,
                        "-s",
                        self.serial,
                        "shell",
                        "readlink",
                        f"/proc/{remote_pid}/exe",
                    ),
                    capture_output=True,
                    check=False,
                    timeout=2,
                    **headless_process_kwargs(),
                ).stdout.decode("utf-8", "replace").strip()
                if resolved == self.helper_path:
                    subprocess.run(
                        (
                            self.adb_path,
                            "-s",
                            self.serial,
                            "shell",
                            "kill",
                            str(remote_pid),
                        ),
                        capture_output=True,
                        check=False,
                        timeout=2,
                        **headless_process_kwargs(),
                    )
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        reader, self._reader_thread = self._reader_thread, None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=2)

    def __enter__(self) -> "ContinuousAdbMemoryProbe":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()
