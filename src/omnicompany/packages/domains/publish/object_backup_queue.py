# [OMNI] origin=human domain=domains/publish type=module agent=ai-ide-292fcd7e ts=2026-07-26T09:07:29Z
# [OMNI] summary="Bounded restic backup queue with a detailed plaintext cloud catalog."
# [OMNI] why="发布管线组件，归在 publish 域"
# [OMNI] tags=publish,module
"""Bounded restic backup queue with a detailed plaintext cloud catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
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


def create_sqlite_snapshot(item: QueueItem, state_root: Path) -> Path:
    if not item.source.is_file():
        raise FileNotFoundError(item.source)
    snapshot_dir = state_root / "snapshots" / item.id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    target = snapshot_dir / (item.snapshot_name or item.source.name)
    temporary = target.with_suffix(target.suffix + ".tmp")
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


def unit_is_due(item: QueueItem, unit: BackupUnit, status: dict[str, Any]) -> bool:
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
    return datetime.now(timezone.utc) >= completed + timedelta(days=item.refresh_days)


def resolve_included_files(item: QueueItem) -> list[Path]:
    def is_excluded(path: Path) -> bool:
        relative = PurePosixPath(path.relative_to(item.source).as_posix())
        return any(relative.match(pattern) for pattern in item.excludes)

    files: set[Path] = set()
    for pattern in item.includes:
        for match in item.source.glob(pattern):
            if match.is_file() and not is_excluded(match):
                files.add(match.resolve())
            elif match.is_dir():
                for child in match.rglob("*"):
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
    rclone_args = "serve restic --stdio --b2-hard-delete --transfers 1 --checkers 4"
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
) -> list[str]:
    command = [
        *restic_base_command(config),
        "backup",
        "--json",
        "--skip-if-unchanged",
        "--tag",
        f"item:{item.id}",
        "--tag",
        f"phase:{item.phase}",
        "--tag",
        chunk_tag(unit),
    ]
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
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip()
        if not line:
            continue
        lines.append(line)
        logger.write(line)
    return process.wait(), "\n".join(lines)


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
    assert process.stdout is not None
    for raw_line in process.stdout:
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
    ]


def publish_catalog(config: BackupConfig, state: dict[str, Any], logger: RunLogger, *, dry_run: bool) -> None:
    if dry_run:
        logger.write("DRY catalog unchanged")
        return
    catalog_root = write_catalog(config, state)
    command = build_catalog_copy_command(config, catalog_root)
    return_code, _ = run_text_command(command, logger)
    if return_code != 0:
        raise RuntimeError(f"catalog upload failed with exit code {return_code}")


def assert_repository_ready(config: BackupConfig, logger: RunLogger) -> None:
    if not config.restic_password_file.is_file():
        raise RuntimeError(f"restic password file is missing: {config.restic_password_file}")
    command = [*restic_base_command(config), "snapshots", "--json", "--latest", "1"]
    return_code, output = run_text_command(command, logger, environment=restic_environment(config))
    if return_code != 0:
        raise RuntimeError(f"restic repository is not ready: {output[-500:]}")


def prepare_source(item: QueueItem, state_root: Path) -> Path | None:
    if item.source_type == "sqlite":
        return create_sqlite_snapshot(item, state_root)
    return None


def unit_state_key(unit: BackupUnit) -> str:
    return hashlib.sha256(unit.key.encode("utf-8")).hexdigest()[:16]


def update_item_completion(item: QueueItem, item_status: dict[str, Any], units: list[BackupUnit]) -> None:
    chunks = item_status.setdefault("chunks", {})
    complete = all(chunks.get(unit_state_key(unit), {}).get("completed_at") for unit in units)
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
    catalog_only: bool = False,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    config.state_root.mkdir(parents=True, exist_ok=True)
    state = load_state(config)
    run_started = utc_now()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    logger = RunLogger(config.state_root / "logs" / f"{run_id}.log", stream=stream)
    limit = limit_bytes if limit_bytes is not None else config.daily_limit_bytes
    remaining = max(0, limit)
    results: list[dict[str, Any]] = []
    try:
        publish_catalog(config, state, logger, dry_run=dry_run)
        if catalog_only:
            return {"run_id": run_id, "catalog_only": True, "dry_run": dry_run}
        queue = selected_items(config, state, phase=phase, item_ids=item_ids)
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
            due_units = [
                unit
                for unit in units
                if unit_is_due(item, unit, item_status.get("chunks", {}).get(unit_state_key(unit), {}))
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
            prepared_source = prepare_source(item, config.state_root)
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
                )
                return_code, summary = run_restic_json(backup_command, config, logger)
                added = data_added(summary)
                remaining = max(0, remaining - added)
                snapshot_id = str(summary.get("snapshot_id") or "")
                status_name = "complete" if return_code == 0 else "failed"
                chunk_status = chunk_states.setdefault(unit_state_key(unit), {})
                chunk_status.update(
                    {
                        "key": unit.key,
                        "label": unit.label,
                        "status": status_name,
                        "completed_at": utc_now() if return_code == 0 else chunk_status.get("completed_at"),
                        "snapshot_id": snapshot_id or chunk_status.get("snapshot_id", ""),
                        "last_added_bytes": added,
                        "last_error": "" if return_code == 0 else f"restic exit={return_code}",
                    }
                )
                results.append(
                    {
                        "id": item.id,
                        "chunk": unit.key,
                        "status": status_name,
                        "bytes": added,
                        "snapshot_id": snapshot_id,
                    }
                )
                save_state(config, state)
                if return_code != 0:
                    item_status.update({"status": "failed", "last_error": f"restic exit={return_code}"})
                    item_failed = True
                    break
            update_item_completion(item, item_status, units)
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
        publish_catalog(config, state, logger, dry_run=dry_run)
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
    parser.add_argument("--catalog-only", action="store_true", help="Publish descriptions without payload")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    limit = None if args.limit_gib is None else int(args.limit_gib * GIB)
    result = run_backup(
        config,
        dry_run=args.dry_run,
        limit_bytes=limit,
        phase=args.phase,
        item_ids=set(args.item) or None,
        catalog_only=args.catalog_only,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    failed = any(
        entry.get("status") in {"failed", "source_missing", "estimate_failed"}
        for entry in result.get("results", [])
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
