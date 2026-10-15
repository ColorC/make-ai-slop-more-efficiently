"""Verified local retention for raw provider session files.

The encrypted restic repository remains append-only.  This module only removes
an old local session file after the exact bytes have been streamed back from a
recorded remote snapshot and matched against the still-unchanged local file.
"""

from __future__ import annotations

import hashlib
import codecs
import errno
import json
import os
import subprocess
import stat
import threading
import argparse
import ctypes
import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .object_backup_queue import (
    BackupConfig,
    load_config,
    load_state,
    restic_base_command,
    restic_environment,
    file_source_metadata,
)


GIB = 1024**3
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
REMOTE_COMPARE_CHUNK_BYTES = 1024 * 1024
REMOTE_VERIFY_TIMEOUT_SECONDS = 120
REMOTE_STDERR_LIMIT_BYTES = 64 * 1024
HISTORY_READ_CHUNK_BYTES = 64 * 1024
HISTORY_MAX_JSON_DEPTH = 256
FILE_SHARE_READ = 0x1
FILE_SHARE_DELETE = 0x4
GENERIC_READ = 0x80000000
DELETE_ACCESS = 0x00010000
OPEN_EXISTING = 3
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
FILE_DISPOSITION_INFO_EX = 21
FILE_DISPOSITION_DELETE = 0x1
FILE_DISPOSITION_POSIX_SEMANTICS = 0x2


def _one_calendar_month_ago(value: datetime) -> datetime:
    """Return the same UTC wall time one calendar month earlier, clamped at month end."""
    utc_value = value.astimezone(timezone.utc)
    month_index = utc_value.year * 12 + utc_value.month - 2
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(utc_value.day, calendar.monthrange(year, month)[1])
    return utc_value.replace(year=year, month=month, day=day)


@dataclass(frozen=True)
class SessionRetentionSource:
    id: str
    backup_item: str
    root: Path
    patterns: tuple[str, ...]
    minimum_age_days: int

    def __post_init__(self) -> None:
        if isinstance(self.minimum_age_days, bool) or not isinstance(self.minimum_age_days, int):
            raise ValueError("session retention minimum_age_days must be an integer")
        if self.minimum_age_days < 31:
            raise ValueError("session retention minimum_age_days must be at least 31")

    @classmethod
    def from_dict(cls, raw: dict[str, Any], default_minimum_age_days: int) -> "SessionRetentionSource":
        source_id = str(raw.get("id", "")).strip()
        backup_item = str(raw.get("backup_item", "")).strip()
        root_value = str(raw.get("root", "")).strip()
        patterns = tuple(str(value) for value in raw.get("patterns", ("**/*.jsonl",)))
        if not source_id or not backup_item or not root_value or not patterns:
            raise ValueError("each session retention source requires id, backup_item, root, and patterns")
        raw_minimum_age_days = raw.get("minimum_age_days", default_minimum_age_days)
        if isinstance(raw_minimum_age_days, bool):
            raise ValueError("session retention minimum_age_days must be an integer")
        minimum_age_days = int(raw_minimum_age_days)
        return cls(
            id=source_id,
            backup_item=backup_item,
            root=_expand_path(root_value),
            patterns=patterns,
            minimum_age_days=minimum_age_days,
        )


@dataclass(frozen=True)
class SessionRetentionPolicy:
    limit_bytes: int
    receipt_root: Path
    sources: tuple[SessionRetentionSource, ...]
    enabled: bool = False


@dataclass(frozen=True)
class RemoteVerification:
    ok: bool
    local_sha256: str
    remote_sha256: str
    local_bytes: int
    remote_bytes: int
    error: str = ""


@dataclass(frozen=True)
class SessionCandidate:
    source: SessionRetentionSource
    path: Path
    relative: str
    size: int
    mtime_ns: int
    source_metadata: dict[str, Any] | None = None
    ineligible_reason: str = ""


RemoteVerifier = Callable[[BackupConfig, str, str, Path], RemoteVerification]


def _expand_path(value: str) -> Path:
    return Path(os.path.abspath(os.path.expandvars(os.path.expanduser(value))))


def load_session_retention_policy(config_path: Path) -> SessionRetentionPolicy:
    with config_path.open("r", encoding="utf-8-sig") as handle:
        raw = json.load(handle)
    policy = raw.get("session_local_retention")
    if not isinstance(policy, dict):
        raise ValueError("object backup config is missing session_local_retention")
    enabled = policy.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("session_local_retention.enabled must be a boolean")
    raw_default_age = policy.get("minimum_age_days", 31)
    if isinstance(raw_default_age, bool):
        raise ValueError("session retention minimum_age_days must be an integer")
    default_age = int(raw_default_age)
    limit_gib = float(policy.get("limit_gib", 100))
    if limit_gib <= 0:
        raise ValueError("session_local_retention.limit_gib must be positive")
    sources = tuple(
        SessionRetentionSource.from_dict(source, default_age)
        for source in policy.get("sources", ())
        if isinstance(source, dict)
    )
    if not sources:
        raise ValueError("session_local_retention.sources must not be empty")
    receipt_root = _expand_path(
        str(policy.get("receipt_root", "%LOCALAPPDATA%/OmniBackup/session-retention-receipts"))
    )
    return SessionRetentionPolicy(
        enabled=enabled,
        limit_bytes=int(limit_gib * GIB),
        receipt_root=receipt_root,
        sources=sources,
    )


def _snapshot_path(path: Path) -> str:
    value = Path(os.path.abspath(path)).as_posix()
    if len(value) >= 3 and value[1:3] == ":/":
        return f"/{value[0]}{value[2:]}"
    return value if value.startswith("/") else f"/{value}"


def verify_remote_file(
    config: BackupConfig,
    snapshot_id: str,
    snapshot_path: str,
    local_path: Path,
) -> RemoteVerification:
    command = [*restic_base_command(config), "dump", snapshot_id, snapshot_path]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=restic_environment(config),
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stderr_chunks: list[bytes] = []
    stderr_size = 0
    local_bytes = 0
    remote_bytes = 0
    streams_equal = True
    read_errors: list[str] = []

    def read_stderr() -> None:
        nonlocal stderr_size
        while True:
            block = process.stderr.read(64 * 1024)
            if not block:
                return
            available = max(0, REMOTE_STDERR_LIMIT_BYTES - stderr_size)
            if available:
                stderr_chunks.append(block[:available])
                stderr_size += min(len(block), available)

    def read_up_to(handle: Any, limit: int) -> bytes:
        parts: list[bytes] = []
        size = 0
        while size < limit:
            block = handle.read(limit - size)
            if not block:
                break
            parts.append(block)
            size += len(block)
        return b"".join(parts)

    def compare_streams() -> None:
        nonlocal local_bytes, remote_bytes, streams_equal
        try:
            with local_path.open("rb") as local_handle:
                while True:
                    local_block = read_up_to(local_handle, REMOTE_COMPARE_CHUNK_BYTES)
                    remote_block = read_up_to(process.stdout, REMOTE_COMPARE_CHUNK_BYTES)
                    local_bytes += len(local_block)
                    remote_bytes += len(remote_block)
                    if local_block != remote_block:
                        streams_equal = False
                    if not local_block and not remote_block:
                        return
        except BaseException as exc:  # conveyed to the owner thread after child cleanup
            read_errors.append(f"{type(exc).__name__}: {exc}")

    stderr_reader = threading.Thread(target=read_stderr, name="session-retention-stderr", daemon=True)
    stdout_reader = threading.Thread(target=compare_streams, name="session-retention-compare", daemon=True)
    stderr_reader.start()
    stdout_reader.start()
    timed_out = False
    return_code: int | None = None
    try:
        return_code = process.wait(timeout=REMOTE_VERIFY_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            process.kill()
        except OSError:
            if process.poll() is None:
                read_errors.append("failed to terminate timed-out restic dump process")
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return_code = None
    finally:
        stdout_reader.join(timeout=5)
        stderr_reader.join(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                if process.poll() is None:
                    read_errors.append("failed to terminate restic dump process during cleanup")
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    readers_completed = not stdout_reader.is_alive() and not stderr_reader.is_alive()
    error = b"".join(stderr_chunks).decode("utf-8", "replace")[-1000:]
    if not readers_completed:
        error = error or "restic dump stream readers did not terminate after child cleanup"
    elif read_errors:
        error = read_errors[0]
    elif timed_out:
        error = f"restic dump timed out after {REMOTE_VERIFY_TIMEOUT_SECONDS} seconds"
    elif return_code != 0:
        error = error or f"restic dump exited with {return_code}"
    elif local_bytes != remote_bytes:
        error = "remote_size_mismatch"
    elif not streams_equal:
        error = "remote_content_mismatch"
    ok = (
        return_code == 0
        and not timed_out
        and readers_completed
        and not read_errors
        and local_bytes == remote_bytes
        and streams_equal
    )
    return RemoteVerification(
        ok=ok,
        local_sha256="",
        remote_sha256="",
        local_bytes=local_bytes,
        remote_bytes=remote_bytes,
        error=error,
    )


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        int(getattr(info, "st_file_attributes", 0)) & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _link_count(path: Path) -> int | None:
    try:
        return int(os.lstat(path).st_nlink)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _has_reparse_ancestor(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in (None, *relative.parts):
        if part is not None:
            current = current / part
        try:
            info = os.lstat(current)
        except OSError:
            return True
        if _is_reparse(info):
            return True
    return False


def _capture_source_metadata(path: Path, root: Path) -> tuple[dict[str, Any] | None, int | None, str]:
    if _has_reparse_ancestor(path, root):
        return None, None, "reparse_path"
    try:
        before = os.lstat(path)
    except OSError:
        return None, None, "source_unavailable"
    if _is_reparse(before):
        return None, None, "reparse_path"
    if not stat.S_ISREG(before.st_mode):
        return None, None, "not_regular_file"
    links = _link_count(path)
    if links is None:
        return None, int(before.st_size), "hardlink_count_unknown"
    if int(links) != 1:
        return None, int(before.st_size), "hardlink_count_gt_one"
    metadata = file_source_metadata(path)
    try:
        after = os.lstat(path)
    except OSError:
        return None, int(before.st_size), "source_changed_during_metadata"
    if _is_reparse(after) or not stat.S_ISREG(after.st_mode):
        return None, int(before.st_size), "reparse_path"
    links_after = _link_count(path)
    if links_after is None:
        return None, int(before.st_size), "hardlink_count_unknown"
    if links_after != 1:
        return None, int(before.st_size), "hardlink_count_gt_one"
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        return None, int(before.st_size), "source_changed_during_metadata"
    if metadata is None:
        return None, int(after.st_size), "source_metadata_unavailable"
    if not (
        metadata.get("size") == int(after.st_size)
        and metadata.get("mtime_ns") == int(after.st_mtime_ns)
        and metadata.get("file_id") is not None
        and (metadata.get("change_time_100ns") is not None or metadata.get("change_time_ns") is not None)
    ):
        return None, int(after.st_size), "source_metadata_incomplete"
    return metadata, int(after.st_size), ""


def _iter_candidates(
    policy: SessionRetentionPolicy,
    on_skip: Callable[[str], None] | None = None,
) -> Iterable[SessionCandidate]:
    seen: set[str] = set()
    skip = on_skip or (lambda _reason: None)
    for source in policy.sources:
        root = Path(os.path.abspath(source.root))
        try:
            root_info = os.lstat(root)
        except OSError:
            skip("source_root_missing")
            continue
        if _is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
            skip("source_root_reparse_or_not_directory")
            continue
        if _has_reparse_ancestor(root, Path(root.anchor)):
            skip("source_root_ancestor_reparse")
            continue
        pending = [root]
        while pending:
            directory = pending.pop()
            try:
                dir_info = os.lstat(directory)
            except OSError:
                skip("directory_unavailable")
                continue
            if _is_reparse(dir_info):
                skip("reparse_directory")
                continue
            if not stat.S_ISDIR(dir_info.st_mode):
                continue
            try:
                with os.scandir(directory) as scanned:
                    entries = list(scanned)
            except OSError:
                skip("directory_unreadable")
                continue
            for entry in entries:
                path = Path(entry.path)
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    skip("entry_metadata_unavailable")
                    continue
                if _is_reparse(info):
                    skip("reparse_entry")
                    continue
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                relative = path.relative_to(root).as_posix()
                if not any(Path(relative).match(pattern.replace("\\", "/")) for pattern in source.patterns):
                    continue
                key = os.path.normcase(os.path.abspath(path))
                if key in seen:
                    continue
                seen.add(key)
                native_metadata, size, reason = _capture_source_metadata(path, root)
                yield SessionCandidate(
                    source=source,
                    path=path,
                    relative=relative,
                    size=int(info.st_size if size is None else size),
                    mtime_ns=int(
                        native_metadata.get("mtime_ns", info.st_mtime_ns)
                        if native_metadata is not None
                        else info.st_mtime_ns
                    ),
                    source_metadata=native_metadata,
                    ineligible_reason=reason,
                )


def _parse_completed_at(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _find_snapshot(item_state: dict[str, Any], relative: str) -> dict[str, Any] | None:
    chunks = item_state.get("chunks", {})
    if not isinstance(chunks, dict):
        return None
    matches: list[dict[str, Any]] = []
    for raw in chunks.values():
        if not isinstance(raw, dict) or not raw.get("snapshot_id"):
            continue
        key = str(raw.get("key", ".")).strip("/")
        if key in {"", "."} or relative == key or relative.startswith(f"{key}/"):
            matches.append(raw)
    if not matches:
        return None
    return max(matches, key=lambda value: len(str(value.get("key", ""))))


def _find_exact_snapshot(item_state: dict[str, Any], relative: str) -> dict[str, Any] | None:
    chunks = item_state.get("chunks", {})
    if not isinstance(chunks, dict):
        return None
    exact = [
        raw
        for raw in chunks.values()
        if isinstance(raw, dict)
        and str(raw.get("key", "")).replace("\\", "/").strip("/") == relative
    ]
    return exact[0] if len(exact) == 1 else None


def _metadata_with_links(path: Path, root: Path) -> tuple[dict[str, Any] | None, int | None, str]:
    if _has_reparse_ancestor(path, Path(path.anchor)) or _has_reparse_ancestor(path, root):
        return None, None, "reparse_path"
    try:
        before = os.lstat(path)
    except OSError:
        return None, None, "source_unavailable"
    if _is_reparse(before) or not stat.S_ISREG(before.st_mode):
        return None, int(before.st_size), "not_plain_file"
    links = _link_count(path)
    metadata = file_source_metadata(path)
    try:
        after = os.lstat(path)
    except OSError:
        return None, int(before.st_size), "source_changed_during_metadata"
    if (
        _is_reparse(after)
        or not stat.S_ISREG(after.st_mode)
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        return None, int(after.st_size), "source_changed_during_metadata"
    links_after = _link_count(path)
    if links is None or links_after is None:
        return None, int(after.st_size), "hardlink_count_unknown"
    if links != links_after:
        return None, int(after.st_size), "link_count_changed_during_metadata"
    if metadata is None:
        return None, int(after.st_size), "source_metadata_unavailable"
    if not (
        metadata.get("size") == int(after.st_size)
        and metadata.get("mtime_ns") == int(after.st_mtime_ns)
        and metadata.get("device") == int(after.st_dev)
        and metadata.get("file_id") == int(after.st_ino)
        and (metadata.get("change_time_100ns") is not None or metadata.get("change_time_ns") is not None)
    ):
        return None, int(after.st_size), "source_metadata_incomplete"
    return metadata, links, ""


class _ReadPin:
    """A Windows handle that denies writes while allowing exact-name deletion."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None
        self.file = None
        self.identity: dict[str, int] | None = None

    def __enter__(self) -> "_ReadPin":
        if os.name == "nt":
            import msvcrt

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create_file = kernel32.CreateFileW
            create_file.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
            ]
            create_file.restype = ctypes.c_void_p
            handle = create_file(
                str(self.path),
                GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_DELETE,
                None,
                OPEN_EXISTING,
                FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN,
                None,
            )
            if handle in (None, ctypes.c_void_p(-1).value):
                error = ctypes.get_last_error()
                raise OSError(error, f"CreateFileW read pin failed (winerror={error})", str(self.path))
            self.handle = handle
            try:
                fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0))
            except OSError as exc:
                self.close()
                raise OSError(exc.errno, f"msvcrt.open_osfhandle failed: {exc}") from exc
            self.handle = None  # fd owns the native handle now
            try:
                self.file = os.fdopen(fd, "rb", closefd=True)
            except OSError as exc:
                os.close(fd)
                raise OSError(exc.errno, f"os.fdopen failed: {exc}") from exc
        else:
            self.file = self.path.open("rb")
        try:
            info = os.fstat(self.file.fileno())
        except OSError as exc:
            self.close()
            raise OSError(exc.errno, f"os.fstat read pin failed: {exc}") from exc
        self.identity = {"device": int(info.st_dev), "file_id": int(info.st_ino)}
        return self

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None
        if self.handle is not None:
            close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
            close_handle.argtypes = [ctypes.c_void_p]
            close_handle.restype = ctypes.c_int
            close_handle(ctypes.c_void_p(int(self.handle)))
            self.handle = None

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _delete_pinned_alias(pin: _ReadPin, alias_path: Path | None = None) -> None:
    """Delete only the name represented by the already identity-checked handle."""
    if pin.file is None or pin.identity is None:
        raise OSError("read pin is not open")
    alias_path = alias_path or pin.path
    if os.name != "nt":
        current = file_source_metadata(alias_path)
        if current is None or {"device": current.get("device"), "file_id": current.get("file_id")} != pin.identity:
            raise OSError(f"alias path no longer names the pinned native file: {alias_path}")
        alias_path.unlink()
        return
    import msvcrt

    class FileDispositionInfoEx(ctypes.Structure):
        _fields_ = [("flags", ctypes.c_uint32)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    create_file = kernel32.CreateFileW
    create_file.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    create_file.restype = ctypes.c_void_p
    set_info = kernel32.SetFileInformationByHandle
    set_info.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    set_info.restype = ctypes.c_int
    info = FileDispositionInfoEx(FILE_DISPOSITION_DELETE | FILE_DISPOSITION_POSIX_SEMANTICS)
    delete_handle = create_file(str(alias_path), DELETE_ACCESS, FILE_SHARE_READ | 0x2 | FILE_SHARE_DELETE, None, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, None)
    if delete_handle in (None, ctypes.c_void_p(-1).value):
        raise OSError(f"CreateFileW DELETE handle failed, winerror={ctypes.get_last_error()}: {alias_path}")
    try:
        delete_fd = msvcrt.open_osfhandle(int(delete_handle), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        delete_handle = None
        try:
            delete_file = os.fdopen(delete_fd, "rb", closefd=True)
        except OSError as exc:
            try:
                os.close(delete_fd)
            except OSError as close_exc:
                if close_exc.errno != errno.EBADF:
                    raise OSError(f"os.fdopen delete handle failed: {exc}; descriptor close failed: {close_exc}") from exc
            raise OSError(exc.errno, f"os.fdopen delete handle failed: {exc}") from exc
        with delete_file:
            delete_info = os.fstat(delete_file.fileno())
            if {"device": int(delete_info.st_dev), "file_id": int(delete_info.st_ino)} != pin.identity:
                raise OSError(f"alias path no longer names the pinned native file: {alias_path}")
            handle = ctypes.c_void_p(msvcrt.get_osfhandle(delete_file.fileno()))
            if not set_info(handle, FILE_DISPOSITION_INFO_EX, ctypes.byref(info), ctypes.sizeof(info)):
                extended_error = ctypes.get_last_error()
                class FileDispositionInfo(ctypes.Structure):
                    _fields_ = [("delete_file", ctypes.c_ubyte)]
                legacy = FileDispositionInfo(1)
                if not set_info(handle, 4, ctypes.byref(legacy), ctypes.sizeof(legacy)):
                    raise OSError(f"SetFileInformationByHandle disposition failed, extended={extended_error}, legacy={ctypes.get_last_error()}: {alias_path}")
    finally:
        if delete_handle is not None:
            kernel32.CloseHandle(delete_handle)
    # Closing only the short-lived delete handle commits that one-name
    # operation; the long-lived read pin continues denying writes.


def _current_process_identity() -> dict[str, int | str | None]:
    identity: dict[str, int | str | None] = {"pid": os.getpid(), "birth_filetime_100ns": None}
    if os.name != "nt":
        return identity
    from ctypes import wintypes

    class FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    current_process = kernel32.GetCurrentProcess
    current_process.restype = wintypes.HANDLE
    get_times = kernel32.GetProcessTimes
    get_times.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileTime), ctypes.POINTER(FileTime), ctypes.POINTER(FileTime), ctypes.POINTER(FileTime)]
    get_times.restype = wintypes.BOOL
    created, exited, kernel, user = FileTime(), FileTime(), FileTime(), FileTime()
    if get_times(current_process(), ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
        identity["birth_filetime_100ns"] = (int(created.high) << 32) | int(created.low)
    return identity


class _StreamingJsonError(ValueError):
    def __init__(self, reason: str = "history_json_unreadable") -> None:
        super().__init__(reason)
        self.reason = reason


class _StreamingJsonl:
    """Validate JSONL incrementally without materializing arbitrary string values."""

    _MISSING = object()
    _RECOGNIZED = {"timestamp", "time", "ts", "created_at", "updated_at", "started_at", "completed_at"}

    def __init__(self, handle: Any) -> None:
        self.handle = handle
        self.decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        self.buffer = ""
        self.index = 0
        self.eof = False

    def peek(self) -> str:
        while self.index >= len(self.buffer) and not self.eof:
            raw = self.handle.read(HISTORY_READ_CHUNK_BYTES)
            self.buffer = self.decoder.decode(raw, final=not raw)
            self.index = 0
            self.eof = not raw
        return self.buffer[self.index] if self.index < len(self.buffer) else ""

    def take(self) -> str:
        char = self.peek()
        if char:
            self.index += 1
        return char

    def _space(self) -> None:
        while self.peek() in (" ", "\t"):
            self.take()

    @staticmethod
    def _candidate_key(value: str | None, tail: str) -> bool:
        lowered = value.lower() if value is not None else ""
        lowered_tail = tail.lower()
        return lowered in _StreamingJsonl._RECOGNIZED or lowered_tail.endswith(("_at", "_timestamp"))

    def _string(self, limit: int | None) -> tuple[str | None, str]:
        if self.take() != '"':
            raise _StreamingJsonError()
        captured: list[str] | None = [] if limit is not None else None
        length = 0
        tail = ""
        while True:
            char = self.take()
            if not char or ord(char) < 0x20:
                raise _StreamingJsonError()
            if char == '"':
                return ("".join(captured) if captured is not None else None), tail
            if char == "\\":
                escape = self.take()
                simple = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
                if escape in simple:
                    char = simple[escape]
                elif escape == "u":
                    digits = "".join(self.take() for _ in range(4))
                    if len(digits) != 4 or any(c not in "0123456789abcdefABCDEF" for c in digits):
                        raise _StreamingJsonError()
                    char = chr(int(digits, 16))
                else:
                    raise _StreamingJsonError()
            length += 1
            tail = (tail + char)[-16:]
            if captured is not None:
                if length <= int(limit):
                    captured.append(char)
                else:
                    captured = None

    def _number(self) -> None:
        if self.peek() == "-":
            self.take()
        char = self.peek()
        if char == "0":
            self.take()
            if self.peek().isdigit():
                raise _StreamingJsonError()
        elif char in "123456789":
            self.take()
            while self.peek().isdigit() and self.peek().isascii():
                self.take()
        else:
            raise _StreamingJsonError()
        if self.peek() == ".":
            self.take()
            if not (self.peek().isdigit() and self.peek().isascii()):
                raise _StreamingJsonError()
            while self.peek().isdigit() and self.peek().isascii():
                self.take()
        if self.peek() in ("e", "E"):
            self.take()
            if self.peek() in ("+", "-"):
                self.take()
            if not (self.peek().isdigit() and self.peek().isascii()):
                raise _StreamingJsonError()
            while self.peek().isdigit() and self.peek().isascii():
                self.take()

    def _literal(self, token: str) -> None:
        for expected in token:
            if self.take() != expected:
                raise _StreamingJsonError()

    def _value(self, depth: int, capture_string: bool = False) -> Any:
        if depth > HISTORY_MAX_JSON_DEPTH:
            raise _StreamingJsonError()
        self._space()
        char = self.peek()
        if char == '"':
            value, _tail = self._string(128 if capture_string else None)
            return value if capture_string else self._MISSING
        if char == "{":
            self._object(depth + 1, top_level=False)
            return self._MISSING
        if char == "[":
            self.take()
            self._space()
            if self.peek() == "]":
                self.take()
                return self._MISSING
            while True:
                self._value(depth + 1)
                self._space()
                delimiter = self.take()
                if delimiter == "]":
                    return self._MISSING
                if delimiter != ",":
                    raise _StreamingJsonError()
                self._space()
        if char == "t":
            self._literal("true")
            return self._MISSING
        if char == "f":
            self._literal("false")
            return self._MISSING
        if char == "n":
            self._literal("null")
            return self._MISSING
        if char == "-" or (char.isascii() and char.isdigit()):
            self._number()
            return self._MISSING
        raise _StreamingJsonError()

    def _object(self, depth: int, *, top_level: bool) -> dict[str, Any]:
        if self.take() != "{":
            raise _StreamingJsonError()
        self._space()
        values: dict[str, Any] = {}
        if self.peek() == "}":
            self.take()
            return values
        while True:
            if self.peek() != '"':
                raise _StreamingJsonError()
            key, tail = self._string(256)
            candidate = top_level and self._candidate_key(key, tail)
            self._space()
            if self.take() != ":":
                raise _StreamingJsonError()
            value = self._value(depth + 1, capture_string=candidate)
            if candidate:
                values[key if key is not None else tail] = value
            self._space()
            delimiter = self.take()
            if delimiter == "}":
                return values
            if delimiter != ",":
                raise _StreamingJsonError()
            self._space()

    def next_event(self) -> dict[str, Any] | None:
        # Skip blank JSONL records while allowing CRLF and LF endings.
        while self.peek() in (" ", "\t", "\r", "\n"):
            self.take()
        if not self.peek():
            return None
        if self.peek() != "{":
            raise _StreamingJsonError("history_event_not_object")
        values = self._object(0, top_level=True)
        self._space()
        if self.peek() == "\r":
            self.take()
            if self.peek() == "\n":
                self.take()
        elif self.peek() == "\n":
            self.take()
        elif self.peek():
            raise _StreamingJsonError()
        return values


def _history_latest_timestamp(path: Path) -> tuple[datetime | None, str]:
    latest: datetime | None = None
    try:
        with path.open("rb") as handle:
            parser = _StreamingJsonl(handle)
            while (event := parser.next_event()) is not None:
                if not event:
                    return None, "history_timestamp_missing"
                parsed_values = [_parse_history_timestamp(value) for value in event.values()]
                if any(value is None for value in parsed_values):
                    return None, "history_timestamp_unparseable"
                latest = max([latest, *parsed_values]) if latest is not None else max(parsed_values)
    except UnicodeDecodeError:
        return None, "history_json_unreadable"
    except _StreamingJsonError as exc:
        return None, exc.reason
    except OSError:
        return None, "history_unreadable"
    if latest is None:
        return None, "history_empty"
    return latest, ""


def _parse_history_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _validate_alias_group(
    policy: SessionRetentionPolicy,
    paths: list[Path],
) -> tuple[list[tuple[Path, SessionRetentionSource, str]], dict[str, Any] | None, str]:
    if len(paths) < 2:
        if not paths:
            return [], None, "alias_group_requires_at_least_one_path"
    if any(not Path(path).is_absolute() for path in paths):
        return [], None, "path_not_absolute"
    normalized = [Path(os.path.abspath(path)) for path in paths]
    keys = [os.path.normcase(os.path.normpath(str(path))) for path in normalized]
    if len(set(keys)) != len(keys):
        return [], None, "duplicate_path"
    records: list[tuple[Path, SessionRetentionSource, str]] = []
    basemeta: dict[str, Any] | None = None
    link_count: int | None = None
    for path in normalized:
        matches: list[tuple[SessionRetentionSource, str]] = []
        for source in policy.sources:
            try:
                relative = path.relative_to(Path(os.path.abspath(source.root))).as_posix()
            except ValueError:
                continue
            if any(Path(relative).match(pattern.replace("\\", "/")) for pattern in source.patterns):
                matches.append((source, relative))
        if len(matches) != 1:
            return [], None, "path_not_in_exactly_one_registered_source"
        source, relative = matches[0]
        metadata, links, error = _metadata_with_links(path, Path(os.path.abspath(source.root)))
        if error or metadata is None or links is None:
            return [], None, error or "source_metadata_unavailable"
        if basemeta is None:
            basemeta, link_count = metadata, links
        elif metadata != basemeta:
            return [], None, "aliases_not_same_native_object_and_version"
        records.append((path, source, relative))
    if link_count != len(records):
        return [], None, "alias_count_does_not_match_native_link_count"
    return records, basemeta, ""


def archive_alias_group(
    config: BackupConfig,
    policy: SessionRetentionPolicy,
    paths: list[Path],
    archive_before: datetime,
    *,
    apply: bool = False,
    verifier: RemoteVerifier = verify_remote_file,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Archive and optionally unlink only one fully enumerated native alias group."""
    observed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if archive_before.tzinfo is None or archive_before.utcoffset() != timedelta(0):
        return {"status": "blocked", "reason": "archive_before_must_be_explicit_utc", "paths": []}
    cutoff = archive_before.astimezone(timezone.utc)
    if cutoff > _one_calendar_month_ago(observed_now):
        return {"status": "blocked", "reason": "archive_before_must_be_at_least_one_calendar_month_old", "paths": []}
    records, before_metadata, error = _validate_alias_group(policy, paths)
    if error or before_metadata is None:
        return {"status": "blocked", "reason": error or "source_metadata_unavailable", "paths": []}
    # The explicit archive cutoff is corroborated by timestamps inside the JSONL,
    # never by filesystem age or the name of the session.
    canonical_path = records[0][0]
    latest_event, history_error = _history_latest_timestamp(canonical_path)
    if history_error:
        return {"status": "blocked", "reason": history_error, "paths": [str(path) for path, _, _ in records]}
    if latest_event is None or latest_event > cutoff:
        return {"status": "blocked", "reason": "history_has_event_after_archive_cutoff", "paths": [str(path) for path, _, _ in records]}
    state = load_state(config)
    item_states = state.get("items", {}) if isinstance(state.get("items", {}), dict) else {}
    canonical: tuple[dict[str, Any], str, SessionRetentionSource, Path] | None = None
    for path, source, relative in records:
        item_state = item_states.get(source.backup_item, {})
        chunk = _find_exact_snapshot(item_state, relative) if isinstance(item_state, dict) else None
        if (
            isinstance(chunk, dict)
            and chunk.get("status") == "complete"
            and str(chunk.get("snapshot_id", "")).strip()
            and chunk.get("source_metadata") == before_metadata
        ):
            canonical = (chunk, _snapshot_path(path), source, path)
            break
    if canonical is None:
        return {"status": "blocked", "reason": "no_exact_complete_snapshot_with_matching_native_metadata", "paths": [str(path) for path, _, _ in records]}
    chunk, snapshot_path, canonical_source, canonical_path = canonical
    snapshot_id = str(chunk["snapshot_id"])
    result: dict[str, Any] = {
        "status": "dry_run" if not apply else "verifying",
        "dry_run": not apply,
        "paths": [str(path) for path, _, _ in records],
        "bytes_per_alias": int(before_metadata["size"]),
        "native_link_count": len(records),
        "source_metadata": before_metadata,
        "history_latest_event_utc": latest_event.isoformat(),
        "archive_before_utc": cutoff.isoformat(),
        "snapshot_id": snapshot_id,
        "snapshot_path": snapshot_path,
        "backup_item": canonical_source.backup_item,
        "source_id": canonical_source.id,
        "deleted_aliases": [],
        "logical_bytes_removed": 0,
        "physical_allocation_credit": "pending_root_review",
    }
    if not apply:
        return result

    pins: list[_ReadPin] = []
    canonical_relative = next(relative for path, source, relative in records if path == canonical_path and source == canonical_source)
    receipt_path = _receipt_path(
        policy,
        SessionCandidate(
            canonical_source,
            canonical_path,
            canonical_relative,
            int(before_metadata["size"]),
            int(before_metadata["mtime_ns"]),
            before_metadata,
        ),
    )
    receipt = {"version": 1, "status": "verification_in_progress", **result}
    stage = "opening_native_read_pins"

    def save_receipt() -> str:
        # The receipt starts as a shallow copy of the result. Keep these
        # counters synchronized at every durable transition, including
        # partial-delete and exception paths after an alias has been removed.
        receipt["deleted_aliases"] = list(result["deleted_aliases"])
        receipt["logical_bytes_removed"] = int(result["logical_bytes_removed"])
        try:
            _atomic_write_json(receipt_path, receipt)
        except OSError as exc:
            return f"{type(exc).__name__}: {exc}"
        return ""

    try:
        # One pin on the native object denies writes through every hard-link
        # name.  Separate DELETE handles are opened and identity-checked for
        # each exact alias at the moment it is unlinked.
        pin = _ReadPin(canonical_path)
        stage = f"opening_native_read_pin:{canonical_path}"
        pin.__enter__()
        pins.append(pin)
        stage = "recording_native_read_pin_identity"
        receipt["native_process"] = _current_process_identity()
        receipt["read_pins"] = [
            {"path": str(pin.path), **(pin.identity or {})}
            for pin in pins
        ]
        expected_identity = {"device": int(before_metadata["device"]), "file_id": int(before_metadata["file_id"])}
        if any(pin.identity != expected_identity for pin in pins):
            result.update(status="blocked", reason="native_read_pin_identity_mismatch")
            return result
        # All names must still resolve to the pinned object and original version.
        stage = "revalidating_alias_group_after_pin"
        current_records, current_metadata, current_error = _validate_alias_group(policy, [path for path, _, _ in records])
        if current_error or current_metadata != before_metadata:
            result.update(status="blocked", reason=current_error or "source_changed_after_pin")
            return result
        stage = "verifying_remote_raw_stream"
        verification = verifier(config, snapshot_id, snapshot_path, canonical_path)
        if not verification.ok or verification.local_bytes != int(before_metadata["size"]) or verification.remote_bytes != int(before_metadata["size"]):
            result.update(
                status="blocked",
                reason=verification.error or "remote_raw_stream_mismatch",
                verified_local_bytes=verification.local_bytes,
                verified_remote_bytes=verification.remote_bytes,
            )
            return result
        current_records, current_metadata, current_error = _validate_alias_group(policy, [path for path, _, _ in records])
        if current_error or current_metadata != before_metadata:
            result.update(status="blocked", reason=current_error or "source_changed_during_remote_readback")
            return result
        receipt.update(status="verified_pending_alias_delete", verified_at=datetime.now(timezone.utc).isoformat())
        stage = "writing_predelete_receipt"
        receipt_error = save_receipt()
        if receipt_error:
            result.update(status="blocked", reason=f"receipt_write_failed_before_delete: {receipt_error}")
            return result
        expected_meta = before_metadata
        remaining = len(records)
        pin_by_path = {path: pins[0] for path, _, _ in records}
        for path, _, _ in records:
            stage = f"checking_alias_before_delete:{path}"
            # Resolve the matching registered source without parent-path inference.
            source = next(source for candidate, source, _ in records if candidate == path)
            current_meta, links, current_error = _metadata_with_links(path, Path(os.path.abspath(source.root)))
            if current_error or current_meta is None or links != remaining:
                receipt.update(status="partial_delete", reason=current_error or "native_link_count_changed", deleted_aliases=list(result["deleted_aliases"]))
                receipt_error = save_receipt()
                result.update(status="partial_delete", reason=receipt["reason"], receipt=str(receipt_path), receipt_write_error=receipt_error)
                return result
            if current_meta != expected_meta:
                receipt.update(status="partial_delete", reason="native_identity_or_change_time_changed", deleted_aliases=list(result["deleted_aliases"]))
                receipt_error = save_receipt()
                result.update(status="partial_delete", reason=receipt["reason"], receipt=str(receipt_path), receipt_write_error=receipt_error)
                return result
            receipt.update(status="deleting_alias", pending_alias=str(path), deleted_aliases=list(result["deleted_aliases"]))
            receipt_error = save_receipt()
            if receipt_error:
                result.update(status="partial_delete" if result["deleted_aliases"] else "blocked", reason=f"receipt_write_failed_before_alias_delete: {receipt_error}")
                if result["deleted_aliases"]:
                    result["receipt"] = str(receipt_path)
                return result
            stage = f"deleting_pinned_alias:{path}"
            _delete_pinned_alias(pin_by_path[path], path)
            result["deleted_aliases"].append(str(path))
            result["logical_bytes_removed"] += int(before_metadata["size"])
            remaining -= 1
            receipt.update(status="partial_delete" if remaining else "deleted_alias_group", pending_alias=None, deleted_aliases=list(result["deleted_aliases"]))
            receipt_error = save_receipt()
            if receipt_error:
                result.update(status="partial_delete", reason=f"receipt_write_failed_after_alias_delete: {receipt_error}", receipt=str(receipt_path))
                return result
            if remaining:
                surviving = next(candidate for candidate, _, _ in records if candidate.exists())
                observed_meta, observed_links, current_error = _metadata_with_links(
                    surviving,
                    Path(os.path.abspath(next(source.root for candidate, source, _ in records if candidate == surviving))),
                )
                if current_error or observed_meta is None or observed_links != remaining:
                    receipt.update(status="partial_delete", reason=current_error or "post_delete_link_count_unexpected")
                    receipt_error = save_receipt()
                    result.update(status="partial_delete", reason=receipt["reason"], receipt=str(receipt_path), receipt_write_error=receipt_error)
                    return result
                # Keep the original object/version pinned.  Unlinking one of
                # its names may update ChangeTime and link count; it must never
                # authorize a replacement file at a surviving path.
                immutable_fields = ("device", "file_id", "size", "mtime_ns")
                if any(observed_meta.get(key) != before_metadata.get(key) for key in immutable_fields):
                    receipt.update(status="partial_delete", reason="surviving_alias_no_longer_original_native_object")
                    receipt_error = save_receipt()
                    result.update(status="partial_delete", reason=receipt["reason"], receipt=str(receipt_path), receipt_write_error=receipt_error)
                    return result
                if any(pin.identity != expected_identity for candidate, pin in pin_by_path.items() if candidate.exists()):
                    receipt.update(status="partial_delete", reason="surviving_read_pin_identity_changed")
                    receipt_error = save_receipt()
                    result.update(status="partial_delete", reason=receipt["reason"], receipt=str(receipt_path), receipt_write_error=receipt_error)
                    return result
                before_change = before_metadata.get("change_time_100ns", before_metadata.get("change_time_ns"))
                observed_change = observed_meta.get("change_time_100ns", observed_meta.get("change_time_ns"))
                if before_change is not None and observed_change is not None and observed_change < before_change:
                    receipt.update(status="partial_delete", reason="surviving_alias_change_time_moved_backwards")
                    receipt_error = save_receipt()
                    result.update(status="partial_delete", reason=receipt["reason"], receipt=str(receipt_path), receipt_write_error=receipt_error)
                    return result
                expected_meta = dict(before_metadata)
                change_field = "change_time_100ns" if "change_time_100ns" in before_metadata else "change_time_ns"
                expected_meta[change_field] = observed_meta.get(change_field)
        result.update(status="deleted_alias_group", receipt=str(receipt_path))
        receipt.update(status="deleted_alias_group", deleted_at=datetime.now(timezone.utc).isoformat(), physical_allocation_credit="pending_root_review")
        receipt_error = save_receipt()
        if receipt_error:
            result.update(status="partial_delete", reason=f"receipt_write_failed_after_alias_group_delete: {receipt_error}")
        return result
    except (OSError, subprocess.SubprocessError) as exc:
        result.update(status="partial_delete" if result["deleted_aliases"] else "blocked", reason=f"{type(exc).__name__} at {stage}: {exc}")
        if result["deleted_aliases"]:
            receipt.update(status="partial_delete", reason=result["reason"], deleted_aliases=list(result["deleted_aliases"]))
            receipt_error = save_receipt()
            result.update(receipt=str(receipt_path), receipt_write_error=receipt_error)
        elif receipt_path.exists():
            # The pre-delete receipt may already exist even when the first
            # unlink fails. Keep its counters and expose its actual path while
            # preserving the phase/status written before the native operation.
            receipt_error = save_receipt()
            result.update(receipt=str(receipt_path), receipt_write_error=receipt_error)
        return result
    finally:
        for pin in reversed(pins):
            pin.close()


def _build_alias_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Archive one explicitly listed session hard-link group.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--archive-before", required=True, help="UTC ISO-8601 cutoff at least one calendar month old, with Z or +00:00")
    parser.add_argument("--path", action="append", required=True, help="Exact absolute alias path; repeat for each name")
    parser.add_argument("--dry-run", action="store_true", help="Validate scope only (the default)")
    parser.add_argument("--apply", action="store_true", help="Verify remote bytes and unlink only the exact alias group")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_alias_parser().parse_args(argv)
    if args.apply and args.dry_run:
        raise ValueError("--apply and --dry-run are mutually exclusive")
    cutoff = datetime.fromisoformat(args.archive_before.replace("Z", "+00:00"))
    if cutoff.tzinfo is None or cutoff.utcoffset() != timedelta(0):
        raise ValueError("--archive-before must be an explicit UTC timestamp")
    if any(not Path(value).is_absolute() for value in args.path):
        raise ValueError("every --path must be an exact absolute path")
    config = load_config(Path(args.config))
    policy = load_session_retention_policy(Path(args.config))
    result = archive_alias_group(config, policy, [Path(value) for value in args.path], cutoff, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"dry_run", "deleted_alias_group"} else 1


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _receipt_path(policy: SessionRetentionPolicy, candidate: SessionCandidate) -> Path:
    key = hashlib.sha256(
        f"{candidate.source.id}\0{candidate.path}".encode("utf-8")
    ).hexdigest()
    return policy.receipt_root / candidate.source.id / f"{key}.json"


def enforce_local_session_limit(
    config: BackupConfig,
    policy: SessionRetentionPolicy,
    *,
    limit_bytes: int | None = None,
    dry_run: bool = False,
    verifier: RemoteVerifier = verify_remote_file,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Keep configured raw session files below a local high-water mark.

    Recent, live-looking, unarchived, changed-after-backup, symlinked, and
    remote-mismatching files fail closed.  Empty directories are intentionally
    retained so provider directory layouts remain stable.
    """

    if not policy.enabled:
        effective_limit = policy.limit_bytes if limit_bytes is None else int(limit_bytes)
        return {
            "status": "disabled",
            "dry_run": dry_run,
            "limit_bytes": effective_limit,
            "total_before_bytes": None,
            "total_after_bytes": None,
            "overage_before_bytes": None,
            "deleted_count": 0,
            "deleted_bytes": 0,
            "would_delete_count": 0,
            "would_delete_bytes": 0,
            "skipped": {},
            "receipts": [],
        }

    effective_limit = policy.limit_bytes if limit_bytes is None else int(limit_bytes)
    if effective_limit <= 0:
        raise ValueError("local session limit must be positive")
    observed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = int(skipped.get(reason, 0)) + 1

    candidates = sorted(
        _iter_candidates(policy, on_skip=skip),
        key=lambda value: (value.mtime_ns, str(value.path)),
    )
    total_before = sum(candidate.size for candidate in candidates)
    report: dict[str, Any] = {
        "status": "within_limit" if total_before <= effective_limit else ("dry_run" if dry_run else "running"),
        "dry_run": dry_run,
        "limit_bytes": effective_limit,
        "total_before_bytes": total_before,
        "total_after_bytes": total_before,
        "overage_before_bytes": max(0, total_before - effective_limit),
        "deleted_count": 0,
        "deleted_bytes": 0,
        "would_delete_count": 0,
        "would_delete_bytes": 0,
        "skipped": skipped,
        "receipts": [],
    }
    if total_before <= effective_limit:
        return report

    state = load_state(config)
    item_states = state.get("items", {}) if isinstance(state.get("items", {}), dict) else {}
    current_bytes = total_before

    for candidate in candidates:
        if current_bytes <= effective_limit:
            break
        if candidate.ineligible_reason:
            skip(candidate.ineligible_reason)
            continue
        before_metadata, _before_size, before_error = _capture_source_metadata(
            candidate.path,
            Path(os.path.abspath(candidate.source.root)),
        )
        if before_error or before_metadata != candidate.source_metadata:
            skip(before_error or "changed_before_verification")
            continue
        age_cutoff = observed_now - timedelta(days=candidate.source.minimum_age_days)
        candidate_mtime = datetime.fromtimestamp(candidate.mtime_ns / 1_000_000_000, tz=timezone.utc)
        if candidate_mtime > age_cutoff:
            skip("recent")
            continue
        item_state = item_states.get(candidate.source.backup_item, {})
        if not isinstance(item_state, dict):
            skip("backup_item_missing")
            continue
        snapshot = _find_snapshot(item_state, candidate.relative)
        if snapshot is None:
            skip("snapshot_missing")
            continue
        completed_at = _parse_completed_at(snapshot.get("completed_at"))
        if completed_at is None or completed_at.timestamp() < candidate.mtime_ns / 1_000_000_000:
            skip("backup_stale")
            continue
        snapshot_id = str(snapshot["snapshot_id"])
        snapshot_path = _snapshot_path(candidate.path)
        if dry_run:
            current_bytes -= candidate.size
            report["would_delete_count"] += 1
            report["would_delete_bytes"] += candidate.size
            continue
        try:
            verification = verifier(config, snapshot_id, snapshot_path, candidate.path)
        except (OSError, subprocess.SubprocessError) as exc:
            skip("remote_readback_failed")
            continue
        if not verification.ok:
            if verification.local_bytes != verification.remote_bytes:
                skip("remote_size_mismatch")
            elif verification.error == "remote_content_mismatch":
                skip("remote_content_mismatch")
            else:
                skip("remote_readback_failed")
            continue
        after_metadata, _after_size, after_error = _capture_source_metadata(
            candidate.path,
            Path(os.path.abspath(candidate.source.root)),
        )
        if after_error or after_metadata != candidate.source_metadata:
            skip(after_error or "changed_during_verification")
            continue
        receipt_path = _receipt_path(policy, candidate)
        receipt = {
            "version": 1,
            "status": "verified_pending_delete",
            "source_id": candidate.source.id,
            "backup_item": candidate.source.backup_item,
            "source_root": str(candidate.source.root),
            "relative_path": candidate.relative,
            "bytes": candidate.size,
            "mtime_ns": candidate.mtime_ns,
            "source_metadata": candidate.source_metadata,
            "snapshot_id": snapshot_id,
            "snapshot_path": snapshot_path,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_write_json(receipt_path, receipt)
        final_metadata, _final_size, final_error = _capture_source_metadata(
            candidate.path,
            Path(os.path.abspath(candidate.source.root)),
        )
        if final_error or final_metadata != candidate.source_metadata:
            receipt.update(
                {
                    "status": "verification_invalidated_before_delete",
                    "error": final_error or "changed_before_delete",
                }
            )
            _atomic_write_json(receipt_path, receipt)
            skip(final_error or "changed_before_delete")
            continue
        try:
            candidate.path.unlink()
        except OSError as exc:
            receipt.update({"status": "verified_delete_failed", "error": str(exc)})
            _atomic_write_json(receipt_path, receipt)
            skip("delete_failed")
            continue
        receipt.update({"status": "deleted_local", "deleted_at": datetime.now(timezone.utc).isoformat()})
        _atomic_write_json(receipt_path, receipt)
        current_bytes -= candidate.size
        report["deleted_count"] += 1
        report["deleted_bytes"] += candidate.size
        report["receipts"].append(str(receipt_path))

    report["total_after_bytes"] = current_bytes
    report["status"] = (
        "dry_run"
        if dry_run
        else ("complete" if current_bytes <= effective_limit else "blocked")
    )
    return report


if __name__ == "__main__":
    raise SystemExit(main())
