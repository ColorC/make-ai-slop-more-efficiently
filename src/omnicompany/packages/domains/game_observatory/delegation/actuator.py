"""MaaFramework ADB actuator with automatic replay frame capture."""

from __future__ import annotations

import cProfile
import io
import json
import math
import re
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..adapters import resolve_adb
from ..replay import ActionRecordV1, RunHandle
from ..subprocess_policy import headless_process_kwargs
from .contracts import (
    ActionRequestV1,
    ActionResultV1,
    ActuatorConnectionV1,
    ActuatorTimingV1,
    FrameMode,
)


EXPECTED_RESOLUTION = (1080, 1920)
DEFAULT_ACTUATOR_CONFIG = Path(
    "config/game_observatory/romantic_restaurant/actuator.v1.json"
)

_SCREENCAP_METHODS = {
    1: "EncodeToFileAndPull",
    2: "Encode",
    4: "RawWithGzip",
    8: "RawByNetcat",
    16: "MinicapDirect",
    32: "MinicapStream",
    64: "EmulatorExtras",
}
_INPUT_METHODS = {
    1: "AdbShell",
    2: "MinitouchAndAdbKey",
    4: "Maatouch",
    8: "EmulatorExtras",
}
_STORE_BREAKDOWN_KEYS = (
    "webp_encode",
    "sha256",
    "ensure_capacity",
    "write_file",
)
_RAW_CHANNELS = ("RGB", "BGR", "RGBA")


def _default_maafw_settings() -> tuple[str | None, dict[str, Any]]:
    try:
        value = json.loads(DEFAULT_ACTUATOR_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, {}
    if not isinstance(value, dict):
        return None, {}
    preferred = value.get("preferred_screencap")
    if not isinstance(preferred, str):
        preferred = None
    controller_config = value.get("maafw_controller_config")
    if not isinstance(controller_config, dict):
        return preferred, {}
    extras = controller_config.get("extras")
    return preferred, {"extras": extras} if isinstance(extras, dict) else {}


class ActuatorError(RuntimeError):
    """Base error for the MaaFramework delegation actuator."""


class ActuatorDisconnectedError(ActuatorError):
    """Raised when an operation is attempted without a live Maa connection."""


class ActuatorResolutionMismatchError(ActuatorError):
    """Raised when the device is not the required 1080 x 1920 portrait display."""


def _elapsed_ms(start_ns: int) -> float:
    return max((time.perf_counter_ns() - start_ns) / 1_000_000, 0.000001)


def _method_names(mask: Any, table: dict[int, str]) -> list[str]:
    try:
        value = int(mask)
    except (TypeError, ValueError):
        return []
    return [name for flag, name in table.items() if value & flag]


def _selected_method(
    info: dict[str, Any],
    controller: Any,
    *,
    singular: str,
    plural: str,
    table: dict[int, str],
) -> tuple[str, list[str]]:
    available = _method_names(info.get(plural), table)
    candidates = (
        info.get(singular),
        info.get(f"selected_{singular}"),
        getattr(controller, singular, None),
        getattr(controller, f"selected_{singular}", None),
    )
    for candidate in candidates:
        if candidate is not None and not callable(candidate):
            if isinstance(candidate, int):
                names = _method_names(candidate, table)
                return (names[0] if len(names) == 1 else str(candidate), available or names)
            return str(candidate), available
    if len(available) == 1:
        return available[0], available
    return "maafw_auto", available


def _negotiated_methods_from_log(runtime_root: Path) -> tuple[str | None, str | None]:
    log_path = runtime_root / "debug" / "maafw.log"
    try:
        log_text = log_path.read_text("utf-8", errors="replace")
    except OSError:
        return None, None

    fastest = re.findall(r"The fastest method is ([A-Za-z0-9]+)", log_text)
    screencap_method = fastest[-1] if fastest else None

    start = log_text.rfind("InputAgent::init] | enter")
    end = log_text.find("InputAgent::init] | leave", start)
    if start < 0 or end < 0:
        return screencap_method, None
    input_log = log_text[start:end]
    attempt_pattern = re.compile(
        r"\[(MuMuPlayerExtras|AndrowsExtras|MaatouchInput|MinitouchInput)\.cpp\]"
        r".*?::init\] \| enter"
    )
    attempts = list(attempt_pattern.finditer(input_log))
    selected_input: str | None = None
    for index, attempt in enumerate(attempts):
        next_start = attempts[index + 1].start() if index + 1 < len(attempts) else len(input_log)
        attempt_log = input_log[attempt.start() : next_start]
        method = attempt.group(1)
        if method in {"MaatouchInput", "MinitouchInput"}:
            if "[display_width_=" in attempt_log:
                selected_input = {
                    "MaatouchInput": "Maatouch",
                    "MinitouchInput": "MinitouchAndAdbKey",
                }[method]
        elif index + 1 == len(attempts):
            selected_input = method.removesuffix("Extras") + "Extras"
    if selected_input is None and "No available input method" not in input_log:
        selected_input = "AdbShell"
    return screencap_method, selected_input


def _png_bytes(frame: Any) -> bytes:
    if isinstance(frame, (bytes, bytearray, memoryview)):
        with Image.open(io.BytesIO(bytes(frame))) as source:
            source.load()
            image = source.copy()
    else:
        ndim = getattr(frame, "ndim", None)
        shape = getattr(frame, "shape", ())
        if ndim == 2:
            image = Image.fromarray(frame)
        elif ndim == 3 and len(shape) == 3 and shape[2] == 3:
            image = Image.fromarray(frame[..., ::-1])
        elif ndim == 3 and len(shape) == 3 and shape[2] == 4:
            image = Image.fromarray(frame[..., [2, 1, 0, 3]])
        else:
            raise TypeError(f"unsupported MaaFramework screenshot shape: {shape!r}")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _dhash(png: bytes) -> int:
    with Image.open(io.BytesIO(png)) as source:
        gray = source.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        get_pixels = getattr(gray, "get_flattened_data", gray.getdata)
        pixels = list(get_pixels())
    value = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            value = (value << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    return value


def _dhash_raw(
    pixels: bytes,
    width: int,
    height: int,
    channels: str,
) -> int:
    if channels == "BGR":
        image = Image.frombytes("RGB", (width, height), pixels, "raw", "BGR")
    elif channels == "RGBA":
        image = Image.frombytes("RGBA", (width, height), pixels)
    else:
        image = Image.frombytes("RGB", (width, height), pixels)
    with image:
        gray = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        try:
            get_pixels = getattr(gray, "get_flattened_data", gray.getdata)
            values = list(get_pixels())
        finally:
            gray.close()
    value = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            value = (value << 1) | int(values[offset + column] > values[offset + column + 1])
    return value


def _profiled_add_frame(
    run: RunHandle,
    png: bytes,
    *,
    trigger: str,
    action_id: str,
) -> tuple[Any, float, dict[str, float]]:
    """Store one frame while measuring the synchronous replay hot path."""

    profiler = cProfile.Profile()
    started = time.perf_counter_ns()
    profiler.enable()
    try:
        record = run.add_frame(png, trigger=trigger, action_id=action_id)
    finally:
        profiler.disable()
    elapsed_ms = _elapsed_ms(started)

    breakdown = dict.fromkeys(_STORE_BREAKDOWN_KEYS, 0.0)
    for entry in profiler.getstats():
        code = entry.code
        if isinstance(code, str):
            if "PIL._webp.WebPEncode" in code:
                breakdown["webp_encode"] += entry.inlinetime * 1000
            elif "_hashlib.openssl_sha256" in code:
                breakdown["sha256"] += entry.inlinetime * 1000
        elif code.co_name == "ensure_capacity" and code.co_filename.endswith("quota.py"):
            breakdown["ensure_capacity"] += entry.totaltime * 1000

    measured_ms = sum(breakdown[key] for key in _STORE_BREAKDOWN_KEYS[:-1])
    breakdown["write_file"] = max(elapsed_ms - measured_ms, 0.0)
    return record, elapsed_ms, breakdown


def _profiled_add_frame_raw(
    run: RunHandle,
    pixels: bytes,
    width: int,
    height: int,
    channels: str,
    *,
    trigger: str,
    action_id: str,
) -> tuple[Any, float, dict[str, float]]:
    started = time.perf_counter_ns()
    record = run.add_frame_raw(
        pixels,
        width,
        height,
        channels=channels,
        trigger=trigger,
        action_id=action_id,
    )
    elapsed_ms = _elapsed_ms(started)
    breakdown = dict.fromkeys(_STORE_BREAKDOWN_KEYS, 0.0)
    breakdown["enqueue"] = elapsed_ms
    return record, elapsed_ms, breakdown


def _merge_store_breakdown(
    total: dict[str, float],
    addition: dict[str, float],
) -> None:
    for key, value in addition.items():
        total[key] = total.get(key, 0.0) + value


class MaaAdbActuator:
    """Execute four ADB primitives and persist their surrounding replay frames."""

    def __init__(
        self,
        adb_path: Path | None,
        serial: str,
        run: RunHandle,
        controller_factory: Callable[[Path, str], Any] | None = None,
        post_frame_delay_ms: int = 150,
        settle_timeout_ms: int = 1500,
        preferred_screencap: str | None = None,
    ) -> None:
        if post_frame_delay_ms < 0:
            raise ValueError("post_frame_delay_ms must be non-negative")
        if settle_timeout_ms < 0:
            raise ValueError("settle_timeout_ms must be non-negative")
        if preferred_screencap is not None and preferred_screencap not in _SCREENCAP_METHODS.values():
            choices = ", ".join(_SCREENCAP_METHODS.values())
            raise ValueError(f"preferred_screencap must be one of: {choices}")
        self._configured_adb_path = adb_path
        self.serial = serial
        self.run = run
        self.controller_factory = controller_factory
        self.post_frame_delay_ms = post_frame_delay_ms
        self.settle_timeout_ms = settle_timeout_ms
        default_preferred, default_controller_config = _default_maafw_settings()
        self.preferred_screencap = preferred_screencap or default_preferred
        self._controller_config = (
            default_controller_config if preferred_screencap is None else {}
        )
        self.adb_path: Path | None = None
        self.controller: Any | None = None
        self.connection: ActuatorConnectionV1 | None = None
        self._use_adb_screencap = False
        self._closed = False

    def connect(self) -> ActuatorConnectionV1:
        if self.controller is not None and not self._closed:
            return self._require_connection()

        started = time.perf_counter_ns()
        self.adb_path = (
            Path(self._configured_adb_path)
            if self._configured_adb_path is not None
            else resolve_adb()
        )
        missing_items: list[str] = []
        runtime_root: Path | None = None

        if self.controller_factory is None:
            from maa import Library
            from maa.controller import AdbController
            from maa.define import MaaAdbInputMethodEnum, MaaAdbScreencapMethodEnum
            from maa.toolkit import Toolkit

            runtime_root = self.run.run_dir / "maafw"
            runtime_root.mkdir(parents=True, exist_ok=True)
            if not Toolkit.init_option(runtime_root, {"logging": True}):
                raise ActuatorError("MaaFramework toolkit initialization failed")
            maafw_version = Library.version()
            if self.preferred_screencap is None:
                screencap_methods = sum(
                    int(value)
                    for value in (
                        MaaAdbScreencapMethodEnum.EncodeToFileAndPull,
                        MaaAdbScreencapMethodEnum.Encode,
                        MaaAdbScreencapMethodEnum.RawWithGzip,
                        MaaAdbScreencapMethodEnum.RawByNetcat,
                        MaaAdbScreencapMethodEnum.MinicapDirect,
                        MaaAdbScreencapMethodEnum.MinicapStream,
                        MaaAdbScreencapMethodEnum.EmulatorExtras,
                    )
                )
            else:
                screencap_methods = int(
                    getattr(MaaAdbScreencapMethodEnum, self.preferred_screencap)
                )
            input_methods = sum(
                int(value)
                for value in (
                    MaaAdbInputMethodEnum.AdbShell,
                    MaaAdbInputMethodEnum.MinitouchAndAdbKey,
                    MaaAdbInputMethodEnum.Maatouch,
                    MaaAdbInputMethodEnum.EmulatorExtras,
                )
            )
            agent_path = Path(AdbController.AGENT_BINARY_PATH)
            if not agent_path.is_dir():
                missing_items.append(str(agent_path))
            self.controller = AdbController(
                self.adb_path,
                self.serial,
                screencap_methods=screencap_methods,
                input_methods=input_methods,
                config=self._controller_config,
            )
        else:
            maafw_version = "test-double"
            self.controller = self.controller_factory(self.adb_path, self.serial)

        raw_size = getattr(self.controller, "set_screenshot_use_raw_size", None)
        if callable(raw_size) and not raw_size(True):
            raise ActuatorError("MaaFramework rejected raw-size screenshots")

        job = self.controller.post_connection()
        self._wait_for_job(job, "connection")
        if not self._controller_connected():
            self.controller = None
            raise ActuatorDisconnectedError(
                f"MaaFramework could not connect to ADB target {self.serial}"
            )

        resolution = self._read_resolution()
        if resolution != EXPECTED_RESOLUTION:
            self._closed = True
            raise ActuatorResolutionMismatchError(
                f"expected {EXPECTED_RESOLUTION[0]}x{EXPECTED_RESOLUTION[1]}, "
                f"got {resolution[0]}x{resolution[1]}"
            )

        try:
            info = dict(self.controller.info)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            info = {}
        screencap_method, available_screencap = _selected_method(
            info,
            self.controller,
            singular="screencap_method",
            plural="screencap_methods",
            table=_SCREENCAP_METHODS,
        )
        input_method, available_input = _selected_method(
            info,
            self.controller,
            singular="input_method",
            plural="input_methods",
            table=_INPUT_METHODS,
        )
        if runtime_root is not None:
            logged_screencap, logged_input = _negotiated_methods_from_log(runtime_root)
            screencap_method = logged_screencap or screencap_method
            input_method = logged_input or input_method
        self.connection = ActuatorConnectionV1(
            serial=self.serial,
            adb_path=str(self.adb_path),
            maafw_version=maafw_version,
            screencap_method=screencap_method,
            input_method=input_method,
            available_screencap_methods=available_screencap,
            available_input_methods=available_input,
            resolution=resolution,
            connect_ms=_elapsed_ms(started),
            missing_items=missing_items,
            controller_info=info,
        )
        self._closed = False
        return self.connection

    def screenshot(self) -> tuple[bytes, float]:
        self._require_connected()
        started = time.perf_counter_ns()
        if self._use_adb_screencap:
            return self._adb_screenshot(), _elapsed_ms(started)

        try:
            frame = self._controller_screenshot()
            return _png_bytes(frame), _elapsed_ms(started)
        except ActuatorDisconnectedError:
            raise
        except Exception as exc:
            if not self._controller_connected():
                raise ActuatorDisconnectedError(
                    f"MaaFramework connection lost for {self.serial}"
                ) from exc
            png = self._adb_screenshot()
            self._use_adb_screencap = True
            connection = self._require_connection()
            connection.screencap_method = "adb_fallback"
            item = f"MaaFramework screencap failed: {type(exc).__name__}: {exc}"
            if item not in connection.missing_items:
                connection.missing_items.append(item)
            return png, _elapsed_ms(started)

    def screenshot_raw(self) -> tuple[bytes, int, int, str, float]:
        self._require_connected()
        started = time.perf_counter_ns()
        if self._use_adb_screencap:
            png = self._adb_screenshot()
            with Image.open(io.BytesIO(png)) as image:
                width, height = image.size
            return png, width, height, "PNG", _elapsed_ms(started)

        try:
            frame = self._controller_screenshot()
            if isinstance(frame, np.ndarray) and frame.dtype == np.uint8:
                shape = frame.shape
                if frame.ndim == 3 and shape[2] == 3:
                    pixels = np.ascontiguousarray(frame).tobytes()
                    return pixels, shape[1], shape[0], "BGR", _elapsed_ms(started)
                if frame.ndim == 3 and shape[2] == 4:
                    rgba = np.ascontiguousarray(frame[..., [2, 1, 0, 3]])
                    return rgba.tobytes(), shape[1], shape[0], "RGBA", _elapsed_ms(started)
            png = _png_bytes(frame)
            with Image.open(io.BytesIO(png)) as image:
                width, height = image.size
            return png, width, height, "PNG", _elapsed_ms(started)
        except ActuatorDisconnectedError:
            raise
        except Exception as exc:
            if not self._controller_connected():
                raise ActuatorDisconnectedError(
                    f"MaaFramework connection lost for {self.serial}"
                ) from exc
            png = self._adb_screenshot()
            self._use_adb_screencap = True
            connection = self._require_connection()
            connection.screencap_method = "adb_fallback"
            item = f"MaaFramework screencap failed: {type(exc).__name__}: {exc}"
            if item not in connection.missing_items:
                connection.missing_items.append(item)
            with Image.open(io.BytesIO(png)) as image:
                width, height = image.size
            return png, width, height, "PNG", _elapsed_ms(started)

    def act(
        self,
        request: ActionRequestV1,
        frame_mode: FrameMode = "png",
    ) -> ActionResultV1:
        if frame_mode not in {"png", "raw"}:
            raise ValueError("frame_mode must be 'png' or 'raw'")
        self._require_connected()
        action_id = f"a.{time.monotonic_ns()}.{uuid.uuid4().hex[:6]}"
        started = time.perf_counter_ns()
        pre_capture_ms = 0.0
        input_ms = 0.0
        settle_ms = 0.0
        post_capture_ms = 0.0
        store_ms = 0.0
        store_breakdown_ms = dict.fromkeys(_STORE_BREAKDOWN_KEYS, 0.0)
        if frame_mode == "raw":
            store_breakdown_ms["enqueue"] = 0.0
        pre_frame_id: str | None = None
        post_frame_ids: list[str] = []
        settled = request.settle == "fixed"
        error: str | None = None

        try:
            if request.capture_pre:
                (
                    record,
                    _pre_payload,
                    _pre_width,
                    _pre_height,
                    _pre_channels,
                    pre_capture_ms,
                    frame_store_ms,
                    frame_breakdown,
                ) = self._capture_and_store(
                    frame_mode, trigger="pre_action", action_id=action_id
                )
                pre_frame_id = record.frame_id
                store_ms += frame_store_ms
                _merge_store_breakdown(store_breakdown_ms, frame_breakdown)

            input_started = time.perf_counter_ns()
            self._perform_input(request)
            input_ms = _elapsed_ms(input_started)

            settle_started = time.perf_counter_ns()
            if self.post_frame_delay_ms:
                time.sleep(self.post_frame_delay_ms / 1000)
            settle_ms += _elapsed_ms(settle_started)

            previous_hash: int | None = None
            if request.capture_post:
                (
                    record,
                    post_payload,
                    post_width,
                    post_height,
                    post_channels,
                    capture_ms,
                    frame_store_ms,
                    frame_breakdown,
                ) = self._capture_and_store(
                    frame_mode, trigger="post_action", action_id=action_id
                )
                post_capture_ms += capture_ms
                post_frame_ids.append(record.frame_id)
                store_ms += frame_store_ms
                _merge_store_breakdown(store_breakdown_ms, frame_breakdown)
                if request.settle == "stable":
                    previous_hash = self._capture_hash(
                        post_payload,
                        post_width,
                        post_height,
                        post_channels,
                    )

            if request.settle == "stable":
                settled = False
                if request.capture_post:
                    deadline_ns = time.perf_counter_ns() + self.settle_timeout_ms * 1_000_000
                    while time.perf_counter_ns() < deadline_ns:
                        wait_started = time.perf_counter_ns()
                        time.sleep(0.1)
                        settle_ms += _elapsed_ms(wait_started)
                        (
                            record,
                            stable_payload,
                            stable_width,
                            stable_height,
                            stable_channels,
                            capture_ms,
                            frame_store_ms,
                            frame_breakdown,
                        ) = self._capture_and_store(
                            frame_mode, trigger="post_action", action_id=action_id
                        )
                        post_capture_ms += capture_ms
                        post_frame_ids.append(record.frame_id)
                        store_ms += frame_store_ms
                        _merge_store_breakdown(store_breakdown_ms, frame_breakdown)
                        current_hash = self._capture_hash(
                            stable_payload,
                            stable_width,
                            stable_height,
                            stable_channels,
                        )
                        if previous_hash is not None and (previous_hash ^ current_hash).bit_count() <= 2:
                            settled = True
                            break
                        previous_hash = current_hash
        except ActuatorDisconnectedError:
            raise
        except Exception as exc:
            if not self._controller_connected():
                raise ActuatorDisconnectedError(
                    f"MaaFramework connection lost for {self.serial}"
                ) from exc
            error = f"{type(exc).__name__}: {exc}"
            settled = False
            self.run.alert(f"actuator action {action_id} failed: {error}")

        total_ms = _elapsed_ms(started)
        action = ActionRecordV1(
            run_id=self.run.run_id,
            action_id=action_id,
            kind=request.kind,
            intent=request.intent,
            params=dict(request.params),
            pre_frame_id=pre_frame_id,
            post_frame_ids=post_frame_ids,
            duration_ms=total_ms,
            outcome="error" if error else ("settled" if settled else "settle_timeout"),
        )
        store_started = time.perf_counter_ns()
        self.run.add_action(action)
        action_store_ms = _elapsed_ms(store_started)
        store_ms += action_store_ms
        store_breakdown_ms["write_file"] += action_store_ms
        total_ms = _elapsed_ms(started)
        return ActionResultV1(
            action_id=action_id,
            pre_frame_id=pre_frame_id,
            post_frame_ids=post_frame_ids,
            timing=ActuatorTimingV1(
                pre_capture_ms=pre_capture_ms,
                input_ms=input_ms,
                settle_ms=settle_ms,
                post_capture_ms=post_capture_ms,
                store_ms=store_ms,
                store_breakdown_ms=store_breakdown_ms,
                total_ms=total_ms,
            ),
            settled=settled,
            error=error,
        )

    def _controller_screenshot(self) -> Any:
        job = self.controller.post_screencap()
        self._wait_for_job(job, "screencap")
        if hasattr(job, "get"):
            return job.get()
        if hasattr(job, "result"):
            return job.result
        return self.controller.cached_image

    def _capture_and_store(
        self,
        frame_mode: FrameMode,
        *,
        trigger: str,
        action_id: str,
    ) -> tuple[Any, bytes, int, int, str, float, float, dict[str, float]]:
        if frame_mode == "raw":
            payload, width, height, channels, capture_ms = self.screenshot_raw()
            if channels in _RAW_CHANNELS:
                record, store_ms, breakdown = _profiled_add_frame_raw(
                    self.run,
                    payload,
                    width,
                    height,
                    channels,
                    trigger=trigger,
                    action_id=action_id,
                )
            else:
                record, store_ms, breakdown = _profiled_add_frame(
                    self.run,
                    payload,
                    trigger=trigger,
                    action_id=action_id,
                )
                breakdown["enqueue"] = 0.0
            return (
                record,
                payload,
                width,
                height,
                channels,
                capture_ms,
                store_ms,
                breakdown,
            )

        payload, capture_ms = self.screenshot()
        record, store_ms, breakdown = _profiled_add_frame(
            self.run,
            payload,
            trigger=trigger,
            action_id=action_id,
        )
        return record, payload, 0, 0, "PNG", capture_ms, store_ms, breakdown

    @staticmethod
    def _capture_hash(
        payload: bytes,
        width: int,
        height: int,
        channels: str,
    ) -> int:
        if channels in _RAW_CHANNELS:
            return _dhash_raw(payload, width, height, channels)
        return _dhash(payload)

    def close(self) -> None:
        if self.controller is None or self._closed:
            self._closed = True
            return
        try:
            inactive = getattr(self.controller, "post_inactive", None)
            if callable(inactive) and self._controller_connected():
                self._wait_for_job(inactive(), "inactive")
        finally:
            self._closed = True
            self.controller = None

    def _perform_input(self, request: ActionRequestV1) -> None:
        params = request.params
        if request.kind == "wait":
            time.sleep(params["ms"] / 1000)
            return
        if request.kind == "tap":
            self._wait_for_job(self.controller.post_click(params["x"], params["y"]), "tap")
            return
        if request.kind == "swipe":
            self._wait_for_job(
                self.controller.post_swipe(
                    params["x1"],
                    params["y1"],
                    params["x2"],
                    params["y2"],
                    params["duration_ms"],
                ),
                "swipe",
            )
            return

        duration_ms = params["duration_ms"]
        steps = max(2, math.ceil(duration_ms / 50))
        interval_s = duration_ms / steps / 1000
        contact = 0
        down = False
        try:
            self._wait_for_job(
                self.controller.post_touch_down(params["x1"], params["y1"], contact),
                "drag touch_down",
            )
            down = True
            for step in range(1, steps + 1):
                time.sleep(interval_s)
                ratio = step / steps
                x = round(params["x1"] + (params["x2"] - params["x1"]) * ratio)
                y = round(params["y1"] + (params["y2"] - params["y1"]) * ratio)
                self._wait_for_job(
                    self.controller.post_touch_move(x, y, contact), "drag touch_move"
                )
            self._wait_for_job(self.controller.post_touch_up(contact), "drag touch_up")
            down = False
        finally:
            if down:
                try:
                    self._wait_for_job(self.controller.post_touch_up(contact), "drag cleanup")
                except Exception:
                    pass

    def _adb_screenshot(self) -> bytes:
        if self.adb_path is None:
            raise ActuatorDisconnectedError("actuator has not connected")
        process = subprocess.run(
            [str(self.adb_path), "-s", self.serial, "exec-out", "screencap", "-p"],
            capture_output=True,
            check=False,
            timeout=20,
            **headless_process_kwargs(),
        )
        if process.returncode != 0:
            detail = process.stderr.decode("utf-8", "replace").strip()
            raise ActuatorError(detail or "adb fallback screencap failed")
        return _png_bytes(process.stdout)

    def _read_resolution(self) -> tuple[int, int]:
        try:
            resolution = tuple(int(value) for value in self.controller.resolution)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            resolution = (0, 0)
        if len(resolution) == 2 and all(value > 0 for value in resolution):
            return resolution
        if self.adb_path is None:
            return (0, 0)

        process = subprocess.run(
            [str(self.adb_path), "-s", self.serial, "shell", "wm", "size"],
            capture_output=True,
            check=False,
            timeout=20,
            **headless_process_kwargs(),
        )
        if process.returncode != 0:
            return (0, 0)
        matches = re.findall(rb"(\d+)x(\d+)", process.stdout)
        if not matches:
            return (0, 0)
        width, height = matches[-1]
        return (int(width), int(height))

    def _wait_for_job(self, job: Any, operation: str) -> None:
        if job is None:
            return
        if hasattr(job, "wait"):
            job.wait()
        succeeded = getattr(job, "succeeded", True)
        if callable(succeeded):
            succeeded = succeeded()
        if not succeeded:
            raise ActuatorError(f"MaaFramework {operation} job failed")

    def _controller_connected(self) -> bool:
        if self.controller is None or self._closed:
            return False
        connected = getattr(self.controller, "connected", False)
        if callable(connected):
            connected = connected()
        return bool(connected)

    def _require_connected(self) -> Any:
        if not self._controller_connected():
            raise ActuatorDisconnectedError(
                f"MaaFramework controller is disconnected from {self.serial}"
            )
        return self.controller

    def _require_connection(self) -> ActuatorConnectionV1:
        if self.connection is None:
            raise ActuatorDisconnectedError("actuator has not connected")
        return self.connection


__all__ = [
    "ActuatorDisconnectedError",
    "ActuatorError",
    "ActuatorResolutionMismatchError",
    "MaaAdbActuator",
]
