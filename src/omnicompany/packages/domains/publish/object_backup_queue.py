# [OMNI] origin=human domain=domains/publish type=module agent=ai-ide-292fcd7e ts=2026-07-26T09:07:29Z
# [OMNI] summary="Bounded restic backup queue with a detailed plaintext cloud catalog."
# [OMNI] why="发布管线组件，归在 publish 域"
# [OMNI] tags=publish,module
"""Bounded restic backup queue with a detailed plaintext cloud catalog."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import queue
import re
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, TextIO


GIB = 1024**3
ITEM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
VALID_SOURCE_TYPES = {"tree", "file", "sqlite"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def expand_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def expand_executable(value: str) -> str:
    return str(expand_path(value)) if any(mark in value for mark in ("/", "\\")) else value


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


@dataclass(frozen=True)
class QueueItem:
    id: str
    title: str
    phase: int
    enabled: bool
    source: Path
    source_type: str
    destination: str
    purpose: str
    contents: tuple[str, ...]
    consumers: tuple[str, ...]
    retention: str
    restore_steps: tuple[str, ...]
    sensitivity: str
    rationale: str
    includes: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    refresh_days: int = 1
    blocked_reason: str = ""
    snapshot_name: str = ""
    chunk_depth: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QueueItem":
        item_id = str(raw.get("id", "")).strip()
        if not ITEM_ID_RE.fullmatch(item_id):
            raise ValueError(f"invalid queue item id: {item_id!r}")
        source_type = str(raw.get("source_type", "tree"))
        if source_type not in VALID_SOURCE_TYPES:
            raise ValueError(f"{item_id}: unsupported source_type {source_type!r}")
        for key in ("title", "destination", "purpose", "retention", "sensitivity", "rationale"):
            if not str(raw.get(key, "")).strip():
                raise ValueError(f"{item_id}: {key} is required")
        for key in ("contents", "consumers", "restore_steps"):
            values = raw.get(key)
            if not isinstance(values, list) or not values or not all(str(v).strip() for v in values):
                raise ValueError(f"{item_id}: {key} must be a non-empty list")
        destination = str(raw["destination"]).strip("/")
        if ".." in Path(destination).parts:
            raise ValueError(f"{item_id}: destination cannot contain '..'")
        chunk_depth = max(0, int(raw.get("chunk_depth", 0)))
        if chunk_depth and (source_type != "tree" or raw.get("includes")):
            raise ValueError(f"{item_id}: chunk_depth only supports an unfiltered tree source")
        return cls(
            id=item_id,
            title=str(raw["title"]),
            phase=int(raw.get("phase", 100)),
            enabled=bool(raw.get("enabled", True)),
            source=expand_path(str(raw["source"])),
            source_type=source_type,
            destination=destination,
            purpose=str(raw["purpose"]),
            contents=tuple(str(v) for v in raw["contents"]),
            consumers=tuple(str(v) for v in raw["consumers"]),
            retention=str(raw["retention"]),
            restore_steps=tuple(str(v) for v in raw["restore_steps"]),
            sensitivity=str(raw["sensitivity"]),
            rationale=str(raw["rationale"]),
            includes=tuple(str(v) for v in raw.get("includes", [])),
            excludes=tuple(str(v) for v in raw.get("excludes", [])),
            refresh_days=max(1, int(raw.get("refresh_days", 1))),
            blocked_reason=str(raw.get("blocked_reason", "")),
            snapshot_name=str(raw.get("snapshot_name", "")),
            chunk_depth=chunk_depth,
        )


@dataclass(frozen=True)
class BackupConfig:
    path: Path
    catalog_remote: str
    repository: str
    restic_executable: str
    restic_password_file: Path
    rclone_executable: str
    rclone_config: Path
    state_root: Path
    daily_limit_bytes: int
    upload_limit_kib: int
    items: tuple[QueueItem, ...]
    schedule: dict[str, Any] = field(default_factory=dict)
    sqlite_staging_root: Path | None = None


@dataclass(frozen=True)
class BackupUnit:
    key: str
    source: Path
    label: str


def load_config(path: str | Path) -> BackupConfig:
    config_path = Path(path).resolve()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if int(raw.get("version", 0)) != 1:
        raise ValueError("object backup config version must be 1")
    items = tuple(QueueItem.from_dict(item) for item in raw.get("items", []))
    if not items:
        raise ValueError("object backup queue is empty")
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("object backup queue contains duplicate ids")
    catalog_remote = str(raw.get("catalog_remote", "")).rstrip("/")
    repository = str(raw.get("repository", ""))
    if ":" not in catalog_remote or not repository.startswith("rclone:"):
        raise ValueError("catalog_remote must be an rclone remote and repository must use rclone:")
    staging_root = None
    if "sqlite_staging_root" in raw:
        configured_root = raw["sqlite_staging_root"]
        if not isinstance(configured_root, str) or not configured_root.strip():
            raise ValueError("sqlite_staging_root must be a non-empty absolute path string")
        expanded_root = os.path.expandvars(os.path.expanduser(configured_root))
        staging_root = Path(expanded_root)
        if not staging_root.is_absolute():
            raise ValueError("sqlite_staging_root must be an absolute path")
        staging_root = Path(os.path.abspath(staging_root))
        _assert_no_reparse_ancestors(staging_root)
        _assert_staging_root_away_from_sources(staging_root, items)
    return BackupConfig(
        path=config_path,
        catalog_remote=catalog_remote,
        repository=repository,
        restic_executable=expand_executable(str(raw.get("restic_executable", "restic"))),
        restic_password_file=expand_path(str(raw["restic_password_file"])),
        rclone_executable=expand_executable(str(raw.get("rclone_executable", "rclone"))),
        rclone_config=expand_path(str(raw["rclone_config"])),
        state_root=expand_path(str(raw["state_root"])),
        daily_limit_bytes=int(float(raw.get("daily_limit_gib", 5)) * GIB),
        upload_limit_kib=max(1, int(raw.get("upload_limit_kib", 8192))),
        items=items,
        sqlite_staging_root=staging_root,
        schedule=dict(raw.get("schedule") or {}),
    )


def load_state(config: BackupConfig) -> dict[str, Any]:
    path = config.state_root / "state.json"
    if not path.exists():
        return {"version": 1, "items": {}, "last_run": None}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "items": {}, "last_run": None}
    state.setdefault("version", 1)
    state.setdefault("items", {})
    state.setdefault("last_run", None)
    return state


def save_state(config: BackupConfig, state: dict[str, Any]) -> None:
    atomic_write_json(config.state_root / "state.json", state)


def item_is_due(item: QueueItem, status: dict[str, Any], *, now: datetime | None = None) -> bool:
    if status.get("status") in {"failed", "estimate_failed", "partial"}:
        return True
    chunks = status.get("chunks", {})
    for unit in chunk_units(item):
        unit_status = chunks.get(unit_state_key(unit), {})
        if unit_status.get("status") in {"failed", "estimate_failed"}:
            return True
        if unit.source.is_file() or isinstance(unit_status.get("source_metadata"), dict):
            if unit_is_due(item, unit, unit_status, now=now):
                return True
    if not status.get("ever_completed"):
        return True
    last_completed_at = status.get("last_completed_at")
    if not last_completed_at:
        return True
    try:
        completed = datetime.fromisoformat(str(last_completed_at))
    except ValueError:
        return True
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) >= completed + timedelta(days=item.refresh_days)


def select_phase(items: Iterable[QueueItem], state: dict[str, Any]) -> int | None:
    enabled = [item for item in items if item.enabled and not item.blocked_reason]
    if not enabled:
        return None
    item_state = state.get("items", {})
    unfinished = [item.phase for item in enabled if not item_state.get(item.id, {}).get("ever_completed")]
    if unfinished:
        return min(unfinished)
    due = [item.phase for item in enabled if item_is_due(item, item_state.get(item.id, {}))]
    return min(due) if due else None


def selected_items(
    config: BackupConfig,
    state: dict[str, Any],
    *,
    phase: int | None = None,
    item_ids: set[str] | None = None,
) -> list[QueueItem]:
    item_state = state.get("items", {})
    unfinished_phases = [
        item.phase
        for item in config.items
        if item.enabled and not item.blocked_reason and not item_state.get(item.id, {}).get("ever_completed")
    ]
    bootstrap_phase = min(unfinished_phases) if unfinished_phases else None
    selected = [
        item
        for item in config.items
        if item.enabled
        and not item.blocked_reason
        and (
            (
                bool(item_ids)
                and item.id in item_ids
                and (phase is None or item.phase == phase)
            )
            or (
                not item_ids
                and (
                    (phase is not None and item.phase == phase)
                    or (phase is None and bootstrap_phase is not None and item.phase == bootstrap_phase)
                    or (phase is None and bootstrap_phase is None and item_is_due(item, item_state.get(item.id, {})))
                )
            )
        )
    ]
    return sorted(selected, key=lambda item: (item.phase, item.id))


def remote_join(remote: str, path: str) -> str:
    return f"{remote}{path.lstrip('/')}" if remote.endswith(":") else f"{remote.rstrip('/')}/{path.lstrip('/')}"


def item_payload_reference(config: BackupConfig, item: QueueItem) -> str:
    return f"{config.repository} · tag=item:{item.id} · logical={item.destination}"


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _is_path_ancestor(ancestor: Path, path: Path) -> bool:
    ancestor_key = _path_key(ancestor)
    path_key = _path_key(path)
    return path_key == ancestor_key or path_key.startswith(ancestor_key.rstrip("\\/") + os.sep)


def _assert_no_reparse_ancestors(path: Path) -> None:
    """Refuse staging paths that would traverse an existing link or junction."""
    absolute = Path(os.path.abspath(path))
    for candidate in reversed((absolute, *absolute.parents)):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise ValueError(f"SQLite staging path contains a reparse point: {candidate}")


def _assert_staging_root_away_from_sources(staging_root: Path, items: Iterable[QueueItem]) -> None:
    for item in items:
        if item.source_type == "sqlite" and _is_path_ancestor(staging_root, item.source):
            raise ValueError(
                f"sqlite_staging_root must not equal or contain SQLite source {item.id}: {item.source}"
            )


def _assert_safe_staging_fragment(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"SQLite staging {label} must be a non-empty relative path")
    windows_path = PureWindowsPath(value)
    if Path(value).is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise ValueError(f"SQLite staging {label} must not be absolute or drive-relative: {value!r}")
    components = value.replace("\\", "/").split("/")
    if ".." in components:
        raise ValueError(f"SQLite staging {label} must not contain parent traversal: {value!r}")


def _is_strict_path_descendant(root: Path, candidate: Path) -> bool:
    root_key = _path_key(root).rstrip("\\/")
    candidate_key = _path_key(candidate)
    return candidate_key.startswith(root_key + os.sep)


def _assert_safe_snapshot_destination(item: QueueItem, source: Path, target: Path, temporary: Path) -> None:
    _assert_no_reparse_ancestors(target)
    for candidate in (target, temporary):
        if _path_key(candidate) == _path_key(source):
            raise ValueError(f"{item.id}: SQLite staging destination matches source: {candidate}")
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise ValueError(f"{item.id}: SQLite staging destination is a reparse point: {candidate}")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{item.id}: SQLite staging destination is not a regular file: {candidate}")
        if info.st_nlink != 1:
            reason = "multiple hard links" if info.st_nlink > 1 else "unknown hard-link count"
            raise ValueError(f"{item.id}: SQLite staging destination has {reason}: {candidate}")


def create_sqlite_snapshot(
    item: QueueItem,
    state_root: Path,
    staging_root: Path | None = None,
) -> Path:
    if not item.source.is_file():
        raise FileNotFoundError(item.source)
    selected_root = staging_root if staging_root is not None else state_root / "snapshots"
    if not selected_root.is_absolute():
        raise ValueError(f"{item.id}: SQLite staging root must be absolute: {selected_root}")
    snapshot_name = item.snapshot_name or item.source.name
    _assert_safe_staging_fragment(item.id, "item id")
    _assert_safe_staging_fragment(snapshot_name, "snapshot name")
    _assert_no_reparse_ancestors(selected_root)
    _assert_staging_root_away_from_sources(selected_root, (item,))
    snapshot_dir = selected_root / item.id
    target = snapshot_dir / snapshot_name
    temporary = target.with_suffix(target.suffix + ".tmp")
    for label, candidate in (("snapshot directory", snapshot_dir), ("target", target), ("temporary", temporary)):
        if not _is_strict_path_descendant(selected_root, candidate):
            raise ValueError(f"{item.id}: SQLite staging {label} escapes staging root: {candidate}")
    _assert_safe_snapshot_destination(item, item.source, target, temporary)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    _assert_safe_snapshot_destination(item, item.source, target, temporary)
    temporary.unlink(missing_ok=True)
    source_uri = f"file:{item.source.as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=30)) as source_db:
        with closing(sqlite3.connect(temporary)) as target_db:
            source_db.backup(target_db)
            result = target_db.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"{item.id}: SQLite integrity check failed: {result}")
            target_db.commit()
    temporary.replace(target)
    return target


def chunk_units(item: QueueItem) -> list[BackupUnit]:
    if item.chunk_depth <= 0 or not item.source.is_dir():
        return [BackupUnit(key=".", source=item.source, label=item.source.name)]
    current = [item.source]
    for _ in range(item.chunk_depth):
        next_level: list[Path] = []
        for source in current:
            if source.is_file():
                next_level.append(source)
                continue
            directories = sorted(path for path in source.iterdir() if path.is_dir())
            if directories:
                next_level.extend(directories)
                continue
            files = sorted(path for path in source.iterdir() if path.is_file())
            next_level.extend(files or [source])
        current = next_level
    units = []
    for source in current:
        relative = source.relative_to(item.source).as_posix()
        units.append(BackupUnit(key=relative or ".", source=source, label=relative or item.source.name))
    return units


def latest_mtime(path: Path) -> float:
    if not path.exists():
        return 0.0
    latest = path.stat().st_mtime
    if path.is_file():
        return latest
    for dirpath, _, filenames in os.walk(path):
        directory = Path(dirpath)
        try:
            latest = max(latest, directory.stat().st_mtime)
        except OSError:
            pass
        for filename in filenames:
            try:
                latest = max(latest, (directory / filename).stat().st_mtime)
            except OSError:
                pass
    return latest


def file_source_metadata(path: Path) -> dict[str, Any] | None:
    """Capture stable file identity and change metadata without using Windows ctime."""
    try:
        info = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None

    metadata: dict[str, Any] = {
        "version": 1,
        "size": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
        "device": int(info.st_dev),
        "file_id": int(info.st_ino),
    }
    if os.name == "nt":
        class FileBasicInfo(ctypes.Structure):
            _fields_ = [
                ("creation_time", ctypes.c_longlong),
                ("last_access_time", ctypes.c_longlong),
                ("last_write_time", ctypes.c_longlong),
                ("change_time", ctypes.c_longlong),
                ("file_attributes", wintypes.DWORD),
            ]

        class ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("file_attributes", wintypes.DWORD),
                ("creation_time_low", wintypes.DWORD),
                ("creation_time_high", wintypes.DWORD),
                ("last_access_time_low", wintypes.DWORD),
                ("last_access_time_high", wintypes.DWORD),
                ("last_write_time_low", wintypes.DWORD),
                ("last_write_time_high", wintypes.DWORD),
                ("volume_serial_number", wintypes.DWORD),
                ("file_size_high", wintypes.DWORD),
                ("file_size_low", wintypes.DWORD),
                ("number_of_links", wintypes.DWORD),
                ("file_index_high", wintypes.DWORD),
                ("file_index_low", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        get_basic_info = kernel32.GetFileInformationByHandleEx
        get_basic_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        get_basic_info.restype = wintypes.BOOL
        get_file_info = kernel32.GetFileInformationByHandle
        get_file_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation)]
        get_file_info.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = create_file(
            str(path),
            0x80,  # FILE_READ_ATTRIBUTES
            0x1 | 0x2 | 0x4,  # share read/write/delete
            None,
            3,  # OPEN_EXISTING
            0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle == invalid_handle:
            return None
        try:
            basic = FileBasicInfo()
            identity = ByHandleFileInformation()
            if not get_basic_info(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
                return None
            if not get_file_info(handle, ctypes.byref(identity)):
                return None
            metadata["change_time_100ns"] = int(basic.change_time)
            metadata["volume_serial"] = int(identity.volume_serial_number)
            metadata["file_id"] = (int(identity.file_index_high) << 32) | int(identity.file_index_low)
        finally:
            close_handle(handle)
    else:
        metadata["change_time_ns"] = int(info.st_ctime_ns)
    return metadata


def unit_is_due(
    item: QueueItem,
    unit: BackupUnit,
    status: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    if status.get("status") in {"failed", "estimate_failed"}:
        return True
    if unit.source.is_file() or isinstance(status.get("source_metadata"), dict):
        checkpoint = status.get("source_metadata")
        current = file_source_metadata(unit.source)
        if not isinstance(checkpoint, dict) or current is None or checkpoint != current:
            return True
    if not status.get("completed_at"):
        return True
    try:
        completed = datetime.fromisoformat(str(status["completed_at"]))
    except ValueError:
        return True
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    if latest_mtime(unit.source) > completed.timestamp():
        return True
    if item.chunk_depth:
        return False
    return (now or datetime.now(timezone.utc)) >= completed + timedelta(days=item.refresh_days)


def resolve_included_files(item: QueueItem) -> list[Path]:
    def is_excluded(path: Path) -> bool:
        relative = PurePosixPath(path.relative_to(item.source).as_posix())
        return any(relative.match(pattern) for pattern in item.excludes)

    files: set[Path] = set()
    recursive_roots: set[Path] = set()
    for pattern in item.includes:
        normalized = pattern.replace("\\", "/").rstrip("/")
        if normalized == "**" or normalized.endswith("/**"):
            prefix = normalized[:-3].rstrip("/")
            root = item.source / prefix if prefix else item.source
            if root.is_file() and not is_excluded(root):
                files.add(root.resolve())
            elif root.is_dir():
                recursive_roots.add(root)
            continue
        for match in item.source.glob(pattern):
            if match.is_file() and not is_excluded(match):
                files.add(match.resolve())
            elif match.is_dir():
                recursive_roots.add(match)

    compact_roots: list[Path] = []
    for root in sorted(recursive_roots, key=lambda path: (len(path.parts), str(path))):
        if any(parent == root or parent in root.parents for parent in compact_roots):
            continue
        compact_roots.append(root)
    for root in compact_roots:
        for child in root.rglob("*"):
            if child.is_file() and not is_excluded(child):
                files.add(child.resolve())
    return sorted(files)


def write_file_list(config: BackupConfig, item: QueueItem) -> Path:
    files = resolve_included_files(item)
    if not files:
        raise RuntimeError(f"{item.id}: include rules selected no files")
    path = config.state_root / "filelists" / f"{item.id}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(str(file) for file in files) + "\n", encoding="utf-8")
    return path


def restic_base_command(config: BackupConfig) -> list[str]:
    # 2026-09-01: OpenList→百度 上传大 pack 常超 rclone 默认 5m 响应超时("timeout awaiting response headers"),
    # 导致 restic exit=1 整块失败; 放宽 IO 超时 + 提高底层重试, 并把 restic→rclone 并发压到 2 路, 少打爆上游。
    rclone_args = (
        "serve restic --stdio --b2-hard-delete --transfers 1 --checkers 4 "
        "--timeout 30m --contimeout 2m --low-level-retries 20 --retries 5"
    )
    return [
        config.restic_executable,
        "--repo",
        config.repository,
        "--password-file",
        str(config.restic_password_file),
        "--cache-dir",
        str(config.state_root / "restic-cache"),
        "--limit-upload",
        str(config.upload_limit_kib),
        "--option",
        "rclone.program=rclone",
        "--option",
        f"rclone.args={rclone_args}",
        "--option",
        "rclone.connections=2",
    ]


def chunk_tag(unit: BackupUnit) -> str:
    digest = hashlib.sha256(unit.key.encode("utf-8")).hexdigest()[:16]
    return f"chunk:{digest}"


def build_restic_backup_command(
    config: BackupConfig,
    item: QueueItem,
    unit: BackupUnit,
    *,
    prepared_source: Path | None = None,
    dry_run: bool = False,
    file_unit: bool | None = None,
) -> list[str]:
    is_file_unit = unit.source.is_file() if file_unit is None else file_unit
    command = [
        *restic_base_command(config),
        "backup",
        "--json",
        "--tag",
        f"item:{item.id}",
        "--tag",
        f"phase:{item.phase}",
        "--tag",
        chunk_tag(unit),
    ]
    if is_file_unit:
        command.append("--force")
    else:
        command.append("--skip-if-unchanged")
    if dry_run:
        command.append("--dry-run")
    for pattern in item.excludes:
        command.extend(("--exclude", pattern))
    source = prepared_source or unit.source
    if item.includes:
        command.extend(("--files-from-verbatim", str(write_file_list(config, item))))
    else:
        command.append(str(source))
    return command


def restic_environment(config: BackupConfig) -> dict[str, str]:
    environment = os.environ.copy()
    rclone_dir = str(Path(config.rclone_executable).parent)
    environment["PATH"] = rclone_dir + os.pathsep + environment.get("PATH", "")
    environment["RCLONE_CONFIG"] = str(config.rclone_config)
    environment["RCLONE_BWLIMIT"] = f"{config.upload_limit_kib}K"
    environment["RESTIC_PASSWORD_FILE"] = str(config.restic_password_file)
    environment["RESTIC_CACHE_DIR"] = str(config.state_root / "restic-cache")
    environment["RESTIC_PROGRESS_FPS"] = "0.1"
    return environment


def render_item_readme(config: BackupConfig, item: QueueItem, status: dict[str, Any]) -> str:
    lines = [
        f"# {item.title}",
        "",
        f"- 数据集 ID：`{item.id}`",
        f"- 队列阶段：`{item.phase}`",
        f"- 当前启用：{'是' if item.enabled else '否'}",
        f"- 本地来源：`{item.source}`",
        f"- 加密负载引用：`{item_payload_reference(config, item)}`",
        f"- 分片深度：`{item.chunk_depth}`",
        f"- 数据敏感度：{item.sensitivity}",
        f"- 最近状态：{status.get('status', '尚未运行')}",
        f"- 最近尝试：{status.get('last_attempt_at', '无')}",
        f"- 累计完成过：{'是' if status.get('ever_completed') else '否'}",
        "",
        "## 用途",
        "",
        item.purpose,
        "",
        "## 包含内容",
        "",
        *[f"- {value}" for value in item.contents],
        "",
        "## 谁会使用",
        "",
        *[f"- {value}" for value in item.consumers],
        "",
        "## 为什么需要备份",
        "",
        item.rationale,
        "",
        "## 本地保留策略",
        "",
        item.retention,
        "",
        "## 恢复方法",
        "",
        *[f"{index}. {value}" for index, value in enumerate(item.restore_steps, 1)],
    ]
    if item.blocked_reason:
        lines.extend(("", "## 当前阻塞", "", item.blocked_reason))
    if item.includes:
        lines.extend(("", "## 收录过滤", "", *[f"- `{value}`" for value in item.includes]))
    if item.excludes:
        lines.extend(("", "## 排除过滤", "", *[f"- `{value}`" for value in item.excludes]))
    lines.extend(
        (
            "",
            "此说明文件是明文目录；实际数据由 restic 本地加密、去重并打包，恢复时按 item 标签定位快照。",
            "",
        )
    )
    return "\n".join(lines)


def render_root_readme(config: BackupConfig, state: dict[str, Any]) -> str:
    phase = select_phase(config.items, state)
    return "\n".join(
        [
            "# OmniCompany 长程备份目录",
            "",
            "这里是备份说明目录，不是数据负载本身。每个数据集都有独立说明，记录用途、内容、消费者、保留策略、恢复方法和上传状态。",
            "",
            "## 存储结构",
            "",
            "- `CATALOG/`：明文说明和运行状态，便于在网盘中直接判断每份加密备份是什么。",
            "- `PAYLOAD-RESTIC/`：restic 加密、去重后的 pack、index 和 snapshot；文件名与内容都不可直接阅读。",
            "- 每个数据集用 `item:<id>` 标签定位；大目录另带 `chunk:<hash>` 分片标签。",
            "- 源码 Git 远端备份与对象数据备份分开管理；这里不代替 GitHub、Gitee 或 GitLab。",
            "",
            "## 运行规则",
            "",
            f"- 每次目标新增量上限约 {config.daily_limit_bytes / GIB:.2f} GiB；上传前 dry-run 估算，超额分片顺延。",
            f"- 上传速率上限：`{config.upload_limit_kib} KiB/s`，单路上传，适合每日闲时运行。",
            "- 队列按阶段推进；当前阶段：" + (f"`{phase}`" if phase is not None else "无"),
            "- restic 把小文件装入 pack，并跨数据集按内容去重。",
            "- SQLite 使用 backup API 生成一致性快照后再进入 restic。",
            "- 上传器不会执行 forget/prune，也绝不删除本地源文件。",
            "- 本地删除必须另经恢复演练和人工审阅，不属于本任务。",
            "",
            f"加密仓：`{config.repository}`",
            f"最近运行：{(state.get('last_run') or {}).get('finished_at', '无')}",
            "",
        ]
    )


def write_catalog(config: BackupConfig, state: dict[str, Any]) -> Path:
    catalog_root = config.state_root / "catalog"
    catalog_root.mkdir(parents=True, exist_ok=True)
    (catalog_root / "README.md").write_text(render_root_readme(config, state), encoding="utf-8")
    items_json = []
    for item in config.items:
        status = dict(state.get("items", {}).get(item.id, {}))
        item_dir = catalog_root / "items" / item.id
        item_dir.mkdir(parents=True, exist_ok=True)
        (item_dir / "README.md").write_text(render_item_readme(config, item, status), encoding="utf-8")
        items_json.append(
            {
                "id": item.id,
                "title": item.title,
                "phase": item.phase,
                "enabled": item.enabled,
                "source": str(item.source),
                "source_type": item.source_type,
                "payload_reference": item_payload_reference(config, item),
                "purpose": item.purpose,
                "contents": list(item.contents),
                "consumers": list(item.consumers),
                "retention": item.retention,
                "restore_steps": list(item.restore_steps),
                "sensitivity": item.sensitivity,
                "rationale": item.rationale,
                "blocked_reason": item.blocked_reason,
                "status": status,
            }
        )
    atomic_write_json(
        catalog_root / "catalog.json",
        {
            "schema": "omnicompany.object-backup-catalog.v1",
            "generated_at": utc_now(),
            "catalog_remote": config.catalog_remote,
            "repository": config.repository,
            "daily_limit_bytes": config.daily_limit_bytes,
            "upload_limit_kib": config.upload_limit_kib,
            "items": items_json,
        },
    )
    atomic_write_json(catalog_root / "run-status.json", state.get("last_run") or {})
    return catalog_root


class RunLogger:
    def __init__(self, path: Path, stream: TextIO | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a", encoding="utf-8")
        self._stream = stream or sys.stdout

    def write(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        self._handle.write(line + "\n")
        self._handle.flush()
        print(line, file=self._stream, flush=True)

    def close(self) -> None:
        self._handle.close()


class RunLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError("another object-backup run is already active") from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is None:
            return
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def run_text_command(
    command: list[str],
    logger: RunLogger,
    *,
    environment: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> tuple[int, str]:
    logger.write("RUN " + subprocess.list2cmdline(command))
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    lines: list[str] = []
    for raw_line in _process_output_lines(process, timeout_seconds=timeout_seconds):
        line = raw_line.rstrip()
        if not line:
            continue
        lines.append(line)
        logger.write(line)
    return process.wait(), "\n".join(lines)


def _process_output_lines(
    process: subprocess.Popen[str], *, timeout_seconds: float | None = None
) -> Iterable[str]:
    """Yield a child process's output without waiting on grandchildren.

    Restic starts ``rclone serve restic --stdio``.  If restic exits early, an
    orphaned rclone can retain its inherited stdout handle; iterating that pipe
    directly then waits forever for EOF even though the command has exited.
    A daemon pump lets the caller stop as soon as the actual child exits while
    retaining every line already received.
    """
    assert process.stdout is not None
    received: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        try:
            for raw_line in process.stdout:
                received.put(raw_line)
        finally:
            received.put(None)

    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    reader = threading.Thread(target=pump, name="omni-backup-output", daemon=True)
    reader.start()
    while process.poll() is None:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise TimeoutError(f"command timed out after {timeout_seconds:g}s")
        try:
            raw_line = received.get(timeout=min(0.2, remaining) if deadline is not None else 0.2)
        except queue.Empty:
            continue
        if raw_line is None:
            continue
        yield raw_line

    # Normal commands close stdout immediately.  Bound the join so an orphaned
    # rclone cannot turn a failed backup into a permanently running queue.
    reader.join(timeout=0.25)
    while True:
        try:
            raw_line = received.get_nowait()
        except queue.Empty:
            return
        if raw_line is not None:
            yield raw_line


def run_restic_json(
    command: list[str],
    config: BackupConfig,
    logger: RunLogger,
) -> tuple[int, dict[str, Any]]:
    logger.write("RUN " + subprocess.list2cmdline(command))
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=restic_environment(config),
    )
    summary: dict[str, Any] = {}
    for raw_line in _process_output_lines(process):
        line = raw_line.rstrip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            logger.write(line)
            continue
        message_type = payload.get("message_type") if isinstance(payload, dict) else ""
        if message_type in {"summary", "error", "fatal"}:
            logger.write(json.dumps(payload, ensure_ascii=False))
        if message_type == "summary":
            summary = payload
    return process.wait(), summary


def data_added(summary: dict[str, Any]) -> int:
    for key in ("data_added_packed", "data_added", "total_bytes_processed"):
        value = summary.get(key)
        if isinstance(value, (int, float)):
            return max(0, int(value))
    return 0


def build_catalog_copy_command(config: BackupConfig, catalog_root: Path) -> list[str]:
    return [
        config.rclone_executable,
        "copy",
        str(catalog_root),
        config.catalog_remote,
        "--config",
        str(config.rclone_config),
        "--transfers",
        "1",
        "--checkers",
        "2",
        "--ignore-times",
        "--contimeout",
        "10s",
        "--timeout",
        "20s",
        "--retries",
        "1",
        "--low-level-retries",
        "1",
    ]


def publish_catalog(
    config: BackupConfig,
    state: dict[str, Any],
    logger: RunLogger,
    *,
    dry_run: bool,
    required: bool = False,
) -> bool:
    if dry_run:
        logger.write("DRY catalog unchanged")
        return True
    catalog_root = write_catalog(config, state)
    command = build_catalog_copy_command(config, catalog_root)
    # This is only the small plaintext catalog.  A stuck remote must not block
    # all real recovery-point uploads before restic has a chance to start.
    try:
        return_code, _ = run_text_command(command, logger, timeout_seconds=60)
    except TimeoutError as exc:
        logger.write(f"WARN catalog upload deferred: {exc}")
        if required:
            raise RuntimeError("catalog upload timed out") from exc
        return False
    if return_code != 0:
        message = f"catalog upload failed with exit code {return_code}"
        logger.write(f"WARN catalog upload deferred: {message}")
        if required:
            raise RuntimeError(message)
        return False
    return True


def assert_repository_ready(config: BackupConfig, logger: RunLogger) -> None:
    if not config.restic_password_file.is_file():
        raise RuntimeError(f"restic password file is missing: {config.restic_password_file}")
    command = build_repository_ready_command(config)
    return_code, output = run_text_command(command, logger, environment=restic_environment(config))
    if return_code != 0:
        raise RuntimeError(f"restic repository is not ready: {output[-500:]}")


def build_repository_ready_command(config: BackupConfig) -> list[str]:
    """Check that the configured repository can be opened and decrypted.

    ``snapshots --latest 1`` is still grouped across snapshot paths and can
    emit a very large catalog. Reading the small repository config validates
    repository reachability and password without enumerating snapshots.
    """
    return [*restic_base_command(config), "cat", "config"]


def prepare_source(
    item: QueueItem,
    state_root: Path,
    staging_root: Path | None = None,
) -> Path | None:
    if item.source_type == "sqlite":
        return create_sqlite_snapshot(item, state_root, staging_root)
    return None


def unit_state_key(unit: BackupUnit) -> str:
    return hashlib.sha256(unit.key.encode("utf-8")).hexdigest()[:16]


def _validate_chunk_selection(
    config: BackupConfig,
    item_ids: set[str] | None,
    phase: int | None,
    chunk_key: str | None,
) -> tuple[QueueItem, BackupUnit] | None:
    if chunk_key is None:
        return None
    if not isinstance(chunk_key, str):
        raise ValueError("--chunk must be a relative file-unit key string")
    if not item_ids or len(item_ids) != 1:
        raise ValueError("--chunk requires exactly one --item")
    windows_key = PureWindowsPath(chunk_key)
    posix_key = PurePosixPath(chunk_key.replace("\\", "/"))
    if (
        not chunk_key
        or windows_key.is_absolute()
        or bool(windows_key.drive)
        or posix_key.is_absolute()
        or ".." in posix_key.parts
        or ".." in chunk_key.replace("\\", "/").split("/")
    ):
        raise ValueError(f"--chunk must be a safe relative file-unit key: {chunk_key!r}")
    item_id = next(iter(item_ids))
    item = next((candidate for candidate in config.items if candidate.id == item_id), None)
    if item is None or not item.enabled or item.blocked_reason:
        raise ValueError(f"--chunk item is not an enabled queue item: {item_id!r}")
    if phase is not None and item.phase != phase:
        raise ValueError(f"--chunk item {item_id!r} does not match phase {phase}")
    if item.source_type != "tree" or item.chunk_depth <= 0:
        raise ValueError("--chunk requires a chunked tree item")
    matches = [unit for unit in chunk_units(item) if unit.key == chunk_key and unit.source.is_file()]
    if len(matches) != 1:
        raise ValueError(f"unknown or non-file chunk for {item_id}: {chunk_key!r}")
    return item, matches[0]


def update_item_completion(item: QueueItem, item_status: dict[str, Any], units: list[BackupUnit]) -> None:
    chunks = item_status.setdefault("chunks", {})
    complete = True
    for unit in units:
        chunk_status = chunks.get(unit_state_key(unit), {})
        file_unit = unit.source.is_file() or isinstance(chunk_status.get("source_metadata"), dict)
        if (
            not chunk_status.get("completed_at")
            or chunk_status.get("status") in {"failed", "estimate_failed"}
        ):
            complete = False
            break
        if file_unit and (
            chunk_status.get("status") != "complete"
            or not isinstance(chunk_status.get("source_metadata"), dict)
            or file_source_metadata(unit.source) != chunk_status.get("source_metadata")
        ):
            complete = False
            break
    item_status["ever_completed"] = bool(item_status.get("ever_completed") or complete)
    if complete:
        item_status["last_completed_at"] = utc_now()
        item_status["status"] = "complete"
        item_status["last_error"] = ""
        item_status.pop("last_return_code", None)
        item_status.pop("last_transferred_bytes", None)


def _run_backup_locked(
    config: BackupConfig,
    *,
    dry_run: bool = False,
    limit_bytes: int | None = None,
    phase: int | None = None,
    item_ids: set[str] | None = None,
    chunk_key: str | None = None,
    catalog_only: bool = False,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    config.state_root.mkdir(parents=True, exist_ok=True)
    state = load_state(config)
    selected_chunk = _validate_chunk_selection(config, item_ids, phase, chunk_key)
    run_started = utc_now()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    logger = RunLogger(config.state_root / "logs" / f"{run_id}.log", stream=stream)
    limit = limit_bytes if limit_bytes is not None else config.daily_limit_bytes
    remaining = max(0, limit)
    results: list[dict[str, Any]] = []
    try:
        publish_catalog(config, state, logger, dry_run=dry_run, required=catalog_only)
        if catalog_only:
            return {"run_id": run_id, "catalog_only": True, "dry_run": dry_run}
        queue = (
            [selected_chunk[0]]
            if selected_chunk is not None
            else selected_items(config, state, phase=phase, item_ids=item_ids)
        )
        selected_phase = phase if phase is not None else select_phase(config.items, state)
        logger.write(f"selected phase={selected_phase} items={len(queue)} limit={limit}")
        if not dry_run:
            assert_repository_ready(config, logger)
        for item in queue:
            if remaining <= 0:
                break
            if not item.source.exists():
                results.append({"id": item.id, "status": "source_missing", "bytes": 0})
                if not dry_run:
                    status = state["items"].setdefault(item.id, {})
                    status.update({"status": "source_missing", "last_error": str(item.source)})
                    save_state(config, state)
                continue
            units = chunk_units(item)
            item_status = state.get("items", {}).get(item.id, {})
            chunk_states = item_status.get("chunks", {})
            file_unit_flags = {
                unit.key: unit.source.is_file()
                or isinstance(chunk_states.get(unit_state_key(unit), {}).get("source_metadata"), dict)
                for unit in units
            }
            due_units = [
                unit
                for unit in units
                if (
                    unit.key == selected_chunk[1].key
                    if selected_chunk is not None
                    else unit_is_due(item, unit, item_status.get("chunks", {}).get(unit_state_key(unit), {}))
                )
            ]
            if dry_run:
                for unit in due_units:
                    logger.write(f"DRY {item.id} chunk={unit.label} source={unit.source}")
                    results.append({"id": item.id, "chunk": unit.key, "status": "dry_run", "bytes": 0})
                continue
            item_status = state["items"].setdefault(item.id, {})
            item_status.update({"last_attempt_at": utc_now(), "status": "running"})
            chunk_states = item_status.setdefault("chunks", {})
            deferred_count = 0
            item_failed = False
            save_state(config, state)
            prepared_source = prepare_source(item, config.state_root, config.sqlite_staging_root)
            for unit in due_units:
                if remaining <= 0:
                    break
                effective_unit = unit
                if item.source_type == "sqlite":
                    effective_unit = BackupUnit(key=unit.key, source=prepared_source, label=unit.label)
                estimate_command = build_restic_backup_command(
                    config,
                    item,
                    effective_unit,
                    prepared_source=prepared_source,
                    dry_run=True,
                    file_unit=file_unit_flags[unit.key],
                )
                estimate_code, estimate_summary = run_restic_json(estimate_command, config, logger)
                if estimate_code != 0:
                    result = {
                        "id": item.id,
                        "chunk": unit.key,
                        "status": "estimate_failed",
                        "bytes": 0,
                    }
                    results.append(result)
                    chunk_status = chunk_states.setdefault(unit_state_key(unit), {})
                    chunk_status.update(
                        {
                            "key": unit.key,
                            "label": unit.label,
                            "status": "estimate_failed",
                            "last_error": "restic dry-run failed",
                        }
                    )
                    item_status.update({"status": "failed", "last_error": "restic dry-run failed"})
                    item_failed = True
                    break
                estimated = data_added(estimate_summary)
                if estimated > remaining:
                    chunk_status = chunk_states.setdefault(unit_state_key(unit), {})
                    chunk_status.update(
                        {
                            "key": unit.key,
                            "label": unit.label,
                            "status": "deferred_quota",
                            "estimated_bytes": estimated,
                            "last_error": "",
                        }
                    )
                    deferred_count += 1
                    results.append(
                        {
                            "id": item.id,
                            "chunk": unit.key,
                            "status": "deferred_quota",
                            "estimated_bytes": estimated,
                            "bytes": 0,
                        }
                    )
                    continue
                backup_command = build_restic_backup_command(
                    config,
                    item,
                    effective_unit,
                    prepared_source=prepared_source,
                    dry_run=False,
                    file_unit=file_unit_flags[unit.key],
                )
                file_unit = file_unit_flags[unit.key]
                source_metadata_before = file_source_metadata(unit.source) if file_unit else None
                return_code, summary = run_restic_json(backup_command, config, logger)
                source_metadata_after = file_source_metadata(unit.source) if file_unit else None
                added = data_added(summary)
                remaining = max(0, remaining - added)
                snapshot_id = str(summary.get("snapshot_id") or "")
                file_metadata_stable = (
                    not file_unit
                    or (
                        source_metadata_before is not None
                        and source_metadata_after is not None
                        and source_metadata_before == source_metadata_after
                    )
                )
                if return_code != 0:
                    error = f"restic exit={return_code}"
                elif not snapshot_id:
                    error = "restic completed without snapshot_id"
                elif not file_metadata_stable:
                    error = "source file metadata changed or became unavailable during backup"
                else:
                    error = ""
                status_name = "complete" if not error else "failed"
                chunk_status = chunk_states.setdefault(unit_state_key(unit), {})
                chunk_status.update(
                    {
                        "key": unit.key,
                        "label": unit.label,
                        "status": status_name,
                        "completed_at": utc_now() if status_name == "complete" else chunk_status.get("completed_at"),
                        "snapshot_id": (
                            snapshot_id if status_name == "complete" else chunk_status.get("snapshot_id", "")
                        ),
                        "last_added_bytes": added,
                        "last_error": error,
                    }
                )
                if status_name == "complete" and file_unit:
                    chunk_status["source_metadata"] = source_metadata_after
                results.append(
                    {
                        "id": item.id,
                        "chunk": unit.key,
                        "status": status_name,
                        "bytes": added,
                        "snapshot_id": snapshot_id,
                        "error": error,
                    }
                )
                save_state(config, state)
                if status_name != "complete":
                    item_status.update({"status": "failed", "last_error": error})
                    item_failed = True
                    break
            update_item_completion(item, item_status, units)
            # 2026-09-01 (MACHINE-FILE-HYGIENE P3): sqlite 一致性快照只是上传暂存, 上传完成即删,
            # 不再让 %LOCALAPPDATA%/OmniBackup/snapshots 长期攒 15 GB 的库副本(远端 restic 已有全部版本)。
            if (
                prepared_source is not None
                and item.source_type == "sqlite"
                and not item_failed
                and not deferred_count
                and item_status.get("status") == "complete"
            ):
                try:
                    Path(prepared_source).unlink(missing_ok=True)
                    logger.write(f"{item.id}: removed staging snapshot {prepared_source}")
                except OSError as exc:  # 暂存删不掉不影响备份结果
                    logger.write(f"{item.id}: staging snapshot cleanup skipped: {exc}")
            if deferred_count and not item_failed:
                item_status.update(
                    {
                        "status": "partial",
                        "deferred": True,
                        "deferred_count": deferred_count,
                        "last_error": "",
                    }
                )
            elif not item_failed:
                item_status.pop("deferred", None)
                item_status.pop("deferred_count", None)
                if item_status.get("status") == "running":
                    item_status["status"] = "partial"
            save_state(config, state)
        result_statuses = {str(result.get("status", "")) for result in results}
        if dry_run:
            run_status = "dry_run"
        elif result_statuses & {"failed", "source_missing", "estimate_failed"}:
            run_status = "failed"
        elif "deferred_quota" in result_statuses:
            run_status = "partial"
        else:
            run_status = "complete"
        run_result = {
            "run_id": run_id,
            "started_at": run_started,
            "finished_at": utc_now(),
            "dry_run": dry_run,
            "status": run_status,
            "deferred": "deferred_quota" in result_statuses,
            "deferred_count": sum(result.get("status") == "deferred_quota" for result in results),
            "limit_bytes": limit,
            "transferred_bytes": sum(int(result.get("bytes", 0)) for result in results),
            "remaining_bytes": remaining,
            "results": results,
        }
        if dry_run:
            return run_result
        state["last_run"] = run_result
        save_state(config, state)
        publish_catalog(config, state, logger, dry_run=dry_run, required=catalog_only)
        return run_result
    finally:
        logger.close()


def run_backup(
    config: BackupConfig,
    *,
    dry_run: bool = False,
    limit_bytes: int | None = None,
    phase: int | None = None,
    item_ids: set[str] | None = None,
    chunk_key: str | None = None,
    catalog_only: bool = False,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    with RunLock(config.state_root / "run.lock"):
        return _run_backup_locked(
            config,
            dry_run=dry_run,
            limit_bytes=limit_bytes,
            phase=phase,
            item_ids=item_ids,
            chunk_key=chunk_key,
            catalog_only=catalog_only,
            stream=stream,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the bounded encrypted object-backup queue.")
    parser.add_argument("--config", required=True, help="Queue JSON path")
    parser.add_argument("--dry-run", action="store_true", help="Render catalog and selected chunks only")
    parser.add_argument("--limit-gib", type=float, default=None, help="Override this run's added-data cap")
    parser.add_argument("--phase", type=int, default=None, help="Run one explicit phase")
    parser.add_argument("--item", action="append", default=[], help="Run one queue item (repeatable)")
    parser.add_argument("--chunk", default=None, help="Run one exact existing file chunk; requires one --item")
    parser.add_argument("--catalog-only", action="store_true", help="Publish descriptions without payload")
    parser.add_argument(
        "--local-session-limit-gib",
        type=float,
        default=None,
        help="After a successful run, enforce verified local raw-session retention",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.chunk is not None and len(args.item) != 1:
        raise ValueError("--chunk requires exactly one --item")
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    limit = None if args.limit_gib is None else int(args.limit_gib * GIB)
    result = run_backup(
        config,
        dry_run=args.dry_run,
        limit_bytes=limit,
        phase=args.phase,
        item_ids=set(args.item) or None,
        chunk_key=args.chunk,
        catalog_only=args.catalog_only,
    )
    failed = any(
        entry.get("status") in {"failed", "source_missing", "estimate_failed"}
        for entry in result.get("results", [])
    )
    if args.local_session_limit_gib is not None and not args.catalog_only:
        if args.local_session_limit_gib <= 0:
            raise ValueError("--local-session-limit-gib must be positive")
        if failed and not args.dry_run:
            result["session_retention"] = {
                "status": "skipped_backup_failed",
                "dry_run": False,
            }
        else:
            from .session_local_retention import (
                GIB as RETENTION_GIB,
                enforce_local_session_limit,
                load_session_retention_policy,
            )

            policy = load_session_retention_policy(config_path)
            result["session_retention"] = enforce_local_session_limit(
                config,
                policy,
                limit_bytes=int(args.local_session_limit_gib * RETENTION_GIB),
                dry_run=args.dry_run,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    retention_blocked = result.get("session_retention", {}).get("status") == "blocked"
    return 1 if failed or retention_blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
