from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .adapters import AdapterError, GameAdapter
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


def _port_open(host: str, port: int, timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class AfkUnityExplorerAdapter(GameAdapter):
    """GameAdapter facade over the registered ``unity-playtest`` pipeline.

    It intentionally dispatches through ``omnicompany.core.dispatch`` rather
    than importing Unity QA routers or bridge primitives directly.
    """

    def __init__(
        self,
        store: ObservatoryStore,
        *,
        dispatcher: Callable[..., Awaitable[Any]] | None = None,
        bridge_probe: Callable[[str, int], bool] = _port_open,
    ) -> None:
        self.store = store
        self.dispatcher = dispatcher
        self.bridge_probe = bridge_probe
        self.target: TargetInfo | None = None
        self.bridge_port = 18820

    def connect(self, target: str) -> TargetInfo:
        parsed = urlparse(target)
        query = parse_qs(parsed.query)
        self.bridge_port = int(query.get("bridge_port", [18820])[0])
        online = self.bridge_probe("127.0.0.1", self.bridge_port)
        self.target = TargetInfo(
            id=target,
            kind="unity",
            label="AFK Journey Unity QA / Explorer",
            status="online" if online else "offline",
            capabilities=[
                "pixel", "ui_tree", "source_probe", "runtime_state", "trace",
                "checkpoint", "failure_recovery", "objective_checker",
            ],
            metadata={
                "pipeline": "unity-playtest",
                "bridge_port": self.bridge_port,
                "entry": "omnicompany.core.dispatch.dispatch",
            },
        )
        return self.target

    def observe(self) -> ObservationBundle:
        raise AdapterError("Unity observation is emitted by the registered unity-playtest trace")

    def reset(self, snapshot: str | None = None) -> dict[str, Any]:
        raise AdapterError("AFK reset must use an approved Unity QA account snapshot")

    def checkpoint(self) -> str:
        raise AdapterError("checkpoint ids are emitted by Unity QA during a playtest run")

    def restore(self, snapshot: str) -> dict[str, Any]:
        raise AdapterError("restore must be executed by the registered Unity QA pipeline")

    def act(self, action: NormalizedAction) -> dict[str, Any]:
        raise AdapterError("individual Unity actions stay inside the registered unity-playtest pipeline")

    @staticmethod
    def _actuals(result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {}
        for key in ("objective_results", "actuals", "checks"):
            value = result.get(key)
            if isinstance(value, dict):
                return value
            if isinstance(value, list):
                return {
                    str(item.get("id")): item.get("actual")
                    for item in value
                    if isinstance(item, dict) and item.get("id")
                }
        for value in result.values():
            nested = AfkUnityExplorerAdapter._actuals(value)
            if nested:
                return nested
        return {}

    def evaluate(self, task: BenchmarkTask) -> RunResult:
        if not self.target:
            raise AdapterError("connect a Unity target before evaluate")
        if self.target.status != "online":
            return RunResult(
                id=f"run.unity-playtest.{uuid.uuid4().hex}",
                adapter="unity-playtest",
                target_id=self.target.id,
                task_id=task.id,
                status="stopped",
                ended_at=utc_now(),
                checks=[item.model_copy() for item in task.checks],
                error=f"AgentBridge is not reachable on 127.0.0.1:{self.bridge_port}",
            )
        if self.dispatcher is None:
            from omnicompany.core.dispatch import dispatch

            dispatcher = dispatch
        else:
            dispatcher = self.dispatcher
        input_dict = {
            "target_state": task.metadata.get("target_state", task.goal),
            "task": task.metadata.get("task_prompt", task.title),
            "bridge_port": str(self.bridge_port),
            "benchmark_task": task.model_dump(mode="json"),
        }
        try:
            raw = asyncio.run(dispatcher("unity-playtest", input_dict, max_steps=task.metadata.get("max_steps")))
            error = None
        except Exception as exc:  # preserve a structured stopped run and upstream diagnostics
            raw = {"dispatch_error": repr(exc)}
            error = str(exc)
        encoded = json.dumps(raw, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        artifact_id = f"art.unity-result.{hashlib.sha256(encoded).hexdigest()[:16]}"
        artifact_path = self.store.artifact_root / f"{artifact_id}.json"
        artifact_path.write_bytes(encoded)
        artifact = ArtifactRef(
            id=artifact_id,
            kind="runtime_state",
            path=str(artifact_path),
            sha256=hashlib.sha256(encoded).hexdigest(),
            media_type="application/json",
            metadata={"pipeline": "unity-playtest", "public": False},
        )
        actuals = self._actuals(raw)
        checks = [
            item.model_copy(
                update={
                    "actual": actuals.get(item.id),
                    "passed": actuals.get(item.id) == item.expected if item.id in actuals else None,
                }
            )
            for item in task.checks
        ]
        complete = bool(checks) and all(item.passed is True for item in checks)
        run = RunResult(
            id=f"run.unity-playtest.{uuid.uuid4().hex}",
            adapter="unity-playtest",
            target_id=self.target.id,
            task_id=task.id,
            status="passed" if complete else "stopped",
            ended_at=utc_now(),
            checks=checks,
            artifact_ids=[artifact.id],
            error=error or (None if complete else "pipeline result did not satisfy every objective checker"),
        )
        artifact.run_id = run.id
        self.store.save_artifact(artifact)
        self.store.save_run(run)
        self.store.append_event(run.id, "unity_dispatch_result", {"artifact_id": artifact.id})
        return run


class MinecraftVisualAdapter(GameAdapter):
    """Read-only visual/runtime adapter for the existing voxelcraft console."""

    def __init__(self, store: ObservatoryStore, *, timeout: float = 5.0) -> None:
        self.store = store
        self.timeout = timeout
        self.base_url = ""
        self.target: TargetInfo | None = None

    def _url(self, path: str, query: dict[str, str] | None = None) -> str:
        suffix = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url}{suffix}" + (f"?{urlencode(query)}" if query else "")

    def _bytes(self, path: str, query: dict[str, str] | None = None) -> bytes:
        request = Request(self._url(path, query), headers={"User-Agent": "game-observatory/0.2"})
        with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - loopback/operator target
            return response.read()

    def _json(self, path: str) -> Any:
        return json.loads(self._bytes(path).decode("utf-8"))

    def connect(self, target: str) -> TargetInfo:
        parsed = urlparse(target)
        if parsed.scheme == "minecraft":
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 8332
            self.base_url = f"http://{host}:{port}"
        elif parsed.scheme in {"http", "https"}:
            self.base_url = target.rstrip("/")
        else:
            raise AdapterError("Minecraft target must be minecraft://host:port or http(s)://host:port")
        try:
            status = self._json("/api/status")
        except Exception as exc:
            raise AdapterError(f"voxelcraft console is unavailable: {exc}") from exc
        online = bool(status.get("server", {}).get("online"))
        self.target = TargetInfo(
            id=target,
            kind="minecraft",
            label="voxelcraft Minecraft visual runtime",
            status="online" if online else "offline",
            capabilities=["pixel", "runtime_state", "world_snapshot", "trace", "source_probe"],
            metadata={"console_url": self.base_url, "status": status},
        )
        return self.target

    def observe(self) -> ObservationBundle:
        if not self.target:
            raise AdapterError("connect a Minecraft target before observe")
        status = self._json("/api/status")
        listing = self._json("/api/screenshots/list")
        if isinstance(listing, dict):
            listing = listing.get("value") or listing.get("screenshots") or []
        if not listing:
            raise AdapterError("voxelcraft console has no screenshots")
        name = str(listing[0]["name"])
        image = self._bytes("/api/screenshots/file", {"name": name})
        if not image.startswith(b"\x89PNG"):
            raise AdapterError("latest voxelcraft screenshot is not PNG")
        stamp = int(time.time() * 1000)
        run_id = f"run.minecraft-observe.{stamp}.{uuid.uuid4().hex[:8]}"
        frame_id = f"art.minecraft-frame.{hashlib.sha256(image).hexdigest()[:16]}"
        frame_path = self.store.artifact_root / f"{frame_id}.png"
        frame_path.write_bytes(image)
        frame = ArtifactRef(
            id=frame_id,
            kind="screenshot",
            path=str(frame_path),
            sha256=hashlib.sha256(image).hexdigest(),
            run_id=run_id,
            locator=name,
            media_type="image/png",
            metadata={"console_url": self.base_url, "public": False},
        )
        state_bytes = json.dumps(status, ensure_ascii=False, indent=2).encode("utf-8")
        state_id = f"art.minecraft-state.{hashlib.sha256(state_bytes).hexdigest()[:16]}"
        state_path = self.store.artifact_root / f"{state_id}.json"
        state_path.write_bytes(state_bytes)
        state = ArtifactRef(
            id=state_id,
            kind="runtime_state",
            path=str(state_path),
            sha256=hashlib.sha256(state_bytes).hexdigest(),
            run_id=run_id,
            media_type="application/json",
            metadata={"console_url": self.base_url, "public": False},
        )
        run = RunResult(
            id=run_id,
            adapter="voxelcraft-console-visual",
            target_id=self.target.id,
            status="passed",
            ended_at=utc_now(),
            artifact_ids=[frame.id, state.id],
        )
        self.store.save_artifact(frame)
        self.store.save_artifact(state)
        self.store.save_run(run)
        self.store.append_event(
            run.id, "observe", {"frame": frame.id, "runtime_state": state.id, "screenshot": name}
        )
        return ObservationBundle(
            target_id=self.target.id,
            frame=frame,
            runtime_state=state,
            metadata={"adapter": run.adapter, "screenshot": name},
        )

    def reset(self, snapshot: str | None = None) -> dict[str, Any]:
        raise AdapterError("world reset is an explicit benchmark operation and is disabled in read-only mode")

    def checkpoint(self) -> str:
        if not self.target:
            raise AdapterError("connect a Minecraft target before checkpoint")
        status = self._json("/api/status")
        snapshots = status.get("world", {}).get("snapshots") or []
        if not snapshots:
            raise AdapterError("no world snapshot is registered")
        return str(snapshots[0]["hash"])

    def restore(self, snapshot: str) -> dict[str, Any]:
        raise AdapterError("world restore requires an explicit operator-approved benchmark run")

    def act(self, action: NormalizedAction) -> dict[str, Any]:
        if action.type != "wait":
            raise AdapterError("voxelcraft console visual adapter is read-only")
        time.sleep(max(0.0, min(action.seconds, 30.0)))
        return {"ok": True, "waited": max(0.0, min(action.seconds, 30.0))}

    def evaluate(self, task: BenchmarkTask) -> RunResult:
        if not self.target:
            raise AdapterError("connect a Minecraft target before evaluate")
        return RunResult(
            id=f"run.minecraft-visual.{uuid.uuid4().hex}",
            adapter="voxelcraft-console-visual",
            target_id=self.target.id,
            task_id=task.id,
            status="stopped",
            ended_at=utc_now(),
            checks=[ObjectiveCheck(**item.model_dump()) for item in task.checks],
            error="read-only observation adapter does not launch the full benchmark",
        )