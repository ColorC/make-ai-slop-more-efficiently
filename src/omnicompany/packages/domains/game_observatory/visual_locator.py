from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

from .omniparser_daemon import (
    PROTOCOL_VERSION,
    WORKER_VERSION,
    daemon_job_hash,
)
from .subprocess_policy import headless_process_kwargs


class VisualLocatorError(RuntimeError):
    """Raised when the isolated screenshot locator cannot be prepared or executed."""


OMNIPARSER_REPO_ID = "microsoft/OmniParser-v2.0"
OMNIPARSER_MODEL_FILES = (
    "icon_detect/train_args.yaml",
    "icon_detect/model.pt",
    "icon_detect/model.yaml",
    "icon_caption/config.json",
    "icon_caption/generation_config.json",
    "icon_caption/model.safetensors",
)
OMNIPARSER_REQUIRED_MODULES = (
    "torch",
    "torchvision",
    "transformers",
    "ultralytics",
    "supervision",
    "easyocr",
    "paddleocr",
    "paddle",
    "accelerate",
    "timm",
    "einops",
)
OMNIPARSER_REQUIRED_DISTRIBUTIONS = {
    "torch": "torch",
    "torchvision": "torchvision",
    "transformers": "transformers",
    "ultralytics": "ultralytics",
    "supervision": "supervision",
    "easyocr": "easyocr",
    "paddleocr": "paddleocr",
    "paddle": "paddlepaddle",
    "accelerate": "accelerate",
    "timm": "timm",
    "einops": "einops",
}
OMNIPARSER_PINNED_VERSIONS = {
    "transformers": "4.40.0",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(slots=True)
class OmniParserRuntime:
    store_root: Path
    source_root: Path | None = None
    python_executable_override: Path | None = None
    daemon_test_mode: bool = False

    def __post_init__(self) -> None:
        self.store_root = self.store_root.resolve()
        if self.source_root is None:
            configured = os.environ.get("OMNIPARSER_HOME")
            self.source_root = (
                Path(configured)
                if configured
                else omni_workspace_root().parent / "参考项目" / "OmniParser"
            )
        self.source_root = self.source_root.resolve()
        if self.python_executable_override is not None:
            self.python_executable_override = self.python_executable_override.resolve()

    @property
    def runtime_root(self) -> Path:
        return self.store_root / "runtimes" / "omniparser-v2"

    @property
    def python_executable(self) -> Path:
        if self.python_executable_override is not None:
            return self.python_executable_override
        return self.runtime_root / ".venv" / "Scripts" / "python.exe"

    @property
    def weights_root(self) -> Path:
        return self.runtime_root / "weights"

    @property
    def daemon_root(self) -> Path:
        return self.runtime_root / "daemon"

    @property
    def daemon_identity_path(self) -> Path:
        return self.daemon_root / "identity.json"

    @property
    def daemon_port(self) -> int:
        workspace_hash = hashlib.sha256(str(self.store_root).encode("utf-8")).digest()
        return 23000 + int.from_bytes(workspace_hash[:2], "big") % 20000

    def _daemon_secret(self) -> str:
        path = self.daemon_root / "secret.txt"
        self.daemon_root.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = secrets.token_hex(32)
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(value)
        except FileExistsError:
            value = path.read_text(encoding="utf-8").strip()
        return value

    def _asset_identity(self, *, box_threshold: float) -> dict[str, str]:
        worker = Path(__file__).with_name("omniparser_worker.py").resolve()
        daemon = Path(__file__).with_name("omniparser_daemon.py").resolve()
        source_paths = [
            self.source_root / "util" / "omniparser.py",
            self.source_root / "util" / "utils.py",
            worker,
            daemon,
        ]
        weight_paths = [self.weights_root / name for name in OMNIPARSER_MODEL_FILES]
        selected = source_paths + weight_paths
        cache_path = self.runtime_root / "asset-hashes.json"
        cached: dict[str, Any] = {}
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = {}
        old_files = cached.get("files") if isinstance(cached.get("files"), dict) else {}
        files: dict[str, dict[str, Any]] = {}
        for path in selected:
            if not path.is_file():
                if self.daemon_test_mode:
                    continue
                raise VisualLocatorError(f"OmniParser asset is missing: {path}")
            stat = path.stat()
            key = str(path)
            previous = old_files.get(key) if isinstance(old_files, dict) else None
            if (
                isinstance(previous, dict)
                and previous.get("size") == stat.st_size
                and previous.get("mtime_ns") == stat.st_mtime_ns
                and isinstance(previous.get("sha256"), str)
            ):
                digest = previous["sha256"]
            else:
                digest = _sha256(path)
            files[key] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": digest,
            }
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        pending = cache_path.with_suffix(
            f".{os.getpid()}.{secrets.token_hex(6)}.tmp"
        )
        pending.write_text(
            json.dumps({"schema": "omniparser.asset-hashes.v1", "files": files}, indent=2),
            encoding="utf-8",
        )
        os.replace(pending, cache_path)

        def aggregate(paths: list[Path]) -> str:
            payload = [
                {"path": str(path), "sha256": files[str(path)]["sha256"]}
                for path in paths
                if str(path) in files
            ]
            return hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()

        weights_hash = aggregate(weight_paths)
        config_payload = {
            "protocol": PROTOCOL_VERSION,
            "worker_version": WORKER_VERSION,
            "source_hash": aggregate(source_paths),
            "weights_hash": weights_hash,
            "box_threshold": round(float(box_threshold), 6),
            "fake_engine": self.daemon_test_mode,
        }
        config_hash = hashlib.sha256(
            json.dumps(config_payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        return {"config_hash": config_hash, "weights_hash": weights_hash}

    def _read_daemon_identity(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.daemon_identity_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _daemon_request(
        self,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        request = {"secret": self._daemon_secret(), **payload}
        try:
            with socket.create_connection(
                ("127.0.0.1", self.daemon_port),
                timeout=timeout_seconds,
            ) as connection:
                connection.settimeout(timeout_seconds)
                connection.sendall(
                    json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
                )
                response = connection.makefile("rb").readline(1024 * 1024)
        except (OSError, TimeoutError) as exc:
            raise VisualLocatorError(f"OmniParser daemon IPC failed: {exc}") from exc
        if not response:
            raise VisualLocatorError("OmniParser daemon closed IPC without a result")
        try:
            result = json.loads(response)
        except json.JSONDecodeError as exc:
            raise VisualLocatorError("OmniParser daemon returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise VisualLocatorError("OmniParser daemon returned an invalid response")
        return result

    def _healthy_identity(
        self,
        identity: dict[str, Any] | None,
        *,
        config_hash: str,
        weights_hash: str,
    ) -> dict[str, Any] | None:
        if not identity or any(
            (
                identity.get("status") != "ready",
                identity.get("schema") != PROTOCOL_VERSION,
                identity.get("worker_version") != WORKER_VERSION,
                identity.get("port") != self.daemon_port,
                identity.get("config_hash") != config_hash,
                identity.get("weights_hash") != weights_hash,
            )
        ):
            return None
        try:
            health = self._daemon_request({"operation": "health"}, timeout_seconds=1.5)
        except VisualLocatorError:
            return None
        if (
            health.get("ok") is True
            and health.get("pid") == identity.get("pid")
            and health.get("config_hash") == config_hash
            and health.get("weights_hash") == weights_hash
        ):
            return {**identity, "health": health}
        return None

    def _stop_daemon(self) -> None:
        try:
            self._daemon_request({"operation": "shutdown"}, timeout_seconds=1.5)
        except VisualLocatorError:
            return
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            identity = self._read_daemon_identity()
            if not identity or identity.get("status") == "stopped":
                return
            time.sleep(0.05)

    def _wait_for_daemon(
        self,
        *,
        config_hash: str,
        weights_hash: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        last_identity: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            last_identity = self._read_daemon_identity()
            healthy = self._healthy_identity(
                last_identity,
                config_hash=config_hash,
                weights_hash=weights_hash,
            )
            if healthy is not None:
                return healthy
            if last_identity and last_identity.get("status") == "failed":
                raise VisualLocatorError(
                    "OmniParser daemon failed during warmup: "
                    + json.dumps(last_identity, ensure_ascii=False)
                )
            time.sleep(0.1)
        raise VisualLocatorError(
            f"OmniParser daemon warmup exceeded {timeout_seconds:g}s; "
            f"identity={json.dumps(last_identity, ensure_ascii=False)}"
        )

    def _ensure_daemon(
        self,
        *,
        box_threshold: float,
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], bool]:
        assets = self._asset_identity(box_threshold=box_threshold)
        identity = self._read_daemon_identity()
        healthy = self._healthy_identity(identity, **assets)
        if healthy is not None:
            return healthy, True
        if (
            identity
            and identity.get("status") == "warming"
            and identity.get("config_hash") == assets["config_hash"]
            and identity.get("weights_hash") == assets["weights_hash"]
        ):
            return (
                self._wait_for_daemon(timeout_seconds=timeout_seconds, **assets),
                True,
            )
        if identity and identity.get("status") in {"ready", "warming"}:
            self._stop_daemon()
        if not self.python_executable.is_file():
            raise VisualLocatorError(
                f"OmniParser Python runtime is missing: {self.python_executable}"
            )
        daemon_script = Path(__file__).with_name("omniparser_daemon.py").resolve()
        command = [
            str(self.python_executable),
            str(daemon_script),
            "--omniparser-home",
            str(self.source_root),
            "--weights-root",
            str(self.weights_root),
            "--runtime-root",
            str(self.runtime_root),
            "--port",
            str(self.daemon_port),
            "--secret",
            self._daemon_secret(),
            "--config-hash",
            assets["config_hash"],
            "--weights-hash",
            assets["weights_hash"],
            "--box-threshold",
            str(box_threshold),
        ]
        if self.daemon_test_mode:
            command.append("--fake-engine")
        env = os.environ.copy()
        env.update(
            {
                "HF_HOME": str(self.runtime_root / "hf-cache"),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "EASYOCR_MODULE_PATH": str(self.runtime_root / "easyocr-cache"),
                "YOLO_CONFIG_DIR": str(self.runtime_root / "ultralytics-config"),
                "PYTHONUTF8": "1",
            }
        )
        self.daemon_root.mkdir(parents=True, exist_ok=True)
        stdout = (self.daemon_root / "stdout.log").open("a", encoding="utf-8")
        stderr = (self.daemon_root / "stderr.log").open("a", encoding="utf-8")
        try:
            subprocess.Popen(  # noqa: S603 - fixed local isolated runtime
                command,
                cwd=str(daemon_script.parent),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                close_fds=True,
                **headless_process_kwargs(),
            )
        finally:
            stdout.close()
            stderr.close()
        return (
            self._wait_for_daemon(timeout_seconds=timeout_seconds, **assets),
            False,
        )

    def probe(self) -> dict[str, Any]:
        source_files = {
            name: (self.source_root / name).is_file()
            for name in ("README.md", "LICENSE", "util/omniparser.py", "util/utils.py")
        }
        weight_files = {
            name: (self.weights_root / name).is_file()
            for name in OMNIPARSER_MODEL_FILES
        }
        modules: dict[str, bool] = {name: False for name in OMNIPARSER_REQUIRED_MODULES}
        versions: dict[str, str] = {}
        python_ok = self.python_executable.is_file()
        module_probe_error = ""
        if python_ok:
            script = (
                "import importlib.util,importlib.metadata,json;"
                f"names={list(OMNIPARSER_REQUIRED_MODULES)!r};"
                f"dists={sorted(set(OMNIPARSER_REQUIRED_DISTRIBUTIONS.values()))!r};"
                "print(json.dumps({'modules':{n:bool(importlib.util.find_spec(n)) "
                "for n in names},'versions':{n:importlib.metadata.version(n) "
                "for n in dists}}))"
            )
            completed = subprocess.run(
                [str(self.python_executable), "-c", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
                **headless_process_kwargs(),
            )
            if completed.returncode == 0:
                payload = json.loads(completed.stdout)
                modules.update(payload["modules"])
                versions.update(payload["versions"])
            else:
                module_probe_error = completed.stderr.strip()
        version_issues = {
            name: {"expected": expected, "actual": versions.get(name)}
            for name, expected in OMNIPARSER_PINNED_VERSIONS.items()
            if versions.get(name) != expected
        }
        return {
            "locator": "omniparser-v2",
            "source_root": str(self.source_root),
            "runtime_root": str(self.runtime_root),
            "python_executable": str(self.python_executable),
            "source_files": source_files,
            "weight_files": weight_files,
            "modules": modules,
            "versions": versions,
            "version_requirements": OMNIPARSER_PINNED_VERSIONS,
            "version_issues": version_issues,
            "module_probe_error": module_probe_error,
            "ready": (
                all(source_files.values())
                and python_ok
                and all(modules.values())
                and all(weight_files.values())
                and not version_issues
            ),
            "license_boundary": {
                "repository_code": "MIT",
                "icon_detect_weights": "AGPL inherited from YOLO",
                "icon_caption_weights": "MIT",
                "deployment": "isolated local process; no public endpoint",
                "source": str(self.source_root / "README.md"),
            },
        }

    def download_weights(self) -> dict[str, Any]:
        from huggingface_hub import hf_hub_download

        self.weights_root.mkdir(parents=True, exist_ok=True)
        downloaded: list[dict[str, Any]] = []
        for filename in OMNIPARSER_MODEL_FILES:
            local = Path(
                hf_hub_download(
                    repo_id=OMNIPARSER_REPO_ID,
                    filename=filename,
                    local_dir=self.weights_root,
                )
            ).resolve()
            downloaded.append(
                {
                    "name": filename,
                    "path": str(local),
                    "bytes": local.stat().st_size,
                    "sha256": _sha256(local),
                }
            )
        return {
            "repo_id": OMNIPARSER_REPO_ID,
            "files": downloaded,
            "total_bytes": sum(item["bytes"] for item in downloaded),
        }

    def setup(self, *, download: bool = True) -> dict[str, Any]:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        downloads = self.download_weights() if download else None
        result = self.probe()
        result["ok"] = bool(result["ready"]) if download else True
        result["downloads"] = downloads
        manifest = self.runtime_root / "runtime-manifest.json"
        manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["manifest"] = str(manifest)
        return result

    def locate(
        self,
        image: Path,
        output_dir: Path,
        *,
        box_threshold: float = 0.05,
        timeout_seconds: float = 1200,
    ) -> dict[str, Any]:
        image = image.resolve()
        output_dir = output_dir.resolve()
        if not image.is_file():
            raise VisualLocatorError(f"screenshot not found: {image}")
        output_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        warmup_timeout = min(60.0, float(timeout_seconds))
        identity, daemon_reused = self._ensure_daemon(
            box_threshold=box_threshold,
            timeout_seconds=warmup_timeout,
        )
        remaining = float(timeout_seconds) - (time.perf_counter() - started)
        if remaining <= 0:
            raise VisualLocatorError(
                f"OmniParser request exceeded {timeout_seconds:g}s during daemon warmup"
            )
        image_sha = _sha256(image)
        job_hash = daemon_job_hash(
            image_sha256=image_sha,
            output_dir=output_dir,
            config_hash=str(identity["config_hash"]),
        )
        inference_timeout = min(20.0, remaining)
        try:
            response = self._daemon_request(
                {
                    "operation": "locate",
                    "job_hash": job_hash,
                    "config_hash": identity["config_hash"],
                    "image": str(image),
                    "image_sha256": image_sha,
                    "output_dir": str(output_dir),
                },
                timeout_seconds=inference_timeout,
            )
        except VisualLocatorError as exc:
            self._stop_daemon()
            raise VisualLocatorError(
                f"OmniParser warm inference made no progress within "
                f"{inference_timeout:g}s; daemon was stopped for clean restart: {exc}"
            ) from exc
        elapsed = time.perf_counter() - started
        (output_dir / "stdout.log").write_text(
            json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output_dir / "stderr.log").write_text("", encoding="utf-8")
        if response.get("ok") is not True:
            raise VisualLocatorError(
                "OmniParser daemon rejected the job: "
                + json.dumps(response, ensure_ascii=False)
            )
        result_path = output_dir / "result.json"
        if not result_path.is_file():
            raise VisualLocatorError("OmniParser daemon did not create result.json")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["ok"] = True
        result["process_elapsed_seconds"] = round(elapsed, 6)
        result["stdout_log"] = str(output_dir / "stdout.log")
        result["stderr_log"] = str(output_dir / "stderr.log")
        result["runtime_manifest"] = str(self.runtime_root / "runtime-manifest.json")
        result["daemon"] = {
            "protocol": PROTOCOL_VERSION,
            "worker_version": WORKER_VERSION,
            "pid": identity["pid"],
            "port": identity["port"],
            "config_hash": identity["config_hash"],
            "weights_hash": identity["weights_hash"],
            "identity": str(self.daemon_identity_path),
            "lock": str(self.daemon_root / "worker.lock"),
            "reused": daemon_reused,
            "inference_timeout_seconds": inference_timeout,
        }
        result.setdefault("metrics", {})["daemon_inference_seconds"] = response.get(
            "inference_seconds"
        )
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result


__all__ = [
    "OMNIPARSER_MODEL_FILES",
    "OMNIPARSER_PINNED_VERSIONS",
    "OMNIPARSER_REPO_ID",
    "OMNIPARSER_REQUIRED_DISTRIBUTIONS",
    "OMNIPARSER_REQUIRED_MODULES",
    "OmniParserRuntime",
    "VisualLocatorError",
]
