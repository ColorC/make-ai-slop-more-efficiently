from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root


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

    @property
    def runtime_root(self) -> Path:
        return self.store_root / "runtimes" / "omniparser-v2"

    @property
    def python_executable(self) -> Path:
        return self.runtime_root / ".venv" / "Scripts" / "python.exe"

    @property
    def weights_root(self) -> Path:
        return self.runtime_root / "weights"

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
        python_ok = self.python_executable.is_file()
        module_probe_error = ""
        if python_ok:
            script = (
                "import importlib.util,json;"
                f"names={list(OMNIPARSER_REQUIRED_MODULES)!r};"
                "print(json.dumps({n:bool(importlib.util.find_spec(n)) for n in names}))"
            )
            completed = subprocess.run(
                [str(self.python_executable), "-c", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            if completed.returncode == 0:
                modules.update(json.loads(completed.stdout))
            else:
                module_probe_error = completed.stderr.strip()
        return {
            "locator": "omniparser-v2",
            "source_root": str(self.source_root),
            "runtime_root": str(self.runtime_root),
            "python_executable": str(self.python_executable),
            "source_files": source_files,
            "weight_files": weight_files,
            "modules": modules,
            "module_probe_error": module_probe_error,
            "ready": (
                all(source_files.values())
                and python_ok
                and all(modules.values())
                and all(weight_files.values())
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
        probe = self.probe()
        if not probe["ready"]:
            raise VisualLocatorError(
                "OmniParser runtime is not ready: "
                + json.dumps(probe, ensure_ascii=False)
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        worker = Path(__file__).with_name("omniparser_worker.py").resolve()
        command = [
            str(self.python_executable),
            str(worker),
            "--omniparser-home",
            str(self.source_root),
            "--weights-root",
            str(self.weights_root),
            "--image",
            str(image),
            "--output-dir",
            str(output_dir),
            "--box-threshold",
            str(box_threshold),
        ]
        env = os.environ.copy()
        env.update(
            {
                "HF_HOME": str(self.runtime_root / "hf-cache"),
                "EASYOCR_MODULE_PATH": str(self.runtime_root / "easyocr-cache"),
                "YOLO_CONFIG_DIR": str(self.runtime_root / "ultralytics-config"),
                "PYTHONUTF8": "1",
            }
        )
        started = time.perf_counter()
        completed = subprocess.run(
            command,
            cwd=str(self.source_root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        elapsed = time.perf_counter() - started
        (output_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
        (output_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode:
            raise VisualLocatorError(
                f"OmniParser worker failed with exit {completed.returncode}; "
                f"see {output_dir / 'stderr.log'}"
            )
        result_path = output_dir / "result.json"
        if not result_path.is_file():
            raise VisualLocatorError("OmniParser worker did not create result.json")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["process_elapsed_seconds"] = round(elapsed, 6)
        result["stdout_log"] = str(output_dir / "stdout.log")
        result["stderr_log"] = str(output_dir / "stderr.log")
        result["runtime_manifest"] = str(self.runtime_root / "runtime-manifest.json")
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result


__all__ = [
    "OMNIPARSER_MODEL_FILES",
    "OMNIPARSER_REPO_ID",
    "OMNIPARSER_REQUIRED_MODULES",
    "OmniParserRuntime",
    "VisualLocatorError",
]