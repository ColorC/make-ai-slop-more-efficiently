from __future__ import annotations

import os
import re
import socket
import struct
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root


class ScrcpyControlError(RuntimeError):
    pass


def resolve_scrcpy_server(explicit: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("OMNI_SCRCPY_SERVER_PATH"):
        candidates.append(Path(os.environ["OMNI_SCRCPY_SERVER_PATH"]))

    device_tools = omni_workspace_root().parent / "lofa" / "tools" / "device"
    candidates.extend(
        path
        for directory in sorted(device_tools.glob("scrcpy-win64-v*"), reverse=True)
        for path in (directory / "scrcpy-server", directory / "scrcpy-server.jar")
    )
    candidates.extend(
        [
            Path("E:/WindowsWorkspace/lofa/tools/device/scrcpy-win64-v4.0/scrcpy-server"),
            Path("E:/WindowsWorkspace/lofa/tools/devview/scrcpy-server"),
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ScrcpyControlError(
        "scrcpy server not found; set OMNI_SCRCPY_SERVER_PATH to a scrcpy-server file"
    )


class ScrcpyControlTransport:
    """Control-only scrcpy transport for coherent multi-pointer injection.

    A single scrcpy server owns pointer state for the whole gesture. This is
    important on Android 15, where the bundled Airtest MAXTOUCH server can emit
    release events with a different synthetic device identity and leave
    InputDispatcher contacts permanently down.
    """

    ACTION_DOWN = 0
    ACTION_UP = 1
    ACTION_MOVE = 2
    MESSAGE_TYPE_INJECT_TOUCH = 2
    REMOTE_SERVER = "/data/local/tmp/game-observatory-scrcpy-server.jar"

    def __init__(
        self,
        *,
        adb_path: str | Path,
        serial: str,
        width: int,
        height: int,
        server_path: str | Path | None = None,
        server_version: str | None = None,
    ) -> None:
        if width <= 0 or height <= 0 or width > 65535 or height > 65535:
            raise ScrcpyControlError("scrcpy touch viewport must fit unsigned 16-bit dimensions")
        self.adb_path = Path(adb_path).resolve()
        self.serial = serial
        self.width = width
        self.height = height
        self.server_path = resolve_scrcpy_server(server_path)
        self.server_version = server_version or self._infer_server_version(self.server_path)
        self._socket: socket.socket | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._forward_port: int | None = None

    @staticmethod
    def _infer_server_version(path: Path) -> str:
        configured = os.environ.get("OMNI_SCRCPY_SERVER_VERSION")
        if configured:
            return configured
        match = re.search(r"scrcpy-win64-v(\d+(?:\.\d+)*)", str(path), re.IGNORECASE)
        return match.group(1) if match else "4.0"

    def _adb(self, *args: str, timeout: float = 30.0) -> str:
        proc = subprocess.run(
            [str(self.adb_path), "-s", self.serial, *args],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        output = proc.stdout.decode("utf-8", "replace")
        error = proc.stderr.decode("utf-8", "replace")
        if proc.returncode != 0:
            raise ScrcpyControlError(error.strip() or output.strip() or "adb command failed")
        return output

    @staticmethod
    def _free_local_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    def input_state(self) -> dict[str, Any]:
        dump = self._adb("shell", "dumpsys", "input")
        active_lines: list[str] = []
        for raw_line in dump.splitlines():
            line = raw_line.strip()
            if "touchingPointers=[" in line and "touchingPointers=[]" not in line:
                active_lines.append(line)
            elif "mMotionMementos:" in line and "deviceId=-1" in line:
                active_lines.append(line)
        return {
            "idle": not active_lines,
            "active_lines": list(dict.fromkeys(active_lines)),
        }

    def assert_input_idle(self, stage: str) -> dict[str, Any]:
        state = self.input_state()
        if not state["idle"]:
            detail = " | ".join(state["active_lines"][:4])
            raise ScrcpyControlError(f"Android input state is active at {stage}: {detail}")
        return state

    def open(self) -> ScrcpyControlTransport:
        if self._socket is not None:
            return self
        self.assert_input_idle("before multi-touch")
        self._adb("push", str(self.server_path), self.REMOTE_SERVER, timeout=60.0)
        scid = int(uuid.uuid4().hex[:7], 16)
        port = self._free_local_port()
        socket_name = f"scrcpy_{scid:08x}"
        self._adb("forward", f"tcp:{port}", f"localabstract:{socket_name}")
        self._forward_port = port
        command = (
            f"CLASSPATH={self.REMOTE_SERVER} app_process / "
            f"com.genymobile.scrcpy.Server {self.server_version} "
            f"scid={scid:x} log_level=error video=false audio=false control=true "
            "tunnel_forward=true cleanup=false power_on=false raw_stream=true"
        )
        self._process = subprocess.Popen(
            [str(self.adb_path), "-s", self.serial, "shell", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 10.0
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                break
            try:
                self._socket = socket.create_connection(("127.0.0.1", port), timeout=1.0)
                self._socket.settimeout(2.0)
                return self
            except OSError as exc:
                last_error = exc
                time.sleep(0.1)
        output = b""
        if self._process.stdout:
            output = self._process.stdout.read()
        self.close()
        detail = output.decode("utf-8", "replace").strip()
        raise ScrcpyControlError(
            detail or f"scrcpy control socket did not open: {last_error or 'unknown error'}"
        )

    def close(self) -> None:
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        process, self._process = self._process, None
        if process is not None:
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3.0)
        port, self._forward_port = self._forward_port, None
        if port is not None:
            try:
                self._adb("forward", "--remove", f"tcp:{port}")
            except ScrcpyControlError:
                pass

    def __enter__(self) -> ScrcpyControlTransport:
        return self.open()

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()

    def _send_touch(
        self,
        action: int,
        pointer_id: int,
        x: int,
        y: int,
        pressure: float,
    ) -> None:
        if self._socket is None:
            raise ScrcpyControlError("scrcpy control transport is not open")
        x = max(0, min(self.width - 1, int(round(x))))
        y = max(0, min(self.height - 1, int(round(y))))
        fixed_pressure = round(max(0.0, min(1.0, pressure)) * 65535)
        message = struct.pack(
            ">BBqiiHHHII",
            self.MESSAGE_TYPE_INJECT_TOUCH,
            action,
            pointer_id,
            x,
            y,
            self.width,
            self.height,
            fixed_pressure,
            0,
            0,
        )
        self._socket.sendall(message)

    @staticmethod
    def _point_between(start: tuple[float, float], end: tuple[float, float], t: float) -> tuple[int, int]:
        return (
            round(start[0] + (end[0] - start[0]) * t),
            round(start[1] + (end[1] - start[1]) * t),
        )

    def _two_pointer_gesture(
        self,
        start_a: tuple[float, float],
        end_a: tuple[float, float],
        start_b: tuple[float, float],
        end_b: tuple[float, float],
        *,
        duration: float,
        steps: int,
    ) -> None:
        if steps < 2:
            raise ScrcpyControlError("multi-touch gesture requires at least two steps")
        interval = max(duration, 0.05) / steps
        self._send_touch(self.ACTION_DOWN, 0, *self._point_between(start_a, end_a, 0), 1.0)
        self._send_touch(self.ACTION_DOWN, 1, *self._point_between(start_b, end_b, 0), 1.0)
        for index in range(1, steps + 1):
            t = index / steps
            self._send_touch(self.ACTION_MOVE, 0, *self._point_between(start_a, end_a, t), 1.0)
            self._send_touch(self.ACTION_MOVE, 1, *self._point_between(start_b, end_b, t), 1.0)
            time.sleep(interval)
        self._send_touch(self.ACTION_UP, 1, *self._point_between(start_b, end_b, 1), 0.0)
        self._send_touch(self.ACTION_UP, 0, *self._point_between(start_a, end_a, 1), 0.0)
        time.sleep(0.15)

    def pinch(
        self,
        *,
        center: tuple[int, int],
        percent: float,
        duration: float,
        steps: int,
        in_or_out: str,
    ) -> None:
        cx, cy = center
        dx = self.width * percent / 2
        dy = self.height * percent / 2
        wide_a = (cx - dx, cy - dy)
        wide_b = (cx + dx, cy + dy)
        narrow_a = (cx - 2, cy - 2)
        narrow_b = (cx + 2, cy + 2)
        if in_or_out == "out":
            start_a, end_a, start_b, end_b = narrow_a, wide_a, narrow_b, wide_b
        elif in_or_out == "in":
            start_a, end_a, start_b, end_b = wide_a, narrow_a, wide_b, narrow_b
        else:
            raise ScrcpyControlError(f"unsupported pinch direction: {in_or_out}")
        self._two_pointer_gesture(
            start_a,
            end_a,
            start_b,
            end_b,
            duration=duration,
            steps=steps,
        )

    def two_finger_swipe(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        *,
        duration: float,
        steps: int,
        offset: tuple[int, int],
    ) -> None:
        start_b = (start[0] + offset[0], start[1] + offset[1])
        end_b = (end[0] + offset[0], end[1] + offset[1])
        self._two_pointer_gesture(
            start,
            end,
            start_b,
            end_b,
            duration=duration,
            steps=steps,
        )