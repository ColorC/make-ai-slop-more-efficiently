# [OMNI] origin=codex created_by=codex intent="provide checked MuMu renderer IPC bindings"
"""Low-level ctypes bindings for MuMu renderer capture and touch IPC."""

from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_INSTALL_PATH = Path("C:/Program Files/Netease/MuMu")
DEFAULT_APP_PACKAGE = "com.mergegames.gossipharbor"
DEFAULT_INSTANCE_INDEX = 2
_DLL_RELATIVE_PATHS = (
    Path("nx_main/sdk/external_renderer_ipc.dll"),
    Path("shell/sdk/external_renderer_ipc.dll"),
)


class MuMuIpcError(RuntimeError):
    """A checked MuMu renderer IPC operation failed."""


@dataclass(frozen=True)
class MuMuCaptureTiming:
    dll_ms: float
    flip_ms: float
    total_ms: float


def resolve_mumu_dll(
    install_path: Path = DEFAULT_INSTALL_PATH,
    dll_path: Path | None = None,
) -> Path:
    """Resolve the renderer IPC DLL using MaaFramework's search order."""

    if dll_path is not None:
        selected = Path(dll_path)
        if not selected.is_file():
            raise MuMuIpcError(f"resolve_mumu_dll failed: file not found: {selected}")
        return selected
    root = Path(install_path)
    for relative in _DLL_RELATIVE_PATHS:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(root / item) for item in _DLL_RELATIVE_PATHS)
    raise MuMuIpcError(f"resolve_mumu_dll failed: no DLL in {searched}")


class MuMuIpc:
    """Checked connection, capture, and finger-touch operations for one instance."""

    def __init__(
        self,
        install_path: Path = DEFAULT_INSTALL_PATH,
        *,
        dll_path: Path | None = None,
        instance_index: int = DEFAULT_INSTANCE_INDEX,
        app_package: str = DEFAULT_APP_PACKAGE,
        app_cloned_index: int = 0,
    ) -> None:
        self.install_path = Path(install_path)
        self.dll_path = resolve_mumu_dll(self.install_path, dll_path)
        self.instance_index = instance_index
        self.app_package = app_package
        self.app_cloned_index = app_cloned_index
        self.handle = 0
        self.display_id: int | None = None
        self.resolution: tuple[int, int] | None = None
        self.last_capture_timing = MuMuCaptureTiming(0.0, 0.0, 0.0)
        self._dll_directory: Any | None = None
        if hasattr(os, "add_dll_directory"):
            self._dll_directory = os.add_dll_directory(str(self.dll_path.parent))
        try:
            self._dll = ctypes.CDLL(str(self.dll_path))
        except OSError as exc:
            if self._dll_directory is not None:
                self._dll_directory.close()
                self._dll_directory = None
            raise MuMuIpcError(
                f"ctypes.CDLL failed for renderer IPC: {type(exc).__name__}: {exc}"
            ) from exc
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        byte_pointer = ctypes.POINTER(ctypes.c_ubyte)
        int_pointer = ctypes.POINTER(ctypes.c_int)
        signatures = {
            "nemu_connect": ([ctypes.c_wchar_p, ctypes.c_int], ctypes.c_int),
            "nemu_disconnect": ([ctypes.c_int], None),
            "nemu_get_display_id": (
                [ctypes.c_int, ctypes.c_char_p, ctypes.c_int],
                ctypes.c_int,
            ),
            "nemu_capture_display": (
                [
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    int_pointer,
                    int_pointer,
                    byte_pointer,
                ],
                ctypes.c_int,
            ),
            "nemu_input_event_finger_touch_down": (
                [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int],
                ctypes.c_int,
            ),
            "nemu_input_event_finger_touch_up": (
                [ctypes.c_int, ctypes.c_int, ctypes.c_int],
                ctypes.c_int,
            ),
        }
        for name, (argtypes, restype) in signatures.items():
            try:
                function = getattr(self._dll, name)
            except AttributeError as exc:
                raise MuMuIpcError(f"{name} failed: function is missing") from exc
            function.argtypes = argtypes
            function.restype = restype

    def _require_connected(self) -> None:
        if self.handle == 0 or self.display_id is None:
            raise MuMuIpcError("MuMu IPC is not connected")

    @staticmethod
    def _check_zero(function: str, ret: int) -> None:
        if ret != 0:
            raise MuMuIpcError(f"{function} failed: ret={ret}")

    def connect(self) -> int:
        if self.handle:
            return self.handle
        handle = int(
            self._dll.nemu_connect(str(self.install_path), self.instance_index)
        )
        if handle == 0:
            raise MuMuIpcError("nemu_connect failed: ret=0")
        self.handle = handle
        display_id = int(
            self._dll.nemu_get_display_id(
                handle,
                self.app_package.encode("utf-8") if self.app_package else b"default",
                self.app_cloned_index if self.app_package else 0,
            )
        )
        if display_id < 0:
            self._dll.nemu_disconnect(handle)
            self.handle = 0
            raise MuMuIpcError(f"nemu_get_display_id failed: ret={display_id}")
        self.display_id = display_id
        self.resolution = None
        return handle

    def refresh_display_id(self) -> int:
        """Discard and re-read the app display id after an in-game scene reload."""

        if self.handle == 0:
            raise MuMuIpcError("MuMu IPC is not connected")
        self.display_id = None
        display_id = int(
            self._dll.nemu_get_display_id(
                self.handle,
                self.app_package.encode("utf-8") if self.app_package else b"default",
                self.app_cloned_index if self.app_package else 0,
            )
        )
        if display_id < 0:
            raise MuMuIpcError(f"nemu_get_display_id failed: ret={display_id}")
        self.display_id = display_id
        self.resolution = None
        return display_id

    def reconnect(self) -> int:
        """Rebuild the renderer handle and refresh all handle-scoped caches."""

        self.disconnect()
        return self.connect()

    def query_resolution(self) -> tuple[int, int]:
        self._require_connected()
        width = ctypes.c_int()
        height = ctypes.c_int()
        ret = int(
            self._dll.nemu_capture_display(
                self.handle,
                self.display_id,
                0,
                ctypes.byref(width),
                ctypes.byref(height),
                None,
            )
        )
        self._check_zero("nemu_capture_display(query)", ret)
        if width.value < 1 or height.value < 1:
            raise MuMuIpcError(
                "nemu_capture_display(query) failed: "
                f"invalid resolution {width.value}x{height.value}"
            )
        self.resolution = (width.value, height.value)
        return self.resolution

    def capture(self) -> tuple[bytes, int, int]:
        """Capture vertically corrected RGBA bytes in top-left screen coordinates."""

        self._require_connected()
        total_started = time.perf_counter_ns()
        width, height = self.resolution or self.query_resolution()
        buffer_size = width * height * 4
        buffer = (ctypes.c_ubyte * buffer_size)()
        returned_width = ctypes.c_int(width)
        returned_height = ctypes.c_int(height)
        dll_started = time.perf_counter_ns()
        ret = int(
            self._dll.nemu_capture_display(
                self.handle,
                self.display_id,
                buffer_size,
                ctypes.byref(returned_width),
                ctypes.byref(returned_height),
                buffer,
            )
        )
        dll_ms = (time.perf_counter_ns() - dll_started) / 1_000_000
        self._check_zero("nemu_capture_display", ret)
        observed = (returned_width.value, returned_height.value)
        if observed != (width, height):
            self.disconnect()
            self.connect()
            self.query_resolution()
            return self.capture()

        flip_started = time.perf_counter_ns()
        row_bytes = width * 4
        raw = memoryview(buffer).cast("B")
        rgba = b"".join(
            raw[offset : offset + row_bytes]
            for offset in range((height - 1) * row_bytes, -1, -row_bytes)
        )
        flip_ms = (time.perf_counter_ns() - flip_started) / 1_000_000
        total_ms = (time.perf_counter_ns() - total_started) / 1_000_000
        self.last_capture_timing = MuMuCaptureTiming(dll_ms, flip_ms, total_ms)
        return rgba, width, height

    def touch_down(self, x: int, y: int, *, finger_id: int = 1) -> None:
        self._require_connected()
        ret = int(
            self._dll.nemu_input_event_finger_touch_down(
                self.handle, self.display_id, finger_id, x, y
            )
        )
        self._check_zero("nemu_input_event_finger_touch_down", ret)

    def touch_move(self, x: int, y: int, *, finger_id: int = 1) -> None:
        self.touch_down(x, y, finger_id=finger_id)

    def touch_up(self, *, finger_id: int = 1) -> None:
        self._require_connected()
        ret = int(
            self._dll.nemu_input_event_finger_touch_up(
                self.handle, self.display_id, finger_id
            )
        )
        self._check_zero("nemu_input_event_finger_touch_up", ret)

    def disconnect(self) -> None:
        if self.handle:
            self._dll.nemu_disconnect(self.handle)
        self.handle = 0
        self.display_id = None
        self.resolution = None
        if self._dll_directory is not None:
            self._dll_directory.close()
            self._dll_directory = None


__all__ = [
    "DEFAULT_APP_PACKAGE",
    "DEFAULT_INSTALL_PATH",
    "DEFAULT_INSTANCE_INDEX",
    "MuMuCaptureTiming",
    "MuMuIpc",
    "MuMuIpcError",
    "resolve_mumu_dll",
]
