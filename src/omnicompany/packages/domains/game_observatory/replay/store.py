"""Pure-filesystem replay store organized by game and run."""

from __future__ import annotations

import hashlib
import io
import json
import os
import queue
import re
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from PIL import Image

from .models import (
    ActionRecordV1,
    BIEventV1,
    FrameRecordV1,
    ReplayStatusV1,
    ReplayTimingV1,
    RunCountsV1,
    RunIndexEntryV1,
    RunManifestV1,
    StateFrameV1,
    TimelineEventV1,
    utc_now,
)
from .quota import (
    QuotaManager,
    ReplayQuotaExceededError,
    directory_size,
    resolve_replay_root,
    run_directory_manifest_hash,
)


_COMPONENT_PATTERN = re.compile(r"^[^/\\]+$")


def _default_root() -> Path:
    configured = os.environ.get("GAME_OBSERVATORY_REPLAY_ROOT")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[6] / "data" / "domains" / "game_observatory" / "replay"


def _validate_component(value: str, label: str) -> None:
    if not value or value in {".", ".."} or not _COMPONENT_PATTERN.fullmatch(value):
        raise ValueError(f"invalid {label}: {value!r}")


def _json_bytes(value: dict[str, Any]) -> bytes:
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


@dataclass(frozen=True)
class _FrameWriteRequest:
    record: FrameRecordV1
    source_bytes: bytes


class FrameWriter:
    """Encode and persist frames for one run on a bounded background worker."""

    def __init__(self, owner: RunHandle, *, queue_size: int = 64) -> None:
        if queue_size < 1:
            raise ValueError("queue_size must be positive")
        self._owner = owner
        self.queue_size = queue_size
        self._queue: queue.Queue[_FrameWriteRequest] = queue.Queue()
        self._capacity = threading.BoundedSemaphore(queue_size)
        self._state_lock = threading.RLock()
        self._pending: dict[str, _FrameWriteRequest] = {}
        self._final_status: dict[str, str] = {}
        self._alerts: list[str] = []
        self._quota_error: ReplayQuotaExceededError | None = None
        self._fatal_error: BaseException | None = None
        self._accepting = True
        self._idle = threading.Event()
        self._idle.set()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"replay-frame-writer-{owner.run_id}",
            daemon=True,
        )
        self._thread.start()

    @property
    def lost_count(self) -> int:
        with self._state_lock:
            return sum(status == "lost" for status in self._final_status.values())

    def pop_alerts(self) -> list[str]:
        with self._state_lock:
            alerts = self._alerts
            self._alerts = []
            return alerts

    def _add_alert(self, text: str) -> None:
        with self._state_lock:
            if text not in self._alerts:
                self._alerts.append(text)

    def _raise_if_rejected(self) -> None:
        with self._state_lock:
            if self._quota_error is not None:
                raise self._quota_error
            if not self._accepting:
                raise RuntimeError("frame writer is closed")

    def submit(
        self,
        record: FrameRecordV1,
        source_bytes: bytes,
        *,
        on_full: Any,
    ) -> FrameRecordV1:
        with self._state_lock:
            quota_error = self._quota_error
            fatal_error = self._fatal_error
            accepting = self._accepting
        if quota_error is not None:
            raise quota_error
        if not accepting:
            raise RuntimeError("frame writer is closed")
        if fatal_error is not None:
            accepted = record.model_copy(
                update={"t_mono_ns": time.monotonic_ns(), "t_wall": utc_now()}
            )
            request = _FrameWriteRequest(record=accepted, source_bytes=source_bytes)
            self._finalize_lost(request)
            return accepted

        if not self._capacity.acquire(blocking=False):
            on_full()
            while not self._capacity.acquire(timeout=0.05):
                with self._state_lock:
                    if self._quota_error is not None:
                        raise self._quota_error
                    if self._fatal_error is not None:
                        accepted = record.model_copy(
                            update={"t_mono_ns": time.monotonic_ns(), "t_wall": utc_now()}
                        )
                        request = _FrameWriteRequest(
                            record=accepted, source_bytes=source_bytes
                        )
                        self._finalize_lost(request)
                        return accepted
                    if not self._accepting:
                        raise RuntimeError("frame writer is closed")

        with self._state_lock:
            if self._quota_error is not None:
                self._capacity.release()
                raise self._quota_error
            if not self._accepting:
                self._capacity.release()
                raise RuntimeError("frame writer is closed")
            if self._fatal_error is not None:
                self._capacity.release()
                accepted = record.model_copy(
                    update={"t_mono_ns": time.monotonic_ns(), "t_wall": utc_now()}
                )
                request = _FrameWriteRequest(
                    record=accepted, source_bytes=source_bytes
                )
                self._finalize_lost(request)
                return accepted
            accepted = record.model_copy(
                update={"t_mono_ns": time.monotonic_ns(), "t_wall": utc_now()}
            )
            request = _FrameWriteRequest(record=accepted, source_bytes=source_bytes)
            self._pending[accepted.frame_id] = request
            self._idle.clear()
        self._queue.put_nowait(request)
        return accepted

    def _write_frame(self, request: _FrameWriteRequest) -> FrameRecordV1:
        if request.record.source_format == "raw_rgb":
            source = Image.frombytes(
                "RGB",
                (request.record.width, request.record.height),
                request.source_bytes,
            )
        elif request.record.source_format == "raw_bgr":
            source = Image.frombytes(
                "RGB",
                (request.record.width, request.record.height),
                request.source_bytes,
                "raw",
                "BGR",
            )
        elif request.record.source_format == "raw_rgba":
            source = Image.frombytes(
                "RGBA",
                (request.record.width, request.record.height),
                request.source_bytes,
            )
        else:
            source = Image.open(io.BytesIO(request.source_bytes))
        with source:
            source.load()
            image = source if source.mode in {"RGB", "RGBA"} else source.convert("RGB")
            try:
                output = io.BytesIO()
                image.save(output, format="WEBP", quality=80)
                webp = output.getvalue()
            finally:
                if image is not source:
                    image.close()

        self._owner.store.quota.ensure_capacity(
            len(webp), self._owner.store._active_run_ids()
        )
        final = request.record.model_copy(
            update={
                "sha256": hashlib.sha256(webp).hexdigest(),
                "status": "written",
                "bytes": len(webp),
            }
        )
        with self._state_lock:
            if request.record.frame_id in self._final_status:
                return final
            destination = self._owner.run_dir / request.record.path
            _atomic_write_bytes(destination, webp)
            self._owner.store.quota._record_growth(len(webp))
            self._finalize(request, final)
        return final

    def _finalize(self, request: _FrameWriteRequest, final: FrameRecordV1) -> None:
        with self._state_lock:
            if request.record.frame_id in self._final_status:
                return
            else:
                try:
                    self._owner._append_frame_final(final)
                finally:
                    self._final_status[request.record.frame_id] = final.status
                    if self._pending.pop(request.record.frame_id, None) is not None:
                        self._capacity.release()
                    if not self._pending:
                        self._idle.set()

    def _finalize_lost(self, request: _FrameWriteRequest) -> None:
        self._finalize(
            request,
            request.record.model_copy(
                update={"sha256": None, "status": "lost", "bytes": 0}
            ),
        )

    def _drain_as_lost(self) -> None:
        while True:
            try:
                request = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                self._finalize_lost(request)
            finally:
                self._queue.task_done()

    def _record_failure(self, error: BaseException) -> None:
        with self._state_lock:
            if isinstance(error, ReplayQuotaExceededError):
                if self._quota_error is None:
                    self._quota_error = error
                    self._accepting = False
                    self._alerts.append(str(error))
            elif self._fatal_error is None:
                self._fatal_error = error
                self._alerts.append(
                    f"frame writer failed: {type(error).__name__}: {error}"
                )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                request = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                final = self._write_frame(request)
                self._finalize(request, final)
            except ReplayQuotaExceededError as error:
                self._record_failure(error)
                self._finalize_lost(request)
                self._drain_as_lost()
            except BaseException as error:
                self._record_failure(error)
                self._finalize_lost(request)
                self._drain_as_lost()
            finally:
                self._queue.task_done()

    def close(self, timeout_seconds: float = 30.0) -> bool:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        with self._state_lock:
            self._accepting = False
        drained = self._idle.wait(timeout_seconds)
        if not drained:
            self._add_alert("frame writer close timeout")
            with self._state_lock:
                pending = list(self._pending.values())
            for request in pending:
                self._finalize_lost(request)
            self._drain_as_lost()
        self._stop.set()
        self._thread.join(timeout=0.2)
        return drained


class ReplayStore:
    """Create replay runs and read their permanent index."""

    def __init__(
        self,
        root: Path | None = None,
        quota: QuotaManager | None = None,
        *,
        frame_queue_size: int = 64,
        frame_close_timeout_seconds: float = 30.0,
    ) -> None:
        if frame_queue_size < 1:
            raise ValueError("frame_queue_size must be positive")
        if frame_close_timeout_seconds < 0:
            raise ValueError("frame_close_timeout_seconds must be non-negative")
        selected_root = quota.root if root is None and quota is not None else (root or _default_root())
        self.root = resolve_replay_root(Path(selected_root))
        if quota is not None and quota.root != self.root:
            raise ValueError("quota root does not match replay store root")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "index").mkdir(parents=True, exist_ok=True)
        self.quota = quota or QuotaManager(self.root)
        self._handles: dict[str, RunHandle] = {}
        self._lock = threading.RLock()
        self._frame_queue_size = frame_queue_size
        self._frame_close_timeout_seconds = frame_close_timeout_seconds
        self.active_scan_last_ns = 0
        self.active_scan_count = 0

    def open_run(
        self,
        game_id: str,
        environment_id: str,
        build_scope_id: str,
        device_serial: str,
        producer: str,
        *,
        frame_queue_size: int | None = None,
        frame_close_timeout_seconds: float | None = None,
    ) -> RunHandle:
        _validate_component(game_id, "game_id")
        selected_queue_size = (
            self._frame_queue_size if frame_queue_size is None else frame_queue_size
        )
        selected_close_timeout = (
            self._frame_close_timeout_seconds
            if frame_close_timeout_seconds is None
            else frame_close_timeout_seconds
        )
        if selected_queue_size < 1:
            raise ValueError("frame_queue_size must be positive")
        if selected_close_timeout < 0:
            raise ValueError("frame_close_timeout_seconds must be non-negative")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        game_dir = self.root / game_id
        game_dir.mkdir(parents=True, exist_ok=True)
        while True:
            run_id = f"run.{game_id}.{timestamp}.{secrets.token_hex(4)}"
            run_dir = game_dir / run_id
            try:
                run_dir.mkdir()
                break
            except FileExistsError:
                continue
        (run_dir / "frames").mkdir()
        handle = RunHandle(
            store=self,
            game_id=game_id,
            run_id=run_id,
            run_dir=run_dir,
            environment_id=environment_id,
            build_scope_id=build_scope_id,
            device_serial=device_serial,
            producer=producer,
            frame_queue_size=selected_queue_size,
            frame_close_timeout_seconds=selected_close_timeout,
        )
        handle._initialize()
        with self._lock:
            self._handles[run_id] = handle
        return handle

    def _unregister(self, run_id: str) -> None:
        with self._lock:
            self._handles.pop(run_id, None)

    def _active_run_ids(self) -> set[str]:
        started = time.perf_counter_ns()
        try:
            with self._lock:
                active = set(self._handles)
            for game_dir in self.root.iterdir():
                if not game_dir.is_dir() or game_dir.name == "index":
                    continue
                for run_dir in game_dir.iterdir():
                    manifest_path = run_dir / "run.json"
                    if not manifest_path.is_file():
                        continue
                    try:
                        manifest = RunManifestV1.model_validate_json(
                            manifest_path.read_text("utf-8")
                        )
                    except (OSError, ValueError):
                        continue
                    if manifest.ended_at is None:
                        active.add(manifest.run_id)
            return active
        finally:
            elapsed = time.perf_counter_ns() - started
            with self._lock:
                self.active_scan_last_ns = elapsed
                self.active_scan_count += 1

    def _write_index(
        self,
        manifest: RunManifestV1,
        run_dir: Path,
        *,
        evicted_at: str | None = None,
        evicted_reason: str | None = None,
    ) -> RunIndexEntryV1:
        entry = RunIndexEntryV1.from_manifest(
            manifest,
            evicted_at=evicted_at,
            evicted_reason=evicted_reason,  # type: ignore[arg-type]
            run_dir_sha256_manifest=run_directory_manifest_hash(run_dir),
        )
        path = self.root / "index" / manifest.game_id / f"{manifest.run_id}.json"
        _atomic_write_bytes(path, _json_bytes(entry.model_dump(mode="json")))
        return entry

    def list_runs(self, game_id: str | None = None) -> list[RunIndexEntryV1]:
        if game_id is not None:
            _validate_component(game_id, "game_id")
            game_directories = [self.root / "index" / game_id]
        else:
            index_root = self.root / "index"
            game_directories = sorted(path for path in index_root.iterdir() if path.is_dir())
        entries: list[RunIndexEntryV1] = []
        for game_dir in game_directories:
            if not game_dir.is_dir():
                continue
            for path in sorted(game_dir.glob("*.json")):
                entries.append(RunIndexEntryV1.model_validate_json(path.read_text("utf-8")))
        entries.sort(key=lambda entry: (entry.started_at, entry.run_id))
        return entries

    def status(self) -> ReplayStatusV1:
        active_run_ids = sorted(self._active_run_ids())
        return ReplayStatusV1(
            bytes_total=self.quota.usage_bytes(),
            limit=self.quota.limit_bytes,
            runs=len(self.list_runs()),
            active_run_ids=active_run_ids,
            last_gc=self.quota.last_gc,
            timing=ReplayTimingV1(
                active_scan_last_ms=self.active_scan_last_ns / 1_000_000,
                active_scan_count=self.active_scan_count,
                ensure_capacity_last_ms=self.quota.ensure_capacity_last_ns / 1_000_000,
                ensure_capacity_count=self.quota.ensure_capacity_count,
                eviction_hash_last_s=self.quota.last_eviction_hash_seconds,
            ),
        )


class RunHandle:
    """Mutable writer facade for one replay run."""

    def __init__(
        self,
        *,
        store: ReplayStore,
        game_id: str,
        run_id: str,
        run_dir: Path,
        environment_id: str,
        build_scope_id: str,
        device_serial: str,
        producer: str,
        frame_queue_size: int = 64,
        frame_close_timeout_seconds: float = 30.0,
    ) -> None:
        self.store = store
        self.game_id = game_id
        self.run_id = run_id
        self.run_dir = run_dir
        self._environment_id = environment_id
        self._build_scope_id = build_scope_id
        self._device_serial = device_serial
        self._producer = producer
        self._frame_queue_size = frame_queue_size
        self._frame_close_timeout_seconds = frame_close_timeout_seconds
        self._manifest: RunManifestV1 | None = None
        self._frame_writer: FrameWriter | None = None
        self._counts = RunCountsV1()
        self._ndjson_appends = 0
        self._closed = False
        self._lock = threading.RLock()
        self._io_lock = threading.RLock()

    @property
    def manifest(self) -> RunManifestV1:
        if self._manifest is None:
            raise RuntimeError("run is not initialized")
        return self._manifest

    @property
    def frame_writer(self) -> FrameWriter:
        if self._frame_writer is None:
            raise RuntimeError("frame writer is not initialized")
        return self._frame_writer

    def _initialize(self) -> None:
        started_at = utc_now()
        manifest = RunManifestV1(
            run_id=self.run_id,
            t_wall=started_at,
            game_id=self.game_id,
            environment_id=self._environment_id,
            build_scope_id=self._build_scope_id,
            device_serial=self._device_serial,
            started_at=started_at,
            producer=self._producer,
            counts=self._counts,
        )
        self._manifest = self._write_manifest(manifest)
        self.store._write_index(self._manifest, self.run_dir)
        self._frame_writer = FrameWriter(self, queue_size=self._frame_queue_size)

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"replay run is already closed: {self.run_id}")

    def _write_manifest(self, manifest: RunManifestV1) -> RunManifestV1:
        path = self.run_dir / "run.json"
        old_size = path.stat().st_size if path.exists() else 0
        base_size = directory_size(self.run_dir) - old_size
        total = manifest.bytes_total
        while True:
            candidate = manifest.model_copy(update={"bytes_total": total})
            payload = _json_bytes(candidate.model_dump(mode="json"))
            calculated = base_size + len(payload)
            if calculated == total:
                break
            total = calculated
        _atomic_write_bytes(path, payload)
        self.store.quota._record_growth(len(payload) - old_size)
        return candidate

    def _append(self, path: Path, model: Any) -> None:
        payload = _json_bytes(model.model_dump(mode="json"))
        with self._io_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                handle.write(payload)
                handle.flush()
            self.store.quota._record_growth(len(payload))
            self._ndjson_appends += 1

    def _append_frame_final(self, record: FrameRecordV1) -> None:
        self._append(self.run_dir / "frames.ndjson", record)

    def _flush_frame_writer_alerts(self) -> None:
        for text in self.frame_writer.pop_alerts():
            before_appends = self._ndjson_appends
            self._append_timeline(kind="alert", ref=None, note=text)
            self._counts = self._counts.model_copy(
                update={"timeline": self._counts.timeline + 1}
            )
            self._enforce_ndjson_interval(before_appends)

    def _record_frame_queue_full(self) -> None:
        before_appends = self._ndjson_appends
        self._append_timeline(kind="alert", ref=None, note="frame queue full")
        self._counts = self._counts.model_copy(
            update={"timeline": self._counts.timeline + 1}
        )
        self._enforce_ndjson_interval(before_appends)

    def _append_timeline(
        self,
        *,
        kind: str,
        ref: str | int | None,
        note: str | None = None,
        t_mono_ns: int | None = None,
        t_wall: str | None = None,
    ) -> None:
        event = TimelineEventV1(
            run_id=self.run_id,
            t_mono_ns=t_mono_ns if t_mono_ns is not None else time.monotonic_ns(),
            t_wall=t_wall if t_wall is not None else utc_now(),
            kind=kind,  # type: ignore[arg-type]
            ref=ref,
            note=note,
        )
        self._append(self.run_dir / "timeline.ndjson", event)

    def _enforce_ndjson_interval(self, before_appends: int) -> None:
        if before_appends // 100 != self._ndjson_appends // 100:
            self.store.quota.ensure_capacity(0, self.store._active_run_ids())

    def add_frame(
        self,
        png_or_webp_bytes: bytes,
        *,
        trigger: str,
        action_id: str | None = None,
    ) -> FrameRecordV1:
        with self._lock:
            self._assert_open()
            self._flush_frame_writer_alerts()
            self.frame_writer._raise_if_rejected()
            with Image.open(io.BytesIO(png_or_webp_bytes)) as source:
                width, height = source.size
                source_format = "webp" if source.format == "WEBP" else "png"

            while True:
                frame_id = f"f.{time.monotonic_ns()}.{secrets.token_hex(3)}"
                path = self.run_dir / "frames" / f"{frame_id}.webp"
                if not path.exists():
                    break
            record = FrameRecordV1(
                run_id=self.run_id,
                frame_id=frame_id,
                path=path.relative_to(self.run_dir).as_posix(),
                width=width,
                height=height,
                sha256=None,
                status="pending",
                bytes=None,
                source_format=source_format,
                trigger=trigger,  # type: ignore[arg-type]
                action_id=action_id,
            )
            record = self.frame_writer.submit(
                record,
                png_or_webp_bytes,
                on_full=self._record_frame_queue_full,
            )
            before_appends = self._ndjson_appends
            self._append_timeline(
                kind="frame",
                ref=frame_id,
                t_mono_ns=record.t_mono_ns,
                t_wall=record.t_wall,
            )
            self._counts = self._counts.model_copy(
                update={"frames": self._counts.frames + 1, "timeline": self._counts.timeline + 1}
            )
            self._enforce_ndjson_interval(before_appends)
            return record

    def add_frame_raw(
        self,
        pixels: bytes,
        width: int,
        height: int,
        *,
        channels: Literal["RGB", "BGR", "RGBA"],
        trigger: str,
        action_id: str | None = None,
    ) -> FrameRecordV1:
        if width < 1 or height < 1:
            raise ValueError("raw frame width and height must be positive")
        channel_count = {"RGB": 3, "BGR": 3, "RGBA": 4}[channels]
        expected_bytes = width * height * channel_count
        if len(pixels) != expected_bytes:
            raise ValueError(
                f"raw frame byte length mismatch: expected {expected_bytes}, got {len(pixels)}"
            )

        with self._lock:
            self._assert_open()
            self._flush_frame_writer_alerts()
            self.frame_writer._raise_if_rejected()
            while True:
                frame_id = f"f.{time.monotonic_ns()}.{secrets.token_hex(3)}"
                path = self.run_dir / "frames" / f"{frame_id}.webp"
                if not path.exists():
                    break
            record = FrameRecordV1(
                run_id=self.run_id,
                frame_id=frame_id,
                path=path.relative_to(self.run_dir).as_posix(),
                width=width,
                height=height,
                sha256=None,
                status="pending",
                bytes=None,
                source_format={
                    "RGB": "raw_rgb",
                    "BGR": "raw_bgr",
                    "RGBA": "raw_rgba",
                }[channels],
                trigger=trigger,  # type: ignore[arg-type]
                action_id=action_id,
            )
            record = self.frame_writer.submit(
                record,
                pixels,
                on_full=self._record_frame_queue_full,
            )
            before_appends = self._ndjson_appends
            self._append_timeline(
                kind="frame",
                ref=frame_id,
                t_mono_ns=record.t_mono_ns,
                t_wall=record.t_wall,
            )
            self._counts = self._counts.model_copy(
                update={"frames": self._counts.frames + 1, "timeline": self._counts.timeline + 1}
            )
            self._enforce_ndjson_interval(before_appends)
            return record

    def add_state(
        self, source: str, payload: dict[str, Any], latency_ms: float | None = None
    ) -> int:
        with self._lock:
            self._assert_open()
            sequence = self._counts.states + 1
            record = StateFrameV1(
                run_id=self.run_id,
                source=source,  # type: ignore[arg-type]
                payload=payload,
                latency_ms=latency_ms,
            )
            before_appends = self._ndjson_appends
            self._append(self.run_dir / "states.ndjson", record)
            self._append_timeline(
                kind="state",
                ref=sequence,
                t_mono_ns=record.t_mono_ns,
                t_wall=record.t_wall,
            )
            self._counts = self._counts.model_copy(
                update={"states": sequence, "timeline": self._counts.timeline + 1}
            )
            self._enforce_ndjson_interval(before_appends)
            return sequence

    def add_bi(self, event: str, payload: dict[str, Any]) -> int:
        with self._lock:
            self._assert_open()
            sequence = self._counts.bi + 1
            record = BIEventV1(run_id=self.run_id, event=event, payload=payload)
            before_appends = self._ndjson_appends
            self._append(self.run_dir / "bi.ndjson", record)
            self._append_timeline(
                kind="bi",
                ref=sequence,
                t_mono_ns=record.t_mono_ns,
                t_wall=record.t_wall,
            )
            self._counts = self._counts.model_copy(
                update={"bi": sequence, "timeline": self._counts.timeline + 1}
            )
            self._enforce_ndjson_interval(before_appends)
            return sequence

    def add_action(self, action: ActionRecordV1) -> None:
        with self._lock:
            self._assert_open()
            if action.run_id != self.run_id:
                raise ValueError("action run_id does not match this run")
            before_appends = self._ndjson_appends
            self._append(self.run_dir / "actions.ndjson", action)
            self._append_timeline(
                kind="action",
                ref=action.action_id,
                t_mono_ns=action.t_mono_ns,
                t_wall=action.t_wall,
            )
            self._counts = self._counts.model_copy(
                update={
                    "actions": self._counts.actions + 1,
                    "timeline": self._counts.timeline + 1,
                }
            )
            self._enforce_ndjson_interval(before_appends)

    def _add_message(self, kind: str, text: str) -> None:
        with self._lock:
            self._assert_open()
            before_appends = self._ndjson_appends
            self._append_timeline(kind=kind, ref=None, note=text)
            self._counts = self._counts.model_copy(
                update={"timeline": self._counts.timeline + 1}
            )
            self._enforce_ndjson_interval(before_appends)

    def note(self, text: str) -> None:
        self._add_message("note", text)

    def alert(self, text: str) -> None:
        self._add_message("alert", text)

    def close(self, end_reason: str, summary: dict[str, Any]) -> RunManifestV1:
        with self._lock:
            self._assert_open()
            self.frame_writer.close(self._frame_close_timeout_seconds)
            self._flush_frame_writer_alerts()
            ended_at = utc_now()
            closed_summary = dict(summary)
            lost_frames = self.frame_writer.lost_count
            if lost_frames:
                closed_summary["lost_frames"] = lost_frames
            values = self.manifest.model_dump(mode="python")
            values.update(
                {
                    "t_mono_ns": time.monotonic_ns(),
                    "t_wall": ended_at,
                    "ended_at": ended_at,
                    "end_reason": end_reason,
                    "summary": closed_summary,
                    "counts": self._counts,
                }
            )
            candidate = RunManifestV1.model_validate(values)
            written = self._write_manifest(candidate)
            self.store._write_index(written, self.run_dir)
            self._manifest = written
            self._closed = True
            self.store._unregister(self.run_id)
            return written


__all__ = ["FrameWriter", "ReplayStore", "RunHandle"]
