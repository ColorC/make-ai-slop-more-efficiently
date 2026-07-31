"""Content-addressed binary storage for canonical Game Observatory artifacts.

The artifact row remains the stable public identity.  Binary bytes are stored once
under their SHA-256, while the historical path is retained as a compatibility alias.
This module never removes a source file; destructive cleanup is a separately reviewed
operation after a dry-run and recovery drill.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CASObjectV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(default="game-observatory.cas-object.v1", alias="schema")
    sha256: str = Field(min_length=64, max_length=64)
    path: str = Field(min_length=1)
    bytes: int = Field(ge=0)
    created: bool


class CASMigrationPreviewV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(
        default="game-observatory.cas-migration-preview.v1",
        alias="schema",
    )
    artifact_count: int = Field(ge=0)
    readable_artifact_count: int = Field(ge=0)
    missing_artifact_count: int = Field(ge=0)
    hash_mismatch_count: int = Field(ge=0)
    unique_object_count: int = Field(ge=0)
    source_bytes: int = Field(ge=0)
    unique_bytes: int = Field(ge=0)
    reclaimable_bytes: int = Field(ge=0)
    missing_artifact_ids: tuple[str, ...] = ()
    hash_mismatch_artifact_ids: tuple[str, ...] = ()


class ContentAddressedObjectStore:
    """Immutable SHA-256 object store with atomic materialization."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.object_root = self.root / "sha256"
        self.link_shard_root = self.root / "hardlink-shards" / "sha256"
        self.object_root.mkdir(parents=True, exist_ok=True)
        self.link_shard_root.mkdir(parents=True, exist_ok=True)
        self._verified_object_stats: dict[str, tuple[int, int]] = {}

    def _verify_object(self, path: Path, sha256: str) -> None:
        stat = path.stat()
        fingerprint = (stat.st_size, stat.st_mtime_ns)
        if self._verified_object_stats.get(sha256) == fingerprint:
            return
        if sha256_file(path) != sha256:
            raise ValueError(f"CAS object is corrupt: {path}")
        self._verified_object_stats[sha256] = fingerprint

    def object_path(self, sha256: str) -> Path:
        normalized = sha256.lower()
        if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
            raise ValueError("CAS object id must be a lowercase SHA-256")
        return self.object_root / normalized[:2] / normalized

    def put_file(self, source: Path, *, expected_sha256: str | None = None) -> CASObjectV1:
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        digest = sha256_file(source)
        if expected_sha256 is not None and digest != expected_sha256.lower():
            raise ValueError(f"artifact content hash changed: {source}")
        destination = self.object_path(digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        created = False
        if destination.exists():
            self._verify_object(destination, digest)
        else:
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
            try:
                shutil.copyfile(source, temporary)
                if sha256_file(temporary) != digest:
                    raise ValueError("CAS temporary copy failed its content hash")
                os.replace(temporary, destination)
                created = True
            finally:
                temporary.unlink(missing_ok=True)
        self._verified_object_stats[digest] = (
            destination.stat().st_size,
            destination.stat().st_mtime_ns,
        )
        return CASObjectV1(
            sha256=digest,
            path=str(destination),
            bytes=destination.stat().st_size,
            created=created,
        )

    def resolve(self, sha256: str, *, verify: bool = True) -> Path:
        path = self.object_path(sha256)
        if not path.is_file():
            raise FileNotFoundError(path)
        if verify:
            self._verify_object(path, sha256.lower())
        return path

    def replace_with_compatibility_link(
        self,
        source: Path,
        *,
        expected_sha256: str,
    ) -> bool:
        """Atomically make an in-volume compatibility path a hardlink to CAS.

        The public artifact path remains readable and unchanged.  ``False`` means
        the path was already linked to the object; no bytes were rewritten.
        """

        source = source.resolve()
        self.resolve(expected_sha256)
        if self.is_compatibility_link(source, expected_sha256):
            return False
        if sha256_file(source) != expected_sha256.lower():
            raise ValueError(f"compatibility source changed before relink: {source}")
        temporary = source.with_name(f".{source.name}.{uuid.uuid4().hex}.cas-link.tmp")
        try:
            anchor = self._link_new_path(temporary, expected_sha256)
            if not os.path.samefile(temporary, anchor):
                raise ValueError("temporary CAS compatibility link is not its anchor")
            os.replace(temporary, source)
        finally:
            temporary.unlink(missing_ok=True)
        if not self.is_compatibility_link(source, expected_sha256):
            raise ValueError(f"CAS compatibility relink did not persist: {source}")
        return True

    def materialize_compatibility_link(
        self,
        target: Path,
        *,
        expected_sha256: str,
    ) -> Path:
        """Create a new compatibility path, sharding anchors at link-count limits."""

        target = target.resolve()
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.cas-link.tmp")
        try:
            anchor = self._link_new_path(temporary, expected_sha256)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        if not self.is_compatibility_link(target, expected_sha256):
            raise ValueError(f"materialized compatibility path is not linked: {target}")
        return anchor

    def _shard_directory(self, sha256: str) -> Path:
        normalized = sha256.lower()
        self.object_path(normalized)
        return self.link_shard_root / normalized[:2] / normalized

    def compatibility_anchors(self, sha256: str) -> list[Path]:
        canonical = self.resolve(sha256, verify=False)
        shard_directory = self._shard_directory(sha256)
        shards = (
            sorted(path for path in shard_directory.iterdir() if path.is_file())
            if shard_directory.is_dir()
            else []
        )
        return [canonical, *shards]

    def is_compatibility_link(self, path: Path, sha256: str) -> bool:
        path = path.resolve()
        if not path.is_file():
            return False
        for anchor in self.compatibility_anchors(sha256):
            try:
                if os.path.samefile(path, anchor):
                    return True
            except OSError:
                continue
        return False

    @staticmethod
    def _is_link_limit(exc: OSError) -> bool:
        return exc.errno == errno.EMLINK or getattr(exc, "winerror", None) == 1142

    def _create_link_shard(self, sha256: str) -> Path:
        canonical = self.resolve(sha256)
        directory = self._shard_directory(sha256)
        directory.mkdir(parents=True, exist_ok=True)
        index = 1
        while (directory / f"anchor-{index:06d}").exists():
            index += 1
        destination = directory / f"anchor-{index:06d}"
        temporary = directory / f".{destination.name}.{uuid.uuid4().hex}.tmp"
        try:
            shutil.copyfile(canonical, temporary)
            if sha256_file(temporary) != sha256.lower():
                raise ValueError("CAS hardlink shard failed its content hash")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def _link_new_path(self, target: Path, sha256: str) -> Path:
        for anchor in self.compatibility_anchors(sha256):
            try:
                os.link(anchor, target)
                return anchor
            except OSError as exc:
                if not self._is_link_limit(exc):
                    raise
        anchor = self._create_link_shard(sha256)
        os.link(anchor, target)
        return anchor

    def restore_independent_compatibility_copy(
        self,
        source: Path,
        *,
        expected_sha256: str,
    ) -> bool:
        """Atomically restore one compatibility path as an independent copy."""

        source = source.resolve()
        self.resolve(expected_sha256)
        if not source.is_file():
            raise FileNotFoundError(source)
        if not self.is_compatibility_link(source, expected_sha256):
            if sha256_file(source) != expected_sha256.lower():
                raise ValueError(f"independent compatibility copy is corrupt: {source}")
            return False
        temporary = source.with_name(f".{source.name}.{uuid.uuid4().hex}.cas-copy.tmp")
        try:
            shutil.copyfile(self.resolve(expected_sha256), temporary)
            if sha256_file(temporary) != expected_sha256.lower():
                raise ValueError("temporary compatibility copy failed its hash")
            os.replace(temporary, source)
        finally:
            temporary.unlink(missing_ok=True)
        if self.is_compatibility_link(source, expected_sha256):
            raise ValueError(f"compatibility rollback remained hardlinked: {source}")
        return True

    @staticmethod
    def preview(artifacts: list[object], *, canonical_root: Path) -> CASMigrationPreviewV1:
        readable = 0
        missing: list[str] = []
        mismatched: list[str] = []
        source_bytes = 0
        unique: dict[str, int] = {}
        for artifact in artifacts:
            artifact_id = str(getattr(artifact, "id"))
            expected = str(getattr(artifact, "sha256")).lower()
            path = Path(str(getattr(artifact, "path")))
            if not path.is_absolute():
                path = canonical_root / path
            if not path.is_file():
                missing.append(artifact_id)
                continue
            size = path.stat().st_size
            actual = sha256_file(path)
            if actual != expected:
                mismatched.append(artifact_id)
                continue
            readable += 1
            source_bytes += size
            unique.setdefault(actual, size)
        unique_bytes = sum(unique.values())
        return CASMigrationPreviewV1(
            artifact_count=len(artifacts),
            readable_artifact_count=readable,
            missing_artifact_count=len(missing),
            hash_mismatch_count=len(mismatched),
            unique_object_count=len(unique),
            source_bytes=source_bytes,
            unique_bytes=unique_bytes,
            reclaimable_bytes=max(0, source_bytes - unique_bytes),
            missing_artifact_ids=tuple(sorted(missing)),
            hash_mismatch_artifact_ids=tuple(sorted(mismatched)),
        )


class CASMigrationManager:
    """Journalled historical migration while retaining stable artifact paths."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.storage_root = (store.export_root / "storage").resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)

    def _source(self, artifact: object) -> Path:
        path = Path(str(getattr(artifact, "path")))
        return path.resolve() if path.is_absolute() else (self.store.root / path).resolve()

    def _is_internal_compatibility_path(self, path: Path) -> bool:
        try:
            path.relative_to(self.store.root)
            path.relative_to(self.store.cas.root)
        except ValueError:
            try:
                path.relative_to(self.store.root)
            except ValueError:
                return False
            return True
        return False

    @staticmethod
    def _append_journal(path: Path, payload: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def apply(self, artifacts: Iterable[object] | None = None) -> dict[str, Any]:
        selected = list(artifacts if artifacts is not None else self.store.list_artifacts())
        preview = ContentAddressedObjectStore.preview(
            selected,
            canonical_root=self.store.root,
        )
        if preview.missing_artifact_count or preview.hash_mismatch_count:
            raise ValueError("CAS migration preflight contains missing or hash-mismatched artifacts")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        migration_id = f"cas-migration-{stamp}-{uuid.uuid4().hex[:8]}"
        journal_path = self.storage_root / f"{migration_id}.journal.jsonl"
        receipt_path = self.storage_root / f"{migration_id}.json"
        entries: list[dict[str, Any]] = []
        relinked = 0
        retained_external = 0
        reclaimed_bytes = 0
        for artifact in selected:
            artifact_id = str(getattr(artifact, "id"))
            expected = str(getattr(artifact, "sha256")).lower()
            source = self._source(artifact)
            size = source.stat().st_size
            internal = self._is_internal_compatibility_path(source)
            self._append_journal(
                journal_path,
                {
                    "event": "before",
                    "artifact_id": artifact_id,
                    "source_path": str(source),
                    "sha256": expected,
                    "bytes": size,
                    "internal": internal,
                },
            )
            cas_object = self.store.cas.put_file(source, expected_sha256=expected)
            self.store.register_artifact_cas_ref(artifact, cas_object)
            mode = "retained_external"
            changed = False
            if internal:
                changed = self.store.cas.replace_with_compatibility_link(
                    source,
                    expected_sha256=expected,
                )
                mode = "hardlink"
                if changed:
                    relinked += 1
                    reclaimed_bytes += size
            else:
                retained_external += 1
            verified = (
                sha256_file(source) == expected
                and (not internal or self.store.cas.is_compatibility_link(source, expected))
            )
            if not verified:
                raise ValueError(f"CAS migration verification failed: {artifact_id}")
            entry = {
                "artifact_id": artifact_id,
                "source_path": str(source),
                "cas_path": cas_object.path,
                "sha256": expected,
                "bytes": size,
                "mode": mode,
                "changed": changed,
                "verified": verified,
            }
            entries.append(entry)
            self._append_journal(journal_path, {"event": "after", **entry})
        verification = self.verify(selected)
        receipt = {
            "schema": "game-observatory.cas-migration-receipt.v1",
            "id": migration_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ok": verification["ok"],
            "preview": preview.model_dump(mode="json", by_alias=True),
            "artifact_count": len(selected),
            "relinked_artifact_count": relinked,
            "retained_external_artifact_count": retained_external,
            "source_allocations_relinked_bytes": reclaimed_bytes,
            "reclaimed_bytes": preview.reclaimable_bytes,
            "estimated_net_reclaimable_bytes": preview.reclaimable_bytes,
            "journal_path": str(journal_path),
            "entries": entries,
            "verification": verification,
        }
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.storage_root / "cas-migration-latest.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        receipt["receipt_path"] = str(receipt_path)
        return receipt

    def verify(self, artifacts: Iterable[object] | None = None) -> dict[str, Any]:
        selected = list(artifacts if artifacts is not None else self.store.list_artifacts())
        failures: list[dict[str, str]] = []
        objects: set[str] = set()
        hardlinked = 0
        for artifact in selected:
            artifact_id = str(getattr(artifact, "id"))
            expected = str(getattr(artifact, "sha256")).lower()
            source = self._source(artifact)
            ref = self.store.get_artifact_cas_ref(artifact_id)
            try:
                if ref is None or str(ref["sha256"]) != expected:
                    raise ValueError("missing_or_wrong_cas_ref")
                if self._is_internal_compatibility_path(source):
                    # Verify the immutable object once per hash, then prove each
                    # compatibility path is the same NTFS file.  Re-hashing every
                    # duplicate path makes routine health checks needlessly linear
                    # in duplicate bytes instead of unique bytes.
                    self.store.cas.resolve(expected)
                    if not source.is_file():
                        raise ValueError("compatibility_path_missing_or_corrupt")
                    if not self.store.cas.is_compatibility_link(source, expected):
                        raise ValueError("internal_compatibility_path_not_hardlinked")
                    hardlinked += 1
                elif not source.is_file() or sha256_file(source) != expected:
                    raise ValueError("compatibility_path_missing_or_corrupt")
                objects.add(expected)
            except (OSError, ValueError) as exc:
                failures.append({"artifact_id": artifact_id, "reason": str(exc)})
        return {
            "ok": not failures,
            "artifact_count": len(selected),
            "unique_object_count": len(objects),
            "hardlinked_artifact_count": hardlinked,
            "failures": failures,
        }

    def reconcile_receipt(
        self,
        receipt: dict[str, Any],
        *,
        receipt_path: Path,
    ) -> dict[str, Any]:
        """Issue an additive correction without rewriting an immutable migration receipt."""
        receipt_path = receipt_path.resolve()
        preview = receipt.get("preview", {})
        net_reclaimable = int(preview.get("reclaimable_bytes", 0))
        legacy_value = int(receipt.get("reclaimed_bytes", 0))
        source_allocations = int(
            receipt.get(
                "source_allocations_relinked_bytes",
                legacy_value if legacy_value != net_reclaimable else 0,
            )
        )
        reconciliation = {
            "schema": "game-observatory.cas-migration-reconciliation.v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ok": bool(receipt.get("ok")) and bool(receipt.get("verification", {}).get("ok")),
            "source_receipt_id": str(receipt.get("id", "")),
            "source_receipt_path": str(receipt_path),
            "source_receipt_sha256": sha256_file(receipt_path),
            "immutable_source_preserved": True,
            "legacy_reclaimed_bytes": legacy_value,
            "legacy_field_interpretation": (
                "source_allocations_relinked_bytes"
                if legacy_value != net_reclaimable
                else "estimated_net_reclaimable_bytes"
            ),
            "source_allocations_relinked_bytes": source_allocations,
            "estimated_net_reclaimable_bytes": net_reclaimable,
            "artifact_count": int(receipt.get("artifact_count", 0)),
            "unique_object_count": int(preview.get("unique_object_count", 0)),
            "verification": receipt.get("verification", {}),
        }
        output = receipt_path.with_name(f"{receipt_path.stem}.reconciliation.json")
        output.write_text(
            json.dumps(reconciliation, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        reconciliation["reconciliation_path"] = str(output)
        return reconciliation

    def rollback_drill(self, receipt: dict[str, Any], *, sample_size: int = 3) -> dict[str, Any]:
        candidates = [entry for entry in receipt.get("entries", []) if entry["mode"] == "hardlink"]
        sampled = candidates[: max(1, sample_size)]
        results: list[dict[str, Any]] = []
        for entry in sampled:
            source = Path(entry["source_path"])
            expected = str(entry["sha256"])
            restored = self.store.cas.restore_independent_compatibility_copy(
                source,
                expected_sha256=expected,
            )
            independent_ok = (
                sha256_file(source) == expected
                and not self.store.cas.is_compatibility_link(source, expected)
            )
            relinked = self.store.cas.replace_with_compatibility_link(
                source,
                expected_sha256=expected,
            )
            final_ok = self.store.cas.is_compatibility_link(source, expected)
            results.append(
                {
                    "artifact_id": entry["artifact_id"],
                    "restored": restored,
                    "independent_copy_verified": independent_ok,
                    "relinked": relinked,
                    "final_hardlink_verified": final_ok,
                }
            )
        return {
            "schema": "game-observatory.cas-rollback-drill.v1",
            "ok": bool(sampled) and all(
                item["independent_copy_verified"] and item["final_hardlink_verified"]
                for item in results
            ),
            "sample_count": len(sampled),
            "results": results,
        }
