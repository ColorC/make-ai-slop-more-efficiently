from __future__ import annotations

import abc
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any

from .subprocess_policy import headless_process_kwargs
from .models import (
    BenchmarkTask,
    NormalizedAction,
    ObjectiveCheck,
    ObservationBundle,
    RunResult,
    TargetInfo,
    utc_now,
)
from .scrcpy_control import ScrcpyControlError, ScrcpyControlTransport
from .store import ObservatoryStore


class AdapterError(RuntimeError):
    pass


class AdapterActionError(AdapterError):
    def __init__(self, message: str, result: dict[str, Any]) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class AdbVideoCaptureHandle:
    remote_path: str
    remote_pid: int
    process: Any
    started_at: str


class GameAdapter(abc.ABC):
    @abc.abstractmethod
    def connect(self, target: str) -> TargetInfo:
        raise NotImplementedError

    @abc.abstractmethod
    def observe(self) -> ObservationBundle:
        raise NotImplementedError

    @abc.abstractmethod
    def reset(self, snapshot: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def checkpoint(self) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def restore(self, snapshot: str) -> dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def act(self, action: NormalizedAction) -> dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def evaluate(self, task: BenchmarkTask) -> RunResult:
        raise NotImplementedError


def resolve_adb(explicit: str | Path | None = None) -> Path:
    candidates = [
        Path(explicit) if explicit else None,
        Path(os.environ["OMNI_ADB_PATH"]) if os.environ.get("OMNI_ADB_PATH") else None,
        Path("E:/WindowsWorkspace/lofa/tools/device/platform-tools/adb.exe"),
        Path("E:/WindowsWorkspace/lofa/tools/android-sdk/platform-tools/adb.exe"),
        Path("C:/Program Files/Netease/MuMu/nx_main/adb.exe"),
        Path("C:/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate.resolve()
    raise AdapterError("adb executable not found; set OMNI_ADB_PATH")


def _run(args: list[str], *, binary: bool = False, timeout: float = 20.0) -> str | bytes:
    proc = subprocess.run(
        args,
        capture_output=True,
        timeout=timeout,
        check=False,
        **headless_process_kwargs(),
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace") if binary else proc.stderr.decode("utf-8", "replace")
        raise AdapterError(detail.strip() or f"command failed: {args[0]}")
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


class AdbAdapter(GameAdapter):
    def __init__(
        self,
        store: ObservatoryStore,
        adb_path: str | Path | None = None,
        *,
        on_transport_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self.store = store
        self.adb = resolve_adb(adb_path)
        self.serial: str | None = None
        self.on_transport_error = on_transport_error
        self._suppress_transport_error_depth = 0

    def _adb(self, *args: str, binary: bool = False, timeout: float = 20.0) -> str | bytes:
        cmd = [str(self.adb)]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += list(args)
        try:
            return _run(cmd, binary=binary, timeout=timeout)
        except (AdapterError, OSError, subprocess.SubprocessError) as exc:
            if self._suppress_transport_error_depth == 0 and self.on_transport_error is not None:
                self.on_transport_error(exc)
            raise

    def _adb_optional(
        self,
        *args: str,
        binary: bool = False,
        timeout: float = 20.0,
    ) -> str | bytes:
        """Run an explicitly tolerated helper command without invalidating transport."""

        self._suppress_transport_error_depth += 1
        try:
            return self._adb(*args, binary=binary, timeout=timeout)
        finally:
            self._suppress_transport_error_depth -= 1

    def _record_action(
        self,
        action: NormalizedAction,
        output: str = "",
        *,
        task_id: str | None = None,
        status: str = "passed",
        error: str | None = None,
    ) -> dict[str, Any]:
        run = RunResult(
            id=f"run.action.{int(time.time() * 1000)}.{uuid.uuid4().hex[:8]}",
            adapter="adb",
            target_id=f"device://adb/{self.serial}",
            task_id=task_id,
            status=status,
            ended_at=utc_now(),
            error=error,
        )
        self.store.save_run(run)
        self.store.append_event(
            run.id,
            "action",
            {
                "action": action.model_dump(mode="json"),
                "output": output,
                "serial": self.serial,
                "task_id": task_id,
                "status": status,
                "error": error,
            },
        )
        return {
            "ok": status == "passed",
            "run_id": run.id,
            "action": action.model_dump(mode="json"),
            "output": output,
            "error": error,
        }

    @classmethod
    def discover(cls, adb_path: str | Path | None = None) -> list[TargetInfo]:
        adb = resolve_adb(adb_path)
        output = str(_run([str(adb), "devices", "-l"]))
        targets: list[TargetInfo] = []
        for line in output.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            serial = parts[0]
            state = parts[1] if len(parts) > 1 else "unknown"
            props = dict(token.split(":", 1) for token in parts[2:] if ":" in token)
            is_mumu = serial.startswith("127.0.0.1:") or props.get("model") == "V2344A"
            targets.append(
                TargetInfo(
                    id=f"device://adb/{serial}",
                    kind="mumu" if is_mumu else "adb",
                    label=f"{'MuMu' if is_mumu else 'Android'} · {props.get('model', serial)}",
                    status="online" if state == "device" else "offline",
                    capabilities=["pixel", "touch", "remote", "install", "ui_tree"],
                    metadata={"serial": serial, "state": state, **props},
                )
            )
        return targets

    def connect(self, target: str) -> TargetInfo:
        serial = target.removeprefix("device://adb/").removeprefix("device://adb-tcp/")
        if ":" in serial and not serial.startswith("emulator-"):
            _run([str(self.adb), "connect", serial])
        self.serial = serial
        for info in self.discover(self.adb):
            if info.metadata.get("serial") == serial:
                if info.status != "online":
                    raise AdapterError(f"target is not online: {serial}")
                return info
        raise AdapterError(f"target not found after connect: {serial}")

    def _capture_png(self) -> bytes:
        if not self.serial:
            raise AdapterError("connect a target before observe")
        raw = self._adb("exec-out", "screencap", "-p", binary=True, timeout=30)
        assert isinstance(raw, bytes)
        if not raw.startswith(b"\x89PNG"):
            raise AdapterError("adb screencap did not return PNG")
        return raw

    def _capture_observation(self, *, include_ui: bool) -> ObservationBundle:
        raw = self._capture_png()
        stamp = int(time.time() * 1000)
        captured_at = utc_now()
        artifact_id = f"art.device.{stamp}.{uuid.uuid4().hex[:8]}"
        path = self.store.artifact_root / f"{artifact_id}.png"
        path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        run_id = f"run.observe.{stamp}.{uuid.uuid4().hex[:8]}"
        from .models import ArtifactRef

        artifact = ArtifactRef(
            id=artifact_id,
            kind="screenshot",
            path=str(path),
            sha256=digest,
            captured_at=captured_at,
            run_id=run_id,
            media_type="image/png",
            metadata={"serial": self.serial},
        )
        ui_artifact = None
        try:
            if not include_ui:
                raise AdapterError("UI hierarchy skipped for this frame")
            self._adb_optional(
                "shell",
                "rm",
                "-f",
                "/sdcard/omni-window.xml",
                timeout=10,
            )
            try:
                # MuMu Android 15 may return a non-zero adb exit code even after
                # uiautomator successfully writes the XML.  The fresh file is the
                # contract; still attempt to read and validate it.
                self._adb_optional(
                    "shell",
                    "uiautomator",
                    "dump",
                    "/sdcard/omni-window.xml",
                    timeout=15,
                )
            except AdapterError:
                pass
            xml = self._adb_optional(
                "exec-out",
                "cat",
                "/sdcard/omni-window.xml",
                binary=True,
                timeout=15,
            )
            assert isinstance(xml, bytes)
            if xml.lstrip().startswith(b"<?xml"):
                ui_id = f"art.ui.{stamp}.{uuid.uuid4().hex[:8]}"
                ui_path = self.store.artifact_root / f"{ui_id}.xml"
                ui_path.write_bytes(xml)
                ui_artifact = ArtifactRef(
                    id=ui_id,
                    kind="ui_tree",
                    path=str(ui_path),
                    sha256=hashlib.sha256(xml).hexdigest(),
                    captured_at=captured_at,
                    run_id=run_id,
                    media_type="application/xml",
                    metadata={"serial": self.serial},
                )
        except AdapterError:
            ui_artifact = None
        run = RunResult(
            id=run_id,
            adapter="adb",
            target_id=f"device://adb/{self.serial}",
            status="passed",
            ended_at=utc_now(),
            artifact_ids=[artifact.id] + ([ui_artifact.id] if ui_artifact else []),
        )
        self.store.save_artifact(artifact)
        if ui_artifact:
            self.store.save_artifact(ui_artifact)
        self.store.save_run(run)
        self.store.append_event(
            run.id,
            "observe",
            {
                "artifact_id": artifact.id,
                "ui_tree_id": ui_artifact.id if ui_artifact else None,
                "serial": self.serial,
            },
        )
        return ObservationBundle(
            target_id=run.target_id,
            captured_at=captured_at,
            frame=artifact,
            ui_tree=ui_artifact,
            metadata={"serial": self.serial, "adapter": "adb"},
        )

    def observe(self) -> ObservationBundle:
        return self._capture_observation(include_ui=True)

    def observe_frame(self, *, include_ui: bool = False) -> ObservationBundle:
        """Capture a stream-friendly frame, optionally adding the slower UI tree."""
        return self._capture_observation(include_ui=include_ui)

    def observe_probe_frame(self) -> bytes:
        """Capture an in-memory stability probe with no run or artifact registration."""
        return self._capture_png()

    def observe_probe_jpeg(self, *, quality: int = 82) -> bytes:
        """Capture a low-latency JPEG frame without creating canonical evidence.

        Android-side PNG compression is the dominant cost of ``screencap -p``.
        The live room instead pulls the raw RGBA framebuffer and performs the
        much faster JPEG encoding on the host. Canonical evidence continues to
        use the lossless PNG path above.
        """

        if not self.serial:
            raise AdapterError("connect a target before observe")
        raw = self._adb("exec-out", "screencap", binary=True, timeout=30)
        assert isinstance(raw, bytes)
        if len(raw) < 12:
            raise AdapterError("adb raw screencap returned an incomplete header")

        import cv2
        import numpy as np

        width, height, pixel_format = np.frombuffer(raw[:12], dtype="<u4")
        width, height, pixel_format = int(width), int(height), int(pixel_format)
        pixel_bytes = width * height * 4
        offset = len(raw) - pixel_bytes
        if (
            width <= 0
            or height <= 0
            or offset not in {12, 16}
            or pixel_format not in {1, 2}
        ):
            raise AdapterError("unsupported adb raw screencap layout")
        rgba = np.frombuffer(raw, dtype=np.uint8, offset=offset).reshape((height, width, 4))
        bgr = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
        encoded, jpeg = cv2.imencode(
            ".jpg",
            bgr,
            [cv2.IMWRITE_JPEG_QUALITY, max(45, min(int(quality), 92))],
        )
        if not encoded:
            raise AdapterError("host JPEG encoding failed")
        return jpeg.tobytes()

    def begin_video_capture(self, *, max_seconds: int = 180) -> AdbVideoCaptureHandle:
        if not self.serial:
            raise AdapterError("connect a target before video capture")
        duration = max(10, min(int(max_seconds), 180))
        remote_path = f"/sdcard/omni-evidence-{uuid.uuid4().hex}.mp4"
        try:
            active = str(
                self._adb_optional(
                    "shell",
                    "pidof",
                    "screenrecord",
                    timeout=5,
                )
            ).strip()
        except AdapterError:
            active = ""
        if active:
            raise AdapterError(f"another screenrecord process is active: {active}")
        command = [
            str(self.adb),
            "-s",
            self.serial,
            "shell",
            "screenrecord",
            "--bit-rate",
            "8000000",
            "--time-limit",
            str(duration),
            remote_path,
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            **headless_process_kwargs(),
        )
        remote_pid: int | None = None
        for _attempt in range(30):
            if process.poll() is not None:
                stderr = (process.communicate()[1] or b"").decode("utf-8", "replace").strip()
                raise AdapterError(stderr or "screenrecord exited before capture started")
            try:
                output = str(
                    self._adb_optional(
                        "shell",
                        "pidof",
                        "screenrecord",
                        timeout=5,
                    )
                ).strip()
            except AdapterError:
                output = ""
            matches = re.findall(r"\d+", output)
            if matches:
                remote_pid = int(matches[0])
                break
            time.sleep(0.1)
        if remote_pid is None:
            process.terminate()
            process.wait(timeout=5)
            raise AdapterError("screenrecord process did not become observable")
        return AdbVideoCaptureHandle(
            remote_path=remote_path,
            remote_pid=remote_pid,
            process=process,
            started_at=utc_now(),
        )

    def finish_video_capture(
        self,
        handle: AdbVideoCaptureHandle,
        *,
        evidence_run_id: str,
        evidence_step_id: str,
    ) -> Any:
        if not self.serial:
            raise AdapterError("connect a target before finishing video capture")
        artifact_id = f"art.video.{int(time.time() * 1000)}.{uuid.uuid4().hex[:8]}"
        path = self.store.artifact_root / f"{artifact_id}.mp4"
        try:
            try:
                self._adb_optional(
                    "shell",
                    "kill",
                    "-2",
                    str(handle.remote_pid),
                    timeout=10,
                )
            except AdapterError:
                # A short capture may already have reached its time limit.
                pass
            try:
                handle.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                handle.process.terminate()
                handle.process.wait(timeout=5)
            self._adb("pull", handle.remote_path, str(path), timeout=60)
        finally:
            try:
                self._adb_optional(
                    "shell",
                    "rm",
                    "-f",
                    handle.remote_path,
                    timeout=10,
                )
            except AdapterError:
                pass
        if not path.is_file():
            raise AdapterError("screenrecord MP4 was not pulled")
        body = path.read_bytes()
        if len(body) < 16 or body[4:8] != b"ftyp":
            raise AdapterError("screenrecord produced an invalid MP4 container")
        from .models import ArtifactRef

        artifact = ArtifactRef(
            id=artifact_id,
            kind="video",
            path=str(path),
            sha256=hashlib.sha256(body).hexdigest(),
            captured_at=handle.started_at,
            run_id=evidence_run_id,
            media_type="video/mp4",
            metadata={
                "serial": self.serial,
                "evidence_run_id": evidence_run_id,
                "evidence_step_id": evidence_step_id,
                "screenrecord_pid": handle.remote_pid,
                "capture_started_at": handle.started_at,
            },
        )
        self.store.save_artifact(artifact)
        return artifact

    @staticmethod
    def _safe_package(package: str) -> str:
        if not package or not re.fullmatch(r"[a-zA-Z0-9_.]+", package):
            raise AdapterError("a safe Android package name is required")
        return package

    def install_apk(self, apk_path: str | Path, *, replace: bool = True) -> dict[str, Any]:
        if not self.serial:
            raise AdapterError("connect a target before install")
        path = Path(apk_path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".apk":
            raise AdapterError(f"APK file not found: {path}")
        args = [str(self.adb), "-s", self.serial, "install"]
        if replace:
            args.append("-r")
        args.append(str(path))
        output = str(_run(args, timeout=180.0)).strip()
        run = RunResult(
            id=f"run.install.{int(time.time() * 1000)}.{uuid.uuid4().hex[:8]}",
            adapter="adb",
            target_id=f"device://adb/{self.serial}",
            status="passed",
            ended_at=utc_now(),
        )
        self.store.save_run(run)
        self.store.append_event(
            run.id,
            "install",
            {"apk_name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
        )
        return {"ok": True, "run_id": run.id, "output": output, "apk_name": path.name}

    def start_package(self, package: str) -> dict[str, Any]:
        package = self._safe_package(package)
        return self.act(NormalizedAction(type="launch", package=package))

    def force_stop_package(self, package: str) -> dict[str, Any]:
        if not self.serial:
            raise AdapterError("connect a target before force-stop")
        package = self._safe_package(package)
        output = str(self._adb("shell", "am", "force-stop", package)).strip()
        run = RunResult(
            id=f"run.force-stop.{int(time.time() * 1000)}.{uuid.uuid4().hex[:8]}",
            adapter="adb",
            target_id=f"device://adb/{self.serial}",
            status="passed",
            ended_at=utc_now(),
        )
        self.store.save_run(run)
        self.store.append_event(run.id, "force_stop", {"package": package})
        return {"ok": True, "run_id": run.id, "output": output, "package": package}

    def package_installed(self, package: str) -> bool:
        package = self._safe_package(package)
        output = str(self._adb("shell", "pm", "path", package)).strip()
        return output.startswith("package:")

    def package_info(self, package: str) -> dict[str, str | int | None]:
        """Read stable package/build identity without changing app state."""

        package = self._safe_package(package)
        output = str(self._adb("shell", "dumpsys", "package", package))

        def value(pattern: str) -> str | None:
            match = re.search(pattern, output, flags=re.MULTILINE)
            return match.group(1).strip() if match else None

        version_code = value(r"^\s*versionCode=(\d+)")
        return {
            "package": package,
            "version_name": value(r"^\s*versionName=([^\s]+)"),
            "version_code": int(version_code) if version_code is not None else None,
            "first_install_time": value(r"^\s*firstInstallTime=(.+)$"),
            "last_update_time": value(r"^\s*lastUpdateTime=(.+)$"),
        }

    def foreground_activity(self) -> str:
        if not self.serial:
            raise AdapterError("connect a target before querying activity")
        output = str(self._adb("shell", "dumpsys", "activity", "activities"))
        match = re.search(r"topResumedActivity=.*?\su\d+\s+([\w.]+/[\w.$]+)", output)
        return match.group(1) if match else "unknown"

    def recover_connection(self) -> TargetInfo:
        if not self.serial:
            raise AdapterError("connect a target before recovery")
        serial = self.serial
        try:
            self._adb("reconnect", "device", timeout=15)
        except AdapterError:
            if ":" in serial and not serial.startswith("emulator-"):
                _run([str(self.adb), "disconnect", serial], timeout=10)
                _run([str(self.adb), "connect", serial], timeout=20)
        try:
            self._adb("wait-for-device", timeout=30)
        except AdapterError:
            pass
        last_error: AdapterError | None = None
        for _attempt in range(5):
            try:
                return self.connect(serial)
            except AdapterError as exc:
                last_error = exc
                time.sleep(0.3)
        raise last_error or AdapterError(f"target did not recover: {serial}")

    def reset(self, snapshot: str | None = None) -> dict[str, Any]:
        if snapshot:
            raise AdapterError("generic ADB targets cannot restore emulator snapshots; use a provider adapter")
        if not self.serial:
            raise AdapterError("connect a target before reset")
        self._adb("shell", "input", "keyevent", "3")
        return {"ok": True, "mode": "home", "serial": self.serial}

    def checkpoint(self) -> str:
        raise AdapterError("generic ADB target does not expose snapshot checkpoints")

    def restore(self, snapshot: str) -> dict[str, Any]:
        raise AdapterError("generic ADB target does not expose snapshot restore")

    def _multitouch_transport(self) -> ScrcpyControlTransport:
        if not self.serial:
            raise AdapterError("connect a target before multi-touch")
        width, height = self._android_logical_viewport()
        return ScrcpyControlTransport(
            adb_path=self.adb,
            serial=self.serial,
            width=width,
            height=height,
        )

    def _android_logical_viewport(self) -> tuple[int, int]:
        """Return the oriented coordinate space consumed by Android input.

        ``wm size`` exposes the physical panel dimensions on MuMu even while
        the active surface is rotated. ``dumpsys input`` exposes the current
        logical display size, which is also the size scrcpy expects in touch
        position messages.
        """
        input_dump = str(self._adb("shell", "dumpsys", "input"))
        logical = re.search(r"\blogicalSize=(\d+)x(\d+)\b", input_dump)
        if logical:
            return int(logical.group(1)), int(logical.group(2))

        size_output = str(self._adb("shell", "wm", "size"))
        physical = re.search(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", size_output)
        if not physical:
            raise AdapterError(
                "cannot determine Android logical viewport: "
                f"dumpsys input={input_dump!r}; wm size={size_output!r}"
            )
        width, height = int(physical.group(1)), int(physical.group(2))
        orientation = re.search(r"\bSurfaceOrientation:\s*([0-3])\b", input_dump)
        if orientation and int(orientation.group(1)) % 2:
            width, height = height, width
        return width, height

    def _perform_pinch(self, action: NormalizedAction) -> str:
        transport = self._multitouch_transport()
        try:
            assert action.x is not None and action.y is not None
            assert action.pinch_direction is not None and action.pinch_percent is not None
            with transport:
                transport.pinch(
                    center=(action.x, action.y),
                    percent=action.pinch_percent,
                    duration=max(action.duration_ms / 1000, 0.05),
                    steps=action.pinch_steps,
                    in_or_out=action.pinch_direction,
                )
                transport.assert_input_idle("after pinch")
            return (
                "SCRCPY["
                f"tunnel={transport.tunnel_mode},"
                f"detached={str(transport.tunnel_detached).lower()},"
                f"server_exit={transport.server_exit_code}]"
            )
        except ScrcpyControlError as exc:
            detail = f"{exc}; {transport.diagnostic_summary()}"
            try:
                transport.assert_input_idle("after failed pinch")
            except ScrcpyControlError as state_exc:
                raise AdapterError(f"{detail}; {state_exc}") from exc
            raise AdapterError(detail) from exc

    def _perform_two_finger_swipe(self, action: NormalizedAction) -> str:
        transport = self._multitouch_transport()
        try:
            assert None not in (action.x, action.y, action.x2, action.y2)
            with transport:
                transport.two_finger_swipe(
                    (action.x, action.y),
                    (action.x2, action.y2),
                    duration=max(action.duration_ms / 1000, 0.05),
                    steps=action.two_finger_steps,
                    offset=(action.two_finger_offset_x, action.two_finger_offset_y),
                )
                transport.assert_input_idle("after two-finger swipe")
            return (
                "SCRCPY["
                f"tunnel={transport.tunnel_mode},"
                f"detached={str(transport.tunnel_detached).lower()},"
                f"server_exit={transport.server_exit_code}]"
            )
        except ScrcpyControlError as exc:
            detail = f"{exc}; {transport.diagnostic_summary()}"
            try:
                transport.assert_input_idle("after failed two-finger swipe")
            except ScrcpyControlError as state_exc:
                raise AdapterError(f"{detail}; {state_exc}") from exc
            raise AdapterError(detail) from exc

    def act(self, action: NormalizedAction) -> dict[str, Any]:
        return self._act(action, task_id=None)

    def act_with_task(
        self,
        action: NormalizedAction,
        *,
        task_id: str,
    ) -> dict[str, Any]:
        if not task_id.strip():
            raise AdapterError("task-bound action requires a non-blank task id")
        return self._act(action, task_id=task_id)

    def _act(
        self,
        action: NormalizedAction,
        *,
        task_id: str | None,
    ) -> dict[str, Any]:
        if not self.serial:
            raise AdapterError("connect a target before act")
        try:
            if action.type == "tap":
                if action.x is None or action.y is None:
                    raise AdapterError("tap requires x/y")
                args = ["shell", "input", "tap", str(action.x), str(action.y)]
            elif action.type == "swipe":
                if None in (action.x, action.y, action.x2, action.y2):
                    raise AdapterError("swipe requires x/y/x2/y2")
                args = [
                    "shell", "input", "swipe", str(action.x), str(action.y),
                    str(action.x2), str(action.y2), str(action.duration_ms),
                ]
            elif action.type == "pinch":
                method = self._perform_pinch(action)
                return self._record_action(
                    action,
                    f"multitouch:{method}",
                    task_id=task_id,
                )
            elif action.type == "two_finger_swipe":
                method = self._perform_two_finger_swipe(action)
                return self._record_action(
                    action,
                    f"multitouch:{method}",
                    task_id=task_id,
                )
            elif action.type == "text":
                if not action.text or not re.fullmatch(r"[\w .,:@+\-/]+", action.text):
                    raise AdapterError("text action only accepts conservative ASCII input")
                args = ["shell", "input", "text", action.text.replace(" ", "%s")]
            elif action.type == "key":
                if action.keycode is None:
                    raise AdapterError("key requires keycode")
                args = ["shell", "input", "keyevent", str(action.keycode)]
            elif action.type == "back":
                args = ["shell", "input", "keyevent", "4"]
            elif action.type == "home":
                args = ["shell", "input", "keyevent", "3"]
            elif action.type == "launch":
                package = self._safe_package(action.package or "")
                args = [
                    "shell", "monkey", "-p", package,
                    "-c", "android.intent.category.LAUNCHER", "1",
                ]
            elif action.type == "force_stop":
                package = self._safe_package(action.package or "")
                args = ["shell", "am", "force-stop", package]
            elif action.type == "wait":
                time.sleep(max(0.0, min(action.seconds, 30.0)))
                return self._record_action(action, task_id=task_id)
            else:
                raise AdapterError(f"unsupported action: {action.type}")
            output = self._adb(*args)
            return self._record_action(
                action,
                str(output).strip(),
                task_id=task_id,
            )
        except AdapterError as exc:
            result = self._record_action(
                action,
                task_id=task_id,
                status="failed",
                error=str(exc),
            )
            raise AdapterActionError(str(exc), result) from exc

    def evaluate(self, task: BenchmarkTask) -> RunResult:
        checks = [
            ObjectiveCheck(
                id=check.id,
                description=check.description,
                expected=check.expected,
                actual=None,
                passed=None,
            )
            for check in task.checks
        ]
        return RunResult(
            id=f"run.evaluate.{uuid.uuid4().hex}",
            adapter="adb",
            target_id=f"device://adb/{self.serial or 'unconnected'}",
            task_id=task.id,
            status="stopped",
            ended_at=utc_now(),
            checks=checks,
            error="objective checker must be supplied by the game-specific adapter",
        )


class SourceFixtureAdapter(GameAdapter):
    """Deterministic source-backed adapter used to validate the shared contract.

    It does not pretend to play a game.  It verifies that source-backed states can
    enter the same trace/evaluation pipeline as pixel observations.
    """

    def __init__(self, store: ObservatoryStore) -> None:
        self.store = store
        self.manifest: dict[str, Any] | None = None
        self.target = ""

    def connect(self, target: str) -> TargetInfo:
        path = Path(target.removeprefix("fixture://")).resolve()
        if not path.is_file():
            raise AdapterError(f"fixture not found: {path}")
        self.manifest = json.loads(path.read_text(encoding="utf-8"))
        self.target = str(path)
        return TargetInfo(
            id=f"fixture://{path}",
            kind="fixture",
            label=self.manifest.get("title", path.stem),
            status="online",
            capabilities=["source_probe", "reset", "objective_checker"],
            metadata={"path": str(path)},
        )

    def observe(self) -> ObservationBundle:
        raise AdapterError("source fixtures expose state through evaluate(), not pixel observe()")

    def reset(self, snapshot: str | None = None) -> dict[str, Any]:
        if not self.target:
            raise AdapterError("connect a fixture before reset")
        self.manifest = json.loads(Path(self.target).read_text(encoding="utf-8"))
        return {"ok": True, "snapshot": self.target}

    def checkpoint(self) -> str:
        if not self.target:
            raise AdapterError("connect a fixture before checkpoint")
        return self.target

    def restore(self, snapshot: str) -> dict[str, Any]:
        return self.connect(f"fixture://{snapshot}").model_dump(mode="json")

    def act(self, action: NormalizedAction) -> dict[str, Any]:
        raise AdapterError("source fixtures are read-only")

    def evaluate(self, task: BenchmarkTask) -> RunResult:
        if self.manifest is None:
            raise AdapterError("connect a fixture before evaluate")
        facts = self.manifest.get("facts", {})
        checks: list[ObjectiveCheck] = []
        for item in task.checks:
            actual = facts.get(item.id)
            checks.append(item.model_copy(update={"actual": actual, "passed": actual == item.expected}))
        passed = all(item.passed for item in checks)
        identity = json.dumps(
            {
                "fixture": str(self.target),
                "facts": facts,
                "task": task.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return RunResult(
            id=f"run.fixture.{digest}",
            adapter="source_fixture",
            target_id=f"fixture://{self.target}",
            task_id=task.id,
            status="passed" if passed else "failed",
            ended_at=utc_now(),
            checks=checks,
        )
