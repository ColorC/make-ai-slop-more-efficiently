from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import ObservatoryStore


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FacilityMaintenance:
    def __init__(self, store: ObservatoryStore) -> None:
        self.store = store

    def monitor(self, *, write: bool = True) -> dict[str, Any]:
        with sqlite3.connect(self.store.db_path) as conn:
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        missing: list[str] = []
        corrupt: list[str] = []
        artifacts = self.store.list_artifacts()
        for artifact in artifacts:
            path = Path(artifact.path)
            if not path.is_file():
                missing.append(artifact.id)
            elif _sha256(path) != artifact.sha256:
                corrupt.append(artifact.id)
        public_root = self.store.export_root / "public"
        expected_public = [public_root / "catalog.json", public_root / "sitemap.xml"]
        for report in self.store.list_reports():
            expected_public.extend(
                [public_root / f"{report.slug}.json", public_root / f"{report.slug}.html"]
            )
        missing_public = [str(path) for path in expected_public if not path.is_file()]
        targets = self.store.list_targets()
        offline_targets = [item.id for item in targets if item.status != "online"]
        result = {
            "ok": integrity == "ok" and not missing and not corrupt and not missing_public,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "database_integrity": integrity,
            "counts": self.store.counts(),
            "artifacts_checked": len(artifacts),
            "missing_artifacts": missing,
            "corrupt_artifacts": corrupt,
            "missing_public_outputs": missing_public,
            "targets_registered": len(targets),
            "offline_targets": offline_targets,
            "warnings": (
                ["some registered targets are offline; this does not invalidate stored content"]
                if offline_targets
                else []
            ),
        }
        if write:
            (self.store.export_root / "monitor.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return result

    def backup(self, destination_root: Path | None = None) -> dict[str, Any]:
        root = (destination_root or (self.store.root / "backups")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = root / f"{stamp}-{uuid.uuid4().hex[:8]}"
        destination.mkdir(parents=False, exist_ok=False)
        db_destination = destination / "observatory.sqlite3"
        with sqlite3.connect(self.store.db_path) as source, sqlite3.connect(db_destination) as target:
            source.backup(target)
        if self.store.artifact_root.is_dir():
            shutil.copytree(self.store.artifact_root, destination / "artifacts")
        if self.store.export_root.is_dir():
            shutil.copytree(self.store.export_root, destination / "exports")
        files = []
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                files.append(
                    {
                        "path": str(path.relative_to(destination)).replace("\\", "/"),
                        "size": path.stat().st_size,
                        "sha256": _sha256(path),
                    }
                )
        manifest = {
            "schema": "game-observatory.backup.v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_root": str(self.store.root),
            "schema_version": 3,
            "files": files,
        }
        manifest_path = destination / "backup.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        verification = self.verify_backup(destination)
        return {
            "ok": verification["ok"],
            "path": str(destination),
            "files": len(files),
            "verification": verification,
        }

    @staticmethod
    def verify_backup(destination: Path) -> dict[str, Any]:
        destination = destination.resolve()
        manifest_path = destination / "backup.json"
        if not manifest_path.is_file():
            return {"ok": False, "error": "backup manifest is missing"}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": f"invalid backup manifest: {exc}"}
        missing: list[str] = []
        corrupt: list[str] = []
        for item in manifest.get("files", []):
            path = (destination / str(item["path"])).resolve()
            try:
                path.relative_to(destination)
            except ValueError:
                corrupt.append(str(item["path"]))
                continue
            if not path.is_file():
                missing.append(str(item["path"]))
            elif _sha256(path) != item.get("sha256"):
                corrupt.append(str(item["path"]))
        db_path = destination / "observatory.sqlite3"
        integrity = "missing"
        if db_path.is_file():
            try:
                with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                    integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            except sqlite3.Error as exc:
                integrity = f"error: {exc}"
        return {
            "ok": not missing and not corrupt and integrity == "ok",
            "database_integrity": integrity,
            "files_checked": len(manifest.get("files", [])),
            "missing": missing,
            "corrupt": corrupt,
        }