from __future__ import annotations

import hashlib
import importlib.util
import os
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .adapters import AdbAdapter, AdapterError, GameAdapter, resolve_adb
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


class AirtestReplayAdapter(GameAdapter):
    """Stable pixel replay backend; high-level planning stays outside Airtest."""

    def __init__(self, store: ObservatoryStore) -> None:
        self.store = store
        self.serial = ""
        self.device: Any | None = None
        self.last_observation: ObservationBundle | None = None

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
        self.last_observation = ObservationBundle(
            target_id=run.target_id,
            frame=artifact,
            metadata={"adapter": "airtest", "serial": self.serial},
        )
        return self.last_observation

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
        if action.type == "tap" and action.x is not None and action.y is not None:
            self.device.touch((action.x, action.y), duration=max(action.duration_ms / 1000, 0.01))
        elif action.type == "swipe" and None not in (action.x, action.y, action.x2, action.y2):
            self.device.swipe(
                (action.x, action.y),
                (action.x2, action.y2),
                duration=max(action.duration_ms / 1000, 0.05),
            )
        elif action.type == "launch" and action.package:
            self.device.start_app(action.package)
        elif action.type == "back":
            self.device.keyevent("BACK")
        elif action.type == "home":
            self.device.keyevent("HOME")
        elif action.type == "wait":
            time.sleep(max(0.0, min(action.seconds, 30.0)))
        else:
            raise AdapterError(f"Airtest replay does not support action: {action.type}")
        result = {
            "ok": True,
            "run_id": f"run.airtest-action.{uuid.uuid4().hex}",
            "action": action.model_dump(mode="json"),
        }
        self.store.append_event(result["run_id"], "action", result)
        return result

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