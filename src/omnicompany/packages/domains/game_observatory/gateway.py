from __future__ import annotations

import abc
import hashlib
import json
import re
import secrets
import socket
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from omnicompany.core.config import omni_workspace_root

from .adapters import AdbAdapter, AdapterError, resolve_adb
from .subprocess_policy import headless_process_kwargs
from .evidence import (
    EvidenceRecorder,
    EvidenceRecorderError,
    perceptual_frame_distance,
    regional_perceptual_frame_distance,
    regional_structural_frame_distance,
)
from .models import (
    CaptureSession,
    DeviceLease,
    EvidenceDynamicSceneProfile,
    EvidenceRun,
    EvidenceRunManifest,
    EvidenceStep,
    EvidenceTerminalCondition,
    GatewayControl,
    NormalizedAction,
    SourcePixelRect,
    TargetInfo,
    TargetRecord,
    utc_now,
)
from .store import ObservatoryStore


class GatewayError(RuntimeError):
    pass


class PreReservedAIPlayerActionV1(BaseModel):
    """Exact autonomous action reservation carried into one evidence run."""

    model_config = ConfigDict(extra="forbid")

    capsule_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    request_sha256: str = Field(min_length=64, max_length=64)
    action: NormalizedAction


class LeaseConflict(GatewayError):
    pass


class EmergencyStopActive(GatewayError):
    pass


class RateLimitExceeded(GatewayError):
    pass


class TargetProvider(abc.ABC):
    name: str

    @abc.abstractmethod
    def discover(self) -> list[TargetRecord]:
        raise NotImplementedError


@dataclass(frozen=True)
class MaaAndroidRuntime:
    """Maa controller, Resource, and Tasker opened under DeviceGateway authority."""

    target_id: str
    serial: str
    framework_version: str
    controller: Any
    resource: Any
    tasker: Any
    resource_paths: tuple[str, ...]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tcp_open(host: str, port: int, timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class MumuCli:
    """Narrow wrapper around MuMu's installed, documented local CLI surface."""

    def __init__(self, executable: Path | None = None) -> None:
        self.executable = executable or Path("C:/Program Files/Netease/MuMu/nx_main/mumu-cli.exe")

    def available(self) -> bool:
        return self.executable.is_file()

    def _run(self, *args: str, timeout: float = 45.0) -> str:
        if not self.available():
            raise GatewayError(f"MuMu CLI not found: {self.executable}")
        proc = subprocess.run(
            [str(self.executable), *args],
            capture_output=True,
            timeout=timeout,
            check=False,
            **headless_process_kwargs(),
        )
        output = proc.stdout.decode("utf-8", "replace").strip()
        error = proc.stderr.decode("utf-8", "replace").strip()
        if proc.returncode != 0:
            raise GatewayError(error or output or f"MuMu CLI failed: {' '.join(args)}")
        return output

    def info(self, vmindex: str = "all") -> dict[str, dict[str, Any]]:
        if not vmindex.replace(",", "").isdigit() and vmindex != "all":
            raise GatewayError("MuMu vmindex must be numeric, comma-separated, or all")
        payload = json.loads(self._run("info", "--vmindex", vmindex))
        if not isinstance(payload, dict):
            raise GatewayError("MuMu info returned an invalid payload")
        return payload

    @staticmethod
    def _vmindex(value: str, *, allow_primary: bool = True) -> str:
        if not value.isdigit():
            raise GatewayError("MuMu vmindex must be numeric")
        if not allow_primary and value == "0":
            raise GatewayError("refusing to delete the primary MuMu instance")
        return value

    @staticmethod
    def _json_object(output: str, operation: str) -> dict[str, Any]:
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise GatewayError(f"MuMu {operation} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise GatewayError(f"MuMu {operation} returned an invalid payload")
        return payload

    @classmethod
    def _assert_success(cls, output: str, operation: str) -> dict[str, Any]:
        payload = cls._json_object(output, operation)
        failures = {
            key: value
            for key, value in payload.items()
            if isinstance(value, dict) and int(value.get("errcode", 0)) != 0
        }
        if failures:
            raise GatewayError(f"MuMu {operation} failed: {json.dumps(failures, ensure_ascii=False)}")
        return payload

    def adb_connect(self, vmindex: str) -> dict[str, Any]:
        vmindex = self._vmindex(vmindex)
        payload = self._json_object(
            self._run("adb", "--vmindex", vmindex, "--cmd", "connect"), "adb connect"
        )
        host = str(payload.get("adb_host") or "")
        port = int(payload.get("adb_port") or 0)
        if not host or not (1 <= port <= 65535):
            raise GatewayError("MuMu adb connect did not return a usable endpoint")
        return {**payload, "serial": f"{host}:{port}", "vmindex": vmindex}

    def clone(self, vmindex: str, *, number: int = 1) -> dict[str, Any]:
        vmindex = self._vmindex(vmindex)
        if not 1 <= number <= 8:
            raise GatewayError("MuMu clone number must be between 1 and 8")
        payload = self._assert_success(
            self._run("clone", "--vmindex", vmindex, "--number", str(number), timeout=300.0),
            "clone",
        )
        return {"ok": True, "source_vmindex": vmindex, "instances": payload}

    def export_snapshot(
        self,
        vmindex: str,
        directory: Path,
        *,
        name: str,
        compressed: bool = True,
    ) -> dict[str, Any]:
        vmindex = self._vmindex(vmindex)
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name):
            raise GatewayError("MuMu snapshot name contains unsupported characters")
        directory.mkdir(parents=True, exist_ok=True)
        existing = sorted(directory.glob(f"{name}*.mumudata"))
        if existing:
            raise GatewayError(f"MuMu snapshot name already exists: {existing[0]}")
        args = ["export", "--vmindex", vmindex, "--dir", str(directory), "--name", name]
        if compressed:
            args.append("--zip")
        payload: dict[str, Any] = {}
        cli_error: str | None = None
        try:
            payload = self._assert_success(self._run(*args, timeout=1800.0), "export")
        except GatewayError as exc:
            # MuMu 4.1 can lose its main-process RPC while the 7za child keeps
            # writing a valid archive. The archive gate below, not RPC alone,
            # decides whether the snapshot is usable.
            cli_error = str(exc)
        snapshots = sorted(directory.glob(f"{name}*.mumudata"), key=lambda path: path.stat().st_mtime)
        if not snapshots:
            raise GatewayError(cli_error or "MuMu export produced no .mumudata file")
        path = snapshots[-1].resolve()
        archive_check = self.wait_for_snapshot_archive(path, timeout=1800.0)
        return {
            "ok": True,
            "vmindex": vmindex,
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
            "compressed": compressed,
            "cli": payload,
            "cli_error": cli_error,
            "archive_check": archive_check,
        }

    def wait_for_snapshot_archive(self, path: Path, *, timeout: float = 1800.0) -> dict[str, Any]:
        root = self.executable.parent.parent
        tools = sorted(root.glob("nx_device/*/shell/7za.exe"), reverse=True)
        if not tools:
            raise GatewayError("MuMu 7za archive verifier was not found")
        deadline = time.monotonic() + timeout
        last_size = -1
        stable = 0
        last_error = "archive is still being written"
        while time.monotonic() < deadline:
            if not path.is_file():
                time.sleep(2)
                continue
            size = path.stat().st_size
            stable = stable + 1 if size == last_size else 0
            last_size = size
            if stable >= 3:
                proc = subprocess.run(
                    [str(tools[0]), "t", str(path)],
                    capture_output=True,
                    timeout=min(900.0, max(30.0, deadline - time.monotonic())),
                    check=False,
                    **headless_process_kwargs(),
                )
                output = (
                    proc.stdout.decode("utf-8", "replace")
                    + "\n"
                    + proc.stderr.decode("utf-8", "replace")
                ).strip()
                if proc.returncode == 0:
                    return {
                        "ok": True,
                        "tool": str(tools[0]),
                        "bytes": size,
                        "summary": output[-1000:],
                    }
                last_error = output[-1000:] or f"7za returned {proc.returncode}"
            time.sleep(2)
        raise GatewayError(f"MuMu snapshot archive did not become valid: {last_error}")

    def import_snapshot(self, path: Path, *, number: int = 1) -> dict[str, Any]:
        if not path.is_file() or path.suffix.lower() != ".mumudata":
            raise GatewayError("MuMu import requires an existing .mumudata file")
        if not 1 <= number <= 8:
            raise GatewayError("MuMu import number must be between 1 and 8")
        payload = self._assert_success(
            self._run("import", "--path", str(path), "--number", str(number), timeout=1800.0),
            "import",
        )
        return {"ok": True, "path": str(path.resolve()), "instances": payload}

    def delete(self, vmindex: str) -> dict[str, Any]:
        vmindex = self._vmindex(vmindex, allow_primary=False)
        payload = self._assert_success(
            self._run("delete", "--vmindex", vmindex, timeout=300.0), "delete"
        )
        return {"ok": True, "vmindex": vmindex, "cli": payload}

    def control(self, vmindex: str, operation: str) -> dict[str, Any]:
        vmindex = self._vmindex(vmindex)
        if operation not in {"launch", "shutdown", "restart", "show_window", "hide_window"}:
            raise GatewayError(f"unsupported MuMu lifecycle operation: {operation}")
        output = self._run("control", "--vmindex", vmindex, operation, timeout=120.0)
        return {"ok": True, "vmindex": vmindex, "operation": operation, "output": output}


class AdbTargetProvider(TargetProvider):
    name = "adb"

    def __init__(self, *, include_mumu: bool = False) -> None:
        self.include_mumu = include_mumu

    def discover(self) -> list[TargetRecord]:
        records: list[TargetRecord] = []
        for target in AdbAdapter.discover():
            if target.kind == "mumu" and not self.include_mumu:
                continue
            records.append(
                TargetRecord(
                    id=target.id,
                    provider=self.name,
                    endpoint=target.id,
                    kind=target.kind,
                    label=target.label,
                    status=target.status,
                    capabilities=target.capabilities,
                    metadata=target.metadata,
                    last_seen_at=_now().isoformat() if target.status == "online" else None,
                )
            )
        return records


class MumuTargetProvider(TargetProvider):
    name = "mumu-adb"

    def __init__(
        self,
        install_roots: list[Path] | None = None,
        *,
        cli: MumuCli | None = None,
    ) -> None:
        self.install_roots = install_roots or [
            Path("C:/Program Files/Netease/MuMu"),
            Path("C:/Program Files/MuMuVMMVbox"),
        ]
        self.cli = cli or MumuCli()

    def discover(self) -> list[TargetRecord]:
        installs = [str(path.resolve()) for path in self.install_roots if path.exists()]
        cli = self.cli
        players: dict[str, dict[str, Any]] = {}
        if cli.available():
            try:
                players = cli.info()
            except (GatewayError, json.JSONDecodeError):
                players = {}
        endpoints: dict[str, dict[str, Any]] = {}
        for vmindex, player in players.items():
            if not player.get("is_android_started"):
                continue
            try:
                endpoints[vmindex] = cli.adb_connect(vmindex)
            except GatewayError:
                endpoints[vmindex] = {}
        discovered = {
            str(item.metadata.get("serial") or ""): item
            for item in AdbAdapter.discover()
            if item.kind == "mumu"
        }
        records: list[TargetRecord] = []
        for vmindex, player in players.items():
            endpoint = endpoints.get(vmindex, {})
            serial = str(endpoint.get("serial") or "")
            target = discovered.get(serial)
            online = bool(target and serial)
            capabilities = target.capabilities if target else []
            records.append(
                TargetRecord(
                    id=f"device://mumu/{vmindex}",
                    provider=self.name,
                    endpoint=serial or f"mumu://{vmindex}",
                    kind="mumu",
                    label=f"MuMu {vmindex} · {player.get('name') or 'Android'}",
                    status="online" if online else "offline",
                    capabilities=sorted(
                        {
                            *capabilities,
                            "lifecycle",
                            "multi_instance",
                            "snapshot_export",
                            "snapshot_import",
                        }
                    ),
                    metadata={
                        **(target.metadata if target else {}),
                        "serial": serial or None,
                        "install_roots": installs,
                        "mumu_cli": str(cli.executable) if cli.available() else None,
                        "vmindex": vmindex,
                        "player_info": player,
                        "adb_endpoint": endpoint,
                    },
                    last_seen_at=_now().isoformat() if online else None,
                )
            )
        if not players:
            for target in discovered.values():
                records.append(
                    TargetRecord(
                        id=target.id,
                        provider=self.name,
                        endpoint=target.id,
                        kind="mumu",
                        label=target.label,
                        status=target.status,
                        capabilities=target.capabilities,
                        metadata={**target.metadata, "install_roots": installs},
                        last_seen_at=_now().isoformat() if target.status == "online" else None,
                    )
                )
        return records


class LofaRemoteAdbProvider(TargetProvider):
    name = "lofa-remote-adb"

    def __init__(
        self,
        registry_path: Path | None = None,
        *,
        probe: Callable[[str, int], bool] = _tcp_open,
    ) -> None:
        self.registry_path = registry_path or (omni_workspace_root() / "data" / "runtime" / "lofa_device.json")
        self.probe = probe

    def _record(self) -> dict[str, Any] | None:
        if not self.registry_path.is_file():
            return None
        try:
            value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def discover(self) -> list[TargetRecord]:
        record = self._record()
        if not record:
            return []
        host = str(record.get("ip") or "").strip()
        port = int(record.get("adb_port") or 5555)
        if not host or not 1 <= port <= 65535:
            return []
        endpoint = f"{host}:{port}"
        online = self.probe(host, port)
        return [
            TargetRecord(
                id=f"device://adb-tcp/{endpoint}",
                provider=self.name,
                endpoint=endpoint,
                kind="adb",
                label=f"LOFA remote Android · {endpoint}",
                status="online" if online else "offline",
                capabilities=["pixel", "touch", "install", "ui_tree", "remote"] if online else ["remote"],
                metadata={
                    "host": host,
                    "port": port,
                    "lofa_last_seen": record.get("last_seen"),
                    "direct_adb_reachable": online,
                },
                last_seen_at=(
                    datetime.fromtimestamp(float(record["last_seen"]), timezone.utc).isoformat()
                    if record.get("last_seen")
                    else None
                ),
            )
        ]


class LofaOutboundProvider(TargetProvider):
    """One-way-network control already exposed by the LOFA app polling channel."""

    name = "lofa-outbound"

    def __init__(self, registry_path: Path | None = None, *, freshness_seconds: int = 900) -> None:
        self.registry_path = registry_path or (omni_workspace_root() / "data" / "runtime" / "lofa_device.json")
        self.freshness_seconds = freshness_seconds

    def discover(self) -> list[TargetRecord]:
        if not self.registry_path.is_file():
            return []
        try:
            value = json.loads(self.registry_path.read_text(encoding="utf-8"))
            host = str(value.get("ip") or "").strip()
            seen = float(value.get("last_seen") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []
        if not host:
            return []
        age = max(0.0, _now().timestamp() - seen) if seen else float("inf")
        return [
            TargetRecord(
                id=f"device://lofa-outbound/{host}",
                provider=self.name,
                endpoint=f"/api/android/commands/*?device_ip={host}",
                kind="remote_outbound",
                label=f"LOFA outbound channel · {host}",
                status="online" if age <= self.freshness_seconds else "offline",
                capabilities=["command_queue", "result_callback", "logs", "ota", "remote"],
                metadata={"host": host, "last_seen_epoch": seen, "age_seconds": age},
                last_seen_at=(datetime.fromtimestamp(seen, timezone.utc).isoformat() if seen else None),
            )
        ]


class DeviceGateway:
    _PRE_RESERVED_ACTION_KEY = "ai_player_pre_reserved_action"

    @staticmethod
    def _expanded_source_region(
        bounds: SourcePixelRect,
        *,
        padding: int,
        viewport_width: int,
        viewport_height: int,
    ) -> SourcePixelRect:
        left = max(0, bounds.x - padding)
        top = max(0, bounds.y - padding)
        right = min(viewport_width, bounds.x + bounds.width + padding)
        bottom = min(viewport_height, bounds.y + bounds.height + padding)
        return SourcePixelRect(
            x=left,
            y=top,
            width=right - left,
            height=bottom - top,
        )

    def __init__(
        self,
        store: ObservatoryStore,
        providers: list[TargetProvider] | None = None,
        *,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.store = store
        self.providers = providers or [
            MumuTargetProvider(),
            AdbTargetProvider(),
            LofaRemoteAdbProvider(),
            LofaOutboundProvider(),
        ]
        self.clock = clock
        self._adb_adapter_lock = threading.RLock()
        self._adb_adapters: dict[tuple[str, str], AdbAdapter] = {}

    def control(self, target_id: str) -> GatewayControl:
        return self.store.get_gateway_control(target_id) or GatewayControl(target_id=target_id)

    def configure_rate_limit(
        self,
        target_id: str,
        *,
        max_actions_per_minute: int,
        min_action_interval_ms: int,
        actor: str,
    ) -> GatewayControl:
        if not self.store.get_target(target_id):
            raise GatewayError(f"unknown target: {target_id}")
        current = self.control(target_id)
        updated = current.model_copy(
            update={
                "max_actions_per_minute": max(1, min(max_actions_per_minute, 600)),
                "min_action_interval_ms": max(0, min(min_action_interval_ms, 60_000)),
                "actor": actor,
                "updated_at": self.clock().isoformat(),
            }
        )
        self.store.save_gateway_control(updated)
        self.store.append_gateway_event(
            "rate_limit_configured",
            target_id,
            {
                "actor": actor,
                "max_actions_per_minute": updated.max_actions_per_minute,
                "min_action_interval_ms": updated.min_action_interval_ms,
            },
        )
        return updated

    def emergency_stop(self, target_id: str, *, reason: str, actor: str) -> GatewayControl:
        if not reason.strip() or not actor.strip():
            raise GatewayError("emergency stop requires reason and actor")
        current = self.control(target_id)
        stopped = current.model_copy(
            update={
                "emergency_stopped": True,
                "reason": reason.strip(),
                "actor": actor.strip(),
                "updated_at": self.clock().isoformat(),
            }
        )
        self.store.save_gateway_control(stopped)
        for lease in self.store.list_leases(target_id):
            if lease.status == "active":
                self.store.save_lease(
                    lease.model_copy(
                        update={"status": "released", "released_at": self.clock().isoformat()}
                    )
                )
        self.store.append_gateway_event(
            "emergency_stop", target_id, {"reason": stopped.reason, "actor": stopped.actor}
        )
        return stopped

    def clear_emergency_stop(self, target_id: str, *, actor: str) -> GatewayControl:
        if not actor.strip():
            raise GatewayError("actor is required")
        current = self.control(target_id)
        cleared = current.model_copy(
            update={
                "emergency_stopped": False,
                "reason": None,
                "actor": actor.strip(),
                "updated_at": self.clock().isoformat(),
            }
        )
        self.store.save_gateway_control(cleared)
        self.store.append_gateway_event("emergency_stop_cleared", target_id, {"actor": actor})
        return cleared

    def assert_operational(self, target_id: str) -> GatewayControl:
        control = self.control(target_id)
        if control.emergency_stopped:
            raise EmergencyStopActive(
                f"target is emergency-stopped: {control.reason or 'no reason recorded'}"
            )
        return control

    def authorize_mutation(self, target_id: str, token: str) -> DeviceLease:
        lease = self.validate(target_id, token)
        control = self.assert_operational(target_id)
        now = self.clock()
        recent = self.store.recent_gateway_events(
            target_id,
            "mutation_dispatched",
            (now - timedelta(seconds=60)).isoformat(),
        )
        if len(recent) >= control.max_actions_per_minute:
            raise RateLimitExceeded(
                f"target action rate exceeded {control.max_actions_per_minute}/minute"
            )
        if recent and control.min_action_interval_ms:
            last = datetime.fromisoformat(recent[0]["timestamp"])
            elapsed_ms = (now - last).total_seconds() * 1000
            if elapsed_ms < control.min_action_interval_ms:
                raise RateLimitExceeded(
                    f"target action interval is {elapsed_ms:.0f}ms; minimum is "
                    f"{control.min_action_interval_ms}ms"
                )
        return lease

    def record_mutation(
        self,
        target_id: str,
        lease: DeviceLease,
        operation: str,
        result: dict[str, Any],
    ) -> None:
        self.store.append_gateway_event(
            "mutation_dispatched",
            target_id,
            {
                "lease_id": lease.id,
                "operation": operation,
                "run_id": result.get("run_id"),
                "evidence_run_id": result.get("evidence_run_id"),
                "evidence_step_id": result.get("evidence_step_id"),
            },
        )

    @staticmethod
    def _explicit_adb_serial(value: Any, *, allow_non_adb_uri: bool) -> str | None:
        text = str(value or "")
        if not text:
            return None
        if text != text.strip():
            raise GatewayError("target record ADB serial contains surrounding whitespace")
        for prefix in ("device://adb/", "device://adb-tcp/"):
            if text.startswith(prefix):
                serial = text.removeprefix(prefix)
                if not serial or "/" in serial:
                    raise GatewayError("target record contains an invalid explicit ADB serial")
                return serial
        if "://" in text:
            if allow_non_adb_uri:
                return None
            raise GatewayError("target record metadata.serial is not an ADB serial")
        if "/" in text:
            raise GatewayError("target record contains an invalid explicit ADB serial")
        return text

    def canonical_adapter_target_ids(self, target_id: str) -> tuple[str, ...]:
        """Return exact action-target IDs declared by one current target record."""

        record = self.store.get_target(target_id)
        if record is None:
            raise GatewayError(f"unknown target: {target_id}")
        if record.id != target_id:
            raise GatewayError("target lookup returned a record with a different ID")
        allowed = [record.id]
        if record.kind not in {"adb", "mumu"}:
            return tuple(allowed)
        metadata_serial = self._explicit_adb_serial(
            record.metadata.get("serial"),
            allow_non_adb_uri=False,
        )
        endpoint_serial = self._explicit_adb_serial(
            record.endpoint,
            allow_non_adb_uri=True,
        )
        if (
            metadata_serial is not None
            and endpoint_serial is not None
            and metadata_serial != endpoint_serial
        ):
            raise GatewayError("target record ADB serial conflicts with endpoint")
        serial = metadata_serial or endpoint_serial
        if serial is not None:
            allowed.append(f"device://adb/{serial}")
        return tuple(dict.fromkeys(allowed))

    @staticmethod
    def _serial(record: TargetRecord) -> str:
        serial = str(record.metadata.get("serial") or "")
        if serial:
            return serial
        return record.endpoint.removeprefix("device://adb/").removeprefix("device://adb-tcp/")

    def _discard_adb_adapter(self, target_id: str, serial: str | None = None) -> None:
        with self._adb_adapter_lock:
            for key in list(self._adb_adapters):
                if key[0] == target_id and (serial is None or key[1] == serial):
                    self._adb_adapters.pop(key, None)

    def _handle_adb_transport_failure(
        self,
        target_id: str,
        serial: str,
        error: Exception,
    ) -> None:
        """Forget a broken transport and make direct bindings recover on the next use."""

        self._discard_adb_adapter(target_id, serial)
        record = self.store.get_target(target_id)
        if (
            record is None
            or record.provider != "adb-direct"
            or self._serial(record) != serial
            or record.status == "offline"
        ):
            return
        offline = record.model_copy(
            update={
                "status": "offline",
                "last_seen_at": None,
                "metadata": {
                    **record.metadata,
                    "direct_adb_reachable": False,
                },
            }
        )
        self.store.upsert_target(offline)
        self.store.append_gateway_event(
            "direct_adb_transport_failed",
            target_id,
            {"serial": serial, "error": str(error)[:500]},
        )

    def _adb_adapter(self, target_id: str) -> AdbAdapter:
        record = self.store.get_target(target_id)
        if not record:
            raise GatewayError(f"unknown target: {target_id}")
        if record.kind not in {"adb", "mumu"}:
            raise GatewayError(f"target does not expose ADB: {target_id}")
        serial = self._serial(record)
        key = (target_id, serial)
        with self._adb_adapter_lock:
            for cached_key in list(self._adb_adapters):
                if cached_key[0] == target_id and cached_key != key:
                    self._adb_adapters.pop(cached_key, None)
            cached = self._adb_adapters.get(key)
            if cached is not None:
                return cached
            try:
                adapter = AdbAdapter(
                    self.store,
                    on_transport_error=lambda error: self._handle_adb_transport_failure(
                        target_id,
                        serial,
                        error,
                    ),
                )
                adapter.connect(serial)
            except (AdapterError, OSError, subprocess.SubprocessError) as exc:
                self._handle_adb_transport_failure(target_id, serial, exc)
                raise GatewayError(
                    f"target ADB connection failed: {target_id}: {exc}"
                ) from exc
            self._adb_adapters[key] = adapter
            return adapter

    def _assert_ai_player_session_binding(
        self,
        environment: dict[str, Any],
    ) -> dict[str, Any] | None:
        session_id = str(environment.get("ai_player_session_id") or "").strip()
        if not session_id:
            return None
        environment_id = str(environment.get("environment_id") or "").strip()
        if not environment_id:
            raise GatewayError(
                "AI 玩家证据运行绑定会话时必须同时声明 environment_id。"
            )
        from .ai_player.session_control import (  # local import avoids gateway cycles
            AIPlayerSessionControl,
            AIPlayerSessionError,
        )
        from .ai_player.store import AIPlayerStore

        try:
            session = AIPlayerSessionControl(
                AIPlayerStore(self.store)
            ).assert_session_can_act(environment_id, session_id)
        except AIPlayerSessionError as exc:
            raise GatewayError(f"{exc.code}: {exc.message}") from exc
        return session.model_dump(mode="json", by_alias=True)

    def _assert_pre_reserved_ai_player_action(
        self,
        environment: dict[str, Any],
        binding: PreReservedAIPlayerActionV1,
        *,
        action: NormalizedAction,
    ) -> dict[str, Any]:
        """Revalidate a reservation without consuming a second action budget."""

        environment_id = str(environment.get("environment_id") or "").strip()
        session_id = str(environment.get("ai_player_session_id") or "").strip()
        if not environment_id or not session_id:
            raise GatewayError(
                "pre-reserved AI player actions require environment_id and "
                "ai_player_session_id"
            )

        from .ai_player.session_control import (
            AIPlayerSessionControl,
            AIPlayerSessionError,
        )
        from .ai_player.store import AIPlayerStore

        player_store = AIPlayerStore(self.store)
        try:
            session = AIPlayerSessionControl(player_store).assert_session_lease_active(
                environment_id,
                session_id,
            )
        except AIPlayerSessionError as exc:
            raise GatewayError(f"{exc.code}: {exc.message}") from exc
        if session.last_capsule_id != binding.capsule_id:
            raise GatewayError(
                "pre_reserved_capsule_mismatch: session last_capsule_id does not match"
            )

        capsule = player_store.get_session_capsule(environment_id, binding.capsule_id)
        if capsule is None:
            raise GatewayError(
                "pre_reserved_capsule_not_found: capsule is absent from the bound environment"
            )
        if capsule.environment_id != environment_id or capsule.session_id != session_id:
            raise GatewayError(
                "pre_reserved_capsule_binding_mismatch: capsule belongs to another "
                "environment or session"
            )
        pending = capsule.pending_action
        if pending is None:
            raise GatewayError("pre_reserved_pending_action_missing: capsule has no pending action")
        if pending.id != binding.command_id:
            raise GatewayError(
                "pre_reserved_command_mismatch: pending action command does not match"
            )
        if pending.request_sha256 != binding.request_sha256:
            raise GatewayError(
                "pre_reserved_request_mismatch: pending action request hash does not match"
            )
        if pending.action != binding.action or pending.action != action:
            raise GatewayError(
                "pre_reserved_action_mismatch: evidence action does not match pending action"
            )
        return session.model_dump(mode="json", by_alias=True)

    def _reserve_ai_player_session_action(
        self,
        environment: dict[str, Any],
        step: EvidenceStep,
    ) -> dict[str, Any] | None:
        session_id = str(environment.get("ai_player_session_id") or "").strip()
        if not session_id:
            return None
        environment_id = str(environment.get("environment_id") or "").strip()
        if not environment_id:
            raise GatewayError(
                "AI 玩家证据运行绑定会话时必须同时声明 environment_id。"
            )
        from .ai_player.session_control import (
            AIPlayerSessionControl,
            AIPlayerSessionError,
        )
        from .ai_player.store import AIPlayerStore

        try:
            session = AIPlayerSessionControl(AIPlayerStore(self.store)).reserve_action(
                environment_id,
                session_id,
                command_id=f"gateway-action-reserve.{step.id}",
                actor="Game Observatory Gateway",
                reason=f"为 EvidenceStep {step.id} 预留一次设备动作预算。",
            )
        except AIPlayerSessionError as exc:
            raise GatewayError(f"{exc.code}: {exc.message}") from exc
        return session.model_dump(mode="json", by_alias=True)

    def install_apk(self, target_id: str, token: str, apk_path: Path) -> dict[str, Any]:
        lease = self.authorize_mutation(target_id, token)
        result = self._adb_adapter(target_id).install_apk(apk_path)
        self.record_mutation(target_id, lease, "install_apk", result)
        return result

    def start_package(self, target_id: str, token: str, package: str) -> dict[str, Any]:
        lease = self.authorize_mutation(target_id, token)
        result = self._adb_adapter(target_id).start_package(package)
        self.record_mutation(target_id, lease, "start_package", result)
        return result

    def force_stop_package(self, target_id: str, token: str, package: str) -> dict[str, Any]:
        lease = self.authorize_mutation(target_id, token)
        result = self._adb_adapter(target_id).force_stop_package(package)
        self.record_mutation(target_id, lease, "force_stop_package", result)
        return result

    def open_maa_android_runtime(
        self,
        target_id: str,
        token: str,
        *,
        pipeline_paths: tuple[Path, ...] = (),
        ocr_model_paths: tuple[Path, ...] = (),
        controller_factory: Callable[[Path, str], Any] | None = None,
        resource_factory: Callable[[], Any] | None = None,
        tasker_factory: Callable[[], Any] | None = None,
    ) -> MaaAndroidRuntime:
        """Open Maa's complete Android substrate without bypassing the gateway lease.

        Connecting and loading recognition resources are read-only.  Any later device
        mutation still has to pass through ``record_evidence_step`` and its action guard.
        """

        lease = self.validate(target_id, token)
        self.assert_operational(target_id)
        target = self.store.get_target(target_id)
        if target is None:
            raise GatewayError(f"unknown target: {target_id}")
        serial = self._serial(target)
        for path in (*pipeline_paths, *ocr_model_paths):
            if not path.exists():
                raise GatewayError(f"Maa resource path is missing: {path}")
        if controller_factory is None:
            from maa import Library
            from maa.controller import AdbController
            from maa.resource import Resource
            from maa.tasker import Tasker
            from maa.toolkit import Toolkit

            runtime_root = self.store.root / "maafw-runtime"
            runtime_root.mkdir(parents=True, exist_ok=True)
            if not Toolkit.init_option(runtime_root, {"logging": True}):
                raise GatewayError("MaaFramework toolkit initialization failed")
            framework_version = str(Library.version())
            controller = AdbController(resolve_adb(), serial)
            resource = Resource()
            tasker = Tasker()
        else:
            framework_version = "test-double"
            controller = controller_factory(resolve_adb(), serial)
            if resource_factory is None or tasker_factory is None:
                raise GatewayError(
                    "Maa test runtime requires controller, resource, and tasker factories"
                )
            resource = resource_factory()
            tasker = tasker_factory()
        connection_job = controller.post_connection().wait()
        if not connection_job.succeeded or not controller.connected:
            raise GatewayError(f"MaaFramework could not connect to ADB target {serial}")
        loaded: list[str] = []
        for path in pipeline_paths:
            job = resource.post_pipeline(path).wait()
            if not job.succeeded:
                raise GatewayError(f"Maa pipeline failed to load: {path}")
            loaded.append(str(path.resolve()))
        for path in ocr_model_paths:
            job = resource.post_ocr_model(path).wait()
            if not job.succeeded:
                raise GatewayError(f"Maa OCR model failed to load: {path}")
            loaded.append(str(path.resolve()))
        if not tasker.bind(resource, controller):
            raise GatewayError("Maa Tasker failed to bind Resource and Controller")
        runtime = MaaAndroidRuntime(
            target_id=target_id,
            serial=serial,
            framework_version=framework_version,
            controller=controller,
            resource=resource,
            tasker=tasker,
            resource_paths=tuple(loaded),
        )
        self.store.append_gateway_event(
            "maa_android_runtime_opened",
            target_id,
            {
                "lease_id": lease.id,
                "serial": serial,
                "framework_version": framework_version,
                "resource_paths": loaded,
                "device_action_count": 0,
            },
        )
        return runtime

    def maa_recognition_service(
        self,
        target_id: str,
        token: str,
        **runtime_options: Any,
    ):
        """Return RecognitionService with Maa as its sole live Android backend."""

        from .recognition_service import MaaRecognitionBackend, RecognitionService

        runtime = self.open_maa_android_runtime(
            target_id,
            token,
            **runtime_options,
        )
        return RecognitionService(
            (
                MaaRecognitionBackend(
                    resource=runtime.resource,
                    tasker=runtime.tasker,
                    version=runtime.framework_version,
                ),
            )
        )

    def start_evidence_run(
        self,
        target_id: str,
        token: str,
        *,
        viewport_width: int,
        viewport_height: int,
        game_id: str | None = None,
        build_scope_id: str | None = None,
        scope_id: str | None = None,
        environment: dict[str, Any] | None = None,
        pre_reserved_action: PreReservedAIPlayerActionV1 | None = None,
    ) -> EvidenceRun:
        lease = self.validate(target_id, token)
        self.assert_operational(target_id)
        target = self.store.get_target(target_id)
        if not target:
            raise GatewayError(f"unknown target: {target_id}")
        bound_environment = {
            "provider": target.provider,
            "endpoint": target.endpoint,
            "capabilities": target.capabilities,
            **dict(environment or {}),
        }
        if self._PRE_RESERVED_ACTION_KEY in bound_environment:
            raise GatewayError(
                f"{self._PRE_RESERVED_ACTION_KEY} is gateway-managed and cannot be supplied "
                "through environment"
            )
        if pre_reserved_action is not None:
            bound_environment[self._PRE_RESERVED_ACTION_KEY] = pre_reserved_action.model_dump(
                mode="json"
            )
            bound_session = self._assert_pre_reserved_ai_player_action(
                bound_environment,
                pre_reserved_action,
                action=pre_reserved_action.action,
            )
        else:
            bound_session = self._assert_ai_player_session_binding(bound_environment)
        recorder = EvidenceRecorder(self.store, None)
        try:
            run = recorder.start_run(
                target_id=target_id,
                adapter=target.kind,
                viewport_width=viewport_width,
                viewport_height=viewport_height,
                game_id=game_id,
                build_scope_id=build_scope_id,
                scope_id=scope_id,
                environment=bound_environment,
            )
        except EvidenceRecorderError as exc:
            raise GatewayError(str(exc)) from exc
        self.store.append_gateway_event(
            "evidence_run_opened",
            target_id,
            {
                "evidence_run_id": run.id,
                "lease_id": lease.id,
                "ai_player_session_id": bound_session["id"] if bound_session else None,
                "ai_player_session_version": (
                    bound_session["version"] if bound_session else None
                ),
            },
        )
        return run

    def record_evidence_step(
        self,
        evidence_run_id: str,
        token: str,
        action: NormalizedAction,
        *,
        target_name: str | None = None,
        target_bounds: SourcePixelRect | None = None,
        settle_threshold: float = 0.01,
        required_consecutive: int = 2,
        settle_timeout_seconds: float = 4.0,
        sample_interval_seconds: float = 0.25,
        terminal_condition: EvidenceTerminalCondition | None = None,
        dynamic_scene_profile: EvidenceDynamicSceneProfile | None = None,
        capture_profile: Literal["full", "compact_static"] = "full",
        action_task_id: str | None = None,
        reused_before_artifact_id: str | None = None,
        trusted_terminal_reference_artifact_id: str | None = None,
        trusted_terminal_max_visual_distance: float = 0.012,
    ) -> EvidenceStep:
        run = self.store.get_evidence_run(evidence_run_id)
        if not run:
            raise GatewayError(f"unknown evidence run: {evidence_run_id}")
        adapter = self._adb_adapter(run.target_id)
        recorder = EvidenceRecorder(self.store, adapter)

        def authorize(step: EvidenceStep) -> None:
            guard = run.environment.get("source_state_guard")
            if guard is not None:
                if not isinstance(guard, dict):
                    raise GatewayError("invalid source-state guard")
                source = self.store.get_artifact(str(guard.get("artifact_id") or ""))
                before = self.store.get_artifact(str(step.before_frame_id or ""))
                if source is None or before is None:
                    raise GatewayError("source-state guard artifacts are missing")
                if source.sha256 != str(guard.get("artifact_sha256") or ""):
                    raise GatewayError("source-state guard artifact hash changed")
                maximum = float(guard.get("max_visual_distance") or 0)
                if not 0 < maximum <= 1:
                    raise GatewayError("source-state guard threshold is invalid")
                distance = perceptual_frame_distance(source.path, before.path)
                dynamic = guard.get("dynamic_target_guard")
                if isinstance(dynamic, dict):
                    if step.target_bounds is None:
                        raise GatewayError(
                            "source_state_mismatch: dynamic target guard requires bounded "
                            "pointer geometry; device action rejected"
                        )
                    expected_bounds = SourcePixelRect.model_validate(
                        dynamic.get("target_bounds")
                    )
                    if expected_bounds != step.target_bounds:
                        raise GatewayError(
                            "source_state_mismatch: dynamic target guard bounds differ from "
                            "the pending action; device action rejected"
                        )
                    global_maximum = float(
                        dynamic.get("max_global_visual_distance") or 0
                    )
                    target_maximum = float(
                        dynamic.get("max_target_visual_distance") or 0
                    )
                    context_maximum = float(
                        dynamic.get("max_context_structural_distance") or 0
                    )
                    padding = int(dynamic.get("context_padding_pixels") or 0)
                    if not (
                        maximum < global_maximum <= 1
                        and 0 < target_maximum <= maximum
                        and 0 < context_maximum <= maximum
                        and 1 <= padding <= 256
                    ):
                        raise GatewayError(
                            "source-state dynamic target guard threshold is invalid"
                        )
                    context_bounds = self._expanded_source_region(
                        step.target_bounds,
                        padding=padding,
                        viewport_width=step.viewport_width,
                        viewport_height=step.viewport_height,
                    )
                    target_distance = regional_perceptual_frame_distance(
                        source.path,
                        before.path,
                        step.target_bounds,
                    )
                    context_distance = regional_structural_frame_distance(
                        source.path,
                        before.path,
                        context_bounds,
                    )
                    if (
                        distance > global_maximum
                        or target_distance > target_maximum
                        or context_distance > context_maximum
                    ):
                        raise GatewayError(
                            "source_state_mismatch: dynamic background exception failed "
                            f"(global={distance:.6f}/{global_maximum:.6f}, "
                            f"target={target_distance:.6f}/{target_maximum:.6f}, "
                            f"context_structure={context_distance:.6f}/"
                            f"{context_maximum:.6f}); device action rejected"
                        )
                elif distance > maximum:
                    raise GatewayError(
                        "source_state_mismatch: current Before differs from the planned "
                        f"source frame ({distance:.6f} > {maximum:.6f}); "
                        "device action rejected"
                    )
            pre_reserved_payload = run.environment.get(self._PRE_RESERVED_ACTION_KEY)
            if pre_reserved_payload is not None:
                try:
                    binding = PreReservedAIPlayerActionV1.model_validate(
                        pre_reserved_payload
                    )
                except ValueError as exc:
                    raise GatewayError(
                        "invalid persisted pre-reserved AI player action binding"
                    ) from exc
                bound_session = self._assert_pre_reserved_ai_player_action(
                    run.environment,
                    binding,
                    action=step.action,
                )
            else:
                self._assert_ai_player_session_binding(run.environment)
                bound_session = self._reserve_ai_player_session_action(
                    run.environment, step
                )
            lease = self.authorize_mutation(run.target_id, token)
            self.record_mutation(
                run.target_id,
                lease,
                f"evidence_step:{action.type}",
                {
                    "evidence_run_id": run.id,
                    "evidence_step_id": step.id,
                    "ai_player_session_id": (
                        bound_session["id"] if bound_session else None
                    ),
                    "ai_player_session_version": (
                        bound_session["version"] if bound_session else None
                    ),
                },
            )

        try:
            step = recorder.record_step(
                run.id,
                action,
                target_name=target_name,
                target_bounds=target_bounds,
                settle_threshold=settle_threshold,
                required_consecutive=required_consecutive,
                settle_timeout_seconds=settle_timeout_seconds,
                sample_interval_seconds=sample_interval_seconds,
                terminal_condition=terminal_condition,
                dynamic_scene_profile=dynamic_scene_profile,
                before_action=authorize,
                capture_profile=capture_profile,
                action_task_id=action_task_id,
                reused_before_artifact_id=reused_before_artifact_id,
                trusted_terminal_reference_artifact_id=(
                    trusted_terminal_reference_artifact_id
                ),
                trusted_terminal_max_visual_distance=(
                    trusted_terminal_max_visual_distance
                ),
            )
        except EvidenceRecorderError as exc:
            raise GatewayError(str(exc)) from exc
        self.store.append_gateway_event(
            "evidence_step_gateway_result",
            run.target_id,
            {
                "evidence_run_id": run.id,
                "evidence_step_id": step.id,
                "action_run_id": step.action_run_id,
                "status": step.status,
            },
        )
        return step

    def pause_evidence_run(
        self,
        evidence_run_id: str,
        token: str,
    ) -> EvidenceRun:
        run = self.store.get_evidence_run(evidence_run_id)
        if not run:
            raise GatewayError(f"unknown evidence run: {evidence_run_id}")
        lease = self.validate(run.target_id, token)
        try:
            paused = EvidenceRecorder(self.store, None).pause_run(run.id)
        except EvidenceRecorderError as exc:
            raise GatewayError(str(exc)) from exc
        self.store.append_gateway_event(
            "evidence_run_paused",
            run.target_id,
            {"evidence_run_id": run.id, "lease_id": lease.id},
        )
        return paused

    def resume_evidence_run(
        self,
        evidence_run_id: str,
        token: str,
    ) -> EvidenceRun:
        run = self.store.get_evidence_run(evidence_run_id)
        if not run:
            raise GatewayError(f"unknown evidence run: {evidence_run_id}")
        lease = self.validate(run.target_id, token)
        self.assert_operational(run.target_id)
        try:
            resumed = EvidenceRecorder(self.store, None).resume_run(run.id)
        except EvidenceRecorderError as exc:
            raise GatewayError(str(exc)) from exc
        self.store.append_gateway_event(
            "evidence_run_resumed",
            run.target_id,
            {"evidence_run_id": run.id, "lease_id": lease.id},
        )
        return resumed

    def complete_evidence_run(
        self,
        evidence_run_id: str,
        token: str,
    ) -> EvidenceRunManifest:
        run = self.store.get_evidence_run(evidence_run_id)
        if not run:
            raise GatewayError(f"unknown evidence run: {evidence_run_id}")
        lease = self.validate(run.target_id, token)
        try:
            manifest = EvidenceRecorder(self.store, None).complete_run(run.id)
        except EvidenceRecorderError as exc:
            raise GatewayError(str(exc)) from exc
        self.store.append_gateway_event(
            "evidence_run_manifested",
            run.target_id,
            {
                "evidence_run_id": run.id,
                "manifest_id": manifest.id,
                "lease_id": lease.id,
                "publishable": manifest.publishable,
            },
        )
        return manifest

    def capture_stream(
        self,
        target_id: str,
        token: str,
        *,
        frame_count: int = 10,
        interval_seconds: float = 0.25,
        include_ui_every: int = 0,
        max_recoveries: int = 2,
    ) -> CaptureSession:
        self.validate(target_id, token)
        self.assert_operational(target_id)
        requested = max(1, min(frame_count, 600))
        interval = max(0.05, min(interval_seconds, 10.0))
        session = CaptureSession(
            id=f"capture.{uuid.uuid4().hex}",
            target_id=target_id,
            status="running",
            requested_frames=requested,
        )
        self.store.save_capture_session(session)
        self.store.append_gateway_event(
            "capture_stream_started",
            target_id,
            {"session_id": session.id, "requested_frames": requested},
        )
        adapter = self._adb_adapter(target_id)
        frames: list[str] = []
        ui_trees: list[str] = []
        recoveries = 0
        error: str | None = None
        status = "passed"
        for index in range(requested):
            try:
                self.assert_operational(target_id)
            except EmergencyStopActive as exc:
                status = "stopped"
                error = str(exc)
                break
            attempts = 0
            while True:
                try:
                    include_ui = include_ui_every > 0 and index % include_ui_every == 0
                    observation = adapter.observe_frame(include_ui=include_ui)
                    frames.append(observation.frame.id)
                    if observation.ui_tree:
                        ui_trees.append(observation.ui_tree.id)
                    break
                except AdapterError as exc:
                    attempts += 1
                    if recoveries >= max(0, max_recoveries):
                        status = "failed"
                        error = str(exc)
                        break
                    recoveries += 1
                    self.store.append_gateway_event(
                        "capture_recovery",
                        target_id,
                        {"session_id": session.id, "attempt": attempts, "error": str(exc)},
                    )
                    adapter.recover_connection()
            if status == "failed":
                break
            if index + 1 < requested:
                time.sleep(interval)
        completed = session.model_copy(
            update={
                "status": status,
                "ended_at": utc_now(),
                "frame_artifact_ids": frames,
                "ui_tree_artifact_ids": ui_trees,
                "recovery_count": recoveries,
                "error": error,
            }
        )
        self.store.save_capture_session(completed)
        self.store.append_gateway_event(
            "capture_stream_finished",
            target_id,
            {
                "session_id": completed.id,
                "status": completed.status,
                "frames": len(frames),
                "recoveries": recoveries,
            },
        )
        return completed

    def recover_target(self, target_id: str, token: str) -> TargetInfo:
        lease = self.validate(target_id, token)
        self.assert_operational(target_id)
        target = self._adb_adapter(target_id).recover_connection()
        self.store.append_gateway_event(
            "transport_recovered",
            target_id,
            {"lease_id": lease.id, "status": target.status},
        )
        return target

    def mumu_control(
        self, target_id: str, token: str, operation: str, *, cli: MumuCli | None = None
    ) -> dict[str, Any]:
        lease = self.authorize_mutation(target_id, token)
        record = self.store.get_target(target_id)
        if not record or record.kind != "mumu":
            raise GatewayError("MuMu lifecycle control requires a MuMu target")
        vmindex = str(record.metadata.get("vmindex") or "")
        if not vmindex:
            raise GatewayError("MuMu vmindex is unavailable; refresh target discovery")
        result = (cli or MumuCli()).control(vmindex, operation)
        self.record_mutation(target_id, lease, f"mumu_{operation}", result)
        return result

    def _mumu_authority(self, target_id: str, token: str) -> tuple[DeviceLease, TargetRecord, str]:
        lease = self.authorize_mutation(target_id, token)
        record = self.store.get_target(target_id)
        if not record or record.kind != "mumu":
            raise GatewayError("MuMu management requires a MuMu target")
        vmindex = str(record.metadata.get("vmindex") or "")
        if not vmindex:
            raise GatewayError("MuMu vmindex is unavailable; refresh target discovery")
        return lease, record, vmindex

    def mumu_clone(
        self,
        target_id: str,
        token: str,
        *,
        number: int = 1,
        cli: MumuCli | None = None,
    ) -> dict[str, Any]:
        lease, _record, vmindex = self._mumu_authority(target_id, token)
        result = (cli or MumuCli()).clone(vmindex, number=number)
        self.record_mutation(target_id, lease, "mumu_clone", result)
        self.refresh()
        return result

    def mumu_export_snapshot(
        self,
        target_id: str,
        token: str,
        *,
        name: str,
        compressed: bool = True,
        cli: MumuCli | None = None,
    ) -> dict[str, Any]:
        lease, _record, vmindex = self._mumu_authority(target_id, token)
        snapshot_root = (self.store.root / "snapshots" / "mumu").resolve()
        result = (cli or MumuCli()).export_snapshot(
            vmindex,
            snapshot_root,
            name=name,
            compressed=compressed,
        )
        self.record_mutation(
            target_id,
            lease,
            "mumu_export_snapshot",
            {key: value for key, value in result.items() if key != "cli"},
        )
        return result

    def mumu_import_snapshot(
        self,
        target_id: str,
        token: str,
        snapshot_path: Path,
        *,
        number: int = 1,
        cli: MumuCli | None = None,
    ) -> dict[str, Any]:
        lease, _record, _vmindex = self._mumu_authority(target_id, token)
        root = (self.store.root / "snapshots" / "mumu").resolve()
        path = snapshot_path.resolve()
        if root != path and root not in path.parents:
            raise GatewayError("MuMu snapshot import is restricted to the facility snapshot store")
        result = (cli or MumuCli()).import_snapshot(path, number=number)
        self.record_mutation(
            target_id,
            lease,
            "mumu_import_snapshot",
            {"path": str(path), "number": number, "result": result},
        )
        self.refresh()
        return result

    def mumu_delete_clone(
        self,
        target_id: str,
        token: str,
        *,
        cli: MumuCli | None = None,
    ) -> dict[str, Any]:
        lease, _record, vmindex = self._mumu_authority(target_id, token)
        result = (cli or MumuCli()).delete(vmindex)
        self.record_mutation(target_id, lease, "mumu_delete_clone", result)
        self.release(token)
        self.refresh()
        return result

    def _refresh_providers(self, providers: list[TargetProvider]) -> set[str]:
        """Refresh only the requested provider set while preserving registry semantics."""

        refreshed_provider_names: set[str] = set()
        for provider in providers:
            try:
                discovered = provider.discover()
            except (AdapterError, GatewayError, OSError, ValueError) as exc:
                self.store.append_gateway_event(
                    "provider_error", None, {"provider": provider.name, "error": str(exc)}
                )
                continue
            refreshed_provider_names.add(provider.name)
            seen = {item.id for item in discovered}
            for item in discovered:
                self.store.upsert_target(item)
            for existing in self.store.list_targets():
                if existing.provider == provider.name and existing.id not in seen and existing.status != "offline":
                    offline = existing.model_copy(update={"status": "offline"})
                    self.store.upsert_target(offline)
            self.store.append_gateway_event(
                "provider_refresh", None, {"provider": provider.name, "targets": sorted(seen)}
            )
        return refreshed_provider_names

    def refresh(self, *, target_id: str | None = None) -> list[TargetRecord]:
        """Refresh providers, preferring the persisted provider for a bound target.

        A regular action already has a canonical target binding, so probing unrelated
        transports only adds latency and subprocess churn.  Missing bindings, provider
        failures, or a target that remains offline are recovery conditions and retain
        the former full-provider scan behavior.
        """

        if target_id is None:
            self._refresh_providers(self.providers)
            return self.store.list_targets()

        existing = self.store.get_target(target_id)
        if (
            existing is not None
            and existing.provider == "adb-direct"
            and existing.status == "online"
        ):
            # ``adb-direct`` is a connection-proven endpoint binding rather than
            # a discovery provider. The action adapter reconnects to and verifies
            # this exact serial before any device write, so scanning MuMu, generic
            # ADB and remote transports here adds latency without strengthening the
            # guard. A failed reconnect remains fail-closed; offline bindings still
            # enter the recovery scan below.
            return self.store.list_targets()
        bound_providers = (
            [provider for provider in self.providers if provider.name == existing.provider]
            if existing is not None
            else []
        )
        if bound_providers:
            refreshed_provider_names = self._refresh_providers(bound_providers)
            refreshed_target = self.store.get_target(target_id)
            if (
                existing.provider in refreshed_provider_names
                and refreshed_target is not None
                and refreshed_target.status == "online"
            ):
                return self.store.list_targets()

        # No canonical binding, an unavailable provider, or an offline target means the
        # caller is establishing/recovering connectivity. Scan every provider, while
        # avoiding a duplicate probe of providers already tried above.
        attempted_names = {provider.name for provider in bound_providers}
        recovery_providers = [
            provider for provider in self.providers if provider.name not in attempted_names
        ]
        self._refresh_providers(recovery_providers)
        return self.store.list_targets()

    def target_infos(self, *, refresh_if_empty: bool = True) -> list[TargetInfo]:
        records = self.store.list_targets()
        if not records and refresh_if_empty:
            records = self.refresh()
        return [
            TargetInfo(
                id=item.id,
                kind=item.kind,
                label=item.label,
                status=item.status,
                capabilities=item.capabilities,
                metadata={**item.metadata, "provider": item.provider, "endpoint": item.endpoint},
            )
            for item in records
        ]

    def _expire_stale(self) -> None:
        now = self.clock()
        stale_leases = self.store.list_leases(
            status="active",
            expires_at_or_before=now.isoformat(),
        )
        for lease in stale_leases:
            expired = lease.model_copy(update={"status": "expired"})
            self.store.save_lease(expired)
            self.store.append_gateway_event(
                "lease_expired", lease.target_id, {"lease_id": lease.id, "holder": lease.holder}
            )

    def acquire(
        self,
        target_id: str,
        holder: str,
        *,
        ttl_seconds: int = 300,
        owner_context: dict[str, Any] | None = None,
    ) -> DeviceLease:
        self._expire_stale()
        target = self.store.get_target(target_id)
        if not target:
            raise GatewayError(f"unknown target: {target_id}")
        lifecycle_only = target.kind == "mumu" and "lifecycle" in target.capabilities
        if target.status != "online" and not lifecycle_only:
            raise GatewayError(f"target is not online: {target_id}")
        if not holder.strip():
            raise GatewayError("lease holder is required")
        active = self.store.list_leases(target_id, status="active")
        if active:
            raise LeaseConflict(f"target already leased by {active[0].holder}")
        ttl = max(15, min(ttl_seconds, 1800))
        now = self.clock()
        lease = DeviceLease(
            id=f"lease.{uuid.uuid4().hex}",
            target_id=target_id,
            holder=holder.strip(),
            token=secrets.token_urlsafe(32),
            acquired_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=ttl)).isoformat(),
            owner_context=dict(owner_context or {}),
        )
        self.store.save_lease(lease)
        self.store.append_gateway_event(
            "lease_acquired", target_id, {"lease_id": lease.id, "holder": lease.holder, "ttl": ttl}
        )
        return lease

    def validate(self, target_id: str, token: str) -> DeviceLease:
        self._expire_stale()
        lease = self.store.get_lease_by_token(token)
        if not lease or lease.target_id != target_id or lease.status != "active":
            raise GatewayError("valid active lease required")
        return lease

    def renew(self, token: str, *, ttl_seconds: int = 300) -> DeviceLease:
        self._expire_stale()
        lease = self.store.get_lease_by_token(token)
        if not lease or lease.status != "active":
            raise GatewayError("active lease required")
        ttl = max(15, min(ttl_seconds, 1800))
        renewed = lease.model_copy(
            update={"expires_at": (self.clock() + timedelta(seconds=ttl)).isoformat()}
        )
        self.store.save_lease(renewed)
        self.store.append_gateway_event(
            "lease_renewed", renewed.target_id, {"lease_id": renewed.id, "ttl": ttl}
        )
        return renewed

    def release(self, token: str) -> DeviceLease:
        self._expire_stale()
        lease = self.store.get_lease_by_token(token)
        if not lease:
            raise GatewayError("lease not found")
        if lease.status != "active":
            return lease
        released = lease.model_copy(
            update={"status": "released", "released_at": self.clock().isoformat()}
        )
        self.store.save_lease(released)
        self.store.append_gateway_event(
            "lease_released", released.target_id, {"lease_id": released.id, "holder": released.holder}
        )
        return released
