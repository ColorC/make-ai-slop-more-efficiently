"""Content-addressed Python runtime snapshots for detached game drives."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from .runtime_version_fence import (
    PlayerRuntimeContract,
    RuntimeContractProbeError,
    probe_runtime_contract,
)


SNAPSHOT_MANIFEST_SCHEMA = "game-observatory.ai-player.runtime-snapshot-manifest.v1"
_INCLUDED_SUFFIXES = frozenset(
    {
        ".css",
        ".example",
        ".json",
        ".jsonc",
        ".md",
        ".py",
        ".pyi",
        ".sql",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_PRUNED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)


class RuntimeSnapshotError(RuntimeError):
    """Raised when an immutable runtime snapshot cannot be materialized safely."""


@dataclass(frozen=True)
class RuntimeSnapshot:
    source_manifest_sha256: str
    repository_root: Path
    manifest_path: Path
    reused: bool
    file_count: int
    size_bytes: int


@dataclass(frozen=True)
class RuntimeSnapshotIntegrity:
    source_manifest_sha256: str
    file_count: int
    size_bytes: int


def find_runtime_repository_root(start: Path) -> Path:
    candidate = start.resolve()
    search = [candidate, *candidate.parents]
    for root in search:
        if (root / "pyproject.toml").is_file() and (
            root / "src" / "omnicompany"
        ).is_dir():
            return root
    raise RuntimeSnapshotError(f"cannot find omnicompany repository above {candidate}")


def _source_files(repository_root: Path) -> Iterator[Path]:
    roots = [
        (repository_root / "src" / "omnicompany", False),
        (
            repository_root
            / "src"
            / "omnicompany"
            / "packages"
            / "domains"
            / "game_observatory",
            True,
        ),
        (repository_root / "config", True),
        (repository_root / ".agents" / "skills", True),
    ]
    yielded: set[Path] = set()
    for selected_root, include_all in roots:
        if not selected_root.is_dir():
            continue
        for current_root, directories, filenames in os.walk(selected_root):
            directories[:] = sorted(
                name for name in directories if name not in _PRUNED_DIRECTORY_NAMES
            )
            current = Path(current_root)
            for filename in sorted(filenames):
                path = current / filename
                if (
                    (include_all or path.suffix.lower() in _INCLUDED_SUFFIXES)
                    and path not in yielded
                ):
                    yielded.add(path)
                    yield path
    for path in (repository_root / "pyproject.toml", repository_root / "docs" / "archmap.yaml"):
        if path.is_file() and path not in yielded:
            yield path


def _manifest_digest(entries: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        canonical = json.dumps(
            entry,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        digest.update(len(canonical).to_bytes(8, "big"))
        digest.update(canonical)
    return digest.hexdigest()


def _source_manifest(repository_root: Path) -> tuple[list[dict[str, Any]], str]:
    entries: list[dict[str, Any]] = []
    for path in _source_files(repository_root):
        relative = path.relative_to(repository_root).as_posix()
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise RuntimeSnapshotError(f"cannot read runtime source {path}: {exc}") from exc
        sha256 = hashlib.sha256(payload).hexdigest()
        entry = {"path": relative, "sha256": sha256, "size_bytes": len(payload)}
        entries.append(entry)
    if not entries:
        raise RuntimeSnapshotError("omnicompany runtime snapshot would be empty")
    return entries, _manifest_digest(entries)


def _read_existing_manifest(path: Path, expected_digest: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != SNAPSHOT_MANIFEST_SCHEMA:
        return None
    if payload.get("source_manifest_sha256") != expected_digest:
        return None
    return payload


def _remove_staging(path: Path, snapshots_root: Path) -> None:
    resolved = path.resolve()
    root = snapshots_root.resolve()
    if resolved.parent != root or not resolved.name.startswith(".staging-"):
        raise RuntimeSnapshotError(f"refusing to remove unsafe snapshot path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _validated_manifest_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_entries = payload.get("files")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise RuntimeSnapshotError("runtime snapshot manifest has no files")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise RuntimeSnapshotError("runtime snapshot manifest file entry is invalid")
        relative = str(raw.get("path") or "")
        relative_path = Path(relative)
        sha256 = str(raw.get("sha256") or "")
        size = raw.get("size_bytes")
        if (
            not relative
            or relative_path.is_absolute()
            or ".." in relative_path.parts
            or relative_path.as_posix() != relative
            or relative in seen
            or len(sha256) != 64
            or not isinstance(size, int)
            or size < 0
        ):
            raise RuntimeSnapshotError(
                f"runtime snapshot manifest entry is unsafe: {relative!r}"
            )
        seen.add(relative)
        entries.append({"path": relative, "sha256": sha256, "size_bytes": size})
    return entries


def verify_runtime_snapshot(repository_root: Path) -> RuntimeSnapshotIntegrity:
    """Fail closed on manifest, file-content or runtime-file-set drift."""

    target = repository_root.resolve()
    manifest_path = target / "snapshot-manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeSnapshotError(f"cannot read runtime snapshot manifest: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SNAPSHOT_MANIFEST_SCHEMA:
        raise RuntimeSnapshotError("runtime snapshot manifest schema is invalid")
    entries = _validated_manifest_entries(payload)
    digest = _manifest_digest(entries)
    if (
        payload.get("source_manifest_sha256") != digest
        or target.name != digest
        or payload.get("file_count") != len(entries)
        or payload.get("size_bytes")
        != sum(int(entry["size_bytes"]) for entry in entries)
    ):
        raise RuntimeSnapshotError("runtime snapshot manifest integrity changed")
    expected_paths = {str(entry["path"]) for entry in entries}
    actual_paths = {
        path.relative_to(target).as_posix() for path in _source_files(target)
    }
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)[:20]
        added = sorted(actual_paths - expected_paths)[:20]
        raise RuntimeSnapshotError(
            f"runtime snapshot file set changed; missing={missing}, added={added}"
        )
    for entry in entries:
        path = target / str(entry["path"])
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise RuntimeSnapshotError(f"cannot verify runtime snapshot file {path}: {exc}") from exc
        if (
            len(payload) != int(entry["size_bytes"])
            or hashlib.sha256(payload).hexdigest() != entry["sha256"]
        ):
            raise RuntimeSnapshotError(f"runtime snapshot file failed integrity check: {path}")
    return RuntimeSnapshotIntegrity(
        source_manifest_sha256=digest,
        file_count=len(entries),
        size_bytes=sum(int(entry["size_bytes"]) for entry in entries),
    )


def _make_snapshot_files_read_only(target: Path, entries: list[dict[str, Any]]) -> None:
    for path in [
        *(target / str(entry["path"]) for entry in entries),
        target / "snapshot-manifest.json",
    ]:
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
            path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        except OSError as exc:
            raise RuntimeSnapshotError(
                f"cannot make runtime snapshot file read-only: {path}: {exc}"
            ) from exc


def _publish_snapshot_directory(
    staging: Path,
    target: Path,
    target_manifest: Path,
    source_digest: str,
    snapshots_root: Path,
    *,
    replace_directory: Callable[[Path, Path], None] = os.replace,
    sleeper: Callable[[float], None] = time.sleep,
    attempts: int = 6,
) -> bool:
    """Publish a completed snapshot, tolerating brief Windows sharing conflicts.

    Returns whether another concurrent publisher already installed the same snapshot.
    """

    if attempts < 1:
        raise ValueError("snapshot publish attempts must be positive")
    for attempt in range(attempts):
        try:
            replace_directory(staging, target)
            return False
        except OSError as exc:
            if _read_existing_manifest(target_manifest, source_digest) is not None:
                _remove_staging(staging, snapshots_root)
                return True
            transient_windows_conflict = isinstance(exc, PermissionError) or getattr(
                exc, "winerror", None
            ) in {5, 32, 33}
            if not transient_windows_conflict or attempt + 1 >= attempts:
                raise
            sleeper(0.05 * (2**attempt))
    raise AssertionError("unreachable snapshot publish retry state")


def prepare_runtime_snapshot(
    repository_root: Path,
    snapshots_root: Path,
    *,
    expected_contract: PlayerRuntimeContract,
) -> RuntimeSnapshot:
    """Create or reuse one immutable source snapshot and verify its contract."""

    repository_root = repository_root.resolve()
    snapshots_root = snapshots_root.resolve()
    snapshots_root.mkdir(parents=True, exist_ok=True)
    entries, source_digest = _source_manifest(repository_root)
    target = snapshots_root / source_digest
    target_manifest = target / "snapshot-manifest.json"
    total_bytes = sum(int(entry["size_bytes"]) for entry in entries)
    existing = _read_existing_manifest(target_manifest, source_digest)
    reused = existing is not None
    if not reused:
        if target.exists():
            raise RuntimeSnapshotError(
                f"runtime snapshot target exists with an invalid manifest: {target}"
            )
        staging = snapshots_root / f".staging-{uuid.uuid4().hex}"
        staging.mkdir(parents=False, exist_ok=False)
        try:
            for entry in entries:
                relative = Path(str(entry["path"]))
                source = repository_root / relative
                destination = staging / relative
                try:
                    payload = source.read_bytes()
                except OSError as exc:
                    raise RuntimeSnapshotError(
                        f"cannot copy runtime source {source}: {exc}"
                    ) from exc
                if (
                    len(payload) != int(entry["size_bytes"])
                    or hashlib.sha256(payload).hexdigest() != entry["sha256"]
                ):
                    raise RuntimeSnapshotError(
                        f"runtime source changed while snapshotting: {source}"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
            _write_json(
                staging / "snapshot-manifest.json",
                {
                    "schema": SNAPSHOT_MANIFEST_SCHEMA,
                    "source_manifest_sha256": source_digest,
                    "file_count": len(entries),
                    "size_bytes": total_bytes,
                    "files": entries,
                },
            )
            reused = _publish_snapshot_directory(
                staging,
                target,
                target_manifest,
                source_digest,
                snapshots_root,
            )
        except Exception:
            if staging.exists():
                _remove_staging(staging, snapshots_root)
            raise
    verify_runtime_snapshot(target)
    _make_snapshot_files_read_only(target, entries)
    try:
        frozen_contract = probe_runtime_contract(target)
    except RuntimeContractProbeError as exc:
        raise RuntimeSnapshotError(f"frozen runtime contract probe failed: {exc}") from exc
    if not expected_contract.semantically_matches(frozen_contract):
        raise RuntimeSnapshotError(
            "live facility changed while its runtime snapshot was being prepared"
        )
    return RuntimeSnapshot(
        source_manifest_sha256=source_digest,
        repository_root=target,
        manifest_path=target_manifest,
        reused=reused,
        file_count=len(entries),
        size_bytes=total_bytes,
    )


__all__ = [
    "RuntimeSnapshot",
    "RuntimeSnapshotError",
    "RuntimeSnapshotIntegrity",
    "SNAPSHOT_MANIFEST_SCHEMA",
    "find_runtime_repository_root",
    "prepare_runtime_snapshot",
    "verify_runtime_snapshot",
]
