"""Concrete ADB/MAA host callbacks for the lightweight Arknights tournament."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from ..adapters import AdbVideoCaptureHandle
from .operations import ArknightsOperations

__all__ = ["AdbMaaLightweightHooks", "LightweightHooksError"]


class LightweightHooksError(RuntimeError):
    pass


class _AdbRecordingAndPackageAdapter(Protocol):
    serial: str | None

    def begin_video_capture(
        self,
        *,
        max_seconds: int = 180,
        display_id: str | int | None = None,
    ) -> AdbVideoCaptureHandle: ...

    def finish_video_capture_to_path(
        self, handle: AdbVideoCaptureHandle, output_path: str | Path
    ) -> Path: ...

    def package_installed(self, package: str) -> bool: ...

    def force_stop_package(self, package: str) -> dict[str, Any]: ...

    def start_package(self, package: str) -> dict[str, Any]: ...

    def connect(self, target: str) -> Any: ...


class AdbMaaLightweightHooks:
    """Visible host actions with segmented video and a simple recovery path.

    Android ``screenrecord`` is capped at three minutes on the MuMu images used by
    Game Observatory.  The recorder rotates MP4 segments automatically.  The first
    segment uses the requested filename; later segments add ``.part-002`` and so on.
    """

    def __init__(
        self,
        *,
        adb: _AdbRecordingAndPackageAdapter,
        operations: ArknightsOperations,
        package: str,
        segment_seconds: int = 180,
        recording_display_id: str | int | None = None,
        restart_delay_seconds: float = 2.0,
        emulator_restart: Callable[[], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        package = package.strip()
        if not package:
            raise ValueError("the Android package name is required")
        if not 10 <= segment_seconds <= 180:
            raise ValueError("recording segments must be between 10 and 180 seconds")
        normalized_display_id: str | None = None
        if recording_display_id is not None:
            normalized_display_id = str(recording_display_id).strip()
            if not normalized_display_id.isdecimal():
                raise ValueError("recording_display_id must be a non-negative integer")
        self._adb = adb
        self._operations = operations
        self._package = package
        self._segment_seconds = segment_seconds
        self._recording_display_id = normalized_display_id
        self._restart_delay_seconds = max(0.0, restart_delay_seconds)
        self._emulator_restart = emulator_restart
        self._sleep = sleep
        self._recording_stop = threading.Event()
        self._recording_thread: threading.Thread | None = None
        self._recording_error: str | None = None
        self._recording_files: list[Path] = []
        self._lock = threading.RLock()

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._recording_error

    @property
    def recording_files(self) -> tuple[Path, ...]:
        with self._lock:
            return tuple(self._recording_files)

    def start_recording(self, output_path: Path) -> bool:
        output_path = Path(output_path).resolve()
        with self._lock:
            if self._recording_thread is not None and self._recording_thread.is_alive():
                return False
            self._recording_stop = threading.Event()
            self._recording_error = None
            self._recording_files = []
            try:
                first_handle = self._begin_video_capture()
            except Exception as exc:
                self._recording_error = str(exc)
                return False
            thread = threading.Thread(
                target=self._record_segments,
                args=(output_path, first_handle),
                name="arknights-lightweight-recorder",
                daemon=True,
            )
            self._recording_thread = thread
            thread.start()
            return True

    def stop_recording(self) -> bool:
        with self._lock:
            thread = self._recording_thread
            if thread is None:
                return True
            self._recording_stop.set()
        thread.join(timeout=90.0)
        with self._lock:
            ok = not thread.is_alive() and self._recording_error is None
            if not thread.is_alive():
                self._recording_thread = None
            return ok

    def pause_game(self) -> bool:
        return self._run_visible_action(self._operations.pause_battle)

    def resume_game(self) -> bool:
        return self._run_visible_action(self._operations.resume_battle)

    def restart_game(self) -> bool:
        if self._restart_package():
            return True
        if self._emulator_restart is None:
            return False
        try:
            if not self._emulator_restart():
                return False
            self._sleep(self._restart_delay_seconds)
            if not self._adb.serial:
                return False
            self._adb.connect(self._adb.serial)
            return self._restart_package()
        except Exception as exc:
            with self._lock:
                self._recording_error = str(exc)
            return False

    def _restart_package(self) -> bool:
        try:
            if not self._adb.package_installed(self._package):
                return False
            self._adb.force_stop_package(self._package)
            self._sleep(self._restart_delay_seconds)
            result = self._adb.start_package(self._package)
            return bool(result.get("ok"))
        except Exception as exc:
            with self._lock:
                self._recording_error = str(exc)
            return False

    def _record_segments(
        self,
        output_path: Path,
        handle: AdbVideoCaptureHandle,
    ) -> None:
        part = 1
        try:
            while True:
                while not self._recording_stop.is_set() and handle.process.poll() is None:
                    self._recording_stop.wait(0.25)
                segment_path = self._segment_path(output_path, part)
                completed = self._adb.finish_video_capture_to_path(handle, segment_path)
                with self._lock:
                    self._recording_files.append(completed)
                if self._recording_stop.is_set():
                    return
                part += 1
                handle = self._begin_video_capture()
        except Exception as exc:
            with self._lock:
                self._recording_error = str(exc)

    def _begin_video_capture(self) -> AdbVideoCaptureHandle:
        if self._recording_display_id is None:
            return self._adb.begin_video_capture(max_seconds=self._segment_seconds)
        return self._adb.begin_video_capture(
            max_seconds=self._segment_seconds,
            display_id=self._recording_display_id,
        )

    @staticmethod
    def _segment_path(base: Path, part: int) -> Path:
        if part == 1:
            return base
        return base.with_name(f"{base.stem}.part-{part:03d}{base.suffix}")

    def _run_visible_action(self, action: Callable[[], Any]) -> bool:
        try:
            action()
            return True
        except Exception as exc:
            with self._lock:
                self._recording_error = str(exc)
            return False
