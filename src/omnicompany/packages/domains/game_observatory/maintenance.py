from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .media_validation import artifact_file_issues
from .store import SCHEMA_VERSION, ObservatoryStore


DURABLE_DIRECTORIES = (
    "artifacts",
    "exports",
    "fixtures",
    "benchmarks",
    "captures",
    "drafts",
    "reviews",
    "saturation",
    "adjudications",
    "proofs",
)

# Binary artifacts are recovered from the immutable CAS pack.  These directories
# contain authored or generated metadata that is not represented by artifact rows.
BACKUP_METADATA_DIRECTORIES = tuple(
    name for name in DURABLE_DIRECTORIES if name != "artifacts"
)


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
        with closing(sqlite3.connect(self.store.db_path)) as conn, conn:
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        missing: list[str] = []
        corrupt: list[str] = []
        invalid_visuals: list[str] = []
        artifacts = self.store.list_artifacts()
        for artifact in artifacts:
            path = Path(artifact.path)
            if not path.is_file():
                missing.append(artifact.id)
                continue
            verified = False
            cas_corrupt = False
            ref = self.store.get_artifact_cas_ref(artifact.id)
            if ref is not None and str(ref["sha256"]) == artifact.sha256:
                try:
                    self.store.cas.resolve(artifact.sha256)
                    verified = self.store.cas.is_compatibility_link(path, artifact.sha256)
                except (OSError, ValueError):
                    cas_corrupt = True
            if cas_corrupt or (not verified and _sha256(path) != artifact.sha256):
                corrupt.append(artifact.id)
            elif artifact.metadata.get("public") is True:
                invalid_visuals.extend(artifact_file_issues(artifact))
        public_root = self.store.export_root / "public"
        expected_public = [public_root / "catalog.json", public_root / "sitemap.xml"]
        for report in self.store.list_reports():
            expected_public.extend(
                [
                    public_root / f"{report.slug}.json",
                    public_root / f"{report.slug}.html",
                    public_root / f"{report.slug}.core-loop.svg",
                    public_root / f"{report.slug}.navigation.svg",
                    public_root / f"{report.slug}.interaction.svg",
                ]
            )
        missing_public = [str(path) for path in expected_public if not path.is_file()]
        targets = self.store.list_targets()
        offline_targets = [item.id for item in targets if item.status != "online"]
        result = {
            "ok": (
                integrity == "ok"
                and not missing
                and not corrupt
                and not invalid_visuals
                and not missing_public
            ),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "database_integrity": integrity,
            "counts": self.store.counts(),
            "artifacts_checked": len(artifacts),
            "missing_artifacts": missing,
            "corrupt_artifacts": corrupt,
            "invalid_visual_artifacts": invalid_visuals,
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

    def backup(
        self,
        destination_root: Path | None = None,
        *,
        backup_name: str | None = None,
    ) -> dict[str, Any]:
        root = (destination_root or (self.store.root / "backups")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if backup_name is not None:
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", backup_name) is None:
                raise ValueError("backup_name must be a safe single path segment")
            destination = root / backup_name
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            destination = root / f"{stamp}-{uuid.uuid4().hex[:8]}"
        destination.mkdir(parents=False, exist_ok=False)
        db_destination = destination / "observatory.sqlite3"
        with (
            closing(sqlite3.connect(self.store.db_path)) as source,
            closing(sqlite3.connect(db_destination)) as target,
        ):
            source.backup(target)
        # A standalone backup must not depend on a sidecar WAL that recovery does
        # not copy.  Checkpoint and switch the snapshot to a single-file journal.
        with closing(sqlite3.connect(db_destination)) as snapshot, snapshot:
            snapshot.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            snapshot.execute("PRAGMA journal_mode=DELETE")
        for name in BACKUP_METADATA_DIRECTORIES:
            source_dir = self.store.root / name
            if source_dir.is_dir():
                shutil.copytree(source_dir, destination / name)
        # Defensive capture for records created by an integration that points at
        # evidence outside the canonical artifact root.
        external_updates: list[tuple[str, str]] = []
        artifact_destination = destination / "artifacts"
        artifact_destination.mkdir(parents=True, exist_ok=True)
        for artifact in self.store.list_artifacts():
            source_path = Path(artifact.path).resolve()
            try:
                source_path.relative_to(self.store.artifact_root)
                continue
            except ValueError:
                pass
            if not source_path.is_file():
                continue
            target_name = f"{artifact.id}{source_path.suffix.lower()}"
            shutil.copy2(source_path, artifact_destination / target_name)
            external_updates.append((artifact.id, str(self.store.artifact_root / target_name)))
        if external_updates:
            with closing(sqlite3.connect(db_destination)) as conn, conn:
                conn.row_factory = sqlite3.Row
                for artifact_id, virtual_path in external_updates:
                    row = conn.execute(
                        "SELECT body_json FROM artifacts WHERE id=?", (artifact_id,)
                    ).fetchone()
                    if not row:
                        continue
                    payload = json.loads(row["body_json"])
                    payload["path"] = virtual_path
                    conn.execute(
                        "UPDATE artifacts SET path=?,body_json=? WHERE id=?",
                        (virtual_path, json.dumps(payload, ensure_ascii=False), artifact_id),
                    )
        cas_objects: dict[str, dict[str, Any]] = {}
        for artifact in self.store.list_artifacts():
            ref = self.store.get_artifact_cas_ref(artifact.id)
            if ref is None:
                raise ValueError(f"artifact is not migrated to CAS: {artifact.id}")
            sha256 = str(ref["sha256"])
            if sha256 in cas_objects:
                continue
            source_object = self.store.cas.resolve(sha256)
            relative_object = Path("cas") / "sha256" / sha256[:2] / sha256
            backup_object = destination / relative_object
            backup_object.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source_object, backup_object)
                storage_mode = "hardlink"
            except OSError:
                shutil.copy2(source_object, backup_object)
                storage_mode = "copy"
            cas_objects[sha256] = {
                "sha256": sha256,
                "path": str(relative_object).replace("\\", "/"),
                "bytes": backup_object.stat().st_size,
                "storage_mode": storage_mode,
            }
        files = []
        for path in sorted(destination.rglob("*")):
            if path.is_file() and not path.name.endswith(("-wal", "-shm")):
                try:
                    path.relative_to(destination / "cas" / "sha256")
                    file_sha256 = path.name
                except ValueError:
                    file_sha256 = _sha256(path)
                files.append(
                    {
                        "path": str(path.relative_to(destination)).replace("\\", "/"),
                        "size": path.stat().st_size,
                        "sha256": file_sha256,
                    }
                )
        manifest = {
            "schema": "game-observatory.backup.v2",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_root": str(self.store.root),
            "schema_version": SCHEMA_VERSION,
            "artifact_storage": "content_addressed",
            "cas_objects": sorted(cas_objects.values(), key=lambda item: item["sha256"]),
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
                with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
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

    @staticmethod
    def _rebase_restored_db(db_path: Path, source_root: Path, destination: Path) -> None:
        source_text = str(source_root.resolve())
        destination_text = str(destination.resolve())

        def rewrite(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: rewrite(item) for key, item in value.items()}
            if isinstance(value, list):
                return [rewrite(item) for item in value]
            if isinstance(value, str) and value.lower().startswith(source_text.lower()):
                suffix = value[len(source_text):].lstrip("\\/")
                return str(destination / suffix)
            return value

        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.row_factory = sqlite3.Row
            for table, keys in (
                ("artifacts", ("id",)),
                ("reports", ("id",)),
                ("report_revisions", ("report_id", "revision")),
            ):
                rows = conn.execute(f"SELECT *, body_json FROM {table}").fetchall()
                for row in rows:
                    payload = rewrite(json.loads(row["body_json"]))
                    where = " AND ".join(f"{key}=?" for key in keys)
                    params = [row[key] for key in keys]
                    if table == "artifacts":
                        conn.execute(
                            f"UPDATE {table} SET path=?, body_json=? WHERE {where}",
                            [payload["path"], json.dumps(payload, ensure_ascii=False), *params],
                        )
                    else:
                        conn.execute(
                            f"UPDATE {table} SET body_json=? WHERE {where}",
                            [json.dumps(payload, ensure_ascii=False), *params],
                        )
            cas_rows = conn.execute("SELECT sha256 FROM cas_objects").fetchall()
            for row in cas_rows:
                sha256 = str(row["sha256"])
                conn.execute(
                    "UPDATE cas_objects SET path=? WHERE sha256=?",
                    (str(destination / "cas" / "sha256" / sha256[:2] / sha256), sha256),
                )
            ref_rows = conn.execute(
                "SELECT artifact_id,source_path FROM artifact_cas_refs"
            ).fetchall()
            for row in ref_rows:
                conn.execute(
                    "UPDATE artifact_cas_refs SET source_path=? WHERE artifact_id=?",
                    (rewrite(str(row["source_path"])), str(row["artifact_id"])),
                )

    def recovery_drill(
        self,
        backup_dir: Path,
        destination_root: Path | None = None,
        *,
        retain_destination: bool = True,
    ) -> dict[str, Any]:
        backup_dir = backup_dir.resolve()
        verification = self.verify_backup(backup_dir)
        if not verification["ok"]:
            return {"ok": False, "stage": "verify", "verification": verification}
        manifest = json.loads((backup_dir / "backup.json").read_text(encoding="utf-8"))
        root = (destination_root or (self.store.root / "recovery-drills")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        destination = root / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        destination.mkdir(parents=False, exist_ok=False)
        shutil.copy2(backup_dir / "observatory.sqlite3", destination / "observatory.sqlite3")
        for name in BACKUP_METADATA_DIRECTORIES:
            source = backup_dir / name
            if source.is_dir():
                shutil.copytree(source, destination / name)
        backup_cas = backup_dir / "cas"
        if not backup_cas.is_dir():
            return {"ok": False, "stage": "cas", "error": "backup CAS pack is missing"}
        for source in sorted(backup_cas.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(backup_cas)
            target = destination / "cas" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
        self._rebase_restored_db(
            destination / "observatory.sqlite3",
            Path(manifest["source_root"]),
            destination,
        )
        restored = ObservatoryStore(destination)
        materialized = 0
        for artifact in restored.list_artifacts():
            ref = restored.get_artifact_cas_ref(artifact.id)
            if ref is None:
                raise ValueError(f"restored artifact has no CAS ref: {artifact.id}")
            artifact_path = Path(artifact.path).resolve()
            try:
                artifact_path.relative_to(destination)
            except ValueError as exc:
                raise ValueError(
                    f"restored artifact path escapes drill root: {artifact.id}"
                ) from exc
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            if artifact_path.exists():
                artifact_path.unlink()
            restored.cas.materialize_compatibility_link(
                artifact_path,
                expected_sha256=str(ref["sha256"]),
            )
            materialized += 1
        from .compiler import SemanticReportCompiler

        SemanticReportCompiler(restored.export_root / "public").compile(restored.list_reports())
        monitor = FacilityMaintenance(restored).monitor(write=False)
        counts_match = restored.counts()["reports"] == self.store.counts()["reports"]
        result = {
            "schema": "game-observatory.recovery-drill.v1",
            "ok": monitor["ok"] and counts_match,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "backup": str(backup_dir),
            "destination": str(destination),
            "counts_match": counts_match,
            "restored_counts": restored.counts(),
            "materialized_artifacts": materialized,
            "monitor": monitor,
        }
        if not retain_destination:
            for attempt in range(10):
                try:
                    shutil.rmtree(destination)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    # SQLite/AV handles can be released a few milliseconds late on Windows.
                    time.sleep(0.1)
            result["destination_retained"] = False
            result["destination_removed"] = not destination.exists()
        else:
            result["destination_retained"] = True
            result["destination_removed"] = False
        (self.store.export_root / "recovery-drill.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result
