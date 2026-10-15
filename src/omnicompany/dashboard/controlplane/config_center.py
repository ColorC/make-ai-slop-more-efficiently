"""Live, read-only Spark configuration review served by the existing Omni gateway."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Callable

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response


class ConfigCenterBuildError(RuntimeError):
    """Raised when the live review cannot be built safely."""


BuildRunner = Callable[[Path], None]


class SparkConfigReviewBuilder:
    """Fingerprint AssetHub inputs and atomically rebuild the embedded review."""

    def __init__(
        self,
        asset_hub_root: Path,
        cache_html: Path,
        *,
        build_runner: BuildRunner | None = None,
    ) -> None:
        self.asset_hub_root = asset_hub_root.resolve()
        self.cache_html = cache_html.resolve()
        self.fingerprint_file = self.cache_html.with_suffix(self.cache_html.suffix + ".fingerprint")
        self._build_runner = build_runner
        self._lock = threading.Lock()

    @classmethod
    def from_environment(cls) -> "SparkConfigReviewBuilder":
        repo_root = Path(__file__).resolve().parents[4]
        asset_hub_root = Path(
            os.environ.get("OMNI_SPARK_CONFIG_HUB_ROOT", str(repo_root.parent / "aiassethub"))
        )
        cache_html = Path(
            os.environ.get(
                "OMNI_SPARK_CONFIG_CENTER_CACHE",
                str(repo_root / "var" / "config-center" / "spark-config-center.html"),
            )
        )
        return cls(asset_hub_root, cache_html)

    def _configured_source_paths(self) -> list[Path]:
        config_path = self.asset_hub_root / "config.json"
        if not config_path.is_file():
            return []
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigCenterBuildError(f"AssetHub config.json cannot be read: {exc}") from exc

        paths: list[Path] = []
        for source in payload.get("configManagement", {}).get("sources", []):
            for key in ("runtimeJsonDir", "headersFile", "typesFile"):
                value = source.get(key)
                if value:
                    paths.append(Path(value))
        return paths

    def input_roots(self) -> list[Path]:
        root = self.asset_hub_root
        return [
            root / "config.json",
            root / "public" / "config.html",
            root / "public" / "config-app.js",
            root / "public" / "config-styles.css",
            root / "public" / "styles.css",
            root / "scripts" / "build-config-review.mjs",
            root / "src" / "config-manager.mjs",
            root / "src" / "config-knowledge.mjs",
            root / "src" / "scanner.mjs",
            root / "knowledge" / "index",
            root / "knowledge" / "manual",
            *self._configured_source_paths(),
        ]

    @staticmethod
    def _files_under(path: Path) -> list[Path]:
        if path.is_file():
            return [path]
        if path.is_dir():
            return sorted((item for item in path.rglob("*") if item.is_file()), key=str)
        return []

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for root in self.input_roots():
            digest.update(str(root).encode("utf-8"))
            files = self._files_under(root)
            if not files:
                digest.update(b"\0missing-or-empty\0")
                continue
            for path in files:
                digest.update(str(path).encode("utf-8"))
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
        return digest.hexdigest()

    def _run_build(self, output: Path) -> None:
        if self._build_runner is not None:
            self._build_runner(output)
            return
        if not self.asset_hub_root.is_dir():
            raise ConfigCenterBuildError(f"AssetHub root is missing: {self.asset_hub_root}")
        script = self.asset_hub_root / "scripts" / "build-config-review.mjs"
        if not script.is_file():
            raise ConfigCenterBuildError(f"Review build script is missing: {script}")
        node = shutil.which("node")
        if not node:
            raise ConfigCenterBuildError("Node.js is not available on PATH")
        completed = subprocess.run(
            [
                node,
                str(script),
                "--output",
                str(output),
                "--delivery-mode",
                "live",
            ],
            cwd=self.asset_hub_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown build failure").strip()
            raise ConfigCenterBuildError(f"Spark configuration review build failed: {detail[-2000:]}")

    def ensure_current(self) -> tuple[Path, str, bool]:
        with self._lock:
            fingerprint = self.fingerprint()
            previous = ""
            if self.fingerprint_file.is_file():
                previous = self.fingerprint_file.read_text(encoding="ascii").strip()
            if self.cache_html.is_file() and previous == fingerprint:
                return self.cache_html, fingerprint, False

            self.cache_html.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_html.with_suffix(self.cache_html.suffix + ".tmp")
            temporary.unlink(missing_ok=True)
            try:
                self._run_build(temporary)
                if not temporary.is_file() or temporary.stat().st_size == 0:
                    raise ConfigCenterBuildError("Review build completed without producing HTML")
                temporary.replace(self.cache_html)
                self.fingerprint_file.write_text(fingerprint, encoding="ascii")
            finally:
                temporary.unlink(missing_ok=True)
            return self.cache_html, fingerprint, True


config_center_router = APIRouter(tags=["config-center"])
_builder = SparkConfigReviewBuilder.from_environment()


@config_center_router.get("/config-center/spark", response_model=None)
def spark_config_center() -> Response:
    try:
        path, fingerprint, rebuilt = _builder.ensure_current()
    except (ConfigCenterBuildError, OSError, subprocess.SubprocessError) as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Omni-Config-Fingerprint": fingerprint,
            "X-Omni-Config-Rebuilt": "1" if rebuilt else "0",
        },
    )
