"""Cached filesystem quota enforcement for replay runs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from .models import (
    EvictionReport,
    OrphanAdoptionReport,
    RunCountsV1,
    RunIndexEntryV1,
    RunManifestV1,
    utc_now,
)


class ReplayRootForbiddenError(ValueError):
    """Raised when a replay root points into a protected storage tree."""


class ReplayQuotaExceededError(RuntimeError):
    """Raised when active runs leave no safe way to satisfy the quota."""

    def __init__(self, *, usage_bytes: int, incoming_bytes: int, limit_bytes: int) -> None:
        self.usage_bytes = usage_bytes
        self.incoming_bytes = incoming_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"replay quota exceeded: usage={usage_bytes}, incoming={incoming_bytes}, "
            f"limit={limit_bytes}"
        )


def resolve_replay_root(root: Path) -> Path:
    resolved = root.expanduser().resolve(strict=False)
    lowered = [part.casefold() for part in resolved.parts]
    protected = {"cas", "backups", "snapshots"}
    for offset in range(max(0, len(lowered) - 3)):
        if lowered[offset : offset + 3] != ["data", "domains", "game_observatory"]:
            continue
        if offset + 3 < len(lowered) and lowered[offset + 3] in protected:
            raise ReplayRootForbiddenError(f"forbidden replay root: {resolved}")
    return resolved


def directory_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for directory, _subdirectories, filenames in os.walk(path):
        base = Path(directory)
        for filename in filenames:
            try:
                total += (base / filename).stat().st_size
            except FileNotFoundError:
                continue
    return total


def run_directory_manifest_hash(run_dir: Path) -> str:
    files: list[dict[str, str]] = []
    for path in sorted(item for item in run_dir.rglob("*") if item.is_file()):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        files.append(
            {"path": path.relative_to(run_dir).as_posix(), "sha256": digest.hexdigest()}
        )
    encoded = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_bytes(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_write_json(path: Path, value: dict[str, object]) -> None:
    _atomic_write_bytes(path, _json_bytes(value))


def _sized_manifest_payload(
    run_dir: Path, manifest: RunManifestV1
) -> tuple[RunManifestV1, bytes, int]:
    path = run_dir / "run.json"
    old_size = path.stat().st_size if path.exists() else 0
    base_size = directory_size(run_dir) - old_size
    total = manifest.bytes_total
    while True:
        candidate = manifest.model_copy(update={"bytes_total": total})
        payload = _json_bytes(candidate.model_dump(mode="json"))
        calculated = base_size + len(payload)
        if calculated == total:
            return candidate, payload, old_size
        total = calculated


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def _read_ndjson(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    if not path.is_file():
        return records
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
    except OSError:
        return []
    return records


def _frame_recovery_counts(run_dir: Path) -> tuple[int, int]:
    frames_dir = run_dir / "frames"
    physical_ids = (
        {path.stem for path in frames_dir.glob("*.webp") if path.is_file()}
        if frames_dir.is_dir()
        else set()
    )
    final_statuses: dict[str, str] = {}
    for record in _read_ndjson(run_dir / "frames.ndjson"):
        frame_id = record.get("frame_id")
        status = record.get("status")
        if isinstance(frame_id, str) and status in {"pending", "written", "lost"}:
            final_statuses[frame_id] = str(status)

    timeline_ids: set[str] = set()
    for event in _read_ndjson(run_dir / "timeline.ndjson"):
        ref = event.get("ref")
        if event.get("kind") == "frame" and isinstance(ref, str):
            timeline_ids.add(ref)

    all_ids = physical_ids | set(final_statuses) | timeline_ids
    complete_written_ids = {
        frame_id
        for frame_id, status in final_statuses.items()
        if status == "written" and frame_id in physical_ids
    }
    return len(all_ids), len(all_ids - complete_written_ids)


def _actual_counts(run_dir: Path) -> RunCountsV1:
    frames, _lost_frames = _frame_recovery_counts(run_dir)
    return RunCountsV1(
        frames=frames,
        states=_line_count(run_dir / "states.ndjson"),
        bi=_line_count(run_dir / "bi.ndjson"),
        actions=_line_count(run_dir / "actions.ndjson"),
        timeline=_line_count(run_dir / "timeline.ndjson"),
    )


def _latest_file_mtime_ns(run_dir: Path) -> int | None:
    latest: int | None = None
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            modified = path.stat().st_mtime_ns
        except FileNotFoundError:
            continue
        latest = modified if latest is None else max(latest, modified)
    return latest


class QuotaManager:
    """Track replay bytes and evict the oldest closed runs as whole units."""

    def __init__(
        self,
        root: Path,
        limit_bytes: int = 10 * 2**30,
        evict_step_bytes: int = 1 * 2**30,
    ) -> None:
        if limit_bytes < 0:
            raise ValueError("limit_bytes must be non-negative")
        if evict_step_bytes < 0 or evict_step_bytes > limit_bytes:
            raise ValueError("evict_step_bytes must be between zero and limit_bytes")
        self.root = resolve_replay_root(Path(root))
        self.limit_bytes = limit_bytes
        self.evict_step_bytes = evict_step_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "index").mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._usage_cache = self._calculate_usage()
        self._last_gc: str | None = None
        self.ensure_capacity_last_ns = 0
        self.ensure_capacity_count = 0
        self.last_eviction_hash_seconds = 0.0

    @property
    def last_gc(self) -> str | None:
        return self._last_gc

    def _run_directories(self) -> list[Path]:
        directories: list[Path] = []
        if not self.root.exists():
            return directories
        for game_dir in self.root.iterdir():
            if not game_dir.is_dir() or game_dir.name == "index":
                continue
            directories.extend(path for path in game_dir.iterdir() if path.is_dir())
        return directories

    def _calculate_usage(self) -> int:
        return sum(directory_size(run_dir) for run_dir in self._run_directories())

    def usage_bytes(self) -> int:
        with self._lock:
            return self._usage_cache

    def recount(self) -> int:
        with self._lock:
            self._usage_cache = self._calculate_usage()
            return self._usage_cache

    def _record_growth(self, delta_bytes: int) -> None:
        with self._lock:
            self._usage_cache = max(0, self._usage_cache + delta_bytes)

    def _write_adopted_manifest(
        self, run_dir: Path, manifest: RunManifestV1
    ) -> RunManifestV1:
        candidate, payload, old_size = _sized_manifest_payload(run_dir, manifest)
        _atomic_write_bytes(run_dir / "run.json", payload)
        self._usage_cache = max(0, self._usage_cache + len(payload) - old_size)
        return candidate

    def _write_adoption_index(self, run_dir: Path, manifest: RunManifestV1) -> None:
        entry = RunIndexEntryV1.from_manifest(
            manifest,
            run_dir_sha256_manifest=run_directory_manifest_hash(run_dir),
        )
        index_path = self.root / "index" / manifest.game_id / f"{manifest.run_id}.json"
        _atomic_write_json(index_path, entry.model_dump(mode="json"))

    def adopt_orphans(
        self,
        *,
        stale_after_seconds: int = 600,
        active_run_ids: set[str],
        dry: bool = False,
    ) -> OrphanAdoptionReport:
        if stale_after_seconds < 0:
            raise ValueError("stale_after_seconds must be non-negative")
        with self._lock:
            now_ns = time.time_ns()
            candidates: list[tuple[str, str, Path, RunManifestV1, int]] = []
            skipped_recent: list[str] = []
            for run_dir in self._run_directories():
                manifest_path = run_dir / "run.json"
                if not manifest_path.is_file():
                    continue
                try:
                    manifest = RunManifestV1.model_validate_json(
                        manifest_path.read_text("utf-8")
                    )
                except (OSError, ValueError):
                    continue
                if manifest.game_id != run_dir.parent.name or manifest.run_id != run_dir.name:
                    continue
                if manifest.ended_at is not None or manifest.run_id in active_run_ids:
                    continue
                latest_mtime_ns = _latest_file_mtime_ns(run_dir)
                if latest_mtime_ns is None:
                    continue
                stale_seconds = max(0, (now_ns - latest_mtime_ns) // 1_000_000_000)
                if stale_seconds <= stale_after_seconds:
                    skipped_recent.append(manifest.run_id)
                    continue
                candidates.append(
                    (manifest.started_at, manifest.run_id, run_dir, manifest, stale_seconds)
                )

            adopted: list[str] = []
            for _started_at, run_id, run_dir, manifest, stale_seconds in sorted(candidates):
                adopted.append(run_id)
                if dry:
                    continue
                ended_at = utc_now()
                _frames, lost_frames = _frame_recovery_counts(run_dir)
                values = manifest.model_dump(mode="python")
                values.update(
                    {
                        "t_mono_ns": time.monotonic_ns(),
                        "t_wall": ended_at,
                        "ended_at": ended_at,
                        "end_reason": "crashed",
                        "summary": {
                            "adopted_by": "gc",
                            "stale_seconds": stale_seconds,
                            "lost_frames": lost_frames,
                        },
                        "counts": _actual_counts(run_dir),
                    }
                )
                closed = self._write_adopted_manifest(
                    run_dir, RunManifestV1.model_validate(values)
                )
                self._write_adoption_index(run_dir, closed)

            return OrphanAdoptionReport(
                adopted_run_ids=adopted,
                skipped_recent_run_ids=sorted(skipped_recent),
                dry=dry,
            )

    def _candidates(self, active_run_ids: set[str]) -> list[tuple[str, str, Path, RunManifestV1]]:
        candidates: list[tuple[str, str, Path, RunManifestV1]] = []
        for run_dir in self._run_directories():
            manifest_path = run_dir / "run.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = RunManifestV1.model_validate_json(manifest_path.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if manifest.game_id != run_dir.parent.name or manifest.run_id != run_dir.name:
                continue
            if manifest.ended_at is None or manifest.run_id in active_run_ids:
                continue
            candidates.append((manifest.started_at, manifest.run_id, run_dir, manifest))
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates

    def _write_eviction_index(self, run_dir: Path, manifest: RunManifestV1) -> None:
        index_path = self.root / "index" / manifest.game_id / f"{manifest.run_id}.json"
        existing: RunIndexEntryV1 | None = None
        if index_path.is_file():
            try:
                existing = RunIndexEntryV1.model_validate_json(index_path.read_text("utf-8"))
            except (OSError, ValueError):
                existing = None
        reuse_hash = (
            existing is not None
            and existing.game_id == manifest.game_id
            and existing.run_id == manifest.run_id
            and bool(existing.run_dir_sha256_manifest)
            and run_dir.stat().st_mtime_ns <= index_path.stat().st_mtime_ns
        )
        if reuse_hash:
            run_hash = existing.run_dir_sha256_manifest
            self.last_eviction_hash_seconds = 0.0
        else:
            hash_started = time.perf_counter_ns()
            run_hash = run_directory_manifest_hash(run_dir)
            self.last_eviction_hash_seconds = (time.perf_counter_ns() - hash_started) / 1_000_000_000
        entry = RunIndexEntryV1.from_manifest(
            manifest,
            evicted_at=utc_now(),
            evicted_reason="quota",
            run_dir_sha256_manifest=run_hash,
            hash_reused=reuse_hash,
        )
        _atomic_write_json(index_path, entry.model_dump(mode="json"))

    def _evict(
        self,
        incoming_bytes: int,
        active_run_ids: set[str],
        *,
        dry: bool,
        raise_when_exhausted: bool,
    ) -> EvictionReport:
        if incoming_bytes < 0:
            raise ValueError("incoming_bytes must be non-negative")
        before = self._usage_cache
        if before + incoming_bytes <= self.limit_bytes:
            return EvictionReport(
                before_bytes=before,
                after_bytes=before,
                evicted_run_ids=[],
                freed_bytes=0,
                dry=dry,
            )

        target = self.limit_bytes - self.evict_step_bytes
        current = before
        evicted: list[str] = []
        for _started_at, run_id, run_dir, manifest in self._candidates(active_run_ids):
            if current + incoming_bytes <= target:
                break
            run_bytes = directory_size(run_dir)
            if not dry:
                self._write_eviction_index(run_dir, manifest)
                shutil.rmtree(run_dir)
            current = max(0, current - run_bytes)
            evicted.append(run_id)

        if not dry:
            self._usage_cache = current
            if evicted:
                self._last_gc = utc_now()

        if current + incoming_bytes > target and raise_when_exhausted:
            raise ReplayQuotaExceededError(
                usage_bytes=current,
                incoming_bytes=incoming_bytes,
                limit_bytes=self.limit_bytes,
            )

        return EvictionReport(
            before_bytes=before,
            after_bytes=current,
            evicted_run_ids=evicted,
            freed_bytes=before - current,
            dry=dry,
        )

    def ensure_capacity(
        self, incoming_bytes: int, active_run_ids: set[str]
    ) -> EvictionReport:
        started = time.perf_counter_ns()
        try:
            with self._lock:
                return self._evict(
                    incoming_bytes,
                    set(active_run_ids),
                    dry=False,
                    raise_when_exhausted=True,
                )
        finally:
            elapsed = time.perf_counter_ns() - started
            with self._lock:
                self.ensure_capacity_last_ns = elapsed
                self.ensure_capacity_count += 1

    def gc(
        self,
        dry: bool = False,
        *,
        adopt_orphans: bool = True,
        stale_after_seconds: int = 600,
        active_run_ids: set[str] | None = None,
    ) -> EvictionReport:
        with self._lock:
            if adopt_orphans:
                self.adopt_orphans(
                    stale_after_seconds=stale_after_seconds,
                    active_run_ids=set(active_run_ids or ()),
                    dry=dry,
                )
            self.recount()
            report = self._evict(0, set(active_run_ids or ()), dry=dry, raise_when_exhausted=False)
            if not dry:
                self._last_gc = utc_now()
            return report


__all__ = [
    "QuotaManager",
    "ReplayQuotaExceededError",
    "ReplayRootForbiddenError",
]
