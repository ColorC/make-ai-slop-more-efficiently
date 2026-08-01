from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from .adapters import AdbAdapter, AdapterError, GameAdapter, resolve_adb
from .gateway import DeviceGateway
from .models import (
    ArtifactRef,
    BenchmarkTask,
    NormalizedAction,
    ObjectiveCheck,
    ObservationBundle,
    RunResult,
    TargetInfo,
    utc_now,
)
from .store import ObservatoryStore


class AgentPluginSpec(BaseModel):
    id: str
    category: str
    title: str
    homepage: str
    status: str
    version: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    blocker: str | None = None


def _gateway_bound_action(
    *,
    store: ObservatoryStore,
    gateway: DeviceGateway | None,
    target_id: str | None,
    lease_token: str | None,
    viewport_width: int,
    viewport_height: int,
    action: NormalizedAction,
    adapter_name: str,
) -> dict[str, Any]:
    """Retire plugin-owned writes while retaining read-only plugin observations."""

    if gateway is None or target_id is None or lease_token is None:
        raise AdapterError(
            f"{adapter_name} direct device mutation is retired; bind a DeviceGateway lease"
        )
    run = gateway.start_evidence_run(
        target_id,
        lease_token,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        environment={"compatibility_adapter": adapter_name},
    )
    step = gateway.record_evidence_step(run.id, lease_token, action)
    manifest = gateway.complete_evidence_run(run.id, lease_token)
    return {
        "ok": step.status == "passed" and manifest.publishable,
        "run_id": run.id,
        "evidence_step_id": step.id,
        "manifest_id": manifest.id,
        "action": action.model_dump(mode="json"),
        "authority": "DeviceGateway",
    }


class AgentPluginRegistry:
    @staticmethod
    def _package_version(name: str) -> str | None:
        try:
            return version(name)
        except PackageNotFoundError:
            return None

    def probe(self) -> list[AgentPluginSpec]:
        airtest_version = self._package_version("airtest")
        maafw_version = self._package_version("maafw")
        codex_executable = shutil.which("codex")
        open_autoglm_home = Path(os.environ.get("OPEN_AUTOGLM_HOME", ""))
        cradle_home = Path(os.environ.get("CRADLE_HOME", ""))
        return [
            AgentPluginSpec(
                id="airtest",
                category="stable_mobile_replay",
                title="Airtest",
                homepage="https://github.com/AirtestProject/Airtest",
                status="runnable" if airtest_version else "not_installed",
                version=airtest_version,
                capabilities=["pixel", "adb", "touch", "swipe", "launch", "report"],
                blocker=None if airtest_version else "Python package airtest is not installed",
            ),
            AgentPluginSpec(
                id="maaframework",
                category="stable_mobile_replay",
                title="MaaFramework",
                homepage="https://github.com/MaaXYZ/MaaFramework",
                status="runnable" if maafw_version else "not_installed",
                version=maafw_version,
                capabilities=["vision_pipeline", "callback", "recovery"],
                blocker=None if maafw_version else "maafw runtime is not installed or configured",
            ),
            AgentPluginSpec(
                id="open-autoglm",
                category="exploratory_mobile_agent",
                title="Open-AutoGLM",
                homepage="https://github.com/zai-org/Open-AutoGLM",
                status=(
                    "configured"
                    if open_autoglm_home.is_dir() and os.environ.get("OPEN_AUTOGLM_MODEL_ENDPOINT")
                    else "blocked"
                ),
                capabilities=["vision_grounding", "planning", "adb", "human_takeover"],
                blocker=(
                    None
                    if open_autoglm_home.is_dir() and os.environ.get("OPEN_AUTOGLM_MODEL_ENDPOINT")
                    else "OPEN_AUTOGLM_HOME and OPEN_AUTOGLM_MODEL_ENDPOINT are not configured"
                ),
            ),
            AgentPluginSpec(
                id="codex-cli-vision",
                category="exploratory_mobile_agent",
                title="Codex CLI vision planner",
                homepage="https://github.com/openai/codex",
                status="configured" if codex_executable else "not_installed",
                capabilities=[
                    "screenshot_planning",
                    "structured_action",
                    "mobile",
                    "pc",
                    "human_takeover",
                ],
                blocker=None if codex_executable else "Codex CLI is not installed",
            ),
            AgentPluginSpec(
                id="cradle",
                category="pc_visual_agent",
                title="Cradle",
                homepage="https://github.com/BAAI-Agents/Cradle",
                status=(
                    "configured"
                    if cradle_home.is_dir() and os.environ.get("CRADLE_MODEL_ENDPOINT")
                    else "blocked"
                ),
                capabilities=["screenshot", "keyboard_mouse", "planning", "skill_library"],
                blocker=(
                    None
                    if cradle_home.is_dir() and os.environ.get("CRADLE_MODEL_ENDPOINT")
                    else "CRADLE_HOME and CRADLE_MODEL_ENDPOINT are not configured"
                ),
            ),
        ]


def _maa_trace_sink(events: list[dict[str, Any]]) -> Any:
    """Create the optional Maa callback sink without importing Maa at module import time."""
    from maa.controller import ControllerEventSink

    class TraceSink(ControllerEventSink):
        def on_raw_notification(
            self,
            _controller: Any,
            message: str,
            details: dict[str, Any],
        ) -> None:
            events.append(
                {
                    "captured_at": utc_now(),
                    "message": message,
                    "details": details,
                }
            )

    return TraceSink()


class MaaFrameworkAdapter(GameAdapter):
    """MaaFramework ADB controller with canonical screenshots and callback traces.

    Maa is used here as a controller and vision-pipeline substrate.  It does not
    become an exploratory AI merely by being installed; planning remains an
    explicit higher-level facility.
    """

    def __init__(
        self,
        store: ObservatoryStore,
        *,
        controller_factory: Callable[[Path, str], Any] | None = None,
        gateway: DeviceGateway | None = None,
        gateway_target_id: str | None = None,
        lease_token: str | None = None,
    ) -> None:
        self.store = store
        self.controller_factory = controller_factory
        self.controller: Any | None = None
        self.sink: Any | None = None
        self.sink_id: int | None = None
        self.serial = ""
        self.framework_version = ""
        self.events: list[dict[str, Any]] = []
        self.last_observation: ObservationBundle | None = None
        self.gateway = gateway
        self.gateway_target_id = gateway_target_id
        self.lease_token = lease_token

    def connect(self, target: str) -> TargetInfo:
        maafw_version = AgentPluginRegistry._package_version("maafw")
        if maafw_version is None and self.controller_factory is None:
            raise AdapterError("MaaFramework is not installed")

        self.serial = target.removeprefix("device://adb/")
        self.events = []
        if self.controller_factory is None:
            from maa import Library
            from maa.controller import AdbController
            from maa.toolkit import Toolkit

            runtime_root = self.store.root / "maafw-runtime"
            runtime_root.mkdir(parents=True, exist_ok=True)
            if not Toolkit.init_option(runtime_root, {"logging": True}):
                raise AdapterError("MaaFramework toolkit initialization failed")
            self.framework_version = Library.version()
            self.controller = AdbController(resolve_adb(), self.serial)
        else:
            self.framework_version = maafw_version or "test-double"
            self.controller = self.controller_factory(resolve_adb(), self.serial)

        self.sink = _maa_trace_sink(self.events) if self.controller_factory is None else None
        if self.sink is not None:
            self.sink_id = self.controller.add_sink(self.sink)
            if self.sink_id is None:
                raise AdapterError("MaaFramework callback sink registration failed")
        job = self.controller.post_connection().wait()
        if not job.succeeded or not self.controller.connected:
            raise AdapterError(f"MaaFramework could not connect to ADB target {self.serial}")
        info = dict(self.controller.info)
        return TargetInfo(
            id=f"device://adb/{self.serial}",
            kind="mumu" if self.serial.startswith("127.0.0.1:") else "adb",
            label=f"MaaFramework controller · {self.serial}",
            status="online",
            capabilities=[
                "pixel",
                "adb",
                "touch",
                "swipe",
                "launch",
                "vision_pipeline",
                "callback_trace",
            ],
            metadata={
                "serial": self.serial,
                "maafw_version": self.framework_version,
                "controller_info": info,
                "callback_sink_id": self.sink_id,
            },
        )

    def _require_controller(self) -> Any:
        if self.controller is None:
            raise AdapterError("connect a MaaFramework target before use")
        return self.controller

    def _trace_artifact(self, run_id: str, *, operation: str) -> ArtifactRef:
        controller = self._require_controller()
        payload = {
            "schema": "game-observatory.maafw-controller-trace.v1",
            "generated_at": utc_now(),
            "operation": operation,
            "framework_version": self.framework_version,
            "serial": self.serial,
            "controller_info": controller.info,
            "events": list(self.events),
        }
        raw = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        artifact = ArtifactRef(
            id=f"art.maafw-trace.{digest[:16]}",
            kind="trace",
            path=str(self.store.artifact_root / f"art.maafw-trace.{digest[:16]}.json"),
            sha256=digest,
            run_id=run_id,
            media_type="application/json",
            metadata={"adapter": "maaframework", "operation": operation, "public": False},
        )
        Path(artifact.path).write_bytes(raw)
        self.store.save_artifact(artifact)
        return artifact

    def observe(self) -> ObservationBundle:
        controller = self._require_controller()
        capture = controller.post_screencap().wait()
        if not capture.succeeded:
            raise AdapterError("MaaFramework screenshot job failed")
        image = capture.get()
        try:
            import cv2

            encoded_ok, encoded = cv2.imencode(".png", image)
        except (ImportError, ValueError, TypeError) as exc:
            raise AdapterError(f"MaaFramework screenshot encoding failed: {exc}") from exc
        if not encoded_ok:
            raise AdapterError("MaaFramework screenshot encoder returned failure")
        raw = encoded.tobytes()
        if not raw.startswith(b"\x89PNG"):
            raise AdapterError("MaaFramework screenshot did not produce PNG")

        run_id = f"run.maafw-observe.{uuid.uuid4().hex}"
        digest = hashlib.sha256(raw).hexdigest()
        frame = ArtifactRef(
            id=f"art.maafw-frame.{digest[:16]}",
            kind="screenshot",
            path=str(self.store.artifact_root / f"art.maafw-frame.{digest[:16]}.png"),
            sha256=digest,
            run_id=run_id,
            media_type="image/png",
            metadata={
                "adapter": "maaframework",
                "serial": self.serial,
                "resolution": list(controller.resolution),
                "public": False,
            },
        )
        Path(frame.path).write_bytes(raw)
        self.store.save_artifact(frame)
        trace = self._trace_artifact(run_id, operation="observe")
        run = RunResult(
            id=run_id,
            adapter="maaframework",
            target_id=f"device://adb/{self.serial}",
            status="passed",
            ended_at=utc_now(),
            artifact_ids=[frame.id, trace.id],
        )
        self.store.save_run(run)
        self.store.append_event(
            run.id,
            "observe",
            {"frame": frame.id, "trace": trace.id, "callback_events": len(self.events)},
        )
        self.last_observation = ObservationBundle(
            target_id=run.target_id,
            frame=frame,
            runtime_state=trace,
            metadata={
                "adapter": "maaframework",
                "serial": self.serial,
                "callback_events": len(self.events),
            },
        )
        return self.last_observation

    def reset(self, snapshot: str | None = None) -> dict[str, Any]:
        if snapshot:
            raise AdapterError("MaaFramework does not own emulator snapshots")
        return self.act(NormalizedAction(type="home"))

    def checkpoint(self) -> str:
        raise AdapterError("MaaFramework does not own emulator checkpoints")

    def restore(self, snapshot: str) -> dict[str, Any]:
        raise AdapterError("MaaFramework does not own emulator snapshot restore")

    def act(self, action: NormalizedAction) -> dict[str, Any]:
        controller = self._require_controller()
        width, height = (int(item) for item in controller.resolution)
        return _gateway_bound_action(
            store=self.store,
            gateway=self.gateway,
            target_id=self.gateway_target_id,
            lease_token=self.lease_token,
            viewport_width=width,
            viewport_height=height,
            action=action,
            adapter_name="MaaFrameworkAdapter",
        )

    def evaluate(self, task: BenchmarkTask) -> RunResult:
        observation = self.observe()
        checks: list[ObjectiveCheck] = []
        for item in task.checks:
            actual: Any = None
            if item.id == "frame_is_png":
                actual = Path(observation.frame.path).read_bytes().startswith(b"\x89PNG")
            elif item.id == "target_connected":
                actual = bool(self._require_controller().connected)
            elif item.id == "callback_trace_saved":
                actual = bool(observation.runtime_state and Path(observation.runtime_state.path).is_file())
            elif item.id == "package_installed":
                package = str(task.metadata.get("package") or "")
                adb = AdbAdapter(self.store)
                adb.connect(self.serial)
                actual = adb.package_installed(package)
            checks.append(
                item.model_copy(
                    update={
                        "actual": actual,
                        "passed": actual == item.expected if actual is not None else None,
                    }
                )
            )
        passed = bool(checks) and all(item.passed is True for item in checks)
        run = RunResult(
            id=f"run.maafw-evaluate.{uuid.uuid4().hex}",
            adapter="maaframework",
            target_id=f"device://adb/{self.serial}",
            task_id=task.id,
            status="passed" if passed else "failed",
            ended_at=utc_now(),
            checks=checks,
            artifact_ids=[
                observation.frame.id,
                *([observation.runtime_state.id] if observation.runtime_state else []),
            ],
            error=None if passed else "MaaFramework stable controller checks failed",
        )
        self.store.save_run(run)
        return run


class AirtestReplayAdapter(GameAdapter):
    """Stable pixel replay backend; high-level planning stays outside Airtest."""

    def __init__(
        self,
        store: ObservatoryStore,
        *,
        gateway: DeviceGateway | None = None,
        gateway_target_id: str | None = None,
        lease_token: str | None = None,
    ) -> None:
        self.store = store
        self.serial = ""
        self.device: Any | None = None
        self.last_observation: ObservationBundle | None = None
        self.frame_size: tuple[int, int] | None = None
        self.device_size: tuple[int, int] | None = None
        self.gateway = gateway
        self.gateway_target_id = gateway_target_id
        self.lease_token = lease_token

    def connect(self, target: str) -> TargetInfo:
        if importlib.util.find_spec("airtest") is None:
            raise AdapterError("Airtest is not installed")
        from airtest.core.android.android import Android

        self.serial = target.removeprefix("device://adb/")
        self.device = Android(
            serialno=self.serial,
            cap_method="ADBCAP",
            touch_method="ADBTOUCH",
            adb_path=str(resolve_adb()),
        )
        state = str(self.device.adb.get_status())
        return TargetInfo(
            id=f"device://adb/{self.serial}",
            kind="mumu" if self.serial.startswith("127.0.0.1:") else "adb",
            label=f"Airtest replay · {self.serial}",
            status="online" if state == "device" else "offline",
            capabilities=["pixel", "touch", "swipe", "launch", "stable_replay"],
            metadata={"serial": self.serial, "airtest_version": version("airtest")},
        )

    def observe(self) -> ObservationBundle:
        if self.device is None:
            raise AdapterError("connect an Airtest target before observe")
        run_id = f"run.airtest-observe.{uuid.uuid4().hex}"
        artifact_id = f"art.airtest-frame.{uuid.uuid4().hex[:16]}"
        path = self.store.artifact_root / f"{artifact_id}.png"
        self.device.snapshot(filename=str(path), quality=90, max_size=1080)
        raw = path.read_bytes()
        if not raw.startswith(b"\x89PNG"):
            raise AdapterError("Airtest snapshot did not produce PNG")
        artifact = ArtifactRef(
            id=artifact_id,
            kind="screenshot",
            path=str(path),
            sha256=hashlib.sha256(raw).hexdigest(),
            run_id=run_id,
            media_type="image/png",
            metadata={"adapter": "airtest", "serial": self.serial, "public": False},
        )
        run = RunResult(
            id=run_id,
            adapter="airtest",
            target_id=f"device://adb/{self.serial}",
            status="passed",
            ended_at=utc_now(),
            artifact_ids=[artifact.id],
        )
        self.store.save_artifact(artifact)
        self.store.save_run(run)
        self.store.append_event(run.id, "observe", {"artifact_id": artifact.id})
        try:
            import cv2

            image = cv2.imread(str(path))
            if image is not None:
                height, width = image.shape[:2]
                self.frame_size = (int(width), int(height))
        except ImportError:
            self.frame_size = None
        raw_resolution = self.device.get_current_resolution()
        self.device_size = (int(raw_resolution[0]), int(raw_resolution[1]))
        self.last_observation = ObservationBundle(
            target_id=run.target_id,
            frame=artifact,
            metadata={
                "adapter": "airtest",
                "serial": self.serial,
                "frame_size": list(self.frame_size) if self.frame_size else None,
                "device_size": list(self.device_size),
            },
        )
        return self.last_observation

    def _device_point(self, x: int, y: int) -> tuple[int, int]:
        if not self.frame_size or not self.device_size:
            return x, y
        frame_width, frame_height = self.frame_size
        device_width, device_height = self.device_size
        return (
            round(x * device_width / frame_width),
            round(y * device_height / frame_height),
        )

    def reset(self, snapshot: str | None = None) -> dict[str, Any]:
        if snapshot:
            raise AdapterError("Airtest does not own emulator snapshots")
        return self.act(NormalizedAction(type="home"))

    def checkpoint(self) -> str:
        raise AdapterError("Airtest does not expose snapshot checkpoints")

    def restore(self, snapshot: str) -> dict[str, Any]:
        raise AdapterError("Airtest does not expose snapshot restore")

    def act(self, action: NormalizedAction) -> dict[str, Any]:
        if self.device is None:
            raise AdapterError("connect an Airtest target before act")
        width, height = self.device_size or self.device.get_current_resolution()
        return _gateway_bound_action(
            store=self.store,
            gateway=self.gateway,
            target_id=self.gateway_target_id,
            lease_token=self.lease_token,
            viewport_width=int(width),
            viewport_height=int(height),
            action=action,
            adapter_name="AirtestReplayAdapter",
        )

    def evaluate(self, task: BenchmarkTask) -> RunResult:
        observation = self.observe()
        checks = []
        for item in task.checks:
            actual: Any = None
            if item.id == "frame_is_png":
                actual = Path(observation.frame.path).read_bytes().startswith(b"\x89PNG")
            elif item.id == "target_connected":
                actual = True
            elif item.id == "package_installed":
                package = str(task.metadata.get("package") or "")
                adb = AdbAdapter(self.store)
                adb.connect(self.serial)
                actual = adb.package_installed(package)
            checks.append(
                item.model_copy(
                    update={"actual": actual, "passed": actual == item.expected if actual is not None else None}
                )
            )
        passed = bool(checks) and all(item.passed is True for item in checks)
        run = RunResult(
            id=f"run.airtest-evaluate.{uuid.uuid4().hex}",
            adapter="airtest",
            target_id=f"device://adb/{self.serial}",
            task_id=task.id,
            status="passed" if passed else "failed",
            ended_at=utc_now(),
            checks=checks,
            artifact_ids=[observation.frame.id],
            error=None if passed else "Airtest stable replay checks failed",
        )
        self.store.save_run(run)
        return run
