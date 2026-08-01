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

from .subprocess_policy import headless_process_kwargs


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
            Path("C:/workspace/lofa/tools/device/scrcpy-win64-v4.0/scrcpy-server"),
            Path("C:/workspace/lofa/tools/devview/scrcpy-server"),
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
        self._reverse_socket_name: str | None = None
        self.server_output = ""
        self.server_exit_code: int | None = None
        self.tunnel_mode: str | None = None
        self.tunnel_detached = False
        self._touch_messages_sent = 0
        self._touch_trace: list[str] = []

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
            **headless_process_kwargs(),
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

    def _prepare_reverse_tunnel(self, socket_name: str) -> tuple[socket.socket, int] | None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = int(listener.getsockname()[1])
            self._adb("reverse", f"localabstract:{socket_name}", f"tcp:{port}")
        except (OSError, ScrcpyControlError):
            listener.close()
            return None
        self._reverse_socket_name = socket_name
        self.tunnel_mode = "adb-reverse"
        return listener, port

    def _detach_active_tunnel(self) -> None:
        if self._reverse_socket_name is not None:
            socket_name = self._reverse_socket_name
            try:
                self._adb("reverse", "--remove", f"localabstract:{socket_name}")
            except ScrcpyControlError:
                return
            self._reverse_socket_name = None
            self.tunnel_detached = True
            return
        if self._forward_port is not None:
            port = self._forward_port
            try:
                self._adb("forward", "--remove", f"tcp:{port}")
            except ScrcpyControlError:
                return
            self._forward_port = None
            self.tunnel_detached = True

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
        socket_name = f"scrcpy_{scid:08x}"
        reverse_tunnel = self._prepare_reverse_tunnel(socket_name)
        if reverse_tunnel is not None:
            listener, port = reverse_tunnel
            tunnel_option = ""
        else:
            listener = None
            port = self._free_local_port()
            self._adb("forward", f"tcp:{port}", f"localabstract:{socket_name}")
            self._forward_port = port
            self.tunnel_mode = "adb-forward"
            tunnel_option = "tunnel_forward=true "
        command = (
            f"CLASSPATH={self.REMOTE_SERVER} app_process / "
            f"com.genymobile.scrcpy.Server {self.server_version} "
            f"scid={scid:x} log_level=debug video=false audio=false control=true "
            f"{tunnel_option}cleanup=false power_on=false raw_stream=true"
        )
        self._process = subprocess.Popen(
            [str(self.adb_path), "-s", self.serial, "shell", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **headless_process_kwargs(),
        )
        deadline = time.monotonic() + 10.0
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                break
            try:
                if listener is not None:
                    listener.settimeout(1.0)
                    self._socket, _peer = listener.accept()
                else:
                    self._socket = socket.create_connection(("127.0.0.1", port), timeout=1.0)
                self._socket.settimeout(2.0)
                self._detach_active_tunnel()
                if listener is not None:
                    listener.close()
                return self
            except OSError as exc:
                last_error = exc
                time.sleep(0.1)
        if listener is not None:
            listener.close()
        self.close()
        detail = self.server_output
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
            if process.stdout:
                output = process.stdout.read()
                process.stdout.close()
                self.server_output = output.decode("utf-8", "replace").strip()
            self.server_exit_code = process.returncode
        reverse_socket_name, self._reverse_socket_name = self._reverse_socket_name, None
        if reverse_socket_name is not None:
            try:
                self._adb("reverse", "--remove", f"localabstract:{reverse_socket_name}")
            except ScrcpyControlError:
                pass
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
        event_number = self._touch_messages_sent + 1
        event = (
            f"#{event_number}:action={action},pointer={pointer_id},"
            f"position=({x},{y}),pressure={fixed_pressure}"
        )
        try:
            self._socket.sendall(message)
        except OSError as exc:
            prior = " | ".join(self._touch_trace[-4:]) or "none"
            raise ScrcpyControlError(
                "scrcpy control send failed "
                f"event={event} viewport={self.width}x{self.height} "
                f"last_success=[{prior}]: {exc}"
            ) from exc
        self._touch_messages_sent = event_number
        self._touch_trace.append(event)

    def diagnostic_summary(self) -> str:
        trace = " | ".join(self._touch_trace[-4:]) or "none"
        parts = [
            f"scrcpy_server_exit={self.server_exit_code}",
            f"adb_tunnel_mode={self.tunnel_mode}",
            f"adb_tunnel_detached={str(self.tunnel_detached).lower()}",
            f"touch_messages_sent={self._touch_messages_sent}",
            f"last_touch_events=[{trace}]",
        ]
        if self.server_output:
            parts.append(f"scrcpy_server_output={self.server_output}")
        return "; ".join(parts)

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
        down: set[int] = set()
        primary_error: BaseException | None = None
        try:
            self._send_touch(
                self.ACTION_DOWN, 0, *self._point_between(start_a, end_a, 0), 1.0
            )
            down.add(0)
            self._send_touch(
                self.ACTION_DOWN, 1, *self._point_between(start_b, end_b, 0), 1.0
            )
            down.add(1)
            for index in range(1, steps + 1):
                t = index / steps
                self._send_touch(
                    self.ACTION_MOVE,
                    0,
                    *self._point_between(start_a, end_a, t),
                    1.0,
                )
                self._send_touch(
                    self.ACTION_MOVE,
                    1,
                    *self._point_between(start_b, end_b, t),
                    1.0,
                )
                time.sleep(interval)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            release_error: BaseException | None = None
            for pointer_id, end in ((1, end_b), (0, end_a)):
                if pointer_id not in down:
                    continue
                try:
                    self._send_touch(
                        self.ACTION_UP,
                        pointer_id,
                        *self._point_between(end, end, 1),
                        0.0,
                    )
                except BaseException as exc:
                    release_error = release_error or exc
            time.sleep(0.15)
            if primary_error is None and release_error is not None:
                raise release_error

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
